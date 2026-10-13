"""
GitHub Repository Resolution Module for DefiLlama Protocols.

Provides reliable, deterministic extraction, normalization, and validation
of GitHub repositories from DefiLlama metadata without fabricating repository names.
"""

import logging
import re
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("GitHubResolver")

# Reserved GitHub paths / keywords that cannot be owner names or represent non-repo pages
GITHUB_RESERVED_NAMES: Set[str] = {
    "about",
    "account",
    "blog",
    "business",
    "contact",
    "customer-stories",
    "enterprise",
    "events",
    "explore",
    "features",
    "git-guides",
    "issues",
    "join",
    "login",
    "marketplace",
    "mobile",
    "new",
    "nonprofit",
    "notifications",
    "orgs",
    "organizations",
    "pricing",
    "pulls",
    "readme",
    "search",
    "security",
    "settings",
    "site",
    "sponsors",
    "stars",
    "team",
    "topics",
    "trending",
    "users",
    "watching",
}

# Known security audit firms and third-party report repositories.
# These should NOT be attributed as the protocol's own smart contract repository.
KNOWN_AUDITOR_ORGS: Set[str] = {
    "peckshield",
    "trailofbits",
    "consensys",
    "sigp",
    "mixbytes",
    "certikproject",
    "certik",
    "slowmist",
    "code-423n4",
    "sherlock-audit",
    "spearbit",
    "pashov",
    "halbornsecurity",
    "chainsecurity",
    "paladinsec",
    "solidity-audits",
    "ackee-blockchain",
    "zellic",
    "cantina",
    "hexens-io",
    "blocksecteam",
    "zenith-security",
}

KNOWN_AUDITOR_REPOSITORIES: Set[str] = {
    "peckshield/publications",
    "trailofbits/publications",
    "sigp/public-audits",
    "consensys/consensys-diligence-audits",
    "mixbytes/audits",
    "openzeppelin/openzeppelin-contracts",
    "openzeppelin/audits",
    "certikproject/audit-reports",
    "slowmist/knowledge-base",
    "code-423n4/audits",
    "code-423n4/org",
    "sherlock-audit/audits",
    "spearbit/audits",
    "pashov/audits",
    "halbornsecurity/publicreports",
    "chainsecurity/audits",
    "paladinsec/audits",
    "solidity-audits/audits",
    "ackee-blockchain/audits",
    "zellic/publications",
    "cantina/audits",
    "hexens-io/audits",
    "blocksecteam/audit-reports",
    "zenith-security/reports",
}


# Repository names commonly used for audit reports or documentation rather than contract source code
AUDIT_AND_DOCS_REPO_NAMES: Set[str] = {
    "audits",
    "audit",
    "audit-reports",
    "audit_reports",
    "auditing",
    "security-audits",
    "publications",
    "public-audits",
    "publicreports",
    "knowledge-base",
    "reports",
    "docs",
    "documentation",
    "wiki",
    "whitepaper",
    "gitbook",
}


class CandidateType(str, Enum):
    REPOSITORY = "REPOSITORY"
    ORGANIZATION_OR_USER = "ORGANIZATION_OR_USER"
    AUDIT_OR_DOCS_REPOSITORY = "AUDIT_OR_DOCS_REPOSITORY"
    ISSUE_OR_PR = "ISSUE_OR_PR"
    TREE_OR_BLOB = "TREE_OR_BLOB"
    NON_GITHUB = "NON_GITHUB"
    INVALID = "INVALID"


class ResolutionStatus(str, Enum):
    RESOLVED_CONFIDENT = "RESOLVED_CONFIDENT"
    RESOLVED_LOW_CONFIDENCE = "RESOLVED_LOW_CONFIDENCE"
    ORGANIZATION_ONLY = "ORGANIZATION_ONLY"
    AMBIGUOUS = "AMBIGUOUS"
    UNRESOLVED = "UNRESOLVED"
    MALFORMED_RECORD = "MALFORMED_RECORD"


class SourceVerificationStatus(str, Enum):
    SOURCE_VERIFIED = "SOURCE_VERIFIED"
    SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
    BUILD_CONFIGURATION_NOT_FOUND = "BUILD_CONFIGURATION_NOT_FOUND"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    VERIFICATION_NOT_RUN = "VERIFICATION_NOT_RUN"


