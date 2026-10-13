"""
Modern Dark Theme Styling for DeFi Protocol Research Desktop Application.
"""

DARK_THEME_QSS = """
QMainWindow, QDialog {
    background-color: #0d1117;
    color: #c9d1d9;
    font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
}

QWidget {
    color: #c9d1d9;
    background-color: transparent;
}

QTabWidget::pane {
    border: 1px solid #30363d;
    background-color: #0d1117;
    border-radius: 6px;
    margin-top: -1px;
}

QTabBar::tab {
    background-color: #161b22;
    color: #8b949e;
    border: 1px solid #30363d;
    border-bottom: none;
    padding: 8px 18px;
    margin-right: 4px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    font-weight: 600;
}

QTabBar::tab:selected {
    background-color: #0d1117;
    color: #58a6ff;
    border-bottom: 2px solid #58a6ff;
}

QTabBar::tab:hover:!selected {
    background-color: #21262d;
    color: #c9d1d9;
}

QGroupBox {
    border: 1px solid #30363d;
    border-radius: 6px;
    margin-top: 12px;
    padding: 14px;
    background-color: #161b22;
    font-weight: bold;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 4px;
    color: #58a6ff;
}

QLabel {
    color: #c9d1d9;
}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #0d1117;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 6px 10px;
    color: #f0f6fc;
    selection-background-color: #1f6feb;
}

QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1px solid #58a6ff;
}

QComboBox::drop-down {
    border: none;
    width: 20px;
}

QComboBox QAbstractItemView {
    background-color: #161b22;
    border: 1px solid #30363d;
    selection-background-color: #1f6feb;
    color: #c9d1d9;
}

QPushButton {
    background-color: #21262d;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 7px 16px;
    color: #c9d1d9;
    font-weight: 600;
}

QPushButton:hover {
    background-color: #30363d;
    border-color: #8b949e;
    color: #f0f6fc;
}

QPushButton:pressed {
    background-color: #161b22;
}

QPushButton:disabled {
    background-color: #161b22;
    border-color: #21262d;
    color: #484f58;
}

QPushButton#primaryBtn {
    background-color: #238636;
    border-color: #2ea043;
    color: #ffffff;
}

QPushButton#primaryBtn:hover {
    background-color: #2ea043;
}

QPushButton#dangerBtn {
    background-color: #da3633;
    border-color: #f85149;
    color: #ffffff;
}

QPushButton#dangerBtn:hover {
    background-color: #b62324;
}

QTableWidget {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    gridline-color: #21262d;
    color: #c9d1d9;
    selection-background-color: #1f6feb;
    selection-color: #ffffff;
}

QHeaderView::section {
    background-color: #0d1117;
    color: #8b949e;
    padding: 6px;
    border: none;
    border-right: 1px solid #21262d;
    border-bottom: 1px solid #30363d;
    font-weight: 600;
}

QTableWidget QTableCornerButton::section {
    background-color: #0d1117;
    border: none;
}

QTextEdit, QPlainTextEdit {
    background-color: #0d1117;
    border: 1px solid #30363d;
    border-radius: 6px;
    color: #7ee787;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 12px;
    padding: 6px;
}

QScrollBar:vertical {
    border: none;
    background: #0d1117;
    width: 10px;
    margin: 0px;
}

QScrollBar::handle:vertical {
    background: #30363d;
    min-height: 20px;
    border-radius: 5px;
}

QScrollBar::handle:vertical:hover {
    background: #8b949e;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    border: none;
    background: #0d1117;
    height: 10px;
    margin: 0px;
}

QScrollBar::handle:horizontal {
    background: #30363d;
    min-width: 20px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal:hover {
    background: #8b949e;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}

QProgressBar {
    border: 1px solid #30363d;
    border-radius: 6px;
    text-align: center;
    color: #ffffff;
    background-color: #0d1117;
    height: 14px;
}

QProgressBar::chunk {
    background-color: #238636;
    border-radius: 5px;
}

QCheckBox {
    color: #c9d1d9;
    spacing: 6px;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #30363d;
    border-radius: 4px;
    background-color: #0d1117;
}

QCheckBox::indicator:checked {
    background-color: #1f6feb;
    border-color: #58a6ff;
}
"""
