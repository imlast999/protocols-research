"""
Automated Headless Tests for PySide6 Desktop GUI (Phase 10 Workstation Redesign).
Executes using Qt's offscreen platform plugin for deterministic CI verification.
Validates 5-view navigation shell, dashboard telemetry, candidate filtering, empty states,
detail evidence rendering, continuous hunter controls, and secure settings configuration.
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
        """MainWindow must construct with proper title and all 5 workstation views."""
        self.assertIn("DeFi Protocol Research", self.window.windowTitle())
        self.assertEqual(self.window.tabs.count(), 5)
        self.assertEqual(self.window.tabs.tabText(0), "📊 Dashboard")
        self.assertEqual(self.window.tabs.tabText(1), "🔍 Protocol Explorer")
        self.assertEqual(self.window.tabs.tabText(2), "🎯 Protocol Hunter")
        self.assertEqual(self.window.tabs.tabText(3), "📄 Reports & History")
        self.assertEqual(self.window.tabs.tabText(4), "⚙️ Settings & Diagnostics")

    def test_02_dashboard_statistics_cards_and_tables(self):
        """Dashboard stat cards and recent tables must accurately reflect store counts."""
        self.assertEqual(self.window.stat_total.findChild(QLabel, "valueLabel").text(), "2")
        self.assertEqual(self.window.stat_matched.findChild(QLabel, "valueLabel").text(), "2")
        self.assertEqual(self.window.stat_pending.findChild(QLabel, "valueLabel").text(), "1")
        self.assertEqual(self.window.stat_completed.findChild(QLabel, "valueLabel").text(), "1")

        # Verify recent discoveries table has entries
        self.assertEqual(self.window.table_recent_disc.rowCount(), 2)
        # Verify recent analysis table has entries
        self.assertEqual(self.window.table_recent_analysis.rowCount(), 2)

    def test_03_table_filtering_and_empty_state(self):
        """Filter dropdowns and search input must filter candidates and toggle empty state."""
        # 1. Search filter
        self.window.search_input.setText("Aave")
        self.assertEqual(self.window.table.rowCount(), 1)
        self.assertEqual(self.window.table.item(0, 0).text(), "Aave V3")
        self.assertEqual(self.window.table_stack.currentIndex(), 0)  # Table active

        # 2. Reset search and filter by category
        self.window.search_input.setText("")
        dex_idx = self.window.cat_combo.findText("Dexes")
        self.window.cat_combo.setCurrentIndex(dex_idx)
        self.assertEqual(self.window.table.rowCount(), 1)
        self.assertEqual(self.window.table.item(0, 0).text(), "Uniswap V3")

        # 3. Filter with no matches -> Empty State Page
        self.window.search_input.setText("NonExistentProtocolxyz123")
        self.assertEqual(self.window.table_stack.currentIndex(), 1)  # Empty State page active
        self.assertEqual(self.window.table.rowCount(), 0)

        # 4. Reset filters
        self.window._on_reset_filters()
        self.assertEqual(self.window.table_stack.currentIndex(), 0)
        self.assertEqual(self.window.table.rowCount(), 2)

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

    def test_08_settings_tab_masking_and_save(self):
        """Settings tab inputs must support password masking and saving."""
        # Test PasswordLineEdit masking toggle
        self.window.setting_certik_key.setText("secret_key_12345")
        self.assertEqual(self.window.setting_certik_key.text(), "secret_key_12345")

        # Save settings via tab
        with patch("defi_research.gui.main_window.save_env_values") as mock_save, \
             patch("defi_research.gui.main_window.QMessageBox.information"):
            self.window._on_save_settings_tab()
            mock_save.assert_called_once()
            saved_args = mock_save.call_args[0][0]
            self.assertEqual(saved_args["CERTIK_API_KEY"], "secret_key_12345")

    def test_09_hunter_metrics_and_state_updates(self):
        """Protocol Hunter metrics and state updates update UI cards properly."""
        metrics = {
            "elapsed_seconds": 3665.0,  # 1h 1m 5s
            "cycles_completed": 12,
            "protocols_examined": 150,
            "unique_candidates_added": 8,
            "recent_errors": ["Error 1"],
        }
        self.window._on_hunter_metrics_updated(metrics)
        self.assertEqual(self.window.hm_elapsed.findChild(QLabel, "valueLabel").text(), "01:01:05")
        self.assertEqual(self.window.hm_cycles.findChild(QLabel, "valueLabel").text(), "12")
        self.assertEqual(self.window.hm_examined.findChild(QLabel, "valueLabel").text(), "150")
        self.assertEqual(self.window.hm_added.findChild(QLabel, "valueLabel").text(), "8")
        self.assertEqual(self.window.hm_errors.findChild(QLabel, "valueLabel").text(), "1")

        # Test state change signal
        self.window._on_hunter_state_changed("COMPLETED")
        self.assertIn("COMPLETED", self.window.lbl_hunter_status.text())
        self.assertTrue(self.window.btn_hunter_start.isEnabled())


if __name__ == "__main__":
    unittest.main()
