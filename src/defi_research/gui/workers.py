"""
Background Qt Workers for Asynchronous Execution in PySide6 GUI.
Ensures network requests, continuous discovery, and analysis runs
never block the main GUI thread.
"""

from typing import Any, Dict, Optional
from PySide6.QtCore import QObject, QThread, Signal

from defi_research.discovery.discovery_store import DiscoveredProtocolRecord, DiscoveryStore
from defi_research.discovery.protocol_hunter import (
    HunterConfig,
    HunterState,
    ProtocolHunter,
)
from defi_research.pipeline import run_protocol_pipeline


class HunterWorker(QThread):
    """
    Background worker thread running continuous protocol discovery.
    Emits Qt signals to safely update GUI controls and tables.
    """

    state_changed = Signal(str)
    cycle_completed = Signal(int, int)  # cycle_num, candidates_count
    candidate_discovered = Signal(dict, bool)  # record_dict, is_new
    metrics_updated = Signal(dict)
    log_emitted = Signal(str)
    error_occurred = Signal(str)

    def __init__(
        self,
        config: HunterConfig,
        store: DiscoveryStore,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.config = config
        self.store = store
        self.hunter = ProtocolHunter(config=self.config, store=self.store)

        # Wire up hunter internal callbacks to emit Qt signals
        self.hunter.on_state_changed = self._handle_state_changed
        self.hunter.on_cycle_completed = self._handle_cycle_completed
        self.hunter.on_candidate_discovered = self._handle_candidate_discovered
        self.hunter.on_log_message = self._handle_log_message

    def _handle_state_changed(self, state: HunterState) -> None:
        self.state_changed.emit(state.value)
        self.metrics_updated.emit(self.hunter.metrics.to_dict())

    def _handle_cycle_completed(self, cycle_num: int, candidates_count: int) -> None:
        self.cycle_completed.emit(cycle_num, candidates_count)
        self.metrics_updated.emit(self.hunter.metrics.to_dict())

    def _handle_candidate_discovered(
        self, record: DiscoveredProtocolRecord, is_new: bool
    ) -> None:
        self.candidate_discovered.emit(record.to_dict(), is_new)
        self.metrics_updated.emit(self.hunter.metrics.to_dict())

    def _handle_log_message(self, msg: str) -> None:
        self.log_emitted.emit(msg)

    def run(self) -> None:
        """Executes the hunter continuous discovery loop on the background thread."""
        try:
            self.hunter.start()
        except Exception as e:
            self.error_occurred.emit(str(e))
        finally:
            self.state_changed.emit(self.hunter.state.value)
            self.metrics_updated.emit(self.hunter.metrics.to_dict())

    def request_stop(self) -> None:
        """Thread-safe cancellation request."""
        self.hunter.stop()

    def request_pause(self) -> None:
        """Thread-safe pause request."""
        self.hunter.pause()

    def request_resume(self) -> None:
        """Thread-safe resume request."""
        self.hunter.resume()


class SingleAnalysisWorker(QThread):
    """
    Worker for running an on-demand deep pipeline analysis on a single selected protocol.
    """

    analysis_started = Signal(str)
    analysis_completed = Signal(dict)
    analysis_failed = Signal(str)

    def __init__(
        self,
        protocol_data: Dict[str, Any],
        slither_backend: str = "auto",
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.protocol_data = protocol_data
        self.slither_backend = slither_backend

    def run(self) -> None:
        slug = self.protocol_data.get("slug") or self.protocol_data.get("name", "unknown")
        self.analysis_started.emit(slug)
        try:
            report = run_protocol_pipeline(
                protocol_input=self.protocol_data,
                slither_backend=self.slither_backend,
            )
            self.analysis_completed.emit(report.to_dict())
        except Exception as e:
            self.analysis_failed.emit(str(e))
