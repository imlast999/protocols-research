"""
Application launcher and entry point for the PySide6 desktop GUI.
"""

import argparse
import logging
import os
from pathlib import Path
import sys
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from defi_research.discovery.discovery_store import DiscoveryStore, get_default_data_dir
from defi_research.gui.main_window import MainWindow

logger = logging.getLogger("DeFiResearchApp")


def setup_exception_logging():
    """Configures global excepthook to persist unhandled exceptions to disk."""
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        log_dir = get_default_data_dir() / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / "crash.log"
            with open(log_file, "a", encoding="utf-8") as f:
                import datetime
                import traceback
                f.write(f"\n--- Crash Report [{datetime.datetime.now().isoformat()}] ---\n")
                traceback.print_exception(exc_type, exc_value, exc_traceback, file=f)
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    sys.excepthook = handle_exception


def create_application() -> QApplication:
    """Initializes and configures the Qt application instance."""
    setup_exception_logging()
    app = QApplication.instance()
    if app is None:
        # High DPI scaling attributes are handled natively in Qt 6
        app = QApplication(sys.argv)

    app.setApplicationName("DeFi Protocol Research Suite")
    app.setApplicationDisplayName("DeFi Protocol Research Suite — Protocol Hunter")
    app.setOrganizationName("DeFi Security Research")
    return app


def launch_gui(
    db_path: Optional[str] = None,
    data_dir: Optional[str] = None,
    headless: bool = False,
) -> int:
    """
    Launches the main window.
    If headless is True (e.g. in automated tests), creates the window without entering
    the blocking Qt event loop.
    """
    if data_dir:
        os.environ["DEFI_RESEARCH_DATA_DIR"] = str(Path(data_dir).resolve())

    app = create_application()

    store = DiscoveryStore(db_path=Path(db_path) if db_path else None)
    window = MainWindow(store=store)

    if not headless:
        window.show()
        return app.exec()
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="DeFi Protocol Research Suite — Desktop Application"
    )
    parser.add_argument(
        "--data-dir",
        help="Custom user-data directory for persistent discovery storage and reports",
    )
    parser.add_argument(
        "--db-path",
        help="Direct path to custom discovery database JSON file",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run GUI in headless mode for verification and testing",
    )
    args = parser.parse_args()

    sys.exit(launch_gui(db_path=args.db_path, data_dir=args.data_dir, headless=args.headless))


if __name__ == "__main__":
    main()
