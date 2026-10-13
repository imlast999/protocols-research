# Phase 2 — Slither Static Analysis Integration Report

**Project Directory:** `C:\Users\lxstb\Documents\amm pools project\scripts protocols research\`  
**Date:** October 12, 2026  
**Status:** Completed  
**Test Suite:** 43 passed (0 failures, 0 errors, offline deterministic)

---

## 1. Architecture Overview

Phase 2 introduces `slither_runner.py`, an isolated, gated static analysis execution engine that links the 3-stage pipeline model established in Phase 1.2 directly into static vulnerability discovery:

```
  ┌─────────────────────────────────────────────────────────────────┐
  │ Stage A: REPOSITORY_RESOLUTION (github_repository_resolver.py)   │
  │ • Extract GitHub candidates from DefiLlama                      │
  │ • Filter out audit archives (CandidateType.AUDIT_OR_DOCS)       │
  │ • Gate condition: ResolutionStatus.RESOLVED_CONFIDENT           │
  └───────────────────────────────┬─────────────────────────────────┘
                                  │
                                  ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │ Stage B: SOURCE_VERIFICATION (github_repository_resolver.py)    │
  │ • Safe static tree inspection (verify_repository_source)        │
  │ • Detect .sol files and build framework config (Foundry/Hardhat)│
  │ • Record analyzed Git commit hash and default branch            │
  │ • Gate condition: SourceVerificationStatus.SOURCE_VERIFIED      │
  └───────────────────────────────┬─────────────────────────────────┘
                                  │
                                  ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │ Stage C: SLITHER_ELIGIBILITY & EXECUTION (slither_runner.py)    │
  │ • Security Gate Check: Workspace boundary containment           │
  │ • Tool Check: Check Slither executable availability             │
  │ • Sandboxed Subprocess: subprocess.run(..., shell=False)        │
  │ • JSON Parsing & Severity Summarization                         │
  │ • Output Artifact: Structured JSON report in ./reports/slither/ │
  └─────────────────────────────────────────────────────────────────┘
