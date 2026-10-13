"""
Professional Dark Navy & Cyan Design System for DeFi Research Suite.
Tailored for information density, contrast, and visual hierarchy.
"""

DARK_THEME_QSS = """
/* =========================================================================
   GLOBAL APPLICATION STYLES
   ========================================================================= */
QMainWindow, QDialog {
    background-color: #0b0f19;
    color: #e2e8f0;
    font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, "Inter", Roboto, Arial, sans-serif;
    font-size: 13px;
}

QWidget {
    color: #cbd5e1;
    background-color: transparent;
}

/* Tooltips */
QToolTip {
    background-color: #1e293b;
    color: #f8fafc;
    border: 1px solid #334155;
    border-radius: 4px;
    padding: 6px 10px;
    font-size: 12px;
}

/* =========================================================================
   NAVIGATION TABS
   ========================================================================= */
QTabWidget::pane {
    border: 1px solid #1e293b;
    background-color: #0b0f19;
    border-radius: 8px;
    margin-top: -1px;
}

QTabBar::tab {
    background-color: #111827;
    color: #94a3b8;
    border: 1px solid #1e293b;
    border-bottom: none;
    padding: 10px 20px;
    margin-right: 4px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    font-weight: 600;
    font-size: 13px;
}

QTabBar::tab:selected {
    background-color: #0f172a;
    color: #38bdf8;
    border-top: 2px solid #0ea5e9;
    border-bottom: 2px solid #0f172a;
}

QTabBar::tab:hover:!selected {
    background-color: #1e293b;
    color: #f1f5f9;
}

/* =========================================================================
   SURFACES & CONTAINERS
   ========================================================================= */
QGroupBox {
    border: 1px solid #1e293b;
    border-radius: 8px;
    margin-top: 14px;
    padding: 16px 14px 14px 14px;
    background-color: #0f172a;
    font-weight: 600;
    font-size: 12px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 6px;
    color: #38bdf8;
    background-color: #0f172a;
    border-radius: 4px;
}

QFrame#cardFrame {
    background-color: #111827;
    border: 1px solid #1e293b;
    border-radius: 8px;
}

QFrame#cardFrame:hover {
    border-color: #334155;
}

/* =========================================================================
   FORM CONTROLS & INPUTS
   ========================================================================= */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #090d16;
    border: 1px solid #1e293b;
    border-radius: 6px;
    padding: 7px 11px;
    color: #f8fafc;
    selection-background-color: #0284c7;
    font-size: 13px;
}

QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1px solid #38bdf8;
    background-color: #0b1120;
}

QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
    background-color: #0f172a;
    color: #64748b;
    border-color: #1e293b;
}

QComboBox::drop-down {
    border: none;
    width: 24px;
}

QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #94a3b8;
    width: 0;
    height: 0;
    margin-right: 8px;
}

QComboBox QAbstractItemView {
    background-color: #111827;
    border: 1px solid #334155;
    border-radius: 6px;
    selection-background-color: #0284c7;
    selection-color: #ffffff;
    color: #e2e8f0;
    padding: 4px;
}

/* =========================================================================
   BUTTONS
   ========================================================================= */
QPushButton {
    background-color: #1e293b;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 7px 16px;
    color: #e2e8f0;
    font-weight: 600;
    font-size: 13px;
}

QPushButton:hover {
    background-color: #334155;
    border-color: #475569;
    color: #f8fafc;
}

QPushButton:pressed {
    background-color: #0f172a;
}

QPushButton:disabled {
    background-color: #0f172a;
    border-color: #1e293b;
    color: #475569;
}

/* Primary Action Button (Teal / Cyan Accent) */
QPushButton#primaryBtn {
    background-color: #0284c7;
    border: 1px solid #0369a1;
    color: #ffffff;
}

QPushButton#primaryBtn:hover {
    background-color: #0ea5e9;
    border-color: #38bdf8;
}

QPushButton#primaryBtn:pressed {
    background-color: #0369a1;
}

/* Success Action Button (Green) */
QPushButton#successBtn {
    background-color: #15803d;
    border: 1px solid #166534;
    color: #ffffff;
}

QPushButton#successBtn:hover {
    background-color: #16a34a;
}

/* Danger / Stop Button (Red) */
QPushButton#dangerBtn {
    background-color: #b91c1c;
    border: 1px solid #991b1b;
    color: #ffffff;
}

QPushButton#dangerBtn:hover {
    background-color: #dc2626;
}

/* Warning / Pause Button (Amber) */
QPushButton#warningBtn {
    background-color: #b45309;
    border: 1px solid #92400e;
    color: #ffffff;
}

QPushButton#warningBtn:hover {
    background-color: #d97706;
}

/* Status Pill Buttons (Header) */
QPushButton#statusPill {
    background-color: #111827;
    border: 1px solid #1e293b;
    border-radius: 12px;
    padding: 4px 12px;
    font-size: 11px;
    font-weight: 600;
}

/* =========================================================================
   DATA TABLES
   ========================================================================= */
QTableWidget {
    background-color: #090d16;
    border: 1px solid #1e293b;
    border-radius: 8px;
    gridline-color: #111827;
    color: #cbd5e1;
    selection-background-color: #0369a1;
    selection-color: #ffffff;
    alternate-background-color: #0c1322;
}

QHeaderView::section {
    background-color: #0f172a;
    color: #94a3b8;
    padding: 8px 10px;
    border: none;
    border-right: 1px solid #1e293b;
    border-bottom: 1px solid #1e293b;
    font-weight: 600;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

QHeaderView::section:hover {
    background-color: #1e293b;
    color: #38bdf8;
}

QTableWidget QTableCornerButton::section {
    background-color: #0f172a;
    border: none;
}

/* =========================================================================
   TEXT EDITORS & LOGS
   ========================================================================= */
QTextEdit, QPlainTextEdit {
    background-color: #060911;
    border: 1px solid #1e293b;
    border-radius: 6px;
    color: #e2e8f0;
    font-family: "Cascadia Code", "Consolas", "Courier New", monospace;
    font-size: 12px;
    padding: 8px;
    line-height: 1.5;
}

QTextEdit:focus, QPlainTextEdit:focus {
    border-color: #38bdf8;
}

/* =========================================================================
   SCROLLBARS
   ========================================================================= */
QScrollBar:vertical {
    border: none;
    background: #090d16;
    width: 10px;
    margin: 0px;
}

QScrollBar::handle:vertical {
    background: #1e293b;
    min-height: 24px;
    border-radius: 5px;
}

QScrollBar::handle:vertical:hover {
    background: #334155;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    border: none;
    background: #090d16;
    height: 10px;
    margin: 0px;
}

QScrollBar::handle:horizontal {
    background: #1e293b;
    min-width: 24px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal:hover {
    background: #334155;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}

/* =========================================================================
   PROGRESS BARS & CHECKBOXES
   ========================================================================= */
QProgressBar {
    border: 1px solid #1e293b;
    border-radius: 6px;
    text-align: center;
    color: #ffffff;
    background-color: #090d16;
    height: 16px;
    font-size: 11px;
    font-weight: 600;
}

QProgressBar::chunk {
    background-color: #0284c7;
    border-radius: 5px;
}

QCheckBox {
    color: #cbd5e1;
    spacing: 8px;
    font-size: 13px;
}

QCheckBox::indicator {
    width: 17px;
    height: 17px;
    border: 1px solid #334155;
    border-radius: 4px;
    background-color: #090d16;
}

QCheckBox::indicator:hover {
    border-color: #38bdf8;
}

QCheckBox::indicator:checked {
    background-color: #0284c7;
    border-color: #38bdf8;
}

/* Splitters */
QSplitter::handle {
    background-color: #1e293b;
}

QSplitter::handle:horizontal {
    width: 4px;
}

QSplitter::handle:vertical {
    height: 4px;
}

QSplitter::handle:hover {
    background-color: #0ea5e9;
}
"""

# Color Palette Constants for Python-side dynamic badge formatting
COLOR_BG_DARK = "#0b0f19"
COLOR_BG_MAIN = "#0b0f19"
COLOR_SURFACE = "#0f172a"
COLOR_CARD = "#111827"
COLOR_BORDER = "#1e293b"
COLOR_BORDER_LIGHT = "#334155"

COLOR_TEXT_PRIMARY = "#f8fafc"
COLOR_TEXT_SECONDARY = "#94a3b8"
COLOR_TEXT_MUTED = "#64748b"

COLOR_CYAN_ACCENT = "#38bdf8"
COLOR_CYAN_PRIMARY = "#0284c7"

COLOR_SUCCESS = "#22c55e"
COLOR_SUCCESS_BG = "#14532d"
COLOR_WARNING = "#f59e0b"
COLOR_WARNING_BG = "#78350f"
COLOR_DANGER = "#ef4444"
COLOR_DANGER_BG = "#7f1d1d"
COLOR_NEUTRAL = "#64748b"
COLOR_NEUTRAL_BG = "#1e293b"
