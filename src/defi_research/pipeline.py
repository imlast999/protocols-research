"""
pipeline.py
Phase 4 — End-to-End Pipeline Orchestration & Consolidated Security Intelligence

Integrates:
1. Discovery (DefiLlama protocol metadata)
2. Repository Resolution (GitHub candidate extraction & confidence grading)
3. Source Verification (Local workspace & Solidity build framework check)
4. Static Analysis (Controlled sandboxed Slither execution)
5. Security Intelligence (CertiK Skynet API & Audit records)
6. Consolidated Structured Reporting (Aggregated JSON reports with strict stage statuses)

Safety and correctness guarantees:
- Ambiguous or non-confident repositories are never automatically analyzed.
- Slither only executes if all eligibility and source-verification gates pass.
- Missing external credentials (e.g., CertiK API key) or missing Slither CLI do not crash the pipeline.
- Analysis provenance (commit hash, timestamps) is preserved.
- Output files are constrained strictly to the configured output directory.
- No secrets or credentials are ever leaked into reports or terminal output.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Robust imports supporting both package-level and standalone usage
try:
    from defi_research.config import DEFAULT_OUTPUT_DIR
    from defi_research.github_repository_resolver import (
        DefiLlamaClient,
        ResolutionStatus,
        SourceVerificationRecord,
        SourceVerificationStatus,
        evaluate_slither_eligibility,
        normalize_protocol_record,
        resolve_github_repo_from_slug,
        resolve_github_repository,
        verify_repository_source,
    )
    from defi_research.market_intelligence import get_high_value_targets
    from defi_research.notrack_script import get_vulnerable_targets
    from defi_research.security_intelligence import (
        ProtocolQuery,
        RetrievalStatus,
        SecurityIntelligenceResult,
        query_protocol_security_intelligence,
        sanitize_secret_text,
    )
    from defi_research.slither_runner import (
        AnalysisStatus,
        SlitherAnalysisResult,
        check_slither_availability,
        is_directory_in_workspace,
        run_slither_analysis,
        sanitize_output_text,
    )
except ImportError:
    from config import DEFAULT_OUTPUT_DIR
    from github_repository_resolver import (
        DefiLlamaClient,
        ResolutionStatus,
        SourceVerificationRecord,
        SourceVerificationStatus,
        evaluate_slither_eligibility,
        normalize_protocol_record,
        resolve_github_repo_from_slug,
        resolve_github_repository,
        verify_repository_source,
    )
    from market_intelligence import get_high_value_targets
    from notrack_script import get_vulnerable_targets
    from security_intelligence import (
        ProtocolQuery,
        RetrievalStatus,
        SecurityIntelligenceResult,
        query_protocol_security_intelligence,
        sanitize_secret_text,
    )
    from slither_runner import (
        AnalysisStatus,
        SlitherAnalysisResult,
        check_slither_availability,
        is_directory_in_workspace,
        run_slither_analysis,
        sanitize_output_text,
    )

logger = logging.getLogger("PipelineOrchestrator")


class StageStatus(str, Enum):
    """Execution status for an individual pipeline stage."""
    SUCCESS = "SUCCESS"
    SKIPPED = "SKIPPED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


class PipelineOverallStatus(str, Enum):
    """Aggregate lifecycle status for a protocol's pipeline run."""
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


# Standard static analysis and security intelligence limitations
STANDARD_LIMITATIONS: List[str] = [
    "Static analysis cannot detect complex economic exploits, price oracle manipulation, or flash loan arbitrage.",
    "Repository source code is not guaranteed to correspond to on-chain deployed bytecode without formal bytecode verification.",
    "External security scores and audit counts do not guarantee smart contract immunity from zero-day vulnerabilities.",
    "External security intelligence is informational and never bypasses repository verification or Slither eligibility gates.",
    "On-chain access modifier scanning is heuristic regex pattern matching on verified explorer source code; it cannot prove bytecode equivalence or catch complex multi-contract delegatecall authorization schemes.",
]


@dataclass
class StageOutcome:
    """Detailed result of a single pipeline stage."""
    status: StageStatus
    message: str
    started_at: str = ""
    completed_at: str = ""
    duration_seconds: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "message": self.message,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": round(self.duration_seconds, 3),
            "details": self.details,
        }