class SlitherEligibilityStatus(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    BLOCKED_SOURCE_UNVERIFIED = "BLOCKED_SOURCE_UNVERIFIED"
    BLOCKED_NO_REPOSITORY = "BLOCKED_NO_REPOSITORY"
    BLOCKED_AMBIGUOUS_OR_UNRESOLVED = "BLOCKED_AMBIGUOUS_OR_UNRESOLVED"
    BLOCKED_NEEDS_CORROBORATION = "BLOCKED_NEEDS_CORROBORATION"


@dataclass
class SourceVerificationRecord:
    status: SourceVerificationStatus = SourceVerificationStatus.VERIFICATION_NOT_RUN
    solidity_source_paths: List[str] = field(default_factory=list)
    build_framework: Optional[str] = None
    commit_hash: Optional[str] = None
    default_branch: Optional[str] = None
    reason: Optional[str] = "Source verification has not been executed yet"
    branch_deployment_limitation: str = (
        "The default branch or analyzed commit is not guaranteed to correspond to on-chain deployed bytecode without contract address bytecode verification."
    )
    repo_path: Optional[str] = None
    is_git_repo: bool = False
    workflow_type: str = "REPOSITORY_CHECKOUT"


@dataclass
class GitHubUrlInfo:
    raw_url: str
    candidate_type: CandidateType
    owner: Optional[str] = None
    repo: Optional[str] = None
    normalized_url: Optional[str] = None
    is_auditor_repo: bool = False
    details: Optional[str] = None


@dataclass
class RepoCandidate:
    original_value: str
    source_field: str
    candidate_type: CandidateType
    normalized_url: Optional[str]
    owner: Optional[str]
    repo: Optional[str]
    is_auditor_repo: bool
    notes: Optional[str] = None


def parse_github_url(raw_input: Any) -> GitHubUrlInfo:
    """
    Parses and categorizes a potential GitHub URL or handle.
    Normalizes valid repository references into: https://github.com/OWNER/REPO.
    """
    if not raw_input or not isinstance(raw_input, str):
        return GitHubUrlInfo(
            raw_url=str(raw_input) if raw_input is not None else "",
            candidate_type=CandidateType.INVALID,
            details="Input is null, empty or not a string",
        )

    cleaned = raw_input.strip()

    # Reject non-URL or dangerous schemes
    if cleaned.startswith(("javascript:", "data:", "file:", "ftp:")):
        return GitHubUrlInfo(
            raw_url=cleaned,
            candidate_type=CandidateType.INVALID,
            details="Rejected non-http scheme",
        )

    # Convert git SSH syntax (e.g., git@github.com:owner/repo.git)
    if cleaned.startswith("git@github.com:"):
        path_part = cleaned.split("git@github.com:", 1)[1]
        cleaned = f"https://github.com/{path_part}"
    elif cleaned.startswith("github.com/"):
        cleaned = f"https://{cleaned}"
    elif not (cleaned.startswith("http://") or cleaned.startswith("https://")):
        # If it's a bare handle like "lidofinance" or "owner/repo" without scheme
        if re.match(r"^[A-Za-z0-9_-]+$", cleaned):
            # Single handle without slashes -> Organization or User handle
            if cleaned.lower() in GITHUB_RESERVED_NAMES:
                return GitHubUrlInfo(
                    raw_url=raw_input,
                    candidate_type=CandidateType.INVALID,
                    details="Reserved GitHub keyword",
                )
            return GitHubUrlInfo(
                raw_url=raw_input,
                candidate_type=CandidateType.ORGANIZATION_OR_USER,
                owner=cleaned,
                normalized_url=f"https://github.com/{cleaned}",
                details="Bare GitHub organization or user handle",
            )
        elif re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", cleaned):
            cleaned = f"https://github.com/{cleaned}"
        else:
            return GitHubUrlInfo(
                raw_url=raw_input,
                candidate_type=CandidateType.INVALID,
                details="Malformed URL or handle format",
            )

    try:
        parsed = urlparse(cleaned)
    except Exception as exc:
        return GitHubUrlInfo(
            raw_url=raw_input,
            candidate_type=CandidateType.INVALID,
            details=f"URL parsing exception: {exc}",
        )

    # Check domain
    netloc = parsed.netloc.lower()
    if ":" in netloc:
        netloc = netloc.split(":")[0]

    if netloc not in ("github.com", "www.github.com"):
        return GitHubUrlInfo(
            raw_url=raw_input,
            candidate_type=CandidateType.NON_GITHUB,
            details=f"Domain '{netloc}' is not github.com",
        )

    # Parse path segments
    path = parsed.path.strip("/")
    if not path:
        return GitHubUrlInfo(
            raw_url=raw_input,
            candidate_type=CandidateType.INVALID,
            details="Root github.com URL without path",
        )

    segments = [s for s in path.split("/") if s]
    if not segments:
        return GitHubUrlInfo(
            raw_url=raw_input,
            candidate_type=CandidateType.INVALID,
            details="Empty path on github.com",
        )

    owner = segments[0]
    # Validate owner name
    if owner.lower() in GITHUB_RESERVED_NAMES or not re.match(r"^[A-Za-z0-9](?:[A-Za-z0-9-_]*[A-Za-z0-9])?$", owner):
        return GitHubUrlInfo(
            raw_url=raw_input,
            candidate_type=CandidateType.INVALID,
            details=f"Owner '{owner}' is reserved or invalid",
        )

    # Case 1: Exactly 1 segment -> Organization or User Profile
    if len(segments) == 1:
        return GitHubUrlInfo(
            raw_url=raw_input,
            candidate_type=CandidateType.ORGANIZATION_OR_USER,
            owner=owner,
            normalized_url=f"https://github.com/{owner}",
            details="GitHub organization or user profile page",
        )

    raw_repo = segments[1]
    # Strip .git suffix if present
    repo = raw_repo[:-4] if raw_repo.lower().endswith(".git") else raw_repo

    # Validate repo name
    if not re.match(r"^[A-Za-z0-9_.-]+$", repo) or repo in (".", ".."):
        return GitHubUrlInfo(
            raw_url=raw_input,
            candidate_type=CandidateType.INVALID,
            details=f"Repository name '{repo}' is invalid",
        )

    repo_key = f"{owner.lower()}/{repo.lower()}"
    is_audit_or_docs = (
        repo_key in KNOWN_AUDITOR_REPOSITORIES
        or owner.lower() in KNOWN_AUDITOR_ORGS
        or repo.lower() in AUDIT_AND_DOCS_REPO_NAMES
    )
    normalized_repo_url = f"https://github.com/{owner}/{repo}"

    if is_audit_or_docs:
        return GitHubUrlInfo(
            raw_url=raw_input,
            candidate_type=CandidateType.AUDIT_OR_DOCS_REPOSITORY,
            owner=owner,
            repo=repo,
            normalized_url=normalized_repo_url,
            is_auditor_repo=True,
            details=f"Identified as audit report or documentation repository ('{owner}/{repo}')",
        )

    # Case 2: Sub-resource: Issues or Pull Requests
    if len(segments) >= 3 and segments[2].lower() in ("issues", "pull", "pulls"):
        return GitHubUrlInfo(
            raw_url=raw_input,
            candidate_type=CandidateType.ISSUE_OR_PR,
            owner=owner,
            repo=repo,
            normalized_url=normalized_repo_url,
            is_auditor_repo=False,
            details=f"Points to GitHub {segments[2].lower()} instead of repository root",
        )

    # Case 3: Sub-resource: tree or blob (subdirectories or files)
    if len(segments) >= 3 and segments[2].lower() in ("tree", "blob", "commits", "commit"):
        return GitHubUrlInfo(
            raw_url=raw_input,
            candidate_type=CandidateType.TREE_OR_BLOB,
            owner=owner,
            repo=repo,
            normalized_url=normalized_repo_url,
            is_auditor_repo=False,
            details=f"Subpath pointing to '{segments[2]}'",
        )

    # Case 4: Standard repository URL
    return GitHubUrlInfo(
        raw_url=raw_input,
        candidate_type=CandidateType.REPOSITORY,
        owner=owner,
        repo=repo,
        normalized_url=normalized_repo_url,
        is_auditor_repo=False,
        details="Valid GitHub repository URL",
    )


def extract_candidates(protocol: Dict[str, Any]) -> List[RepoCandidate]:
    """
    Extracts all potential GitHub candidates from known DefiLlama fields:
    - 'github' (list or string)
    - 'audit_links' (list)
    - 'url' (string)
    - 'audit_note' (string)
    - 'description' (string)
    """
    candidates: List[RepoCandidate] = []

    def _process_item(val: Any, field_name: str):
        if not val or not isinstance(val, str):
            return
        parsed_info = parse_github_url(val)
        candidates.append(
            RepoCandidate(
                original_value=val,
                source_field=field_name,
                candidate_type=parsed_info.candidate_type,
                normalized_url=parsed_info.normalized_url,
                owner=parsed_info.owner,
                repo=parsed_info.repo,
                is_auditor_repo=parsed_info.is_auditor_repo,
                notes=parsed_info.details,
            )
        )

    # 1. Field: 'github'
    raw_github = protocol.get("github")
    if isinstance(raw_github, list):
        for entry in raw_github:
            _process_item(entry, "github")
    elif isinstance(raw_github, str):
        _process_item(raw_github, "github")

    # 2. Field: 'audit_links'
    raw_audits = protocol.get("audit_links")
    if isinstance(raw_audits, list):
        for link in raw_audits:
            _process_item(link, "audit_links")
    elif isinstance(raw_audits, str):
        _process_item(raw_audits, "audit_links")

    # 3. Field: 'url' (official website if it happens to be GitHub)
    raw_url = protocol.get("url")
    if isinstance(raw_url, str) and "github.com" in raw_url:
        _process_item(raw_url, "url")

    # 4. Search for regex URLs in free text fields (description, audit_note)
    github_url_regex = re.compile(r"https?://github\.com/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?")
    for text_field in ("audit_note", "description"):
        text_val = protocol.get(text_field)
        if isinstance(text_val, str):
            for match in github_url_regex.findall(text_val):
                _process_item(match, text_field)

    return candidates


def resolve_github_repository(protocol: Dict[str, Any]) -> Tuple[ResolutionStatus, Optional[str], Dict[str, Any]]:
    """
    Resolves the most trustworthy GitHub repository for a protocol record.
    Returns:
        (ResolutionStatus, selected_github_url, resolution_evidence_dict)
    """
    if not isinstance(protocol, dict) or not (protocol.get("name") or protocol.get("slug")):
        return (
            ResolutionStatus.MALFORMED_RECORD,
            None,
            {
                "reason": "Protocol record is not a valid dictionary or lacks name/slug",
                "declared_candidates": [],
                "confidence": "NONE",
            },
        )

    candidates = extract_candidates(protocol)

    declared_orgs: Set[str] = set()
    valid_repo_candidates: Dict[str, List[RepoCandidate]] = {}
    auditor_repos_found: List[str] = []

    for c in candidates:
        if c.candidate_type == CandidateType.ORGANIZATION_OR_USER and c.owner:
            declared_orgs.add(c.owner.lower())
        elif c.candidate_type in (CandidateType.REPOSITORY, CandidateType.TREE_OR_BLOB, CandidateType.AUDIT_OR_DOCS_REPOSITORY) and c.normalized_url:
            if c.is_auditor_repo or c.candidate_type == CandidateType.AUDIT_OR_DOCS_REPOSITORY:
                auditor_repos_found.append(c.normalized_url)
            else:
                norm_key = c.normalized_url.lower()
                valid_repo_candidates.setdefault(norm_key, []).append(c)

    # Scenario 1: No valid repository candidates at all
    if not valid_repo_candidates:
        if declared_orgs:
            # Protocol declared one or more GitHub organizations, but no specific contract repo
            reason = (
                "Protocol declares GitHub organization(s) and audit/documentation repositories, but no contract source repository is declared"
                if auditor_repos_found
                else "Protocol declares GitHub organization(s) but no specific repository"
            )
            return (
                ResolutionStatus.ORGANIZATION_ONLY,
                None,
                {
                    "reason": reason,
                    "declared_organizations": [f"https://github.com/{org}" for org in sorted(declared_orgs)],
                    "auditor_repos_ignored": auditor_repos_found,
                    "audit_repositories_identified": auditor_repos_found,
                    "confidence": "NONE",
                },
            )
        return (
            ResolutionStatus.UNRESOLVED,
            None,
            {
                "reason": "No valid GitHub repository metadata found in protocol record",
                "auditor_repos_ignored": auditor_repos_found,
                "confidence": "NONE",
            },
        )

    # Scenario 2: Exactly 1 distinct valid repository candidate
    if len(valid_repo_candidates) == 1:
        repo_url_key = next(iter(valid_repo_candidates.keys()))
        repo_objs = valid_repo_candidates[repo_url_key]
        canonical_url = repo_objs[0].normalized_url
        owner = (repo_objs[0].owner or "").lower()

        # Check corroboration against declared organization or primary metadata
        matches_declared_org = owner in declared_orgs
        sources = {c.source_field for c in repo_objs}
        # True primary sources where repo URL is directly declared
        is_declared_primary = any(
            c.candidate_type in (CandidateType.REPOSITORY, CandidateType.TREE_OR_BLOB)
            and c.source_field in ("github", "url")
            for c in repo_objs
        )

        if matches_declared_org or is_declared_primary:
            confidence = "HIGH"
            status = ResolutionStatus.RESOLVED_CONFIDENT
            reason = "Single distinct repository verified with official organization/metadata corroboration"
        else:
            confidence = "LOW"
            status = ResolutionStatus.RESOLVED_LOW_CONFIDENCE
            reason = "Repository discovered in audit links without official organization corroboration; requires verification before automated analysis"

        return (
            status,
            canonical_url,
            {
                "reason": reason,
                "confidence": confidence,
                "matched_against_declared_org": matches_declared_org,
                "sources": list(sources),
                "original_references": [c.original_value for c in repo_objs],
                "auditor_repos_ignored": auditor_repos_found,
            },
        )

    # Scenario 3: Multiple distinct repository candidates
    # Check if exactly one matches the explicitly declared GitHub organization
    if declared_orgs:
        matching_org_candidates = [
            (url, objs)
            for url, objs in valid_repo_candidates.items()
            if (objs[0].owner or "").lower() in declared_orgs
        ]
        if len(matching_org_candidates) == 1:
            canonical_url = matching_org_candidates[0][1][0].normalized_url
            return (
                ResolutionStatus.RESOLVED_CONFIDENT,
                canonical_url,
                {
                    "reason": "Disambiguated candidate that matches protocol's declared GitHub organization",
                    "confidence": "HIGH",
                    "competing_candidates": list(valid_repo_candidates.keys()),
                    "matched_declared_org": matching_org_candidates[0][1][0].owner,
                    "auditor_repos_ignored": auditor_repos_found,
                },
            )

    # Truly ambiguous: multiple valid repos without authoritative tie-breaker
    return (
        ResolutionStatus.AMBIGUOUS,
        None,
        {
            "reason": "Multiple conflicting repository candidates without authoritative primary",
            "candidates": list(valid_repo_candidates.keys()),
            "confidence": "NONE",
            "auditor_repos_ignored": auditor_repos_found,
        },
    )


def extract_github_repo(protocol: Dict[str, Any]) -> Tuple[ResolutionStatus, Optional[str], Dict[str, Any]]:
    """
    Primary requirement for Step 2:
    Resolves the candidate GitHub repository for a protocol record.
    Returns (ResolutionStatus, selected_github_url, resolution_evidence_dict).
    """
    return resolve_github_repository(protocol)


def normalize_github_repo_url(url: str) -> GitHubUrlInfo:
    """Convenience alias for parse_github_url."""
    return parse_github_url(url)


def verify_repository_source(
    repo_path: str,
    default_branch: Optional[str] = None,
    commit_hash: Optional[str] = None,
) -> SourceVerificationRecord:
    """
    Safely inspects a local repository directory tree without executing arbitrary code,
    scripts, build hooks, or package managers.

    Checks:
    1. Directory existence, canonicalization, and accessibility.
    2. Git commit hash extraction from local .git metadata (with packed-refs and git fallback).
    3. Strict validation: if commit_hash is supplied, it MUST match the actual local Git checkout.
    4. Solidity (.sol) source file presence.
    5. Build system configuration (Foundry, Hardhat, Truffle, Brownie, Ape).
    6. Distinguishes REPOSITORY_CHECKOUT from LOCAL_UNTRACKED_SOURCE workflows.
    """
    import os
    import subprocess

    if not repo_path or not os.path.exists(repo_path) or not os.path.isdir(repo_path):
        return SourceVerificationRecord(
            status=SourceVerificationStatus.VERIFICATION_FAILED,
            reason=f"Repository path '{repo_path}' does not exist or is not a valid directory",
        )

    canonical_path = os.path.realpath(os.path.abspath(repo_path))

    # Detect git repository metadata
    git_dir = os.path.join(canonical_path, ".git")
    is_git_repo = os.path.isdir(git_dir)
    workflow_type = "REPOSITORY_CHECKOUT" if is_git_repo else "LOCAL_UNTRACKED_SOURCE"

    actual_git_commit = None
    resolved_branch = default_branch

    if is_git_repo:
        try:
            head_file = os.path.join(git_dir, "HEAD")
            if os.path.isfile(head_file):
                with open(head_file, "r", encoding="utf-8", errors="ignore") as f:
                    head_content = f.read().strip()
                if head_content.startswith("ref:"):
                    ref_path = head_content.split(":", 1)[1].strip()
                    if not resolved_branch:
                        resolved_branch = ref_path.replace("refs/heads/", "")
                    ref_file = os.path.join(git_dir, ref_path.replace("/", os.sep))
                    if os.path.isfile(ref_file):
                        with open(ref_file, "r", encoding="utf-8", errors="ignore") as f:
                            actual_git_commit = f.read().strip()
                    elif os.path.isfile(os.path.join(git_dir, "packed-refs")):
                        with open(os.path.join(git_dir, "packed-refs"), "r", encoding="utf-8", errors="ignore") as f:
                            for line in f:
                                line = line.strip()
                                if line and not line.startswith("#") and not line.startswith("^"):
                                    parts = line.split()
                                    if len(parts) == 2 and parts[1] == ref_path:
                                        actual_git_commit = parts[0]
                                        break
                elif len(head_content) == 40:
                    actual_git_commit = head_content
        except Exception:
            pass

        if not actual_git_commit:
            try:
                proc = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=canonical_path,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    shell=False,
                    timeout=5,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    actual_git_commit = proc.stdout.strip()
            except Exception:
                pass

        # Validation: If caller supplied a commit_hash, ensure it matches actual local checkout
        if commit_hash and actual_git_commit:
            if not actual_git_commit.lower().startswith(commit_hash.lower()):
                return SourceVerificationRecord(
                    status=SourceVerificationStatus.VERIFICATION_FAILED,
                    repo_path=canonical_path,
                    commit_hash=actual_git_commit,
                    default_branch=resolved_branch,
                    is_git_repo=True,
                    workflow_type="REPOSITORY_CHECKOUT",
                    reason=(
                        f"Commit hash mismatch: caller specified '{commit_hash}', "
                        f"but actual checked-out Git HEAD is '{actual_git_commit}'. "
                        "Analysis on mismatched revisions is prohibited."
                    ),
                )

        resolved_commit = actual_git_commit or commit_hash
    else:
        # Untracked directory (no .git)
        resolved_commit = commit_hash

    ignored_dirs = {
        ".git",
        "node_modules",
        "cache",
        "artifacts",
        "dist",
        "build",
        ".husky",
        ".github",
    }
    sol_files = []
    build_framework = None

    for root, dirs, files in os.walk(canonical_path):
        dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]
        rel_dir = os.path.relpath(root, canonical_path)

        for file in files:
            file_lower = file.lower()
            rel_file_path = os.path.normpath(os.path.join(rel_dir, file)).replace("\\", "/")

            if file_lower.endswith(".sol"):
                sol_files.append(rel_file_path)

            if file_lower == "foundry.toml" and not build_framework:
                build_framework = "Foundry"
            elif (
                file_lower in ("hardhat.config.js", "hardhat.config.ts", "hardhat.config.cjs", "hardhat.config.mjs")
                and not build_framework
            ):
                build_framework = "Hardhat"
            elif file_lower in ("truffle-config.js", "truffle.js") and not build_framework:
                build_framework = "Truffle"
            elif file_lower == "brownie-config.yaml" and not build_framework:
                build_framework = "Brownie"
            elif file_lower == "ape-config.yaml" and not build_framework:
                build_framework = "Ape"

    if not sol_files:
        return SourceVerificationRecord(
            status=SourceVerificationStatus.SOURCE_NOT_FOUND,
            solidity_source_paths=[],
            build_framework=build_framework,
            commit_hash=resolved_commit,
            default_branch=resolved_branch,
            repo_path=canonical_path,
            is_git_repo=is_git_repo,
            workflow_type=workflow_type,
            reason="No Solidity (.sol) source files detected in repository tree",
        )

    if not build_framework:
        return SourceVerificationRecord(
            status=SourceVerificationStatus.BUILD_CONFIGURATION_NOT_FOUND,
            solidity_source_paths=sol_files,
            build_framework=None,
            commit_hash=resolved_commit,
            default_branch=resolved_branch,
            repo_path=canonical_path,
            is_git_repo=is_git_repo,
            workflow_type=workflow_type,
            reason=f"Found {len(sol_files)} Solidity file(s), but no supported build framework configuration file (foundry.toml, hardhat.config, etc.) was detected",
        )

    return SourceVerificationRecord(
        status=SourceVerificationStatus.SOURCE_VERIFIED,
        solidity_source_paths=sol_files,
        build_framework=build_framework,
        commit_hash=resolved_commit,
        default_branch=resolved_branch,
        repo_path=canonical_path,
        is_git_repo=is_git_repo,
        workflow_type=workflow_type,
        reason=f"Source verified: {len(sol_files)} Solidity file(s) found with {build_framework} build configuration",
    )


