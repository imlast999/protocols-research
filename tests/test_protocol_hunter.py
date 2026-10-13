"""
Automated Tests for ProtocolHunter Continuous Discovery Service.
Validates continuous background cycles, candidate filtering, deduplication,
API error resilience, state transitions, and safe cancellation.
"""

from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from defi_research.discovery.discovery_store import DiscoveryStore
from defi_research.discovery.protocol_hunter import (
    HunterConfig,
    HunterMetrics,
    HunterState,
    ProtocolHunter,
)
from defi_research.github_repository_resolver import ResolutionStatus


class TestProtocolHunter(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "discovery_database.json"
        self.store = DiscoveryStore(db_path=self.db_path)

        self.mock_protocols = [
            {
                "id": "1",
                "name": "Lending Protocol Alpha",
                "slug": "alpha-lending",
                "chain": "Ethereum",
                "chains": ["Ethereum", "Arbitrum"],
                "tvl": 10_000_000.0,
                "category": "Lending",
                "address": "0x1111111111111111111111111111111111111111",
            },
            {
                "id": "2",
                "name": "Dex Beta",
                "slug": "beta-dex",
                "chain": "Arbitrum",
                "chains": ["Arbitrum"],
                "tvl": 250_000.0,
                "category": "Dexes",
                "address": "0x2222222222222222222222222222222222222222",
            },
            {
                "id": "3",
                "name": "Micro Yield Gamma",
                "slug": "gamma-yield",
                "chain": "Polygon",
                "chains": ["Polygon"],
                "tvl": 10_000.0,  # Below $50k TVL
                "category": "Yield",
                "address": None,
            },
        ]

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_hunter_config_defaults(self):
        """Default Hunter configuration must have safe, non-zero defaults."""
        cfg = HunterConfig()
        self.assertEqual(cfg.duration_hours, 1.0)
        self.assertEqual(cfg.cycle_interval_seconds, 60)
        self.assertEqual(cfg.min_tvl, 50_000.0)
        self.assertEqual(cfg.max_candidates_per_cycle, 25)
        self.assertTrue(cfg.resume_session)

    def test_02_single_discovery_cycle_filtering(self):
        """A single cycle must filter candidates by min_tvl and category, then save to store."""
        mock_client = MagicMock()
        mock_client.get_protocols.return_value = self.mock_protocols

        cfg = HunterConfig(
            min_tvl=50_000.0,
            categories=["Lending"],
        )
        hunter = ProtocolHunter(config=cfg, store=self.store, client=mock_client)

        matched_count = hunter.run_discovery_cycle()
        self.assertEqual(matched_count, 1)
        self.assertEqual(self.store.total_count, 1)

        rec = self.store.get_record("alpha-lending")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.name, "Lending Protocol Alpha")
        self.assertEqual(rec.tvl, 10_000_000.0)

    def test_03_deduplication_across_repeated_cycles(self):
        """Running multiple cycles must update existing candidates rather than creating duplicate records."""
        mock_client = MagicMock()
        mock_client.get_protocols.return_value = self.mock_protocols

        cfg = HunterConfig(min_tvl=50_000.0)
        hunter = ProtocolHunter(config=cfg, store=self.store, client=mock_client)

        # Cycle 1
        hunter.run_discovery_cycle()
        self.assertEqual(self.store.total_count, 2)  # Alpha & Beta pass TVL > 50k
        self.assertEqual(hunter.metrics.unique_candidates_added, 2)

        # Cycle 2 with updated TVL
        updated_protocols = [dict(p) for p in self.mock_protocols]
        updated_protocols[0]["tvl"] = 12_000_000.0
        mock_client.get_protocols.return_value = updated_protocols

        hunter.run_discovery_cycle()
        self.assertEqual(self.store.total_count, 2)  # No duplicates!
        self.assertEqual(hunter.metrics.unique_candidates_added, 2)

        rec = self.store.get_record("alpha-lending")
        self.assertEqual(rec.tvl, 12_000_000.0)

    def test_04_api_error_resilience(self):
        """DefiLlama API network failures must be caught cleanly without crashing the session."""
        mock_client = MagicMock()
        mock_client.get_protocols.side_effect = RuntimeError("503 Service Unavailable")

        hunter = ProtocolHunter(store=self.store, client=mock_client)
        matched_count = hunter.run_discovery_cycle()

        self.assertEqual(matched_count, 0)
        self.assertEqual(len(hunter.metrics.recent_errors), 1)
        self.assertIn("503 Service Unavailable", hunter.metrics.recent_errors[0])

    def test_05_state_transitions_and_stop_cancellation(self):
        """Hunter must transition between IDLE, RUNNING, and safely stop when signaled."""
        mock_client = MagicMock()
        mock_client.get_protocols.return_value = self.mock_protocols

        cfg = HunterConfig(
            duration_hours=0.005,  # Very short
            cycle_interval_seconds=1,
            min_tvl=50_000.0,
        )
        hunter = ProtocolHunter(config=cfg, store=self.store, client=mock_client)
        self.assertEqual(hunter.state, HunterState.IDLE)

        # Request stop immediately after starting
        def stop_after_delay():
            time.sleep(0.05)
            hunter.stop()

        import threading
        t = threading.Thread(target=stop_after_delay)
        t.start()

        hunter.start()
        t.join()

        self.assertIn(hunter.state, (HunterState.IDLE, HunterState.COMPLETED))

    def test_06_pause_and_resume(self):
        """Hunter pause and resume flags must toggle state properly."""
        hunter = ProtocolHunter(store=self.store)
        hunter._set_state(HunterState.RUNNING)

        hunter.pause()
        self.assertEqual(hunter.state, HunterState.PAUSED)

        hunter.resume()
        self.assertEqual(hunter.state, HunterState.RUNNING)

    @patch("defi_research.discovery.protocol_hunter.resolve_github_repo_from_slug")
    def test_07_deep_investigation_repo_resolution(self, mock_resolve):
        """Deep investigation mode must resolve GitHub repository and persist findings."""
        mock_resolve.return_value = {
            "repository_url": "https://github.com/alpha/alpha-core",
            "repository_resolution_status": ResolutionStatus.RESOLVED_CONFIDENT.value,
            "repository_confidence": "HIGH",
            "repository_resolution_reason": "Exact slug match",
        }
        mock_client = MagicMock()
        mock_client.get_protocols.return_value = [self.mock_protocols[0]]

        cfg = HunterConfig(
            min_tvl=50_000.0,
            deep_investigation=True,
        )
        hunter = ProtocolHunter(config=cfg, store=self.store, client=mock_client)
        hunter.run_discovery_cycle()

        rec = self.store.get_record("alpha-lending")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.repository_url, "https://github.com/alpha/alpha-core")
        self.assertEqual(rec.repository_resolution_status, "RESOLVED_CONFIDENT")


if __name__ == "__main__":
    unittest.main()