@dataclass
class ProtocolPipelineReport:
    """Structured, consolidated output for a protocol analysis run."""
    protocol_name: str
    defillama_slug: str
    defillama_id: Optional[str] = None
    chain: Optional[str] = None
    category: Optional[str] = None
    tvl: Optional[float] = None

    # Lifecycle timing
    started_at: str = ""
    completed_at: str = ""
    duration_seconds: float = 0.0
    overall_status: PipelineOverallStatus = PipelineOverallStatus.PARTIAL

    # Per-stage outcomes
    stages: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Stage 2: Repository resolution details
    repository_url: Optional[str] = None
    repository_resolution_status: Optional[str] = None
    repository_confidence_reason: Optional[str] = None

    # Stage 3: Source verification details
    source_verification_status: Optional[str] = None
    analyzed_commit: Optional[str] = None

    # Stage 4: Static analysis details
    static_analysis_status: Optional[str] = None
    slither_version: Optional[str] = None
    slither_findings_count: Optional[int] = None
    slither_findings_summary: Dict[str, int] = field(default_factory=dict)
    slither_diagnostics: Optional[str] = None
    slither_execution_backend: Optional[str] = None

    # Stage 4b: On-chain heuristic permission analysis details
    permission_scanner_status: Optional[str] = None
    permission_scanner_contract: Optional[str] = None
    permission_scanner_critical_count: Optional[int] = None
    permission_scanner_findings: List[Dict[str, Any]] = field(default_factory=list)
    permission_scanner_diagnostics: Optional[str] = None

    # Stage 5: Security intelligence details
    security_intelligence_status: Optional[str] = None
    security_score: Optional[float] = None          # None if unknown, NEVER 0!
    security_score_grade: Optional[str] = None
    certik_project_id: Optional[str] = None
    audit_count: Optional[int] = None

    # Diagnostic metadata
    errors: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=lambda: list(STANDARD_LIMITATIONS))

    def to_dict(self) -> Dict[str, Any]:
        """Convert report to a clean, JSON-serializable dictionary."""
        return {
            "protocol_name": self.protocol_name,
            "defillama_slug": self.defillama_slug,
            "defillama_id": self.defillama_id,
            "chain": self.chain,
            "category": self.category,
            "tvl": self.tvl,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": round(self.duration_seconds, 3),
            "overall_status": self.overall_status.value,
            "stages": self.stages,
            "repository": {
                "url": self.repository_url,
                "resolution_status": self.repository_resolution_status,
                "confidence_reason": self.repository_confidence_reason,
            },
            "source_verification": {
                "status": self.source_verification_status,
                "analyzed_commit": self.analyzed_commit,
            },
            "static_analysis": {
                "status": self.static_analysis_status,
                "execution_backend": self.slither_execution_backend,
                "slither_version": self.slither_version,
                "findings_count": self.slither_findings_count,
                "findings_summary": self.slither_findings_summary,
                "diagnostics": self.slither_diagnostics,
            },
            "on_chain_permission_analysis": {
                "status": self.permission_scanner_status,
                "contract_address": self.permission_scanner_contract,
                "critical_count": self.permission_scanner_critical_count,
                "findings": self.permission_scanner_findings,
                "diagnostics": self.permission_scanner_diagnostics,
            },
            "security_intelligence": {
                "status": self.security_intelligence_status,
                "security_score": self.security_score,
                "security_score_grade": self.security_score_grade,
                "certik_project_id": self.certik_project_id,
                "audit_count": self.audit_count,
            },
            "errors": self.errors,
            "limitations": self.limitations,
        }

    def to_json(self, indent: int = 2) -> str:
        """Produce formatted JSON string with credentials and secrets redacted."""
        raw = json.dumps(self.to_dict(), indent=indent, default=str)
        return sanitize_secret_text(raw) or ""


@dataclass
class PipelineRunSummary:
    """Run-level summary for multi-protocol batch pipeline runs."""
    started_at: str
    completed_at: str
    total_protocols: int
    completed_count: int
    partial_count: int
    failed_count: int
    reports: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "total_protocols": self.total_protocols,
            "completed_count": self.completed_count,
            "partial_count": self.partial_count,
            "failed_count": self.failed_count,
            "reports": self.reports,
        }

    def to_json(self, indent: int = 2) -> str:
        """Produce formatted JSON string with credentials and secrets redacted."""
        raw = json.dumps(self.to_dict(), indent=indent, default=str)
        return sanitize_secret_text(raw) or ""


def sanitize_filename_component(name: str) -> str:
    """Sanitize a string for safe filesystem usage."""
    sanitized = re.sub(r"[^\w\-.]", "_", name)
    return sanitized[:64].strip("._") or "protocol"


def save_protocol_report(
    report: ProtocolPipelineReport,
    output_dir: Optional[str] = None,
) -> str:
    """
    Serialize and write a ProtocolPipelineReport to JSON inside the output directory.
    Guarantees output stays strictly inside the configured directory.
    """
    target_dir = Path(output_dir or os.path.join(DEFAULT_OUTPUT_DIR, "pipeline")).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    safe_slug = sanitize_filename_component(report.defillama_slug)
    timestamp = int(datetime.now(timezone.utc).timestamp())
    filename = f"pipeline_{safe_slug}_{timestamp}.json"
    file_path = (target_dir / filename).resolve()

    # Boundary check: ensure target file is strictly inside target directory
    try:
        file_path.relative_to(target_dir)
    except ValueError:
        raise ValueError(f"Path traversal detected: {file_path} escapes {target_dir}")

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(report.to_json(indent=2))

    return str(file_path)


