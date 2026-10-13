# DeFi Research & Security Intelligence Suite

A modular Python framework for automated DeFi protocol discovery, GitHub repository resolution, controlled Slither static analysis, external security score retrieval (CertiK Skynet), and consolidated multi-stage security reporting.

Supported EVM networks: **Ethereum**, **Arbitrum**, **Polygon**, and **BNB Smart Chain (BSC)**.

---

## Architecture & Pipeline Overview

The project connects 7 logical stages into an end-to-end audit and intelligence workflow:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. Discovery (market_intelligence.py / DefiLlama Client)                    │
│    • Public DefiLlama API (/protocols) discovery & TVL filtering            │
│    • Sector filtering (Lending, DEXes, Yield, Bridges, Liquid Staking)      │
│    • Contract address extraction across EVM chains                          │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 2. Repository Resolution (github_repository_resolver.py)                   │
│    • Normalizes GitHub handles, trees, and candidate repository URLs        │
│    • Evaluates resolution confidence: RESOLVED_CONFIDENT, LOW, ORG, etc.    │
│    • Blocks automated scanning of ambiguous or unverified repositories     │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 3. Source Verification & Workspace Containment                             │
│    • Validates Solidity build frameworks (Foundry, Hardhat, Truffle, Brownie)│
│    • Verifies commit hash and branch provenance                             │
│    • Enforces workspace path boundaries (prevents directory traversal)      │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 4. Controlled Static Analysis (slither_runner.py)                           │
│    • Subprocess execution with shell=False in isolated workspaces           │
│    • Zero untrusted build scripts or package installs                       │
│    • Parses structured JSON findings (High, Medium, Low, Info, Gas)         │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 5. Security Intelligence (security_intelligence.py)                        │
│    • Official CertiK Partner API bridge (Security Scores & Audit records)   │
│    • Multi-criteria match confidence (ID, contract, domain, name)           │
│    • Decoupled fault isolation (provider errors do not crash pipeline)      │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 6. Consolidated Aggregation (pipeline.py)                                   │
│    • Aggregates all stage outcomes with explicit machine-readable statuses  │
│    • Redacts secrets and API keys from outputs and diagnostics              │
│    • Represents missing values as null (never fabricated as 0)             │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 7. Persistence & Reporting                                                  │
│    • Writes structured JSON reports strictly within ./reports/pipeline/     │
│    • Generates run-level summaries for batch operations                     │
└─────────────────────────────────────────────────────────────────────────────┘
```

For complete technical details, see the [Architecture Guide](docs/architecture.md).

---

## Directory Layout

```
.
├── cli.py                        # Unified CLI & interactive console
├── config.py                     # Root configuration facade
├── pyproject.toml                # Package metadata (PEP 517/621)
├── requirements.txt              # Project dependencies
├── .env.example                  # Sanitized environment template
├── .gitignore                    # Git ignore rules (.env, caches, venv)
├── README.md                     # Project documentation
├── docs/
│   ├── architecture.md           # Deep-dive architecture and security gates
│   └── reports/                  # Phase implementation reports (Phases 1 - 3.1)
├── reports/                      # Runtime generated reports
│   ├── pipeline/                 # Consolidated Phase 4 reports
│   └── slither/                  # Raw Slither static analysis reports
├── src/
│   └── defi_research/            # Source package
│       ├── __init__.py           # Package exports & versioning
│       ├── config.py             # Package configuration
│       ├── market_intelligence.py# DefiLlama discovery & filtering
│       ├── notrack_script.py     # Filtered DeFi sector pipeline
│       ├── contract_permission_scanner.py # On-chain permission scanner
│       ├── github_repository_resolver.py  # GitHub resolution & candidate evaluation
│       ├── slither_runner.py     # Controlled Slither static analysis runner
│       ├── security_intelligence.py       # CertiK Skynet partner API bridge
│       └── pipeline.py           # 7-stage end-to-end pipeline orchestrator
└── tests/
    ├── test_github_repository_resolver.py # GitHub resolver unit tests (23 tests)
    ├── test_slither_runner.py             # Slither runner unit tests (14 tests)
    ├── test_slither_security_audit.py     # Security audit & path safety (17 tests)
    ├── test_security_intelligence.py      # CertiK provider unit tests (31 tests)
    └── test_pipeline.py                   # Phase 4 pipeline unit tests (13 tests)
