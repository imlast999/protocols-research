"""
defi_research: Modular DeFi Protocol Research & Security Analysis Suite.
Integrates DefiLlama market telemetry, automated GitHub repository resolution,
controlled Slither static analysis sandboxing, and CertiK Skynet security intelligence.
"""

from typing import Any, List

__version__ = "1.0.0"

# Mapping of exported attribute names to their submodules for PEP 562 lazy loading.
# Prevents eager loading of Web3 and heavy dependencies on simple package import.
_LAZY_EXPORTS = {
    # config
    "ACCESS_MODIFIERS": "defi_research.config",
    "CHAINS": "defi_research.config",
    "SENSITIVE_KEYWORDS": "defi_research.config",
    "ChainConfig": "defi_research.config",
    "DEFAULT_OUTPUT_DIR": "defi_research.config",
    "WORKSPACE_ROOT": "defi_research.config",
    # contract_permission_scanner
    "ContractScanner": "defi_research.contract_permission_scanner",
    # github_repository_resolver
    "CandidateType": "defi_research.github_repository_resolver",
    "RepoCandidate": "defi_research.github_repository_resolver",
    "ResolutionStatus": "defi_research.github_repository_resolver",
    "SlitherEligibilityStatus": "defi_research.github_repository_resolver",
    "SourceVerificationRecord": "defi_research.github_repository_resolver",
    "SourceVerificationStatus": "defi_research.github_repository_resolver",
    "evaluate_slither_eligibility": "defi_research.github_repository_resolver",
    "normalize_github_repo_url": "defi_research.github_repository_resolver",
    "normalize_protocol_record": "defi_research.github_repository_resolver",
    "resolve_github_repo_from_slug": "defi_research.github_repository_resolver",
    "resolve_github_repository": "defi_research.github_repository_resolver",
    "verify_repository_source": "defi_research.github_repository_resolver",
    "DefiLlamaClient": "defi_research.github_repository_resolver",
    # market_intelligence
    "CHAIN_ALIAS_MAP": "defi_research.market_intelligence",
    "clean_contract_address": "defi_research.market_intelligence",
    "get_high_value_targets": "defi_research.market_intelligence",
    # notrack_script
    "audit_vulnerable_targets": "defi_research.notrack_script",
    "get_vulnerable_targets": "defi_research.notrack_script",
    # security_intelligence
    "AuditRecord": "defi_research.security_intelligence",
    "CertikSkynetProvider": "defi_research.security_intelligence",
    "MatchConfidence": "defi_research.security_intelligence",
    "ProtocolQuery": "defi_research.security_intelligence",
    "RetrievalStatus": "defi_research.security_intelligence",
    "SecurityIncident": "defi_research.security_intelligence",
    "SecurityIntelligenceResult": "defi_research.security_intelligence",
    "SecurityScore": "defi_research.security_intelligence",
    "check_live_api_connectivity": "defi_research.security_intelligence",
    "normalize_chain_prefix": "defi_research.security_intelligence",
    "query_protocol_security_intelligence": "defi_research.security_intelligence",
    "sanitize_secret_text": "defi_research.security_intelligence",
    # pipeline
    "PipelineOverallStatus": "defi_research.pipeline",
    "PipelineRunSummary": "defi_research.pipeline",
    "ProtocolPipelineReport": "defi_research.pipeline",
    "StageOutcome": "defi_research.pipeline",
    "StageStatus": "defi_research.pipeline",
    "run_pipeline": "defi_research.pipeline",
    "run_protocol_pipeline": "defi_research.pipeline",
    "save_protocol_report": "defi_research.pipeline",
    # slither_runner
    "AnalysisStatus": "defi_research.slither_runner",
    "SlitherAnalysisResult": "defi_research.slither_runner",
    "build_docker_run_args": "defi_research.slither_runner",
    "check_docker_availability": "defi_research.slither_runner",
    "check_docker_image_availability": "defi_research.slither_runner",
    "check_slither_availability": "defi_research.slither_runner",
    "is_directory_in_workspace": "defi_research.slither_runner",
    "run_slither_analysis": "defi_research.slither_runner",
    "sanitize_output_text": "defi_research.slither_runner",
}

__all__ = list(_LAZY_EXPORTS.keys()) + ["__version__"]


def __getattr__(name: str) -> Any:
    """Lazily load symbols upon attribute access (PEP 562)."""
    if name in _LAZY_EXPORTS:
        module_path = _LAZY_EXPORTS[name]
        module = __import__(module_path, fromlist=[name])
        val = getattr(module, name)
        globals()[name] = val
        return val
    raise AttributeError(f"module 'defi_research' has no attribute '{name}'")


def __dir__() -> List[str]:
    return sorted(list(globals().keys()) + __all__)