def run_protocol_pipeline(
    protocol_input: Union[str, Dict[str, Any]],
    target_dir: Optional[str] = None,
    commit_hash: Optional[str] = None,
    output_dir: Optional[str] = None,
    live_provider: bool = False,
    client: Optional[DefiLlamaClient] = None,
    allowed_workspaces: Optional[List[str]] = None,
    slither_backend: Optional[str] = None,
    docker_image: Optional[str] = None,
    contract_address: Optional[str] = None,
    skip_permission_scan: bool = False,
) -> ProtocolPipelineReport:
    """
    Execute the end-to-end 8-stage pipeline for a single DeFi protocol.

    Stages:
    1. Discovery: Extract and normalize protocol metadata from DefiLlama.
    2. Repository Resolution: Identify canonical GitHub repository and confidence.
    3. Source Verification: Verify local files, Solidity presence, and build framework.
    4. Static Analysis: Sandboxed Slither analysis (strictly gated on verification).
    4b. Permission Analysis: On-chain heuristic ABI modifier scan on verified contracts.
    5. Security Intelligence: Query CertiK Skynet score and audit records.
    6. Aggregation: Consolidate outcomes, verify errors, and determine overall status.
    7. Persistence: Save structured JSON artifact if output_dir is configured.
    """
    t_start = time.time()
    iso_start = datetime.now(timezone.utc).isoformat()

    # -------------------------------------------------------------------------
    # Stage 1: Discovery
    # -------------------------------------------------------------------------
    t_stage = time.time()
    s1_iso_start = datetime.now(timezone.utc).isoformat()
    stage_outcomes: Dict[str, StageOutcome] = {}
    normalized: Dict[str, Any] = {}
    errors: List[str] = []

    try:
        if isinstance(protocol_input, str):
            slug_or_name = protocol_input.strip()
            defillama_client = client or DefiLlamaClient()
            raw_meta = defillama_client.get_protocol(slug_or_name)
            if not raw_meta:
                # If lookup by slug failed, record failure
                stage_outcomes["discovery"] = StageOutcome(
                    status=StageStatus.FAILED,
                    message=f"Protocol '{slug_or_name}' not found on DefiLlama",
                    started_at=s1_iso_start,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    duration_seconds=time.time() - t_stage,
                )
                errors.append(f"Discovery failed: protocol '{slug_or_name}' not found")
                return ProtocolPipelineReport(
                    protocol_name=slug_or_name,
                    defillama_slug=slug_or_name,
                    started_at=iso_start,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    duration_seconds=time.time() - t_start,
                    overall_status=PipelineOverallStatus.FAILED,
                    stages={k: v.to_dict() for k, v in stage_outcomes.items()},
                    errors=errors,
                )
            normalized = normalize_protocol_record(raw_meta, requested_slug=slug_or_name)
        elif isinstance(protocol_input, dict):
            # Already a protocol record dictionary
            req_slug = protocol_input.get("defillama_slug") or protocol_input.get("slug")
            normalized = normalize_protocol_record(protocol_input, requested_slug=req_slug)
        else:
            raise TypeError("protocol_input must be a string slug or a protocol dict")

        stage_outcomes["discovery"] = StageOutcome(
            status=StageStatus.SUCCESS,
            message="Protocol metadata successfully resolved and normalized",
            started_at=s1_iso_start,
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=time.time() - t_stage,
            details={
                "name": normalized.get("name"),
                "slug": normalized.get("slug"),
                "id": normalized.get("id"),
                "chain": normalized.get("chain"),
                "category": normalized.get("category"),
                "tvl": normalized.get("tvl"),
            },
        )
    except Exception as exc:
        stage_outcomes["discovery"] = StageOutcome(
            status=StageStatus.FAILED,
            message=f"Discovery exception: {exc}",
            started_at=s1_iso_start,
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=time.time() - t_stage,
        )
        errors.append(f"Discovery error: {exc}")
        return ProtocolPipelineReport(
            protocol_name=str(protocol_input),
            defillama_slug=str(protocol_input),
            started_at=iso_start,
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=time.time() - t_start,
            overall_status=PipelineOverallStatus.FAILED,
            stages={k: v.to_dict() for k, v in stage_outcomes.items()},
            errors=errors,
        )

    protocol_name = normalized.get("name") or normalized.get("slug") or "Unknown"
    slug = normalized.get("slug") or "unknown"
    defillama_id = normalized.get("id")
    chain = normalized.get("chain")
    category = normalized.get("category")
    tvl = normalized.get("tvl")

    # -------------------------------------------------------------------------
    # Stage 2: Repository Resolution
    # -------------------------------------------------------------------------
    t_stage = time.time()
    s2_iso_start = datetime.now(timezone.utc).isoformat()
    repo_url: Optional[str] = None
    res_status = ResolutionStatus.UNRESOLVED
    res_reason = "No candidate repositories identified"

    try:
        status_str = normalized.get("github_resolution_status")
        if status_str:
            res_status = ResolutionStatus(status_str)
            repo_url = normalized.get("selected_github_url")
            evidence = normalized.get("resolution_evidence", {})
            res_reason = evidence.get("reason", "No reason provided")
        else:
            res_status, repo_url, evidence = resolve_github_repository(normalized)
            res_reason = evidence.get("reason", "No reason provided")

        stage_status_map = {
            ResolutionStatus.RESOLVED_CONFIDENT: StageStatus.SUCCESS,
            ResolutionStatus.RESOLVED_LOW_CONFIDENCE: StageStatus.PARTIAL,
            ResolutionStatus.ORGANIZATION_ONLY: StageStatus.PARTIAL,
            ResolutionStatus.AMBIGUOUS: StageStatus.FAILED,
            ResolutionStatus.UNRESOLVED: StageStatus.FAILED,
            ResolutionStatus.MALFORMED_RECORD: StageStatus.FAILED,
        }
        s2_status = stage_status_map.get(res_status, StageStatus.FAILED)

        stage_outcomes["repository_resolution"] = StageOutcome(
            status=s2_status,
            message=res_reason,
            started_at=s2_iso_start,
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=time.time() - t_stage,
            details={
                "repository_url": repo_url,
                "resolution_status": res_status.value if hasattr(res_status, "value") else str(res_status),
                "reason": res_reason,
            },
        )
    except Exception as exc:
        stage_outcomes["repository_resolution"] = StageOutcome(
            status=StageStatus.FAILED,
            message=f"Repository resolution error: {exc}",
            started_at=s2_iso_start,
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=time.time() - t_stage,
        )
        errors.append(f"Repository resolution error: {exc}")

    # -------------------------------------------------------------------------
    # Stage 3: Source Verification
    # -------------------------------------------------------------------------
    t_stage = time.time()
    s3_iso_start = datetime.now(timezone.utc).isoformat()
    verification_record: Optional[SourceVerificationRecord] = None
    verification_status_str: Optional[str] = None

    if not target_dir:
        # Local source not provided; source verification and static analysis skipped
        stage_outcomes["source_verification"] = StageOutcome(
            status=StageStatus.SKIPPED,
            message="No local target directory provided for source verification",
            started_at=s3_iso_start,
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=time.time() - t_stage,
        )
    elif allowed_workspaces and not is_directory_in_workspace(target_dir, allowed_workspaces):
        stage_outcomes["source_verification"] = StageOutcome(
            status=StageStatus.NOT_ELIGIBLE,
            message=f"Source verification blocked: target directory '{target_dir}' is outside authorized workspaces",
            started_at=s3_iso_start,
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=time.time() - t_stage,
        )
    elif res_status != ResolutionStatus.RESOLVED_CONFIDENT:
        stage_outcomes["source_verification"] = StageOutcome(
            status=StageStatus.NOT_ELIGIBLE,
            message=f"Source verification blocked: repository resolution status '{res_status.value}' is not RESOLVED_CONFIDENT ({res_reason})",
            started_at=s3_iso_start,
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=time.time() - t_stage,
        )
    else:
        try:
            verification_record = verify_repository_source(
                repo_path=target_dir,
                commit_hash=commit_hash,
            )
            verification_status_str = verification_record.status.value

            if verification_record.status == SourceVerificationStatus.SOURCE_VERIFIED:
                stage_outcomes["source_verification"] = StageOutcome(
                    status=StageStatus.SUCCESS,
                    message=verification_record.reason or "Source code and build config verified",
                    started_at=s3_iso_start,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    duration_seconds=time.time() - t_stage,
                    details={
                        "status": verification_status_str,
                        "build_framework": verification_record.build_framework,
                        "solidity_files_count": len(verification_record.solidity_source_paths),
                        "commit_hash": verification_record.commit_hash,
                    },
                )
            else:
                stage_outcomes["source_verification"] = StageOutcome(
                    status=StageStatus.FAILED,
                    message=verification_record.reason or f"Verification failed: {verification_status_str}",
                    started_at=s3_iso_start,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    duration_seconds=time.time() - t_stage,
                    details={
                        "status": verification_status_str,
                        "branch_deployment_limitation": verification_record.branch_deployment_limitation,
                    },
                )
        except Exception as exc:
            stage_outcomes["source_verification"] = StageOutcome(
                status=StageStatus.FAILED,
                message=f"Source verification error: {exc}",
                started_at=s3_iso_start,
                completed_at=datetime.now(timezone.utc).isoformat(),
                duration_seconds=time.time() - t_stage,
            )
            errors.append(f"Source verification error: {exc}")

    # -------------------------------------------------------------------------
    # Stage 4: Static Analysis (Slither)
    # -------------------------------------------------------------------------
    t_stage = time.time()
    s4_iso_start = datetime.now(timezone.utc).isoformat()
    slither_result: Optional[SlitherAnalysisResult] = None

    # Gate: Slither must strictly run only when verification is SOURCE_VERIFIED
    if not target_dir:
        stage_outcomes["static_analysis"] = StageOutcome(
            status=StageStatus.SKIPPED,
            message="Static analysis skipped: local source directory not specified",
            started_at=s4_iso_start,
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=time.time() - t_stage,
        )
    elif not verification_record or verification_record.status != SourceVerificationStatus.SOURCE_VERIFIED:
        stage_outcomes["static_analysis"] = StageOutcome(
            status=StageStatus.NOT_ELIGIBLE,
            message="Static analysis blocked: repository source verification did not pass",
            started_at=s4_iso_start,
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=time.time() - t_stage,
        )
    else:
        elig = evaluate_slither_eligibility(res_status, verification_record)
        if not elig.get("eligible_for_slither", False):
            stage_outcomes["static_analysis"] = StageOutcome(
                status=StageStatus.NOT_ELIGIBLE,
                message=elig.get("reason", "Repository not eligible for Slither"),
                started_at=s4_iso_start,
                completed_at=datetime.now(timezone.utc).isoformat(),
                duration_seconds=time.time() - t_stage,
            )
        else:
            # Check tool availability before running
            res_avail = check_slither_availability(backend=slither_backend, docker_image=docker_image)
            if isinstance(res_avail, tuple) and len(res_avail) == 3:
                avail_ok = res_avail[0]
                avail_diag = res_avail[2] or res_avail[1] or "Slither is available"
            elif isinstance(res_avail, tuple) and len(res_avail) == 2:
                avail_ok, avail_diag = res_avail
            else:
                avail_ok = bool(res_avail)
                avail_diag = "Available" if avail_ok else "Unavailable"

            if not avail_ok:
                stage_outcomes["static_analysis"] = StageOutcome(
                    status=StageStatus.UNAVAILABLE,
                    message=avail_diag,
                    started_at=s4_iso_start,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    duration_seconds=time.time() - t_stage,
                )
            else:
                try:
                    slither_result = run_slither_analysis(
                        repo_dir=target_dir,
                        protocol=normalized,
                        source_verification=verification_record,
                        commit_hash=commit_hash or verification_record.analyzed_commit,
                        allowed_workspaces=allowed_workspaces,
                        backend=slither_backend,
                        docker_image=docker_image,
                    )

                    sl_status = getattr(slither_result, "analysis_status", getattr(slither_result, "status", AnalysisStatus.ANALYSIS_FAILED))
                    sl_diag = getattr(slither_result, "reason", getattr(slither_result, "diagnostics", getattr(slither_result, "stderr_summary", "")))
                    sl_backend = getattr(slither_result, "execution_backend", "native")

                    if sl_status == AnalysisStatus.COMPLETED:
                        findings_summary: Dict[str, int] = {}
                        for f in slither_result.findings:
                            sev = getattr(f, "severity", f.get("impact", f.get("severity", "Informational")) if isinstance(f, dict) else "Informational")
                            findings_summary[sev] = findings_summary.get(sev, 0) + 1

                        stage_outcomes["static_analysis"] = StageOutcome(
                            status=StageStatus.SUCCESS,
                            message=f"Analysis completed with {len(slither_result.findings)} findings via {sl_backend}",
                            started_at=s4_iso_start,
                            completed_at=datetime.now(timezone.utc).isoformat(),
                            duration_seconds=slither_result.duration_seconds,
                            details={
                                "status": sl_status.value,
                                "execution_backend": sl_backend,
                                "slither_version": slither_result.slither_version,
                                "findings_count": len(slither_result.findings),
                                "findings_summary": findings_summary,
                                "exit_code": slither_result.exit_code,
                            },
                        )
                    elif sl_status == AnalysisStatus.TOOL_UNAVAILABLE:
                        stage_outcomes["static_analysis"] = StageOutcome(
                            status=StageStatus.UNAVAILABLE,
                            message=sl_diag,
                            started_at=s4_iso_start,
                            completed_at=datetime.now(timezone.utc).isoformat(),
                            duration_seconds=slither_result.duration_seconds,
                            details={"execution_backend": sl_backend},
                        )
                    elif sl_status == AnalysisStatus.NOT_ELIGIBLE:
                        stage_outcomes["static_analysis"] = StageOutcome(
                            status=StageStatus.NOT_ELIGIBLE,
                            message=sl_diag,
                            started_at=s4_iso_start,
                            completed_at=datetime.now(timezone.utc).isoformat(),
                            duration_seconds=slither_result.duration_seconds,
                            details={"execution_backend": sl_backend},
                        )
                    else:
                        stage_outcomes["static_analysis"] = StageOutcome(
                            status=StageStatus.FAILED,
                            message=sl_diag or f"Slither status: {sl_status.value}",
                            started_at=s4_iso_start,
                            completed_at=datetime.now(timezone.utc).isoformat(),
                            duration_seconds=slither_result.duration_seconds,
                            details={"exit_code": slither_result.exit_code, "execution_backend": sl_backend},
                        )
                except Exception as exc:
                    stage_outcomes["static_analysis"] = StageOutcome(
                        status=StageStatus.FAILED,
                        message=f"Static analysis error: {exc}",
                        started_at=s4_iso_start,
                        completed_at=datetime.now(timezone.utc).isoformat(),
                        duration_seconds=time.time() - t_stage,
                    )
                    errors.append(f"Static analysis error: {exc}")

    # -------------------------------------------------------------------------
    # Stage 4b: On-Chain Permission Analysis (ContractScanner / Explorer ABI)
    # -------------------------------------------------------------------------
    t_stage = time.time()
    s4b_iso_start = datetime.now(timezone.utc).isoformat()
    perm_findings: List[Dict[str, Any]] = []
    perm_critical_count: Optional[int] = None
    perm_status: StageStatus = StageStatus.SKIPPED
    perm_contract: Optional[str] = None
    perm_diag: str = ""

    candidate_addr = contract_address or normalized.get("address")
    if not candidate_addr and normalized.get("contracts"):
        c_list = normalized.get("contracts")
        if isinstance(c_list, list) and len(c_list) > 0:
            candidate_addr = c_list[0]

    if candidate_addr and isinstance(candidate_addr, str):
        try:
            from defi_research.market_intelligence import clean_contract_address
        except ImportError:
            from market_intelligence import clean_contract_address
        cleaned = clean_contract_address(candidate_addr)
        if cleaned:
            perm_contract = cleaned

    if skip_permission_scan:
        perm_status = StageStatus.SKIPPED
        perm_diag = "On-chain permission scanning skipped by configuration"
        stage_outcomes["permission_analysis"] = StageOutcome(
            status=perm_status,
            message=perm_diag,
            started_at=s4b_iso_start,
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=time.time() - t_stage,
        )
    elif not perm_contract:
        perm_status = StageStatus.SKIPPED
        perm_diag = "No valid on-chain contract address available for permission scanning"
        stage_outcomes["permission_analysis"] = StageOutcome(
            status=perm_status,
            message=perm_diag,
            started_at=s4b_iso_start,
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=time.time() - t_stage,
        )
    else:
        try:
            from defi_research.config import CHAINS
            from defi_research.market_intelligence import CHAIN_ALIAS_MAP
        except ImportError:
            from config import CHAINS
            from market_intelligence import CHAIN_ALIAS_MAP

        raw_chain = (chain or "").lower()
        effective_chain = CHAIN_ALIAS_MAP.get(raw_chain, raw_chain)

        if effective_chain not in CHAINS:
            perm_status = StageStatus.SKIPPED
            perm_diag = f"Chain '{effective_chain}' is not supported for explorer ABI retrieval (Supported: {list(CHAINS.keys())})"
            stage_outcomes["permission_analysis"] = StageOutcome(
                status=perm_status,
                message=perm_diag,
                started_at=s4b_iso_start,
                completed_at=datetime.now(timezone.utc).isoformat(),
                duration_seconds=time.time() - t_stage,
                details={"contract_address": perm_contract, "chain": effective_chain},
            )
        else:
            try:
                try:
                    from defi_research.contract_permission_scanner import ContractScanner
                except ImportError:
                    from contract_permission_scanner import ContractScanner

                scanner = ContractScanner(chain_key=effective_chain)
                scan_res = scanner.scan_contract(perm_contract)

                perm_findings = scan_res.get("findings", [])
                perm_critical_count = scan_res.get("critical_count", 0)
                perm_status = StageStatus.SUCCESS
                contract_name = scan_res.get("contract_name", "UnknownContract")
                perm_diag = (
                    f"Heuristic access modifier analysis completed on verified contract '{contract_name}' "
                    f"({len(perm_findings)} sensitive functions flagged, {perm_critical_count} critical/high)"
                )

                stage_outcomes["permission_analysis"] = StageOutcome(
                    status=perm_status,
                    message=perm_diag,
                    started_at=s4b_iso_start,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    duration_seconds=time.time() - t_stage,
                    details={
                        "contract_address": perm_contract,
                        "contract_name": contract_name,
                        "chain": effective_chain,
                        "findings_count": len(perm_findings),
                        "critical_count": perm_critical_count,
                        "analysis_type": "HEURISTIC",
                        "tvl_data": scan_res.get("tvl_data"),
                    },
                )
            except Exception as scan_err:
                err_str = str(scan_err)
                logger.warning(f"Permission scanner error for {perm_contract} on {effective_chain}: {err_str}")
                perm_status = StageStatus.FAILED
                perm_diag = sanitize_secret_text(f"Permission scanner error: {err_str}") or ""
                stage_outcomes["permission_analysis"] = StageOutcome(
                    status=perm_status,
                    message=perm_diag,
                    started_at=s4b_iso_start,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    duration_seconds=time.time() - t_stage,
                    details={"contract_address": perm_contract, "chain": effective_chain},
                )

    # -------------------------------------------------------------------------
    # Stage 5: Security Intelligence (CertiK / Skynet)
    # -------------------------------------------------------------------------
    t_stage = time.time()
    s5_iso_start = datetime.now(timezone.utc).isoformat()
    intel_result: Optional[SecurityIntelligenceResult] = None

    try:
        intel_result = query_protocol_security_intelligence(
            protocol_slug=slug,
            protocol_name=protocol_name,
            chain=chain,
            website=normalized.get("url"),
        )

        intel_status_map = {
            RetrievalStatus.SUCCESS: StageStatus.SUCCESS,
            RetrievalStatus.NOT_CONFIGURED: StageStatus.NOT_CONFIGURED,
            RetrievalStatus.NOT_FOUND: StageStatus.PARTIAL,
            RetrievalStatus.UNAVAILABLE: StageStatus.UNAVAILABLE,
            RetrievalStatus.AMBIGUOUS: StageStatus.FAILED,
            RetrievalStatus.PROVIDER_ERROR: StageStatus.FAILED,
            RetrievalStatus.RATE_LIMITED: StageStatus.FAILED,
            RetrievalStatus.AUTHENTICATION_FAILED: StageStatus.FAILED,
            RetrievalStatus.INVALID_RESPONSE: StageStatus.FAILED,
        }
        s5_status = intel_status_map.get(intel_result.retrieval_status, StageStatus.FAILED)

        details_dict: Dict[str, Any] = {
            "retrieval_status": intel_result.retrieval_status.value,
            "provider": intel_result.provider_name,
            "match_confidence": intel_result.match_confidence.value,
        }
        if intel_result.security_score is not None:
            details_dict["security_score"] = intel_result.security_score.score
            details_dict["security_score_tier"] = intel_result.security_score.tier
        if intel_result.audit_records:
            details_dict["audit_count"] = len(intel_result.audit_records)

        stage_outcomes["security_intelligence"] = StageOutcome(
            status=s5_status,
            message=sanitize_secret_text(intel_result.diagnostic) or f"Retrieval status: {intel_result.retrieval_status.value}",
            started_at=s5_iso_start,
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=time.time() - t_stage,
            details=details_dict,
        )
    except Exception as exc:
        stage_outcomes["security_intelligence"] = StageOutcome(
            status=StageStatus.FAILED,
            message=f"Security intelligence error: {exc}",
            started_at=s5_iso_start,
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=time.time() - t_stage,
        )
        errors.append(f"Security intelligence error: {exc}")

    # -------------------------------------------------------------------------
    # Stage 6: Aggregate Consolidated Report
    # -------------------------------------------------------------------------
    iso_end = datetime.now(timezone.utc).isoformat()
    duration_total = time.time() - t_start

    # Determine Overall Lifecycle Status:
    # COMPLETED: All critical and applicable core stages executed with SUCCESS
    # PARTIAL: Some stages succeeded, others skipped / unconfigured / unavailable / failed
    # FAILED: Critical discovery or resolution stages failed
    critical_statuses = [
        stage_outcomes["discovery"].status,
        stage_outcomes["repository_resolution"].status,
    ]

    perm_outcome = stage_outcomes.get("permission_analysis")
    perm_success = (
        perm_outcome is None
        or perm_outcome.status == StageStatus.SUCCESS
        or (perm_outcome.status == StageStatus.SKIPPED and (not candidate_addr or skip_permission_scan))
    )

    core_stages_success = all(
        outcome.status == StageStatus.SUCCESS
        for key, outcome in stage_outcomes.items()
        if key != "permission_analysis"
    )

    if any(s == StageStatus.FAILED for s in critical_statuses):
        overall_status = PipelineOverallStatus.FAILED
    elif core_stages_success and perm_success:
        overall_status = PipelineOverallStatus.COMPLETED
    else:
        overall_status = PipelineOverallStatus.PARTIAL

    # Slither details extraction
    static_status_str = stage_outcomes["static_analysis"].status.value
    slither_ver = slither_result.slither_version if slither_result else None
    findings_count = len(slither_result.findings) if slither_result and slither_result.findings is not None else None
    findings_summary: Dict[str, int] = {}
    if slither_result and slither_result.findings:
        for f in slither_result.findings:
            sev = getattr(f, "severity", f.get("impact", f.get("severity", "Informational")) if isinstance(f, dict) else "Informational")
            findings_summary[sev] = findings_summary.get(sev, 0) + 1
    slither_diag = getattr(slither_result, "reason", getattr(slither_result, "stderr_summary", getattr(slither_result, "diagnostics", ""))) if slither_result else stage_outcomes["static_analysis"].message

    # Intelligence details extraction
    sec_status_str = stage_outcomes["security_intelligence"].status.value
    sec_score = intel_result.security_score.score if (intel_result and intel_result.security_score) else None
    sec_grade = intel_result.security_score.tier if (intel_result and intel_result.security_score) else None
    certik_proj = intel_result.provider_project_id if intel_result else None
    audit_cnt = len(intel_result.audit_records) if (intel_result and intel_result.audit_records is not None) else None

    # Commit provenance
    resolved_commit = commit_hash or (verification_record.commit_hash if verification_record else None)

    report = ProtocolPipelineReport(
        protocol_name=protocol_name,
        defillama_slug=slug,
        defillama_id=str(defillama_id) if defillama_id else None,
        chain=chain,
        category=category,
        tvl=tvl,
        started_at=iso_start,
        completed_at=iso_end,
        duration_seconds=duration_total,
        overall_status=overall_status,
        stages={k: v.to_dict() for k, v in stage_outcomes.items()},
        repository_url=repo_url,
        repository_resolution_status=res_status.value if hasattr(res_status, "value") else str(res_status),
        repository_confidence_reason=res_reason,
        source_verification_status=verification_status_str,
        analyzed_commit=resolved_commit,
        static_analysis_status=static_status_str,
        slither_version=slither_ver,
        slither_findings_count=findings_count,
        slither_findings_summary=findings_summary,
        slither_diagnostics=slither_diag,
        slither_execution_backend=getattr(slither_result, "execution_backend", None) if slither_result else None,
        permission_scanner_status=perm_status.value if perm_status else None,
        permission_scanner_contract=perm_contract,
        permission_scanner_critical_count=perm_critical_count,
        permission_scanner_findings=perm_findings,
        permission_scanner_diagnostics=perm_diag,
        security_intelligence_status=sec_status_str,
        security_score=sec_score,
        security_score_grade=sec_grade,
        certik_project_id=certik_proj,
        audit_count=audit_cnt,
        errors=errors,
    )

    # -------------------------------------------------------------------------
    # Stage 7: Persistence
    # -------------------------------------------------------------------------
    if output_dir:
        try:
            save_protocol_report(report, output_dir=output_dir)
        except Exception as exc:
            report.errors.append(f"Report persistence error: {exc}")

    return report


