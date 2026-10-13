"""
Unit tests for slither_runner.py.
Validates all safety gates, subprocess sandboxing, JSON schema output, and error handling.
Runs 100% deterministically offline without requiring Slither or external network.
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

try:
    from defi_research.github_repository_resolver import (
        ResolutionStatus,
        SourceVerificationRecord,
        SourceVerificationStatus,
    )
    from defi_research.slither_runner import (
        AnalysisStatus,
        check_slither_availability,
        is_directory_in_workspace,
        parse_slither_json,
        run_slither_analysis,
    )
except ImportError:
    from github_repository_resolver import (
        ResolutionStatus,
        SourceVerificationRecord,
        SourceVerificationStatus,
    )
    from slither_runner import (
        AnalysisStatus,
        check_slither_availability,
        is_directory_in_workspace,
        parse_slither_json,
        run_slither_analysis,
    )


class TestSlitherRunner(unittest.TestCase):

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

    def tearDown(self):
        self.tmp_dir_obj.cleanup()

    # 1. Tool Availability Gate
    @patch("shutil.which", return_value=None)
    def test_tool_unavailable_when_slither_missing(self, mock_which):
        """When Slither is not installed, must return TOOL_UNAVAILABLE with diagnostic guidance."""
        is_avail, ver, err = check_slither_availability()
        self.assertFalse(is_avail)
        self.assertIsNone(ver)
        self.assertIn("not found in PATH", err)

        res = run_slither_analysis(
            target_dir=self.repo_dir,
            protocol_record=self.valid_protocol,
            allowed_workspaces=[self.workspace_dir],
            commit_hash="a1b2c3d4",
        )
        self.assertEqual(res.analysis_status, AnalysisStatus.TOOL_UNAVAILABLE)
        self.assertIn("pip install slither-analyzer", res.reason)

    # 2. Input Directory Gate
    def test_invalid_input_directory(self):
        """Non-existent directory must be rejected with PREREQUISITE_MISSING."""
        res = run_slither_analysis(
            target_dir=os.path.join(self.workspace_dir, "non_existent_folder"),
            protocol_record=self.valid_protocol,
            allowed_workspaces=[self.workspace_dir],
            commit_hash="a1b2c3d4",
        )
        self.assertEqual(res.analysis_status, AnalysisStatus.PREREQUISITE_MISSING)
        self.assertIn("does not exist", res.reason)

    # 3. Workspace Boundary Gate
    def test_directory_outside_allowed_workspace(self):
        """Target directories outside configured workspace must be rejected with NOT_ELIGIBLE."""
        with tempfile.TemporaryDirectory() as outside_dir:
            self.assertFalse(is_directory_in_workspace(outside_dir, allowed_workspaces=[self.workspace_dir]))

            res = run_slither_analysis(
                target_dir=outside_dir,
                protocol_record=self.valid_protocol,
                allowed_workspaces=[self.workspace_dir],
                commit_hash="a1b2c3d4",
            )
            self.assertEqual(res.analysis_status, AnalysisStatus.NOT_ELIGIBLE)
            self.assertIn("outside the configured allowed workspace", res.reason)

    # 4. Repository Resolution Gate (Stage A)
    def test_ineligible_scanner_handoff_rejected(self):
        """Protocols with non-confident resolution statuses must be rejected."""
        unresolved_proto = dict(self.valid_protocol)
        unresolved_proto["github_resolution_status"] = ResolutionStatus.UNRESOLVED.value

        res_unres = run_slither_analysis(
            target_dir=self.repo_dir,
            protocol_record=unresolved_proto,
            allowed_workspaces=[self.workspace_dir],
            commit_hash="a1b2c3d4",
        )
        self.assertEqual(res_unres.analysis_status, AnalysisStatus.NOT_ELIGIBLE)
        self.assertIn("UNRESOLVED", res_unres.reason)

        low_proto = dict(self.valid_protocol)
        low_proto["github_resolution_status"] = ResolutionStatus.RESOLVED_LOW_CONFIDENCE.value

        res_low = run_slither_analysis(
            target_dir=self.repo_dir,
            protocol_record=low_proto,
            allowed_workspaces=[self.workspace_dir],
            commit_hash="a1b2c3d4",
        )
        self.assertEqual(res_low.analysis_status, AnalysisStatus.NOT_ELIGIBLE)
        self.assertIn("RESOLVED_LOW_CONFIDENCE", res_low.reason)

    # 5. Source Verification Gate (Stage B)
    def test_failed_or_incomplete_source_verification_rejected(self):
        """When source verification fails (no config or no .sol files), Slither must not run."""
        fail_record = SourceVerificationRecord(
            status=SourceVerificationStatus.BUILD_CONFIGURATION_NOT_FOUND,
            solidity_source_paths=["src/Vault.sol"],
            reason="Missing foundry.toml or hardhat config",
        )
        res = run_slither_analysis(
            target_dir=self.repo_dir,
            protocol_record=self.valid_protocol,
            allowed_workspaces=[self.workspace_dir],
            verification_record=fail_record,
            commit_hash="a1b2c3d4",
        )
        self.assertEqual(res.analysis_status, AnalysisStatus.PREREQUISITE_MISSING)
        self.assertIn("Source verification not satisfied", res.reason)

    # 6. Commit Hash Requirement Gate
    def test_missing_commit_hash_rejected(self):
        """Missing deterministic commit hash must refuse execution."""
        proto_no_commit = dict(self.valid_protocol)
        proto_no_commit["commit_hash"] = None

        res = run_slither_analysis(
            target_dir=self.repo_dir,
            protocol_record=proto_no_commit,
            allowed_workspaces=[self.workspace_dir],
            commit_hash=None,
        )
        self.assertEqual(res.analysis_status, AnalysisStatus.PREREQUISITE_MISSING)
        self.assertIn("Git commit hash could not be identified", res.reason)

    # 7. Subprocess Timeout Handling
    @patch("shutil.which", return_value="slither")
    @patch("subprocess.run")
    def test_subprocess_timeout_handling(self, mock_run, mock_which):
        """When Slither times out, analysis must return TIMED_OUT cleanly."""
        # 1st call for check_slither_availability (--version)
        mock_ver = MagicMock()
        mock_ver.returncode = 0
        mock_ver.stdout = "0.10.0"

        # 2nd call raises TimeoutExpired
        mock_run.side_effect = [
            mock_ver,
            subprocess.TimeoutExpired(cmd=["slither", "."], timeout=10),
        ]

        res = run_slither_analysis(
            target_dir=self.repo_dir,
            protocol_record=self.valid_protocol,
            allowed_workspaces=[self.workspace_dir],
            timeout_seconds=10,
            commit_hash="a1b2c3d4",
        )
        self.assertEqual(res.analysis_status, AnalysisStatus.TIMED_OUT)
        self.assertIn("timed out after 10 seconds", res.reason)

    # 8. Non-Zero Exit Code with Valid Findings
    @patch("shutil.which", return_value="slither")
    @patch("subprocess.run")
    def test_nonzero_exit_code_with_valid_findings(self, mock_run, mock_which):
        """Slither exits with non-zero when vulnerabilities are found; must not treat as failure."""
        slither_json_payload = {
            "success": True,
            "results": {
                "detectors": [
                    {
                        "check": "reentrancy-eth",
                        "impact": "High",
                        "confidence": "Medium",
                        "description": "Reentrancy vulnerability in Vault.withdraw()",
                        "elements": [
                            {
                                "type": "function",
                                "name": "withdraw",
                                "source_mapping": {
                                    "filename_relative": "src/Vault.sol",
                                    "lines": [10, 11, 12],
                                    "start": 100,
                                    "length": 50,
                                },
                                "type_specific_fields": {
                                    "parent": {
                                        "type": "contract",
                                        "name": "Vault",
                                    }
                                },
                            }
                        ],
                    }
                ]
            },
        }

        mock_ver = MagicMock(returncode=0, stdout="0.10.0")
        mock_exec = MagicMock(returncode=1, stdout="", stderr="WARNING: Slither detected vulnerabilities")

        def side_effect(cmd, **kwargs):
            if "--version" in cmd:
                return mock_ver
            # Simulate Slither writing to output JSON file
            out_file = cmd[3]
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(slither_json_payload, f)
            return mock_exec

        mock_run.side_effect = side_effect

        res = run_slither_analysis(
            target_dir=self.repo_dir,
            protocol_record=self.valid_protocol,
            allowed_workspaces=[self.workspace_dir],
            commit_hash="a1b2c3d4",
        )
        self.assertEqual(res.analysis_status, AnalysisStatus.COMPLETED)
        self.assertEqual(res.exit_code, 1)
        self.assertEqual(len(res.findings), 1)
        self.assertEqual(res.findings[0]["detector_id"], "reentrancy-eth")
        self.assertEqual(res.findings[0]["severity"], "High")
        self.assertEqual(res.findings[0]["affected_contract"], "Vault")
        self.assertEqual(res.findings_summary["High"], 1)

    # 9. Successful Execution with Zero Findings
    @patch("shutil.which", return_value="slither")
    @patch("subprocess.run")
    def test_successful_execution_zero_findings(self, mock_run, mock_which):
        """Clean run with 0 findings must report COMPLETED with empty findings array."""
        slither_json_payload = {
            "success": True,
            "results": {
                "detectors": []
            },
        }

        mock_ver = MagicMock(returncode=0, stdout="0.10.0")
        mock_exec = MagicMock(returncode=0, stdout="", stderr="")

        def side_effect(cmd, **kwargs):
            if "--version" in cmd:
                return mock_ver
            out_file = cmd[3]
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(slither_json_payload, f)
            return mock_exec

        mock_run.side_effect = side_effect

        res = run_slither_analysis(
            target_dir=self.repo_dir,
            protocol_record=self.valid_protocol,
            allowed_workspaces=[self.workspace_dir],
            commit_hash="a1b2c3d4",
        )
        self.assertEqual(res.analysis_status, AnalysisStatus.COMPLETED)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(len(res.findings), 0)
        self.assertEqual(res.findings_summary["total"], 0)

    # 10. Compilation Failure Reports PREREQUISITE_MISSING
    @patch("shutil.which", return_value="slither")
    @patch("subprocess.run")
    def test_compilation_failure_reports_prerequisite_missing(self, mock_run, mock_which):
        """Compilation failure due to missing dependencies must report PREREQUISITE_MISSING without running scripts."""
        mock_ver = MagicMock(returncode=0, stdout="0.10.0")
        mock_exec = MagicMock(
            returncode=255,
            stdout="",
            stderr="Error: Source file requires different compiler version (current compiler is 0.8.20)\nImport not found: @openzeppelin/contracts/token/ERC20/IERC20.sol",
        )

        mock_run.side_effect = [mock_ver, mock_exec]

        res = run_slither_analysis(
            target_dir=self.repo_dir,
            protocol_record=self.valid_protocol,
            allowed_workspaces=[self.workspace_dir],
            commit_hash="a1b2c3d4",
        )
        self.assertEqual(res.analysis_status, AnalysisStatus.PREREQUISITE_MISSING)
        self.assertIn("Slither compilation failed", res.reason)
        self.assertIn("Import not found", res.stderr_summary)

    # 11. Malformed or Incomplete Output Handling
    @patch("shutil.which", return_value="slither")
    @patch("subprocess.run")
    def test_malformed_json_output_handling(self, mock_run, mock_which):
        """Malformed JSON or internal crash must produce ANALYSIS_FAILED without crashing."""
        mock_ver = MagicMock(returncode=0, stdout="0.10.0")
        mock_exec = MagicMock(returncode=2, stdout="", stderr="Internal Slither traceback error")

        def side_effect(cmd, **kwargs):
            if "--version" in cmd:
                return mock_ver
            out_file = cmd[3]
            with open(out_file, "w", encoding="utf-8") as f:
                f.write("{corrupted json...")
            return mock_exec

        mock_run.side_effect = side_effect

        res = run_slither_analysis(
            target_dir=self.repo_dir,
            protocol_record=self.valid_protocol,
            allowed_workspaces=[self.workspace_dir],
            commit_hash="a1b2c3d4",
        )
        self.assertEqual(res.analysis_status, AnalysisStatus.ANALYSIS_FAILED)

    # 12. Artifact Serialization and JSON Schema Validation
    @patch("shutil.which", return_value="slither")
    @patch("subprocess.run")
    def test_json_result_schema_and_serialization(self, mock_run, mock_which):
        """Validates all schema fields and JSON artifact generation."""
        mock_ver = MagicMock(returncode=0, stdout="0.10.0")
        mock_exec = MagicMock(returncode=0, stdout="", stderr="")

        slither_json_payload = {
            "success": True,
            "results": {
                "detectors": [
                    {
                        "check": "uninitialized-state",
                        "impact": "Medium",
                        "confidence": "High",
                        "description": "Vault.owner is never initialized",
                        "elements": [],
                    }
                ]
            },
        }

        def side_effect(cmd, **kwargs):
            if "--version" in cmd:
                return mock_ver
            out_file = cmd[3]
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(slither_json_payload, f)
            return mock_exec

        mock_run.side_effect = side_effect

        artifacts_dir = os.path.join(self.workspace_dir, "artifacts")
        res = run_slither_analysis(
            target_dir=self.repo_dir,
            protocol_record=self.valid_protocol,
            allowed_workspaces=[self.workspace_dir],
            output_dir=artifacts_dir,
            commit_hash="a1b2c3d4e5f6",
        )

        self.assertEqual(res.analysis_status, AnalysisStatus.COMPLETED)
        self.assertIsNotNone(res.raw_artifact_path)
        self.assertTrue(os.path.exists(res.raw_artifact_path))

        # Check serialization
        d = res.to_dict()
        self.assertIn("protocol_name", d)
        self.assertIn("defillama_slug", d)
        self.assertIn("repository_url", d)
        self.assertIn("commit_hash", d)
        self.assertIn("source_verification_status", d)
        self.assertIn("slither_version", d)
        self.assertIn("analysis_status", d)
        self.assertIn("started_at", d)
        self.assertIn("duration_seconds", d)
        self.assertIn("exit_code", d)
        self.assertIn("findings", d)
        self.assertIn("limitations", d)
        self.assertGreaterEqual(len(d["limitations"]), 3)

        # Ensure valid JSON string serialization
        json_str = res.to_json()
        parsed = json.loads(json_str)
        self.assertEqual(parsed["analysis_status"], "COMPLETED")
        self.assertEqual(parsed["protocol_name"], "Test Protocol")

    # 13. CLI Integration Tests
    @patch("cli.resolve_github_repo_from_slug")
    def test_cli_slither_rejects_unresolved_protocol(self, mock_resolve):
        """CLI slither command must refuse execution if protocol resolution is not confident."""
        from cli import cmd_slither
        import argparse

        mock_resolve.return_value = {
            "name": "Lido",
            "slug": "lido",
            "github_resolution_status": "ORGANIZATION_ONLY",
            "resolution_evidence": {"reason": "Organization only"},
        }

        args = argparse.Namespace(
            target_dir=self.repo_dir,
            slug="lido",
            commit=None,
            timeout=120,
            output_dir=None,
        )

        mock_logger = MagicMock()
        # Should not crash and should refuse execution early
        with patch("slither_runner.run_slither_analysis") as mock_runner:
            cmd_slither(args, mock_logger)
            mock_runner.assert_not_called()

    @patch("cli.resolve_github_repo_from_slug")
    @patch("cli.run_slither_analysis")
    def test_cli_slither_executes_when_confident(self, mock_runner, mock_resolve):
        """CLI slither command proceeds to runner when protocol resolution is confident."""
        from cli import cmd_slither
        import argparse
        from slither_runner import SlitherAnalysisResult

        mock_resolve.return_value = {
            "name": "Aave V3",
            "slug": "aave-v3",
            "github_resolution_status": "RESOLVED_CONFIDENT",
            "commit_hash": "abcdef123456",
            "selected_github_url": "https://github.com/aave/aave-v3-core",
        }

        dummy_result = SlitherAnalysisResult(
            protocol_name="Aave V3",
            defillama_slug="aave-v3",
            repository_url="https://github.com/aave/aave-v3-core",
            commit_hash="abcdef123456",
            source_verification_status="SOURCE_VERIFIED",
            slither_version="0.10.0",
            analysis_status=AnalysisStatus.COMPLETED,
            started_at=12345,
            duration_seconds=1.2,
            exit_code=0,
            findings=[],
            findings_summary={"total": 0},
            reason="Completed",
        )
        mock_runner.return_value = dummy_result

        args = argparse.Namespace(
            target_dir=self.repo_dir,
            slug="aave-v3",
            commit="abcdef123456",
            timeout=120,
            output_dir="./reports",
        )

        mock_logger = MagicMock()
        cmd_slither(args, mock_logger)
        mock_runner.assert_called_once()


if __name__ == "__main__":
    unittest.main()
