"""
test_notrack_script.py
Deterministic, offline unit tests for Specialized Sector Audit Pipeline (notrack_script.py).

Validates:
1. Chain resolution from forced arguments, address prefixes, chainTvls maps, and chain lists.
2. Handling of unsupported chains in protocol metadata.
3. Category-based DeFi filtering (Lending, Derivatives, etc.) excluding non-target sectors.
4. TVL threshold gating for complex protocol targeting.
5. Error resilience during DefiLlama querying.
6. Execution and audit dispatch loop with mocked contract scanners.
"""

import unittest
from pathlib import Path
import sys
from unittest.mock import MagicMock, patch
import requests

_src_dir = str(Path(__file__).resolve().parent.parent / "src")
_pkg_dir = str(Path(__file__).resolve().parent.parent / "src" / "defi_research")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

from defi_research.notrack_script import (
    TARGET_CATEGORIES,
    audit_vulnerable_targets,
    get_vulnerable_targets,
    resolve_protocol_chain,
)


class TestNoTrackScript(unittest.TestCase):

    def test_resolve_protocol_chain_forced(self):
        """Forced chain parameter takes precedence and normalizes aliases."""
        proto = {"address": "0x123", "chains": ["Arbitrum"]}
        self.assertEqual(resolve_protocol_chain(proto, forced_chain="ETH"), "ethereum")
        self.assertEqual(resolve_protocol_chain(proto, forced_chain="polygon"), "polygon")

    def test_resolve_protocol_chain_from_address_prefix(self):
        """Prefix in contract address (e.g. 'arbitrum:0x...') determines chain."""
        proto = {"address": "arbitrum:0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"}
        self.assertEqual(resolve_protocol_chain(proto), "arbitrum")

    def test_resolve_protocol_chain_from_chain_tvls(self):
        """Protocol selects supported chain with highest TVL allocation."""
        proto = {
            "address": "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
            "chainTvls": {
                "Solana": 100_000_000,   # Unsupported chain
                "Arbitrum": 5_000_000,
                "Ethereum": 20_000_000,  # Highest among supported
            },
        }
        self.assertEqual(resolve_protocol_chain(proto), "ethereum")

    def test_resolve_protocol_chain_from_chains_list(self):
        """Falls back to general chains array if chainTvls is absent."""
        proto = {
            "address": "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
            "chains": ["Solana", "Polygon"],
        }
        self.assertEqual(resolve_protocol_chain(proto), "polygon")

    def test_resolve_protocol_chain_unsupported(self):
        """Returns None if protocol has no supported EVM chains."""
        proto = {
            "address": "solana_address",
            "chains": ["Solana", "Bitcoin"],
        }
        self.assertIsNone(resolve_protocol_chain(proto))

    @patch("defi_research.notrack_script.requests.get")
    def test_get_vulnerable_targets_category_and_tvl_filtering(self, mock_get):
        """Filters protocols by target categories and minimum TVL."""
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "name": "Target Lending",
                "slug": "target-lending",
                "category": "Lending",
                "tvl": 50_000_000,
                "address": "ethereum:0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
            },
            {
                "name": "Small Lending",
                "slug": "small-lending",
                "category": "Lending",
                "tvl": 1_000_000,  # Below min_tvl 10M
                "address": "ethereum:0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
            },
            {
                "name": "CEX Protocol",
                "slug": "binance-cex",
                "category": "CEX",  # Excluded category
                "tvl": 500_000_000,
                "address": "ethereum:0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
            },
        ]
        mock_get.return_value = mock_response

        targets = get_vulnerable_targets(min_tvl=10_000_000)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["name"], "Target Lending")
        self.assertEqual(targets[0]["category"], "Lending")

    @patch("defi_research.notrack_script.requests.get")
    def test_get_vulnerable_targets_network_error(self, mock_get):
        """Network exception returns empty list."""
        mock_get.side_effect = requests.exceptions.RequestException("Network error")
        targets = get_vulnerable_targets()
        self.assertEqual(targets, [])

    @patch("defi_research.notrack_script.get_vulnerable_targets")
    @patch("defi_research.notrack_script.ContractScanner")
    def test_audit_vulnerable_targets_execution(self, mock_scanner_cls, mock_get_targets):
        """Audits retrieved targets and aggregates findings."""
        mock_get_targets.return_value = [
            {
                "name": "Mock Protocol",
                "slug": "mock-proto",
                "category": "DEX",
                "tvl": 25_000_000,
                "main_address": "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
                "chain": "ethereum",
            }
        ]

        mock_scanner = MagicMock()
        mock_scanner.run_report.return_value = {
            "critical_count": 0,
            "findings": [],
        }
        mock_scanner_cls.return_value = mock_scanner

        # Should execute without errors
        with patch("defi_research.notrack_script.time.sleep"):
            summary = audit_vulnerable_targets(min_tvl=20_000_000, limit=1)
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["name"], "Mock Protocol")
        mock_scanner.run_report.assert_called_once()


if __name__ == "__main__":
    unittest.main()
