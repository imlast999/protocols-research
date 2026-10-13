"""
Main Application Window for DeFi Protocol Research & Protocol Hunter.
Implements the polished dark-themed dashboard, candidate explorer,
protocol detail inspector, continuous discovery manager, and report viewer.
"""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import subprocess
from typing import Any, Dict, List, Optional
import webbrowser

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from defi_research.config import CHAINS
from defi_research.discovery.discovery_store import (
    CURRENT_SCHEMA_VERSION,
    DiscoveredProtocolRecord,
    DiscoveryStore,
    get_default_data_dir,
)
from defi_research.discovery.protocol_hunter import HunterConfig, HunterState
from defi_research.gui.theme import DARK_THEME_QSS
from defi_research.gui.workers import HunterWorker, SingleAnalysisWorker
from defi_research.slither_runner import check_docker_availability, check_slither_availability

logger = logging.getLogger("MainWindow")


class MainWindow(QMainWindow):
    """Main window of the DeFi Protocol Research Desktop Application."""

    def __init__(self, store: Optional[DiscoveryStore] = None):
        super().__init__()
        self.setWindowTitle("DeFi Protocol Research Suite — Protocol Hunter")
        self.resize(1340, 880)
        self.setMinimumSize(1024, 680)

        self.store = store or DiscoveryStore()
        self.data_dir = get_default_data_dir()
        self.hunter_worker: Optional[HunterWorker] = None
        self.analysis_worker: Optional[SingleAnalysisWorker] = None

        self._active_filtered_records: List[DiscoveredProtocolRecord] = []
        self._selected_record: Optional[DiscoveredProtocolRecord] = None

        self._init_ui()
        self._apply_theme()
        self._check_environment_status()
        self._refresh_table_and_dashboard()

        # UI ticker timer for elapsed time updates
        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self._on_ui_tick)
        self.ui_timer.start(1000)

    def _apply_theme(self):
        self.setStyleSheet(DARK_THEME_QSS)

    def _init_ui(self):
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(14, 12, 14, 12)
        main_layout.setSpacing(10)

        # 1. Top Header Bar (App Title & Diagnostic Pills)
        header_layout = QHBoxLayout()

        title_label = QLabel("🛡️ DeFi Protocol Research Suite")
        title_font = QFont()
        title_font.setPointSize(15)
        title_font.setBold(True)
        title_label.setFont(title_font)
        header_layout.addWidget(title_label)

        sub_label = QLabel("v0.4.0 — Protocol Hunter")
        sub_label.setStyleSheet("color: #8b949e; margin-left: 6px;")
        header_layout.addWidget(sub_label)

        header_layout.addStretch()

        # Environment status pills
        self.docker_pill = QPushButton("Docker: Checking...")
        self.docker_pill.setToolTip("Click for Docker execution status details")
        self.docker_pill.clicked.connect(self._show_docker_status_dialog)
        header_layout.addWidget(self.docker_pill)

        self.certik_pill = QPushButton("CertiK: Checking...")
        self.certik_pill.setToolTip("Click for CertiK API configuration status")
        self.certik_pill.clicked.connect(self._show_certik_status_dialog)
        header_layout.addWidget(self.certik_pill)

        self.btn_settings = QPushButton("⚙️ Settings & API Keys")
        self.btn_settings.setToolTip("Configure CertiK, Explorer API keys, and Slither backend")
        self.btn_settings.clicked.connect(self._show_settings_dialog)
        header_layout.addWidget(self.btn_settings)

        main_layout.addLayout(header_layout)

        # 2. Main Tab Widget
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        self.dashboard_tab = QWidget()
        self.hunter_tab = QWidget()
        self.reports_tab = QWidget()

        self.tabs.addTab(self.dashboard_tab, "📊 Protocol Explorer & Dashboard")
        self.tabs.addTab(self.hunter_tab, "🎯 Protocol Hunter (Continuous Discovery)")
        self.tabs.addTab(self.reports_tab, "📄 Reports & Exports")

        self._setup_dashboard_tab()
        self._setup_hunter_tab()
        self._setup_reports_tab()

    # -------------------------------------------------------------------------
    # TAB 1: PROTOCOL EXPLORER & DASHBOARD
    # -------------------------------------------------------------------------
    def _setup_dashboard_tab(self):
        tab_layout = QVBoxLayout(self.dashboard_tab)
        tab_layout.setContentsMargins(10, 10, 10, 10)
        tab_layout.setSpacing(10)

        # 1. Statistics Cards Row
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(10)

        self.stat_total = self._create_stat_card("Total Discovered", "0", "#58a6ff")
        self.stat_matched = self._create_stat_card("Filter Matches", "0", "#3fb950")
        self.stat_pending = self._create_stat_card("Pending Analysis", "0", "#d29922")
        self.stat_completed = self._create_stat_card("Completed Analyses", "0", "#2ea043")
        self.stat_unavailable = self._create_stat_card("Failed / Unavailable", "0", "#f85149")
        self.stat_last_cycle = self._create_stat_card("Last Discovery Cycle", "Never", "#8b949e")

        stats_layout.addWidget(self.stat_total)
        stats_layout.addWidget(self.stat_matched)
        stats_layout.addWidget(self.stat_pending)
        stats_layout.addWidget(self.stat_completed)
        stats_layout.addWidget(self.stat_unavailable)
        stats_layout.addWidget(self.stat_last_cycle)
        tab_layout.addLayout(stats_layout)

        # 2. Filters & Search Control Bar
        filter_box = QGroupBox("Filter Candidates")
        filter_layout = QHBoxLayout(filter_box)
        filter_layout.setContentsMargins(10, 8, 10, 8)
        filter_layout.setSpacing(12)

        # Search box
        filter_layout.addWidget(QLabel("Search:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Name, slug, category, address...")
        self.search_input.textChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.search_input, stretch=2)

        # Min TVL selector
        filter_layout.addWidget(QLabel("Min TVL:"))
        self.tvl_combo = QComboBox()
        self.tvl_combo.addItems([
            "All ($0)",
            "$50,000",
            "$500,000",
            "$1,000,000",
            "$10,000,000",
            "$50,000,000",
        ])
        self.tvl_combo.setCurrentIndex(1)  # Default: $50,000
        self.tvl_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.tvl_combo)

        # Chain selector
        filter_layout.addWidget(QLabel("Chain:"))
        self.chain_combo = QComboBox()
        self.chain_combo.addItem("All")
        for ch in sorted(CHAINS.keys()):
            self.chain_combo.addItem(ch.capitalize())
        self.chain_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.chain_combo)

        # Category selector
        filter_layout.addWidget(QLabel("Category:"))
        self.cat_combo = QComboBox()
        self.cat_combo.addItems([
            "All",
            "Lending",
            "Dexes",
            "Derivatives",
            "Bridge",
            "Liquid Staking",
            "Yield",
        ])
        self.cat_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.cat_combo)

        # Refresh / Run Cycle button
        self.btn_quick_fetch = QPushButton("🔄 Run Discovery Cycle")
        self.btn_quick_fetch.setObjectName("primaryBtn")
        self.btn_quick_fetch.clicked.connect(self._on_run_single_cycle)
        filter_layout.addWidget(self.btn_quick_fetch)

        tab_layout.addWidget(filter_box)

        # 3. Main Splitter: Candidates Table (Left/Center) + Protocol Details (Right)
        splitter = QSplitter(Qt.Horizontal)

        # Left: Table
        table_container = QWidget()
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "Protocol Name",
            "Chain",
            "TVL ($)",
            "Category",
            "Repo Resolution",
            "Slither Status",
            "CertiK Tier",
            "Last Updated",
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._on_table_selection_changed)
        table_layout.addWidget(self.table)

        splitter.addWidget(table_container)

        # Right: Detail Inspector
        self.detail_panel = self._create_detail_inspector_panel()
        splitter.addWidget(self.detail_panel)
        splitter.setSizes([780, 520])

        tab_layout.addWidget(splitter)

    def _create_stat_card(self, title: str, value: str, color: str) -> QFrame:
        card = QFrame()
        card.setFrameShape(QFrame.StyledPanel)
        card.setStyleSheet(f"""
            QFrame {{
                background-color: #161b22;
                border: 1px solid #30363d;
                border-radius: 6px;
                padding: 6px 12px;
            }}
        """)
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(4, 4, 4, 4)
        c_layout.setSpacing(2)

        t_lbl = QLabel(title)
        t_lbl.setStyleSheet("color: #8b949e; font-size: 11px; font-weight: 600;")
        c_layout.addWidget(t_lbl)

        v_lbl = QLabel(value)
        v_lbl.setObjectName("valueLabel")
        v_lbl.setStyleSheet(f"color: {color}; font-size: 18px; font-weight: bold;")
        c_layout.addWidget(v_lbl)

        return card

    def _create_detail_inspector_panel(self) -> QWidget:
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(8)

        group = QGroupBox("Protocol Intelligence & Evidence")
        g_layout = QVBoxLayout(group)
        g_layout.setContentsMargins(10, 12, 10, 10)
        g_layout.setSpacing(8)

        # Scrollable metadata area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        scroll_content = QWidget()
        self.detail_layout = QVBoxLayout(scroll_content)
        self.detail_layout.setContentsMargins(0, 0, 4, 0)
        self.detail_layout.setSpacing(10)

        # Protocol Header
        self.lbl_proto_title = QLabel("Select a protocol to inspect evidence")
        f = QFont()
        f.setPointSize(14)
        f.setBold(True)
        self.lbl_proto_title.setFont(f)
        self.lbl_proto_title.setStyleSheet("color: #58a6ff;")
        self.detail_layout.addWidget(self.lbl_proto_title)

        # Text summary view
        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setStyleSheet("""
            QTextEdit {
                background-color: #0d1117;
                border: 1px solid #30363d;
                border-radius: 6px;
                font-family: 'Segoe UI', sans-serif;
                font-size: 12px;
                color: #c9d1d9;
            }
        """)
        self.detail_layout.addWidget(self.detail_text)

        scroll.setWidget(scroll_content)
        g_layout.addWidget(scroll)

        # Action Buttons
        btn_layout = QHBoxLayout()
        self.btn_run_analysis = QPushButton("⚡ Run Pipeline Analysis")
        self.btn_run_analysis.setObjectName("primaryBtn")
        self.btn_run_analysis.clicked.connect(self._on_run_deep_analysis_selected)
        btn_layout.addWidget(self.btn_run_analysis)

        self.btn_open_repo = QPushButton("🔗 Open Repository")
        self.btn_open_repo.clicked.connect(self._on_open_selected_repo)
        btn_layout.addWidget(self.btn_open_repo)

        g_layout.addLayout(btn_layout)
        panel_layout.addWidget(group)

        return panel

    # -------------------------------------------------------------------------
    # TAB 2: PROTOCOL HUNTER (CONTINUOUS DISCOVERY)
    # -------------------------------------------------------------------------
    def _setup_hunter_tab(self):
        layout = QVBoxLayout(self.hunter_tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # 1. Config Box
        cfg_box = QGroupBox("Continuous Discovery Configuration (Protocol Hunter)")
        cfg_layout = QGridLayout(cfg_box)
        cfg_layout.setContentsMargins(12, 14, 12, 12)
        cfg_layout.setSpacing(10)

        # Notice
        notice_lbl = QLabel(
            "ℹ️ <b>Protocol Hunter</b> continuously discovers and monitors active DeFi protocols on DefiLlama.<br>"
            "This is research and security intelligence collection, <b>not cryptocurrency mining</b>. "
            "No wallet actions or blockchain transactions are performed."
        )
        notice_lbl.setStyleSheet("color: #58a6ff; margin-bottom: 6px;")
        cfg_layout.addWidget(notice_lbl, 0, 0, 1, 4)

        # Duration
        cfg_layout.addWidget(QLabel("Discovery Duration (Hours):"), 1, 0)
        self.spin_duration = QDoubleSpinBox()
        self.spin_duration.setRange(0.1, 72.0)
        self.spin_duration.setValue(2.0)
        self.spin_duration.setSingleStep(0.5)
        self.spin_duration.setSuffix(" hrs")
        cfg_layout.addWidget(self.spin_duration, 1, 1)

        # Interval
        cfg_layout.addWidget(QLabel("Cycle Interval (Seconds):"), 1, 2)
        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(10, 3600)
        self.spin_interval.setValue(60)
        self.spin_interval.setSuffix(" s")
        cfg_layout.addWidget(self.spin_interval, 1, 3)

        # Min TVL
        cfg_layout.addWidget(QLabel("Minimum TVL ($ USD):"), 2, 0)
        self.spin_min_tvl = QDoubleSpinBox()
        self.spin_min_tvl.setRange(0.0, 1_000_000_000.0)
        self.spin_min_tvl.setValue(50_000.0)
        self.spin_min_tvl.setPrefix("$")
        cfg_layout.addWidget(self.spin_min_tvl, 2, 1)

        # Max candidates per cycle
        cfg_layout.addWidget(QLabel("Max Ingested Per Cycle:"), 2, 2)
        self.spin_max_per_cycle = QSpinBox()
        self.spin_max_per_cycle.setRange(5, 200)
        self.spin_max_per_cycle.setValue(25)
        cfg_layout.addWidget(self.spin_max_per_cycle, 2, 3)

        # Deep investigation & Resume toggles
        self.chk_deep = QCheckBox("Deep Investigation (Resolve GitHub Repositories)")
        self.chk_deep.setChecked(True)
        cfg_layout.addWidget(self.chk_deep, 3, 0, 1, 2)

        self.chk_resume = QCheckBox("Resume / Append to Existing Discovery Store")
        self.chk_resume.setChecked(True)
        cfg_layout.addWidget(self.chk_resume, 3, 2, 1, 2)

        layout.addWidget(cfg_box)

        # 2. Controls & Status Card Row
        ctrl_layout = QHBoxLayout()

        self.btn_hunter_start = QPushButton("▶️ Start Continuous Hunter")
        self.btn_hunter_start.setObjectName("primaryBtn")
        self.btn_hunter_start.clicked.connect(self._on_start_hunter)
        ctrl_layout.addWidget(self.btn_hunter_start)

        self.btn_hunter_pause = QPushButton("⏸️ Pause")
        self.btn_hunter_pause.setEnabled(False)
        self.btn_hunter_pause.clicked.connect(self._on_pause_hunter)
        ctrl_layout.addWidget(self.btn_hunter_pause)

        self.btn_hunter_resume = QPushButton("▶️ Resume")
        self.btn_hunter_resume.setEnabled(False)
        self.btn_hunter_resume.clicked.connect(self._on_resume_hunter)
        ctrl_layout.addWidget(self.btn_hunter_resume)

        self.btn_hunter_stop = QPushButton("⏹️ Stop")
        self.btn_hunter_stop.setObjectName("dangerBtn")
        self.btn_hunter_stop.setEnabled(False)
        self.btn_hunter_stop.clicked.connect(self._on_stop_hunter)
        ctrl_layout.addWidget(self.btn_hunter_stop)

        ctrl_layout.addStretch()

        self.lbl_hunter_status = QLabel("Status: IDLE")
        self.lbl_hunter_status.setStyleSheet("font-weight: bold; color: #8b949e; font-size: 14px;")
        ctrl_layout.addWidget(self.lbl_hunter_status)

        layout.addLayout(ctrl_layout)

        # 3. Live Metrics Cards
        m_row = QHBoxLayout()
        self.hm_elapsed = self._create_stat_card("Elapsed Time", "00:00:00", "#58a6ff")
        self.hm_cycles = self._create_stat_card("Cycles Completed", "0", "#3fb950")
        self.hm_examined = self._create_stat_card("Protocols Evaluated", "0", "#8b949e")
        self.hm_added = self._create_stat_card("New Candidates Added", "0", "#d29922")
        self.hm_errors = self._create_stat_card("Recent Errors", "0", "#f85149")

        m_row.addWidget(self.hm_elapsed)
        m_row.addWidget(self.hm_cycles)
        m_row.addWidget(self.hm_examined)
        m_row.addWidget(self.hm_added)
        m_row.addWidget(self.hm_errors)
        layout.addLayout(m_row)

        # 4. Activity Log Console
        log_box = QGroupBox("Protocol Hunter Live Activity Log")
        log_layout = QVBoxLayout(log_box)
        log_layout.setContentsMargins(8, 10, 8, 8)

        self.hunter_log_console = QPlainTextEdit()
        self.hunter_log_console.setReadOnly(True)
        self.hunter_log_console.setMaximumBlockCount(2000)
        log_layout.addWidget(self.hunter_log_console)

        layout.addWidget(log_box)

    # -------------------------------------------------------------------------
    # TAB 3: REPORTS & EXPORTS
    # -------------------------------------------------------------------------
    def _setup_reports_tab(self):
        layout = QVBoxLayout(self.reports_tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Controls
        btn_bar = QHBoxLayout()

        btn_export_db = QPushButton("💾 Export Discovery Database (JSON)")
        btn_export_db.clicked.connect(self._on_export_database)
        btn_bar.addWidget(btn_export_db)

        btn_export_filtered = QPushButton("📤 Export Active Filtered Candidates (JSON)")
        btn_export_filtered.clicked.connect(self._on_export_filtered_records)
        btn_bar.addWidget(btn_export_filtered)

        btn_open_folder = QPushButton("📁 Open Data Directory")
        btn_open_folder.clicked.connect(self._on_open_data_directory)
        btn_bar.addWidget(btn_open_folder)

        btn_bar.addStretch()

        btn_refresh_reports = QPushButton("🔄 Refresh Reports List")
        btn_refresh_reports.clicked.connect(self._refresh_reports_list)
        btn_bar.addWidget(btn_refresh_reports)

        layout.addLayout(btn_bar)

        # Splitter: Left Reports List, Right Report JSON Previewer
        splitter = QSplitter(Qt.Horizontal)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Generated Pipeline Reports:"))

        self.reports_table = QTableWidget()
        self.reports_table.setColumnCount(3)
        self.reports_table.setHorizontalHeaderLabels(["Report File", "Protocol", "Timestamp"])
        self.reports_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.reports_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.reports_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.reports_table.horizontalHeader().setStretchLastSection(True)
        self.reports_table.itemSelectionChanged.connect(self._on_report_selected)
        left_layout.addWidget(self.reports_table)

        splitter.addWidget(left_widget)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("Report Contents (Redacted Secrets):"))

        self.report_preview = QTextEdit()
        self.report_preview.setReadOnly(True)
        self.report_preview.setPlaceholderText("Select a report file from the left to view contents...")
        right_layout.addWidget(self.report_preview)

        splitter.addWidget(right_widget)
        splitter.setSizes([450, 850])

        layout.addWidget(splitter)
        self._refresh_reports_list()

    # -------------------------------------------------------------------------
    # ENVIRONMENT & DIAGNOSTIC DIALOGS
    # -------------------------------------------------------------------------
    def _check_environment_status(self):
        # 1. Docker check
        docker_ok, img_ok, docker_msg = check_docker_availability()
        if docker_ok and img_ok:
            self.docker_pill.setText("Docker: Ready")
            self.docker_pill.setStyleSheet("background-color: #238636; color: #fff;")
        elif docker_ok:
            self.docker_pill.setText("Docker: Image Missing")
            self.docker_pill.setStyleSheet("background-color: #d29922; color: #fff;")
        else:
            self.docker_pill.setText("Docker: Unavailable")
            self.docker_pill.setStyleSheet("background-color: #da3633; color: #fff;")

        # 2. CertiK check
        certik_key = os.getenv("CERTIK_API_KEY", "").strip()
        if certik_key:
            self.certik_pill.setText("CertiK: Key Configured")
            self.certik_pill.setStyleSheet("background-color: #238636; color: #fff;")
        else:
            self.certik_pill.setText("CertiK: Key Missing")
            self.certik_pill.setStyleSheet("background-color: #30363d; color: #8b949e;")

    def _show_docker_status_dialog(self):
        docker_ok, img_ok, docker_msg = check_docker_availability()
        slither_ok, slither_ver, slither_msg = check_slither_availability(backend="native")

        msg = (
            "<h3>Docker & Slither Static Analysis Environment Status</h3>"
            f"<p><b>Docker Daemon Available:</b> {'✅ Yes' if docker_ok else '❌ No'}</p>"
            f"<p><b>Docker Slither Image (defi-slither:0.10.4):</b> {'✅ Found' if img_ok else '⚠️ Not Found'}</p>"
            f"<p><b>Native Slither Installed:</b> {'✅ ' + str(slither_ver) if slither_ok else '❌ No'}</p>"
            "<hr>"
            "<p><b>Operational Guidance:</b></p>"
            "<ul>"
            "<li><b>Protocol Discovery, Repository Resolution, and CertiK Intelligence</b> "
            "do NOT require Docker and operate with 100% functionality.</li>"
            "<li>To enable live Slither static analysis on Windows, ensure Docker Desktop is running with WSL2 and build the analyzer image:<br>"
            "<code>docker build -t defi-slither:0.10.4 .</code></li>"
            "<li>Any analysis conducted without Docker will be explicitly flagged as <code>UNAVAILABLE</code> "
            "and will never masquerade as a clean 0-findings audit.</li>"
            "</ul>"
        )
        QMessageBox.information(self, "Environment Status — Docker & Slither", msg)

    def _show_certik_status_dialog(self):
        certik_key = os.getenv("CERTIK_API_KEY", "").strip()
        has_key = bool(certik_key)
        msg = (
            "<h3>CertiK Skynet Partner API Status</h3>"
            f"<p><b>API Key Configured:</b> {'✅ Yes' if has_key else '❌ Not Configured'}</p>"
            "<hr>"
            "<p><b>Guidance:</b></p>"
            "<ul>"
            "<li>Commercial CertiK security intelligence requires a partner API key.</li>"
            "<li>Configure <code>CERTIK_API_KEY</code> in your <code>.env</code> file, environment variables, or via the <b>⚙️ Settings & API Keys</b> dialog.</li>"
            "<li>When missing, the pipeline marks security intelligence as <code>NOT_CONFIGURED</code> cleanly "
            "without interrupting protocol discovery or repository resolution.</li>"
            "</ul>"
        )
        QMessageBox.information(self, "CertiK Skynet API Status", msg)

    def _show_settings_dialog(self):
        """Displays secure settings dialog for configuring API keys and execution preferences."""
        from defi_research.config import save_env_values
        from defi_research.security_intelligence import check_live_api_connectivity

        dialog = QDialog(self)
        dialog.setWindowTitle("Application Configuration & API Keys")
        dialog.resize(580, 520)
        d_layout = QVBoxLayout(dialog)
        d_layout.setSpacing(12)

        # 1. CertiK Group
        certik_box = QGroupBox("CertiK Skynet Partner API Configuration")
        c_layout = QGridLayout(certik_box)

        c_layout.addWidget(QLabel("CertiK API Key:"), 0, 0)
        key_input = QLineEdit(os.getenv("CERTIK_API_KEY", ""))
        key_input.setEchoMode(QLineEdit.Password)
        c_layout.addWidget(key_input, 0, 1)

        btn_toggle_key = QPushButton("👁️")
        btn_toggle_key.setFixedWidth(36)
        def _toggle_echo():
            if key_input.echoMode() == QLineEdit.Password:
                key_input.setEchoMode(QLineEdit.Normal)
            else:
                key_input.setEchoMode(QLineEdit.Password)
        btn_toggle_key.clicked.connect(_toggle_echo)
        c_layout.addWidget(btn_toggle_key, 0, 2)

        btn_test_certik = QPushButton("🧪 Test CertiK Connection")
        def _test_certik():
            test_key = key_input.text().strip()
            if not test_key:
                QMessageBox.warning(dialog, "Missing Key", "Please enter a CertiK API key before testing connection.")
                return
            old_key = os.environ.get("CERTIK_API_KEY")
            os.environ["CERTIK_API_KEY"] = test_key
            try:
                ok, diag = check_live_api_connectivity()
                if ok:
                    QMessageBox.information(dialog, "CertiK Live Check", f"✅ Connection successful!\n{diag}")
                else:
                    QMessageBox.warning(dialog, "CertiK Live Check", f"⚠️ Connection test returned: {diag}")
            finally:
                if old_key is not None:
                    os.environ["CERTIK_API_KEY"] = old_key
                else:
                    os.environ.pop("CERTIK_API_KEY", None)

        btn_test_certik.clicked.connect(_test_certik)
        c_layout.addWidget(btn_test_certik, 1, 1, 1, 2)

        d_layout.addWidget(certik_box)

        # 2. Explorer API Keys Group
        explorer_box = QGroupBox("Explorer API Keys (for Heuristic On-Chain Permission Scanner)")
        e_layout = QGridLayout(explorer_box)

        e_layout.addWidget(QLabel("Etherscan API Key:"), 0, 0)
        eth_key_in = QLineEdit(os.getenv("ETHERSCAN_API_KEY", ""))
        eth_key_in.setEchoMode(QLineEdit.Password)
        e_layout.addWidget(eth_key_in, 0, 1)

        e_layout.addWidget(QLabel("Arbiscan API Key:"), 1, 0)
        arb_key_in = QLineEdit(os.getenv("ARBISCAN_API_KEY", ""))
        arb_key_in.setEchoMode(QLineEdit.Password)
        e_layout.addWidget(arb_key_in, 1, 1)

        e_layout.addWidget(QLabel("Polygonscan API Key:"), 2, 0)
        poly_key_in = QLineEdit(os.getenv("POLYGONSCAN_API_KEY", ""))
        poly_key_in.setEchoMode(QLineEdit.Password)
        e_layout.addWidget(poly_key_in, 2, 1)

        e_layout.addWidget(QLabel("BscScan API Key:"), 3, 0)
        bsc_key_in = QLineEdit(os.getenv("BSCSCAN_API_KEY", ""))
        bsc_key_in.setEchoMode(QLineEdit.Password)
        e_layout.addWidget(bsc_key_in, 3, 1)

        d_layout.addWidget(explorer_box)

        # 3. Slither Settings Group
        slither_box = QGroupBox("Slither Static Analysis Configuration")
        s_layout = QGridLayout(slither_box)

        s_layout.addWidget(QLabel("Execution Backend:"), 0, 0)
        backend_combo = QComboBox()
        backend_combo.addItems(["auto", "docker", "native"])
        curr_b = os.getenv("SLITHER_BACKEND", "auto").lower()
        idx = backend_combo.findText(curr_b)
        backend_combo.setCurrentIndex(idx if idx >= 0 else 0)
        s_layout.addWidget(backend_combo, 0, 1)

        s_layout.addWidget(QLabel("Docker Image Tag:"), 1, 0)
        image_in = QLineEdit(os.getenv("SLITHER_DOCKER_IMAGE", "defi-slither:0.10.4"))
        s_layout.addWidget(image_in, 1, 1)

        btn_check_slither = QPushButton("🧪 Check Slither & Docker")
        btn_check_slither.clicked.connect(self._show_docker_status_dialog)
        s_layout.addWidget(btn_check_slither, 2, 1)

        d_layout.addWidget(slither_box)

        # 4. Save and Cancel Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(dialog.reject)
        btn_row.addWidget(btn_cancel)

        btn_save = QPushButton("💾 Save & Apply")
        btn_save.setObjectName("primaryBtn")
        def _save_settings():
            to_save = {
                "CERTIK_API_KEY": key_input.text().strip(),
                "ETHERSCAN_API_KEY": eth_key_in.text().strip(),
                "ARBISCAN_API_KEY": arb_key_in.text().strip(),
                "POLYGONSCAN_API_KEY": poly_key_in.text().strip(),
                "BSCSCAN_API_KEY": bsc_key_in.text().strip(),
                "SLITHER_BACKEND": backend_combo.currentText(),
                "SLITHER_DOCKER_IMAGE": image_in.text().strip(),
            }
            save_env_values(to_save)
            self._check_environment_status()
            QMessageBox.information(dialog, "Settings Saved", "Configuration saved and applied successfully.")
            dialog.accept()

        btn_save.clicked.connect(_save_settings)
        btn_row.addWidget(btn_save)

        d_layout.addLayout(btn_row)
        dialog.exec()

    # -------------------------------------------------------------------------
    # TABLE & DETAIL SYNCHRONIZATION
    # -------------------------------------------------------------------------
    def _get_active_min_tvl(self) -> float:
        tvl_text = self.tvl_combo.currentText()
        if "$50,000" in tvl_text:
            return 50_000.0
        elif "$500,000" in tvl_text:
            return 500_000.0
        elif "$1,000,000" in tvl_text:
            return 1_000_000.0
        elif "$10,000,000" in tvl_text:
            return 10_000_000.0
        elif "$50,000,000" in tvl_text:
            return 50_000_000.0
        return 0.0

    def _on_filter_changed(self):
        self._refresh_table_and_dashboard()

    def _refresh_table_and_dashboard(self):
        min_tvl = self._get_active_min_tvl()
        chain = self.chain_combo.currentText()
        category = self.cat_combo.currentText()
        search_query = self.search_input.text()

        records = self.store.filter_records(
            min_tvl=min_tvl if min_tvl > 0 else None,
            chain=chain,
            category=category,
            search_query=search_query,
        )
        self._active_filtered_records = records

        # Update stats
        all_records = self.store.get_all_records()
        self.stat_total.findChild(QLabel, "valueLabel").setText(str(len(all_records)))
        self.stat_matched.findChild(QLabel, "valueLabel").setText(str(len(records)))

        pending_cnt = sum(
            1 for r in all_records
            if not r.static_analysis_status or r.static_analysis_status.upper() == "PENDING"
        )
        completed_cnt = sum(1 for r in all_records if r.static_analysis_status == "COMPLETED")
        failed_cnt = sum(
            1 for r in all_records
            if r.static_analysis_status in ("FAILED", "UNAVAILABLE", "ANALYSIS_FAILED")
        )

        self.stat_pending.findChild(QLabel, "valueLabel").setText(str(pending_cnt))
        self.stat_completed.findChild(QLabel, "valueLabel").setText(str(completed_cnt))
        self.stat_unavailable.findChild(QLabel, "valueLabel").setText(str(failed_cnt))

        meta = self.store.get_metadata()
        last_cy = meta.get("last_cycle_at")
        if last_cy:
            try:
                dt = datetime.fromisoformat(last_cy)
                self.stat_last_cycle.findChild(QLabel, "valueLabel").setText(dt.strftime("%H:%M:%S"))
            except Exception:
                self.stat_last_cycle.findChild(QLabel, "valueLabel").setText(str(last_cy)[:19])
        else:
            self.stat_last_cycle.findChild(QLabel, "valueLabel").setText("Never")

        # Repopulate table
        self.table.setRowCount(len(records))
        for row_idx, r in enumerate(records):
            # Name
            self.table.setItem(row_idx, 0, QTableWidgetItem(r.name))

            # Chain
            ch_str = (r.chain or "unknown").upper()
            self.table.setItem(row_idx, 1, QTableWidgetItem(ch_str))

            # TVL
            tvl_str = f"${r.tvl:,.2f}" if r.tvl is not None else "N/A"
            tvl_item = QTableWidgetItem(tvl_str)
            tvl_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row_idx, 2, tvl_item)

            # Category
            self.table.setItem(row_idx, 3, QTableWidgetItem(r.category or "General"))

            # Repo Resolution
            res_str = r.repository_resolution_status or "UNRESOLVED"
            self.table.setItem(row_idx, 4, QTableWidgetItem(res_str))

            # Slither
            sli_str = r.static_analysis_status or "PENDING"
            self.table.setItem(row_idx, 5, QTableWidgetItem(sli_str))

            # CertiK Tier
            sec_str = r.security_score_grade or (f"{r.security_score:.0f}" if r.security_score else "N/A")
            self.table.setItem(row_idx, 6, QTableWidgetItem(sec_str))

            # Last Updated
            upd_str = r.last_updated_at[:19].replace("T", " ") if r.last_updated_at else "N/A"
            self.table.setItem(row_idx, 7, QTableWidgetItem(upd_str))

        if records and self._selected_record is None:
            self.table.selectRow(0)

    def _on_table_selection_changed(self):
        sel = self.table.selectedItems()
        if not sel:
            return
        row = sel[0].row()
        if 0 <= row < len(self._active_filtered_records):
            record = self._active_filtered_records[row]
            self._selected_record = record
            self._display_protocol_details(record)

    def _display_protocol_details(self, r: DiscoveredProtocolRecord):
        self.lbl_proto_title.setText(f"{r.name} ({r.slug})")

        tvl_display = f"${r.tvl:,.2f} USD" if r.tvl is not None else "N/A"
        addr_display = r.address or "No primary EVM address recorded"
        repo_display = r.repository_url or "Not resolved"
        res_status = r.repository_resolution_status or "UNRESOLVED"
        res_reason = r.repository_confidence_reason or "N/A"
        res_score = f"{r.repository_confidence_score:.2f}" if r.repository_confidence_score is not None else "N/A"

        # Static analysis summary
        sli_status = r.static_analysis_status or "NOT_RUN"
        sli_findings = f"{r.slither_findings_count} findings" if r.slither_findings_count is not None else "N/A"
        sli_breakdown = ", ".join(f"{k}: {v}" for k, v in r.slither_findings_summary.items()) or "None"
        sli_diag = r.slither_diagnostics or "None"

        # CertiK summary
        sec_status = r.security_intelligence_status or "NOT_CONFIGURED"
        sec_score = f"{r.security_score:.1f}" if r.security_score is not None else "N/A"
        sec_tier = r.security_score_grade or "N/A"
        audit_cnt = str(r.audit_count) if r.audit_count is not None else "N/A"

        # Permission scan summary
        perm_crit = (
            f"{r.permission_scanner_critical_count} critical functions"
            if r.permission_scanner_critical_count is not None
            else "Not scanned"
        )
        perm_findings_html = ""
        if r.permission_scanner_findings:
            items_html = "".join(
                f"<li><code>{f.get('signature', 'unknown')}</code> "
                f"[{f.get('category', 'unknown')}] "
                f"(modifiers: {', '.join(f.get('modifiers', [])) or 'none'})</li>"
                for f in r.permission_scanner_findings[:10]
            )
            if len(r.permission_scanner_findings) > 10:
                items_html += f"<li>... and {len(r.permission_scanner_findings) - 10} more</li>"
            perm_findings_html = f"<br><b>Detected Signals:</b><ul>{items_html}</ul>"
        elif r.permission_scanner_status == "SUCCESS":
            perm_findings_html = "<br><b>Findings:</b> Clean (No unhedged administrative/privileged keywords detected)"

        detail_md = f"""
<h3>📌 General Metadata</h3>
<b>Protocol Name:</b> {r.name}<br>
<b>DefiLlama Slug:</b> {r.slug}<br>
<b>Discovery Source:</b> {r.discovery_source}<br>
<b>Primary Chain:</b> {(r.chain or 'unknown').upper()}<br>
<b>All Chains:</b> {', '.join(r.chains) if r.chains else 'N/A'}<br>
<b>TVL:</b> {tvl_display}<br>
<b>Contract Address:</b> <code>{addr_display}</code><br>
<b>First Discovered:</b> {r.first_discovered_at}<br>
<b>Last Updated:</b> {r.last_updated_at}
<hr>

<h3>🐙 Repository Resolution</h3>
<b>Status:</b> <b>{res_status}</b><br>
<b>Confidence Score:</b> {res_score}<br>
<b>Rationale:</b> {res_reason}<br>
<b>Repository URL:</b> <a href="{repo_display}">{repo_display}</a><br>
<b>Analyzed Commit:</b> <code>{r.analyzed_commit or 'N/A'}</code>
<hr>

<h3>🔍 Slither Static Analysis</h3>
<b>Status:</b> <b>{sli_status}</b><br>
<b>Execution Backend:</b> {r.slither_execution_backend or 'N/A'}<br>
<b>Slither Version:</b> {r.slither_version or 'N/A'}<br>
<b>Findings Count:</b> {sli_findings}<br>
<b>Severity Breakdown:</b> {sli_breakdown}<br>
<b>Diagnostics / Reason:</b> {sli_diag}<br>
<small style="color: #8b949e;">* Note: An UNAVAILABLE or SKIPPED scan is never presented as zero findings.</small>
<hr>

<h3>🛡️ CertiK Security Intelligence</h3>
<b>Status:</b> {sec_status}<br>
<b>Security Score:</b> {sec_score} (Tier: {sec_tier})<br>
<b>Audit Records:</b> {audit_cnt}<br>
<b>CertiK Project ID:</b> {r.certik_project_id or 'N/A'}
<hr>

<h3>⚡ On-Chain Permission Scanner [HEURISTIC]</h3>
<b>Status:</b> {r.permission_scanner_status or 'NOT_SCANNED'}<br>
<b>Critical Signals:</b> {perm_crit}{perm_findings_html}<br>
<small style="color: #8b949e;">* Explorer ABI regex search is heuristic and requires Foundry simulation confirmation.</small>
<hr>

<h3>⚠️ Analytical Limitations</h3>
<ul>
<li>Static analysis cannot detect complex economic exploits, price oracle manipulation, or flash loan arbitrage.</li>
<li>Repository source code is not guaranteed to correspond to on-chain deployed bytecode without formal bytecode verification.</li>
<li>External security scores and audit counts do not guarantee smart contract immunity from zero-day vulnerabilities.</li>
</ul>
        """
        self.detail_text.setHtml(detail_md)

    def _on_open_selected_repo(self):
        if self._selected_record and self._selected_record.repository_url:
            webbrowser.open(self._selected_record.repository_url)
        else:
            QMessageBox.information(
                self,
                "No Repository URL",
                "Selected protocol does not have a resolved repository URL.",
            )

    def _on_run_deep_analysis_selected(self):
        if not self._selected_record:
            QMessageBox.warning(self, "No Protocol Selected", "Please select a protocol from the table.")
            return

        slug = self._selected_record.slug
        confirm = QMessageBox.question(
            self,
            "Run Deep Pipeline Analysis",
            f"Run deep analysis pipeline on protocol '{slug}'?\n"
            "This will verify sources, execute Slither static analysis, and query security intelligence.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        self.btn_run_analysis.setEnabled(False)
        self.btn_run_analysis.setText("⏳ Analyzing...")

        self.analysis_worker = SingleAnalysisWorker(
            protocol_data=self._selected_record.to_dict(),
            slither_backend="auto",
        )
        self.analysis_worker.analysis_completed.connect(self._on_single_analysis_completed)
        self.analysis_worker.analysis_failed.connect(self._on_single_analysis_failed)
        self.analysis_worker.start()

    def _on_single_analysis_completed(self, report_dict: Dict[str, Any]):
        self.btn_run_analysis.setEnabled(True)
        self.btn_run_analysis.setText("⚡ Run Pipeline Analysis")

        # Ingest updated details into store
        slug = report_dict.get("defillama_slug") or report_dict.get("protocol_name")
        static_analysis = report_dict.get("static_analysis", {})
        sec_intel = report_dict.get("security_intelligence", {})
        repo_info = report_dict.get("repository", {})
        perm_scan = report_dict.get("on_chain_permission_analysis", {})

        update_data = {
            "slug": slug,
            "static_analysis_status": static_analysis.get("status"),
            "slither_execution_backend": static_analysis.get("execution_backend"),
            "slither_version": static_analysis.get("slither_version"),
            "slither_findings_count": static_analysis.get("findings_count"),
            "slither_findings_summary": static_analysis.get("findings_summary", {}),
            "slither_diagnostics": static_analysis.get("diagnostics"),
            "security_intelligence_status": sec_intel.get("status"),
            "security_score": sec_intel.get("security_score"),
            "security_score_grade": sec_intel.get("security_score_grade"),
            "audit_count": sec_intel.get("audit_count"),
            "repository_url": repo_info.get("url"),
            "repository_resolution_status": repo_info.get("resolution_status"),
            "permission_scanner_status": perm_scan.get("status"),
            "permission_scanner_critical_count": perm_scan.get("critical_count"),
            "permission_scanner_findings": perm_scan.get("findings", []),
        }

        rec, _ = self.store.upsert_record(update_data)
        self.store.save()
        self._refresh_table_and_dashboard()
        self._display_protocol_details(rec)

        QMessageBox.information(
            self,
            "Analysis Completed",
            f"Pipeline analysis for '{slug}' finished.\nOverall Status: {report_dict.get('overall_status')}",
        )

    def _on_single_analysis_failed(self, err_msg: str):
        self.btn_run_analysis.setEnabled(True)
        self.btn_run_analysis.setText("⚡ Run Pipeline Analysis")
        QMessageBox.critical(self, "Analysis Failed", f"Deep pipeline analysis failed: {err_msg}")

    def _on_run_single_cycle(self):
        self.btn_quick_fetch.setEnabled(False)
        self.btn_quick_fetch.setText("🔄 Running Cycle...")

        # Run one discovery cycle in HunterWorker with 1 cycle
        cfg = HunterConfig(
            duration_hours=0.001,  # immediate 1-cycle
            cycle_interval_seconds=1,
            min_tvl=self._get_active_min_tvl(),
            max_candidates_per_cycle=30,
            deep_investigation=self.chk_deep.isChecked(),
        )
        self._start_hunter_with_config(cfg, single_cycle=True)

    # -------------------------------------------------------------------------
    # CONTINUOUS DISCOVERY (HUNTER) LOGIC
    # -------------------------------------------------------------------------
    def _on_start_hunter(self):
        duration = self.spin_duration.value()
        interval = self.spin_interval.value()
        min_tvl = self.spin_min_tvl.value()
        max_c = self.spin_max_per_cycle.value()
        deep = self.chk_deep.isChecked()

        cfg = HunterConfig(
            duration_hours=duration,
            cycle_interval_seconds=interval,
            min_tvl=min_tvl,
            max_candidates_per_cycle=max_c,
            deep_investigation=deep,
            resume_session=self.chk_resume.isChecked(),
            session_id=f"hunter-{int(datetime.now(timezone.utc).timestamp())}",
        )
        self._start_hunter_with_config(cfg, single_cycle=False)

    def _start_hunter_with_config(self, cfg: HunterConfig, single_cycle: bool = False):
        self.hunter_worker = HunterWorker(config=cfg, store=self.store, parent=self)

        self.hunter_worker.state_changed.connect(self._on_hunter_state_changed)
        self.hunter_worker.cycle_completed.connect(self._on_hunter_cycle_completed)
        self.hunter_worker.candidate_discovered.connect(self._on_hunter_candidate_discovered)
        self.hunter_worker.metrics_updated.connect(self._on_hunter_metrics_updated)
        self.hunter_worker.log_emitted.connect(self._on_hunter_log)
        self.hunter_worker.error_occurred.connect(self._on_hunter_error)

        self.btn_hunter_start.setEnabled(False)
        self.btn_hunter_pause.setEnabled(True)
        self.btn_hunter_resume.setEnabled(False)
        self.btn_hunter_stop.setEnabled(True)

        self.lbl_hunter_status.setText("Status: RUNNING")
        self.lbl_hunter_status.setStyleSheet("font-weight: bold; color: #3fb950; font-size: 14px;")

        self.hunter_worker.start()

    def _on_pause_hunter(self):
        if self.hunter_worker:
            self.hunter_worker.request_pause()
            self.btn_hunter_pause.setEnabled(False)
            self.btn_hunter_resume.setEnabled(True)
            self.lbl_hunter_status.setText("Status: PAUSED")
            self.lbl_hunter_status.setStyleSheet("font-weight: bold; color: #d29922; font-size: 14px;")

    def _on_resume_hunter(self):
        if self.hunter_worker:
            self.hunter_worker.request_resume()
            self.btn_hunter_pause.setEnabled(True)
            self.btn_hunter_resume.setEnabled(False)
            self.lbl_hunter_status.setText("Status: RUNNING")
            self.lbl_hunter_status.setStyleSheet("font-weight: bold; color: #3fb950; font-size: 14px;")

    def _on_stop_hunter(self):
        if self.hunter_worker:
            self.hunter_worker.request_stop()
            self.lbl_hunter_status.setText("Status: STOPPING...")
            self.lbl_hunter_status.setStyleSheet("font-weight: bold; color: #f85149; font-size: 14px;")

    def _on_hunter_state_changed(self, state_str: str):
        if state_str in ("IDLE", "COMPLETED", "FAILED"):
            self.btn_hunter_start.setEnabled(True)
            self.btn_hunter_pause.setEnabled(False)
            self.btn_hunter_resume.setEnabled(False)
            self.btn_hunter_stop.setEnabled(False)
            self.btn_quick_fetch.setEnabled(True)
            self.btn_quick_fetch.setText("🔄 Run Discovery Cycle")

            color = "#3fb950" if state_str == "COMPLETED" else ("#f85149" if state_str == "FAILED" else "#8b949e")
            self.lbl_hunter_status.setText(f"Status: {state_str}")
            self.lbl_hunter_status.setStyleSheet(f"font-weight: bold; color: {color}; font-size: 14px;")

    def _on_hunter_cycle_completed(self, cycle_num: int, candidates_cnt: int):
        self._refresh_table_and_dashboard()

    def _on_hunter_candidate_discovered(self, record_dict: Dict[str, Any], is_new: bool):
        pass  # Table refreshed per cycle

    def _on_hunter_metrics_updated(self, metrics: Dict[str, Any]):
        elapsed = metrics.get("elapsed_seconds", 0.0)
        h = int(elapsed // 3600)
        m = int((elapsed % 3600) // 60)
        s = int(elapsed % 60)
        self.hm_elapsed.findChild(QLabel, "valueLabel").setText(f"{h:02d}:{m:02d}:{s:02d}")
        self.hm_cycles.findChild(QLabel, "valueLabel").setText(str(metrics.get("cycles_completed", 0)))
        self.hm_examined.findChild(QLabel, "valueLabel").setText(str(metrics.get("protocols_examined", 0)))
        self.hm_added.findChild(QLabel, "valueLabel").setText(str(metrics.get("unique_candidates_added", 0)))
        self.hm_errors.findChild(QLabel, "valueLabel").setText(str(len(metrics.get("recent_errors", []))))

    def _on_hunter_log(self, msg: str):
        self.hunter_log_console.appendPlainText(msg)

    def _on_hunter_error(self, err: str):
        self.hunter_log_console.appendPlainText(f"[ERROR] {err}")

    def _on_ui_tick(self):
        # Keeps status pills current
        pass

    # -------------------------------------------------------------------------
    # REPORTS & EXPORTS
    # -------------------------------------------------------------------------
    def _refresh_reports_list(self):
        report_dirs = [
            self.data_dir / "reports",
            Path("reports") / "pipeline",
            Path("reports"),
        ]

        found_files = []
        for rd in report_dirs:
            if rd.exists():
                for p in rd.glob("*.json"):
                    if "summary" not in p.name.lower():
                        found_files.append(p)

        found_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        self.reports_table.setRowCount(len(found_files))

        for row_idx, fpath in enumerate(found_files):
            self.reports_table.setItem(row_idx, 0, QTableWidgetItem(fpath.name))

            # Try to read protocol name
            proto_name = "Unknown"
            ts_str = datetime.fromtimestamp(fpath.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    rdata = json.load(f)
                    proto_name = rdata.get("protocol_name") or rdata.get("defillama_slug", "Unknown")
            except Exception:
                pass

            self.reports_table.setItem(row_idx, 1, QTableWidgetItem(proto_name))
            self.reports_table.setItem(row_idx, 2, QTableWidgetItem(ts_str))

    def _on_report_selected(self):
        sel = self.reports_table.selectedItems()
        if not sel:
            return
        fname = self.reports_table.item(sel[0].row(), 0).text()

        report_dirs = [
            self.data_dir / "reports",
            Path("reports") / "pipeline",
            Path("reports"),
        ]
        target_file = None
        for rd in report_dirs:
            candidate = rd / fname
            if candidate.exists():
                target_file = candidate
                break

        if target_file:
            try:
                with open(target_file, "r", encoding="utf-8") as f:
                    content = f.read()
                self.report_preview.setPlainText(content)
            except Exception as e:
                self.report_preview.setPlainText(f"Error reading report: {e}")

    def _on_export_database(self):
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Export Discovery Database",
            str(Path.home() / "discovery_database_export.json"),
            "JSON Files (*.json)",
        )
        if dest:
            try:
                all_records = [r.to_dict() for r in self.store.get_all_records()]
                payload = {
                    "schema_version": CURRENT_SCHEMA_VERSION,
                    "metadata": self.store.get_metadata(),
                    "total_records": len(all_records),
                    "exported_at": datetime.now(timezone.utc).isoformat(),
                    "records": all_records,
                }
                with open(dest, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, default=str)
                QMessageBox.information(self, "Export Successful", f"Saved {len(all_records)} records to {dest}")
            except Exception as e:
                QMessageBox.critical(self, "Export Failed", f"Could not export database: {e}")

    def _on_export_filtered_records(self):
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Export Active Filtered Candidates",
            str(Path.home() / "filtered_candidates_export.json"),
            "JSON Files (*.json)",
        )
        if dest:
            try:
                filtered_dicts = [r.to_dict() for r in self._active_filtered_records]
                payload = {
                    "schema_version": CURRENT_SCHEMA_VERSION,
                    "exported_at": datetime.now(timezone.utc).isoformat(),
                    "applied_filters": {
                        "min_tvl": self._get_active_min_tvl(),
                        "chain": self.chain_combo.currentText(),
                        "category": self.cat_combo.currentText(),
                        "search_query": self.search_input.text(),
                    },
                    "total_filtered": len(filtered_dicts),
                    "records": filtered_dicts,
                }
                with open(dest, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, default=str)
                QMessageBox.information(
                    self, "Export Successful", f"Saved {len(filtered_dicts)} filtered records to {dest}"
                )
            except Exception as e:
                QMessageBox.critical(self, "Export Failed", f"Could not export records: {e}")

    def _on_open_data_directory(self):
        path_str = str(self.data_dir)
        if os.name == "nt":
            os.startfile(path_str)
        else:
            subprocess.Popen(["xdg-open", path_str])

    def closeEvent(self, event):
        """Ensures background worker is safely stopped on application exit."""
        if self.hunter_worker and self.hunter_worker.isRunning():
            self.hunter_worker.request_stop()
            self.hunter_worker.wait(3000)
        event.accept()
