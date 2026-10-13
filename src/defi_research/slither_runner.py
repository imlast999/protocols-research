"""
slither_runner.py
Controlled, isolated static analysis runner for Slither.
Enforces the 3-stage security gating architecture:
  Stage A: REPOSITORY_RESOLUTION
  Stage B: SOURCE_VERIFICATION
  Stage C: SLITHER_ELIGIBILITY

Safety invariants:
- Executes Slither as a subprocess strictly without shell (shell=False).
- Requires resolved confidence and successful source verification before running.
- Validates that target directories reside inside an explicitly configured workspace.
- Never runs arbitrary build hooks, dependency installs, or untrusted repository scripts.
- Never treats non-zero exit codes alone as proof of vulnerability; parses actual JSON report.
- Produces structured, machine-readable JSON results with explicit limitations.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

try:
    from defi_research.github_repository_resolver import (
        ResolutionStatus,
        SlitherEligibilityStatus,
        SourceVerificationRecord,
        SourceVerificationStatus,
        evaluate_slither_eligibility,
        verify_repository_source,
    )
except ImportError:
    from github_repository_resolver import (
        ResolutionStatus,
        SlitherEligibilityStatus,
        SourceVerificationRecord,
        SourceVerificationStatus,
        evaluate_slither_eligibility,
        verify_repository_source,
    )

logger = logging.getLogger("SlitherRunner")


class AnalysisStatus(str, Enum):
    """Machine-readable outcomes for Slither static analysis runs."""
    COMPLETED = "COMPLETED"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
    PREREQUISITE_MISSING = "PREREQUISITE_MISSING"
    TIMED_OUT = "TIMED_OUT"
    ANALYSIS_FAILED = "ANALYSIS_FAILED"


STANDARD_LIMITATIONS: List[str] = [
    "Static analysis cannot detect complex economic exploits, price oracle manipulation, or flash loan arbitrage vulnerabilities.",
    "Repository source code is not guaranteed to correspond to on-chain deployed bytecode without contract address bytecode verification.",
    "Slither analysis is limited to recognized Solidity patterns and detector heuristics; false positives and false negatives may occur.",
    "Analysis results do not imply or constitute a certification of security, formal verification, or audit guarantee.",
    "Third-party external dependencies not present locally were not installed or compiled during this sandboxed run.",
]


def sanitize_output_text(text: Optional[str]) -> Optional[str]:
    """
    Redacts sensitive secrets such as EVM private keys and API keys from CLI/process output
    to prevent credential leakage into analysis reports.
    """
    if not text:
        return text

    # Redact 64-hex-character private keys (with or without 0x prefix)
    sanitized = re.sub(r'\b0x[a-fA-F0-9]{64}\b', '[REDACTED_HEX_KEY]', text)
    sanitized = re.sub(r'(?<![a-zA-Z0-9])[a-fA-F0-9]{64}(?![a-zA-Z0-9])', '[REDACTED_HEX_KEY]', sanitized)

    # Redact API keys and authorization tokens (e.g. key=..., secret: ..., token=...)
    sanitized = re.sub(
        r'(?i)(api[_-]?key|secret|token|password|bearer)[\s:=]+([a-zA-Z0-9_\-\.]{16,})',
        r'\1=[REDACTED_SECRET]',
        sanitized,
    )
    return sanitized


@dataclass
class SlitherFinding:
    """Represents a single finding detected by Slither."""
    detector_id: str
    title: str
    severity: str
    confidence: str
    description: str
    affected_contract: Optional[str] = None
    source_locations: List[Dict[str, Any]] = field(default_factory=list)
    source_file_paths: List[str] = field(default_factory=list)
    raw_finding: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SlitherAnalysisResult:
    """Structured, machine-readable outcome for a Slither analysis execution."""
    protocol_name: str
    defillama_slug: Optional[str]
    repository_url: Optional[str]
    commit_hash: Optional[str]
    source_verification_status: str
    slither_version: Optional[str]
    analysis_status: AnalysisStatus
    started_at: int
    duration_seconds: float
    exit_code: Optional[int]
    execution_backend: str = "native"
    workflow_type: str = "REPOSITORY_CHECKOUT"
    findings: List[Dict[str, Any]] = field(default_factory=list)
    findings_summary: Dict[str, int] = field(default_factory=dict)
    stderr_summary: Optional[str] = None
    stdout_summary: Optional[str] = None
    raw_artifact_path: Optional[str] = None
    limitations: List[str] = field(default_factory=lambda: list(STANDARD_LIMITATIONS))
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["analysis_status"] = self.analysis_status.value
        data["execution_backend"] = self.execution_backend
        return data

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def get_default_allowed_workspaces() -> List[str]:
    """Returns the default allowed workspace directories."""
    base_dir = os.path.abspath(os.path.dirname(__file__))
    repo_root = os.path.abspath(os.path.join(base_dir, "..", ".."))
    allowed = [base_dir, repo_root, os.path.abspath(".")]

    # Include explicit environment workspace if configured
    env_ws = os.getenv("SLITHER_ALLOWED_WORKSPACE")
    if env_ws:
        for p in env_ws.split(os.pathsep):
            p_strip = p.strip()
            if p_strip:
                allowed.append(os.path.abspath(p_strip))

    return allowed


def is_directory_in_workspace(target_dir: str, allowed_workspaces: Optional[List[str]] = None, check_exists: bool = False) -> bool:
    """
    Validates that target_dir is physically contained within an explicitly allowed workspace root.
    Prevents directory traversal, prefix spoofing, symlink escape, and arbitrary system file inspection.
    """
    if not target_dir:
        return False
    if check_exists and not os.path.exists(target_dir):
        return False

    workspaces = allowed_workspaces if allowed_workspaces is not None else get_default_allowed_workspaces()
    real_target = os.path.realpath(os.path.abspath(target_dir))

    for ws in workspaces:
        if not ws:
            continue
        real_ws = os.path.realpath(os.path.abspath(ws))
        try:
            # os.path.commonpath prevents prefix collisions like /workspace_fake vs /workspace
            common = os.path.commonpath([real_ws, real_target])
            if common == real_ws:
                return True
        except ValueError:
            # Different drives on Windows (e.g. C: vs D:)
            continue

    return False


def check_docker_availability(
    docker_executable: Optional[str] = None,
    timeout_seconds: int = 5,
) -> Tuple[bool, bool, Optional[str]]:
    """
    Checks whether Docker CLI is installed and the Docker daemon is accessible.
    Returns (cli_available, daemon_available, diagnostic_message).
    Executes subprocess strictly without shell (shell=False).
    """
    docker_bin = docker_executable or "docker"
    which_path = shutil.which(docker_bin)
    if not which_path and not (docker_executable and os.path.exists(docker_executable)):
        return (
            False,
            False,
            f"Docker CLI executable '{docker_bin}' not found in PATH. "
            "Please install Docker Desktop and ensure 'docker' is available in your PATH.",
        )

    cmd_target = which_path or docker_bin
    try:
        proc = subprocess.run(
            [cmd_target, "info"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            timeout=timeout_seconds,
        )
        if proc.returncode == 0:
            return True, True, None
        else:
            err_snip = sanitize_output_text(proc.stderr.strip()[:200]) or "Docker daemon returned non-zero status"
            return (
                True,
                False,
                f"Docker CLI is installed, but Docker daemon is offline or inaccessible: {err_snip}",
            )
    except subprocess.TimeoutExpired:
        return True, False, f"Timeout after {timeout_seconds}s querying Docker daemon."
    except Exception as e:
        return True, False, f"Error communicating with Docker daemon: {e}"


def check_docker_image_availability(
    image_name: str,
    docker_executable: Optional[str] = None,
    timeout_seconds: int = 5,
) -> Tuple[bool, Optional[str]]:
    """
    Checks whether a specific Docker image is available locally.
    Returns (is_available, diagnostic_message).
    Executes subprocess strictly without shell (shell=False).
    """
    if not image_name:
        return False, "Docker image name was not specified."

    docker_bin = shutil.which(docker_executable or "docker") or docker_executable or "docker"
    try:
        proc = subprocess.run(
            [docker_bin, "image", "inspect", image_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            timeout=timeout_seconds,
        )
        if proc.returncode == 0:
            return True, None
        return (
            False,
            f"Docker image '{image_name}' is not found locally. "
            f"Please build it using: docker build -t {image_name} .",
        )
    except subprocess.TimeoutExpired:
        return False, f"Timeout inspecting Docker image '{image_name}'."
    except Exception as e:
        return False, f"Error inspecting Docker image '{image_name}': {e}"


def build_docker_run_args(
    target_dir: str,
    host_output_dir: str,
    image_name: Optional[str] = None,
    container_output_path: str = "/output/slither_out.json",
    docker_executable: Optional[str] = None,
    extra_slither_args: Optional[List[str]] = None,
) -> List[str]:
    """
    Constructs the secure argument list for invoking Slither within a Docker container.

    Security constraints enforced:
    - --rm: automatic container cleanup on exit
    - --network none: total network isolation (no network access permitted)
    - --security-opt no-new-privileges: prevents privilege escalation
    - --cap-drop ALL: drops all Linux capabilities
    - --memory 2g: caps container RAM usage to prevent DoS
    - --cpus 2.0: caps CPU usage
    - --user 1000:1000: runs as unprivileged user
    - Target repository mounted strictly read-only (:ro)
    - Output directory mounted in dedicated temporary directory (:rw)
    - Working directory set to /repo
    - Never uses shell=True
    """
    eff_image = image_name or os.getenv("SLITHER_DOCKER_IMAGE", "defi-slither:0.10.4")
    docker_bin = shutil.which(docker_executable or "docker") or docker_executable or "docker"
    real_target = os.path.realpath(os.path.abspath(target_dir))
    real_out = os.path.realpath(os.path.abspath(host_output_dir))

    cmd = [
        docker_bin,
        "run",
        "--rm",
        "--network", "none",
        "--security-opt", "no-new-privileges",
        "--cap-drop", "ALL",
        "--memory", "2g",
        "--cpus", "2.0",
        "--user", "1000:1000",
        "-v", f"{real_target}:/repo:ro",
        "-v", f"{real_out}:/output:rw",
        "-w", "/repo",
        eff_image,
        "slither",
        ".",
        "--json",
        container_output_path,
    ]

    if extra_slither_args:
        cmd.extend(extra_slither_args)

    return cmd


def check_slither_availability(
    custom_executable: Optional[str] = None,
    timeout_seconds: int = 5,
    backend: Optional[str] = None,
    docker_image: Optional[str] = None,
    docker_executable: Optional[str] = None,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Checks whether Slither executable or Docker environment is available.
    Supports backend='auto' (fallback), backend='native', or backend='docker'.
    Returns (is_available, version_string, error_message).
    Executes subprocess without invoking a shell.
    """
    eff_backend = (backend or os.getenv("SLITHER_BACKEND", "auto")).lower()
    eff_image = docker_image or os.getenv("SLITHER_DOCKER_IMAGE", "defi-slither:0.10.4")

    def _check_native() -> Tuple[bool, Optional[str], Optional[str]]:
        exe_name = custom_executable or "slither"
        which_path = shutil.which(exe_name)

        if not which_path and not (custom_executable and os.path.exists(custom_executable)):
            return (
                False,
                None,
                f"Slither executable '{exe_name}' not found in PATH. "
                "Please install Slither ('pip install slither-analyzer') and ensure it is available in PATH.",
            )

        cmd_target = which_path or exe_name
        try:
            proc = subprocess.run(
                [cmd_target, "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                timeout=timeout_seconds,
            )
            if proc.returncode == 0:
                version_str = proc.stdout.strip()
                return True, version_str, None
            else:
                return (
                    False,
                    None,
                    f"Slither invocation failed with code {proc.returncode}: {proc.stderr.strip()}",
                )
        except subprocess.TimeoutExpired:
            return False, None, "Timeout checking Slither version."
        except Exception as e:
            return False, None, f"Error checking Slither executable: {e}"

    if eff_backend == "native":
        return _check_native()

    if eff_backend == "docker":
        cli_ok, daemon_ok, doc_err = check_docker_availability(docker_executable, timeout_seconds)
        if not cli_ok:
            return False, None, doc_err
        if not daemon_ok:
            return False, None, doc_err
        img_ok, img_err = check_docker_image_availability(eff_image, docker_executable, timeout_seconds)
        if not img_ok:
            return False, None, img_err
        docker_bin = shutil.which(docker_executable or "docker") or docker_executable or "docker"
        try:
            proc = subprocess.run(
                [docker_bin, "run", "--rm", "--network", "none", eff_image, "slither", "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                timeout=timeout_seconds,
            )
            if proc.returncode == 0:
                return True, f"docker:{proc.stdout.strip()}", None
            return True, f"docker:{eff_image}", None
        except Exception:
            return True, f"docker:{eff_image}", None

    # eff_backend == "auto":
    # 1. Native first
    nat_ok, nat_ver, nat_err = _check_native()
    if nat_ok:
        return True, nat_ver, None

    # 2. Docker fallback
    cli_ok, daemon_ok, doc_err = check_docker_availability(docker_executable, timeout_seconds)
    if not cli_ok:
        return (
            False,
            None,
            f"Slither executable 'slither' not found in PATH. "
            "Please install Slither ('pip install slither-analyzer') or install Docker Desktop. "
            f"Docker CLI check: {doc_err}",
        )
    if not daemon_ok:
        return (
            False,
            None,
            f"Slither not installed on host, and Docker daemon is unavailable: {doc_err}. "
            "Please start Docker Desktop or install Slither ('pip install slither-analyzer').",
        )
    img_ok, img_err = check_docker_image_availability(eff_image, docker_executable, timeout_seconds)
    if not img_ok:
        return (
            False,
            None,
            f"Slither not installed on host, and Docker image '{eff_image}' is missing: {img_err}. "
            f"Build it using: docker build -t {eff_image} . or install Slither ('pip install slither-analyzer').",
        )

    docker_bin = shutil.which(docker_executable or "docker") or docker_executable or "docker"
    try:
        proc = subprocess.run(
            [docker_bin, "run", "--rm", "--network", "none", eff_image, "slither", "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            timeout=timeout_seconds,
        )
        if proc.returncode == 0:
            return True, f"docker:{proc.stdout.strip()}", None
        return True, f"docker:{eff_image}", None
    except Exception:
        return True, f"docker:{eff_image}", None


def parse_slither_json(raw_json_str: str) -> Tuple[List[Dict[str, Any]], Dict[str, int], Optional[str]]:
    """
    Parses Slither's JSON report into normalized findings and severity counts.
    Returns (findings_list, summary_counts, error_message).
    """
    if not raw_json_str or not raw_json_str.strip():
        return [], {}, "Empty JSON output from Slither"

    try:
        data = json.loads(raw_json_str)
    except json.JSONDecodeError as e:
        return [], {}, f"Malformed JSON output: {e}"

    if not isinstance(data, dict):
        return [], {}, "Unexpected Slither JSON structure: root is not an object"

    # Check for top-level error reported by Slither
    if not data.get("success", True) and "error" in data and data["error"]:
        err_msg = str(data["error"])
        return [], {}, err_msg

    results_obj = data.get("results")
    if not isinstance(results_obj, dict):
        # Empty run with no findings
        return [], {"High": 0, "Medium": 0, "Low": 0, "Informational": 0, "Optimization": 0, "total": 0}, None

    detectors = results_obj.get("detectors", [])
    if not isinstance(detectors, list):
        detectors = []

    findings: List[Dict[str, Any]] = []
    summary: Dict[str, int] = {
        "High": 0,
        "Medium": 0,
        "Low": 0,
        "Informational": 0,
        "Optimization": 0,
        "total": 0,
    }

    for item in detectors:
        if not isinstance(item, dict):
            continue

        det_id = str(item.get("check") or "unknown-check")
        impact = str(item.get("impact") or "Informational").capitalize()
        confidence = str(item.get("confidence") or "Unknown").capitalize()
        desc = str(item.get("description") or "").strip()

        # Update summary counts
        if impact in summary:
            summary[impact] += 1
        else:
            summary.setdefault(impact, 0)
            summary[impact] += 1
        summary["total"] += 1

        # Extract affected contract & source locations from elements
        elements = item.get("elements", [])
        affected_contracts: List[str] = []
        source_locations: List[Dict[str, Any]] = []
        source_file_paths: List[str] = []

        if isinstance(elements, list):
            for el in elements:
                if not isinstance(el, dict):
                    continue

                # Check parent contract or direct contract element
                c_name = None
                type_fields = el.get("type_specific_fields", {})
                if isinstance(type_fields, dict):
                    parent = type_fields.get("parent", {})
                    if isinstance(parent, dict) and parent.get("type") == "contract":
                        c_name = parent.get("name")

                if not c_name and el.get("type") == "contract":
                    c_name = el.get("name")

                if c_name and c_name not in affected_contracts:
                    affected_contracts.append(c_name)

                # Source mapping
                s_map = el.get("source_mapping", {})
                if isinstance(s_map, dict):
                    fn = s_map.get("filename_relative") or s_map.get("filename_short")
                    if fn and fn not in source_file_paths:
                        source_file_paths.append(fn)
                    source_locations.append({
                        "file": fn,
                        "lines": s_map.get("lines", []),
                        "start": s_map.get("start"),
                        "length": s_map.get("length"),
                    })

        finding = SlitherFinding(
            detector_id=det_id,
            title=det_id.replace("-", " ").title(),
            severity=impact,
            confidence=confidence,
            description=desc,
            affected_contract=", ".join(affected_contracts) if affected_contracts else None,
            source_locations=source_locations,
            source_file_paths=source_file_paths,
            raw_finding=item,
        )
        findings.append(finding.to_dict())

    return findings, summary, None


def run_slither_analysis(
    target_dir: str = "",
    protocol_record: Optional[Dict[str, Any]] = None,
    allowed_workspaces: Optional[List[str]] = None,
    timeout_seconds: int = 120,
    output_dir: Optional[str] = None,
    slither_executable: Optional[str] = None,
    verification_record: Optional[SourceVerificationRecord] = None,
    commit_hash: Optional[str] = None,
    default_branch: Optional[str] = None,
    backend: Optional[str] = None,
    docker_image: Optional[str] = None,
    docker_executable: Optional[str] = None,
    **kwargs,
) -> SlitherAnalysisResult:
    """
    Executes a controlled, gated Slither static analysis on a local repository directory.

    Enforces safety gates:
    1. Input directory exists and is inside an allowed workspace.
    2. Repository resolution confidence is RESOLVED_CONFIDENT (if protocol_record provided).
    3. Source verification status is SOURCE_VERIFIED.
    4. Deterministic Git commit hash is recorded.
    5. Slither executable or Docker container environment is available.

    Supports execution backends:
    - 'native': executes local Slither installation
    - 'docker': executes reproducible Slither container with --network none and :ro mount
    - 'auto': uses native Slither if available, falls back to Docker container

    Executes subprocess strictly without invoking a shell.
    """
    start_time = time.time()
    started_at = int(start_time)

    # Backward compatibility parameter aliases
    if not target_dir and "repo_dir" in kwargs:
        target_dir = kwargs["repo_dir"]
    if protocol_record is None and "protocol" in kwargs:
        protocol_record = kwargs["protocol"]
    if verification_record is None and "source_verification" in kwargs:
        verification_record = kwargs["source_verification"]

    proto_name = (protocol_record.get("protocol_name") or protocol_record.get("name") or "Unknown Protocol") if protocol_record else "Local Repository"
    proto_slug = (protocol_record.get("defillama_slug") or protocol_record.get("slug")) if protocol_record else None
    repo_url = protocol_record.get("selected_github_url") if protocol_record else None

    # Helper for fast rejection results
    def _create_rejection(
        status: AnalysisStatus,
        reason: str,
        ver_status: str = "VERIFICATION_NOT_RUN",
        exit_code: Optional[int] = None,
        wf_type: str = "REPOSITORY_CHECKOUT",
        execution_backend: str = "native",
    ) -> SlitherAnalysisResult:
        duration = time.time() - start_time
        eff_c = commit_hash
        return SlitherAnalysisResult(
            protocol_name=proto_name,
            defillama_slug=proto_slug,
            repository_url=repo_url,
            commit_hash=eff_c,
            source_verification_status=ver_status,
            slither_version=None,
            analysis_status=status,
            started_at=started_at,
            duration_seconds=round(duration, 3),
            exit_code=exit_code,
            execution_backend=execution_backend,
            workflow_type=wf_type,
            findings=[],
            findings_summary={"High": 0, "Medium": 0, "Low": 0, "Informational": 0, "Optimization": 0, "total": 0},
            reason=reason,
        )

    # Gate 1: Directory Existence
    if not target_dir or not os.path.exists(target_dir) or not os.path.isdir(target_dir):
        return _create_rejection(
            AnalysisStatus.PREREQUISITE_MISSING,
            f"Target directory '{target_dir}' does not exist or is not a directory.",
        )

    # Gate 2: Workspace Boundary Validation for target directory
    if not is_directory_in_workspace(target_dir, allowed_workspaces, check_exists=True):
        return _create_rejection(
            AnalysisStatus.NOT_ELIGIBLE,
            f"Security Gate Rejected: Target directory '{target_dir}' is outside the configured allowed workspace. "
            "Execution is forbidden to prevent directory traversal or inspecting unauthorized system files.",
        )

    # Gate 2b: Workspace Boundary Validation for output directory
    if output_dir:
        if not is_directory_in_workspace(output_dir, allowed_workspaces, check_exists=False):
            return _create_rejection(
                AnalysisStatus.NOT_ELIGIBLE,
                f"Security Gate Rejected: Output directory '{output_dir}' is outside the configured allowed workspace. "
                "Artifact generation is forbidden to prevent path traversal or overwriting unauthorized files.",
            )

    # Gate 3: Repository Resolution Status (Stage A)
    if protocol_record:
        raw_res_status = protocol_record.get("github_resolution_status")
        try:
            res_status = ResolutionStatus(raw_res_status)
        except (ValueError, TypeError):
            res_status = ResolutionStatus.UNRESOLVED

        if res_status != ResolutionStatus.RESOLVED_CONFIDENT:
            return _create_rejection(
                AnalysisStatus.NOT_ELIGIBLE,
                f"Security Gate Rejected: Protocol repository resolution status is '{res_status.value}'. "
                "Slither static analysis is permitted ONLY when repository resolution confidence is RESOLVED_CONFIDENT.",
            )

    # Gate 4: Source Verification & Identity Binding (Stage B)
    real_target = os.path.realpath(os.path.abspath(target_dir))
    if verification_record is not None:
        ver = verification_record
        if ver.repo_path:
            real_record_path = os.path.realpath(os.path.abspath(ver.repo_path))
            if real_record_path != real_target:
                return _create_rejection(
                    AnalysisStatus.NOT_ELIGIBLE,
                    f"Security Gate Rejected: Verification record repository path '{ver.repo_path}' does not match target directory '{target_dir}'. "
                    "Stale or cross-directory verification records are forbidden.",
                    ver_status=ver.status.value,
                )
    else:
        ver = verify_repository_source(
            repo_path=target_dir,
            default_branch=default_branch or (protocol_record.get("default_branch") if protocol_record else None),
            commit_hash=commit_hash or (protocol_record.get("commit_hash") if protocol_record else None),
        )

    wf_type = getattr(ver, "workflow_type", "REPOSITORY_CHECKOUT")
    is_git_repo = getattr(ver, "is_git_repo", False)

    if ver.status != SourceVerificationStatus.SOURCE_VERIFIED:
        return _create_rejection(
            AnalysisStatus.NOT_ELIGIBLE if ver.status == SourceVerificationStatus.VERIFICATION_NOT_RUN else AnalysisStatus.PREREQUISITE_MISSING,
            f"Security Gate Rejected: Source verification not satisfied (status: '{ver.status.value}'). Reason: {ver.reason}",
            ver_status=ver.status.value,
            wf_type=wf_type,
        )

    # Gate 4b: Caller commit mismatch check against verified checkout
    if commit_hash and is_git_repo and ver.commit_hash:
        if not ver.commit_hash.lower().startswith(commit_hash.lower()):
            return _create_rejection(
                AnalysisStatus.NOT_ELIGIBLE,
                f"Security Gate Rejected: Caller-supplied commit '{commit_hash}' does not match verified Git checkout commit '{ver.commit_hash}'.",
                ver_status=ver.status.value,
                wf_type=wf_type,
            )

    # Gate 5: Deterministic Git Commit Hash Requirement
    effective_commit = commit_hash or ver.commit_hash or (protocol_record.get("commit_hash") if protocol_record else None)
    if not effective_commit or not str(effective_commit).strip():
        return _create_rejection(
            AnalysisStatus.PREREQUISITE_MISSING,
            "Security Gate Rejected: Git commit hash could not be identified or recorded. "
            "Slither static analysis requires a recorded, immutable commit hash for audit traceability.",
            ver_status=ver.status.value,
            wf_type=wf_type,
        )

    # Gate 6: Slither Eligibility Evaluation (Stage C)
    gate_res_status = ResolutionStatus.RESOLVED_CONFIDENT
    if protocol_record and protocol_record.get("github_resolution_status"):
        try:
            gate_res_status = ResolutionStatus(protocol_record["github_resolution_status"])
        except ValueError:
            gate_res_status = ResolutionStatus.RESOLVED_CONFIDENT

    eligibility = evaluate_slither_eligibility(gate_res_status, ver)
    if not eligibility.get("eligible_for_slither", False):
        return _create_rejection(
            AnalysisStatus.NOT_ELIGIBLE,
            f"Security Gate Rejected: Slither eligibility check failed: {eligibility.get('reason')}",
            ver_status=ver.status.value,
            wf_type=wf_type,
        )

    # Gate 7: Tool Availability & Execution Backend Selection
    eff_backend = (backend or os.getenv("SLITHER_BACKEND", "auto")).lower()
    eff_image = docker_image or os.getenv("SLITHER_DOCKER_IMAGE", "defi-slither:0.10.4")

    selected_backend = "native"
    selected_version: Optional[str] = None

    if eff_backend == "docker":
        cli_ok, daemon_ok, doc_err = check_docker_availability(docker_executable)
        if not cli_ok:
            return _create_rejection(
                AnalysisStatus.TOOL_UNAVAILABLE,
                f"Docker execution backend selected but Docker CLI is unavailable: {doc_err}",
                ver_status=ver.status.value,
                wf_type=wf_type,
                execution_backend="docker",
            )
        if not daemon_ok:
            return _create_rejection(
                AnalysisStatus.TOOL_UNAVAILABLE,
                f"Docker execution backend selected but Docker daemon is offline: {doc_err}",
                ver_status=ver.status.value,
                wf_type=wf_type,
                execution_backend="docker",
            )
        img_ok, img_err = check_docker_image_availability(eff_image, docker_executable)
        if not img_ok:
            return _create_rejection(
                AnalysisStatus.TOOL_UNAVAILABLE,
                f"Docker execution backend selected but Docker image is unavailable: {img_err}",
                ver_status=ver.status.value,
                wf_type=wf_type,
                execution_backend="docker",
            )
        selected_backend = "docker"
        _, selected_version, _ = check_slither_availability(
            backend="docker",
            docker_image=eff_image,
            docker_executable=docker_executable,
        )

    elif eff_backend == "native":
        tool_ok, slither_version, tool_err = check_slither_availability(
            custom_executable=slither_executable,
            backend="native",
        )
        if not tool_ok:
            return _create_rejection(
                AnalysisStatus.TOOL_UNAVAILABLE,
                tool_err or "Slither static analyzer is unavailable.",
                ver_status=ver.status.value,
                wf_type=wf_type,
                execution_backend="native",
            )
        selected_backend = "native"
        selected_version = slither_version

    else:  # eff_backend == "auto"
        nat_ok, nat_ver, nat_err = check_slither_availability(
            custom_executable=slither_executable,
            backend="native",
        )
        if nat_ok:
            selected_backend = "native"
            selected_version = nat_ver
        else:
            cli_ok, daemon_ok, doc_err = check_docker_availability(docker_executable)
            if not cli_ok:
                return _create_rejection(
                    AnalysisStatus.TOOL_UNAVAILABLE,
                    f"Slither static analyzer is unavailable. Host: {nat_err}. Docker CLI: {doc_err}",
                    ver_status=ver.status.value,
                    wf_type=wf_type,
                    execution_backend="native",
                )
            if not daemon_ok:
                return _create_rejection(
                    AnalysisStatus.TOOL_UNAVAILABLE,
                    f"Slither static analyzer is unavailable. Host: {nat_err}. Docker daemon is offline: {doc_err}",
                    ver_status=ver.status.value,
                    wf_type=wf_type,
                    execution_backend="docker",
                )
            img_ok, img_err = check_docker_image_availability(eff_image, docker_executable)
            if not img_ok:
                return _create_rejection(
                    AnalysisStatus.TOOL_UNAVAILABLE,
                    f"Slither static analyzer is unavailable. Host: {nat_err}. Docker image '{eff_image}' is missing: {img_err}",
                    ver_status=ver.status.value,
                    wf_type=wf_type,
                    execution_backend="docker",
                )
            selected_backend = "docker"
            _, selected_version, _ = check_slither_availability(
                backend="docker",
                docker_image=eff_image,
                docker_executable=docker_executable,
            )

    # Stage C Execution: Run Slither via selected backend
    stdout_str = ""
    stderr_str = ""
    exit_code = None
    raw_json_str = ""

    if selected_backend == "docker":
        host_output_dir = tempfile.mkdtemp(prefix="slither_docker_out_")
        host_json_path = os.path.join(host_output_dir, "slither_out.json")
        cmd = build_docker_run_args(
            target_dir=real_target,
            host_output_dir=host_output_dir,
            image_name=eff_image,
            container_output_path="/output/slither_out.json",
            docker_executable=docker_executable,
        )
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                timeout=timeout_seconds,
            )
            stdout_str = proc.stdout
            stderr_str = proc.stderr
            exit_code = proc.returncode

            if os.path.exists(host_json_path):
                with open(host_json_path, "r", encoding="utf-8") as f:
                    raw_json_str = f.read()

        except subprocess.TimeoutExpired as e:
            duration = time.time() - start_time
            return SlitherAnalysisResult(
                protocol_name=proto_name,
                defillama_slug=proto_slug,
                repository_url=repo_url,
                commit_hash=effective_commit,
                source_verification_status=ver.status.value,
                slither_version=selected_version,
                analysis_status=AnalysisStatus.TIMED_OUT,
                started_at=started_at,
                duration_seconds=round(duration, 3),
                exit_code=None,
                execution_backend="docker",
                workflow_type=wf_type,
                findings=[],
                findings_summary={"High": 0, "Medium": 0, "Low": 0, "Informational": 0, "Optimization": 0, "total": 0},
                stderr_summary=sanitize_output_text(((e.stderr or "")[:1000]) if hasattr(e, "stderr") and e.stderr else "Docker container exceeded timeout threshold"),
                stdout_summary=sanitize_output_text(((e.stdout or "")[:1000]) if hasattr(e, "stdout") and e.stdout else ""),
                reason=f"Slither static analysis in Docker timed out after {timeout_seconds} seconds.",
            )
        except Exception as e:
            duration = time.time() - start_time
            return SlitherAnalysisResult(
                protocol_name=proto_name,
                defillama_slug=proto_slug,
                repository_url=repo_url,
                commit_hash=effective_commit,
                source_verification_status=ver.status.value,
                slither_version=selected_version,
                analysis_status=AnalysisStatus.ANALYSIS_FAILED,
                started_at=started_at,
                duration_seconds=round(duration, 3),
                exit_code=None,
                execution_backend="docker",
                workflow_type=wf_type,
                findings=[],
                findings_summary={"High": 0, "Medium": 0, "Low": 0, "Informational": 0, "Optimization": 0, "total": 0},
                stderr_summary=sanitize_output_text(str(e)),
                reason=f"Failed to execute Slither in Docker container: {e}",
            )
        finally:
            if os.path.exists(host_output_dir):
                shutil.rmtree(host_output_dir, ignore_errors=True)

    else:
        # Native execution
        slither_bin = shutil.which(slither_executable or "slither") or slither_executable or "slither"
        tmp_fd, output_json_path = tempfile.mkstemp(prefix="slither_tmp_", suffix=".json")
        os.close(tmp_fd)

        cmd = [
            slither_bin,
            ".",
            "--json",
            output_json_path,
        ]

        try:
            proc = subprocess.run(
                cmd,
                cwd=real_target,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                timeout=timeout_seconds,
            )
            stdout_str = proc.stdout
            stderr_str = proc.stderr
            exit_code = proc.returncode

            if os.path.exists(output_json_path):
                with open(output_json_path, "r", encoding="utf-8") as f:
                    raw_json_str = f.read()

        except subprocess.TimeoutExpired as e:
            duration = time.time() - start_time
            return SlitherAnalysisResult(
                protocol_name=proto_name,
                defillama_slug=proto_slug,
                repository_url=repo_url,
                commit_hash=effective_commit,
                source_verification_status=ver.status.value,
                slither_version=selected_version,
                analysis_status=AnalysisStatus.TIMED_OUT,
                started_at=started_at,
                duration_seconds=round(duration, 3),
                exit_code=None,
                execution_backend="native",
                workflow_type=wf_type,
                findings=[],
                findings_summary={"High": 0, "Medium": 0, "Low": 0, "Informational": 0, "Optimization": 0, "total": 0},
                stderr_summary=sanitize_output_text(((e.stderr or "")[:1000]) if hasattr(e, "stderr") and e.stderr else "Subprocess exceeded timeout threshold"),
                stdout_summary=sanitize_output_text(((e.stdout or "")[:1000]) if hasattr(e, "stdout") and e.stdout else ""),
                reason=f"Slither static analysis timed out after {timeout_seconds} seconds.",
            )
        except Exception as e:
            duration = time.time() - start_time
            return SlitherAnalysisResult(
                protocol_name=proto_name,
                defillama_slug=proto_slug,
                repository_url=repo_url,
                commit_hash=effective_commit,
                source_verification_status=ver.status.value,
                slither_version=selected_version,
                analysis_status=AnalysisStatus.ANALYSIS_FAILED,
                started_at=started_at,
                duration_seconds=round(duration, 3),
                exit_code=None,
                execution_backend="native",
                workflow_type=wf_type,
                findings=[],
                findings_summary={"High": 0, "Medium": 0, "Low": 0, "Informational": 0, "Optimization": 0, "total": 0},
                stderr_summary=sanitize_output_text(str(e)),
                reason=f"Failed to execute Slither subprocess: {e}",
            )
        finally:
            if output_json_path and os.path.exists(output_json_path):
                try:
                    os.remove(output_json_path)
                except OSError:
                    pass

    duration = time.time() - start_time

    # Fallback to stdout if JSON was printed to stdout
    if not raw_json_str and stdout_str and stdout_str.strip().startswith("{"):
        raw_json_str = stdout_str.strip()

    # Distinguish compilation / dependency failure from valid run
    findings: List[Dict[str, Any]] = []
    summary: Dict[str, int] = {}
    json_err = None

    if raw_json_str:
        findings, summary, json_err = parse_slither_json(raw_json_str)

    # Check for compile / dependency / compiler version errors
    compile_error_signatures = [
        "source file requires different compiler version",
        "file not found",
        "source not found",
        "compilation failed",
        "error compiling",
        "solc was not able to compile",
        "cannot find",
        "import not found",
        "no solc version matching",
        "solc-select install",
        "unsupported compiler",
        "compiler version",
    ]
    has_compile_error = any(sig in stderr_str.lower() for sig in compile_error_signatures) or (json_err and any(sig in json_err.lower() for sig in compile_error_signatures))

    # Add workflow-specific limitations
    active_limitations = list(STANDARD_LIMITATIONS)
    if wf_type == "LOCAL_UNTRACKED_SOURCE" or not is_git_repo:
        active_limitations.append(
            "Analyzed target is an untracked local source workflow without Git repository metadata; commit identity cannot be verified against upstream Git origin."
        )

    if has_compile_error and not findings:
        return SlitherAnalysisResult(
            protocol_name=proto_name,
            defillama_slug=proto_slug,
            repository_url=repo_url,
            commit_hash=effective_commit,
            source_verification_status=ver.status.value,
            slither_version=selected_version,
            analysis_status=AnalysisStatus.PREREQUISITE_MISSING,
            started_at=started_at,
            duration_seconds=round(duration, 3),
            exit_code=exit_code,
            execution_backend=selected_backend,
            workflow_type=wf_type,
            findings=[],
            findings_summary={"High": 0, "Medium": 0, "Low": 0, "Informational": 0, "Optimization": 0, "total": 0},
            stderr_summary=sanitize_output_text(stderr_str.strip()[:2000]),
            stdout_summary=sanitize_output_text(stdout_str.strip()[:1000]),
            limitations=active_limitations,
            reason="Slither compilation failed: external dependencies or required compiler version are missing or incompatible with the environment. "
                   "Arbitrary build scripts were not executed to preserve system safety.",
        )

    # Non-zero exit code analysis interpretation:
    # Exit codes >= 2 in Slither indicate fatal tool, compiler, or parameter failure.
    # Exit code 1 indicates vulnerabilities found (valid COMPLETED run if findings parsed).
    # Exit code 0 indicates clean run without findings (valid COMPLETED run).
    if exit_code is not None and exit_code >= 2:
        return SlitherAnalysisResult(
            protocol_name=proto_name,
            defillama_slug=proto_slug,
            repository_url=repo_url,
            commit_hash=effective_commit,
            source_verification_status=ver.status.value,
            slither_version=selected_version,
            analysis_status=AnalysisStatus.PREREQUISITE_MISSING if has_compile_error else AnalysisStatus.ANALYSIS_FAILED,
            started_at=started_at,
            duration_seconds=round(duration, 3),
            exit_code=exit_code,
            execution_backend=selected_backend,
            workflow_type=wf_type,
            findings=[],
            findings_summary={"High": 0, "Medium": 0, "Low": 0, "Informational": 0, "Optimization": 0, "total": 0},
            stderr_summary=sanitize_output_text((stderr_str.strip() or json_err or f"Process exited with code {exit_code}")[:2000]),
            stdout_summary=sanitize_output_text(stdout_str.strip()[:1000]),
            limitations=active_limitations,
            reason=f"Slither execution aborted (exit code {exit_code}): {json_err or stderr_str.strip()[:200] or 'tool failure'}",
        )

    # If exit code was non-zero and no findings/valid json was parsed
    if exit_code != 0 and not findings and (not raw_json_str or json_err):
        return SlitherAnalysisResult(
            protocol_name=proto_name,
            defillama_slug=proto_slug,
            repository_url=repo_url,
            commit_hash=effective_commit,
            source_verification_status=ver.status.value,
            slither_version=selected_version,
            analysis_status=AnalysisStatus.ANALYSIS_FAILED,
            started_at=started_at,
            duration_seconds=round(duration, 3),
            exit_code=exit_code,
            execution_backend=selected_backend,
            workflow_type=wf_type,
            findings=[],
            findings_summary={"High": 0, "Medium": 0, "Low": 0, "Informational": 0, "Optimization": 0, "total": 0},
            stderr_summary=sanitize_output_text((stderr_str.strip() or json_err or f"Process exited with code {exit_code}")[:2000]),
            stdout_summary=sanitize_output_text(stdout_str.strip()[:1000]),
            limitations=active_limitations,
            reason=f"Slither analysis failed (exit code {exit_code}): {json_err or stderr_str.strip()[:200]}",
        )

    # Save artifact if output_dir specified
    artifact_saved_path = None
    if output_dir:
        try:
            os.makedirs(output_dir, exist_ok=True)
            clean_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', proto_slug or "local_repo")
            clean_commit = re.sub(r'[^a-zA-Z0-9_-]', '_', str(effective_commit or "unknown"))
            artifact_filename = f"slither_{clean_slug}_{clean_commit[:8]}_{started_at}.json"
            planned_path = os.path.join(output_dir, artifact_filename)
            # Verify planned path remains inside allowed workspace
            if is_directory_in_workspace(planned_path, allowed_workspaces, check_exists=False):
                artifact_saved_path = planned_path
            else:
                logger.warning(f"Artifact path '{planned_path}' violates workspace boundary. Artifact write skipped.")
        except Exception as e:
            logger.warning(f"Could not prepare output artifact path: {e}")

    result = SlitherAnalysisResult(
        protocol_name=proto_name,
        defillama_slug=proto_slug,
        repository_url=repo_url,
        commit_hash=effective_commit,
        source_verification_status=ver.status.value,
        slither_version=selected_version,
        analysis_status=AnalysisStatus.COMPLETED,
        started_at=started_at,
        duration_seconds=round(duration, 3),
        exit_code=exit_code,
        execution_backend=selected_backend,
        workflow_type=wf_type,
        findings=findings,
        findings_summary=summary or {"High": 0, "Medium": 0, "Low": 0, "Informational": 0, "Optimization": 0, "total": 0},
        stderr_summary=sanitize_output_text(stderr_str.strip()[:2000]) if stderr_str else None,
        stdout_summary=sanitize_output_text(stdout_str.strip()[:1000]) if stdout_str else None,
        raw_artifact_path=artifact_saved_path,
        limitations=active_limitations,
        reason=f"Slither analysis completed successfully via {selected_backend}: {len(findings)} finding(s) detected across {ver.build_framework or 'Solidity'} codebase.",
    )

    if artifact_saved_path:
        try:
            with open(artifact_saved_path, "w", encoding="utf-8") as f:
                f.write(result.to_json())
        except Exception as e:
            logger.warning(f"Could not write Slither analysis artifact file: {e}")

    return result
