"""
Reusable UI Components for DeFi Research Desktop Application.
Provides consistent cards, badges, empty states, and secure password inputs.
"""

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from defi_research.gui.theme import (
    COLOR_BORDER,
    COLOR_CARD,
    COLOR_CYAN_ACCENT,
    COLOR_DANGER,
    COLOR_NEUTRAL,
    COLOR_SUCCESS,
    COLOR_SURFACE,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
    COLOR_WARNING,
)


class StatCardWidget(QFrame):
    """
    Standardized, clean statistical card widget.
    Presents a title, prominent metric value, and subtle accent.
    """

    def __init__(
        self,
        title: str,
        value: str = "0",
        accent_color: str = COLOR_CYAN_ACCENT,
        sublabel: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("cardFrame")
        self.setFrameShape(QFrame.StyledPanel)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setMinimumHeight(84)

        # Subtle left border in accent color
        self.setStyleSheet(f"""
            QFrame#cardFrame {{
                background-color: {COLOR_CARD};
                border: 1px solid {COLOR_BORDER};
                border-left: 4px solid {accent_color};
                border-radius: 8px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(3)

        self.title_lbl = QLabel(title.upper())
        self.title_lbl.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.5px;")
        layout.addWidget(self.title_lbl)

        self.value_lbl = QLabel(value)
        self.value_lbl.setObjectName("valueLabel")
        val_font = QFont()
        val_font.setPointSize(18)
        val_font.setBold(True)
        self.value_lbl.setFont(val_font)
        self.value_lbl.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-weight: 800;")
        layout.addWidget(self.value_lbl)

        if sublabel:
            self.sub_lbl = QLabel(sublabel)
            self.sub_lbl.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 11px;")
            layout.addWidget(self.sub_lbl)
        else:
            self.sub_lbl = None

    def set_value(self, val: str):
        self.value_lbl.setText(val)

    def set_sublabel(self, sub: str):
        if self.sub_lbl:
            self.sub_lbl.setText(sub)


class StatusBadgeWidget(QLabel):
    """
    Color-coded status badge with restrained background and border.
    """

    VARIANT_COLORS = {
        "success": (COLOR_SUCCESS, "#052e16", "#14532d"),
        "warning": (COLOR_WARNING, "#451a03", "#78350f"),
        "danger": (COLOR_DANGER, "#450a0a", "#7f1d1d"),
        "cyan": (COLOR_CYAN_ACCENT, "#082f49", "#0369a1"),
        "neutral": (COLOR_TEXT_SECONDARY, "#0f172a", "#1e293b"),
    }

    def __init__(self, text: str = "", variant: str = "neutral", parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.set_variant(variant, text)

    def set_variant(self, variant: str, text: Optional[str] = None):
        if text is not None:
            self.setText(text)
        fg, bg, border = self.VARIANT_COLORS.get(variant, self.VARIANT_COLORS["neutral"])
        self.setStyleSheet(f"""
            QLabel {{
                background-color: {bg};
                color: {fg};
                border: 1px solid {border};
                border-radius: 10px;
                padding: 2px 10px;
                font-size: 11px;
                font-weight: 700;
            }}
        """)


class EmptyStateWidget(QWidget):
    """
    Visual empty state placeholder for tables or details.
    """

    def __init__(self, icon_text: str, title: str, description: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setContentsMargins(30, 40, 30, 40)
        layout.setSpacing(8)

        lbl_icon = QLabel(icon_text)
        lbl_icon.setAlignment(Qt.AlignCenter)
        lbl_icon.setStyleSheet(f"font-size: 32px; color: {COLOR_TEXT_MUTED};")
        layout.addWidget(lbl_icon)

        lbl_title = QLabel(title)
        lbl_title.setAlignment(Qt.AlignCenter)
        lbl_title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {COLOR_TEXT_PRIMARY};")
        layout.addWidget(lbl_title)

        lbl_desc = QLabel(description)
        lbl_desc.setAlignment(Qt.AlignCenter)
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_MUTED}; max-width: 400px;")
        layout.addWidget(lbl_desc)


class PasswordLineEdit(QWidget):
    """
    Secure password input with an eye toggle button to view/mask secrets.
    """

    textChanged = Signal(str)

    def __init__(self, placeholder: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText(placeholder)
        self.input_edit.setEchoMode(QLineEdit.Password)
        self.input_edit.textChanged.connect(self.textChanged.emit)
        layout.addWidget(self.input_edit)

        self.btn_toggle = QPushButton("👁️")
        self.btn_toggle.setFixedSize(32, 32)
        self.btn_toggle.setToolTip("Show / Hide secret key")
        self.btn_toggle.clicked.connect(self._toggle_echo)
        layout.addWidget(self.btn_toggle)

    def _toggle_echo(self):
        if self.input_edit.echoMode() == QLineEdit.Password:
            self.input_edit.setEchoMode(QLineEdit.Normal)
            self.btn_toggle.setText("🔒")
        else:
            self.input_edit.setEchoMode(QLineEdit.Password)
            self.btn_toggle.setText("👁️")

    def text(self) -> str:
        return self.input_edit.text()

    def setText(self, text: str):
        self.input_edit.setText(text)
