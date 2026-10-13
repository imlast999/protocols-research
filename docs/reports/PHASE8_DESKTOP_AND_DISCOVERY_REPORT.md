# Phase 8 — Windows Desktop Application and Continuous Protocol Discovery Report

**Delivery Date:** 2026-10-13  
**Status:** `READY_WITH_LIMITATIONS`  
**Automated Tests:** 181 total (180 passed, 0 failures, 1 skipped offline)  
**Target Repository:** `https://github.com/imlast999/protocols-research.git`  
**Branch:** `main`

---

## 1. Executive Summary

Phase 8 implements a high-performance Windows desktop application and continuous protocol discovery system (**Protocol Hunter**) for the **DeFi Protocol Research & Security Analysis Suite**.

Rather than rebuilding research logic, the desktop application and discovery engine directly reuse the existing underlying research services:
- **`src/defi_research/discovery/discovery_store.py`**: Versioned JSON database (`1.0.0`) providing atomic disk writes (`.tmp` -> `.bak` -> replace), corruption recovery, record deduplication, and separation of persistent user data from application bundles.
- **`src/defi_research/discovery/protocol_hunter.py`**: Continuous discovery service orchestrating periodic queries to DefiLlama, applying TVL and sector filters, tracking runtime metrics, handling rate limits (HTTP 429) and timeouts, and executing deep repository investigations.
- **`src/defi_research/gui/`**: PySide6 dark-themed research dashboard with 3 primary tabs:
  1. **Protocol Explorer & Dashboard**: Real-time statistics cards, filter controls, candidate table, and deep protocol intelligence inspector.
  2. **Protocol Hunter**: Continuous discovery manager with configurable duration, cycle intervals, TVL thresholds, live activity console, and pause/resume/stop controls.
  3. **Reports & Exports**: Report viewer and JSON database exporter.
- **`packaging/`**: Reproducible PyInstaller specification (`packaging/defi_research.spec`) and build script (`packaging/build_exe.py`) producing a standalone Windows executable.

---

## 2. Architecture & File Organization

```
src/defi_research/
├── discovery/                         # [NEW] Discovery & Persistence
│   ├── __init__.py                    # Public exports
│   ├── discovery_store.py             # Versioned JSON store, atomic writes & recovery
│   └── protocol_hunter.py             # Continuous discovery engine & metrics
├── gui/                               # [NEW] PySide6 Desktop GUI
│   ├── __init__.py                    # GUI package init
│   ├── theme.py                       # Modern dark-themed QSS stylesheet
│   ├── workers.py                     # Non-blocking Qt worker threads (QThread)
│   ├── main_window.py                 # Multi-tab research dashboard & explorer
│   └── app.py                         # Application launcher & headless harness
packaging/                             # [NEW] Standalone Packaging
├── defi_research.spec                 # PyInstaller spec configuration
└── build_exe.py                       # Build script generating dist/DeFiProtocolResearch/
desktop_app.py                         # [NEW] Desktop launcher entry point
tests/
├── test_discovery_store.py            # [NEW] 8 tests: atomic writes, backup, recovery
├── test_protocol_hunter.py            # [NEW] 7 tests: lifecycle, deduplication, error handling
└── test_gui_headless.py               # [NEW] 7 tests: offscreen Qt GUI construction & filters
```

---

## 3. Reused Existing Research Engine Functions

The desktop application and Protocol Hunter interface directly with established project components without code duplication:
- **`DefiLlamaClient`**: Used for protocol retrieval, exponential backoff retries, and rate-limit handling.
- **`get_high_value_targets` & `clean_contract_address`**: Used for TVL filtering, EVM address validation, and checksum verification.
- **`resolve_github_repo_from_slug` & `normalize_protocol_record`**: Used for automated GitHub repository resolution during deep discovery cycles.
- **`run_protocol_pipeline`**: Used by `SingleAnalysisWorker` to execute end-to-end 7-stage analysis on demand from the GUI.
- **`check_docker_availability` & `check_slither_availability`**: Used by header status pills and diagnostic dialogs to display real-time environment status.

---

## 4. Supported Filters & Exact Semantics

All exposed filter controls map directly to existing codebase logic:

| Filter Control | Source Module | Values / Semantics |
| :--- | :--- | :--- |
| **Minimum TVL** | `market_intelligence.py` / `pipeline.py` | `$0`, `$50,000` (default), `$500,000`, `$1,000,000`, `$10,000,000`, `$50,000,000`. Filters out protocols whose TVL is below threshold or missing. |
| **EVM Chain** | `config.py` (`CHAINS`) | `All`, `Ethereum`, `Arbitrum`, `Polygon`, `BSC`. Matches primary chain or supported chain arrays via `CHAIN_ALIAS_MAP`. |
| **Category / Sector** | `notrack_script.py` (`TARGET_CATEGORIES`) | `All`, `Lending`, `Dexes`, `Derivatives`, `Bridge`, `Liquid Staking`, `Yield`. Excludes non-DeFi categories and CEXs. |
| **Search Query** | `discovery_store.py` (`filter_records`) | Case-insensitive substring match against `name`, `slug`, `address`, and `category`. |
| **Resolution Status** | `github_repository_resolver.py` | `All`, `RESOLVED_CONFIDENT`, `RESOLVED_LOW_CONFIDENCE`, `ORGANIZATION_ONLY`, `UNRESOLVED`. |

---

## 5. Protocol Hunter — Continuous Discovery Behavior

> [!NOTE]
> **Research Context:** Protocol Hunter is automated protocol intelligence collection, **not cryptocurrency mining**. No wallet operations, private keys, or blockchain transactions are performed.