def run_pipeline(
    limit: int = 10,
    chain: Optional[str] = None,
    category: Optional[str] = None,
    min_tvl: float = 1_000_000.0,
    target_dirs: Optional[Dict[str, str]] = None,
    output_dir: Optional[str] = None,
    live_provider: bool = False,
    client: Optional[DefiLlamaClient] = None,
    allowed_workspaces: Optional[List[str]] = None,
    slither_backend: Optional[str] = None,
    docker_image: Optional[str] = None,
) -> PipelineRunSummary:
    """
    Run pipeline on multiple protocols discovered via DefiLlama.
    Supports filtering by chain, category, and minimum TVL.
    """
    iso_start = datetime.now(timezone.utc).isoformat()
    defillama_client = client or DefiLlamaClient()

    # Discover protocols
    raw_protocols = defillama_client.get_protocols()
    filtered: List[Dict[str, Any]] = []

    for p in raw_protocols:
        if len(filtered) >= limit:
            break
        p_tvl = p.get("tvl") or 0
        if p_tvl < min_tvl:
            continue
        if chain and p.get("chain", "").lower() != chain.lower():
            continue
        if category and p.get("category", "").lower() != category.lower():
            continue
        filtered.append(p)

    reports: List[ProtocolPipelineReport] = []
    comp_count = 0
    part_count = 0
    fail_count = 0

    target_map = target_dirs or {}

    for proto in filtered:
        p_slug = proto.get("slug") or proto.get("name")
        t_dir = target_map.get(p_slug)
        rep = run_protocol_pipeline(
            protocol_input=proto,
            target_dir=t_dir,
            output_dir=output_dir,
            live_provider=live_provider,
            client=defillama_client,
            allowed_workspaces=allowed_workspaces,
            slither_backend=slither_backend,
            docker_image=docker_image,
        )
        reports.append(rep)
        if rep.overall_status == PipelineOverallStatus.COMPLETED:
            comp_count += 1
        elif rep.overall_status == PipelineOverallStatus.PARTIAL:
            part_count += 1
        else:
            fail_count += 1

    iso_end = datetime.now(timezone.utc).isoformat()
    summary = PipelineRunSummary(
        started_at=iso_start,
        completed_at=iso_end,
        total_protocols=len(reports),
        completed_count=comp_count,
        partial_count=part_count,
        failed_count=fail_count,
        reports=[r.to_dict() for r in reports],
    )

    if output_dir:
        out_path = Path(output_dir).resolve()
        out_path.mkdir(parents=True, exist_ok=True)
        timestamp = int(datetime.now(timezone.utc).timestamp())
        summary_file = out_path / f"pipeline_run_summary_{timestamp}.json"
        with open(summary_file, "w", encoding="utf-8") as f:
            f.write(summary.to_json(indent=2))

    return summary