def evaluate_slither_eligibility(
    resolution_status: ResolutionStatus,
    verification: SourceVerificationRecord,
) -> Dict[str, Any]:
    """
    Evaluates whether a repository is eligible for downstream Slither static analysis.
    Slither is permitted ONLY if both Stage A (repository resolution) and Stage B (source verification) have passed.
    """
    if resolution_status in (
        ResolutionStatus.ORGANIZATION_ONLY,
        ResolutionStatus.AMBIGUOUS,
        ResolutionStatus.UNRESOLVED,
        ResolutionStatus.MALFORMED_RECORD,
    ):
        return {
            "eligible_for_slither": False,
            "status": SlitherEligibilityStatus.BLOCKED_NO_REPOSITORY.value,
            "reason": f"No distinct contract repository resolved (status: {resolution_status.value})",
        }

    if resolution_status == ResolutionStatus.RESOLVED_LOW_CONFIDENCE:
        return {
            "eligible_for_slither": False,
            "status": SlitherEligibilityStatus.BLOCKED_NEEDS_CORROBORATION.value,
            "reason": "Repository resolution confidence is LOW; requires manual corroboration before automated analysis",
        }

    if verification.status == SourceVerificationStatus.VERIFICATION_NOT_RUN:
        return {
            "eligible_for_slither": False,
            "status": SlitherEligibilityStatus.BLOCKED_SOURCE_UNVERIFIED.value,
            "reason": "Source verification has not been performed yet. Slither requires verified Solidity sources and build configuration.",
        }

    if verification.status != SourceVerificationStatus.SOURCE_VERIFIED:
        return {
            "eligible_for_slither": False,
            "status": SlitherEligibilityStatus.BLOCKED_SOURCE_UNVERIFIED.value,
            "reason": f"Source verification failed ({verification.status.value}): {verification.reason}",
        }

    return {
        "eligible_for_slither": True,
        "status": SlitherEligibilityStatus.ELIGIBLE.value,
        "reason": f"Eligible for Slither: Verified {len(verification.solidity_source_paths)} Solidity files with {verification.build_framework} framework",
    }