1. **Configurable Runtime Parameters**:
   - **Duration**: Configurable in hours (0.1h to 72.0h, or 0 for continuous execution).
   - **Cycle Interval**: Delay between discovery queries (10s to 3600s, default 60s).
   - **Max Candidates per Cycle**: Ingestion cap per query (default 25) to avoid API burst load.
   - **Deep Investigation**: Toggle to resolve official GitHub repositories automatically.
2. **Deduplication & Provenance**:
   - Candidates are identified by stable IDs: DefiLlama `slug`, `chain:address`, or normalized name.
   - Re-discovered candidates merge fresh market TVL and update `last_updated_at` without overwriting existing verified repositories or audit records with null values.
3. **Fault Tolerance & API Resilience**:
   - Catches HTTP timeouts, connection resets, and HTTP 429 rate limits without crashing the discovery loop. Errors are captured in `HunterMetrics.recent_errors`.
4. **State Transitions**:
   - Supports thread-safe `IDLE` -> `RUNNING` -> `PAUSED` -> `STOPPING` -> `COMPLETED`.

---

## 6. Versioned JSON Schema & Persistence Guarantees

Persistent user data is stored in the user's writable data directory (`%APPDATA%\DeFiResearch\discovery\discovery_database.json` on Windows, or `~/.defi_research/` on Linux/macOS), keeping it strictly separated from the application and PyInstaller bundle paths.

### Schema (`1.0.0`) Structure:
```json
{
  "schema_version": "1.0.0",
  "metadata": {
    "created_at": "2026-10-13T22:50:00Z",
    "last_cycle_at": "2026-10-13T23:05:00Z",
    "total_cycles_completed": 12,
    "active_session_id": "hunter-1791925500"
  },
  "total_records": 45,
  "records": {
    "aave-v3": {
      "protocol_id": "aave-v3",
      "name": "Aave V3",
      "slug": "aave-v3",
      "chain": "ethereum",
      "tvl": 15000000.0,
      "repository_url": "https://github.com/aave/aave-v3-core",
      "repository_resolution_status": "RESOLVED_CONFIDENT",
      "static_analysis_status": "COMPLETED",
      "permission_scanner_type": "HEURISTIC",
      "first_discovered_at": "2026-10-13T22:51:00Z",
      "last_updated_at": "2026-10-13T23:05:00Z"
    }
  }
}
```

### Safety & Recovery Guarantees:
1. **Atomic Disk Writes**: Output is written to a unique `.tmp` file in the same directory, flushed, and synced to disk before replacing the target file via `os.replace`.
2. **Automatic Backup Rotation**: Prior to replacing the primary file, the previous valid version is copied to `.bak`.
3. **Corruption Recovery**: If the primary JSON file is corrupted or truncated, the store automatically reloads from `.bak`. If both are missing, the corrupted file is preserved as `corrupted_db_<timestamp>.json` and a clean database is initialized so the application never crashes.

---

## 7. Automated Test Suite Execution

Test command:
```bash
python -m unittest discover -s tests -v
```

### Results:
- **Total Tests Ran**: **181**
- **Passed**: **180**
- **Failures / Errors**: **0**
- **Skipped**: **1** (`test_live_certik_api_call` in `tests/test_certik_skynet.py`, skipped offline)
- **Execution Time**: ~5.15 seconds

### New Tests Added in Phase 8 (+24 Tests):
- `tests/test_discovery_store.py` (8 tests): Stable ID generation, empty initialization, atomic write and `.bak` backup, corruption recovery, corrupted file preservation, incremental merging, filter matching, heuristic permission scanner labeling.
- `tests/test_protocol_hunter.py` (7 tests): Config defaults, single cycle filtering, deduplication across cycles, API error resilience, state transitions and stop cancellation, pause/resume, deep investigation repository resolution.
- `tests/test_gui_headless.py` (7 tests): Offscreen Qt application construction, tab layout, stat cards update, table filter interactions, detail inspector evidence rendering, Hunter controls, environment pills.
- `tests/test_cli.py` (+2 tests): CLI `gui` and `hunter` subcommand dispatch.

---

## 8. Standalone Windows Executable (.exe) Packaging

- **Build Script**: `python packaging/build_exe.py`
- **PyInstaller Spec**: `packaging/defi_research.spec`
- **Output Target**: `dist/DeFiProtocolResearch/DeFiProtocolResearch.exe`
- **Packaging Mode**: Windowed GUI (`console=False`), bundling Python 3.13, PySide6, web3, requests, and all `defi_research` modules.
- **Data Isolation**: Application reads/writes user data in `%APPDATA%\DeFiResearch`, preventing runtime errors when installed in write-protected paths (such as `C:\Program Files`).

---

## 9. Docker & Live Slither Status

> [!IMPORTANT]
> **Docker & Slither Live Execution Status:**
> - Docker Desktop and the Docker daemon remain **unavailable on this Windows development machine**.
> - Therefore, while GUI controls, Docker status indicators, argument builders, and mock tests are fully verified, **live Docker-based Slither static analysis on real Solidity code has NOT yet been validated in a live environment**.
> - In the GUI, when Docker is absent, the Docker status pill displays `[Docker: Unavailable]` in red, clicking it provides setup instructions, and any protocol evaluated without Docker explicitly records `static_analysis_status: "UNAVAILABLE"` (never claiming 0 findings).

---

## 10. Remaining Limitations & Next Phase Recommendations

1. **Host Docker Execution**: Validate live execution of `docker build -t defi-slither:0.10.4 -f docker/slither/Dockerfile .` on a machine with Docker Desktop or Linux/WSL2.
2. **On-Chain Bytecode Verification**: Bridge source code from GitHub repositories with deployed on-chain bytecode across block explorers.
3. **Direct Contract Scanner GUI Integration**: Add interactive Solidity AST browsing into the detail panel.
