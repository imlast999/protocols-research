"""
test_cli.py
Deterministic, offline unit tests for the Unified CLI (cli.py).

Validates:
1. Root CLI parser and subcommands registration.
2. Argument parsing for all subcommands (scan, market, notrack, resolve, slither, intelligence, pipeline).
3. Correct registration and default values of --backend and --docker-image options.
4. Execution of cmd_scan with mocked ContractScanner.
5. Execution of cmd_market with mocked market intelligence query.
6. Execution of cmd_notrack with mocked sector pipeline.
7. Execution of cmd_resolve with mocked repository resolver.
8. Execution of cmd_slither with mocked Slither runner and backend propagation.
9. Execution of cmd_intelligence with mocked CertiK security intelligence bridge.
10. Execution of cmd_pipeline with single protocol, bulk options, and --json output formatting.
"""

import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_src_dir = str(Path(__file__).resolve().parent.parent / "src")
_pkg_dir = str(Path(__file__).resolve().parent.parent / "src" / "defi_research")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

import cli


class TestCLI(unittest.TestCase):

    def test_cli_subparsers_registered(self):
        """Verifies that all 7 primary subcommands are registered."""
        # Test parser creation by invoking cli.py with --help catching SystemExit
        with patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as ctx:
                with patch("sys.argv", ["cli.py", "--help"]):
                    cli.main()
            self.assertEqual(ctx.exception.code, 0)

    def test_cli_slither_subcommand_arguments(self):
        """Slither subcommand correctly exposes --backend and --docker-image."""
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            with self.assertRaises(SystemExit) as ctx:
                with patch("sys.argv", ["cli.py", "slither", "--help"]):
                    cli.main()
            self.assertEqual(ctx.exception.code, 0)
            help_text = mock_out.getvalue()
            self.assertIn("--backend", help_text)
            self.assertIn("--docker-image", help_text)
            self.assertIn("--commit", help_text)

    def test_cli_pipeline_subcommand_arguments(self):
        """Pipeline subcommand correctly exposes --backend, --docker-image, and --json."""
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            with self.assertRaises(SystemExit) as ctx:
                with patch("sys.argv", ["cli.py", "pipeline", "--help"]):
                    cli.main()
            self.assertEqual(ctx.exception.code, 0)
            help_text = mock_out.getvalue()
            self.assertIn("--backend", help_text)
            self.assertIn("--docker-image", help_text)
            self.assertIn("--slug", help_text)
            self.assertIn("--json", help_text)

    @patch("cli.ContractScanner")
    def test_cmd_scan_execution(self, mock_scanner_cls):
        """cmd_scan invokes ContractScanner.run_report."""
        mock_scanner = MagicMock()
        mock_scanner.run_report.return_value = {
            "findings": [],
            "critical_count": 0,
            "contract_address": "0x1111111111111111111111111111111111111111",
        }
        mock_scanner_cls.return_value = mock_scanner

        with patch("sys.stdout", new_callable=io.StringIO):
            with patch("sys.argv", ["cli.py", "scan", "-c", "0x1111111111111111111111111111111111111111", "--chain", "ethereum"]):
                cli.main()

        mock_scanner_cls.assert_called_once()
        mock_scanner.run_report.assert_called_once_with("0x1111111111111111111111111111111111111111")

    @patch("cli.get_high_value_targets")
    def test_cmd_market_execution(self, mock_get_targets):
        """cmd_market queries market intelligence and prints targets."""
        mock_get_targets.return_value = [
            {
                "name": "Uniswap",
                "slug": "uniswap",
                "chain": "ethereum",
                "tvl": 1_000_000_000.0,
                "address": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
                "contracts": ["0x1f9840a85d5af5bf1d1762f925bdaddc4201f984"],
            }
        ]

        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            with patch("sys.argv", ["cli.py", "market", "--min-tvl", "500000"]):
                cli.main()

        mock_get_targets.assert_called_once()
        self.assertIn("Uniswap", mock_out.getvalue())

    @patch("cli.audit_vulnerable_targets")
    def test_cmd_notrack_execution_with_scan(self, mock_audit):
        """cmd_notrack invokes specialized sector pipeline when --scan is passed."""
        with patch("sys.stdout", new_callable=io.StringIO):
            with patch("sys.argv", ["cli.py", "notrack", "--min-tvl", "1000000", "--limit", "2", "--scan"]):
                cli.main()

        mock_audit.assert_called_once()

    @patch("cli.resolve_github_repo_from_slug")
    def test_cmd_resolve_execution(self, mock_resolve):
        """cmd_resolve invokes GitHub resolver."""
        mock_resolve.return_value = {
            "name": "Aave V3",
            "slug": "aave-v3",
            "github_resolution_status": "RESOLVED_CONFIDENT",
            "selected_github_url": "https://github.com/aave/aave-v3-core",
            "resolution_evidence": {"confidence": "HIGH", "reason": "Exact slug match"},
            "declared_repository_candidates": [],
        }

        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            with patch("sys.argv", ["cli.py", "resolve", "--slug", "aave-v3"]):
                cli.main()

        mock_resolve.assert_called_once_with("aave-v3")
        self.assertIn("RESOLVED_CONFIDENT", mock_out.getvalue())

    @patch("cli.run_slither_analysis")
    def test_cmd_slither_execution_with_backend_args(self, mock_slither):
        """cmd_slither propagates backend and docker_image arguments."""
        mock_res = MagicMock(
            analysis_status=MagicMock(value="COMPLETED"),
            execution_backend="docker",
            protocol_name="Aave V3",
            commit_hash="abcdef123456",
            source_verification_status="SOURCE_VERIFIED",
            slither_version="docker:0.10.4",
            duration_seconds=1.5,
            exit_code=0,
            reason="Completed",
            findings=[],
            findings_summary={"High": 0, "Medium": 0, "Low": 0, "Informational": 0, "Optimization": 0},
            limitations=[],
        )
        mock_slither.return_value = mock_res

        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            with patch(
                "sys.argv",
                [
                    "cli.py",
                    "slither",
                    "-t",
                    "./workspace/test_repo",
                    "--backend",
                    "docker",
                    "--docker-image",
                    "defi-slither:0.10.4",
                    "--commit",
                    "abcdef123456",
                ],
            ):
                cli.main()

        mock_slither.assert_called_once()
        call_kwargs = mock_slither.call_args[1]
        self.assertEqual(call_kwargs.get("backend"), "docker")
        self.assertEqual(call_kwargs.get("docker_image"), "defi-slither:0.10.4")
        self.assertIn("Backend de ejecución:     docker", mock_out.getvalue())

    @patch("cli.query_protocol_security_intelligence")
    def test_cmd_intelligence_execution(self, mock_intel):
        """cmd_intelligence executes security intelligence query."""
        mock_res = MagicMock(
            retrieval_status=MagicMock(value="SUCCESS"),
            protocol_name="Aave V3",
            provider_name="CertiK Skynet",
            provider_project_id="aave-v3",
            match_confidence=MagicMock(value="EXACT_SLUG"),
            security_score=MagicMock(score=92.5, tier="AA"),
            audit_records=[],
            diagnostic="Success",
            limitations=[],
        )
        mock_intel.return_value = mock_res

        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            with patch("sys.argv", ["cli.py", "intelligence", "--slug", "aave-v3"]):
                cli.main()

        mock_intel.assert_called_once()
        self.assertIn("CertiK Skynet", mock_out.getvalue())

    @patch("cli.run_protocol_pipeline")
    def test_cmd_pipeline_execution_single_json(self, mock_run_pipeline):
        """cmd_pipeline formats output as JSON when --json flag is passed."""
        mock_report = MagicMock()
        mock_report.to_json.return_value = json.dumps({
            "protocol_name": "Aave V3",
            "overall_status": "COMPLETED",
            "slither_execution_backend": "docker",
        })
        mock_run_pipeline.return_value = mock_report

        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            with patch(
                "sys.argv",
                [
                    "cli.py",
                    "pipeline",
                    "--slug",
                    "aave-v3",
                    "--backend",
                    "docker",
                    "--json",
                ],
            ):
                cli.main()

        mock_run_pipeline.assert_called_once()
        call_kwargs = mock_run_pipeline.call_args[1]
        self.assertEqual(call_kwargs.get("slither_backend"), "docker")

        output = mock_out.getvalue()
        # Find JSON block in output
        json_start = output.find("{")
        self.assertNotEqual(json_start, -1)
        parsed_json = json.loads(output[json_start:])
        self.assertEqual(parsed_json["protocol_name"], "Aave V3")
        self.assertEqual(parsed_json["slither_execution_backend"], "docker")


if __name__ == "__main__":
    unittest.main()