```

---

## Installation & Setup

### Requirements
- Python 3.9+ (tested on Python 3.13)
- Windows PowerShell, macOS, or Linux

### Windows PowerShell Setup

```powershell
# 1. Clone the repository
git clone https://github.com/imlast999/protocols-research.git
cd "protocols-research"

# 2. Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. (Optional) Install editable package
pip install -e .

# 5. Configure environment variables
Copy-Item .env.example .env
```

### Configuration (`.env`)

Configure RPC endpoints, explorer API keys, and external security provider credentials in `.env`:

```ini
# Workspace Root (restricts Slither filesystem access)
WORKSPACE_ROOT="."

# Block Explorer API Keys (Optional - for contract ABI fetching)
ETHERSCAN_API_KEY=""
BSCSCAN_API_KEY=""
POLYGONSCAN_API_KEY=""
ARBISCAN_API_KEY=""

# CertiK Skynet Partner API (Optional - requires commercial partnership)
CERTIK_API_KEY=""
CERTIK_BASE_URL="https://partner.certik-skynet.com"
CERTIK_TIMEOUT_SECONDS="10"

# Output directory for reports
OUTPUT_DIR="./reports"
```

> [!NOTE]
> `.env` is ignored by Git and will never be tracked or committed. Never commit real API keys or private keys.

---

## CLI Usage (`cli.py`)

The unified CLI provides access to all individual modules as well as the new Phase 4 end-to-end pipeline:

### Interactive Mode

Run without arguments to launch the interactive step-by-step assistant:

```bash
python cli.py
```

### 1. End-to-End Pipeline (Phase 4)

Run the full 7-stage pipeline across one or many protocols:

```bash
# Analyze a single protocol by DefiLlama slug and output formatted JSON:
python cli.py pipeline --slug aave-v3 --json

# Run a batch discovery pipeline with chain and TVL filters:
python cli.py pipeline --chain ethereum --category Lending --limit 5

# Run the pipeline with a local repository directory for Slither static analysis:
python cli.py pipeline --slug aave-v3 -t ./workspace/aave-v3-core --commit abcdef123456

# Save structured reports to a custom directory:
python cli.py pipeline --slug aave-v3 --output-dir ./reports/my_run
```

#### Pipeline Options:
- `--slug SLUG`: DefiLlama protocol slug (e.g. `aave-v3`, `lido`, `uniswap-v3`).
- `--limit LIMIT`: Maximum protocols to process in batch mode (default: `10`).
- `--chain {ethereum,arbitrum,polygon,bsc}`: Filter protocols by primary blockchain.
- `--category CATEGORY`: Filter by DeFi category (e.g. `Lending`, `DEXes`, `Yield`).
- `-t, --target-dir DIR`: Local path for source verification and Slither analysis.
- `--commit COMMIT`: Commit hash to verify and analyze.
- `--output-dir DIR`: Directory for output JSON reports.
- `--live-check`: Opt-in to live external API calls.
- `--json`: Emit structured JSON output to stdout.

---

### 2. GitHub Repository Resolution

```bash
# Resolve canonical repository for a DefiLlama protocol:
python cli.py resolve --slug maple

# Normalize and classify an arbitrary GitHub URL:
python cli.py resolve --url "https://github.com/aave/aave-v3-core.git"
```

---

### 3. Slither Static Analysis

```bash
# Execute Slither in an isolated workspace with a verified commit:
python cli.py slither -t ./workspace/aave-v3-core --slug aave-v3 --commit e5d4c3b2
```

---

### 4. CertiK Security Intelligence

```bash
# Query CertiK security score and audit records:
python cli.py intelligence --slug aave-v3 --json
```

---

### 5. Market Discovery & On-Chain Scanning

```bash
# Discover top protocols on Arbitrum with TVL > 100k:
python cli.py market --chain arbitrum --min-tvl 100000 --limit 5