def get_scanner_handoff(
    status: ResolutionStatus,
    selected_repo: Optional[str] = None,
    verification: Optional[SourceVerificationRecord] = None,
) -> Dict[str, Any]:
    """
    3-stage pipeline handoff contract defining boundaries across:
    A. REPOSITORY_RESOLUTION: identifies candidate repository and confidence.
    B. SOURCE_VERIFICATION: inspects tree, detects Solidity and build framework without running code.
    C. SLITHER_ELIGIBILITY: permits Slither ONLY when both resolution and source verification have passed.
    """
    ver = verification or SourceVerificationRecord()
    is_res_confident = (status == ResolutionStatus.RESOLVED_CONFIDENT)
    is_res_low = (status == ResolutionStatus.RESOLVED_LOW_CONFIDENCE)

    # Stage A: Repository Resolution
    repo_resolution_stage = {
        "status": status.value,
        "selected_repository": selected_repo if (is_res_confident or is_res_low) else None,
        "eligible_for_source_verification": is_res_confident,
        "requires_manual_corroboration": is_res_low,
        "action": (
            "PROCEED_TO_SOURCE_VERIFICATION" if is_res_confident
            else "AWAIT_MANUAL_CORROBORATION" if is_res_low
            else "HALT_NO_REPOSITORY"
        ),
    }

    # Stage B: Source Verification
    source_verification_stage = {
        "status": ver.status.value,
        "solidity_source_paths": ver.solidity_source_paths,
        "build_framework": ver.build_framework,
        "commit_hash": ver.commit_hash,
        "default_branch": ver.default_branch,
        "reason": ver.reason,
        "branch_deployment_limitation": ver.branch_deployment_limitation,
    }

    # Stage C: Slither Eligibility
    slither_eval = evaluate_slither_eligibility(status, ver)

    effective_repo = selected_repo if (is_res_confident or is_res_low) else None

    return {
        "pipeline_stages": {
            "A_repository_resolution": repo_resolution_stage,
            "B_source_verification": source_verification_stage,
            "C_slither_eligibility": slither_eval,
        },
        "stage": "REPOSITORY_RESOLUTION" if ver.status == SourceVerificationStatus.VERIFICATION_NOT_RUN else "SOURCE_VERIFICATION",
        "status": (
            "SOURCE_VERIFIED" if (is_res_confident and ver.status == SourceVerificationStatus.SOURCE_VERIFIED)
            else "ELIGIBLE_FOR_SOURCE_VERIFICATION" if is_res_confident
            else "REQUIRES_CORROBORATION" if is_res_low
            else "NO_REPOSITORY_SELECTED"
        ),
        "eligible_for_source_verification": is_res_confident,
        "eligible_for_slither": slither_eval["eligible_for_slither"],
        # False during Stage A; True ONLY if Stage B source verification has succeeded and is confident
        "eligible_for_automated_analysis": slither_eval["eligible_for_slither"],
        "requires_manual_corroboration": is_res_low,
        "selected_repository": effective_repo,
        "next_stage_action": (
            "PROCEED_TO_SLITHER_ANALYSIS" if (is_res_confident and ver.status == SourceVerificationStatus.SOURCE_VERIFIED)
            else "PROCEED_TO_SOURCE_VERIFICATION" if is_res_confident
            else "AWAIT_MANUAL_CORROBORATION" if is_res_low
            else "SKIP_ANALYSIS"
        ),
        "downstream_slither_action": (
            "PROCEED_TO_SLITHER_ANALYSIS" if (is_res_confident and ver.status == SourceVerificationStatus.SOURCE_VERIFIED)
            else "PROCEED_TO_SOURCE_VERIFICATION" if is_res_confident
            else "AWAIT_MANUAL_CORROBORATION" if is_res_low
            else "SKIP_AUTOMATED_ANALYSIS"
        ),
        "prerequisites": [
            "Inspect repository file tree",
            "Identify Solidity source files (.sol)",
            "Detect build framework (Foundry / Hardhat / Truffle)",
            "Record exact Git commit hash (git rev-parse HEAD)",
        ],
    }


