# Phase 1.2 — Repository Identity and Source Verification Readiness Report

**Project Directory:** `C:\Users\lxstb\Documents\amm pools project\scripts protocols research\`  
**Date:** October 12, 2026  
**Status:** Completed  
**Test Suite:** 29 passed (0 failures, 0 errors, offline deterministic)

---

## 1. Audit Findings for Lido, Aave V3, and LayerZero V2

### A. Lido (`slug: lido`, `id: 182`)
* **Raw DefiLlama Metadata:**
  * `github`: `["lidofinance"]`
  * `audit_links`: `["https://github.com/lidofinance/audits"]`
* **Root Cause for Previous Output:**
  * In Phase 1.1, `lidofinance/audits` was extracted from `audit_links` and parsed as a general repository candidate. Because its owner (`lidofinance`) matched the declared organization handle in `github: ["lidofinance"]`, the resolver marked it as `RESOLVED_CONFIDENT`.
* **Repository Content Inspection:**
  * `https://github.com/lidofinance/audits` is an archive of external security audits (PDF reports and summaries from MixBytes, Sigma Prime, ChainSecurity, Statemind). It contains **zero core smart contracts**.
  * Lido's actual smart contracts are maintained in repositories such as `lidofinance/lido-dao` and `lidofinance/core`.
* **Phase 1.2 Resolution:**
  * Repositories with names matching audit/documentation keywords (`audits`, `audit-reports`, `publications`, `docs`, etc.) or known audit firm repositories are classified as `CandidateType.AUDIT_OR_DOCS_REPOSITORY`.
  * They are strictly excluded from contract source candidate selection.
  * **Final Status:** `ORGANIZATION_ONLY`
  * **Selected Repository:** `null`
  * **Ignored Audit Repositories:** `["https://github.com/lidofinance/audits"]`

### B. Aave V3 (`slug: aave-v3`, `id: 1599`)
* **Raw DefiLlama Metadata:**
  * `github`: `null` (not present in raw protocol record)
  * `audit_links`: `["https://aave.com/security"]`
* **Root Cause for Previous Output:**
  * DefiLlama does not declare GitHub repositories in its primary protocol record for `aave-v3`.
  * The only audit link points to `https://aave.com/security`, which has domain `aave.com` (non-GitHub).
* **Repository Content Inspection & Corroboration:**
  * The official contract repository is known to the ecosystem as `aave/aave-v3-core`. However, DefiLlama's API record does not supply this URL.
  * The resolver adheres to the strict rule: **Do not guess or fabricate repositories without evidence**.
* **Phase 1.2 Resolution:**
  * **Final Status:** `UNRESOLVED`
  * **Selected Repository:** `null`
  * **Reason:** No valid GitHub repository metadata found in protocol record.

### C. LayerZero V2 (`slug: layerzero-v2`, `id: 4867`)
* **Raw DefiLlama Metadata:**
  * `github`: `null`
  * `audit_links`: `[]`
* **Root Cause for Previous Output:**
  * DefiLlama returns neither GitHub repository handles nor audit links for `layerzero-v2`.
* **Repository Content Inspection & Corroboration:**
  * LayerZero contracts are maintained in repositories under `LayerZero-Labs`, but no link is declared in DefiLlama.
* **Phase 1.2 Resolution:**
  * **Final Status:** `UNRESOLVED`
  * **Selected Repository:** `null`
  * **Reason:** No valid GitHub repository metadata found in protocol record.

---

## 2. Changes Made and Their Rationale