```

---

## 2. Enforced Security Gates and Failure Modes

The runner refuses execution and produces distinct, machine-readable outcomes across all failure modes:

| Status Code | Gate Description | Safety Invariant |
| :--- | :--- | :--- |
| `NOT_ELIGIBLE` | Protocol resolution is not `RESOLVED_CONFIDENT` (e.g. `ORGANIZATION_ONLY`, `LOW_CONFIDENCE`, `UNRESOLVED`) OR directory is outside allowed workspace. | Prevents running scanners on uncorroborated repos, audit archives, or arbitrary system paths (directory traversal defense). |
| `TOOL_UNAVAILABLE` | Slither binary not found in system `PATH`. | Reports clear error and pip installation steps instead of throwing unhandled exceptions. |
| `PREREQUISITE_MISSING` | Non-existent directory, zero `.sol` files found, missing build config, missing commit hash, or compilation failure. | Prohibits guessing contract paths and forbids running automatic package managers (`npm install`, `forge build`) on untrusted code. |
| `TIMED_OUT` | Subprocess execution exceeded the bounded timeout (configurable, default 120s). | Prevents unbounded process hangs and infinite recursion. |
| `ANALYSIS_FAILED` | Internal Slither crash or unrecoverable execution error. | Preserves stderr diagnostics without crashing the caller. |
| `COMPLETED` | Slither executed cleanly and produced structured findings (or zero findings). | Structured JSON output produced with full metadata and explicit limitations. |

---

## 3. Sandboxed Subprocess Execution Details

- **No Shell Execution (`shell=False`):** Commands are passed as tokenized argument lists (`[slither_path, ".", "--json", output_json_path]`). No command shell (`cmd.exe` or `powershell.exe`) is invoked, eliminating command-injection risks.
- **Exit Code Agnosticism:** Slither returns exit code `1-255` when issues are detected, but also when compilation errors happen. The runner inspects the generated JSON output and stderr signatures to verify whether vulnerabilities were found or compilation failed, rather than equating non-zero exit codes with vulnerabilities.
- **Zero Build Hooks / No Dependency Autoinstall:** Untrusted cloned codebases often contain malicious `preinstall` or `postinstall` npm scripts. The runner strictly avoids running build scripts. If compilation fails due to missing dependencies, it halts with `PREREQUISITE_MISSING`.

---

## 4. Test Suite and Deterministic Validation

The unit test suite was extended with 14 new tests in `test_slither_runner.py` (total project test count: 43 passing tests):

### Test Cases in `test_slither_runner.py`:
1. `test_tool_unavailable_when_slither_missing`: Verifies `TOOL_UNAVAILABLE` when `slither` is missing from `PATH`.
2. `test_invalid_input_directory`: Verifies `PREREQUISITE_MISSING` when target directory does not exist.
3. `test_directory_outside_allowed_workspace`: Verifies `NOT_ELIGIBLE` when target directory is outside the workspace root.
4. `test_ineligible_scanner_handoff_rejected`: Verifies rejection when resolution status is `UNRESOLVED` or `RESOLVED_LOW_CONFIDENCE`.
5. `test_failed_or_incomplete_source_verification_rejected`: Verifies rejection when build configuration is missing.
6. `test_missing_commit_hash_rejected`: Verifies rejection when commit hash cannot be identified.
7. `test_subprocess_timeout_handling`: Verifies clean timeout handling producing `TIMED_OUT`.
8. `test_nonzero_exit_code_with_valid_findings`: Verifies that Slither exit code 1 with valid findings produces `COMPLETED` with parsed findings.
9. `test_successful_execution_zero_findings`: Verifies clean run with 0 findings produces `COMPLETED`.
10. `test_compilation_failure_reports_prerequisite_missing`: Verifies that compiler errors report `PREREQUISITE_MISSING` without executing scripts.
11. `test_malformed_json_output_handling`: Verifies robust handling of corrupted JSON or Slither crashes.
12. `test_json_result_schema_and_serialization`: Validates all JSON schema fields, summary counts, and disk artifact persistence.
13. `test_cli_slither_rejects_unresolved_protocol`: Verifies that `cli.py slither --slug lido` rejects unconfident protocols early.
14. `test_cli_slither_executes_when_confident`: Verifies that `cli.py slither` executes the runner when resolution is confident.

### Execution Output:
```
Ran 43 tests in 1.159s
OK
```

---

## 5. Live CLI Demonstration and Example Outputs

### Case 1: Rejection of Unresolved Protocol (`lido` -> `ORGANIZATION_ONLY`)
```powershell
python cli.py slither -t . --slug lido
```
**Output:**
```
================================================================================
[*] COMANDO: ANÁLISIS ESTÁTICO SLITHER (Sandboxed Subprocess)
[*] Directorio objetivo: .
[*] Validación de protocolo (DefiLlama slug): lido
================================================================================
[*] Verificando resolución del repositorio para 'lido'...
    -> Estado de resolución GitHub: ORGANIZATION_ONLY

[!] ACCESO RECHAZADO POR PUERTA DE ENLACE DE SEGURIDAD:
    El protocolo 'lido' tiene estado de resolución 'ORGANIZATION_ONLY'.
    Motivo: Protocol declares GitHub organization(s) and audit/documentation repositories, but no contract source repository is declared
    El análisis Slither automatizado sólo está permitido cuando el repositorio
    ha sido resuelto con CONFIDENCIA ALTA (RESOLVED_CONFIDENT).
    Se omite la ejecución de Slither para proteger la integridad del pipeline.
```

### Case 2: Tool Availability Rejection with Clear Setup Guidance
When running against a verified directory (`./scratch_repo` with `.sol` and `foundry.toml`) on a system without Slither installed:
```powershell
python cli.py slither -t ./scratch_repo --commit abc123def456
```
**Output:**
```
[+] RESULTADO DEL ANÁLISIS SLITHER:
    -> Estado del análisis:      TOOL_UNAVAILABLE
    -> Protocolo:                 Local Repository
    -> Commit Git analizado:      abc123def456
    -> Verificación de fuentes:   SOURCE_VERIFIED
    -> Versión de Slither:        N/A
    -> Duración:                  0.01 s
    -> Código de salida:          N/A
    -> Diagnóstico / Motivo:      Slither executable 'slither' not found in PATH. Please install Slither ('pip install slither-analyzer') and ensure it is available in PATH.

