"""
Continuous Protocol Discovery Service — Protocol Hunter.

Automated background research service that periodically discovers, filters,
and tracks DeFi protocols across supported EVM networks.
Deduplicates candidates into the versioned JSON DiscoveryStore.
Note: This is protocol discovery and security research, NOT cryptocurrency mining.
No wallet operations, transactions, or token rewards are performed.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from defi_research.discovery.discovery_store import (
    DiscoveryStore,
    DiscoveredProtocolRecord,
    generate_stable_protocol_id,
)
from defi_research.github_repository_resolver import (
    DefiLlamaClient,
    resolve_github_repo_from_slug,
)
from defi_research.market_intelligence import (
    CHAIN_ALIAS_MAP,
    clean_contract_address,
    get_high_value_targets,
)

logger = logging.getLogger("ProtocolHunter")


class HunterState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class HunterConfig:
    """Configuration parameters for the continuous discovery service."""
    duration_hours: float = 1.0           # 0.0 means run indefinitely until stopped
    cycle_interval_seconds: int = 60      # Delay between discovery cycles
    min_tvl: float = 50_000.0             # Minimum TVL in USD
    chains: Optional[List[str]] = None    # Allowed chains (e.g. ['ethereum', 'arbitrum'])
    categories: Optional[List[str]] = None # Filter categories (e.g. ['Lending', 'DEX'])
    deep_investigation: bool = False      # Whether to resolve GitHub repositories
    max_candidates_per_cycle: int = 25    # Max candidates to inspect/ingest per cycle
    resume_session: bool = True           # Whether to append to existing discovery store
    session_id: Optional[str] = None


@dataclass
class HunterMetrics:
    """Real-time metrics calculated from actual discovery execution state."""
    state: HunterState = HunterState.IDLE
    started_at: Optional[str] = None
    elapsed_seconds: float = 0.0
    cycles_completed: int = 0
    protocols_examined: int = 0
    unique_candidates_added: int = 0
    candidates_matching_filters: int = 0
    pending_deeper_analysis: int = 0
    recent_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "started_at": self.started_at,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "cycles_completed": self.cycles_completed,
            "protocols_examined": self.protocols_examined,
            "unique_candidates_added": self.unique_candidates_added,
            "candidates_matching_filters": self.candidates_matching_filters,
            "pending_deeper_analysis": self.pending_deeper_analysis,
            "recent_errors": self.recent_errors[-10:],
        }


class ProtocolHunter:
    """
    Background discovery engine that coordinates periodic queries,
    applies filters, and incrementally updates the DiscoveryStore.
    """

    def __init__(
        self,
        config: Optional[HunterConfig] = None,
        store: Optional[DiscoveryStore] = None,
        client: Optional[DefiLlamaClient] = None,
    ):
        self.config = config or HunterConfig()
        self.store = store or DiscoveryStore()
        self.client = client or DefiLlamaClient()
        self.metrics = HunterMetrics()

        self._state = HunterState.IDLE
        self._stop_requested = threading.Event()
        self._pause_requested = threading.Event()
        self._lock = threading.RLock()

        # Callbacks for GUI or observer hooks
        self.on_state_changed: Optional[Callable[[HunterState], None]] = None
        self.on_cycle_completed: Optional[Callable[[int, int], None]] = None
        self.on_candidate_discovered: Optional[Callable[[DiscoveredProtocolRecord, bool], None]] = None
        self.on_log_message: Optional[Callable[[str], None]] = None

    @property
    def state(self) -> HunterState:
        with self._lock:
            return self._state

    def _set_state(self, new_state: HunterState) -> None:
        with self._lock:
            self._state = new_state
            self.metrics.state = new_state
        if self.on_state_changed:
            try:
                self.on_state_changed(new_state)
            except Exception as e:
                logger.error(f"Error in on_state_changed callback: {e}")

    def _log(self, msg: str) -> None:
        logger.info(msg)
        if self.on_log_message:
            try:
                self.on_log_message(msg)
            except Exception:
                pass

    def stop(self) -> None:
        """Signals the background discovery process to terminate safely."""
        with self._lock:
            if self._state in (HunterState.RUNNING, HunterState.PAUSED):
                self._stop_requested.set()
                self._set_state(HunterState.STOPPING)
                self._log("[Hunter] Cancellation requested. Stopping after current operation...")

    def pause(self) -> None:
        """Pauses the discovery loop after the current cycle."""
        with self._lock:
            if self._state == HunterState.RUNNING:
                self._pause_requested.set()
                self._set_state(HunterState.PAUSED)
                self._log("[Hunter] Discovery paused.")

    def resume(self) -> None:
        """Resumes a paused discovery loop."""
        with self._lock:
            if self._state == HunterState.PAUSED:
                self._pause_requested.clear()
                self._set_state(HunterState.RUNNING)
                self._log("[Hunter] Discovery resumed.")

    def run_discovery_cycle(self) -> int:
        """
        Executes a single end-to-end discovery and filtering cycle.
        Returns the number of matching candidates processed in this cycle.
        """
        self._log(f"[Hunter] Starting discovery cycle #{self.metrics.cycles_completed + 1}...")

        # 1. Fetch raw protocols from DefiLlama
        try:
            raw_protocols = self.client.get_protocols()
        except Exception as e:
            err_msg = f"Failed to fetch DefiLlama protocols: {e}"
            logger.warning(err_msg)
            self.metrics.recent_errors.append(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {err_msg}")
            return 0

        if not raw_protocols:
            self._log("[Hunter] No protocols returned from DefiLlama.")
            return 0

        self.metrics.protocols_examined += len(raw_protocols)

        # 2. Filter candidates based on active config
        allowed_chains = (
            [CHAIN_ALIAS_MAP.get(c.lower(), c.lower()) for c in self.config.chains]
            if self.config.chains
            else None
        )
        allowed_categories = (
            [cat.lower() for cat in self.config.categories]
            if self.config.categories
            else None
        )

        matched_batch: List[Dict[str, Any]] = []

        for p in raw_protocols:
            if not isinstance(p, dict):
                continue

            tvl = p.get("tvl")
            if tvl is None or not isinstance(tvl, (int, float)) or tvl < self.config.min_tvl:
                continue

            # Category filter
            cat = p.get("category", "")
            if allowed_categories and cat.lower() not in allowed_categories:
                continue

            # Chain filter
            p_chain = p.get("chain", "unknown").lower()
            p_chains = [str(c).lower() for c in p.get("chains", [])]
            if allowed_chains:
                matches_chain = (p_chain in allowed_chains) or bool(set(p_chains) & set(allowed_chains))
                if not matches_chain:
                    continue

            matched_batch.append(p)
            if len(matched_batch) >= self.config.max_candidates_per_cycle:
                break

        self.metrics.candidates_matching_filters += len(matched_batch)
        self._log(f"[Hunter] Found {len(matched_batch)} candidates matching criteria.")

        # 3. Process matched candidates and upsert into store
        candidates_added_this_cycle = 0

        for p in matched_batch:
            if self._stop_requested.is_set():
                break

            name = p.get("name", "Unknown")
            slug = p.get("slug", "")
            raw_addr = p.get("address")
            clean_addr = clean_contract_address(raw_addr)
            chain = p.get("chain", "unknown").lower()

            record_data: Dict[str, Any] = {
                "name": name,
                "slug": slug,
                "discovery_source": "defillama",
                "chain": chain,
                "chains": [str(c) for c in p.get("chains", [])],
                "address": clean_addr,
                "contracts": [clean_addr] if clean_addr else [],
                "category": p.get("category"),
                "tvl": float(p.get("tvl", 0.0)),
                "tvl_timestamp": datetime.now(timezone.utc).isoformat(),
                "applied_filter_config": {
                    "min_tvl": self.config.min_tvl,
                    "chains": self.config.chains,
                    "categories": self.config.categories,
                },
                "session_id": self.config.session_id,
            }

            # Optional deep investigation: Repository resolution
            if self.config.deep_investigation and slug:
                try:
                    res = resolve_github_repo_from_slug(slug, client=self.client)
                    if isinstance(res, dict):
                        record_data["repository_url"] = res.get("repository_url")
                        record_data["repository_resolution_status"] = res.get("repository_resolution_status")
                        conf = res.get("repository_confidence")
                        record_data["repository_confidence_score"] = 0.95 if conf == "HIGH" else (0.5 if conf == "LOW" else 0.0)
                        record_data["repository_confidence_reason"] = res.get("repository_resolution_reason")
                    else:
                        record_data["repository_url"] = getattr(res, "repo_url", None)
                        status_val = getattr(res, "status", None)
                        record_data["repository_resolution_status"] = getattr(status_val, "value", str(status_val)) if status_val else None
                        record_data["repository_confidence_score"] = getattr(res, "confidence_score", None)
                        record_data["repository_confidence_reason"] = getattr(res, "reason", None)
                except Exception as res_err:
                    self._log(f"[Hunter] Repo resolution error for {slug}: {res_err}")

            record, is_new = self.store.upsert_record(record_data)

            if is_new:
                candidates_added_this_cycle += 1
                self.metrics.unique_candidates_added += 1

            if not record.static_analysis_status:
                self.metrics.pending_deeper_analysis += 1

            if self.on_candidate_discovered:
                try:
                    self.on_candidate_discovered(record, is_new)
                except Exception as cb_err:
                    logger.error(f"Error in on_candidate_discovered callback: {cb_err}")

        # 4. Save store incrementally after each cycle
        iso_now = datetime.now(timezone.utc).isoformat()
        self.metrics.cycles_completed += 1
        self.store.update_session_metadata(
            session_id=self.config.session_id,
            increment_cycle=True,
            last_cycle_at=iso_now,
        )
        self.store.save()

        self._log(
            f"[Hunter] Cycle #{self.metrics.cycles_completed} completed. "
            f"New candidates: {candidates_added_this_cycle}, Total in store: {self.store.total_count}."
        )

        if self.on_cycle_completed:
            try:
                self.on_cycle_completed(self.metrics.cycles_completed, len(matched_batch))
            except Exception as cb_err:
                logger.error(f"Error in on_cycle_completed callback: {cb_err}")

        return len(matched_batch)

    def start(self) -> None:
        """
        Runs the continuous discovery loop until max duration, stop signal, or error.
        Designed to be called on a background thread.
        """
        self._stop_requested.clear()
        self._pause_requested.clear()
        self._set_state(HunterState.RUNNING)

        start_time = time.time()
        self.metrics.started_at = datetime.now(timezone.utc).isoformat()
        max_duration_seconds = self.config.duration_hours * 3600.0 if self.config.duration_hours > 0 else float("inf")

        self._log(
            f"[Hunter] Continuous discovery service started. "
            f"Duration: {self.config.duration_hours:.1f}h | "
            f"Interval: {self.config.cycle_interval_seconds}s | "
            f"Min TVL: ${self.config.min_tvl:,.2f}"
        )

        try:
            while not self._stop_requested.is_set():
                # Check pause state
                while self._pause_requested.is_set() and not self._stop_requested.is_set():
                    time.sleep(1.0)
                    self.metrics.elapsed_seconds = time.time() - start_time

                if self._stop_requested.is_set():
                    break

                # Execute one cycle
                self.run_discovery_cycle()

                self.metrics.elapsed_seconds = time.time() - start_time

                # Check duration limit
                if (time.time() - start_time) >= max_duration_seconds:
                    self._log(f"[Hunter] Configured duration ({self.config.duration_hours}h) reached. Finishing session.")
                    self._set_state(HunterState.COMPLETED)
                    return

                # Wait for next cycle in short increments to allow fast stop response
                wait_remaining = float(self.config.cycle_interval_seconds)
                while wait_remaining > 0 and not self._stop_requested.is_set():
                    sleep_chunk = min(1.0, wait_remaining)
                    time.sleep(sleep_chunk)
                    wait_remaining -= sleep_chunk
                    self.metrics.elapsed_seconds = time.time() - start_time

            # Finished cleanly via stop
            self._set_state(HunterState.IDLE)
            self._log("[Hunter] Discovery stopped safely.")

        except Exception as e:
            err_msg = f"Fatal error in continuous discovery service: {e}"
            logger.error(err_msg, exc_info=True)
            self.metrics.recent_errors.append(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {err_msg}")
            self._set_state(HunterState.FAILED)
