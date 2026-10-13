"""
test_security_intelligence.py
Deterministic, offline unit and integration tests for security_intelligence.py.
Validates normalized provider responses, conservative matching, status handling,
secret redaction, error conditions, and strict decoupling from Slither eligibility gates.
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

_src_dir = str(Path(__file__).resolve().parent.parent / "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

try:
    from defi_research.github_repository_resolver import ResolutionStatus, SourceVerificationRecord, SourceVerificationStatus, evaluate_slither_eligibility
    from defi_research.security_intelligence import (
        CERTIK_CHAIN_PREFIX_MAP,
        AuditRecord,
        CertikSkynetProvider,
        MatchConfidence,
        ProtocolQuery,
        RetrievalStatus,
        SecurityIntelligenceResult,
        SecurityScore,
        check_live_api_connectivity,
        normalize_chain_prefix,
        query_protocol_security_intelligence,
        sanitize_secret_text,
    )
    from defi_research.slither_runner import AnalysisStatus, run_slither_analysis
except ImportError:
    from github_repository_resolver import ResolutionStatus, SourceVerificationRecord, SourceVerificationStatus, evaluate_slither_eligibility
    from security_intelligence import (
        CERTIK_CHAIN_PREFIX_MAP,
        AuditRecord,
        CertikSkynetProvider,
        MatchConfidence,
        ProtocolQuery,
        RetrievalStatus,
        SecurityIntelligenceResult,
        SecurityScore,
        check_live_api_connectivity,
        normalize_chain_prefix,
        query_protocol_security_intelligence,
        sanitize_secret_text,
    )
    from slither_runner import AnalysisStatus, run_slither_analysis


class TestSecurityIntelligence(unittest.TestCase):

    def setUp(self):
        self.sample_skynet_payload = {
            "items": [
                {
                    "certiKProjectId": "aave",
                    "projectName": "Aave",
                    "certiKProjectPage": "https://skynet.certik.com/projects/aave",
                    "projectTokenStatus": "cryptocurrency",
                    "primaryTokenContractAddress": "ethereum:0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9",
                    "auditedByCertiK": True,
                    "lastAuditDate": "2023-01-15T00:00:00Z",
                    "securityScore": {
                        "score": 92.4,
                        "rank": 5,
                        "tier": "AAA",
                        "rankPercentile": 99.2,
                        "codeSecurity": 94.0,
                        "fundamentalHealth": 90.5,
                        "operationalResilience": 91.0,
                        "governanceStrength": 93.5,
                        "marketStability": 93.0,
                    },
                }
            ]
        }

    # =========================================================================
    # 1. Configuration & Unconfigured Gate
    # =========================================================================

    def test_unconfigured_provider_returns_not_configured(self):
        """When CERTIK_API_KEY is missing, provider returns NOT_CONFIGURED without calling network."""
        provider = CertikSkynetProvider(api_key="")
        self.assertFalse(provider.is_configured())

        query = ProtocolQuery(name="Aave V3", defillama_slug="aave-v3")
        res = provider.fetch_intelligence(query)

        self.assertEqual(res.retrieval_status, RetrievalStatus.NOT_CONFIGURED)
        self.assertEqual(res.match_confidence, MatchConfidence.NONE)
        self.assertIsNone(res.security_score)
        self.assertEqual(len(res.audit_records), 0)
        self.assertEqual(res.error_category, "MISSING_CREDENTIALS")
        self.assertIn("CERTIK_API_KEY", res.diagnostic)
        self.assertIn("APIsupport@certik.com", res.diagnostic)

    # =========================================================================
    # 2. Valid Normalized Response & Matching Hierarchies
    # =========================================================================

    def test_valid_exact_id_match_and_score_normalization(self):
        """Valid response with matching provider ID normalizes score, rank, tier, and audits."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self.sample_skynet_payload

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        provider = CertikSkynetProvider(api_key="test_key_12345", session=mock_session)
        query = ProtocolQuery(name="Aave", defillama_slug="aave", provider_project_id="aave")

        res = provider.fetch_intelligence(query)

        self.assertEqual(res.retrieval_status, RetrievalStatus.SUCCESS)
        self.assertEqual(res.match_confidence, MatchConfidence.EXACT_PROVIDER_ID)
        self.assertEqual(res.provider_project_id, "aave")
        self.assertIsNotNone(res.security_score)
        self.assertEqual(res.security_score.score, 92.4)
        self.assertEqual(res.security_score.tier, "AAA")
        self.assertEqual(res.security_score.rank, 5)
        self.assertEqual(res.security_score.sub_scores.get("codeSecurity"), 94.0)

        # Audit record
        self.assertEqual(len(res.audit_records), 1)
        self.assertEqual(res.audit_records[0].auditor, "CertiK")
        self.assertEqual(res.audit_records[0].status, "Audited by CertiK")
        self.assertEqual(res.audit_records[0].audit_date, "2023-01-15T00:00:00Z")

    def test_conservative_contract_address_matching(self):
        """Matching by chain and primary token contract address provides CHAIN_CONTRACT_MATCH confidence."""
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = self.sample_skynet_payload

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        provider = CertikSkynetProvider(api_key="test_key_12345", session=mock_session)
        query = ProtocolQuery(
            name="Aave V3 Protocol",
            contract_address="0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9",
            chain="ethereum",
        )

        res = provider.fetch_intelligence(query)
        self.assertEqual(res.retrieval_status, RetrievalStatus.SUCCESS)
        self.assertEqual(res.match_confidence, MatchConfidence.CHAIN_CONTRACT_MATCH)
        self.assertEqual(res.provider_project_id, "aave")

    # =========================================================================
    # 3. Missing Score & Missing Audit Integrity (Preserving Unknowns)
    # =========================================================================

    def test_missing_score_preserved_as_none_not_zero(self):
        """CRITICAL: A project without a published score must have security_score.score=None, NEVER 0.0."""
        payload = {
            "items": [
                {
                    "certiKProjectId": "new-protocol",
                    "projectName": "New Protocol",
                    "certiKProjectPage": "https://skynet.certik.com/projects/new-protocol",
                    "auditedByCertiK": False,
                    "securityScore": {
                        "score": None,  # Not scored yet
                        "tier": "Unrated",
                    },
                }
            ]
        }
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = payload
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        provider = CertikSkynetProvider(api_key="test_key_12345", session=mock_session)
        query = ProtocolQuery(name="New Protocol", defillama_slug="new-protocol")

        res = provider.fetch_intelligence(query)
        self.assertEqual(res.retrieval_status, RetrievalStatus.SUCCESS)
        self.assertIsNotNone(res.security_score)
        self.assertIsNone(res.security_score.score)
        self.assertNotEqual(res.security_score.score, 0.0)
        self.assertEqual(res.security_score.tier, "Unrated")

    def test_missing_audit_record_not_treated_as_zero_audits(self):
        """When auditedByCertiK is False, audit_records is empty without implying project was never audited elsewhere."""
        payload = {
            "items": [
                {
                    "certiKProjectId": "curve",
                    "projectName": "Curve",
                    "auditedByCertiK": False,
                    "securityScore": {"score": 88.0},
                }
            ]
        }
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = payload
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        provider = CertikSkynetProvider(api_key="test_key_12345", session=mock_session)
        query = ProtocolQuery(name="Curve", defillama_slug="curve")

        res = provider.fetch_intelligence(query)
        self.assertEqual(res.retrieval_status, RetrievalStatus.SUCCESS)
        self.assertEqual(len(res.audit_records), 0)
        # Verify explicit limitation is preserved
        self.assertTrue(any("does not imply the protocol has never undergone auditing" in lim for lim in res.limitations))

    # =========================================================================
    # 4. Ambiguous Matches & Conservative Rejection
    # =========================================================================

    def test_ambiguous_matches_rejected(self):
        """When multiple distinct projects match the same name/slug, must return AMBIGUOUS."""
        payload = {
            "items": [
                {
                    "certiKProjectId": "lido-dao",
                    "projectName": "Lido",
                    "certiKProjectPage": "https://skynet.certik.com/projects/lido-dao",
                },
                {
                    "certiKProjectId": "lido-finance-v2",
                    "projectName": "Lido",
                    "certiKProjectPage": "https://skynet.certik.com/projects/lido-finance-v2",
                },
            ]
        }
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = payload
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        provider = CertikSkynetProvider(api_key="test_key_12345", session=mock_session)
        query = ProtocolQuery(name="Lido")

        res = provider.fetch_intelligence(query)
        self.assertEqual(res.retrieval_status, RetrievalStatus.AMBIGUOUS)
        self.assertEqual(res.match_confidence, MatchConfidence.AMBIGUOUS)
        self.assertEqual(res.error_category, "AMBIGUOUS_MATCH")
        self.assertIn("Multiple distinct projects matched", res.diagnostic)

    def test_protocol_not_found(self):
        """When no project matches any identifier, returns NOT_FOUND."""
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"items": []}
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        provider = CertikSkynetProvider(api_key="test_key_12345", session=mock_session)
        query = ProtocolQuery(name="NonExistentProtocol999")

        res = provider.fetch_intelligence(query)
        self.assertEqual(res.retrieval_status, RetrievalStatus.NOT_FOUND)
        self.assertEqual(res.match_confidence, MatchConfidence.NONE)
        self.assertIn("No matching project found", res.diagnostic)

    # =========================================================================
    # 5. HTTP Error Handling & Reliability
    # =========================================================================

    def test_authentication_failure_handling(self):
        """HTTP 401/403 must return AUTHENTICATION_FAILED without crashing."""
        mock_resp = MagicMock(status_code=401)
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        provider = CertikSkynetProvider(api_key="invalid_key", session=mock_session)
        query = ProtocolQuery(name="Aave")

        res = provider.fetch_intelligence(query)
        self.assertEqual(res.retrieval_status, RetrievalStatus.AUTHENTICATION_FAILED)
        self.assertEqual(res.error_category, "AUTH_FAILED")
        self.assertIn("authentication failed", res.diagnostic)

    def test_rate_limited_handling(self):
        """HTTP 429 must return RATE_LIMITED."""
        mock_resp = MagicMock(status_code=429)
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        provider = CertikSkynetProvider(api_key="test_key", session=mock_session)
        query = ProtocolQuery(name="Aave")

        res = provider.fetch_intelligence(query)
        self.assertEqual(res.retrieval_status, RetrievalStatus.RATE_LIMITED)
        self.assertEqual(res.error_category, "RATE_LIMITED")

    def test_provider_server_error_handling(self):
        """HTTP 500 must return PROVIDER_ERROR."""
        mock_resp = MagicMock(status_code=500)
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        provider = CertikSkynetProvider(api_key="test_key", session=mock_session)
        query = ProtocolQuery(name="Aave")

        res = provider.fetch_intelligence(query)
        self.assertEqual(res.retrieval_status, RetrievalStatus.PROVIDER_ERROR)
        self.assertEqual(res.error_category, "SERVER_ERROR")

    def test_timeout_handling(self):
        """Request timeout must return UNAVAILABLE with TIMEOUT category."""
        mock_session = MagicMock()
        mock_session.get.side_effect = requests.exceptions.Timeout("Connection timed out")

        provider = CertikSkynetProvider(api_key="test_key", timeout_seconds=5, session=mock_session)
        query = ProtocolQuery(name="Aave")

        res = provider.fetch_intelligence(query)
        self.assertEqual(res.retrieval_status, RetrievalStatus.UNAVAILABLE)
        self.assertEqual(res.error_category, "TIMEOUT")
        self.assertIn("timed out", res.diagnostic)

    def test_malformed_json_response_handling(self):
        """Invalid JSON syntax from upstream must return INVALID_RESPONSE."""
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.side_effect = json.JSONDecodeError("Expecting value", "<html>Error</html>", 0)
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        provider = CertikSkynetProvider(api_key="test_key", session=mock_session)
        query = ProtocolQuery(name="Aave")

        res = provider.fetch_intelligence(query)
        self.assertEqual(res.retrieval_status, RetrievalStatus.INVALID_RESPONSE)
        self.assertEqual(res.error_category, "MALFORMED_JSON")

    # =========================================================================
    # 6. Secret Redaction & JSON Serialization
    # =========================================================================

    def test_secret_redaction_in_diagnostics_and_errors(self):
        """API keys and tokens in exception text must be redacted."""
        raw_error = "Failed to connect with X-Certik-Api-Key: sk-secret-apikey-1234567890abcdef"
        sanitized = sanitize_secret_text(raw_error)
        self.assertNotIn("sk-secret-apikey-1234567890abcdef", sanitized)
        self.assertIn("[REDACTED_SECRET]", sanitized)

    def test_json_report_serialization(self):
        """Result must serialize cleanly to valid JSON and dict."""
        query = ProtocolQuery(name="Test Protocol", defillama_slug="test-protocol")
        res = SecurityIntelligenceResult(
            protocol_name=query.name,
            defillama_slug=query.defillama_slug,
            provider_name="CertiK Skynet",
            provider_project_id="test-proto",
            project_url="https://skynet.certik.com/projects/test-proto",
            retrieval_status=RetrievalStatus.SUCCESS,
            match_confidence=MatchConfidence.EXACT_PROVIDER_ID,
            security_score=SecurityScore(score=88.5, tier="A", rank=10),
            audit_records=[AuditRecord(auditor="CertiK", status="Completed")],
        )

        res_dict = res.to_dict()
        self.assertEqual(res_dict["retrieval_status"], "SUCCESS")
        self.assertEqual(res_dict["security_score"]["score"], 88.5)

        json_str = res.to_json()
        parsed = json.loads(json_str)
        self.assertEqual(parsed["protocol_name"], "Test Protocol")
        self.assertEqual(parsed["match_confidence"], "EXACT_PROVIDER_ID")

    # =========================================================================
    # 7. Regression Invariant: Provider Data CANNOT Bypass Slither Gates
    # =========================================================================

    def test_security_score_does_not_upgrade_repository_eligibility(self):
        """
        CRITICAL ARCHITECTURAL INVARIANT:
        Even if a protocol has a perfect CertiK score of 100/100 and AAA tier,
        if repository resolution status is UNRESOLVED or low confidence,
        Slither eligibility MUST REMAIN INELIGIBLE.
        """
        # 1. Evaluate Slither eligibility for UNRESOLVED repository
        unresolved_status = ResolutionStatus.UNRESOLVED
        mock_ver = SourceVerificationRecord(
            status=SourceVerificationStatus.SOURCE_VERIFIED,
            solidity_source_paths=["Vault.sol"],
        )
        eligibility = evaluate_slither_eligibility(unresolved_status, mock_ver)
        self.assertFalse(eligibility["eligible_for_slither"])

        # 2. Perfect security intelligence result
        perfect_intel = SecurityIntelligenceResult(
            protocol_name="Aave",
            defillama_slug="aave",
            provider_name="CertiK Skynet",
            provider_project_id="aave",
            project_url="https://skynet.certik.com/projects/aave",
            retrieval_status=RetrievalStatus.SUCCESS,
            match_confidence=MatchConfidence.EXACT_PROVIDER_ID,
            security_score=SecurityScore(score=100.0, tier="AAA", rank=1),
        )

        # 3. Slither runner rejection check:
        # A protocol with perfect score but UNRESOLVED repository MUST BE REJECTED!
        fake_protocol_record = {
            "name": "Aave",
            "github_resolution_status": ResolutionStatus.UNRESOLVED.value,
            "security_score": perfect_intel.security_score.score,  # Attempting to supply high score
        }

        res = run_slither_analysis(
            target_dir=os.path.abspath(os.path.dirname(__file__)),
            protocol_record=fake_protocol_record,
            allowed_workspaces=[os.path.abspath(os.path.dirname(__file__))],
            commit_hash="12345678",
        )

        self.assertEqual(res.analysis_status, AnalysisStatus.NOT_ELIGIBLE)
        self.assertIn("Security Gate Rejected", res.reason)
        self.assertIn("UNRESOLVED", res.reason)

    # =========================================================================
    # 8. Phase 3.1: Direct Project Lookup & Verified Endpoints
    # =========================================================================

    def test_direct_project_lookup_by_provider_id(self):
        """Direct lookup endpoint /v1/security-scores/{id} is queried when provider_project_id is provided."""
        project_item = {
            "certiKProjectId": "aave",
            "projectName": "Aave",
            "certiKProjectPage": "https://skynet.certik.com/projects/aave",
            "primaryTokenContractAddress": "eth:0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9",
            "auditedByCertiK": True,
            "securityScore": {"score": 92.5, "tier": "AAA"},
        }
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = project_item

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        provider = CertikSkynetProvider(api_key="valid_key", session=mock_session)
        query = ProtocolQuery(name="Aave", provider_project_id="aave")

        res = provider.fetch_intelligence(query)

        self.assertEqual(res.retrieval_status, RetrievalStatus.SUCCESS)
        self.assertEqual(res.match_confidence, MatchConfidence.EXACT_PROVIDER_ID)
        self.assertEqual(res.provider_project_id, "aave")
        self.assertEqual(res.security_score.score, 92.5)
        # Verify direct endpoint was called with project ID path parameter
        mock_session.get.assert_called_once()
        called_url = mock_session.get.call_args[0][0]
        self.assertTrue(called_url.endswith("/v1/security-scores/aave"))

    def test_direct_project_lookup_by_chain_qualified_contract(self):
        """Direct lookup /v1/security-scores/{chain}:{contract} is used for chain-qualified address."""
        project_item = {
            "certiKProjectId": "uniswap",
            "projectName": "Uniswap",
            "certiKProjectPage": "https://skynet.certik.com/projects/uniswap",
            "primaryTokenContractAddress": "eth:0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
            "auditedByCertiK": True,
            "securityScore": {"score": 90.0, "tier": "AA"},
        }
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = project_item

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        provider = CertikSkynetProvider(api_key="valid_key", session=mock_session)
        query = ProtocolQuery(
            name="Uniswap",
            contract_address="0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
            chain="ethereum",
        )

        res = provider.fetch_intelligence(query)

        self.assertEqual(res.retrieval_status, RetrievalStatus.SUCCESS)
        self.assertEqual(res.match_confidence, MatchConfidence.CHAIN_CONTRACT_MATCH)
        self.assertEqual(res.provider_project_id, "uniswap")
        called_url = mock_session.get.call_args[0][0]
        self.assertTrue(called_url.endswith("/v1/security-scores/eth:0x1f9840a85d5af5bf1d1762f925bdaddc4201f984"))

    def test_direct_lookup_404_preserves_not_found(self):
        """When direct project lookup returns HTTP 404, returns NOT_FOUND with clear diagnostic."""
        mock_resp = MagicMock(status_code=404)
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        provider = CertikSkynetProvider(api_key="valid_key", session=mock_session)
        query = ProtocolQuery(name="unknown-proto", provider_project_id="unknown-proto")

        res = provider.fetch_intelligence(query)

        self.assertEqual(res.retrieval_status, RetrievalStatus.NOT_FOUND)
        self.assertEqual(res.match_confidence, MatchConfidence.NONE)
        self.assertIn("was not found on Skynet (HTTP 404)", res.diagnostic)

    # =========================================================================
    # 9. Phase 3.1: Cross-Chain Address Collision Prevention & Prefix Normalization
    # =========================================================================

    def test_cross_chain_address_collision_strictly_rejected(self):
        """
        CRITICAL SECURITY REQUIREMENT:
        Two identical contract addresses on different chains CANNOT match!
        A query for BSC with an address that CertiK has indexed under Ethereum must NOT match.
        """
        shared_contract_addr = "0x8888888888888888888888888888888888888888"
        # Item indexed on Ethereum
        eth_item = {
            "certiKProjectId": "eth-token",
            "projectName": "Ethereum Token",
            "primaryTokenContractAddress": f"eth:{shared_contract_addr}",
            "securityScore": {"score": 80.0},
        }

        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"items": [eth_item], "page": {"hasMore": False, "total": 1}}

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        provider = CertikSkynetProvider(api_key="valid_key", session=mock_session)

        # Query specifies BSC chain
        query = ProtocolQuery(
            name="Different Project on BSC",
            contract_address=shared_contract_addr,
            chain="bsc",
        )

        res = provider.fetch_intelligence(query)

        # Must NOT match the Ethereum project!
        self.assertEqual(res.retrieval_status, RetrievalStatus.NOT_FOUND)
        self.assertEqual(res.match_confidence, MatchConfidence.NONE)
        self.assertIsNone(res.provider_project_id)

    def test_canonical_chain_prefix_mappings(self):
        """Verifies canonical normalization across EVM chain identifiers."""
        self.assertEqual(normalize_chain_prefix("ethereum"), "eth")
        self.assertEqual(normalize_chain_prefix("eth"), "eth")
        self.assertEqual(normalize_chain_prefix("arbitrum"), "aeth")
        self.assertEqual(normalize_chain_prefix("aeth"), "aeth")
        self.assertEqual(normalize_chain_prefix("bsc"), "bsc")
        self.assertEqual(normalize_chain_prefix("binance"), "bsc")
        self.assertEqual(normalize_chain_prefix("polygon"), "polygon")
        self.assertEqual(normalize_chain_prefix("matic"), "polygon")

    # =========================================================================
    # 10. Phase 3.1: DefiLlama Slug != CertiK Project ID & Documented Fields
    # =========================================================================

    def test_defillama_slug_is_not_assumed_to_be_certik_id(self):
        """
        DefiLlama slug (e.g. 'aave-v3') is NOT treated as an exact CertiK project ID.
        It must not be passed to direct lookup without explicit provider ID or contract address.
        """
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"items": [], "page": {"hasMore": False, "total": 0}}

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        provider = CertikSkynetProvider(api_key="valid_key", session=mock_session)
        query = ProtocolQuery(name="Aave V3", defillama_slug="aave-v3")

        res = provider.fetch_intelligence(query)

        # Must not call /v1/security-scores/aave-v3
        mock_session.get.assert_called_once()
        called_url = mock_session.get.call_args[0][0]
        self.assertTrue(called_url.endswith("/v1/security-scores"))
        self.assertEqual(res.retrieval_status, RetrievalStatus.NOT_FOUND)

    def test_documented_project_website_and_community_trust_parsed(self):
        """Validates documented projectWebsite field for domain matching and communityTrust sub-score."""
        skynet_item = {
            "certiKProjectId": "curve-dex",
            "projectName": "Curve",
            "projectWebsite": "https://curve.fi",
            "certiKProjectPage": "https://skynet.certik.com/projects/curve-dex",
            "securityScore": {
                "score": 89.2,
                "tier": "AA",
                "codeSecurity": 91.0,
                "fundamentalHealth": 85.0,
                "operationalResilience": 88.0,
                "governanceStrength": 90.0,
                "marketStability": 87.0,
                "communityTrust": 93.5,
            },
        }
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"items": [skynet_item], "page": {"hasMore": False, "total": 1}}

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        provider = CertikSkynetProvider(api_key="valid_key", session=mock_session)
        query = ProtocolQuery(name="Curve Finance", website="https://www.curve.fi")

        res = provider.fetch_intelligence(query)

        self.assertEqual(res.retrieval_status, RetrievalStatus.SUCCESS)
        self.assertEqual(res.match_confidence, MatchConfidence.OFFICIAL_DOMAIN_MATCH)
        self.assertEqual(res.security_score.sub_scores.get("communityTrust"), 93.5)

    # =========================================================================
    # 11. Phase 3.1: Safe Bounded Pagination
    # =========================================================================

    def test_bounded_pagination_respects_limits_and_reports_bounds(self):
        """Pagination fetches up to max_pages and reports bounded diagnostic when item is unencountered."""
        page_1 = {
            "items": [{"certiKProjectId": "p1", "projectName": "Other 1"}],
            "page": {"skip": 0, "limit": 10, "total": 100, "hasMore": True},
        }
        page_2 = {
            "items": [{"certiKProjectId": "p2", "projectName": "Other 2"}],
            "page": {"skip": 10, "limit": 10, "total": 100, "hasMore": True},
        }

        mock_resp_1 = MagicMock(status_code=200)
        mock_resp_1.json.return_value = page_1

        mock_resp_2 = MagicMock(status_code=200)
        mock_resp_2.json.return_value = page_2

        mock_session = MagicMock()
        mock_session.get.side_effect = [mock_resp_1, mock_resp_2]

        provider = CertikSkynetProvider(api_key="valid_key", session=mock_session)
        query = ProtocolQuery(name="TargetProtocol")

        res = provider.fetch_intelligence(query, max_pages=2, page_limit=10)

        self.assertEqual(res.retrieval_status, RetrievalStatus.NOT_FOUND)
        self.assertIn("bounded search window of 2 items across 2 page(s)", res.diagnostic)
        self.assertIn("100 total items in catalog", res.diagnostic)
        self.assertEqual(mock_session.get.call_count, 2)

    # =========================================================================
    # 12. Phase 3.1: Live Diagnostic Connectivity Probe
    # =========================================================================

    def test_live_connectivity_probe_diagnostics(self):
        """Validates all potential response outcomes of check_live_api_connectivity."""
        # Case 1: Unconfigured
        unconfigured_res = check_live_api_connectivity(CertikSkynetProvider(api_key=""))
        self.assertEqual(unconfigured_res["status"], "NOT_CONFIGURED")
        self.assertFalse(unconfigured_res["authenticated"])

        # Case 2: 401 Auth Failure
        mock_session = MagicMock()
        mock_session.get.return_value = MagicMock(status_code=401)
        auth_fail_res = check_live_api_connectivity(CertikSkynetProvider(api_key="bad_key", session=mock_session))
        self.assertEqual(auth_fail_res["status"], "AUTHENTICATION_FAILED")
        self.assertEqual(auth_fail_res["http_status"], 401)
        self.assertFalse(auth_fail_res["authenticated"])
        self.assertNotIn("bad_key", auth_fail_res["diagnostic"])

        # Case 3: 429 Rate Limited
        mock_session.get.return_value = MagicMock(status_code=429)
        rate_res = check_live_api_connectivity(CertikSkynetProvider(api_key="key", session=mock_session))
        self.assertEqual(rate_res["status"], "RATE_LIMITED")

        # Case 4: 500 Provider Error
        mock_session.get.return_value = MagicMock(status_code=500)
        err_res = check_live_api_connectivity(CertikSkynetProvider(api_key="key", session=mock_session))
        self.assertEqual(err_res["status"], "PROVIDER_ERROR")

        # Case 5: 200 Success
        mock_session.get.return_value = MagicMock(status_code=200)
        success_res = check_live_api_connectivity(CertikSkynetProvider(api_key="key", session=mock_session))
        self.assertEqual(success_res["status"], "SUCCESS")
        self.assertTrue(success_res["authenticated"])

    @unittest.skipUnless(os.getenv("RUN_LIVE_CERTIK_TEST") == "1", "Live test skipped unless RUN_LIVE_CERTIK_TEST=1")
    def test_opt_in_live_api_call(self):
        """Optional opt-in live test verifying real API response against live CertiK Partner API."""
        provider = CertikSkynetProvider()
        if not provider.is_configured():
            self.skipTest("CERTIK_API_KEY is not configured in environment.")

        result = check_live_api_connectivity(provider)
        self.assertIn("status", result)
        self.assertIn("authenticated", result)


if __name__ == "__main__":
    unittest.main()