[!] INSTRUCCIONES DE INSTALACIÓN DE SLITHER:
    Para habilitar el escáner estático Slither:
    1. Asegúrate de tener Python y pip instalados.
    2. Ejecuta: pip install slither-analyzer
    3. Instala solc-select: pip install solc-select && solc-select install 0.8.20

[*] Limitaciones del análisis:
    - Static analysis cannot detect complex economic exploits, price oracle manipulation, or flash loan arbitrage vulnerabilities.
    - Repository source code on default branch is not guaranteed to correspond to on-chain deployed bytecode without contract address bytecode verification.
```

### Case 3: Example Structured JSON Result Schema
```json
{
  "protocol_name": "Aave V3",
  "defillama_slug": "aave-v3",
  "repository_url": "https://github.com/aave/aave-v3-core",
  "commit_hash": "a1b2c3d4e5f67890",
  "source_verification_status": "SOURCE_VERIFIED",
  "slither_version": "0.10.0",
  "analysis_status": "COMPLETED",
  "started_at": 1791836850,
  "duration_seconds": 4.512,
  "exit_code": 1,
  "findings": [
    {
      "detector_id": "reentrancy-eth",
      "title": "Reentrancy Eth",
      "severity": "High",
      "confidence": "Medium",
      "description": "Reentrancy vulnerability in Pool.withdraw()",
      "affected_contract": "Pool",
      "source_locations": [
        {
          "file": "contracts/protocol/pool/Pool.sol",
          "lines": [145, 146, 147],
          "start": 5210,
          "length": 120
        }
      ],
      "source_file_paths": [
        "contracts/protocol/pool/Pool.sol"
      ]
    }
  ],
  "findings_summary": {
    "High": 1,
    "Medium": 0,
    "Low": 0,
    "Informational": 2,
    "Optimization": 5,
    "total": 8
  },
  "raw_artifact_path": "./reports/slither/slither_aave-v3_a1b2c3d4_1791836850.json",
  "limitations": [
    "Static analysis cannot detect complex economic exploits, price oracle manipulation, or flash loan arbitrage vulnerabilities.",
    "Repository source code on default branch is not guaranteed to correspond to on-chain deployed bytecode without contract address bytecode verification.",
    "Slither analysis is limited to recognized Solidity patterns and detector heuristics; false positives and false negatives may occur.",
    "Analysis results do not imply or constitute a certification of security, formal verification, or audit guarantee."
  ],
  "reason": "Slither analysis completed successfully: 8 finding(s) detected across Foundry codebase."
}
```

---

## 6. Remaining Limitations & Boundaries

1. **Local Repository Prerequisite:** The runner operates strictly on local directories. It does not clone remote git repositories automatically. A safe git checkout engine (with commit pinning and size limits) is required before end-to-end automated URL-to-Slither execution can occur.
2. **Compiler Version Management:** Repositories using historical `solc` versions (e.g. `^0.5.0` or `^0.6.0`) require matching local compilers via `solc-select`. When versions are mismatched, the runner safely produces `PREREQUISITE_MISSING`.
3. **No Bytecode Discrepancy Verification:** Static analysis targets source files; on-chain contracts may have been compiled with different optimization flags, metadata salts, or modified constructors.
4. **Out-of-Scope Modules:** CertiK/Skynet intelligence, on-chain transaction simulation, and contract exploitation logic were intentionally not implemented in this phase.

---

## 7. Component Readiness Summary

| Component | Status | Readiness for Next Phase | Notes |
| :--- | :--- | :--- | :--- |
| **DefiLlama Protocol Discovery** | Validated | **READY** | Backward-compatible filtering & normalization. |
| **GitHub Repository Resolver** | Validated | **READY** | Audit repositories & ambiguous cases separated. |
| **Source Verification Engine** | Validated | **READY** | Detects `.sol` files and frameworks without execution. |
| **Slither Static Analysis Runner** | Completed | **READY** | Sandboxed subprocess, 6-state safety gates, JSON output schema. |
| **CLI `slither` Command** | Integrated | **READY** | Supports interactive and non-interactive execution with gate checks. |
| **CertiK / Skynet Reputation Bridge** | Not Implemented | **NOT READY** | Scheduled for next phase. |