def resolve_github_repo_from_slug(slug: str, client: Optional[Any] = None) -> Dict[str, Any]:
    """
    Fetches full protocol details by slug from DefiLlama and returns
    the normalized protocol record.
    """
    c = client or DefiLlamaClient()
    details = c.fetch_protocol(slug)
    if not details:
        return normalize_protocol_record({"name": slug}, requested_slug=slug)
    return normalize_protocol_record(details, requested_slug=slug)


def normalize_protocol_record(
    raw_protocol: Dict[str, Any],
    base_record: Optional[Dict[str, Any]] = None,
    requested_slug: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Extends and normalizes a DefiLlama protocol dictionary with structured GitHub
    resolution metadata without breaking existing consumers.
    Preserves distinct fields for requested_slug, defillama_slug, defillama_id, and protocol_name.
    """
    if not isinstance(raw_protocol, dict):
        raw_protocol = {}

    status, selected_repo, evidence = resolve_github_repository(raw_protocol)
    candidates = extract_candidates(raw_protocol)

    # Format candidates list
    formatted_candidates = [
        {
            "original_value": c.original_value,
            "source_field": c.source_field,
            "candidate_type": c.candidate_type.value,
            "normalized_url": c.normalized_url,
            "owner": c.owner,
            "repo": c.repo,
            "is_auditor_repo": c.is_auditor_repo,
            "notes": c.notes,
        }
        for c in candidates
    ]

    # Format audit links
    raw_audits = raw_protocol.get("audit_links")
    audit_links_list: List[Dict[str, str]] = []
    if isinstance(raw_audits, list):
        for a in raw_audits:
            if isinstance(a, str) and a.strip():
                audit_links_list.append({"url": a.strip(), "source": "audit_links"})
    elif isinstance(raw_audits, str) and raw_audits.strip():
        audit_links_list.append({"url": raw_audits.strip(), "source": "audit_links"})

    # Extract contract addresses by chain
    contract_addresses_by_chain: Dict[str, List[str]] = {}
    primary_addr = raw_protocol.get("address")
    primary_chain = raw_protocol.get("chain", "Unknown")

    if primary_addr and isinstance(primary_addr, str):
        clean_addr = primary_addr.split(":")[-1].strip()
        chain_key = str(primary_chain).lower()
        contract_addresses_by_chain.setdefault(chain_key, []).append(clean_addr)

    # Extract TVL float safely whether from /protocols (number) or /protocol/{slug} (list of points)
    raw_tvl = raw_protocol.get("tvl")
    tvl_val = 0.0
    if isinstance(raw_tvl, (int, float)):
        tvl_val = float(raw_tvl)
    elif isinstance(raw_tvl, list) and raw_tvl:
        last_pt = raw_tvl[-1]
        if isinstance(last_pt, dict):
            tvl_val = float(last_pt.get("totalLiquidityUSD") or 0.0)

    # Resolve distinct identifier fields
    protocol_name = raw_protocol.get("name")
    if not isinstance(protocol_name, str) or not protocol_name.strip():
        protocol_name = base_record.get("protocol_name") if base_record else None

    raw_slug = raw_protocol.get("slug")
    defillama_slug = str(raw_slug).strip() if (isinstance(raw_slug, str) and raw_slug.strip()) else None

    raw_id = raw_protocol.get("id")
    defillama_id = str(raw_id).strip() if (raw_id is not None and str(raw_id).strip() != "") else None

    req_slug = requested_slug or (base_record.get("requested_slug") if base_record else None)

    # Base output preserving existing schema for backwards compatibility
    record: Dict[str, Any] = {}
    if base_record:
        record.update(base_record)
    else:
        legacy_name = protocol_name or "Unknown"
        # Never overwrite slug with internal numeric ID if requested_slug or defillama_slug is known!
        legacy_slug = defillama_slug or req_slug or (defillama_id if not (defillama_slug or req_slug) else "")
        record.update({
            "name": legacy_name,
            "slug": legacy_slug,
            "chain": str(raw_protocol.get("chain", "Unknown")).lower(),
            "chains": [str(c).lower() for c in raw_protocol.get("chains", [])],
            "tvl": tvl_val,
            "address": raw_protocol.get("address"),
            "contracts": [raw_protocol["address"]] if raw_protocol.get("address") else [],
            "category": raw_protocol.get("category", "General"),
        })

    # Distinct identity fields (Phase 1.1)
    record["requested_slug"] = req_slug
    record["defillama_slug"] = defillama_slug
    record["defillama_id"] = defillama_id
    record["protocol_name"] = protocol_name

    # Extend with normalized GitHub fields
    record["timestamp"] = int(time.time())
    record["contract_addresses_by_chain"] = contract_addresses_by_chain
    record["declared_repository_candidates"] = formatted_candidates
    record["selected_github_url"] = selected_repo
    record["github_resolution_status"] = status.value
    record["resolution_evidence"] = evidence
    record["audit_links"] = audit_links_list

    # Safe handoff contract for downstream security scanners
    handoff = get_scanner_handoff(status, selected_repo)
    record["scanner_handoff"] = handoff
    record["pipeline_stages"] = handoff.get("pipeline_stages")
    record["source_verification"] = handoff.get("pipeline_stages", {}).get("B_source_verification")
    record["slither_eligibility"] = handoff.get("pipeline_stages", {}).get("C_slither_eligibility")

    return record


class DefiLlamaClient:
    """
    HTTP client for DefiLlama API with timeout, exponential backoff retries,
    rate-limit handling (HTTP 429), and in-memory TTL caching.
    """

    PROTOCOLS_URL = "https://api.llama.fi/protocols"
    PROTOCOL_DETAIL_URL = "https://api.llama.fi/protocol/{slug}"

    def __init__(self, cache_ttl_seconds: int = 300, session: Optional[Any] = None):
        import requests
        self._requests = requests
        self.session = session or requests.Session()
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: Dict[str, Tuple[float, Any]] = {}

    def _get_cached(self, key: str) -> Optional[Any]:
        if key in self._cache:
            ts, val = self._cache[key]
            if time.time() - ts < self.cache_ttl_seconds:
                return val
            del self._cache[key]
        return None

    def _set_cached(self, key: str, val: Any) -> None:
        self._cache[key] = (time.time(), val)

    def _request_with_retry(
        self,
        url: str,
        timeout: int = 15,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
    ) -> Any:
        cached = self._get_cached(url)
        if cached is not None:
            return cached

        attempt = 0
        while attempt < max_retries:
            attempt += 1
            try:
                resp = self.session.get(url, timeout=timeout)
                if resp.status_code == 429:
                    # Rate limited: backoff exponentially
                    wait_time = backoff_factor * (2 ** (attempt - 1))
                    logger.warning(f"Rate limited (429) on {url}. Retrying in {wait_time:.1f}s (attempt {attempt}/{max_retries})")
                    time.sleep(wait_time)
                    continue

                if resp.status_code >= 500:
                    wait_time = backoff_factor * (2 ** (attempt - 1))
                    logger.warning(f"Server error ({resp.status_code}) on {url}. Retrying in {wait_time:.1f}s (attempt {attempt}/{max_retries})")
                    time.sleep(wait_time)
                    continue

                resp.raise_for_status()
                data = resp.json()
                self._set_cached(url, data)
                return data

            except self._requests.exceptions.Timeout as t_err:
                logger.warning(f"Timeout on {url} (attempt {attempt}/{max_retries}): {t_err}")
                if attempt >= max_retries:
                    raise
                time.sleep(backoff_factor * (2 ** (attempt - 1)))
            except self._requests.exceptions.RequestException as req_err:
                logger.error(f"HTTP error on {url} (attempt {attempt}/{max_retries}): {req_err}")
                if attempt >= max_retries:
                    raise
                time.sleep(backoff_factor * (2 ** (attempt - 1)))

        raise RuntimeError(f"Failed to fetch {url} after {max_retries} attempts")

    def fetch_all_protocols(
        self, timeout: int = 15, max_retries: int = 3, backoff_factor: float = 1.0
    ) -> List[Dict[str, Any]]:
        data = self._request_with_retry(
            self.PROTOCOLS_URL, timeout=timeout, max_retries=max_retries, backoff_factor=backoff_factor
        )
        if not isinstance(data, list):
            logger.warning("DefiLlama /protocols response was not a list")
            return []
        return data

    def fetch_protocol(
        self, slug: str, timeout: int = 10, max_retries: int = 2, backoff_factor: float = 1.0
    ) -> Optional[Dict[str, Any]]:
        if not slug:
            return None
        url = self.PROTOCOL_DETAIL_URL.format(slug=slug.strip())
        try:
            data = self._request_with_retry(
                url, timeout=timeout, max_retries=max_retries, backoff_factor=backoff_factor
            )
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Could not fetch protocol details for '{slug}': {e}")
        return None


def main():
    import argparse
    import json
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="GitHub Repository Resolver for DefiLlama Protocols"
    )
    parser.add_argument("--slug", help="Fetch and resolve repository for a single DefiLlama protocol slug (e.g. lido)")
    parser.add_argument("--url", help="Parse and normalize a single GitHub URL or handle")
    parser.add_argument("--limit", type=int, default=5, help="Number of protocols to resolve from live DefiLlama feed (default: 5)")
    parser.add_argument("--min-tvl", type=float, default=50000000.0, help="Minimum TVL filter for live batch test (default: $50M)")

    args = parser.parse_args()

    if args.url:
        info = parse_github_url(args.url)
        print(f"Raw Input:       {info.raw_url}")
        print(f"Candidate Type:  {info.candidate_type.value}")
        print(f"Owner:           {info.owner}")
        print(f"Repo:            {info.repo}")
        print(f"Normalized URL:  {info.normalized_url}")
        print(f"Is Auditor Repo: {info.is_auditor_repo}")
        print(f"Details:         {info.details}")
        return

    client = DefiLlamaClient()

    if args.slug:
        print(f"[*] Fetching metadata for protocol slug: '{args.slug}'...")
        details = client.fetch_protocol(args.slug)
        if not details:
            print(f"[!] Protocol '{args.slug}' not found.")
            return

        normalized = normalize_protocol_record(details, requested_slug=args.slug)
        print(json.dumps(normalized, indent=2))
        return

    print(f"[*] Fetching live protocols with TVL >= ${args.min_tvl:,.2f} USD...")
    protocols = client.fetch_all_protocols()
    filtered = [p for p in protocols if (p.get("tvl") or 0) >= args.min_tvl][:args.limit]

    print(f"[+] Resolving top {len(filtered)} protocols:\n")
    print(f"{'#':<3} | {'Protocol':<22} | {'Resolution Status':<20} | {'Selected GitHub Repository'}")
    print("-" * 80)
    for idx, proto in enumerate(filtered, 1):
        norm = normalize_protocol_record(proto)
        selected = norm["selected_github_url"] or f"({norm['github_resolution_status']})"
        print(f"{idx:<3} | {norm['name']:<22} | {norm['github_resolution_status']:<20} | {selected}")
    print("-" * 80)


if __name__ == "__main__":
    main()

