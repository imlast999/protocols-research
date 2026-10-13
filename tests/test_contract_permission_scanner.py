"""
test_contract_permission_scanner.py
Deterministic, offline unit tests for ContractScanner (contract_permission_scanner.py).

Validates:
1. Supported and unsupported blockchain networks.
2. Graceful RPC provider disconnection handling.
3. Test parameter generation for EVM types.
4. Regex extraction of access control modifiers from Solidity source code.
5. Identification of critical functions (withdraw, admin changes) without guards.
6. Guarded function recognition (onlyOwner, onlyAdmin, etc.).
7. Verified contract data retrieval from explorer endpoints and fallback behavior.
8. Native token pricing via CoinGecko and TVL approximation.
9. Comprehensive contract scan execution and reporting schema.
"""

import json
import unittest
from pathlib import Path
import sys
from unittest.mock import MagicMock, patch

_src_dir = str(Path(__file__).resolve().parent.parent / "src")
_pkg_dir = str(Path(__file__).resolve().parent.parent / "src" / "defi_research")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

from defi_research.contract_permission_scanner import ContractScanner


class TestContractPermissionScanner(unittest.TestCase):

    def setUp(self):
        self.dummy_addr = "0x1111111111111111111111111111111111111111"

    @patch("defi_research.contract_permission_scanner.Web3")
    def test_init_supported_chains(self, mock_web3_cls):
        """Scanner initializes successfully for all supported chains."""
        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = True
        mock_web3_cls.return_value = mock_w3

        for chain in ["ethereum", "arbitrum", "polygon", "bsc"]:
            scanner = ContractScanner(chain_key=chain)
            self.assertEqual(scanner.chain_key, chain)
            self.assertIsNotNone(scanner.chain_cfg)

    def test_init_unsupported_chain_raises(self):
        """Unsupported chain name must raise ValueError."""
        with self.assertRaises(ValueError) as ctx:
            ContractScanner(chain_key="solana")
        self.assertIn("no soportada", str(ctx.exception))

    @patch("defi_research.contract_permission_scanner.Web3")
    def test_rpc_disconnection_handled_gracefully(self, mock_web3_cls):
        """RPC connection failure issues a warning but does not raise an exception."""
        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = False
        mock_web3_cls.return_value = mock_w3

        scanner = ContractScanner(chain_key="ethereum")
        self.assertFalse(scanner.w3.is_connected())

    @patch("defi_research.contract_permission_scanner.Web3")
    def test_generate_test_param_types(self, mock_web3_cls):
        """Verify deterministic generation of dummy test arguments."""
        scanner = ContractScanner(chain_key="ethereum")
        caller = "0x0000000000000000000000000000000000000001"

        self.assertEqual(scanner._generate_test_param("address", caller), caller)
        self.assertEqual(scanner._generate_test_param("uint256", caller), 0)
        self.assertEqual(scanner._generate_test_param("int128", caller), 0)
        self.assertEqual(scanner._generate_test_param("bool", caller), False)
        self.assertEqual(
            scanner._generate_test_param("bytes32", caller),
            "0x0000000000000000000000000000000000000000000000000000000000000000",
        )
        self.assertEqual(scanner._generate_test_param("bytes", caller), "0x")
        self.assertEqual(scanner._generate_test_param("string", caller), "audit_test")
        self.assertEqual(scanner._generate_test_param("custom_tuple", caller), "0")

    @patch("defi_research.contract_permission_scanner.Web3")
    def test_check_source_modifiers_extraction(self, mock_web3_cls):
        """Source modifier regex extracts recognized access modifiers."""
        scanner = ContractScanner(chain_key="ethereum")

        sol_code = """
        function withdrawFunds(uint256 amount) public onlyOwner whenNotPaused {
            balances[msg.sender] -= amount;
        }

        function setFee(uint256 fee) external onlyRole(ADMIN_ROLE) {
            feeRate = fee;
        }

        function publicDeposit() external payable {
            balances[msg.sender] += msg.value;
        }
        """

        mods_withdraw = scanner._check_source_modifiers("withdrawFunds", sol_code)
        self.assertIn("onlyOwner", mods_withdraw)

        mods_fee = scanner._check_source_modifiers("setFee", sol_code)
        self.assertIn("onlyRole", mods_fee)

        mods_deposit = scanner._check_source_modifiers("publicDeposit", sol_code)
        self.assertEqual(mods_deposit, [])

        # Empty source code
        self.assertEqual(scanner._check_source_modifiers("withdrawFunds", ""), [])

    @patch("defi_research.contract_permission_scanner.Web3")
    def test_analyze_permissions_flags_unguarded_withdrawal(self, mock_web3_cls):
        """Unguarded withdraw function is flagged as CRITICAL."""
        scanner = ContractScanner(chain_key="ethereum")

        abi = [
            {
                "type": "function",
                "name": "withdraw",
                "stateMutability": "nonpayable",
                "inputs": [{"name": "amount", "type": "uint256"}],
            },
            {
                "type": "function",
                "name": "viewBalance",
                "stateMutability": "view",
                "inputs": [],
            },
        ]
        source_code = """
        function withdraw(uint256 amount) external {
            msg.sender.call{value: amount}("");
        }
        """

        findings = scanner.analyze_permissions(abi, source_code)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding["name"], "withdraw")
        self.assertTrue(finding["is_critical"])
        self.assertFalse(finding["has_guard"])
        self.assertIn("CRÍTICO", finding["risk_category"])

    @patch("defi_research.contract_permission_scanner.Web3")
    def test_analyze_permissions_recognizes_guarded_withdrawal(self, mock_web3_cls):
        """Withdrawal guarded by onlyOwner is flagged as INFO, not CRITICAL."""
        scanner = ContractScanner(chain_key="ethereum")

        abi = [
            {
                "type": "function",
                "name": "withdraw",
                "stateMutability": "nonpayable",
                "inputs": [{"name": "amount", "type": "uint256"}],
            }
        ]
        source_code = """
        function withdraw(uint256 amount) external onlyOwner {
            msg.sender.call{value: amount}("");
        }
        """

        findings = scanner.analyze_permissions(abi, source_code)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding["name"], "withdraw")
        self.assertFalse(finding["is_critical"])
        self.assertTrue(finding["has_guard"])
        self.assertIn("INFO", finding["risk_category"])

    @patch("defi_research.contract_permission_scanner.Web3")
    def test_analyze_permissions_flags_unguarded_admin_function(self, mock_web3_cls):
        """Unguarded admin/parameter change is flagged as HIGH."""
        scanner = ContractScanner(chain_key="ethereum")

        abi = [
            {
                "type": "function",
                "name": "setAdmin",
                "stateMutability": "nonpayable",
                "inputs": [{"name": "newAdmin", "type": "address"}],
            }
        ]
        source_code = """
        function setAdmin(address newAdmin) external {
            admin = newAdmin;
        }
        """

        findings = scanner.analyze_permissions(abi, source_code)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding["name"], "setAdmin")
        self.assertTrue(finding["is_critical"])
        self.assertFalse(finding["has_guard"])
        self.assertIn("ALTO", finding["risk_category"])

    @patch("defi_research.contract_permission_scanner.Web3")
    @patch("defi_research.contract_permission_scanner.requests.get")
    def test_get_verified_contract_data_success(self, mock_get, mock_web3_cls):
        """Successfully parses verified contract ABI and source code from explorer."""
        scanner = ContractScanner(chain_key="ethereum", api_key="dummy_api_key")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "status": "1",
            "message": "OK",
            "result": [
                {
                    "ContractName": "Vault",
                    "ABI": json.dumps([{"type": "function", "name": "deposit"}]),
                    "SourceCode": "contract Vault {}",
                }
            ],
        }
        mock_get.return_value = mock_resp

        abi, src, name = scanner.get_verified_contract_data(self.dummy_addr)
        self.assertEqual(name, "Vault")
        self.assertEqual(len(abi), 1)
        self.assertEqual(src, "contract Vault {}")

    @patch("defi_research.contract_permission_scanner.Web3")
    @patch("defi_research.contract_permission_scanner.requests.get")
    def test_get_verified_contract_data_fallback_and_failure(self, mock_get, mock_web3_cls):
        """Primary endpoint failure triggers fallback or raises RuntimeError."""
        scanner = ContractScanner(chain_key="ethereum")

        # Simulate primary failure and fallback failure
        mock_resp_fail = MagicMock()
        mock_resp_fail.json.return_value = {"status": "0", "message": "NOTOK", "result": "Contract not found"}
        mock_get.return_value = mock_resp_fail

        with self.assertRaises(RuntimeError) as ctx:
            scanner.get_verified_contract_data(self.dummy_addr)
        self.assertIn("No se pudo recuperar el ABI/fuente", str(ctx.exception))

    @patch("defi_research.contract_permission_scanner.Web3")
    @patch("defi_research.contract_permission_scanner.requests.get")
    def test_get_token_price_usd_and_failure(self, mock_get, mock_web3_cls):
        """CoinGecko token price returns float or 0.0 on error."""
        scanner = ContractScanner(chain_key="ethereum")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ethereum": {"usd": 3200.50}}
        mock_get.return_value = mock_resp

        price = scanner.get_token_price_usd()
        self.assertEqual(price, 3200.50)

        # Exception during fetch returns 0.0
        mock_get.side_effect = Exception("Network timeout")
        price_err = scanner.get_token_price_usd()
        self.assertEqual(price_err, 0.0)

    def test_calculate_approx_tvl(self):
        """TVL calculation multiplies native balance by token price."""
        scanner = ContractScanner(chain_key="ethereum")
        scanner.w3.eth.get_balance = MagicMock(return_value=2 * 10**18)
        scanner.get_token_price_usd = MagicMock(return_value=3000.0)

        tvl = scanner.calculate_approx_tvl(self.dummy_addr)
        self.assertEqual(tvl["native_balance"], 2.0)
        self.assertEqual(tvl["native_symbol"], "ETH")
        self.assertEqual(tvl["native_price_usd"], 3000.0)
        self.assertEqual(tvl["approx_tvl_usd"], 6000.0)

    def test_scan_contract_and_run_report(self):
        """End-to-end scan_contract and run_report produce consistent report dictionaries."""
        scanner = ContractScanner(chain_key="ethereum")
        scanner.w3.eth.get_balance = MagicMock(return_value=10**18)
        scanner.get_verified_contract_data = MagicMock(
            return_value=(
                [
                    {
                        "type": "function",
                        "name": "withdraw",
                        "stateMutability": "nonpayable",
                        "inputs": [{"name": "amt", "type": "uint256"}],
                    }
                ],
                "function withdraw(uint256 amt) external {}",
                "TestVault",
            )
        )
        scanner.get_token_price_usd = MagicMock(return_value=2500.0)

        res = scanner.run_report(self.dummy_addr)
        self.assertEqual(res["contract_name"], "TestVault")
        self.assertEqual(res["chain_key"], "ethereum")
        self.assertEqual(res["critical_count"], 1)
        self.assertEqual(len(res["findings"]), 1)


if __name__ == "__main__":
    unittest.main()
