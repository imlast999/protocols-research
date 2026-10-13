"""
test_market_intelligence.py
Deterministic, offline unit tests for DeFi Market Intelligence (market_intelligence.py).

Validates:
1. EVM contract address cleaning, validation, and checksum normalization.
2. Handling of addresses with network prefixes (e.g. 'ethereum:0x...').
3. Rejection of invalid, non-hex, or malformed contract addresses.
4. Chain alias mapping for standard EVM networks.
5. DefiLlama protocol discovery and TVL threshold filtering.
6. Chain filtering behavior.
7. Resilience against API timeouts, network failures, and malformed JSON payloads.
8. Protocol normalization preserving base records and metadata.
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

from defi_research.market_intelligence import (
    CHAIN_ALIAS_MAP,
    clean_contract_address,
    get_high_value_targets,
)


class TestMarketIntelligence(unittest.TestCase):

    def test_clean_contract_address_valid(self):
        """Valid EVM address returns standard checksum format."""
        raw = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"
        expected = "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640"
        self.assertEqual(clean_contract_address(raw), expected)

    def test_clean_contract_address_with_chain_prefix(self):
        """Prefixes like 'ethereum:0x...' or 'arbitrum:0x...' are stripped."""
        raw_eth = "ethereum:0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"
        raw_arb = "arbitrum:0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"
        expected = "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640"
        self.assertEqual(clean_contract_address(raw_eth), expected)
        self.assertEqual(clean_contract_address(raw_arb), expected)

    def test_clean_contract_address_with_whitespace(self):
        """Surrounding whitespace is trimmed."""
        raw = "  0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640 \n"
        expected = "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640"
        self.assertEqual(clean_contract_address(raw), expected)

    def test_clean_contract_address_invalid(self):
        """Invalid inputs return None."""
        self.assertIsNone(clean_contract_address(None))
        self.assertIsNone(clean_contract_address(12345))  # non-string
        self.assertIsNone(clean_contract_address(""))
        self.assertIsNone(clean_contract_address("not_an_address"))
        self.assertIsNone(clean_contract_address("0x123"))  # too short
        self.assertIsNone(clean_contract_address("0x" + "g" * 40))  # non-hex chars

    def test_chain_alias_map_mappings(self):
        """Validates canonical mappings for supported EVM chains."""
        self.assertEqual(CHAIN_ALIAS_MAP["eth"], "ethereum")
        self.assertEqual(CHAIN_ALIAS_MAP["arb"], "arbitrum")
        self.assertEqual(CHAIN_ALIAS_MAP["matic"], "polygon")
        self.assertEqual(CHAIN_ALIAS_MAP["bnb"], "bsc")
        self.assertEqual(CHAIN_ALIAS_MAP["binance smart chain"], "bsc")

    @patch("defi_research.market_intelligence.requests.get")
    def test_get_high_value_targets_filtering(self, mock_get):
        """Filters protocols by TVL threshold and chain compatibility."""
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "name": "High TVL Protocol",
                "slug": "high-tvl",
                "chain": "Ethereum",
                "chains": ["Ethereum", "Arbitrum"],
                "tvl": 500_000.0,
                "address": "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
                "category": "Lending",
            },
            {
                "name": "Low TVL Protocol",
                "slug": "low-tvl",
                "chain": "Ethereum",
                "chains": ["Ethereum"],
                "tvl": 5_000.0,  # Below default min_tvl 50,000
                "address": "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
                "category": "DEX",
            },
            {
                "name": "Non-EVM Protocol",
                "slug": "solana-proto",
                "chain": "Solana",
                "chains": ["Solana"],
                "tvl": 1_000_000.0,
                "address": "not_an_evm_addr",
                "category": "DEX",
            },
        ]
        mock_get.return_value = mock_response

        # Query with min_tvl = 50,000 and supported_chains = ['ethereum']
        targets = get_high_value_targets(min_tvl=50_000.0, supported_chains=["ethereum"])
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["slug"], "high-tvl")
        self.assertEqual(targets[0]["chain"], "ethereum")
        self.assertEqual(targets[0]["tvl"], 500_000.0)
        self.assertEqual(len(targets[0]["contracts"]), 1)

    @patch("defi_research.market_intelligence.requests.get")
    def test_get_high_value_targets_network_timeout(self, mock_get):
        """HTTP timeout returns empty list without raising exception."""
        mock_get.side_effect = requests.exceptions.Timeout("Connection timed out")
        targets = get_high_value_targets(timeout=5)
        self.assertEqual(targets, [])

    @patch("defi_research.market_intelligence.requests.get")
    def test_get_high_value_targets_http_error(self, mock_get):
        """HTTP error returns empty list without raising exception."""
        mock_get.side_effect = requests.exceptions.RequestException("503 Service Unavailable")
        targets = get_high_value_targets()
        self.assertEqual(targets, [])

    @patch("defi_research.market_intelligence.requests.get")
    def test_get_high_value_targets_malformed_json(self, mock_get):
        """Malformed JSON payload returns empty list without crashing."""
        mock_resp = MagicMock()
        mock_resp.json.side_effect = ValueError("Invalid JSON")
        mock_get.return_value = mock_resp
        targets = get_high_value_targets()
        self.assertEqual(targets, [])

    @patch("defi_research.market_intelligence.requests.get")
    def test_get_high_value_targets_non_list_response(self, mock_get):
        """Non-list response payload returns empty list."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": "rate limit exceeded"}
        mock_get.return_value = mock_resp
        targets = get_high_value_targets()
        self.assertEqual(targets, [])


if __name__ == "__main__":
    unittest.main()
