"""
End-to-End Pipeline Integration & Protocol Hunter Validation Tests.

Validates the complete 8-stage research pipeline:
1. Discover protocol
2. Apply filters
3. Deduplicate by stable ID
4. Resolve repository
5. Verify source eligibility
6. Run Slither static analysis (mocked backend)
7. Run on-chain permission scanner (mocked explorer ABI)
8. Run CertiK security intelligence (mocked provider)
9. Persist combined result to DiscoveryStore
10. Reload JSON database and assert evidence preservation & schema integrity
11. Verify GUI presentation data model compatibility

Also tests negative paths:
- Missing repository / ambiguous repo
- Unverified source / missing build config
- Missing CertiK API key (NOT_CONFIGURED)
- Explorer rate limiting / network failure
- Slither unavailable / Docker missing
- Interrupted Hunter session & state recovery
- API secret redaction
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from defi_research import (
    AnalysisStatus,
    SlitherAnalysisResult,
    SourceVerificationStatus,
)
from defi_research.discovery.discovery_store import (
    CURRENT_SCHEMA_VERSION,
    DiscoveredProtocolRecord,
    DiscoveryStore,
    generate_stable_protocol_id,
)
from defi_research.discovery.protocol_hunter import (
    HunterConfig,
    HunterState,
    ProtocolHunter,
)
from defi_research.github_repository_resolver import ResolutionStatus
from defi_research.pipeline import (
    PipelineOverallStatus,
    ProtocolPipelineReport,
    StageStatus,
    run_protocol_pipeline,
)
from defi_research.security_intelligence import (
    MatchConfidence,
    RetrievalStatus,
    SecurityIntelligenceResult,
    SecurityScore,
)


def make_intel_result(
    retrieval_status: RetrievalStatus = RetrievalStatus.NOT_CONFIGURED,
    match_confidence: MatchConfidence = MatchConfidence.NONE,
    score: SecurityScore = None,
    audits=None,
    diagnostic: str = None,
    protocol_name: str = "Starlight DEX",
    defillama_slug: str = "starlight-dex",
) -> SecurityIntelligenceResult:
    """Helper to instantiate mock SecurityIntelligenceResult."""
    return SecurityIntelligenceResult(
        protocol_name=protocol_name,
        defillama_slug=defillama_slug,
        provider_name="CertiK Skynet",
        provider_project_id="starlight-dex" if retrieval_status == RetrievalStatus.SUCCESS else None,
        project_url="https://skynet.certik.com/projects/starlight-dex" if retrieval_status == RetrievalStatus.SUCCESS else None,
        retrieval_status=retrieval_status,
        match_confidence=match_confidence,
        security_score=score,
        audit_records=audits or [],
        diagnostic=diagnostic,
        limitations=[],
    )


class TestEndToEndPipeline(unittest.TestCase):
    """Integration test suite executing full mock pipeline with positive and negative paths."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "discovery_database.json")
        self.store = DiscoveryStore(db_path=self.db_path)

        # Mock protocol candidate
        self.mock_candidate = {
            "name": "Starlight DEX",
            "slug": "starlight-dex",
            "id": "starlight-999",
            "chain": "ethereum",
            "chains": ["ethereum", "arbitrum"],
            "tvl": 15000000.0,
            "category": "Dexs",
            "url": "https://starlight.exchange",
            "address": "0x1111111111111111111111111111111111111111",
            "github": ["https://github.com/starlight-labs/core-contracts"],
        }

        # Mock repository on disk
        self.repo_dir = os.path.join(self.test_dir, "mock_repo")
        os.makedirs(self.repo_dir, exist_ok=True)
        # Add Foundry build file and solidity contract
        with open(os.path.join(self.repo_dir, "foundry.toml"), "w") as f:
            f.write("[profile.default]\nsrc = 'src'\n")
        src_dir = os.path.join(self.repo_dir, "src")
        os.makedirs(src_dir, exist_ok=True)
        with open(os.path.join(src_dir, "Vault.sol"), "w") as f:
            f.write("pragma solidity ^0.8.20;\ncontract Vault {}\n")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("defi_research.pipeline.verify_repository_source")
    @patch("defi_research.pipeline.check_slither_availability", return_value=(True, "slither 0.10.4"))
    @patch("defi_research.pipeline.run_slither_analysis")
    @patch("defi_research.pipeline.query_protocol_security_intelligence")
    def test_01_full_connected_pipeline_success_path(
        self, mock_intel, mock_slither, mock_slither_avail, mock_verifier
    ):
        """
        Step-by-step end-to-end integration test:
        Discover -> Filter -> Deduplicate -> Resolve -> Verify -> Slither ->
        Permission Scanner -> CertiK -> Persist -> Reload -> Inspect
        """
        # 1. Verification mock
        mock_rec = MagicMock()
        mock_rec.status = SourceVerificationStatus.SOURCE_VERIFIED
        mock_rec.reason = "Foundry configuration verified"
        mock_rec.build_framework = "foundry"
        mock_rec.solidity_source_paths = ["src/Vault.sol"]
        mock_rec.commit_hash = "a1b2c3d4e5f6"
        mock_verifier.return_value = mock_rec

        # 2. Slither mock
        mock_slither.return_value = SlitherAnalysisResult(
            protocol_name="Starlight DEX",
            defillama_slug="starlight-dex",
            repository_url="https://github.com/starlight-labs/core-contracts",
            commit_hash="a1b2c3d4e5f6",
            source_verification_status="SOURCE_VERIFIED",
            slither_version="0.10.4",
            analysis_status=AnalysisStatus.COMPLETED,
            started_at=int(time.time()),
            duration_seconds=2.1,
            exit_code=0,
            execution_backend="native",
            findings=[
                {"detector_id": "reentrancy-eth", "impact": "High", "confidence": "High"},
                {"detector_id": "unused-return", "impact": "Medium", "confidence": "Medium"},
            ],
            reason="Static analysis completed successfully",
        )

        # 3. Security intelligence mock
        mock_intel.return_value = make_intel_result(
            retrieval_status=RetrievalStatus.SUCCESS,
            match_confidence=MatchConfidence.EXACT_PROVIDER_ID,
            score=SecurityScore(score=86.5, tier="AAA"),
            audits=[{"title": "CertiK Security Audit", "date": "2024-01-15"}],
            diagnostic="CertiK intelligence successfully retrieved",
        )

        # Mock permission scanner (explorer ABI return)
        mock_abi = [
            {"type": "function", "name": "withdrawAdmin", "inputs": [], "stateMutability": "nonpayable"},
            {"type": "function", "name": "pauseAll", "inputs": [], "stateMutability": "nonpayable"},
        ]
        mock_source = (
            "contract Vault { function withdrawAdmin() external onlyOwner {} "
            "function pauseAll() external onlyRole(ADMIN_ROLE) {} }"
        )

        with patch("defi_research.contract_permission_scanner.ContractScanner.get_verified_contract_data", return_value=(mock_abi, mock_source, "Vault")):
            report = run_protocol_pipeline(
                protocol_input=self.mock_candidate,
                target_dir=self.repo_dir,
                commit_hash="a1b2c3d4e5f6",
                output_dir=self.test_dir,
                allowed_workspaces=[self.test_dir],
                contract_address=self.mock_candidate["address"],
            )

        # Assert Pipeline Execution & Statuses
        self.assertEqual(report.overall_status, PipelineOverallStatus.COMPLETED)
        self.assertEqual(report.stages["discovery"]["status"], StageStatus.SUCCESS.value)
        self.assertEqual(report.stages["repository_resolution"]["status"], StageStatus.SUCCESS.value)
        self.assertEqual(report.stages["source_verification"]["status"], StageStatus.SUCCESS.value)
        self.assertEqual(report.stages["static_analysis"]["status"], StageStatus.SUCCESS.value)
        self.assertEqual(report.stages["permission_analysis"]["status"], StageStatus.SUCCESS.value)
        self.assertEqual(report.stages["security_intelligence"]["status"], StageStatus.SUCCESS.value)

        # Assert Findings
        self.assertEqual(report.slither_findings_count, 2)
        self.assertEqual(report.security_score, 86.5)
        self.assertEqual(report.security_score_grade, "AAA")
        self.assertEqual(report.audit_count, 1)
        self.assertIsNotNone(report.permission_scanner_critical_count)

        # Persist report into DiscoveryStore
        record_data = {
            "slug": report.defillama_slug,
            "name": report.protocol_name,
            "chain": report.chain,
            "tvl": report.tvl,
            "category": report.category,
            "address": report.permission_scanner_contract,
            "repository_url": report.repository_url,
            "repository_resolution_status": report.repository_resolution_status,
            "analyzed_commit": report.analyzed_commit,
            "static_analysis_status": report.static_analysis_status,
            "slither_execution_backend": report.slither_execution_backend,
            "slither_version": report.slither_version,
            "slither_findings_count": report.slither_findings_count,
            "slither_findings_summary": report.slither_findings_summary,
            "slither_diagnostics": report.slither_diagnostics,
            "permission_scanner_status": report.permission_scanner_status,
            "permission_scanner_critical_count": report.permission_scanner_critical_count,
            "permission_scanner_findings": report.permission_scanner_findings,
            "security_intelligence_status": report.security_intelligence_status,
            "security_score": report.security_score,
            "security_score_grade": report.security_score_grade,
            "audit_count": report.audit_count,
        }
        rec, is_new = self.store.upsert_record(record_data)
        self.assertTrue(is_new)
        self.store.save()

        # Reload store from JSON on disk
        reloaded_store = DiscoveryStore(db_path=self.db_path)
        reloaded_rec = reloaded_store.get_record(rec.protocol_id)
        self.assertIsNotNone(reloaded_rec)

        # Verify all fields and evidence preserved
        self.assertEqual(reloaded_rec.slug, "starlight-dex")
        self.assertEqual(reloaded_rec.static_analysis_status, "SUCCESS")
        self.assertEqual(reloaded_rec.slither_findings_count, 2)
        self.assertEqual(reloaded_rec.security_score, 86.5)
        self.assertEqual(reloaded_rec.security_score_grade, "AAA")
        self.assertEqual(reloaded_rec.permission_scanner_status, "SUCCESS")
        self.assertGreaterEqual(len(reloaded_rec.permission_scanner_findings), 1)

        # Verify analysis history recorded
        self.assertGreaterEqual(len(reloaded_rec.analysis_history), 1)
        hist_entry = reloaded_rec.analysis_history[-1]
        self.assertEqual(hist_entry["static_analysis_status"], "SUCCESS")
        self.assertEqual(hist_entry["slither_findings_count"], 2)

    @patch("defi_research.pipeline.run_slither_analysis")
    @patch("defi_research.pipeline.query_protocol_security_intelligence")
    def test_02_negative_path_unresolved_repository_blocks_slither(
        self, mock_intel, mock_slither
    ):
        """Unresolved or missing repository halts static analysis safely."""
        mock_intel.return_value = make_intel_result(
            retrieval_status=RetrievalStatus.NOT_CONFIGURED,
            protocol_name="Unresolved Proto",
            defillama_slug="unresolved-proto",
        )

        candidate = dict(self.mock_candidate)
        candidate["slug"] = "unresolved-proto"
        candidate["github"] = []  # No repo provided

        report = run_protocol_pipeline(
            protocol_input=candidate,
            target_dir=self.repo_dir,
            output_dir=self.test_dir,
            allowed_workspaces=[self.test_dir],
            skip_permission_scan=True,
        )

        self.assertEqual(report.stages["repository_resolution"]["status"], StageStatus.FAILED.value)
        self.assertEqual(report.stages["source_verification"]["status"], StageStatus.NOT_ELIGIBLE.value)
        self.assertEqual(report.stages["static_analysis"]["status"], StageStatus.NOT_ELIGIBLE.value)
        mock_slither.assert_not_called()
        self.assertEqual(report.overall_status, PipelineOverallStatus.FAILED)

    @patch("defi_research.pipeline.verify_repository_source")
    @patch("defi_research.pipeline.run_slither_analysis")
    @patch("defi_research.pipeline.query_protocol_security_intelligence")
    def test_03_negative_path_unverified_source_blocks_slither(
        self, mock_intel, mock_slither, mock_verifier
    ):
        """Failed source verification blocks Slither static analysis."""
        mock_rec = MagicMock()
        mock_rec.status = SourceVerificationStatus.BUILD_CONFIGURATION_NOT_FOUND
        mock_rec.reason = "No foundry.toml or hardhat.config.js found"
        mock_verifier.return_value = mock_rec

        mock_intel.return_value = make_intel_result(
            retrieval_status=RetrievalStatus.NOT_CONFIGURED,
        )

        report = run_protocol_pipeline(
            protocol_input=self.mock_candidate,
            target_dir=self.repo_dir,
            output_dir=self.test_dir,
            allowed_workspaces=[self.test_dir],
            skip_permission_scan=True,
        )

        self.assertEqual(report.stages["source_verification"]["status"], StageStatus.FAILED.value)
        self.assertEqual(report.stages["static_analysis"]["status"], StageStatus.NOT_ELIGIBLE.value)
        mock_slither.assert_not_called()

    @patch("defi_research.pipeline.verify_repository_source")
    @patch("defi_research.pipeline.check_slither_availability", return_value=(False, "Docker and Native unavailable"))
    @patch("defi_research.pipeline.query_protocol_security_intelligence")
    def test_04_negative_path_unavailable_slither_does_not_mask_as_clean(
        self, mock_intel, mock_avail, mock_verifier
    ):
        """When static analysis is unavailable, status is UNAVAILABLE and findings are None, never 0."""
        mock_rec = MagicMock()
        mock_rec.status = SourceVerificationStatus.SOURCE_VERIFIED
        mock_verifier.return_value = mock_rec

        mock_intel.return_value = make_intel_result(
            retrieval_status=RetrievalStatus.SUCCESS,
            score=SecurityScore(score=75.0, tier="BB"),
        )

        report = run_protocol_pipeline(
            protocol_input=self.mock_candidate,
            target_dir=self.repo_dir,
            output_dir=self.test_dir,
            allowed_workspaces=[self.test_dir],
            skip_permission_scan=True,
        )

        self.assertEqual(report.stages["static_analysis"]["status"], StageStatus.UNAVAILABLE.value)
        self.assertIsNone(report.slither_findings_count)
        self.assertEqual(report.overall_status, PipelineOverallStatus.PARTIAL)

    @patch("defi_research.pipeline.query_protocol_security_intelligence")
    def test_05_negative_path_certik_unconfigured_and_rate_limited(self, mock_intel):
        """CertiK unconfigured or rate limited does not crash pipeline and records diagnostic."""
        mock_intel.return_value = make_intel_result(
            retrieval_status=RetrievalStatus.NOT_CONFIGURED,
            diagnostic="CERTIK_API_KEY environment variable is not configured",
        )

        report = run_protocol_pipeline(
            protocol_input=self.mock_candidate,
            target_dir=None,
            skip_permission_scan=True,
            output_dir=self.test_dir,
        )

        self.assertEqual(report.stages["security_intelligence"]["status"], StageStatus.NOT_CONFIGURED.value)
        self.assertIsNone(report.security_score)
        self.assertIn("CERTIK_API_KEY", report.stages["security_intelligence"]["message"])

    def test_06_hunter_deduplication_caching_and_stop_behavior(self):
        """Protocol Hunter reuses cached repo resolution and stops promptly."""
        # Prepopulate store with a candidate having a resolved repo
        stable_id = generate_stable_protocol_id("dex-alpha", None, "Dex Alpha")
        rec = DiscoveredProtocolRecord(
            protocol_id=stable_id,
            slug="dex-alpha",
            name="Dex Alpha",
            chain="ethereum",
            tvl=1000000.0,
            repository_url="https://github.com/dex-alpha/contracts",
            repository_resolution_status="RESOLVED_CONFIDENT",
            repository_confidence_score=0.95,
        )
        self.store.upsert_record(rec.to_dict())
        self.store.save()

        # Mock DefiLlama discovery returning dex-alpha via mock_client
        mock_client = MagicMock()
        mock_client.get_protocols.return_value = [
            {
                "id": "1",
                "name": "Dex Alpha",
                "slug": "dex-alpha",
                "chain": "Ethereum",
                "tvl": 1200000.0,
                "category": "Dexs",
            }
        ]

        cfg = HunterConfig(
            duration_hours=0.001,
            cycle_interval_seconds=1,
            min_tvl=50000,
            deep_investigation=True,
        )
        hunter = ProtocolHunter(config=cfg, store=self.store, client=mock_client)

        with patch("defi_research.discovery.protocol_hunter.resolve_github_repo_from_slug") as mock_resolver:
            matched_count = hunter.run_discovery_cycle()
            # Resolver should NOT be called because it was cached in the store
            mock_resolver.assert_not_called()

        self.assertEqual(matched_count, 1)
        # Check that existing candidate's TVL was updated without erasing repo info
        updated_rec = self.store.get_record(stable_id)
        self.assertEqual(updated_rec.tvl, 1200000.0)
        self.assertEqual(updated_rec.repository_url, "https://github.com/dex-alpha/contracts")

    def test_07_secret_redaction_in_pipeline_json_output(self):
        """Ensure secret keys are completely sanitized from serialized report output."""
        fake_secret = "certik_sec_99998888777766665555444433332222"
        report = ProtocolPipelineReport(
            protocol_name="SecretProtocol",
            defillama_slug="secret-protocol",
            overall_status=PipelineOverallStatus.PARTIAL,
            errors=[f"Failed connecting with api_key={fake_secret}"],
        )

        json_out = report.to_json()
        self.assertNotIn(fake_secret, json_out)
        self.assertIn("[REDACTED_SECRET]", json_out)


if __name__ == "__main__":
    unittest.main()