# Audit specific on-chain contract permissions:
python cli.py scan -c 0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9 --chain ethereum
```

---

## Example Consolidated JSON Report

The Phase 4 orchestrator generates standardized JSON reports:

```json
{
  "protocol_name": "Aave V3",
  "defillama_slug": "aave-v3",
  "defillama_id": null,
  "chain": "optimism",
  "category": "Lending",
  "tvl": 17473490789.0,
  "started_at": "2026-10-13T14:45:22.256423+00:00",
  "completed_at": "2026-10-13T14:45:23.217513+00:00",
  "duration_seconds": 0.961,
  "overall_status": "PARTIAL",
  "stages": {
    "discovery": {
      "status": "SUCCESS",
      "message": "Protocol metadata successfully resolved and normalized",
      "duration_seconds": 0.96
    },
    "repository_resolution": {
      "status": "SUCCESS",
      "message": "Single distinct repository verified with official organization corroboration"
    },
    "source_verification": {
      "status": "SKIPPED",
      "message": "No local target directory provided for source verification"
    },
    "static_analysis": {
      "status": "SKIPPED",
      "message": "Static analysis skipped: local source directory not specified"
    },
    "security_intelligence": {
      "status": "NOT_CONFIGURED",
      "message": "CertiK Partner API credentials ('CERTIK_API_KEY') are not configured."
    }
  },
  "repository": {
    "url": "https://github.com/aave/aave-v3-core",
    "resolution_status": "RESOLVED_CONFIDENT",
    "confidence_reason": "Official organization match"
  },
  "source_verification": {
    "status": null,
    "analyzed_commit": null
  },
  "static_analysis": {
    "status": "SKIPPED",
    "slither_version": null,
    "findings_count": null,
    "findings_summary": {},
    "diagnostics": "Static analysis skipped: local source directory not specified"
  },
  "security_intelligence": {
    "status": "NOT_CONFIGURED",
    "security_score": null,
    "security_score_grade": null,
    "certik_project_id": null,
    "audit_count": 0
  },
  "errors": [],
  "limitations": [
    "Static analysis cannot detect complex economic exploits, price oracle manipulation, or flash loan arbitrage.",
    "Repository source code is not guaranteed to correspond to on-chain deployed bytecode without formal bytecode verification.",
    "External security scores and audit counts do not guarantee smart contract immunity from zero-day vulnerabilities.",
    "External security intelligence is informational and never bypasses repository verification or Slither eligibility gates."
  ]
}
```

---

## Running Automated Tests

All tests are 100% deterministic, offline by default, and require no live network connections or external tools:

```bash
# Run the complete test suite (98 tests total):
python -m unittest discover -s tests -v

# Run individual test modules:
python -m unittest tests/test_pipeline.py -v
python -m unittest tests/test_security_intelligence.py -v
python -m unittest tests/test_slither_security_audit.py -v
python -m unittest tests/test_slither_runner.py -v
python -m unittest tests/test_github_repository_resolver.py -v
```

### Test Suite Summary
- **Total Tests:** 98
- **Passing:** 97
- **Skipped:** 1 (explicit live opt-in test `test_live_api_authentication_and_query_flow`)
- **Status:** 100% passing offline suite.

---

## Security Invariants & Limitations

1. **Slither Static Analysis Availability:**
   - Slither requires Python 3.8+ with `solc-select` or `solc` installed in the local environment.
   - In environments where Slither is not installed (e.g. standard Windows hosts without Linux/WSL), Slither execution reports `TOOL_UNAVAILABLE` or `SKIPPED` cleanly without failing the overall pipeline.
2. **CertiK Partner API Credentials:**
   - The CertiK Skynet Partner API requires a commercial agreement and private API key (`CERTIK_API_KEY`).
   - When credentials are not configured, the pipeline records `NOT_CONFIGURED`. It does not perform brittle web scraping or fabricate scores.
3. **Bytecode Verification Limitation:**
   - Source code analysis against a GitHub repository does not prove that the analyzed code corresponds to deployed bytecode on-chain. Bytecode decompilation and on-chain verification are separate disciplines.
4. **Economic & Oracle Exploits:**
   - Static analysis tools (like Slither) detect code-level patterns (reentrancy, uninitialized variables, shadow variables) but cannot detect economic vulnerabilities, oracle manipulation, or cross-chain bridge logic flaws.

---

## License & Attribution

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details if applicable.
Data provided by [DefiLlama](https://defillama.com) and [CertiK Skynet](https://skynet.certik.com).
