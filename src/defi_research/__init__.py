"""
defi_research: Modular DeFi Protocol Research & Security Analysis Suite.
Integrates DefiLlama market telemetry, automated GitHub repository resolution,
controlled Slither static analysis sandboxing, and CertiK Skynet security intelligence.
"""

__version__ = "1.0.0"

from defi_research.config import ACCESS_MODIFIERS, CHAINS, SENSITIVE_KEYWORDS, ChainConfig
from defi_research.contract_permission_scanner import ContractScanner
from defi_research.github_repository_resolver import (
    CandidateType,
    RepoCandidate,
    ResolutionStatus,
    SlitherEligibilityStatus,
    SourceVerificationRecord,
    SourceVerificationStatus,
    evaluate_slither_eligibility,
    normalize_github_repo_url,
    normalize_protocol_record,
    resolve_github_repo_from_slug,
    verify_repository_source,
)
from defi_research.market_intelligence import (
    CHAIN_ALIAS_MAP,
    clean_contract_address,
    get_high_value_targets,
)
from defi_research.notrack_script import (
    audit_vulnerable_targets,
    get_vulnerable_targets,
)
from defi_research.security_intelligence import (
    AuditRecord,
    CertikSkynetProvider,
    MatchConfidence,
    ProtocolQuery,
    RetrievalStatus,
    SecurityIncident,
    SecurityIntelligenceResult,
    SecurityScore,
    check_live_api_connectivity,
    normalize_chain_prefix,
    query_protocol_security_intelligence,
    sanitize_secret_text,
)
from defi_research.pipeline import (
    PipelineOverallStatus,
    PipelineRunSummary,
    ProtocolPipelineReport,
    StageOutcome,
    StageStatus,
    run_pipeline,
    run_protocol_pipeline,
    save_protocol_report,
)
from defi_research.slither_runner import (
    AnalysisStatus,
    SlitherAnalysisResult,
    build_docker_run_args,
    check_docker_availability,
    check_docker_image_availability,
    check_slither_availability,
    is_directory_in_workspace,
    run_slither_analysis,
    sanitize_output_text,
)
