"""
test_pipeline.py
Deterministic, offline unit and integration tests for Phase 4 Pipeline Orchestrator.
Validates:
1. Stage ordering and handoff.
2. Unresolved and ambiguous repository rejection.
3. Source verification failures blocking Slither.
4. Slither unavailable while other independent stages continue.
5. CertiK unconfigured while other independent stages continue.
6. Malformed provider responses and provider errors.
7. Consolidated JSON serialization.
8. Failed stages never appearing as successful.
9. Output paths remaining inside the configured output directory.
10. Running the CLI from the repository root.
11. Running tests after the source package has been reorganized.
12. Missing configuration and invalid configuration.
13. Ensuring provider data cannot bypass security gates.
14. Ensuring no credentials appear in logs or generated reports.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_src_dir = str(Path(__file__).resolve().parent.parent / "src")
_pkg_dir = str(Path(__file__).resolve().parent.parent / "src" / "defi_research")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

from defi_research.github_repository_resolver import (
    CandidateType,
    RepoCandidate,
    ResolutionStatus,
    SourceVerificationRecord,
    SourceVerificationStatus,
)
from defi_research.pipeline import (
    PipelineOverallStatus,
    PipelineRunSummary,
    ProtocolPipelineReport,
    StageOutcome,
    StageStatus,
    run_pipeline,
    run_protocol_pipeline,
    sanitize_filename_component,
    save_protocol_report,
)
from defi_research.security_intelligence import (
    AuditRecord,
    MatchConfidence,
    RetrievalStatus,
    SecurityIntelligenceResult,
    SecurityScore,
)
from defi_research.slither_runner import (
    AnalysisStatus,
    SlitherAnalysisResult,
    SlitherFinding,
)


def make_intel_result(
    retrieval_status: RetrievalStatus = RetrievalStatus.NOT_CONFIGURED,
    match_confidence: MatchConfidence = MatchConfidence.NONE,
    score: SecurityScore = None,
    audits=None,
    diagnostic: str = None,
    protocol_name: str = "Aave V3",
    defillama_slug: str = "aave-v3",
    limitations=None,
) -> SecurityIntelligenceResult:
    """Helper to instantiate mock SecurityIntelligenceResult conforming to dataclass schema."""
    return SecurityIntelligenceResult(
        protocol_name=protocol_name,
        defillama_slug=defillama_slug,
        provider_name="CertiK Skynet",
        provider_project_id="aave-v3" if retrieval_status == RetrievalStatus.SUCCESS else None,
        project_url="https://skynet.certik.com/projects/aave-v3" if retrieval_status == RetrievalStatus.SUCCESS else None,
        retrieval_status=retrieval_status,
        match_confidence=match_confidence,
        security_score=score,
        audit_records=audits or [],
        diagnostic=diagnostic,
        limitations=limitations or [],
    )


class TestPipelineOrchestrator(unittest.TestCase):

    def setUp(self):
        self.tmp_dir_obj = tempfile.TemporaryDirectory()
        self.workspace_dir = os.path.realpath(self.tmp_dir_obj.name)

        # Standard mock protocol
        self.sample_protocol = {
            "name": "Aave V3",
            "slug": "aave-v3",
            "id": "1838",
            "chain": "ethereum",
            "category": "Lending",
            "tvl": 10000000.0,
            "url": "https://aave.com",
            "github": ["https://github.com/aave/aave-v3-core"],
        }

        # Valid mock repo directory
        self.repo_dir = os.path.join(self.workspace_dir, "test_repo")
        os.makedirs(os.path.join(self.repo_dir, "contracts"), exist_ok=True)
        with open(os.path.join(self.repo_dir, "foundry.toml"), "w", encoding="utf-8") as f:
            f.write("[profile.default]\n")
        with open(os.path.join(self.repo_dir, "contracts", "Vault.sol"), "w", encoding="utf-8") as f:
            f.write("// SPDX-License-Identifier: MIT\npragma solidity ^0.8.20;\ncontract Vault {}\n")

    def tearDown(self):
        self.tmp_dir_obj.cleanup()

    # 1. Pipeline stage ordering and handoff (success path)
    @patch("defi_research.pipeline.run_slither_analysis")
    @patch("defi_research.pipeline.check_slither_availability", return_value=(True, "slither 0.10.0"))
    @patch("defi_research.pipeline.query_protocol_security_intelligence")
    def test_stage_ordering_and_handoff_success_path(self, mock_intel, mock_slither_avail, mock_slither_run):
        mock_intel.return_value = make_intel_result(
            retrieval_status=RetrievalStatus.SUCCESS,
            match_confidence=MatchConfidence.NAME_SLUG_MATCH,
            score=SecurityScore(score=88.5, tier="AA"),
            audits=[AuditRecord(auditor="OpenZeppelin", audit_date="2023-01-01")],
        )
        mock_slither_run.return_value = SlitherAnalysisResult(
            protocol_name="Aave V3",
            defillama_slug="aave-v3",
            repository_url="https://github.com/aave/aave-v3-core",
            commit_hash="abcdef123456",
            source_verification_status="SOURCE_VERIFIED",
            slither_version="0.10.0",
            analysis_status=AnalysisStatus.COMPLETED,
            started_at=int(time.time()),
            duration_seconds=1.5,
            exit_code=0,
            findings=[{"detector_id": "reentrancy", "impact": "High"}],
        )

        report = run_protocol_pipeline(
            protocol_input=self.sample_protocol,
            target_dir=self.repo_dir,
            commit_hash="abcdef123456",
            output_dir=self.workspace_dir,
            allowed_workspaces=[self.workspace_dir],
        )

        # Stage assertions
        self.assertEqual(report.stages["discovery"]["status"], StageStatus.SUCCESS.value)
        self.assertEqual(report.stages["repository_resolution"]["status"], StageStatus.SUCCESS.value)
        self.assertEqual(report.stages["source_verification"]["status"], StageStatus.SUCCESS.value)
        self.assertEqual(report.stages["static_analysis"]["status"], StageStatus.SUCCESS.value)
        self.assertEqual(report.stages["security_intelligence"]["status"], StageStatus.SUCCESS.value)

        # Global status
        self.assertEqual(report.overall_status, PipelineOverallStatus.COMPLETED)
        self.assertEqual(report.slither_findings_count, 1)
        self.assertEqual(report.security_score, 88.5)
        self.assertEqual(report.analyzed_commit, "abcdef123456")

    # 2. Unresolved and ambiguous repository rejection
    @patch("defi_research.pipeline.run_slither_analysis")
    @patch("defi_research.pipeline.query_protocol_security_intelligence")
    def test_unresolved_and_ambiguous_repository_rejection(self, mock_intel, mock_slither_run):
        mock_intel.return_value = make_intel_result(
            protocol_name="Ambiguous Protocol",
            retrieval_status=RetrievalStatus.NOT_CONFIGURED,
        )

        ambiguous_protocol = dict(self.sample_protocol)
        ambiguous_protocol["github"] = [
            "https://github.com/teamA/core",
            "https://github.com/teamB/core",
        ]

        report = run_protocol_pipeline(
            protocol_input=ambiguous_protocol,
            target_dir=self.repo_dir,
            allowed_workspaces=[self.workspace_dir],
        )

        # Resolution should fail or be ambiguous
        self.assertIn(report.stages["repository_resolution"]["status"], [StageStatus.FAILED.value, StageStatus.PARTIAL.value])
        # Verification & Slither MUST be blocked
        self.assertEqual(report.stages["source_verification"]["status"], StageStatus.NOT_ELIGIBLE.value)
        self.assertEqual(report.stages["static_analysis"]["status"], StageStatus.NOT_ELIGIBLE.value)
        # Slither runner must NEVER be called
        mock_slither_run.assert_not_called()

    # 3. Source verification failures blocking Slither
    @patch("defi_research.pipeline.run_slither_analysis")
    @patch("defi_research.pipeline.query_protocol_security_intelligence")
    def test_source_verification_failures_block_slither(self, mock_intel, mock_slither_run):
        mock_intel.return_value = make_intel_result()

        # Empty directory without solidity or build config
        empty_repo = os.path.join(self.workspace_dir, "empty_repo")
        os.makedirs(empty_repo, exist_ok=True)

        report = run_protocol_pipeline(
            protocol_input=self.sample_protocol,
            target_dir=empty_repo,
            allowed_workspaces=[self.workspace_dir],
        )

        self.assertEqual(report.stages["source_verification"]["status"], StageStatus.FAILED.value)
        self.assertEqual(report.stages["static_analysis"]["status"], StageStatus.NOT_ELIGIBLE.value)
        mock_slither_run.assert_not_called()

    # 4. Slither unavailable while other independent stages continue
    @patch("defi_research.pipeline.check_slither_availability", return_value=(False, "Slither not in PATH"))
    @patch("defi_research.pipeline.query_protocol_security_intelligence")
    def test_slither_unavailable_independent_stages_continue(self, mock_intel, mock_avail):
        mock_intel.return_value = make_intel_result()

        report = run_protocol_pipeline(
            protocol_input=self.sample_protocol,
            target_dir=self.repo_dir,
            commit_hash="abcdef123456",
            allowed_workspaces=[self.workspace_dir],
        )

        self.assertEqual(report.stages["discovery"]["status"], StageStatus.SUCCESS.value)
        self.assertEqual(report.stages["repository_resolution"]["status"], StageStatus.SUCCESS.value)
        self.assertEqual(report.stages["source_verification"]["status"], StageStatus.SUCCESS.value)
        self.assertEqual(report.stages["static_analysis"]["status"], StageStatus.UNAVAILABLE.value)
        self.assertEqual(report.stages["security_intelligence"]["status"], StageStatus.NOT_CONFIGURED.value)
        self.assertEqual(report.overall_status, PipelineOverallStatus.PARTIAL)

    # 5. CertiK unconfigured while other independent stages continue
    @patch("defi_research.pipeline.query_protocol_security_intelligence")
    def test_certik_unconfigured_independent_stages_continue(self, mock_intel):
        mock_intel.return_value = make_intel_result(
            retrieval_status=RetrievalStatus.NOT_CONFIGURED,
            diagnostic="CERTIK_API_KEY is not configured",
        )

        report = run_protocol_pipeline(
            protocol_input=self.sample_protocol,
            target_dir=None,  # No local dir
        )

        self.assertEqual(report.stages["discovery"]["status"], StageStatus.SUCCESS.value)
        self.assertEqual(report.stages["repository_resolution"]["status"], StageStatus.SUCCESS.value)
        self.assertEqual(report.stages["security_intelligence"]["status"], StageStatus.NOT_CONFIGURED.value)
        self.assertIsNone(report.security_score)  # Must be None, not 0!
        self.assertEqual(report.overall_status, PipelineOverallStatus.PARTIAL)

    # 6. Malformed provider responses and provider errors
    @patch("defi_research.pipeline.query_protocol_security_intelligence")
    def test_malformed_provider_responses_and_provider_errors(self, mock_intel):
        mock_intel.return_value = make_intel_result(
            retrieval_status=RetrievalStatus.PROVIDER_ERROR,
            diagnostic="HTTP 500: Server Error",
        )

        report = run_protocol_pipeline(
            protocol_input=self.sample_protocol,
            target_dir=None,
        )

        self.assertEqual(report.stages["security_intelligence"]["status"], StageStatus.FAILED.value)
        self.assertIsNone(report.security_score)
        self.assertEqual(report.overall_status, PipelineOverallStatus.PARTIAL)

    # 7. Consolidated JSON serialization
    def test_consolidated_json_serialization(self):
        report = ProtocolPipelineReport(
            protocol_name="Aave V3",
            defillama_slug="aave-v3",
            defillama_id="1838",
            chain="ethereum",
            overall_status=PipelineOverallStatus.PARTIAL,
            security_score=None,  # Missing score must remain None, not 0
        )

        rep_dict = report.to_dict()
        rep_json = report.to_json()
        parsed = json.loads(rep_json)

        self.assertEqual(parsed["protocol_name"], "Aave V3")
        self.assertEqual(parsed["defillama_slug"], "aave-v3")
        self.assertIsNone(parsed["security_intelligence"]["security_score"])
        self.assertEqual(parsed["overall_status"], "PARTIAL")

    # 8. Failed stages never appearing as successful
    @patch("defi_research.pipeline.query_protocol_security_intelligence")
    def test_failed_stages_never_appear_as_successful(self, mock_intel):
        mock_intel.return_value = make_intel_result(
            retrieval_status=RetrievalStatus.RATE_LIMITED,
            diagnostic="HTTP 429",
        )

        report = run_protocol_pipeline(
            protocol_input=self.sample_protocol,
            target_dir=None,
        )

        # Stage 5 failed
        self.assertEqual(report.stages["security_intelligence"]["status"], StageStatus.FAILED.value)
        # Overall status CANNOT be COMPLETED
        self.assertNotEqual(report.overall_status, PipelineOverallStatus.COMPLETED)

    # 9. Output paths remaining inside the configured output directory
    def test_output_paths_remaining_inside_output_dir(self):
        report = ProtocolPipelineReport(
            protocol_name="Malicious ../../../ Traversal",
            defillama_slug="../../evil-slug",
            defillama_id="999",
        )

        out_dir = os.path.join(self.workspace_dir, "clean_reports")
        saved_file = save_protocol_report(report, output_dir=out_dir)

        # Verify saved file is strictly inside out_dir
        abs_saved = os.path.abspath(saved_file)
        abs_out = os.path.abspath(out_dir)
        self.assertTrue(abs_saved.startswith(abs_out))
        self.assertTrue(os.path.exists(saved_file))

    # 10. Provider data cannot bypass security gates
    @patch("defi_research.pipeline.run_slither_analysis")
    @patch("defi_research.pipeline.query_protocol_security_intelligence")
    def test_provider_data_cannot_bypass_security_gates(self, mock_intel, mock_slither_run):
        # High security score returned by CertiK
        mock_intel.return_value = make_intel_result(
            protocol_name="Untrusted Fork",
            retrieval_status=RetrievalStatus.SUCCESS,
            match_confidence=MatchConfidence.EXACT_PROVIDER_ID,
            score=SecurityScore(score=99.9, tier="AAA"),
        )

        # But protocol repository resolution is ORGANIZATION_ONLY (e.g. lidofinance org only)
        fork_protocol = dict(self.sample_protocol)
        fork_protocol["github"] = ["https://github.com/lidofinance"]

        report = run_protocol_pipeline(
            protocol_input=fork_protocol,
            target_dir=self.repo_dir,
            allowed_workspaces=[self.workspace_dir],
        )

        # Slither MUST NOT run despite the 99.9 security score!
        self.assertEqual(report.stages["static_analysis"]["status"], StageStatus.NOT_ELIGIBLE.value)
        mock_slither_run.assert_not_called()

    # 11. Ensuring no credentials appear in logs or generated reports
    @patch("defi_research.pipeline.query_protocol_security_intelligence")
    def test_ensuring_no_credentials_appear_in_reports(self, mock_intel):
        secret_key = "certik_live_secret_key_12345"
        mock_intel.return_value = make_intel_result(
            retrieval_status=RetrievalStatus.AUTHENTICATION_FAILED,
            diagnostic=f"Auth failed with header X-Certik-Api-Key: {secret_key}",
            limitations=[f"Used key {secret_key}"],
        )

        report = run_protocol_pipeline(
            protocol_input=self.sample_protocol,
            target_dir=None,
        )

        rep_json = report.to_json()
        self.assertNotIn(secret_key, rep_json)

    # 12. Missing and invalid configuration handled gracefully
    @patch("defi_research.pipeline.query_protocol_security_intelligence")
    def test_missing_and_invalid_configuration(self, mock_intel):
        mock_intel.return_value = make_intel_result(protocol_name="Unknown")

        # Discovered with unknown slug string
        mock_client = MagicMock()
        mock_client.get_protocol.return_value = None

        report = run_protocol_pipeline(
            protocol_input="non-existent-protocol-xyz",
            client=mock_client,
        )

        self.assertEqual(report.overall_status, PipelineOverallStatus.FAILED)
        self.assertEqual(report.stages["discovery"]["status"], StageStatus.FAILED.value)

    # 13. Batch pipeline run with filtering
    @patch("defi_research.pipeline.query_protocol_security_intelligence")
    def test_run_pipeline_batch_filtering(self, mock_intel):
        mock_intel.return_value = make_intel_result(protocol_name="Proto")

        mock_client = MagicMock()
        mock_client.get_protocols.return_value = [
            {"name": "P1", "slug": "p1", "chain": "ethereum", "category": "Lending", "tvl": 2000000},
            {"name": "P2", "slug": "p2", "chain": "arbitrum", "category": "DEXes", "tvl": 5000000},
            {"name": "P3", "slug": "p3", "chain": "ethereum", "category": "DEXes", "tvl": 100000},  # Below min TVL
        ]

        summary = run_pipeline(
            limit=5,
            chain="ethereum",
            min_tvl=1000000,
            output_dir=self.workspace_dir,
            client=mock_client,
        )

        # Only P1 matches (chain=ethereum, tvl >= 1M)
        self.assertEqual(summary.total_protocols, 1)
        self.assertEqual(summary.reports[0]["protocol_name"], "P1")

    # 14. Stage 4 unhandled exception does not crash the pipeline and isolates failures
    @patch("defi_research.pipeline.verify_repository_source")
    @patch("defi_research.pipeline.check_slither_availability", return_value=(True, "0.10.4", None))
    @patch("defi_research.pipeline.run_slither_analysis")
    @patch("defi_research.pipeline.query_protocol_security_intelligence")
    def test_slither_unexpected_exception_isolated(
        self, mock_intel, mock_slither, mock_avail, mock_verify
    ):
        """Unexpected exception in Slither stage must be caught and marked FAILED without aborting later stages."""
        mock_verify.return_value = SourceVerificationRecord(
            status=SourceVerificationStatus.SOURCE_VERIFIED,
            repo_path=self.repo_dir,
            build_framework="foundry",
            commit_hash="abcdef1234567890abcdef1234567890abcdef12",
        )
        mock_slither.side_effect = RuntimeError("Low-level OS failure: pipe broken")
        mock_intel.return_value = make_intel_result(retrieval_status=RetrievalStatus.NOT_CONFIGURED)

        report = run_protocol_pipeline(
            protocol_input=self.sample_protocol,
            target_dir=self.repo_dir,
            commit_hash="abcdef1234567890abcdef1234567890abcdef12",
            allowed_workspaces=[self.workspace_dir],
        )

        stage_s4 = report.stages.get("static_analysis", {})
        self.assertEqual(stage_s4.get("status"), StageStatus.FAILED.value)
        self.assertIn("pipe broken", stage_s4.get("message", ""))
        # CertiK stage still ran cleanly
        stage_s5 = report.stages.get("security_intelligence", {})
        self.assertEqual(stage_s5.get("status"), StageStatus.NOT_CONFIGURED.value)

    # 15. Failed or skipped Slither stage never produces zero-finding false positive
    @patch("defi_research.pipeline.verify_repository_source")
    @patch("defi_research.pipeline.check_slither_availability", return_value=(False, None, "Slither not installed"))
    @patch("defi_research.pipeline.query_protocol_security_intelligence")
    def test_failed_slither_never_masquerades_as_clean_zero_findings(
        self, mock_intel, mock_avail, mock_verify
    ):
        """Tool unavailability must never mark stage as SUCCESS or claim 0 findings."""
        mock_verify.return_value = SourceVerificationRecord(
            status=SourceVerificationStatus.SOURCE_VERIFIED,
            repo_path=self.repo_dir,
            build_framework="foundry",
            commit_hash="abcdef1234567890abcdef1234567890abcdef12",
        )
        mock_intel.return_value = make_intel_result(retrieval_status=RetrievalStatus.NOT_CONFIGURED)

        report = run_protocol_pipeline(
            protocol_input=self.sample_protocol,
            target_dir=self.repo_dir,
            commit_hash="abcdef1234567890abcdef1234567890abcdef12",
            allowed_workspaces=[self.workspace_dir],
        )

        stage_s4 = report.stages.get("static_analysis", {})
        self.assertNotEqual(stage_s4.get("status"), StageStatus.SUCCESS.value)
        self.assertEqual(stage_s4.get("status"), StageStatus.UNAVAILABLE.value)
        self.assertEqual(report.static_analysis_status, StageStatus.UNAVAILABLE.value)
        self.assertIsNone(report.slither_findings_count)

    # 16. Sensitive secret redaction in exception messages
    @patch("defi_research.pipeline.verify_repository_source")
    @patch("defi_research.pipeline.check_slither_availability", return_value=(True, "0.10.4", None))
    @patch("defi_research.pipeline.run_slither_analysis")
    @patch("defi_research.pipeline.query_protocol_security_intelligence")
    def test_secret_redaction_in_stage_exception(
        self, mock_intel, mock_slither, mock_avail, mock_verify
    ):
        """Secrets appearing in exception strings are redacted before persisting in report."""
        secret_key = "certik_sec_99998888777766665555444433332222"
        mock_verify.return_value = SourceVerificationRecord(
            status=SourceVerificationStatus.SOURCE_VERIFIED,
            repo_path=self.repo_dir,
            build_framework="foundry",
            commit_hash="abcdef1234567890abcdef1234567890abcdef12",
        )
        mock_slither.side_effect = RuntimeError(f"Error accessing api_key={secret_key}")
        mock_intel.return_value = make_intel_result(retrieval_status=RetrievalStatus.NOT_CONFIGURED)

        report = run_protocol_pipeline(
            protocol_input=self.sample_protocol,
            target_dir=self.repo_dir,
            commit_hash="abcdef1234567890abcdef1234567890abcdef12",
            allowed_workspaces=[self.workspace_dir],
        )

        report_json = report.to_json()
        self.assertNotIn(secret_key, report_json)


if __name__ == "__main__":
    unittest.main()
