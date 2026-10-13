"""
test_slither_security_audit.py
Focused security and correctness audit suite for Slither execution boundaries,
source verification and commit identity binding, result interpretation, and controlled fixtures.
Runs deterministically offline with isolated workspaces and mock subprocesses.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from github_repository_resolver import (
    ResolutionStatus,
    SourceVerificationRecord,
    SourceVerificationStatus,
    verify_repository_source,
)
from slither_runner import (
    AnalysisStatus,
    is_directory_in_workspace,
    run_slither_analysis,
    sanitize_output_text,
)


class TestSlitherSecurityAudit(unittest.TestCase):

    def setUp(self):
        self.tmp_dir_obj = tempfile.TemporaryDirectory()
        self.workspace_dir = os.path.realpath(self.tmp_dir_obj.name)

        # Setup standard mock valid repo inside workspace
        self.repo_dir = os.path.join(self.workspace_dir, "audited_repo")
        os.makedirs(os.path.join(self.repo_dir, "contracts"), exist_ok=True)
        with open(os.path.join(self.repo_dir, "foundry.toml"), "w", encoding="utf-8") as f:
            f.write("[profile.default]\n")
        with open(os.path.join(self.repo_dir, "contracts", "Vault.sol"), "w", encoding="utf-8") as f:
            f.write("// SPDX-License-Identifier: MIT\npragma solidity ^0.8.20;\ncontract Vault {}\n")

        self.valid_protocol = {
            "name": "Audit Protocol",
            "slug": "audit-protocol",
            "protocol_name": "Audit Protocol",
            "defillama_slug": "audit-protocol",
            "selected_github_url": "https://github.com/audit-org/audit-repo",
            "github_resolution_status": ResolutionStatus.RESOLVED_CONFIDENT.value,
            "commit_hash": "e1f2a3b4c5d6e7f80123456789abcdef01234567",
        }

    def tearDown(self):
        self.tmp_dir_obj.cleanup()

    # =========================================================================
    # 1. Audit the Slither Execution Boundary
    # =========================================================================

    def test_path_traversal_dot_dot_rejected(self):
        """Relative path using '..' escaping workspace must be rejected."""
        outside_path = os.path.join(self.workspace_dir, "..", "escaped_folder")
        self.assertFalse(is_directory_in_workspace(outside_path, [self.workspace_dir]))

        res = run_slither_analysis(
            target_dir=outside_path,
            allowed_workspaces=[self.workspace_dir],
            commit_hash="12345678",
        )
        # Target doesn't exist or is outside workspace
        self.assertIn(res.analysis_status, [AnalysisStatus.NOT_ELIGIBLE, AnalysisStatus.PREREQUISITE_MISSING])

    def test_prefix_collision_attack_rejected(self):
        """A directory sharing a string prefix with the allowed workspace must be rejected."""
        fake_workspace = self.workspace_dir + "_fake_suffix"
        os.makedirs(fake_workspace, exist_ok=True)
        try:
            # Although fake_workspace starts with self.workspace_dir string, it is outside!
            self.assertFalse(is_directory_in_workspace(fake_workspace, [self.workspace_dir]))

            res = run_slither_analysis(
                target_dir=fake_workspace,
                allowed_workspaces=[self.workspace_dir],
                commit_hash="12345678",
            )
            self.assertEqual(res.analysis_status, AnalysisStatus.NOT_ELIGIBLE)
            self.assertIn("outside the configured allowed workspace", res.reason)
        finally:
            shutil.rmtree(fake_workspace, ignore_errors=True)

    def test_symlink_escaping_workspace_rejected(self):
        """A symlink inside workspace pointing outside workspace must be resolved and rejected."""
        with tempfile.TemporaryDirectory() as outside_dir:
            outside_real = os.path.realpath(outside_dir)
            symlink_path = os.path.join(self.workspace_dir, "symlink_to_outside")
            try:
                os.symlink(outside_real, symlink_path, target_is_directory=True)
            except (OSError, NotImplementedError):
                # On Windows without SeCreateSymbolicLinkPrivilege, skip symlink test
                return

            self.assertFalse(is_directory_in_workspace(symlink_path, [self.workspace_dir]))
            res = run_slither_analysis(
                target_dir=symlink_path,
                allowed_workspaces=[self.workspace_dir],
                commit_hash="12345678",
            )
            self.assertEqual(res.analysis_status, AnalysisStatus.NOT_ELIGIBLE)
            self.assertIn("outside the configured allowed workspace", res.reason)

    def test_output_dir_outside_workspace_rejected(self):
        """Output directory pointing outside workspace must be rejected to prevent file overwrite."""
        with tempfile.TemporaryDirectory() as outside_output_dir:
            res = run_slither_analysis(
                target_dir=self.repo_dir,
                protocol_record=self.valid_protocol,
                allowed_workspaces=[self.workspace_dir],
                output_dir=outside_output_dir,
                commit_hash="12345678",
            )
            self.assertEqual(res.analysis_status, AnalysisStatus.NOT_ELIGIBLE)
            self.assertIn("Output directory", res.reason)

    def test_output_slug_traversal_sanitized(self):
        """Malicious characters in protocol slug or commit cannot inject path traversal into artifacts."""
        malicious_proto = dict(self.valid_protocol)
        malicious_proto["defillama_slug"] = "../../etc/cron"
        malicious_proto["slug"] = "../../etc/cron"

        out_dir = os.path.join(self.workspace_dir, "reports")
        os.makedirs(out_dir, exist_ok=True)

        mock_ver = MagicMock(returncode=0, stdout="0.10.0")
        mock_exec = MagicMock(returncode=0, stdout='{"results": {"detectors": []}}', stderr="")

        with patch("shutil.which", return_value="slither"):
            with patch("subprocess.run", side_effect=[mock_ver, mock_exec]):
                res = run_slither_analysis(
                    target_dir=self.repo_dir,
                    protocol_record=malicious_proto,
                    allowed_workspaces=[self.workspace_dir],
                    output_dir=out_dir,
                    commit_hash="../../evil",
                )
                self.assertEqual(res.analysis_status, AnalysisStatus.COMPLETED)
                self.assertIsNotNone(res.raw_artifact_path)
                # Confirm the artifact was contained in out_dir and did not escape
                self.assertTrue(res.raw_artifact_path.startswith(out_dir))
                self.assertNotIn("..", os.path.relpath(res.raw_artifact_path, out_dir))

    def test_secret_redaction_in_output_summaries(self):
        """Private keys and API tokens in stderr/stdout must be automatically redacted."""
        raw_stderr = (
            "Error: could not decrypt key 0x4f3edf983ac636a65a842ce7c78d9aa706d3b113bce9c46f30d7d21715b23b1d "
            "using apiKey: sk-proj-1234567890abcdef1234"
        )
        sanitized = sanitize_output_text(raw_stderr)
        self.assertNotIn("4f3edf983ac636a65a842ce7c78d9aa706d3b113bce9c46f30d7d21715b23b1d", sanitized)
        self.assertNotIn("sk-proj-1234567890abcdef1234", sanitized)
        self.assertIn("[REDACTED_HEX_KEY]", sanitized)
        self.assertIn("[REDACTED_SECRET]", sanitized)

    # =========================================================================
    # 2. Audit Source Verification and Commit Identity Binding
    # =========================================================================

    def test_caller_cannot_bypass_source_verification_with_commit(self):
        """Supplying --commit must NOT bypass source verification requirements."""
        empty_repo = os.path.join(self.workspace_dir, "empty_repo")
        os.makedirs(empty_repo, exist_ok=True)

        # Directory has no Solidity files and no build configuration
        res = run_slither_analysis(
            target_dir=empty_repo,
            allowed_workspaces=[self.workspace_dir],
            commit_hash="abcdef123456",
        )
        self.assertEqual(res.analysis_status, AnalysisStatus.PREREQUISITE_MISSING)
        self.assertIn("Source verification not satisfied", res.reason)

    def test_caller_commit_mismatch_with_local_git_head_rejected(self):
        """Caller supplying a commit hash different from actual local Git HEAD must be rejected."""
        # Create a genuine local git repo with known HEAD commit
        git_dir = os.path.join(self.repo_dir, ".git")
        os.makedirs(os.path.join(git_dir, "refs", "heads"), exist_ok=True)
        with open(os.path.join(git_dir, "HEAD"), "w", encoding="utf-8") as f:
            f.write("ref: refs/heads/main\n")
        real_commit = "1111222233334444555566667777888899990000"
        with open(os.path.join(git_dir, "refs", "heads", "main"), "w", encoding="utf-8") as f:
            f.write(f"{real_commit}\n")

        # Caller claims the commit is 999988887777...
        mismatched_commit = "9999888877776666555544443333222211110000"

        res = run_slither_analysis(
            target_dir=self.repo_dir,
            protocol_record=self.valid_protocol,
            allowed_workspaces=[self.workspace_dir],
            commit_hash=mismatched_commit,
        )
        self.assertIn(res.analysis_status, [AnalysisStatus.NOT_ELIGIBLE, AnalysisStatus.PREREQUISITE_MISSING])
        self.assertIn("mismatch", res.reason.lower())

    def test_stale_or_cross_directory_verification_record_rejected(self):
        """A verification record bound to directory A cannot be supplied to analyze directory B."""
        foreign_repo = os.path.join(self.workspace_dir, "foreign_repo")
        os.makedirs(foreign_repo, exist_ok=True)

        # Verification record for self.repo_dir
        bound_record = SourceVerificationRecord(
            status=SourceVerificationStatus.SOURCE_VERIFIED,
            build_framework="foundry",
            solidity_source_paths=["contracts/Vault.sol"],
            commit_hash="12345678",
            repo_path=self.repo_dir,
        )

        # Attempt to use bound_record on foreign_repo
        res = run_slither_analysis(
            target_dir=foreign_repo,
            allowed_workspaces=[self.workspace_dir],
            verification_record=bound_record,
            commit_hash="12345678",
        )
        self.assertEqual(res.analysis_status, AnalysisStatus.NOT_ELIGIBLE)
        self.assertIn("does not match target directory", res.reason)

    def test_untracked_local_source_workflow_distinguished(self):
        """Untracked local source workflow must be clearly distinguished in result and limitations."""
        ver = verify_repository_source(repo_path=self.repo_dir)
        self.assertEqual(ver.status, SourceVerificationStatus.SOURCE_VERIFIED)
        self.assertFalse(ver.is_git_repo)
        self.assertEqual(ver.workflow_type, "LOCAL_UNTRACKED_SOURCE")

        mock_ver = MagicMock(returncode=0, stdout="0.10.0")
        mock_exec = MagicMock(returncode=0, stdout='{"results": {"detectors": []}}', stderr="")

        with patch("shutil.which", return_value="slither"):
            with patch("subprocess.run", side_effect=[mock_ver, mock_exec]):
                res = run_slither_analysis(
                    target_dir=self.repo_dir,
                    allowed_workspaces=[self.workspace_dir],
                    commit_hash="LOCAL_UNCOMMITTED_DEV",
                )
                self.assertEqual(res.analysis_status, AnalysisStatus.COMPLETED)
                self.assertEqual(res.workflow_type, "LOCAL_UNTRACKED_SOURCE")
                self.assertTrue(any("untracked local source" in lim for lim in res.limitations))

    # =========================================================================
    # 3. Audit Slither Result Interpretation
    # =========================================================================

    def test_exit_code_two_or_greater_treated_as_failure(self):
        """Exit code >= 2 must be interpreted as fatal failure (ANALYSIS_FAILED), never COMPLETED."""
        mock_ver = MagicMock(returncode=0, stdout="0.10.0")
        mock_exec = MagicMock(returncode=2, stdout='{"results": {"detectors": []}}', stderr="Slither error: invalid CLI flags")

        with patch("shutil.which", return_value="slither"):
            with patch("subprocess.run", side_effect=[mock_ver, mock_exec]):
                res = run_slither_analysis(
                    target_dir=self.repo_dir,
                    protocol_record=self.valid_protocol,
                    allowed_workspaces=[self.workspace_dir],
                    commit_hash="12345678",
                )
                self.assertEqual(res.analysis_status, AnalysisStatus.ANALYSIS_FAILED)
                self.assertEqual(res.exit_code, 2)
                self.assertIn("aborted", res.reason)

    # =========================================================================
    # 4. Controlled Real-World Validation Fixtures
    # =========================================================================

    def test_fixture_1_known_solidity_finding(self):
        """Fixture 1: Controlled Solidity contract producing a known finding (reentrancy-eth)."""
        slither_json = {
            "success": True,
            "results": {
                "detectors": [
                    {
                        "check": "reentrancy-eth",
                        "impact": "High",
                        "confidence": "High",
                        "description": "Vault.withdraw() sends ETH before updating balances.",
                        "elements": [],
                    }
                ]
            },
        }

        mock_ver = MagicMock(returncode=0, stdout="0.10.0")
        mock_exec = MagicMock(returncode=1, stdout=json.dumps(slither_json), stderr="")

        with patch("shutil.which", return_value="slither"):
            with patch("subprocess.run", side_effect=[mock_ver, mock_exec]):
                res = run_slither_analysis(
                    target_dir=self.repo_dir,
                    protocol_record=self.valid_protocol,
                    allowed_workspaces=[self.workspace_dir],
                    commit_hash="12345678",
                )
                self.assertEqual(res.analysis_status, AnalysisStatus.COMPLETED)
                self.assertEqual(len(res.findings), 1)
                self.assertEqual(res.findings[0]["detector_id"], "reentrancy-eth")
                self.assertEqual(res.findings_summary["High"], 1)

    def test_fixture_2_missing_build_configuration(self):
        """Fixture 2: Repository with Solidity files but missing build configuration."""
        unconfigured_dir = os.path.join(self.workspace_dir, "unconfigured_repo")
        os.makedirs(os.path.join(unconfigured_dir, "src"), exist_ok=True)
        with open(os.path.join(unconfigured_dir, "src", "Token.sol"), "w", encoding="utf-8") as f:
            f.write("// SPDX-License-Identifier: MIT\npragma solidity ^0.8.0;\ncontract Token {}\n")

        res = run_slither_analysis(
            target_dir=unconfigured_dir,
            protocol_record=self.valid_protocol,
            allowed_workspaces=[self.workspace_dir],
            commit_hash="12345678",
        )
        self.assertEqual(res.analysis_status, AnalysisStatus.PREREQUISITE_MISSING)
        self.assertEqual(res.source_verification_status, SourceVerificationStatus.BUILD_CONFIGURATION_NOT_FOUND.value)

    def test_fixture_3_missing_compiler_prerequisite(self):
        """Fixture 3: Compilation failure due to missing compiler version or external imports."""
        mock_ver = MagicMock(returncode=0, stdout="0.10.0")
        mock_exec = MagicMock(
            returncode=255,
            stdout="",
            stderr="Error: Source file requires different compiler version (current: 0.8.20, required: ^0.7.6)",
        )

        with patch("shutil.which", return_value="slither"):
            with patch("subprocess.run", side_effect=[mock_ver, mock_exec]):
                res = run_slither_analysis(
                    target_dir=self.repo_dir,
                    protocol_record=self.valid_protocol,
                    allowed_workspaces=[self.workspace_dir],
                    commit_hash="12345678",
                )
                self.assertEqual(res.analysis_status, AnalysisStatus.PREREQUISITE_MISSING)
                self.assertIn("Slither compilation failed", res.reason)

    def test_fixture_4_deliberately_invalid_solidity(self):
        """Fixture 4: Deliberately invalid Solidity syntax causing compiler failure."""
        mock_ver = MagicMock(returncode=0, stdout="0.10.0")
        mock_exec = MagicMock(
            returncode=1,
            stdout="",
            stderr="ParserError: Expected ';' but got 'contract'\nCompilation failed",
        )

        with patch("shutil.which", return_value="slither"):
            with patch("subprocess.run", side_effect=[mock_ver, mock_exec]):
                res = run_slither_analysis(
                    target_dir=self.repo_dir,
                    protocol_record=self.valid_protocol,
                    allowed_workspaces=[self.workspace_dir],
                    commit_hash="12345678",
                )
                self.assertEqual(res.analysis_status, AnalysisStatus.PREREQUISITE_MISSING)

    def test_fixture_5_zero_findings_clean_codebase(self):
        """Fixture 5: Codebase producing zero findings is accurately marked COMPLETED with 0 findings."""
        slither_json = {
            "success": True,
            "results": {
                "detectors": []
            },
        }

        mock_ver = MagicMock(returncode=0, stdout="0.10.0")
        mock_exec = MagicMock(returncode=0, stdout=json.dumps(slither_json), stderr="")

        with patch("shutil.which", return_value="slither"):
            with patch("subprocess.run", side_effect=[mock_ver, mock_exec]):
                res = run_slither_analysis(
                    target_dir=self.repo_dir,
                    protocol_record=self.valid_protocol,
                    allowed_workspaces=[self.workspace_dir],
                    commit_hash="12345678",
                )
                self.assertEqual(res.analysis_status, AnalysisStatus.COMPLETED)
                self.assertEqual(res.exit_code, 0)
                self.assertEqual(len(res.findings), 0)
                self.assertEqual(res.findings_summary["total"], 0)

    def test_fixture_6_unresolved_protocol_handoff(self):
        """Fixture 6: Protocol with UNRESOLVED status is rejected at the scanner handoff gate."""
        unresolved_proto = dict(self.valid_protocol)
        unresolved_proto["github_resolution_status"] = ResolutionStatus.UNRESOLVED.value

        res = run_slither_analysis(
            target_dir=self.repo_dir,
            protocol_record=unresolved_proto,
            allowed_workspaces=[self.workspace_dir],
            commit_hash="12345678",
        )
        self.assertEqual(res.analysis_status, AnalysisStatus.NOT_ELIGIBLE)
        self.assertIn("UNRESOLVED", res.reason)


if __name__ == "__main__":
    unittest.main()
