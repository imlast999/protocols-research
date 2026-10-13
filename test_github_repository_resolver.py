"""
Comprehensive unit tests for github_repository_resolver.py.
All tests run offline with zero external network dependencies.
"""

import unittest
from unittest.mock import MagicMock, patch

from github_repository_resolver import (
    CandidateType,
    ResolutionStatus,
    extract_candidates,
    normalize_protocol_record,
    parse_github_url,
    resolve_github_repository,
)


class TestGitHubRepositoryResolver(unittest.TestCase):

    def test_valid_github_repo_urls(self):
        """Standard valid GitHub repository URLs."""
        res = parse_github_url("https://github.com/aave/aave-v3-core")
        self.assertEqual(res.candidate_type, CandidateType.REPOSITORY)
        self.assertEqual(res.owner, "aave")
        self.assertEqual(res.repo, "aave-v3-core")
        self.assertEqual(res.normalized_url, "https://github.com/aave/aave-v3-core")

        # http and www
        res_http = parse_github_url("http://www.github.com/uniswap/v3-core")
        self.assertEqual(res_http.candidate_type, CandidateType.REPOSITORY)
        self.assertEqual(res_http.normalized_url, "https://github.com/uniswap/v3-core")

        # bare github.com
        res_bare = parse_github_url("github.com/compound-finance/compound-protocol")
        self.assertEqual(res_bare.candidate_type, CandidateType.REPOSITORY)
        self.assertEqual(res_bare.normalized_url, "https://github.com/compound-finance/compound-protocol")

        # owner/repo string without scheme
        res_short = parse_github_url("makerdao/dss")
        self.assertEqual(res_short.candidate_type, CandidateType.REPOSITORY)
        self.assertEqual(res_short.normalized_url, "https://github.com/makerdao/dss")

    def test_urls_ending_in_dot_git(self):
        """.git suffixes should be stripped cleanly."""
        res = parse_github_url("https://github.com/lidofinance/lido-dao.git")
        self.assertEqual(res.candidate_type, CandidateType.REPOSITORY)
        self.assertEqual(res.repo, "lido-dao")
        self.assertEqual(res.normalized_url, "https://github.com/lidofinance/lido-dao")

        # git SSH format
        res_ssh = parse_github_url("git@github.com:curvefi/curve-contract.git")
        self.assertEqual(res_ssh.candidate_type, CandidateType.REPOSITORY)
        self.assertEqual(res_ssh.owner, "curvefi")
        self.assertEqual(res_ssh.repo, "curve-contract")
        self.assertEqual(res_ssh.normalized_url, "https://github.com/curvefi/curve-contract")

    def test_trailing_slashes(self):
        """Trailing slashes must be stripped cleanly."""
        res = parse_github_url("https://github.com/balancer/balancer-v2-monorepo/")
        self.assertEqual(res.candidate_type, CandidateType.REPOSITORY)
        self.assertEqual(res.repo, "balancer-v2-monorepo")
        self.assertEqual(res.normalized_url, "https://github.com/balancer/balancer-v2-monorepo")

    def test_subpath_tree_and_blob(self):
        """Tree/blob subpaths should be classified and normalized to the base repository."""
        res_tree = parse_github_url("https://github.com/Se7en-Seas/boring-vault/tree/main/audit")
        self.assertEqual(res_tree.candidate_type, CandidateType.TREE_OR_BLOB)
        self.assertEqual(res_tree.normalized_url, "https://github.com/Se7en-Seas/boring-vault")

        res_blob = parse_github_url("https://github.com/maple-labs/maple-core/blob/master/contracts/MapleToken.sol#L1-L10")
        self.assertEqual(res_blob.candidate_type, CandidateType.TREE_OR_BLOB)
        self.assertEqual(res_blob.normalized_url, "https://github.com/maple-labs/maple-core")

    def test_organization_and_user_profile_urls(self):
        """Single-segment paths represent orgs/users, not repositories."""
        res_org_url = parse_github_url("https://github.com/lidofinance")
        self.assertEqual(res_org_url.candidate_type, CandidateType.ORGANIZATION_OR_USER)
        self.assertEqual(res_org_url.owner, "lidofinance")
        self.assertEqual(res_org_url.normalized_url, "https://github.com/lidofinance")

        res_bare_handle = parse_github_url("lidofinance")
        self.assertEqual(res_bare_handle.candidate_type, CandidateType.ORGANIZATION_OR_USER)
        self.assertEqual(res_bare_handle.owner, "lidofinance")

    def test_issues_and_pull_requests(self):
        """Issue and PR URLs must be identified as such."""
        res_issue = parse_github_url("https://github.com/aave/aave-v3-core/issues/42")
        self.assertEqual(res_issue.candidate_type, CandidateType.ISSUE_OR_PR)
        self.assertEqual(res_issue.owner, "aave")
        self.assertEqual(res_issue.repo, "aave-v3-core")

        res_pr = parse_github_url("https://github.com/aave/aave-v3-core/pull/105")
        self.assertEqual(res_pr.candidate_type, CandidateType.ISSUE_OR_PR)

    def test_invalid_urls_and_schemes(self):
        """Malformed URLs, reserved paths, and non-http schemes must be rejected."""
        self.assertEqual(parse_github_url("").candidate_type, CandidateType.INVALID)
        self.assertEqual(parse_github_url(None).candidate_type, CandidateType.INVALID)
        self.assertEqual(parse_github_url("javascript:alert(1)").candidate_type, CandidateType.INVALID)
        self.assertEqual(parse_github_url("https://github.com").candidate_type, CandidateType.INVALID)
        self.assertEqual(parse_github_url("https://github.com/").candidate_type, CandidateType.INVALID)
        self.assertEqual(parse_github_url("https://github.com/explore").candidate_type, CandidateType.INVALID)
        self.assertEqual(parse_github_url("https://github.com/features/actions").candidate_type, CandidateType.INVALID)

    def test_non_github_domains(self):
        """Non-GitHub domains must be classified as NON_GITHUB and rejected."""
        self.assertEqual(parse_github_url("https://gitlab.com/owner/repo").candidate_type, CandidateType.NON_GITHUB)
        self.assertEqual(parse_github_url("https://bitbucket.org/owner/repo").candidate_type, CandidateType.NON_GITHUB)
        self.assertEqual(parse_github_url("https://notgithub.com/owner/repo").candidate_type, CandidateType.NON_GITHUB)

    def test_missing_github_metadata(self):
        """Protocols without any GitHub metadata resolve as UNRESOLVED."""
        protocol = {
            "name": "Ghost Protocol",
            "slug": "ghost-protocol",
            "tvl": 1000000,
            "chain": "Ethereum",
        }
        status, selected_repo, evidence = resolve_github_repository(protocol)
        self.assertEqual(status, ResolutionStatus.UNRESOLVED)
        self.assertIsNone(selected_repo)
        self.assertEqual(evidence["confidence"], "NONE")

    def test_organization_only_metadata(self):
        """Protocols declaring only an organization handle resolve as ORGANIZATION_ONLY without inventing repo."""
        protocol = {
            "name": "Lido",
            "slug": "lido",
            "tvl": 24000000000,
            "github": ["lidofinance"],
        }
        status, selected_repo, evidence = resolve_github_repository(protocol)
        self.assertEqual(status, ResolutionStatus.ORGANIZATION_ONLY)
        self.assertIsNone(selected_repo)
        self.assertIn("https://github.com/lidofinance", evidence["declared_organizations"])

    def test_single_confident_repository_corroborated(self):
        """Single repository matching protocol's declared organization resolves with CONFIDENT status."""
        protocol = {
            "name": "Maple",
            "slug": "maple",
            "tvl": 50000000,
            "github": ["maple-labs"],
            "audit_links": ["https://github.com/maple-labs/maple-core#audit-reports"],
        }
        status, selected_repo, evidence = resolve_github_repository(protocol)
        self.assertEqual(status, ResolutionStatus.RESOLVED_CONFIDENT)
        self.assertEqual(selected_repo, "https://github.com/maple-labs/maple-core")
        self.assertEqual(evidence["confidence"], "HIGH")
        self.assertTrue(evidence["matched_against_declared_org"])

    def test_auditor_publications_filtered(self):
        """Audit links pointing to third-party auditor repositories must not be attributed to the protocol."""
        protocol = {
            "name": "Binance staked ETH",
            "slug": "binance-staked-eth",
            "tvl": 9000000000,
            "audit_links": [
                "https://github.com/peckshield/publications/blob/master/audit_reports/PeckShield-Audit-Report-wBETH-v1.0.pdf"
            ],
        }
        status, selected_repo, evidence = resolve_github_repository(protocol)
        self.assertEqual(status, ResolutionStatus.UNRESOLVED)
        self.assertIsNone(selected_repo)
        self.assertIn("https://github.com/peckshield/publications", evidence["auditor_repos_ignored"])

    def test_multiple_conflicting_repository_candidates(self):
        """Conflicting repositories from different orgs without tie-breaker resolve as AMBIGUOUS."""
        protocol = {
            "name": "Conflicted DEX",
            "slug": "conflicted-dex",
            "tvl": 20000000,
            "audit_links": [
                "https://github.com/dex-team-a/core-v1",
                "https://github.com/dex-team-b/core-v2",
            ],
        }
        status, selected_repo, evidence = resolve_github_repository(protocol)
        self.assertEqual(status, ResolutionStatus.AMBIGUOUS)
        self.assertIsNone(selected_repo)
        self.assertEqual(evidence["confidence"], "NONE")
        self.assertEqual(len(evidence["candidates"]), 2)

    def test_disambiguation_via_declared_org(self):
        """When multiple repositories are found, the one matching declared organization is selected."""
        protocol = {
            "name": "Smart Protocol",
            "slug": "smart-proto",
            "tvl": 10000000,
            "github": ["smartprotocol-official"],
            "audit_links": [
                "https://github.com/smartprotocol-official/smart-core",
                "https://github.com/random-community/smart-frontend",
            ],
        }
        status, selected_repo, evidence = resolve_github_repository(protocol)
        self.assertEqual(status, ResolutionStatus.RESOLVED_CONFIDENT)
        self.assertEqual(selected_repo, "https://github.com/smartprotocol-official/smart-core")
        self.assertEqual(evidence["confidence"], "HIGH")

    def test_malformed_defillama_records(self):
        """Non-dictionary or empty records return MALFORMED_RECORD safely without crashing."""
        self.assertEqual(resolve_github_repository(None)[0], ResolutionStatus.MALFORMED_RECORD)
        self.assertEqual(resolve_github_repository("invalid_string")[0], ResolutionStatus.MALFORMED_RECORD)
        self.assertEqual(resolve_github_repository({})[0], ResolutionStatus.MALFORMED_RECORD)

    def test_normalize_protocol_record_schema(self):
        """Verify normalized protocol record structure preserves all original fields and adds new metadata."""
        raw_proto = {
            "name": "Maple",
            "slug": "maple",
            "tvl": 12345678.9,
            "category": "Lending",
            "chain": "Ethereum",
            "chains": ["Ethereum", "Arbitrum"],
            "address": "0x643C4E15d7d62Ad0aBeC4a9BD4b001aA3Ef52d66",
            "github": ["maple-labs"],
            "audit_links": ["https://github.com/maple-labs/maple-core"],
        }
        normalized = normalize_protocol_record(raw_proto)

        # Existing consumer contract check
        self.assertEqual(normalized["name"], "Maple")
        self.assertEqual(normalized["slug"], "maple")
        self.assertEqual(normalized["tvl"], 12345678.9)
        self.assertEqual(normalized["category"], "Lending")
        self.assertIn("ethereum", normalized["chains"])
        self.assertEqual(normalized["address"], "0x643C4E15d7d62Ad0aBeC4a9BD4b001aA3Ef52d66")

        # New normalized fields check
        self.assertEqual(normalized["selected_github_url"], "https://github.com/maple-labs/maple-core")
        self.assertEqual(normalized["github_resolution_status"], "RESOLVED_CONFIDENT")
        self.assertIn("ethereum", normalized["contract_addresses_by_chain"])
        self.assertEqual(len(normalized["declared_repository_candidates"]), 2)
        self.assertEqual(len(normalized["audit_links"]), 1)
        self.assertIn("timestamp", normalized)

    def test_defillama_client_caching(self):
        """Verify client caches responses within TTL to prevent duplicate network calls."""
        from github_repository_resolver import DefiLlamaClient

        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"name": "ProtocolA", "slug": "proto-a", "tvl": 1000}]
        mock_session.get.return_value = mock_resp

        client = DefiLlamaClient(cache_ttl_seconds=60, session=mock_session)
        res1 = client.fetch_all_protocols()
        res2 = client.fetch_all_protocols()

        self.assertEqual(len(res1), 1)
        self.assertEqual(len(res2), 1)
        # Should only have made 1 network call due to cache
        self.assertEqual(mock_session.get.call_count, 1)

    @patch("time.sleep", return_value=None)
    def test_defillama_client_rate_limit_retry(self, mock_sleep):
        """Verify client handles HTTP 429 rate limits with backoff retry."""
        from github_repository_resolver import DefiLlamaClient

        mock_session = MagicMock()
        resp_429 = MagicMock()
        resp_429.status_code = 429

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = [{"name": "ProtocolAfterRetry", "slug": "proto-retry"}]

        # First call fails with 429, second call succeeds with 200
        mock_session.get.side_effect = [resp_429, resp_200]

        client = DefiLlamaClient(session=mock_session)
        res = client.fetch_all_protocols(max_retries=3, backoff_factor=0.01)

        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["slug"], "proto-retry")
        self.assertEqual(mock_session.get.call_count, 2)
        self.assertTrue(mock_sleep.called)

    @patch("time.sleep", return_value=None)
    def test_defillama_client_timeout_retry(self, mock_sleep):
        """Verify client retries upon requests.exceptions.Timeout."""
        import requests
        from github_repository_resolver import DefiLlamaClient

        mock_session = MagicMock()
        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {"name": "LidoDetail", "slug": "lido"}

        mock_session.get.side_effect = [
            requests.exceptions.Timeout("Connection timed out"),
            resp_200,
        ]

        client = DefiLlamaClient(session=mock_session)
        res = client.fetch_protocol("lido", max_retries=2, backoff_factor=0.01)

        self.assertIsNotNone(res)
        self.assertEqual(res["slug"], "lido")
        self.assertEqual(mock_session.get.call_count, 2)

    def test_slug_discrepancy_regression(self):
        """
        Regression test for Phase 1.1:
        When DefiLlama detail endpoint returns integer id (e.g. 587) and null slug,
        the resolver must NOT overwrite slug with '587', preserving distinct fields:
        requested_slug, defillama_slug, defillama_id, protocol_name.
        """
        raw_detail_response = {
            "id": 587,
            "name": "Maple",
            "slug": None,
            "address": "0x643c4e15d7d62ad0abec4a9bd4b001aa3ef52d66",
            "tvl": [{"date": 1670976000, "totalLiquidityUSD": 2863007570.0}],
            "audit_links": ["https://github.com/maple-labs/maple-core#audit-reports"],
        }

        normalized = normalize_protocol_record(raw_detail_response, requested_slug="maple")

        # Distinct identity assertions
        self.assertEqual(normalized["requested_slug"], "maple")
        self.assertIsNone(normalized["defillama_slug"])
        self.assertEqual(normalized["defillama_id"], "587")
        self.assertEqual(normalized["protocol_name"], "Maple")

        # Backward compatibility assertions (slug must NOT be '587')
        self.assertEqual(normalized["slug"], "maple")
        self.assertEqual(normalized["name"], "Maple")

        # Uncorroborated audit link resolves as LOW confidence
        self.assertEqual(normalized["github_resolution_status"], "RESOLVED_LOW_CONFIDENCE")
        self.assertEqual(normalized["resolution_evidence"]["confidence"], "LOW")
        self.assertIn("audit links without official organization corroboration", normalized["resolution_evidence"]["reason"])

        # Scanner handoff contract
        handoff = normalized["scanner_handoff"]
        self.assertEqual(handoff["status"], "REQUIRES_CORROBORATION")
        self.assertFalse(handoff["eligible_for_automated_analysis"])
        self.assertTrue(handoff["requires_manual_corroboration"])
        self.assertEqual(handoff["downstream_slither_action"], "AWAIT_MANUAL_CORROBORATION")

    def test_uncorroborated_audit_link_low_confidence(self):
        """Audit links without official organization corroboration must never be RESOLVED_CONFIDENT."""
        protocol = {
            "name": "Test Protocol",
            "slug": "test-proto",
            "audit_links": ["https://github.com/test-org/contracts-v1"],
        }
        status, repo, evidence = resolve_github_repository(protocol)
        self.assertEqual(status, ResolutionStatus.RESOLVED_LOW_CONFIDENCE)
        self.assertEqual(repo, "https://github.com/test-org/contracts-v1")
        self.assertEqual(evidence["confidence"], "LOW")
        self.assertFalse(evidence["matched_against_declared_org"])

    def test_audit_repository_rejected_as_contract_source(self):
        """Audit and docs repositories must never be selected as smart contract source."""
        # 1. Protocol with org and only an audit repo in audit_links (like Lido)
        protocol_lido = {
            "name": "Lido",
            "slug": "lido",
            "github": ["lidofinance"],
            "audit_links": ["https://github.com/lidofinance/audits"],
        }
        status, repo, evidence = resolve_github_repository(protocol_lido)
        self.assertEqual(status, ResolutionStatus.ORGANIZATION_ONLY)
        self.assertIsNone(repo)
        self.assertIn("https://github.com/lidofinance/audits", evidence.get("auditor_repos_ignored", []))

        # 2. Protocol where sole repo link is a known audit firm repo
        protocol_auditor = {
            "name": "Test Protocol",
            "slug": "test-protocol",
            "audit_links": ["https://github.com/trailofbits/publications/blob/master/reviews/test.pdf"],
        }
        status, repo, evidence = resolve_github_repository(protocol_auditor)
        self.assertEqual(status, ResolutionStatus.UNRESOLVED)
        self.assertIsNone(repo)

    def test_org_match_without_corroborated_source_repo(self):
        """An organization match alone is insufficient if the repository is not corroborated as source code."""
        protocol = {
            "name": "Example DAO",
            "slug": "example-dao",
            "github": ["exampledao"],
            "audit_links": ["https://github.com/exampledao/docs"],
        }
        status, repo, evidence = resolve_github_repository(protocol)
        self.assertEqual(status, ResolutionStatus.ORGANIZATION_ONLY)
        self.assertIsNone(repo)

    def test_multiple_plausible_repositories_remain_ambiguous(self):
        """Multiple conflicting repository candidates without authoritative tie-breaker must remain AMBIGUOUS."""
        protocol = {
            "name": "Ambiguous DeFi",
            "slug": "ambiguous-defi",
            "audit_links": [
                "https://github.com/team-alpha/core-contracts",
                "https://github.com/team-beta/core-contracts",
            ],
        }
        status, repo, evidence = resolve_github_repository(protocol)
        self.assertEqual(status, ResolutionStatus.AMBIGUOUS)
        self.assertIsNone(repo)
        self.assertEqual(len(evidence.get("candidates", [])), 2)

    def test_missing_metadata_remains_unresolved(self):
        """Missing or empty metadata must remain strictly UNRESOLVED."""
        # Empty fields with protocol identity
        protocol_empty_meta = {"name": "Test Protocol", "slug": "test-protocol"}
        status, repo, evidence = resolve_github_repository(protocol_empty_meta)
        self.assertEqual(status, ResolutionStatus.UNRESOLVED)
        self.assertIsNone(repo)

        # Null fields (like Aave V3 or LayerZero V2 raw records)
        protocol_null = {
            "name": "LayerZero V2",
            "slug": "layerzero-v2",
            "github": None,
            "audit_links": None,
        }
        status, repo, evidence = resolve_github_repository(protocol_null)
        self.assertEqual(status, ResolutionStatus.UNRESOLVED)
        self.assertIsNone(repo)

        # Completely empty record lacking name/slug returns MALFORMED_RECORD
        status_empty, repo_empty, _ = resolve_github_repository({})
        self.assertEqual(status_empty, ResolutionStatus.MALFORMED_RECORD)
        self.assertIsNone(repo_empty)

    def test_scanner_handoff_rules(self):
        """Test safe 3-stage handoff boundaries for all possible resolution statuses."""
        from github_repository_resolver import (
            SourceVerificationRecord,
            SourceVerificationStatus,
            get_scanner_handoff,
        )

        # 1. RESOLVED_CONFIDENT (Stage A passed, but Stage B source verification NOT run)
        h_conf = get_scanner_handoff(ResolutionStatus.RESOLVED_CONFIDENT, "https://github.com/aave/aave-v3-core")
        self.assertEqual(h_conf["status"], "ELIGIBLE_FOR_SOURCE_VERIFICATION")
        self.assertTrue(h_conf["eligible_for_source_verification"])
        # Must be FALSE at Stage A to prevent downstream scanners from running prematurely
        self.assertFalse(h_conf["eligible_for_automated_analysis"])
        self.assertFalse(h_conf["eligible_for_slither"])
        self.assertFalse(h_conf["requires_manual_corroboration"])
        self.assertEqual(h_conf["selected_repository"], "https://github.com/aave/aave-v3-core")
        self.assertEqual(h_conf["downstream_slither_action"], "PROCEED_TO_SOURCE_VERIFICATION")

        # 2. RESOLVED_CONFIDENT + Stage B SOURCE_VERIFIED -> Eligible for Slither
        ver_ok = SourceVerificationRecord(
            status=SourceVerificationStatus.SOURCE_VERIFIED,
            solidity_source_paths=["contracts/AaveV3Pool.sol"],
            build_framework="Foundry",
            commit_hash="abc123def456",
            default_branch="main",
            reason="Verified 1 Solidity file(s)",
        )
        h_verified = get_scanner_handoff(ResolutionStatus.RESOLVED_CONFIDENT, "https://github.com/aave/aave-v3-core", verification=ver_ok)
        self.assertTrue(h_verified["eligible_for_slither"])
        self.assertTrue(h_verified["eligible_for_automated_analysis"])
        self.assertEqual(h_verified["pipeline_stages"]["C_slither_eligibility"]["status"], "ELIGIBLE")

        # 3. RESOLVED_LOW_CONFIDENCE
        h_low = get_scanner_handoff(ResolutionStatus.RESOLVED_LOW_CONFIDENCE, "https://github.com/maple-labs/maple-core")
        self.assertEqual(h_low["status"], "REQUIRES_CORROBORATION")
        self.assertFalse(h_low["eligible_for_source_verification"])
        self.assertFalse(h_low["eligible_for_automated_analysis"])
        self.assertFalse(h_low["eligible_for_slither"])
        self.assertTrue(h_low["requires_manual_corroboration"])
        self.assertEqual(h_low["selected_repository"], "https://github.com/maple-labs/maple-core")
        self.assertEqual(h_low["downstream_slither_action"], "AWAIT_MANUAL_CORROBORATION")

        # 4. ORGANIZATION_ONLY
        h_org = get_scanner_handoff(ResolutionStatus.ORGANIZATION_ONLY, None)
        self.assertEqual(h_org["status"], "NO_REPOSITORY_SELECTED")
        self.assertFalse(h_org["eligible_for_source_verification"])
        self.assertFalse(h_org["eligible_for_automated_analysis"])
        self.assertFalse(h_org["eligible_for_slither"])
        self.assertIsNone(h_org["selected_repository"])
        self.assertEqual(h_org["downstream_slither_action"], "SKIP_AUTOMATED_ANALYSIS")

        # 5. AMBIGUOUS
        h_amb = get_scanner_handoff(ResolutionStatus.AMBIGUOUS, None)
        self.assertEqual(h_amb["status"], "NO_REPOSITORY_SELECTED")
        self.assertFalse(h_amb["eligible_for_automated_analysis"])
        self.assertFalse(h_amb["eligible_for_slither"])
        self.assertIsNone(h_amb["selected_repository"])

        # 6. UNRESOLVED
        h_unres = get_scanner_handoff(ResolutionStatus.UNRESOLVED, None)
        self.assertEqual(h_unres["status"], "NO_REPOSITORY_SELECTED")
        self.assertFalse(h_unres["eligible_for_automated_analysis"])
        self.assertFalse(h_unres["eligible_for_slither"])
        self.assertIsNone(h_unres["selected_repository"])

    def test_source_verification_outcomes(self):
        """Test safe static file-tree inspection across all 4 machine-readable outcomes."""
        import os
        import tempfile
        from github_repository_resolver import (
            SourceVerificationStatus,
            verify_repository_source,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            # Outcome 1: SOURCE_VERIFIED (Foundry)
            foundry_dir = os.path.join(tmp_dir, "foundry_repo")
            os.makedirs(os.path.join(foundry_dir, "src"), exist_ok=True)
            with open(os.path.join(foundry_dir, "foundry.toml"), "w", encoding="utf-8") as f:
                f.write("[profile.default]\n")
            with open(os.path.join(foundry_dir, "src", "Token.sol"), "w", encoding="utf-8") as f:
                f.write("// SPDX-License-Identifier: MIT\npragma solidity ^0.8.20;\ncontract Token {}\n")

            rec_ver = verify_repository_source(foundry_dir, default_branch="main", commit_hash="1122334455667788")
            self.assertEqual(rec_ver.status, SourceVerificationStatus.SOURCE_VERIFIED)
            self.assertEqual(rec_ver.build_framework, "Foundry")
            self.assertEqual(len(rec_ver.solidity_source_paths), 1)
            self.assertEqual(rec_ver.commit_hash, "1122334455667788")
            self.assertIn("not guaranteed to correspond to on-chain deployed bytecode", rec_ver.branch_deployment_limitation)

            # Outcome 2: BUILD_CONFIGURATION_NOT_FOUND (Solidity files but no build config)
            no_config_dir = os.path.join(tmp_dir, "no_config_repo")
            os.makedirs(os.path.join(no_config_dir, "contracts"), exist_ok=True)
            with open(os.path.join(no_config_dir, "contracts", "Test.sol"), "w", encoding="utf-8") as f:
                f.write("// test\n")

            rec_noconfig = verify_repository_source(no_config_dir)
            self.assertEqual(rec_noconfig.status, SourceVerificationStatus.BUILD_CONFIGURATION_NOT_FOUND)
            self.assertIsNone(rec_noconfig.build_framework)
            self.assertEqual(len(rec_noconfig.solidity_source_paths), 1)

            # Outcome 3: SOURCE_NOT_FOUND (Repo exists, maybe build config, but zero .sol files)
            no_source_dir = os.path.join(tmp_dir, "no_source_repo")
            os.makedirs(no_source_dir, exist_ok=True)
            with open(os.path.join(no_source_dir, "foundry.toml"), "w", encoding="utf-8") as f:
                f.write("")

            rec_nosource = verify_repository_source(no_source_dir)
            self.assertEqual(rec_nosource.status, SourceVerificationStatus.SOURCE_NOT_FOUND)
            self.assertEqual(len(rec_nosource.solidity_source_paths), 0)

            # Outcome 4: VERIFICATION_FAILED (Non-existent directory)
            non_existent = os.path.join(tmp_dir, "does_not_exist")
            rec_fail = verify_repository_source(non_existent)
            self.assertEqual(rec_fail.status, SourceVerificationStatus.VERIFICATION_FAILED)

    def test_evaluate_slither_eligibility_scenarios(self):
        """Test Slither eligibility gating logic."""
        from github_repository_resolver import (
            ResolutionStatus,
            SlitherEligibilityStatus,
            SourceVerificationRecord,
            SourceVerificationStatus,
            evaluate_slither_eligibility,
        )

        # 1. Blocked when resolution is ORGANIZATION_ONLY or UNRESOLVED
        unres_eval = evaluate_slither_eligibility(ResolutionStatus.UNRESOLVED, SourceVerificationRecord())
        self.assertFalse(unres_eval["eligible_for_slither"])
        self.assertEqual(unres_eval["status"], SlitherEligibilityStatus.BLOCKED_NO_REPOSITORY.value)

        # 2. Blocked when resolution is LOW CONFIDENCE
        low_eval = evaluate_slither_eligibility(ResolutionStatus.RESOLVED_LOW_CONFIDENCE, SourceVerificationRecord())
        self.assertFalse(low_eval["eligible_for_slither"])
        self.assertEqual(low_eval["status"], SlitherEligibilityStatus.BLOCKED_NEEDS_CORROBORATION.value)

        # 3. Blocked when resolution is CONFIDENT but verification NOT RUN
        not_run_eval = evaluate_slither_eligibility(ResolutionStatus.RESOLVED_CONFIDENT, SourceVerificationRecord())
        self.assertFalse(not_run_eval["eligible_for_slither"])
        self.assertEqual(not_run_eval["status"], SlitherEligibilityStatus.BLOCKED_SOURCE_UNVERIFIED.value)

        # 4. Blocked when source verification failed / missing build configuration
        missing_build_rec = SourceVerificationRecord(
            status=SourceVerificationStatus.BUILD_CONFIGURATION_NOT_FOUND,
            solidity_source_paths=["test.sol"],
            reason="No config",
        )
        build_fail_eval = evaluate_slither_eligibility(ResolutionStatus.RESOLVED_CONFIDENT, missing_build_rec)
        self.assertFalse(build_fail_eval["eligible_for_slither"])
        self.assertEqual(build_fail_eval["status"], SlitherEligibilityStatus.BLOCKED_SOURCE_UNVERIFIED.value)

        # 5. ELIGIBLE when resolution is CONFIDENT and verification is SOURCE_VERIFIED
        verified_rec = SourceVerificationRecord(
            status=SourceVerificationStatus.SOURCE_VERIFIED,
            solidity_source_paths=["src/Core.sol"],
            build_framework="Foundry",
            commit_hash="deadbeef1234",
            reason="Verified",
        )
        ok_eval = evaluate_slither_eligibility(ResolutionStatus.RESOLVED_CONFIDENT, verified_rec)
        self.assertTrue(ok_eval["eligible_for_slither"])
        self.assertEqual(ok_eval["status"], SlitherEligibilityStatus.ELIGIBLE.value)

    @patch("requests.get")
    def test_pipeline_integration_market_and_notrack(self, mock_get):
        """Integration test verifying market_intelligence and notrack_script with mocked API responses."""
        from market_intelligence import get_high_value_targets
        from notrack_script import get_vulnerable_targets

        mock_payload = [
            {
                "id": "1",
                "name": "Lido",
                "slug": "lido",
                "address": "0x5A98FcBEA516Cf06857215779Fd812CA3beF1B32",
                "chain": "Ethereum",
                "chains": ["Ethereum"],
                "tvl": 20000000000.0,
                "category": "Liquid Staking",
                "github": ["lidofinance"],
                "audit_links": ["https://github.com/lidofinance/audits"],
            },
            {
                "id": "2",
                "name": "Binance CEX",
                "slug": "binance-cex",
                "address": "0x28C6c06298d514Db089934071355E5743bf21d60",
                "chain": "Ethereum",
                "chains": ["Ethereum"],
                "tvl": 10000000000.0,
                "category": "CEX",
            },
            {
                "id": "3",
                "name": "Aave V3",
                "slug": "aave-v3",
                "address": "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
                "chain": "Ethereum",
                "chains": ["Ethereum"],
                "tvl": 15000000000.0,
                "category": "Lending",
                "github": ["https://github.com/aave/aave-v3-core"],
            },
        ]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_payload
        mock_get.return_value = mock_resp

        # 1. Market intelligence integration
        market_targets = get_high_value_targets(min_tvl=100000, supported_chains=["ethereum"])
        self.assertEqual(len(market_targets), 3)

        # Check Lido: In Phase 1.2, lidofinance/audits is an audit repo, NOT contract source!
        # It must resolve as ORGANIZATION_ONLY with selected_github_url = None
        lido_target = next(t for t in market_targets if t["name"] == "Lido")
        self.assertIsNone(lido_target["selected_github_url"])
        self.assertEqual(lido_target["github_resolution_status"], "ORGANIZATION_ONLY")
        self.assertEqual(lido_target["scanner_handoff"]["status"], "NO_REPOSITORY_SELECTED")
        self.assertIn("https://github.com/lidofinance/audits", lido_target["resolution_evidence"]["auditor_repos_ignored"])

        # Check Binance CEX (unresolved repo must not crash pipeline)
        binance_target = next(t for t in market_targets if t["name"] == "Binance CEX")
        self.assertIsNone(binance_target["selected_github_url"])
        self.assertEqual(binance_target["github_resolution_status"], "UNRESOLVED")
        self.assertEqual(binance_target["scanner_handoff"]["status"], "NO_REPOSITORY_SELECTED")

        # 2. NoTrack script integration (filters out CEX)
        notrack_targets = get_vulnerable_targets(min_tvl=100000, target_chain="ethereum")
        self.assertEqual(len(notrack_targets), 2)
        target_names = [t["name"] for t in notrack_targets]
        self.assertIn("Lido", target_names)
        self.assertIn("Aave V3", target_names)
        self.assertNotIn("Binance CEX", target_names)

        # Aave V3 has direct contract repository
        aave_target = next(t for t in notrack_targets if t["name"] == "Aave V3")
        self.assertEqual(aave_target["selected_github_url"], "https://github.com/aave/aave-v3-core")
        self.assertEqual(aave_target["github_resolution_status"], "RESOLVED_CONFIDENT")
        self.assertEqual(aave_target["scanner_handoff"]["status"], "ELIGIBLE_FOR_SOURCE_VERIFICATION")


if __name__ == "__main__":
    unittest.main()

