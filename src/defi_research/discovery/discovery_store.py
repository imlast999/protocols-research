"""
Versioned JSON Persistence Store for Continuous DeFi Protocol Discovery.

Provides thread-safe, atomic disk writes, automatic backup rotation,
corruption recovery, and schema versioning for discovered protocol candidates.
Ensures persistent user data is stored safely in the user's writable data directory.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("DiscoveryStore")

CURRENT_SCHEMA_VERSION = "1.0.0"


def get_default_data_dir() -> Path:
    """
    Returns the writable user-data directory for the application.
    Separates persistent user state from the potentially read-only
    application or PyInstaller bundle directory.
    """
    override = os.environ.get("DEFI_RESEARCH_DATA_DIR")
    if override:
        path = Path(override).resolve()
    elif os.name == "nt":
        app_data = os.environ.get("APPDATA")
        if app_data:
            path = Path(app_data) / "DeFiResearch"
        else:
            path = Path.home() / ".defi_research"
    else:
        path = Path.home() / ".defi_research"

    path.mkdir(parents=True, exist_ok=True)
    return path


def generate_stable_protocol_id(
    slug: Optional[str] = None,
    name: Optional[str] = None,
    chain: Optional[str] = None,
    address: Optional[str] = None,
) -> str:
    """
    Generates a deterministic, stable identifier for a protocol candidate.
    Prioritizes DefiLlama slug, then chain-qualified address, then normalized name.
    """
    if slug and slug.strip():
        return slug.strip().lower()

    if address and address.strip():
        addr_clean = address.strip().lower()
        chain_clean = (chain or "unknown").strip().lower()
        return f"{chain_clean}:{addr_clean}"

    if name and name.strip():
        # Normalized slug-like string
        norm = "".join(c if c.isalnum() else "-" for c in name.strip().lower())
        return norm.strip("-")

    return f"unknown-{int(datetime.now(timezone.utc).timestamp())}"


@dataclass
class DiscoveredProtocolRecord:
    """
    Structured model for a discovered protocol candidate.
    Preserves market data, repository resolution, static analysis,
    security intelligence, and heuristic permission scan results.
    """
    protocol_id: str
    name: str
    slug: str
    discovery_source: str = "defillama"
    chain: Optional[str] = None
    chains: List[str] = field(default_factory=list)
    address: Optional[str] = None
    contracts: List[str] = field(default_factory=list)
    category: Optional[str] = None
    tvl: Optional[float] = None
    tvl_timestamp: Optional[str] = None
    applied_filter_config: Dict[str, Any] = field(default_factory=dict)

    # Repository resolution details
    repository_url: Optional[str] = None
    repository_resolution_status: Optional[str] = None
    repository_confidence_score: Optional[float] = None
    repository_confidence_reason: Optional[str] = None

    # Source verification details
    source_verification_status: Optional[str] = None
    analyzed_commit: Optional[str] = None

    # Slither static analysis details
    static_analysis_status: Optional[str] = None
    slither_version: Optional[str] = None
    slither_execution_backend: Optional[str] = None
    slither_findings_count: Optional[int] = None
    slither_findings_summary: Dict[str, int] = field(default_factory=dict)
    slither_diagnostics: Optional[str] = None

    # CertiK security intelligence
    security_intelligence_status: Optional[str] = None
    security_score: Optional[float] = None
    security_score_grade: Optional[str] = None
    certik_project_id: Optional[str] = None
    audit_count: Optional[int] = None

    # Contract permission scanner details (clearly labeled as HEURISTIC)
    permission_scanner_status: Optional[str] = None
    permission_scanner_findings: List[Dict[str, Any]] = field(default_factory=list)
    permission_scanner_critical_count: Optional[int] = None
    permission_scanner_type: str = "HEURISTIC"

    # Provenance and timestamps
    first_discovered_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    analysis_history: List[Dict[str, Any]] = field(default_factory=list)
    session_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Converts dataclass to serializable dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DiscoveredProtocolRecord":
        """Instantiates record from dictionary, safely handling missing optional keys."""
        valid_keys = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)

    def merge_update(self, new_data: Dict[str, Any]) -> None:
        """
        Updates fields with new information while preventing silent regression:
        Never overwrites non-empty existing data with None or empty values.
        Updates last_updated_at timestamp.
        """
        for k, v in new_data.items():
            if k in ("protocol_id", "first_discovered_at"):
                continue  # Never overwrite immutable identifiers or creation timestamp

            if v is not None:
                # If field is an empty collection or string and we already have data, preserve existing
                curr_val = getattr(self, k, None)
                if curr_val and (v == "" or v == [] or v == {}):
                    continue
                setattr(self, k, v)

        self.last_updated_at = datetime.now(timezone.utc).isoformat()


class DiscoveryStore:
    """
    Versioned JSON store managing protocol candidates and discovery session metadata.
    Provides atomic replacement and backup recovery.
    """

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is not None:
            self.db_path = Path(db_path).resolve()
        else:
            self.db_path = get_default_data_dir() / "discovery" / "discovery_database.json"

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.bak_path = self.db_path.with_suffix(".json.bak")
        self._lock = threading.RLock()
        self._records: Dict[str, DiscoveredProtocolRecord] = {}
        self._metadata: Dict[str, Any] = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_cycle_at": None,
            "total_cycles_completed": 0,
            "active_session_id": None,
        }

        self.load()

    @property
    def total_count(self) -> int:
        with self._lock:
            return len(self._records)

    def get_all_records(self) -> List[DiscoveredProtocolRecord]:
        with self._lock:
            return list(self._records.values())

    def get_record(self, protocol_id: str) -> Optional[DiscoveredProtocolRecord]:
        with self._lock:
            return self._records.get(protocol_id.lower())

    def upsert_record(self, record_data: Dict[str, Any]) -> Tuple[DiscoveredProtocolRecord, bool]:
        """
        Inserts a new protocol record or updates an existing one without losing data.
        Returns (record, is_new).
        """
        with self._lock:
            proto_id = record_data.get("protocol_id")
            if not proto_id:
                proto_id = generate_stable_protocol_id(
                    slug=record_data.get("slug"),
                    name=record_data.get("name"),
                    chain=record_data.get("chain"),
                    address=record_data.get("address") or record_data.get("main_address"),
                )
                record_data["protocol_id"] = proto_id

            proto_id_lower = proto_id.lower()
            is_new = proto_id_lower not in self._records

            if is_new:
                record = DiscoveredProtocolRecord.from_dict(record_data)
                self._records[proto_id_lower] = record
            else:
                record = self._records[proto_id_lower]
                record.merge_update(record_data)

            return record, is_new

    def update_session_metadata(
        self,
        session_id: Optional[str] = None,
        increment_cycle: bool = False,
        last_cycle_at: Optional[str] = None,
    ) -> None:
        with self._lock:
            if session_id is not None:
                self._metadata["active_session_id"] = session_id
            if increment_cycle:
                self._metadata["total_cycles_completed"] = (
                    self._metadata.get("total_cycles_completed", 0) + 1
                )
            if last_cycle_at is not None:
                self._metadata["last_cycle_at"] = last_cycle_at

    def get_metadata(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._metadata)

    def load(self) -> bool:
        """
        Loads the database from disk. If the primary file is corrupted,
        automatically attempts recovery from the .bak file.
        """
        with self._lock:
            if not self.db_path.exists():
                logger.info(f"Discovery database does not exist yet at {self.db_path}. Initialized empty.")
                return False

            success = self._read_file(self.db_path)
            if not success:
                logger.warning(
                    f"Corrupted discovery file detected at {self.db_path}. Attempting recovery from {self.bak_path}..."
                )
                if self.bak_path.exists():
                    bak_success = self._read_file(self.bak_path)
                    if bak_success:
                        logger.info("Successfully recovered discovery store from backup file.")
                        self.save()  # restore primary
                        return True

                # Backup corrupted file safely and initialize clean store
                ts = int(datetime.now(timezone.utc).timestamp())
                corrupted_dest = self.db_path.parent / f"corrupted_db_{ts}.json"
                try:
                    shutil.copy2(self.db_path, corrupted_dest)
                    logger.error(
                        f"Failed to recover from backup. Corrupted file preserved at {corrupted_dest}."
                    )
                except Exception as e:
                    logger.error(f"Could not preserve corrupted database file: {e}")

                self._records = {}
                return False

            return True

    def _read_file(self, target_path: Path) -> bool:
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                return False

            schema_ver = data.get("schema_version", "1.0.0")
            metadata = data.get("metadata", {})
            self._metadata.update(metadata)
            self._metadata["schema_version"] = schema_ver

            raw_records = data.get("records", {})
            parsed_records: Dict[str, DiscoveredProtocolRecord] = {}

            if isinstance(raw_records, dict):
                for k, v in raw_records.items():
                    if isinstance(v, dict):
                        parsed_records[k.lower()] = DiscoveredProtocolRecord.from_dict(v)
            elif isinstance(raw_records, list):
                for v in raw_records:
                    if isinstance(v, dict) and "protocol_id" in v:
                        parsed_records[v["protocol_id"].lower()] = DiscoveredProtocolRecord.from_dict(v)

            self._records = parsed_records
            return True
        except Exception as exc:
            logger.error(f"Error reading discovery file {target_path}: {exc}")
            return False

    def save(self) -> bool:
        """
        Persists the database atomically.
        Writes to a temporary file, fsyncs, creates a .bak backup of the current
        valid database, and atomically replaces the primary file.
        """
        with self._lock:
            payload = {
                "schema_version": CURRENT_SCHEMA_VERSION,
                "metadata": self._metadata,
                "total_records": len(self._records),
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "records": {k: v.to_dict() for k, v in self._records.items()},
            }

            parent_dir = self.db_path.parent
            parent_dir.mkdir(parents=True, exist_ok=True)

            try:
                # 1. Write to temporary file in the same directory (required for atomic rename)
                tmp_fd, tmp_path_str = tempfile.mkstemp(
                    prefix="disc_db_", suffix=".tmp", dir=str(parent_dir)
                )
                tmp_path = Path(tmp_path_str)

                with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp_file:
                    json.dump(payload, tmp_file, indent=2, default=str)
                    tmp_file.flush()
                    os.fsync(tmp_file.fileno())

                # 2. Rotate current primary file to .bak if primary exists and is valid
                if self.db_path.exists():
                    try:
                        shutil.copy2(self.db_path, self.bak_path)
                    except Exception as bak_err:
                        logger.warning(f"Failed to create backup file prior to atomic save: {bak_err}")

                # 3. Atomically replace primary file
                os.replace(tmp_path, self.db_path)
                return True

            except Exception as e:
                logger.error(f"Failed atomic write of discovery database: {e}")
                if "tmp_path" in locals() and tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass
                return False

    def filter_records(
        self,
        min_tvl: Optional[float] = None,
        chain: Optional[str] = None,
        category: Optional[str] = None,
        search_query: Optional[str] = None,
        resolution_status: Optional[str] = None,
        analysis_status: Optional[str] = None,
    ) -> List[DiscoveredProtocolRecord]:
        """
        Filters stored records using exact project semantics.
        Supports query text matching on name, slug, address, category.
        """
        with self._lock:
            results = []
            q_lower = search_query.strip().lower() if search_query else None

            for record in self._records.values():
                # TVL filter
                if min_tvl is not None:
                    if record.tvl is None or record.tvl < min_tvl:
                        continue

                # Chain filter
                if chain and chain.lower() != "all":
                    ch_lower = chain.lower()
                    record_ch = (record.chain or "").lower()
                    record_chs = [c.lower() for c in record.chains]
                    if record_ch != ch_lower and ch_lower not in record_chs:
                        continue

                # Category filter
                if category and category.lower() != "all":
                    cat_lower = category.lower()
                    if (record.category or "").lower() != cat_lower:
                        continue

                # Resolution status filter
                if resolution_status and resolution_status.lower() != "all":
                    res_status = (record.repository_resolution_status or "").lower()
                    if res_status != resolution_status.lower():
                        continue

                # Analysis status filter
                if analysis_status and analysis_status.lower() != "all":
                    ana_status = (record.static_analysis_status or "").lower()
                    if ana_status != analysis_status.lower():
                        continue

                # Search text filter
                if q_lower:
                    name_match = q_lower in record.name.lower()
                    slug_match = q_lower in record.slug.lower()
                    addr_match = bool(record.address and q_lower in record.address.lower())
                    cat_match = bool(record.category and q_lower in record.category.lower())
                    if not (name_match or slug_match or addr_match or cat_match):
                        continue

                results.append(record)

            # Sort by TVL descending (None at the end)
            results.sort(key=lambda r: (r.tvl is not None, r.tvl or 0.0), reverse=True)
            return results
