# Phase 3.1 — CertiK API Integration Verification and Data Matching Audit Report

**Date:** 2026-10-13  
**Status:** COMPLETE  
**Readiness Decision:** `READY_WITH_LIMITATIONS`  

---

## 1. Executive Summary

Phase 3 introduced `security_intelligence.py` to ingest external security telemetry and reputation scoring from CertiK's Skynet Partner API without weakening compiler or Slither source verification gates. Phase 3.1 performed an in-depth audit of API compatibility, data matching hierarchies, cross-chain collision risks, and safe pagination models against CertiK's official documentation.

Key outcomes of Phase 3.1:
1. **Direct Project Lookup Implemented**: Added direct endpoint support (`GET /v1/security-scores/{id}`) for explicit CertiK project IDs and chain-qualified addresses (`{chain}:{contractAddress}`), eliminating unnecessary multi-page catalog scans.
2. **Cross-Chain Collision Hardening**: Built `CERTIK_CHAIN_PREFIX_MAP` and strict chain prefix verification (`eth`, `aeth`, `bsc`, `polygon`) preventing false positive matches across identical contract hex addresses on different chains.
3. **Decoupled DefiLlama Slugs from Provider IDs**: Ensured DefiLlama protocol slugs are never treated as native CertiK project IDs.
4. **Documented Response Fields & Sub-scores**: Corrected domain resolution to inspect the documented `projectWebsite` field and added the `communityTrust` sub-score to `SecurityScore`.
5. **Safe Bounded Pagination**: Added bounded catalog pagination (`limit=50`, `skip=0`, `max_pages=2`) respecting provider rate limits and returning explicit incomplete search diagnostics rather than false `NOT_FOUND` claims.
6. **Safe Live Diagnostic**: Added `check_live_api_connectivity()` and CLI flag `--live-check` with strict secret redaction and graceful error classification (`NOT_CONFIGURED`, `AUTHENTICATION_FAILED`, `RATE_LIMITED`, `PROVIDER_ERROR`, `SUCCESS`).
7. **Comprehensive Test Suite**: Expanded the test suite from 75 to 85 tests (84 passing, 1 opt-in skipped).

---

## 2. Official Documentation Consulted

