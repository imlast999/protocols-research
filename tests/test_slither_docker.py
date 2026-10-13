"""
Deterministic offline unit tests for Docker-backed Slither static analysis execution (Phase 6).

Covers all 15 required test scenarios:
1. Docker CLI unavailable.
2. Docker daemon unavailable.
3. Image missing.
4. Correct and safe Docker argument construction.
5. Read-only target mount.
6. No network access and no privileged mode.
7. Invalid or out-of-workspace target rejection.
8. Timeout handling.
9. Container execution failure.
10. Malformed or missing Slither output.
11. Correct parsing of valid findings.
12. Unsupported compiler requirements.
13. Secret redaction in diagnostic output.
14. Preservation of the existing native runner behavior.
15. Correct propagation of Docker analysis results into pipeline report.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_src_dir = str(Path(__file__).resolve().parent.parent / "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from defi_research.github_repository_resolver import (
    ResolutionStatus,
    SourceVerificationRecord,
    SourceVerificationStatus,
)
from defi_research.pipeline import (
    PipelineOverallStatus,
    ProtocolPipelineReport,
    StageStatus,
    run_protocol_pipeline,
)
from defi_research.slither_runner import (
    AnalysisStatus,
    SlitherAnalysisResult,
    build_docker_run_args,
    check_docker_availability,
    check_docker_image_availability,
    check_slither_availability,
    run_slither_analysis,
    sanitize_output_text,
)


class TestSlitherDockerBackend(unittest.TestCase):
    """Deterministic offline unit test suite for Docker-backed Slither execution."""

    def setUp(self):
        self.tmp_dir_obj = tempfile.TemporaryDirectory()
        self.workspace_dir = os.path.realpath(self.tmp_dir_obj.name)

        # Setup valid dummy repository inside workspace
        self.repo_dir = os.path.join(self.workspace_dir, "test_repo")
        os.makedirs(os.path.join(self.repo_dir, "src"), exist_ok=True)
        with open(os.path.join(self.repo_dir, "foundry.toml"), "w", encoding="utf-8") as f:
            f.write("[profile.default]\n")
        with open(os.path.join(self.repo_dir, "src", "Vault.sol"), "w", encoding="utf-8") as f:
            f.write("// SPDX-License-Identifier: MIT\npragma solidity ^0.8.20;\ncontract Vault {}\n")

        self.valid_protocol = {
            "name": "Test Protocol",
            "slug": "test-protocol",
            "protocol_name": "Test Protocol",
            "defillama_slug": "test-protocol",
            "selected_github_url": "https://github.com/test-org/test-repo",
            "github_resolution_status": ResolutionStatus.RESOLVED_CONFIDENT.value,
            "commit_hash": "a1b2c3d4e5f678901234567890abcdef12345678",
        }

        self.valid_ver = SourceVerificationRecord(
            status=SourceVerificationStatus.SOURCE_VERIFIED,
            repo_path=self.repo_dir,
            is_git_repo=True,
            commit_hash="a1b2c3d4e5f678901234567890abcdef12345678",
            build_framework="foundry",
            solidity_source_paths=[os.path.join(self.repo_dir, "src", "Vault.sol")],
            reason="Verified successfully",
        )

    def tearDown(self):
        self.tmp_dir_obj.cleanup()

    # 1. Docker CLI unavailable
    @patch("shutil.which", return_value=None)
    def test_01_docker_cli_unavailable(self, mock_which):
        """When Docker CLI is missing, check_docker_availability and runner return TOOL_UNAVAILABLE."""
        cli_ok, daemon_ok, err = check_docker_availability()
        self.assertFalse(cli_ok)
        self.assertFalse(daemon_ok)
        self.assertIn("not found in PATH", err)

        res = run_slither_analysis(
            target_dir=self.repo_dir,
            protocol_record=self.valid_protocol,
            verification_record=self.valid_ver,
            allowed_workspaces=[self.workspace_dir],
            commit_hash="a1b2c3d4",
            backend="docker",
        )
        self.assertEqual(res.analysis_status, AnalysisStatus.TOOL_UNAVAILABLE)
        self.assertEqual(res.execution_backend, "docker")
        self.assertIn("Docker CLI is unavailable", res.reason)

    # 2. Docker daemon unavailable
    @patch("shutil.which", return_value="/usr/bin/docker")
    @patch("subprocess.run")
    def test_02_docker_daemon_unavailable(self, mock_run, mock_which):
        """When Docker CLI exists but daemon is stopped/offline, returns TOOL_UNAVAILABLE."""
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?"
        mock_run.return_value = mock_proc

        cli_ok, daemon_ok, err = check_docker_availability()
        self.assertTrue(cli_ok)
        self.assertFalse(daemon_ok)
        self.assertIn("Docker daemon is offline or inaccessible", err)

        res = run_slither_analysis(
            target_dir=self.repo_dir,
            protocol_record=self.valid_protocol,
            verification_record=self.valid_ver,
            allowed_workspaces=[self.workspace_dir],
            commit_hash="a1b2c3d4",
            backend="docker",
        )
        self.assertEqual(res.analysis_status, AnalysisStatus.TOOL_UNAVAILABLE)
        self.assertIn("Docker daemon is offline", res.reason)

    # 3. Image missing
    @patch("shutil.which", return_value="/usr/bin/docker")
    @patch("subprocess.run")
    def test_03_docker_image_missing(self, mock_run, mock_which):
        """When Docker CLI and daemon are available but image is missing, returns TOOL_UNAVAILABLE."""
        # Check inspect alone first
        inspect_proc = MagicMock(returncode=1, stderr="Error: No such image", stdout="")
        mock_run.return_value = inspect_proc

        img_ok, err = check_docker_image_availability("defi-slither:missing")
        self.assertFalse(img_ok)
        self.assertIn("not found locally", err)

        # 1st call for docker info (daemon ok), 2nd call for docker image inspect (image missing)
        info_proc = MagicMock(returncode=0, stderr="", stdout="")
        mock_run.side_effect = [info_proc, inspect_proc]
        res = run_slither_analysis(
            target_dir=self.repo_dir,
            protocol_record=self.valid_protocol,
            verification_record=self.valid_ver,
            allowed_workspaces=[self.workspace_dir],
            commit_hash="a1b2c3d4",
            backend="docker",
            docker_image="defi-slither:missing",
        )
        self.assertEqual(res.analysis_status, AnalysisStatus.TOOL_UNAVAILABLE)
        self.assertIn("Docker image is unavailable", res.reason)

    # 4. Correct and safe Docker argument construction
    def test_04_docker_argument_construction(self):
        """Verify build_docker_run_args produces safe, non-shell arguments."""
        args = build_docker_run_args(
            target_dir=self.repo_dir,
            host_output_dir=self.workspace_dir,
            image_name="defi-slither:0.10.4",
        )
        self.assertEqual(args[0], "docker")
        self.assertEqual(args[1], "run")
        self.assertIn("--rm", args)
        self.assertIn("-w", args)
        self.assertEqual(args[args.index("-w") + 1], "/repo")
        self.assertIn("defi-slither:0.10.4", args)
        self.assertIn("slither", args)
        self.assertIn("--json", args)

        # Test default fallback when image_name is omitted
        args_default = build_docker_run_args(
            target_dir=self.repo_dir,
            host_output_dir=self.workspace_dir,
        )
        self.assertIn("defi-slither:0.10.4", args_default)

    # 5. Read-only target mount
    def test_05_read_only_target_mount(self):
        """Target repository must strictly be mounted as read-only (:ro)."""
        args = build_docker_run_args(
            target_dir=self.repo_dir,
            host_output_dir=self.workspace_dir,
            image_name="defi-slither:0.10.4",
        )
        real_target = os.path.realpath(os.path.abspath(self.repo_dir))
        expected_ro_mount = f"{real_target}:/repo:ro"
        self.assertIn(expected_ro_mount, args)

        # Output mount must be read-write (:rw)
        real_out = os.path.realpath(os.path.abspath(self.workspace_dir))
        expected_rw_mount = f"{real_out}:/output:rw"
        self.assertIn(expected_rw_mount, args)

    # 6. No network access and no privileged mode
    def test_06_security_flags_enforced(self):
        """Enforces --network none, drop capabilities, no new privileges, and unprivileged user."""
        args = build_docker_run_args(
            target_dir=self.repo_dir,
            host_output_dir=self.workspace_dir,
            image_name="defi-slither:0.10.4",
        )
        self.assertIn("--network", args)
        self.assertEqual(args[args.index("--network") + 1], "none")
        self.assertIn("--security-opt", args)
        self.assertEqual(args[args.index("--security-opt") + 1], "no-new-privileges")
        self.assertIn("--cap-drop", args)
        self.assertEqual(args[args.index("--cap-drop") + 1], "ALL")
        self.assertIn("--user", args)
        self.assertEqual(args[args.index("--user") + 1], "1000:1000")
        self.assertIn("--memory", args)
        self.assertEqual(args[args.index("--memory") + 1], "2g")
        self.assertNotIn("--privileged", args)

    # 7. Invalid or out-of-workspace target rejection
    def test_07_invalid_or_out_of_workspace_target(self):
        """Gate 1 & Gate 2 must reject invalid or out-of-workspace directories even when Docker is active."""
        # Non-existent directory
        res = run_slither_analysis(
            target_dir=os.path.join(self.workspace_dir, "non_existent"),
            protocol_record=self.valid_protocol,
            allowed_workspaces=[self.workspace_dir],
            backend="docker",
        )
        self.assertEqual(res.analysis_status, AnalysisStatus.PREREQUISITE_MISSING)

        # Out of workspace directory
        outside_dir = tempfile.mkdtemp()
        try:
            res_outside = run_slither_analysis(
                target_dir=outside_dir,
                protocol_record=self.valid_protocol,
                allowed_workspaces=[self.workspace_dir],
                backend="docker",
            )
            self.assertEqual(res_outside.analysis_status, AnalysisStatus.NOT_ELIGIBLE)
            self.assertIn("Security Gate Rejected", res_outside.reason)
        finally:
            os.rmdir(outside_dir)

    # 8. Timeout handling
    @patch("shutil.which", return_value="/usr/bin/docker")
    @patch("defi_research.slither_runner.check_docker_availability", return_value=(True, True, None))
    @patch("defi_research.slither_runner.check_docker_image_availability", return_value=(True, None))
    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["docker", "run"], timeout=5))
    def test_08_timeout_handling(self, mock_run, mock_img, mock_avail, mock_which):
        """Subprocess.TimeoutExpired must return TIMED_OUT status cleanly."""
        res = run_slither_analysis(
            target_dir=self.repo_dir,
            protocol_record=self.valid_protocol,
            verification_record=self.valid_ver,
            allowed_workspaces=[self.workspace_dir],
            commit_hash="a1b2c3d4",
            timeout_seconds=5,
            backend="docker",
        )
        self.assertEqual(res.analysis_status, AnalysisStatus.TIMED_OUT)
        self.assertEqual(res.execution_backend, "docker")
        self.assertIn("timed out after 5 seconds", res.reason)

    # 9. Container execution failure
    @patch("shutil.which", return_value="/usr/bin/docker")
    @patch("defi_research.slither_runner.check_docker_availability", return_value=(True, True, None))
    @patch("defi_research.slither_runner.check_docker_image_availability", return_value=(True, None))
    @patch("subprocess.run")
    def test_09_container_execution_failure(self, mock_run, mock_img, mock_avail, mock_which):
        """Non-zero exit code with no output must return ANALYSIS_FAILED."""
        fail_proc = MagicMock(returncode=125, stdout="", stderr="docker: Error response from daemon: OOMKilled")
        mock_run.return_value = fail_proc

        res = run_slither_analysis(
            target_dir=self.repo_dir,
            protocol_record=self.valid_protocol,
            verification_record=self.valid_ver,
            allowed_workspaces=[self.workspace_dir],
            commit_hash="a1b2c3d4",
            backend="docker",
        )
        self.assertEqual(res.analysis_status, AnalysisStatus.ANALYSIS_FAILED)
        self.assertEqual(res.execution_backend, "docker")
        self.assertEqual(res.exit_code, 125)

    # 10. Malformed or missing Slither output
    @patch("shutil.which", return_value="/usr/bin/docker")
    @patch("defi_research.slither_runner.check_docker_availability", return_value=(True, True, None))
    @patch("defi_research.slither_runner.check_docker_image_availability", return_value=(True, None))
    @patch("subprocess.run")
    def test_10_malformed_slither_output(self, mock_run, mock_img, mock_avail, mock_which):
        """Malformed JSON from container must not report COMPLETED; must report ANALYSIS_FAILED."""
        bad_proc = MagicMock(returncode=1, stdout="Not a json document", stderr="Corrupted output")
        mock_run.return_value = bad_proc

        res = run_slither_analysis(
            target_dir=self.repo_dir,
            protocol_record=self.valid_protocol,
            verification_record=self.valid_ver,
            allowed_workspaces=[self.workspace_dir],
            commit_hash="a1b2c3d4",
            backend="docker",
        )
        self.assertEqual(res.analysis_status, AnalysisStatus.ANALYSIS_FAILED)
        self.assertNotEqual(res.analysis_status, AnalysisStatus.COMPLETED)

    # 11. Correct parsing of valid findings
    @patch("shutil.which", return_value="/usr/bin/docker")
    @patch("defi_research.slither_runner.check_docker_availability", return_value=(True, True, None))
    @patch("defi_research.slither_runner.check_docker_image_availability", return_value=(True, None))
    @patch("subprocess.run")
    def test_11_valid_findings_parsed_successfully(self, mock_run, mock_img, mock_avail, mock_which):
        """Valid Slither JSON output must be parsed and return COMPLETED with structured findings."""
        sample_json = {
            "success": True,
            "error": None,
            "results": {
                "detectors": [
                    {
                        "check": "reentrancy-eth",
                        "impact": "High",
                        "confidence": "Medium",
                        "description": "Reentrancy in Vault.withdraw(uint256)",
                        "elements": [
                            {
                                "type": "function",
                                "name": "withdraw",
                                "type_specific_fields": {
                                    "parent": {"type": "contract", "name": "Vault"}
                                },
                                "source_mapping": {
                                    "filename_relative": "src/Vault.sol",
                                    "lines": [25, 26, 27],
                                    "start": 100,
                                    "length": 50,
                                },
                            }
                        ],
                    }
                ]
            },
        }

        def mock_docker_exec(cmd, **kwargs):
            # If writing to /output/slither_out.json via volume mount:
            out_vol = [arg for arg in cmd if ":/output:rw" in arg]
            if out_vol:
                host_out_dir = out_vol[0].split(":/output:rw")[0]
                with open(os.path.join(host_out_dir, "slither_out.json"), "w", encoding="utf-8") as f:
                    json.dump(sample_json, f)
            return MagicMock(returncode=1, stdout="", stderr="")

        mock_run.side_effect = mock_docker_exec

        res = run_slither_analysis(
            target_dir=self.repo_dir,
            protocol_record=self.valid_protocol,
            verification_record=self.valid_ver,
            allowed_workspaces=[self.workspace_dir],
            commit_hash="a1b2c3d4",
            backend="docker",
        )
        self.assertEqual(res.analysis_status, AnalysisStatus.COMPLETED)
        self.assertEqual(res.execution_backend, "docker")
        self.assertEqual(len(res.findings), 1)
        finding = res.findings[0]
        self.assertEqual(finding["detector_id"], "reentrancy-eth")
        self.assertEqual(finding["severity"], "High")
        self.assertEqual(finding["affected_contract"], "Vault")
        self.assertEqual(res.findings_summary["High"], 1)

    # 12. Unsupported compiler requirements
    @patch("shutil.which", return_value="/usr/bin/docker")
    @patch("defi_research.slither_runner.check_docker_availability", return_value=(True, True, None))
    @patch("defi_research.slither_runner.check_docker_image_availability", return_value=(True, None))
    @patch("subprocess.run")
    def test_12_unsupported_compiler_requirements(self, mock_run, mock_img, mock_avail, mock_which):
        """Missing or incompatible compiler version must return PREREQUISITE_MISSING."""
        mock_proc = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: Source file requires different compiler version (found 0.8.20, need ^0.8.28). solc was not able to compile.",
        )
        mock_run.return_value = mock_proc

        res = run_slither_analysis(
            target_dir=self.repo_dir,
            protocol_record=self.valid_protocol,
            verification_record=self.valid_ver,
            allowed_workspaces=[self.workspace_dir],
            commit_hash="a1b2c3d4",
            backend="docker",
        )
        self.assertEqual(res.analysis_status, AnalysisStatus.PREREQUISITE_MISSING)
        self.assertIn("Slither compilation failed", res.reason)
        self.assertIn("Arbitrary build scripts were not executed", res.reason)

    # 13. Secret redaction in diagnostic output
    def test_13_secret_redaction(self):
        """Ensures EVM private keys and API tokens in diagnostic text are scrubbed."""
        raw_stderr = "Fatal error with private key 0x4f3edf983ac636a65a842ce7c78d9aa706d3b113bce9c46f30d7d21715b23b1d and api_key=ab12cd34ef56gh78ij90"
        sanitized = sanitize_output_text(raw_stderr)
        self.assertNotIn("0x4f3edf983ac636a65a842ce7c78d9aa706d3b113bce9c46f30d7d21715b23b1d", sanitized)
        self.assertIn("[REDACTED_HEX_KEY]", sanitized)
        self.assertIn("api_key=[REDACTED_SECRET]", sanitized)

    # 14. Preservation of the existing native runner behavior
    @patch("shutil.which")
    @patch("subprocess.run")
    def test_14_preservation_of_native_runner(self, mock_run, mock_which):
        """When backend='native' is requested, executes native slither binary directly."""
        mock_which.return_value = "/usr/local/bin/slither"
        # 1st call: slither --version
        ver_proc = MagicMock(returncode=0, stdout="0.10.4", stderr="")
        # 2nd call: slither analysis
        run_proc = MagicMock(returncode=0, stdout='{"success": true, "results": {"detectors": []}}', stderr="")
        mock_run.side_effect = [ver_proc, run_proc]

        res = run_slither_analysis(
            target_dir=self.repo_dir,
            protocol_record=self.valid_protocol,
            verification_record=self.valid_ver,
            allowed_workspaces=[self.workspace_dir],
            commit_hash="a1b2c3d4",
            backend="native",
        )
        self.assertEqual(res.analysis_status, AnalysisStatus.COMPLETED)
        self.assertEqual(res.execution_backend, "native")
        self.assertEqual(res.slither_version, "0.10.4")

    # 15. Correct propagation of Docker analysis results into pipeline report
    @patch("defi_research.pipeline.query_protocol_security_intelligence")
    @patch("defi_research.pipeline.verify_repository_source")
    @patch("defi_research.pipeline.check_slither_availability", return_value=(True, "docker:0.10.4", None))
    @patch("defi_research.pipeline.run_slither_analysis")
    def test_15_pipeline_propagation_of_docker_results(self, mock_slither, mock_avail, mock_verify, mock_intel):
        """Pipeline Stage 4 correctly invokes Docker backend and records backend details in report."""
        mock_verify.return_value = self.valid_ver
        mock_intel.return_value = MagicMock(
            retrieval_status=MagicMock(value="NOT_CONFIGURED"),
            security_score=None,
            audit_records=[],
            provider_project_id=None,
        )

        mock_result = SlitherAnalysisResult(
            protocol_name="Test Protocol",
            defillama_slug="test-protocol",
            repository_url="https://github.com/test-org/test-repo",
            commit_hash="a1b2c3d4e5f678901234567890abcdef12345678",
            source_verification_status="SOURCE_VERIFIED",
            slither_version="docker:0.10.4",
            analysis_status=AnalysisStatus.COMPLETED,
            started_at=1700000000,
            duration_seconds=2.5,
            exit_code=0,
            execution_backend="docker",
            workflow_type="REPOSITORY_CHECKOUT",
            findings=[],
            findings_summary={"High": 0, "Medium": 0, "total": 0},
            reason="Completed via docker",
        )
        mock_slither.return_value = mock_result

        proto_input = dict(self.valid_protocol)
        proto_input["github"] = ["https://github.com/test-org/test-repo"]

        # Run pipeline with slither_backend="docker"
        report = run_protocol_pipeline(
            protocol_input=proto_input,
            target_dir=self.repo_dir,
            commit_hash="a1b2c3d4e5f678901234567890abcdef12345678",
            allowed_workspaces=[self.workspace_dir],
            slither_backend="docker",
        )

        stage_s4 = report.stages.get("static_analysis", {})
        self.assertEqual(stage_s4.get("status"), StageStatus.SUCCESS.value)
        self.assertEqual(stage_s4.get("details", {}).get("execution_backend"), "docker")
        self.assertEqual(report.slither_execution_backend, "docker")
        self.assertEqual(report.slither_version, "docker:0.10.4")


if __name__ == "__main__":
    unittest.main()
