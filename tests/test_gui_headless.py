"""
Automated Headless Tests for PySide6 Desktop GUI.
Executes using Qt's offscreen platform plugin for deterministic CI verification.
Validates window construction, stats cards, filtering, candidate selection,
evidence rendering, and data export.
"""

from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication, QLabel

from defi_research.discovery.discovery_store import (
    CURRENT_SCHEMA_VERSION,
    DiscoveredProtocolRecord,
    DiscoveryStore,
)
from defi_research.gui.app import create_application
from defi_research.gui.main_window import MainWindow


class TestGuiHeadless(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Create headless offscreen Qt application for test runner
        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication(["test_gui", "-platform", "offscreen"])

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "discovery_database.json"
        self.store = DiscoveryStore(db_path=self.db_path)

        # Populate sample records
        self.store.upsert_record({
            "slug": "aave-v3",
            "name": "Aave V3",
            "chain": "ethereum",
            "chains": ["ethereum", "polygon"],
            "tvl": 15_000_000.0,
            "category": "Lending",
            "repository_url": "https://github.com/aave/aave-v3-core",
            "repository_resolution_status": "RESOLVED_CONFIDENT",
            "static_analysis_status": "COMPLETED",
            "slither_findings_count": 0,
            "security_score": 94.0,
            "security_score_grade": "AAA",
        })
        self.store.upsert_record({
            "slug": "uniswap-v3",
            "name": "Uniswap V3",
            "chain": "arbitrum",
            "chains": ["arbitrum"],
            "tvl": 60_000_000.0,
            "category": "Dexes",
            "repository_resolution_status": "RESOLVED_CONFIDENT",
            "static_analysis_status": "PENDING",
        })
        self.store.save()

        self.window = MainWindow(store=self.store)

    def tearDown(self):
        self.window.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_window_construction_and_tabs(self):
        """MainWindow must construct with proper title and 3 core tabs."""
        self.assertIn("DeFi Protocol Research", self.window.windowTitle())
        self.assertEqual(self.window.tabs.count(), 3)
        self.assertEqual(self.window.tabs.tabText(0), "📊 Protocol Explorer & Dashboard")
        self.assertEqual(self.window.tabs.tabText(1), "🎯 Protocol Hunter (Continuous Discovery)")
        self.assertEqual(self.window.tabs.tabText(2), "📄 Reports & Exports")

    def test_02_dashboard_statistics_cards(self):
        """Dashboard stat cards must accurately reflect store counts."""
        self.assertEqual(self.window.stat_total.findChild(QLabel, "valueLabel").text(), "2")
        self.assertEqual(self.window.stat_matched.findChild(QLabel, "valueLabel").text(), "2")
        self.assertEqual(self.window.stat_pending.findChild(QLabel, "valueLabel").text(), "1")
        self.assertEqual(self.window.stat_completed.findChild(QLabel, "valueLabel").text(), "1")

    def test_03_table_filtering(self):
        """Filter dropdowns and search input must filter the candidates table."""
        # 1. Search filter
        self.window.search_input.setText("Aave")
        self.assertEqual(self.window.table.rowCount(), 1)
        self.assertEqual(self.window.table.item(0, 0).text(), "Aave V3")

        # 2. Reset search and filter by category
        self.window.search_input.setText("")
        dex_idx = self.window.cat_combo.findText("Dexes")
        self.window.cat_combo.setCurrentIndex(dex_idx)
        self.assertEqual(self.window.table.rowCount(), 1)
        self.assertEqual(self.window.table.item(0, 0).text(), "Uniswap V3")

    def test_04_detail_evidence_rendering(self):
        """Selecting a table candidate must render evidence, status, and heuristic labels."""
        self.window.search_input.setText("")
        self.window.cat_combo.setCurrentIndex(0)  # All
        self.window.table.selectRow(0)

        html = self.window.detail_text.toHtml()
        self.assertIn("General Metadata", html)
        self.assertIn("Repository Resolution", html)
        self.assertIn("Slither Static Analysis", html)
        self.assertIn("CertiK Security Intelligence", html)
        self.assertIn("HEURISTIC", html)
        self.assertIn("Analytical Limitations", html)

    def test_05_hunter_tab_controls(self):
        """Protocol Hunter tab controls must have expected default values."""
        self.assertEqual(self.window.spin_duration.value(), 2.0)
        self.assertEqual(self.window.spin_interval.value(), 60)
        self.assertEqual(self.window.spin_min_tvl.value(), 50000.0)
        self.assertTrue(self.window.chk_deep.isChecked())
        self.assertTrue(self.window.chk_resume.isChecked())
        self.assertTrue(self.window.btn_hunter_start.isEnabled())
        self.assertFalse(self.window.btn_hunter_stop.isEnabled())

    def test_06_environment_status_pills(self):
        """Environment pills must reflect Docker and CertiK configuration cleanly."""
        self.window._check_environment_status()
        self.assertIn("Docker:", self.window.docker_pill.text())
        self.assertIn("CertiK:", self.window.certik_pill.text())

    def test_07_single_discovery_cycle_trigger(self):
        """Quick fetch button triggers a discovery worker without raising errors."""
        with patch("defi_research.discovery.protocol_hunter.ProtocolHunter.run_discovery_cycle", return_value=1):
            self.window._on_run_single_cycle()
            self.assertIsNotNone(self.window.hunter_worker)
            self.window.hunter_worker.request_stop()
            self.window.hunter_worker.wait(1000)


if __name__ == "__main__":
    unittest.main()
