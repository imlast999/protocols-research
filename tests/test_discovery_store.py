"""
Automated Tests for DiscoveryStore and DiscoveredProtocolRecord.
Validates versioned JSON persistence, atomic disk writes, corruption recovery,
data deduplication, incremental merging, and search filtering.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from defi_research.discovery.discovery_store import (
    CURRENT_SCHEMA_VERSION,
    DiscoveredProtocolRecord,
    DiscoveryStore,
    generate_stable_protocol_id,
    get_default_data_dir,
)


class TestDiscoveryStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "discovery_database.json"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_stable_protocol_id_generation(self):
        """Stable IDs must prioritize slug, then chain:address, then normalized name."""
        # 1. Slug priority
        id_slug = generate_stable_protocol_id(slug="Uniswap-V3", address="0x1111", name="Uniswap")
        self.assertEqual(id_slug, "uniswap-v3")

        # 2. Address fallback
        id_addr = generate_stable_protocol_id(slug="", address="0x1234567890123456789012345678901234567890", chain="Ethereum")
        self.assertEqual(id_addr, "ethereum:0x1234567890123456789012345678901234567890")

        # 3. Name fallback
        id_name = generate_stable_protocol_id(slug=None, address=None, name="Aave Protocol V3!")
        self.assertEqual(id_name, "aave-protocol-v3")

    def test_02_store_initialization_empty(self):
        """Newly created store on non-existent path must initialize with schema version 1.0.0 and 0 records."""
        store = DiscoveryStore(db_path=self.db_path)
        self.assertEqual(store.total_count, 0)
        self.assertEqual(len(store.get_all_records()), 0)
        meta = store.get_metadata()
        self.assertEqual(meta.get("schema_version"), CURRENT_SCHEMA_VERSION)

    def test_03_atomic_write_and_backup_creation(self):
        """Saving records must atomically replace the file and create a .bak backup on subsequent saves."""
        store = DiscoveryStore(db_path=self.db_path)
        record_data = {
            "name": "Aave V3",
            "slug": "aave-v3",
            "chain": "ethereum",
            "tvl": 15_000_000.0,
            "category": "Lending",
        }
        rec, is_new = store.upsert_record(record_data)
        self.assertTrue(is_new)
        self.assertEqual(store.total_count, 1)

        # First save
        success = store.save()
        self.assertTrue(success)
        self.assertTrue(self.db_path.exists())

        # Modify and save again to verify .bak creation
        store.upsert_record({"slug": "uniswap-v3", "name": "Uniswap V3", "tvl": 50_000_000.0})
        store.save()
        self.assertTrue(store.bak_path.exists())

        # Inspect backup contents (should have 1 record)
        with open(store.bak_path, "r", encoding="utf-8") as f:
            bak_json = json.load(f)
        self.assertEqual(bak_json.get("total_records"), 1)

        # Inspect primary file (should have 2 records)
        with open(self.db_path, "r", encoding="utf-8") as f:
            prim_json = json.load(f)
        self.assertEqual(prim_json.get("total_records"), 2)

    def test_04_recovery_from_corrupted_primary_file(self):
        """If primary JSON is corrupted or truncated, DiscoveryStore must recover from .bak."""
        store = DiscoveryStore(db_path=self.db_path)
        store.upsert_record({"slug": "curve-dex", "name": "Curve", "tvl": 20_000_000.0})
        store.save()

        # Save second time so .bak has valid Curve record
        store.upsert_record({"slug": "balancer", "name": "Balancer", "tvl": 10_000_000.0})
        store.save()
        self.assertTrue(store.bak_path.exists())

        # Corrupt the primary JSON file
        with open(self.db_path, "w", encoding="utf-8") as f:
            f.write("{ invalid json corrupted ...")

        # Reload store
        recovered_store = DiscoveryStore(db_path=self.db_path)
        self.assertEqual(recovered_store.total_count, 1)
        self.assertIsNotNone(recovered_store.get_record("curve-dex"))

    def test_05_corrupted_file_preservation_when_no_backup(self):
        """When primary JSON is corrupt and no valid .bak exists, corrupt file must be preserved safely."""
        with open(self.db_path, "w", encoding="utf-8") as f:
            f.write("<<<CORRUPT BINARY DATA>>>")

        store = DiscoveryStore(db_path=self.db_path)
        self.assertEqual(store.total_count, 0)

        # Corrupted file was preserved
        corrupted_files = list(self.db_path.parent.glob("corrupted_db_*.json"))
        self.assertGreaterEqual(len(corrupted_files), 1)

    def test_06_incremental_update_prevents_silent_regression(self):
        """Updating a record must not overwrite existing valid metadata with None or empty fields."""
        store = DiscoveryStore(db_path=self.db_path)
        initial_data = {
            "slug": "aave-v3",
            "name": "Aave V3",
            "repository_url": "https://github.com/aave/aave-v3-core",
            "repository_resolution_status": "RESOLVED_CONFIDENT",
            "slither_findings_count": 0,
            "security_score": 92.5,
            "tvl": 10_000_000.0,
        }
        rec, _ = store.upsert_record(initial_data)

        # Second update with newer TVL but missing repository / analysis fields
        update_data = {
            "slug": "aave-v3",
            "tvl": 12_500_000.0,
            "repository_url": None,
            "repository_resolution_status": None,
        }
        updated_rec, is_new = store.upsert_record(update_data)
        self.assertFalse(is_new)

        # Repository info and score must remain intact!
        self.assertEqual(updated_rec.tvl, 12_500_000.0)
        self.assertEqual(updated_rec.repository_url, "https://github.com/aave/aave-v3-core")
        self.assertEqual(updated_rec.repository_resolution_status, "RESOLVED_CONFIDENT")
        self.assertEqual(updated_rec.security_score, 92.5)
        self.assertEqual(updated_rec.slither_findings_count, 0)

    def test_07_filtering_semantics(self):
        """filter_records must strictly adhere to TVL, chain, category, and text query filters."""
        store = DiscoveryStore(db_path=self.db_path)
        store.upsert_record({
            "slug": "p1",
            "name": "Lending Protocol Alpha",
            "chain": "ethereum",
            "chains": ["ethereum", "arbitrum"],
            "tvl": 25_000_000.0,
            "category": "Lending",
            "repository_resolution_status": "RESOLVED_CONFIDENT",
            "static_analysis_status": "COMPLETED",
        })
        store.upsert_record({
            "slug": "p2",
            "name": "Dex Beta",
            "chain": "polygon",
            "chains": ["polygon"],
            "tvl": 5_000_000.0,
            "category": "Dexes",
            "repository_resolution_status": "UNRESOLVED",
            "static_analysis_status": "PENDING",
        })
        store.upsert_record({
            "slug": "p3",
            "name": "Micro Yield Gamma",
            "chain": "bsc",
            "chains": ["bsc"],
            "tvl": 100_000.0,
            "category": "Yield",
            "repository_resolution_status": "ORGANIZATION_ONLY",
            "static_analysis_status": "UNAVAILABLE",
        })

        # Min TVL filter
        matches_tvl = store.filter_records(min_tvl=10_000_000.0)
        self.assertEqual(len(matches_tvl), 1)
        self.assertEqual(matches_tvl[0].slug, "p1")

        # Chain filter
        matches_chain = store.filter_records(chain="polygon")
        self.assertEqual(len(matches_chain), 1)
        self.assertEqual(matches_chain[0].slug, "p2")

        # Category filter
        matches_cat = store.filter_records(category="Dexes")
        self.assertEqual(len(matches_cat), 1)
        self.assertEqual(matches_cat[0].slug, "p2")

        # Search query filter (matches name, slug, or category)
        matches_search = store.filter_records(search_query="Gamma")
        self.assertEqual(len(matches_search), 1)
        self.assertEqual(matches_search[0].slug, "p3")

    def test_08_permission_scanner_heuristic_labeling(self):
        """Permission scanner results must always carry the HEURISTIC label."""
        rec = DiscoveredProtocolRecord(
            protocol_id="test-proto",
            name="Test Proto",
            slug="test-proto",
            permission_scanner_status="SCANNED",
            permission_scanner_critical_count=1,
            permission_scanner_findings=[{"function": "withdraw", "critical": True}],
        )
        self.assertEqual(rec.permission_scanner_type, "HEURISTIC")
        as_dict = rec.to_dict()
        self.assertEqual(as_dict.get("permission_scanner_type"), "HEURISTIC")


if __name__ == "__main__":
    unittest.main()