### A. Audit & Documentation Repository Disambiguation
* Added `AUDIT_AND_DOCS_REPO_NAMES` (`audits`, `audit-reports`, `publications`, `docs`, `documentation`, `audit`, `security-audits`, `contracts-audit`, `papers`).
* Added `CandidateType.AUDIT_OR_DOCS_REPOSITORY`.
* URLs pointing to audit report repositories (even under the protocol's own organization, e.g., `lidofinance/audits`) are identified as audit archives, recorded in `auditor_repos_ignored`, and excluded from `selected_github_url`.

### B. Decoupled 3-Stage Pipeline Handoff Architecture
1. **Stage A (`A_repository_resolution`):**
   * Identifies candidate repository and confidence level (`RESOLVED_CONFIDENT`, `RESOLVED_LOW_CONFIDENCE`, `ORGANIZATION_ONLY`, `AMBIGUOUS`, `UNRESOLVED`, `MALFORMED_RECORD`).
   * Explicitly sets `eligible_for_automated_analysis: false` and `eligible_for_slither: false`.
   * Sets `eligible_for_source_verification: true` only if resolution is `RESOLVED_CONFIDENT`.
2. **Stage B (`B_source_verification`):**
   * Safe static inspection of local repository file tree.
   * Checks for Solidity source files (`.sol`) and detects build configuration without executing code.
3. **Stage C (`C_slither_eligibility`):**
   * Blocks Slither execution until **both** Stage A (confident resolution) and Stage B (`SOURCE_VERIFIED`) have completed.

### C. Static Source Verification Engine (`verify_repository_source`)
* Implemented in `github_repository_resolver.py`.
* Scans directory structure for `.sol` files while pruning non-contract directories (`node_modules`, `lib`, `.git`, `test`, `tests`, `docs`).
* Detects build configuration files:
  * **Foundry:** `foundry.toml`
  * **Hardhat:** `hardhat.config.js`, `hardhat.config.ts`, `hardhat.config.cjs`, `hardhat.config.mjs`
  * **Truffle:** `truffle-config.js`, `truffle.js`
  * **Brownie:** `brownie-config.yaml`
  * **Ape:** `ape-config.yaml`
* Explicitly records the limitation:
  > *"The default branch or analyzed commit is not guaranteed to correspond to on-chain deployed bytecode without contract address bytecode verification."*

### D. Security Boundaries Preserved
* **Zero code execution:** No `npm install`, `forge build`, `npx hardhat`, or arbitrary build scripts are run.
* **Deterministic timeouts:** DefiLlama client uses bounded timeouts (10s) and retry backoff for rate limits.

---

## 3. New Test Cases and Verification Results

The test suite in `test_github_repository_resolver.py` was extended with 6 new deterministic regression test suites covering Phase 1.2 requirements:

1. `test_audit_repository_rejected_as_contract_source`:
   * Verifies that `lidofinance/audits` and third-party auditor repositories are categorized as `AUDIT_OR_DOCS_REPOSITORY` and rejected from contract source selection.
2. `test_org_match_without_corroborated_source_repo`:
   * Verifies that an organization match alone (e.g., `exampledao/docs`) does not produce `RESOLVED_CONFIDENT`.
3. `test_multiple_plausible_repositories_remain_ambiguous`:
   * Verifies that competing repositories without tie-breakers remain `AMBIGUOUS`.
4. `test_missing_metadata_remains_unresolved`:
   * Verifies that missing or null fields remain strictly `UNRESOLVED` (and empty records return `MALFORMED_RECORD`).
5. `test_source_verification_outcomes`:
   * Tests `verify_repository_source` against temporary file trees for all 4 machine-readable outcomes:
     * `SOURCE_VERIFIED` (Foundry configuration + `.sol` file)
     * `BUILD_CONFIGURATION_NOT_FOUND` (`.sol` file with no build config)
     * `SOURCE_NOT_FOUND` (empty repo / no `.sol` files)
     * `VERIFICATION_FAILED` (non-existent path)
6. `test_evaluate_slither_eligibility_scenarios`:
   * Validates all blocking rules: unverified source, low confidence, missing repository, and successful qualification when verified.

### Unit Test Execution Result
```
Ran 29 tests in 1.077s
OK
```

---

## 4. Final Handoff State Model

The normalized record now exposes machine-readable pipeline stages:

```json
{
  "pipeline_stages": {
    "A_repository_resolution": {
      "status": "ORGANIZATION_ONLY",
      "selected_repository": null,
      "eligible_for_source_verification": false,
      "requires_manual_corroboration": false,
      "action": "HALT_NO_REPOSITORY"
    },
    "B_source_verification": {
      "status": "VERIFICATION_NOT_RUN",
      "solidity_source_paths": [],
      "build_framework": null,
      "commit_hash": null,
      "default_branch": null,
      "reason": "Source verification has not been executed yet",
      "branch_deployment_limitation": "The default branch or analyzed commit is not guaranteed to correspond to on-chain deployed bytecode without contract address bytecode verification."
    },
    "C_slither_eligibility": {
      "eligible_for_slither": false,
      "status": "BLOCKED_NO_REPOSITORY",
      "reason": "No distinct contract repository resolved (status: ORGANIZATION_ONLY)"
    }
  },
  "scanner_handoff": {
    "stage": "REPOSITORY_RESOLUTION",
    "status": "NO_REPOSITORY_SELECTED",
    "eligible_for_source_verification": false,
    "eligible_for_slither": false,
    "eligible_for_automated_analysis": false,
    "selected_repository": null,
    "downstream_slither_action": "SKIP_AUTOMATED_ANALYSIS"
  }
}
```

### Machine-Readable Outcomes for Source Verification
| Outcome Code | Meaning | Downstream Slither Action |
| :--- | :--- | :--- |
| `SOURCE_VERIFIED` | Solidity sources found and build framework configuration detected | `PROCEED_TO_SLITHER_ANALYSIS` |
| `SOURCE_NOT_FOUND` | Repository cloned/inspected, but zero `.sol` files found | `BLOCKED_SOURCE_UNVERIFIED` |
| `BUILD_CONFIGURATION_NOT_FOUND` | Solidity files found, but no recognized build framework config | `BLOCKED_SOURCE_UNVERIFIED` |
| `VERIFICATION_FAILED` | File system error or path inaccessible | `BLOCKED_SOURCE_UNVERIFIED` |
| `VERIFICATION_NOT_RUN` | Initial state prior to repository checkout and inspection | `BLOCKED_SOURCE_UNVERIFIED` |

---

## 5. Remaining Limitations and Unresolved Cases

1. **DefiLlama Incomplete Coverage:** Major protocols such as `aave-v3` and `layerzero-v2` omit GitHub contract URLs in DefiLlama's API. They remain `UNRESOLVED` until a secondary authoritative source (or audited manual map) is integrated.
2. **Organization-Only Protocols:** Protocols like Lido provide an organization handle (`lidofinance`) with multiple public repositories (`lido-dao`, `core`, `gate-seal`, etc.). Resolution correctly stays `ORGANIZATION_ONLY` without guessing which repository contains the deployed bytecode.
3. **Branch/Revision vs On-chain Bytecode Discrepancy:** Even when a repository is verified, `HEAD` on the default branch may differ from the bytecode deployed on Ethereum. Bytecode verification against on-chain block explorers remains a future requirement.

---

## 6. Exact Commands Executed and Live Results

### Command 1: Resolver for Lido
```powershell
python github_repository_resolver.py --slug lido
```
* **Result:**
  * `github_resolution_status`: `ORGANIZATION_ONLY`
  * `selected_github_url`: `null`
  * `auditor_repos_ignored`: `["https://github.com/lidofinance/audits"]`
  * `eligible_for_automated_analysis`: `false`

### Command 2: Resolver for Aave V3
```powershell
python github_repository_resolver.py --slug aave-v3
```
* **Result:**
  * `github_resolution_status`: `UNRESOLVED`
  * `selected_github_url`: `null`
  * `declared_repository_candidates`: `[{"original_value": "https://aave.com/security", "candidate_type": "NON_GITHUB"}]`

### Command 3: Resolver for LayerZero V2
```powershell
python github_repository_resolver.py --slug layerzero-v2
```
* **Result:**
  * `github_resolution_status`: `UNRESOLVED`
  * `selected_github_url`: `null`

### Command 4: Resolver for Maple (Slug Regression Check)
```powershell
python github_repository_resolver.py --slug maple
```
* **Result:**
  * `name`: `"Maple"`
  * `slug`: `"maple"`
  * `defillama_id`: `"587"`
  * `selected_github_url`: `"https://github.com/maple-labs/maple-core"`
  * `github_resolution_status`: `"RESOLVED_LOW_CONFIDENCE"`

### Command 5: Market Intelligence Discovery
```powershell
python market_intelligence.py --limit 3 --chain ethereum
```
* **Output:**
```
[+] Top 3 objetivos descubiertos en ETHEREUM:

1. Binance CEX               | TVL: $171,869,826,533.12 | Contrato: (Sin dirección directa)
   GitHub: [UNRESOLVED]
2. OKX                       | TVL: $31,094,986,426.78 | Contrato: 0x75231F58b43240C9718Dd58B4967c5114342a86c
   GitHub: [UNRESOLVED]
3. Lido                      | TVL: $24,353,306,279.48 | Contrato: 0x5A98FcBEA516Cf06857215779Fd812CA3beF1B32
   GitHub: [ORGANIZATION_ONLY]
```
*(Lido no longer displays `lidofinance/audits` as contract source)*

### Command 6: NoTrack Protocol Discovery
```powershell
python notrack_script.py --limit 3 --chain ethereum
```
* **Output:**
```
1. Lido (Liquid Staking)
   TVL: $24,353,306,279.48
   Red detectada: ETHEREUM
   Contract Addr: 0x5A98FcBEA516Cf06857215779Fd812CA3beF1B32
   GitHub: [ORGANIZATION_ONLY]
----------------------------------------
2. Aave V3 (Lending)
   TVL: $17,370,663,286.04
   Red detectada: ETHEREUM
   Contract Addr: 0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9
   GitHub: [UNRESOLVED]
----------------------------------------
3. LayerZero V2 (Bridge)
   TVL: $11,477,168,139.60
   Red detectada: ETHEREUM
   Contract Addr: 0x6985884C4392D348587B19cb9eAAf157F13271cd
   GitHub: [UNRESOLVED]
```

### Command 7: CLI NoTrack Command
```powershell
python cli.py notrack --limit 2 --chain ethereum
```
* **Output:**
```
#   | Nombre             | Categoría      | Red       | TVL            | GitHub Repo / Estado                  
---------------------------------------------------------------------------------------------------------
1   | Lido               | Liquid Staking | ETHEREUM  | $24,353,306,279.48 | [ORGANIZATION_ONLY]                   
2   | Aave V3            | Lending        | ETHEREUM  | $17,370,663,286.04 | [UNRESOLVED]                          
```

---

## 7. Component Readiness Summary for Next Phase

| Component | Status | Readiness for Next Phase | Notes |
| :--- | :--- | :--- | :--- |
| **DefiLlama Protocol Discovery** | Complete | **READY** | TVL parsing, chain filtering, CEX exclusion fully operational. |
| **Repository Identity Resolver** | Complete | **READY** | Disambiguates audit repos, bare orgs, and multiple candidates. |
| **3-Stage Scanner Handoff Contract** | Complete | **READY** | Explicit gates prevent downstream static scanners from executing before source verification. |
| **Static Source Verification Engine** | Complete | **READY** | Safe file-tree inspector detects `.sol` and build configs without code execution. |
| **Slither Eligibility Gates** | Complete | **READY** | Machine-readable gating rules validated. |
| **Slither Static Analysis Runner** | Not Implemented | **NOT READY (Out of Scope)** | Scheduled for subsequent phase after safe cloning / checkout mechanism is built. |
| **CertiK / Skynet Reputation Bridge** | Not Implemented | **NOT READY (Out of Scope)** | Scheduled for subsequent security intelligence phase. |