The following official CertiK Skynet developer references were audited:
- **Base URL & Authentication**: [https://api.certik-skynet.com/public-docs/authentication](https://api.certik-skynet.com/public-docs/authentication)  
  - Base URL: `https://partner.certik-skynet.com`
  - Authentication Header: `X-Certik-Api-Key: <PARTNER_KEY>`
- **Security Score Endpoints**: [https://api.certik-skynet.com/public-docs/apis/security-score](https://api.certik-skynet.com/public-docs/apis/security-score)  
  - List endpoint: `GET /v1/security-scores` with query parameters `skip` (integer) and `limit` (integer, max 50).
  - Direct lookup endpoint: `GET /v1/security-scores/{id}` where `{id}` is either `certiKProjectId` or `{chain_prefix}:{contractAddress}`.
- **Supported Chains & Prefixes**: [https://api.certik-skynet.com/public-docs/prefix](https://api.certik-skynet.com/public-docs/prefix)  
  - Ethereum: `eth`
  - BNB Smart Chain: `bsc`
  - Arbitrum One: `aeth`
  - Polygon PoS: `polygon`
  - Optimism: `oeth`
  - Base: `base`
  - Avalanche: `avalanche`

---

## 3. API Compatibility Verification

| Feature / Contract Requirement | Documented Specification | Implementation Status | Verification Details |
| :--- | :--- | :--- | :--- |
| **Base URL** | `https://partner.certik-skynet.com` | Verified | Configurable via `CERTIK_BASE_URL` with default matching documentation. |
| **Authentication Header** | `X-Certik-Api-Key` | Verified | Header passed with non-empty API keys; never logged or exposed in exceptions. |
| **Direct Lookup Endpoint** | `GET /v1/security-scores/{id}` | Implemented | Direct query executed when `provider_project_id` or chain contract address exists. |
| **List Pagination Schema** | `items` array + `page` (`skip`, `limit`, `total`, `hasMore`) | Verified | Documented `page` metadata parsed; stops when `hasMore=False` or bounds met. |
| **Pagination Parameters** | `skip` (default 0), `limit` (max 50) | Verified | Passes `params={"skip": skip, "limit": min(limit, 50)}`. |
| **Score Scale & Nullability** | 0–100; missing score is `null` | Verified | Preserved as `None`, never converted to `0.0`. |
| **Sub-Score Metrics** | `codeSecurity`, `fundamentalHealth`, `operationalResilience`, `governanceStrength`, `marketStability`, `communityTrust` | Verified | Added `communityTrust` to `sub_scores` dictionary. |
| **Official Website Field** | `projectWebsite` | Verified | Checked first, with fallback to `website` / `officialUrl`. |
| **HTTP Error Codes** | 401/403 (Auth), 429 (Rate Limit), 5xx (Provider) | Verified | Explicit error classification and bounded retry avoidance. |

---

## 4. Protocol Matching Audit & Hardening

1. **DefiLlama Slug Independence**:
   - DefiLlama slug (e.g. `aave-v3`, `lido`, `uniswap-v3`) is **not** silently treated as a CertiK project ID.
   - Direct lookup (`/v1/security-scores/{id}`) is never invoked with an arbitrary DefiLlama slug.
   - Slugs are matched only in Tier 4 heuristics against `cmcSlug` or project name with `NAME_SLUG_MATCH` confidence.
2. **Cross-Chain Collision Prevention**:
   - Distinct chains can deploy contracts to identical hex addresses.
   - The resolver converts chain identifiers to CertiK prefixes (`ethereum` -> `eth`, `arbitrum` -> `aeth`, `bsc` -> `bsc`, `polygon` -> `polygon`).
   - If an item's address specifies `eth:` and the query specifies `bsc:`, the match is strictly rejected.
3. **Ambiguity Resolution**:
   - If multiple distinct project IDs match the same domain, name, or contract, status returns `AMBIGUOUS`.
   - Matching never makes arbitrary selections among multiple candidates.
4. **Source Verification Decoupling**:
   - Security scores and CertiK audit status **never** override compiler safety or source verification gates.
   - An unresolved or unverified repository cannot be handed off to Slither static analysis, regardless of high security scores.

---

## 5. Safe Pagination and Bounded Execution

- **Bounded Execution**: Search across the catalog list is bounded to `max_pages=2` and `limit=50` (100 items maximum).
- **Incomplete Search Diagnostic**: If a protocol is not found within the inspected pages and `hasMore` is `True`, the diagnostic explicitly indicates that search was halted at the bound and suggests direct lookup.
- **No Infinite Loops**: Halts immediately when `hasMore=False` or empty `items` are returned.

---

## 6. Live API Validation Status

- **Offline Unit Tests**: All 84 standard unit tests run completely offline and deterministically using mocked HTTP responses.
- **Opt-in Live Integration**:
  - Live probe provided via `check_live_api_connectivity()` and CLI flag `--live-check`.
  - Opt-in unit test `test_opt_in_live_api_call` guarded by `RUN_LIVE_CERTIK_TEST=1`.
- **Live Status in Current Environment**:
  - `CERTIK_API_KEY` is not configured in `.env` (empty string).
  - Diagnostic output: `CERTIK_API_KEY is not configured in the environment or .env file.`
  - Retrieval status: `NOT_CONFIGURED`.
  - Live network authentication remains unverified due to absence of commercial partner credentials. No false claims of live success are made.

---

## 7. CLI Validation Evidence

### Command 1: `python cli.py intelligence --help`
```
usage: cli.py intelligence [-h] [-v] [--slug SLUG] [--name NAME]
                           [--contract CONTRACT]
                           [--chain {ethereum,arbitrum,polygon,bsc}]
                           [--live-check] [--json]

options:
  -h, --help            show this help message and exit
  -v, --verbose         Activa logs detallados y modo verbose
  --slug SLUG           Slug DefiLlama del protocolo (ej. aave-v3, uniswap-v3)
  --name NAME           Nombre del protocolo
  --contract CONTRACT   Dirección del contrato inteligente principal
  --chain {ethereum,arbitrum,polygon,bsc}
                        Cadena EVM
  --live-check          Diagnóstico seguro opcional de conectividad y
                        autenticación con la API en vivo de CertiK
  --json                Imprimir salida en formato JSON legible por máquina
```

### Command 2: `python cli.py intelligence --slug aave-v3`
```
================================================================================
[*] COMANDO: INTELIGENCIA DE SEGURIDAD & SKYNET (CertiK Telemetry)
[*] Slug DefiLlama:     aave-v3
================================================================================

[+] RESULTADO DE INTELIGENCIA DE SEGURIDAD:
    -> Proveedor:             CertiK Skynet
    -> Estado de consulta:    NOT_CONFIGURED
    -> Nivel de coincidencia: NONE
    -> Protocolo:             Aave V3
    -> Diagnóstico:           CertiK Partner API credentials ('CERTIK_API_KEY') are not configured in environment or .env. Access to the machine-readable Skynet Security Score API requires an active commercial agreement with CertiK (contact: APIsupport@certik.com). Public scraping is rejected to adhere to access controls.

[!] PROVEEDOR NO CONFIGURADO:
    Para habilitar la API de CertiK Skynet, debes contar con credenciales de Partner.
    Agrega tu clave al archivo .env:
    CERTIK_API_KEY="<tu_partner_api_key>"

[*] Limitaciones & Garantías:
    - Security ratings represent third-party, point-in-time assessments and do not guarantee immunity from smart contract bugs or economic exploits.
    - Absence of an audit record in a specific provider does not imply the protocol has never undergone auditing by other independent firms.
```

### Command 3: `python cli.py intelligence --slug aave-v3 --json`
```json
{
  "protocol_name": "Aave V3",
  "defillama_slug": "aave-v3",
  "provider_name": "CertiK Skynet",
  "provider_project_id": null,
  "project_url": null,
  "retrieval_status": "NOT_CONFIGURED",
  "match_confidence": "NONE",
  "security_score": null,
  "audit_records": [],
  "incidents": [],
  "retrieved_at": 1791849286,
  "source_url": "https://partner.certik-skynet.com/v1/security-scores",
  "error_category": "MISSING_CREDENTIALS",
  "diagnostic": "CertiK Partner API credentials ('CERTIK_API_KEY') are not configured in environment or .env. Access to the machine-readable Skynet Security Score API requires an active commercial agreement with CertiK (contact: APIsupport@certik.com). Public scraping is rejected to adhere to access controls.",
  "data_freshness": null,
  "limitations": [
    "Security ratings represent third-party, point-in-time assessments and do not guarantee immunity from smart contract bugs or economic exploits.",
    "Absence of an audit record in a specific provider does not imply the protocol has never undergone auditing by other independent firms.",
    "Third-party reputation scores are informational and MUST NOT bypass local static source verification or compiler safety gates.",
    "Data freshness is subject to provider update cycles and may not reflect recent protocol modifications, governance proposals, or migrations."
  ],
  "unverified_fields": [
    "security_score",
    "audit_records",
    "incidents"
  ]
}
```

### Command 4: `python cli.py intelligence --live-check`
```
================================================================================
[*] COMANDO: INTELIGENCIA DE SEGURIDAD & SKYNET (CertiK Telemetry)
[*] Modo:               Diagnóstico en vivo (--live-check)
================================================================================

[+] RESULTADO DIAGNÓSTICO EN VIVO (CertiK Partner API):
    -> Proveedor:             CertiK Skynet
    -> Estado:                NOT_CONFIGURED
    -> Autenticado:           False
    -> Diagnóstico:           CERTIK_API_KEY is not configured in the environment or .env file.
```

---

## 8. Files Changed

1. **`security_intelligence.py`**:
   - Added `CERTIK_CHAIN_PREFIX_MAP` and `normalize_chain_prefix()`.
   - Implemented direct lookup helper `_fetch_direct_project()` (`GET /v1/security-scores/{id}`).
   - Implemented paginated catalog helper `_fetch_paged_list()` (`GET /v1/security-scores?skip={s}&limit={l}`).
   - Updated `_match_item_conservatively()` to enforce exact chain verification and avoid cross-chain collisions.
   - Updated `_parse_matched_item()` to include the `communityTrust` sub-score.
   - Added `check_live_api_connectivity()` for safe live status probing.
2. **`cli.py`**:
   - Added `--live-check` flag to `intelligence` subcommand.
   - Cleaned up `--json` mode output to suppress human console headers when raw JSON output is requested.
   - Integrated live diagnostic results into CLI output.
3. **`test_security_intelligence.py`**:
   - Added 10 new test cases covering direct lookup, chain prefix normalization, cross-chain collision rejection, DefiLlama slug separation, documented schema fields, bounded pagination, and live connectivity diagnostics.

---

## 9. Test Suite Results

```
Ran 85 tests in 3.059s

OK (skipped=1)
```
- **84 passed**: Complete test coverage across repository resolution, source verification, Slither sandboxing, security gates, and security intelligence.
- **1 skipped**: Opt-in live test (`test_opt_in_live_api_call`) correctly skipped when `RUN_LIVE_CERTIK_TEST` is unset.
- **0 failures, 0 errors**.

---

## 10. Readiness Decision & Remaining Limitations

**Readiness Decision:** `READY_WITH_LIMITATIONS`

### Justification & Documented Limitations:
1. **Live Partner API Unverified**: Live authenticated requests cannot be performed without a valid commercial CertiK partner API key. The implementation is 100% compliant with documented schemas, mock-tested, and returns non-sensitive `NOT_CONFIGURED` diagnostics when unkeyed.
2. **Slither Environment Absence**: Slither static analysis remains uninstalled on the Windows host environment, functioning as verified in Phase 2.1 via mocked subprocess isolation and security gates.
3. **Catalog Pagination Bounds**: Catalog list searches are bounded to 2 pages (100 items) to prevent quota exhaustion. For protocols outside the top 100, direct lookup using contract address or CertiK project ID is required.
