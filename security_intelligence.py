"""
security_intelligence.py
Provider-independent security intelligence and reputation scoring module.
Integrates external security telemetry (e.g. CertiK Skynet Partner API, DefiLlama audit records)
with conservative matching, secret redaction, and strict decoupling from compiler/Slither gates.
"""

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

try:
    from config import CERTIK_API_KEY, CERTIK_BASE_URL, CERTIK_TIMEOUT_SECONDS
except ImportError:
    CERTIK_API_KEY = os.getenv("CERTIK_API_KEY", "")
    CERTIK_BASE_URL = os.getenv("CERTIK_BASE_URL", "https://partner.certik-skynet.com")
    CERTIK_TIMEOUT_SECONDS = int(os.getenv("CERTIK_TIMEOUT_SECONDS", "10"))

logger = logging.getLogger("SecurityIntelligence")


class RetrievalStatus(str, Enum):
    """Machine-readable statuses for external security intelligence retrieval."""
    SUCCESS = "SUCCESS"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    UNAVAILABLE = "UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    INVALID_RESPONSE = "INVALID_RESPONSE"


class MatchConfidence(str, Enum):
    """Degrees of confidence when associating an external security record with a target protocol."""
    EXACT_PROVIDER_ID = "EXACT_PROVIDER_ID"
    CHAIN_CONTRACT_MATCH = "CHAIN_CONTRACT_MATCH"
    OFFICIAL_DOMAIN_MATCH = "OFFICIAL_DOMAIN_MATCH"
    NAME_SLUG_MATCH = "NAME_SLUG_MATCH"
    AMBIGUOUS = "AMBIGUOUS"
    NONE = "NONE"


STANDARD_SECURITY_LIMITATIONS: List[str] = [
    "Security ratings represent third-party, point-in-time assessments and do not guarantee immunity from smart contract bugs or economic exploits.",
    "Absence of an audit record in a specific provider does not imply the protocol has never undergone auditing by other independent firms.",
    "Third-party reputation scores are informational and MUST NOT bypass local static source verification or compiler safety gates.",
    "Data freshness is subject to provider update cycles and may not reflect recent protocol modifications, governance proposals, or migrations.",
]


def sanitize_secret_text(text: Optional[str]) -> Optional[str]:
    """Redacts API keys and private secrets from error strings, logs, and exception dumps."""
    if not text:
        return text
    # Redact common API key patterns (bearer tokens, x-certik-api-key, etc.)
    sanitized = re.sub(
        r'(?i)(api[_-]?key|secret|token|password|bearer|x-certik-api-key)[\s:=]+([a-zA-Z0-9_\-\.]{12,})',
        r'\1=[REDACTED_SECRET]',
        text,
    )
    # Redact hex private keys
    sanitized = re.sub(r'\b0x[a-fA-F0-9]{64}\b', '[REDACTED_HEX_KEY]', sanitized)
    sanitized = re.sub(r'(?<![a-zA-Z0-9])[a-fA-F0-9]{64}(?![a-zA-Z0-9])', '[REDACTED_HEX_KEY]', sanitized)
    return sanitized


@dataclass
class AuditRecord:
    """Represents a discrete smart contract audit performed by a security firm."""
    auditor: str
    audit_date: Optional[str] = None
    report_url: Optional[str] = None
    status: Optional[str] = None
    source: str = "certik"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SecurityScore:
    """Represents a normalized security or reputation score."""
    score: Optional[float] = None
    scale: str = "0-100 (Higher is safer)"
    tier: Optional[str] = None
    rank: Optional[int] = None
    rank_percentile: Optional[float] = None
    sub_scores: Dict[str, float] = field(default_factory=dict)
    score_meaning: str = "Aggregate multi-factor risk, code security, and operational health rating"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SecurityIncident:
    """Represents a documented historical security incident or exploit."""
    incident_id: str
    incident_type: str
    date: Optional[str] = None
    description: Optional[str] = None
    severity: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProtocolQuery:
    """Input query specification for retrieving security intelligence for a protocol."""
    name: str
    defillama_slug: Optional[str] = None
    contract_address: Optional[str] = None
    chain: Optional[str] = None
    website: Optional[str] = None
    provider_project_id: Optional[str] = None


@dataclass
class SecurityIntelligenceResult:
    """Normalized, provider-independent security intelligence report."""
    protocol_name: str
    defillama_slug: Optional[str]
    provider_name: str
    provider_project_id: Optional[str]
    project_url: Optional[str]
    retrieval_status: RetrievalStatus
    match_confidence: MatchConfidence
    security_score: Optional[SecurityScore] = None
    audit_records: List[AuditRecord] = field(default_factory=list)
    incidents: List[SecurityIncident] = field(default_factory=list)
    retrieved_at: int = field(default_factory=lambda: int(time.time()))
    source_url: Optional[str] = None
    error_category: Optional[str] = None
    diagnostic: Optional[str] = None
    data_freshness: Optional[str] = None
    limitations: List[str] = field(default_factory=lambda: list(STANDARD_SECURITY_LIMITATIONS))
    unverified_fields: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["retrieval_status"] = self.retrieval_status.value
        data["match_confidence"] = self.match_confidence.value
        if self.security_score is not None:
            data["security_score"] = self.security_score.to_dict()
        data["audit_records"] = [a.to_dict() if isinstance(a, AuditRecord) else a for a in self.audit_records]
        data["incidents"] = [i.to_dict() if isinstance(i, SecurityIncident) else i for i in self.incidents]
        return data

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


class BaseSecurityIntelligenceProvider:
    """Abstract interface for external security intelligence providers."""

    @property
    def provider_name(self) -> str:
        raise NotImplementedError

    def is_configured(self) -> bool:
        raise NotImplementedError

    def fetch_intelligence(self, query: ProtocolQuery) -> SecurityIntelligenceResult:
        raise NotImplementedError


def _extract_domain(url_str: Optional[str]) -> Optional[str]:
    """Helper to extract clean root domain from URL for conservative matching."""
    if not url_str or not isinstance(url_str, str):
        return None
    try:
        raw = url_str.strip()
        if not raw.startswith("http://") and not raw.startswith("https://"):
            raw = "https://" + raw
        parsed = urlparse(raw)
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc or None
    except Exception:
        return None


CERTIK_CHAIN_PREFIX_MAP: Dict[str, str] = {
    "ethereum": "eth",
    "eth": "eth",
    "bsc": "bsc",
    "binance": "bsc",
    "arbitrum": "aeth",
    "aeth": "aeth",
    "polygon": "polygon",
    "matic": "polygon",
    "optimism": "oeth",
    "oeth": "oeth",
    "base": "base",
    "avalanche": "avalanche",
    "avax": "avalanche",
}

REVERSE_CERTIK_CHAIN_MAP: Dict[str, str] = {
    "eth": "ethereum",
    "bsc": "bsc",
    "aeth": "arbitrum",
    "polygon": "polygon",
    "oeth": "optimism",
    "base": "base",
    "avalanche": "avalanche",
}


def normalize_chain_prefix(raw_chain: Optional[str]) -> Optional[str]:
    """Normalizes an EVM chain name or identifier to CertiK's canonical chain prefix."""
    if not raw_chain or not isinstance(raw_chain, str):
        return None
    c = raw_chain.strip().lower()
    return CERTIK_CHAIN_PREFIX_MAP.get(c, c)


class CertikSkynetProvider(BaseSecurityIntelligenceProvider):
    """
    Integration for CertiK's Skynet Security Score Partner API.
    Documented Base URL: https://partner.certik-skynet.com
    Endpoints:
      - List: GET /v1/security-scores?skip=0&limit=50
      - Direct lookup: GET /v1/security-scores/{id}
        where {id} is certiKProjectId (e.g. 'aave') or '{chain}:{contractAddress}' (e.g. 'eth:0x...').
    Authentication header: X-Certik-Api-Key.
    Requires commercial partner credentials passed via header: X-Certik-Api-Key.
    """

    DEFAULT_BASE_URL = "https://partner.certik-skynet.com"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_seconds: int = 10,
        session: Optional[requests.Session] = None,
    ):
        self.api_key = api_key or os.getenv("CERTIK_API_KEY", "").strip()
        self.base_url = (base_url or os.getenv("CERTIK_BASE_URL", self.DEFAULT_BASE_URL)).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    @property
    def provider_name(self) -> str:
        return "CertiK Skynet"

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _make_headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json",
            "X-Certik-Api-Key": self.api_key,
        }

    def _handle_http_error(
        self,
        status_code: int,
        endpoint: str,
        query: ProtocolQuery,
        now_ts: int,
    ) -> SecurityIntelligenceResult:
        if status_code in (401, 403):
            return SecurityIntelligenceResult(
                protocol_name=query.name,
                defillama_slug=query.defillama_slug,
                provider_name=self.provider_name,
                provider_project_id=query.provider_project_id,
                project_url=None,
                retrieval_status=RetrievalStatus.AUTHENTICATION_FAILED,
                match_confidence=MatchConfidence.NONE,
                retrieved_at=now_ts,
                source_url=endpoint,
                error_category="AUTH_FAILED",
                diagnostic="CertiK API authentication failed (HTTP 401/403). The provided API key is invalid or unauthorized.",
                unverified_fields=["security_score", "audit_records"],
            )

        if status_code == 429:
            return SecurityIntelligenceResult(
                protocol_name=query.name,
                defillama_slug=query.defillama_slug,
                provider_name=self.provider_name,
                provider_project_id=query.provider_project_id,
                project_url=None,
                retrieval_status=RetrievalStatus.RATE_LIMITED,
                match_confidence=MatchConfidence.NONE,
                retrieved_at=now_ts,
                source_url=endpoint,
                error_category="RATE_LIMITED",
                diagnostic="CertiK API rate limit exceeded (HTTP 429). Please back off and retry later.",
                unverified_fields=["security_score", "audit_records"],
            )

        if status_code >= 500:
            return SecurityIntelligenceResult(
                protocol_name=query.name,
                defillama_slug=query.defillama_slug,
                provider_name=self.provider_name,
                provider_project_id=query.provider_project_id,
                project_url=None,
                retrieval_status=RetrievalStatus.PROVIDER_ERROR,
                match_confidence=MatchConfidence.NONE,
                retrieved_at=now_ts,
                source_url=endpoint,
                error_category="SERVER_ERROR",
                diagnostic=f"CertiK Partner API returned upstream server error (HTTP {status_code}).",
                unverified_fields=["security_score", "audit_records"],
            )

        return SecurityIntelligenceResult(
            protocol_name=query.name,
            defillama_slug=query.defillama_slug,
            provider_name=self.provider_name,
            provider_project_id=query.provider_project_id,
            project_url=None,
            retrieval_status=RetrievalStatus.PROVIDER_ERROR,
            match_confidence=MatchConfidence.NONE,
            retrieved_at=now_ts,
            source_url=endpoint,
            error_category="UNEXPECTED_STATUS",
            diagnostic=f"CertiK API returned unexpected HTTP status {status_code}.",
            unverified_fields=["security_score", "audit_records"],
        )

    def _fetch_direct_project(
        self,
        project_id_or_chain_addr: str,
        query: ProtocolQuery,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[SecurityIntelligenceResult]]:
        """
        Direct lookup endpoint: GET /v1/security-scores/{id}
        Returns (item_dict, None) on 200, (None, None) on 404, or (None, err_result) on error.
        """
        now_ts = int(time.time())
        endpoint = f"{self.base_url}/v1/security-scores/{project_id_or_chain_addr}"
        headers = self._make_headers()

        try:
            resp = self.session.get(endpoint, headers=headers, timeout=self.timeout_seconds)
            code = resp.status_code

            if code == 404:
                return None, None

            if code != 200:
                return None, self._handle_http_error(code, endpoint, query, now_ts)

            try:
                payload = resp.json()
            except Exception as e:
                return None, SecurityIntelligenceResult(
                    protocol_name=query.name,
                    defillama_slug=query.defillama_slug,
                    provider_name=self.provider_name,
                    provider_project_id=query.provider_project_id,
                    project_url=None,
                    retrieval_status=RetrievalStatus.INVALID_RESPONSE,
                    match_confidence=MatchConfidence.NONE,
                    retrieved_at=now_ts,
                    source_url=endpoint,
                    error_category="MALFORMED_JSON",
                    diagnostic=f"Failed to parse JSON response from CertiK: {sanitize_secret_text(str(e))}",
                    unverified_fields=["security_score", "audit_records"],
                )

            if not isinstance(payload, dict):
                return None, SecurityIntelligenceResult(
                    protocol_name=query.name,
                    defillama_slug=query.defillama_slug,
                    provider_name=self.provider_name,
                    provider_project_id=query.provider_project_id,
                    project_url=None,
                    retrieval_status=RetrievalStatus.INVALID_RESPONSE,
                    match_confidence=MatchConfidence.NONE,
                    retrieved_at=now_ts,
                    source_url=endpoint,
                    error_category="UNEXPECTED_PAYLOAD_STRUCTURE",
                    diagnostic="Root JSON returned by CertiK direct lookup is not an object.",
                    unverified_fields=["security_score", "audit_records"],
                )

            # Handle payload wrapped in {"items": [...]} or raw item object
            if "items" in payload and isinstance(payload["items"], list):
                if payload["items"]:
                    return payload["items"][0], None
                return None, None

            return payload, None

        except requests.exceptions.Timeout:
            return None, SecurityIntelligenceResult(
                protocol_name=query.name,
                defillama_slug=query.defillama_slug,
                provider_name=self.provider_name,
                provider_project_id=query.provider_project_id,
                project_url=None,
                retrieval_status=RetrievalStatus.UNAVAILABLE,
                match_confidence=MatchConfidence.NONE,
                retrieved_at=now_ts,
                source_url=endpoint,
                error_category="TIMEOUT",
                diagnostic=f"Request to CertiK Partner API timed out after {self.timeout_seconds}s.",
                unverified_fields=["security_score", "audit_records"],
            )
        except requests.exceptions.RequestException as e:
            return None, SecurityIntelligenceResult(
                protocol_name=query.name,
                defillama_slug=query.defillama_slug,
                provider_name=self.provider_name,
                provider_project_id=query.provider_project_id,
                project_url=None,
                retrieval_status=RetrievalStatus.UNAVAILABLE,
                match_confidence=MatchConfidence.NONE,
                retrieved_at=now_ts,
                source_url=endpoint,
                error_category="NETWORK_ERROR",
                diagnostic=f"Network error communicating with CertiK Partner API: {sanitize_secret_text(str(e))}",
                unverified_fields=["security_score", "audit_records"],
            )

    def _fetch_paged_list(
        self,
        skip: int,
        limit: int,
        query: ProtocolQuery,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[SecurityIntelligenceResult]]:
        """
        Fetches a paginated page from GET /v1/security-scores.
        Returns (payload_dict, None) or (None, err_result).
        """
        now_ts = int(time.time())
        endpoint = f"{self.base_url}/v1/security-scores"
        headers = self._make_headers()
        params = {"skip": skip, "limit": limit}

        try:
            resp = self.session.get(endpoint, headers=headers, params=params, timeout=self.timeout_seconds)
            code = resp.status_code

            if code != 200:
                return None, self._handle_http_error(code, endpoint, query, now_ts)

            try:
                payload = resp.json()
            except Exception as e:
                return None, SecurityIntelligenceResult(
                    protocol_name=query.name,
                    defillama_slug=query.defillama_slug,
                    provider_name=self.provider_name,
                    provider_project_id=query.provider_project_id,
                    project_url=None,
                    retrieval_status=RetrievalStatus.INVALID_RESPONSE,
                    match_confidence=MatchConfidence.NONE,
                    retrieved_at=now_ts,
                    source_url=endpoint,
                    error_category="MALFORMED_JSON",
                    diagnostic=f"Failed to parse JSON response from CertiK: {sanitize_secret_text(str(e))}",
                    unverified_fields=["security_score", "audit_records"],
                )

            if not isinstance(payload, dict):
                return None, SecurityIntelligenceResult(
                    protocol_name=query.name,
                    defillama_slug=query.defillama_slug,
                    provider_name=self.provider_name,
                    provider_project_id=query.provider_project_id,
                    project_url=None,
                    retrieval_status=RetrievalStatus.INVALID_RESPONSE,
                    match_confidence=MatchConfidence.NONE,
                    retrieved_at=now_ts,
                    source_url=endpoint,
                    error_category="UNEXPECTED_PAYLOAD_STRUCTURE",
                    diagnostic="Root JSON returned by CertiK API is not an object.",
                    unverified_fields=["security_score", "audit_records"],
                )

            return payload, None

        except requests.exceptions.Timeout:
            return None, SecurityIntelligenceResult(
                protocol_name=query.name,
                defillama_slug=query.defillama_slug,
                provider_name=self.provider_name,
                provider_project_id=query.provider_project_id,
                project_url=None,
                retrieval_status=RetrievalStatus.UNAVAILABLE,
                match_confidence=MatchConfidence.NONE,
                retrieved_at=now_ts,
                source_url=endpoint,
                error_category="TIMEOUT",
                diagnostic=f"Request to CertiK Partner API timed out after {self.timeout_seconds}s.",
                unverified_fields=["security_score", "audit_records"],
            )
        except requests.exceptions.RequestException as e:
            return None, SecurityIntelligenceResult(
                protocol_name=query.name,
                defillama_slug=query.defillama_slug,
                provider_name=self.provider_name,
                provider_project_id=query.provider_project_id,
                project_url=None,
                retrieval_status=RetrievalStatus.UNAVAILABLE,
                match_confidence=MatchConfidence.NONE,
                retrieved_at=now_ts,
                source_url=endpoint,
                error_category="NETWORK_ERROR",
                diagnostic=f"Network error communicating with CertiK Partner API: {sanitize_secret_text(str(e))}",
                unverified_fields=["security_score", "audit_records"],
            )

    def fetch_intelligence(
        self,
        query: ProtocolQuery,
        max_pages: int = 2,
        page_limit: int = 50,
    ) -> SecurityIntelligenceResult:
        """
        Fetches and conservatively matches protocol security telemetry from CertiK Skynet.
        Prefers direct project lookup (/v1/security-scores/{id}) when trusted identifiers exist.
        Falls back to bounded, schema-compliant list pagination (/v1/security-scores).
        Returns a structured SecurityIntelligenceResult preserving unverified/missing states.
        """
        now_ts = int(time.time())

        # Check configuration gate
        if not self.is_configured():
            return SecurityIntelligenceResult(
                protocol_name=query.name,
                defillama_slug=query.defillama_slug,
                provider_name=self.provider_name,
                provider_project_id=None,
                project_url=None,
                retrieval_status=RetrievalStatus.NOT_CONFIGURED,
                match_confidence=MatchConfidence.NONE,
                retrieved_at=now_ts,
                source_url=f"{self.base_url}/v1/security-scores",
                error_category="MISSING_CREDENTIALS",
                diagnostic=(
                    "CertiK Partner API credentials ('CERTIK_API_KEY') are not configured in environment or .env. "
                    "Access to the machine-readable Skynet Security Score API requires an active commercial agreement "
                    "with CertiK (contact: APIsupport@certik.com). Public scraping is rejected to adhere to access controls."
                ),
                unverified_fields=["security_score", "audit_records", "incidents"],
            )

        # ---------------------------------------------------------------------
        # 1. Direct Project Lookup Optimization
        # ---------------------------------------------------------------------
        # Prefer direct endpoint /v1/security-scores/{id} if trusted identifier or chain contract exists
        direct_lookup_id: Optional[str] = None
        direct_confidence: Optional[MatchConfidence] = None

        if query.provider_project_id:
            direct_lookup_id = query.provider_project_id.strip()
            direct_confidence = MatchConfidence.EXACT_PROVIDER_ID
        elif query.contract_address:
            clean_addr = query.contract_address.strip().lower()
            norm_chain = normalize_chain_prefix(query.chain)
            if norm_chain and clean_addr.startswith("0x"):
                direct_lookup_id = f"{norm_chain}:{clean_addr}"
                direct_confidence = MatchConfidence.CHAIN_CONTRACT_MATCH

        if direct_lookup_id and direct_confidence:
            item, err_res = self._fetch_direct_project(direct_lookup_id, query)
            if err_res is not None:
                return err_res
            if item is not None:
                # Conservative verification: if direct lookup was by chain-qualified address,
                # verify that the returned item does not contradict the queried chain prefix!
                if direct_confidence == MatchConfidence.CHAIN_CONTRACT_MATCH:
                    primary_token = str(item.get("primaryTokenContractAddress", "")).strip().lower()
                    if ":" in primary_token:
                        item_chain_raw, item_hex = primary_token.split(":", 1)
                        item_chain_norm = normalize_chain_prefix(item_chain_raw)
                        q_chain_norm = normalize_chain_prefix(query.chain)
                        if item_chain_norm and q_chain_norm and item_chain_norm != q_chain_norm:
                            item = None  # Reject cross-chain collision!
                    elif query.contract_address:
                        if primary_token != query.contract_address.strip().lower():
                            item = None

                if item is not None:
                    direct_endpoint = f"{self.base_url}/v1/security-scores/{direct_lookup_id}"
                    return self._parse_matched_item(query, item, direct_confidence, direct_endpoint, now_ts)

            # Direct lookup returned 404 (None, None)
            if query.provider_project_id and (not query.name or query.name == query.provider_project_id):
                return SecurityIntelligenceResult(
                    protocol_name=query.name,
                    defillama_slug=query.defillama_slug,
                    provider_name=self.provider_name,
                    provider_project_id=query.provider_project_id,
                    project_url=None,
                    retrieval_status=RetrievalStatus.NOT_FOUND,
                    match_confidence=MatchConfidence.NONE,
                    retrieved_at=now_ts,
                    source_url=f"{self.base_url}/v1/security-scores/{direct_lookup_id}",
                    diagnostic=f"CertiK project identifier '{direct_lookup_id}' was not found on Skynet (HTTP 404).",
                    unverified_fields=["security_score", "audit_records"],
                )

        # ---------------------------------------------------------------------
        # 2. Bounded Catalog Pagination Search
        # ---------------------------------------------------------------------
        # Limit per page is bounded to 50 max according to API documentation
        effective_limit = min(max(page_limit, 1), 50)
        pages_fetched = 0
        total_items_inspected = 0
        has_more_available = False
        catalog_total: Optional[int] = None
        endpoint = f"{self.base_url}/v1/security-scores"

        for page_idx in range(max_pages):
            skip = page_idx * effective_limit
            payload, err_res = self._fetch_paged_list(skip=skip, limit=effective_limit, query=query)
            if err_res is not None:
                return err_res

            pages_fetched += 1
            items = payload.get("items")
            if not isinstance(items, list):
                if "certiKProjectId" in payload:
                    items = [payload]
                else:
                    items = []

            page_info = payload.get("page")
            if isinstance(page_info, dict):
                has_more_available = bool(page_info.get("hasMore", False))
                catalog_total = page_info.get("total")
            else:
                has_more_available = False

            total_items_inspected += len(items)

            # Perform conservative protocol matching in this page
            matched_item, confidence, match_reason = self._match_item_conservatively(query, items)

            if confidence == MatchConfidence.AMBIGUOUS:
                return SecurityIntelligenceResult(
                    protocol_name=query.name,
                    defillama_slug=query.defillama_slug,
                    provider_name=self.provider_name,
                    provider_project_id=None,
                    project_url=None,
                    retrieval_status=RetrievalStatus.AMBIGUOUS,
                    match_confidence=MatchConfidence.AMBIGUOUS,
                    retrieved_at=now_ts,
                    source_url=endpoint,
                    error_category="AMBIGUOUS_MATCH",
                    diagnostic=f"Matching rejected: {match_reason}",
                    unverified_fields=["security_score", "audit_records"],
                )

            if matched_item and confidence != MatchConfidence.NONE:
                return self._parse_matched_item(query, matched_item, confidence, endpoint, now_ts)

            if not has_more_available or len(items) == 0:
                break

        # ---------------------------------------------------------------------
        # 3. Not Found or Bounded Incomplete Diagnostic
        # ---------------------------------------------------------------------
        if has_more_available:
            diag = (
                f"Protocol '{query.name}' was not found within the bounded search window "
                f"of {total_items_inspected} items across {pages_fetched} page(s). "
                f"Pagination halted to respect request bounds ({catalog_total if catalog_total is not None else 'unknown'} total items in catalog). "
                f"Direct project lookup via provider project ID or chain contract address is recommended."
            )
        else:
            diag = (
                f"No matching project found on CertiK Skynet for protocol '{query.name}' "
                f"(slug: '{query.defillama_slug}') after inspecting {total_items_inspected} catalog items."
            )

        return SecurityIntelligenceResult(
            protocol_name=query.name,
            defillama_slug=query.defillama_slug,
            provider_name=self.provider_name,
            provider_project_id=None,
            project_url=None,
            retrieval_status=RetrievalStatus.NOT_FOUND,
            match_confidence=MatchConfidence.NONE,
            retrieved_at=now_ts,
            source_url=endpoint,
            diagnostic=diag,
            unverified_fields=["security_score", "audit_records"],
        )

    def _match_item_conservatively(
        self,
        query: ProtocolQuery,
        items: List[Dict[str, Any]],
    ) -> Tuple[Optional[Dict[str, Any]], MatchConfidence, str]:
        """
        Conservative matching logic enforcing strict hierarchy:
        1. Exact provider project ID
        2. Exact chain-qualified contract address (strictly rejecting cross-chain collisions)
        3. Official website domain (projectWebsite / website)
        4. Name / CMC slug (unambiguous only; DefiLlama slug NEVER treated as provider ID)
        """
        if not items:
            return None, MatchConfidence.NONE, "Provider returned empty items array"

        # Tier 1: Explicit provider ID
        if query.provider_project_id:
            target_pid = query.provider_project_id.strip().lower()
            for item in items:
                if str(item.get("certiKProjectId", "")).strip().lower() == target_pid:
                    return item, MatchConfidence.EXACT_PROVIDER_ID, f"Matched exact CertiK project ID '{query.provider_project_id}'"

        # Tier 2: Chain and contract address matching with cross-chain collision prevention
        if query.contract_address:
            clean_addr = query.contract_address.strip().lower()
            if clean_addr.startswith("0x"):
                q_chain_norm = normalize_chain_prefix(query.chain)
                contract_matches = []

                for item in items:
                    primary_token = str(item.get("primaryTokenContractAddress", "")).strip().lower()
                    if not primary_token:
                        continue

                    # Parse item chain and hex address
                    if ":" in primary_token:
                        item_chain_raw, item_hex = primary_token.split(":", 1)
                        item_chain_norm = normalize_chain_prefix(item_chain_raw)
                    else:
                        item_chain_norm = None
                        item_hex = primary_token

                    # Address must match strictly (full string equality, not substring)
                    if item_hex != clean_addr:
                        continue

                    # If query specified chain, item chain MUST match normalized chain prefix
                    if q_chain_norm:
                        if item_chain_norm and item_chain_norm != q_chain_norm:
                            # Cross-chain address collision prevented (e.g. same address on BSC vs ETH)
                            continue

                    contract_matches.append(item)

                if len(contract_matches) == 1:
                    return contract_matches[0], MatchConfidence.CHAIN_CONTRACT_MATCH, f"Matched on-chain primary contract address '{query.contract_address}'"
                elif len(contract_matches) > 1:
                    # Check if all refer to the same project ID
                    first_pid = contract_matches[0].get("certiKProjectId")
                    if all(m.get("certiKProjectId") == first_pid for m in contract_matches):
                        return contract_matches[0], MatchConfidence.CHAIN_CONTRACT_MATCH, f"Matched on-chain primary contract address '{query.contract_address}'"
                    return None, MatchConfidence.AMBIGUOUS, f"Contract address '{query.contract_address}' associated with multiple distinct CertiK projects"

        # Tier 3: Official project website domain matching
        q_domain = _extract_domain(query.website)
        if q_domain:
            domain_matches = []
            for item in items:
                # Check documented projectWebsite field first, then website / officialUrl
                item_site = _extract_domain(item.get("projectWebsite") or item.get("website") or item.get("officialUrl"))
                if item_site and item_site == q_domain:
                    domain_matches.append(item)
            if len(domain_matches) == 1:
                return domain_matches[0], MatchConfidence.OFFICIAL_DOMAIN_MATCH, f"Matched official domain '{q_domain}'"
            elif len(domain_matches) > 1:
                # Disambiguate if duplicate entries for the same project
                first_pid = domain_matches[0].get("certiKProjectId")
                if all(m.get("certiKProjectId") == first_pid for m in domain_matches):
                    return domain_matches[0], MatchConfidence.OFFICIAL_DOMAIN_MATCH, f"Matched official domain '{q_domain}'"
                return None, MatchConfidence.AMBIGUOUS, f"Multiple projects share domain '{q_domain}'"

        # Tier 4: Slug & Name matching (conservative: must be unique, DefiLlama slug != provider ID)
        candidate_matches: List[Tuple[Dict[str, Any], str]] = []
        target_slug = (query.defillama_slug or "").strip().lower()
        target_name = (query.name or "").strip().lower()

        for item in items:
            c_name = str(item.get("projectName", "") or item.get("name", "")).strip().lower()
            c_cmc_slug = str(item.get("cmcSlug", "")).strip().lower()
            c_id = str(item.get("certiKProjectId", "")).strip().lower()

            # Name match (highest tier 4 priority)
            if target_name and c_name and target_name == c_name:
                candidate_matches.append((item, f"Exact project name match '{target_name}'"))
            # CMC slug match (secondary)
            elif target_slug and c_cmc_slug and target_slug == c_cmc_slug:
                candidate_matches.append((item, f"DefiLlama slug matched CMC slug '{target_slug}'"))
            # Fallback exact name-to-id if name matches id
            elif target_name and c_id and target_name == c_id:
                candidate_matches.append((item, f"Project name matched project ID '{target_name}'"))

        if len(candidate_matches) == 1:
            item, reason = candidate_matches[0]
            return item, MatchConfidence.NAME_SLUG_MATCH, reason
        elif len(candidate_matches) > 1:
            # Check if all matches refer to the exact same project ID
            first_id = candidate_matches[0][0].get("certiKProjectId")
            if all(m[0].get("certiKProjectId") == first_id for m in candidate_matches):
                return candidate_matches[0][0], MatchConfidence.NAME_SLUG_MATCH, "Multiple duplicate entries for same project ID"
            return None, MatchConfidence.AMBIGUOUS, f"Multiple distinct projects matched name/slug '{target_name}' or '{target_slug}'"

        return None, MatchConfidence.NONE, "No record matched by ID, contract, domain, or unambiguous name"

    def _parse_matched_item(
        self,
        query: ProtocolQuery,
        item: Dict[str, Any],
        confidence: MatchConfidence,
        source_url: str,
        now_ts: int,
    ) -> SecurityIntelligenceResult:
        """Parses a verified matched project item into the normalized result schema."""
        project_id = str(item.get("certiKProjectId") or "")
        project_page = item.get("certiKProjectPage")

        # Parse security score
        sec_score_obj = item.get("securityScore")
        security_score: Optional[SecurityScore] = None
        unverified_fields: List[str] = []

        if isinstance(sec_score_obj, dict):
            raw_score = sec_score_obj.get("score")
            # CRITICAL: Preserve missing score as None, never convert to zero!
            parsed_score: Optional[float] = None
            if raw_score is not None:
                try:
                    parsed_score = float(raw_score)
                except (ValueError, TypeError):
                    parsed_score = None

            tier = sec_score_obj.get("tier")
            rank = sec_score_obj.get("rank")
            rank_p = sec_score_obj.get("rankPercentile")

            sub_scores: Dict[str, float] = {}
            for sub_key in [
                "codeSecurity",
                "fundamentalHealth",
                "operationalResilience",
                "governanceStrength",
                "marketStability",
                "communityTrust",
            ]:
                sub_val = sec_score_obj.get(sub_key)
                if sub_val is not None:
                    try:
                        sub_scores[sub_key] = float(sub_val)
                    except (ValueError, TypeError):
                        pass

            security_score = SecurityScore(
                score=parsed_score,
                scale="0-100 (Higher is safer)",
                tier=str(tier) if tier else None,
                rank=int(rank) if rank is not None else None,
                rank_percentile=float(rank_p) if rank_p is not None else None,
                sub_scores=sub_scores,
                score_meaning="Aggregate Skynet multi-factor security score (0-100)",
            )
        else:
            unverified_fields.append("security_score")

        # Parse audit records
        audit_records: List[AuditRecord] = []
        is_audited_by_certik = item.get("auditedByCertiK")
        last_audit_date = item.get("lastAuditDate")

        if is_audited_by_certik:
            audit_records.append(
                AuditRecord(
                    auditor="CertiK",
                    audit_date=str(last_audit_date) if last_audit_date else None,
                    report_url=project_page,
                    status="Audited by CertiK",
                    source="certik",
                )
            )
        elif is_audited_by_certik is False:
            # Explicitly verified negative for CertiK audit
            pass
        else:
            unverified_fields.append("audit_records")

        # Data freshness
        freshness_str = f"Last audit: {last_audit_date}" if last_audit_date else "Real-time Skynet telemetry"

        return SecurityIntelligenceResult(
            protocol_name=query.name,
            defillama_slug=query.defillama_slug,
            provider_name=self.provider_name,
            provider_project_id=project_id,
            project_url=project_page,
            retrieval_status=RetrievalStatus.SUCCESS,
            match_confidence=confidence,
            security_score=security_score,
            audit_records=audit_records,
            incidents=[],
            retrieved_at=now_ts,
            source_url=source_url,
            data_freshness=freshness_str,
            unverified_fields=unverified_fields,
            diagnostic=f"Successfully retrieved and conservatively matched CertiK Skynet telemetry with {confidence.value} confidence.",
        )


def check_live_api_connectivity(
    provider: Optional[CertikSkynetProvider] = None,
) -> Dict[str, Any]:
    """
    Safely probes the CertiK Partner API endpoint for live authentication and reachability.
    Enforces strict security rules:
    - Never logs, prints, or exposes the API key or secrets.
    - Sends a single bounded request (limit=1).
    - Does not retry on 401/403 or 429.
    Returns structured status dictionary.
    """
    prov = provider or CertikSkynetProvider()
    if not prov.is_configured():
        return {
            "status": "NOT_CONFIGURED",
            "provider": prov.provider_name,
            "base_url": prov.base_url,
            "diagnostic": "CERTIK_API_KEY is not configured in the environment or .env file.",
            "authenticated": False,
        }

    endpoint = f"{prov.base_url}/v1/security-scores"
    headers = {
        "Accept": "application/json",
        "X-Certik-Api-Key": prov.api_key,
    }
    params = {"limit": 1, "skip": 0}

    try:
        resp = prov.session.get(endpoint, headers=headers, params=params, timeout=prov.timeout_seconds)
        code = resp.status_code

        if code in (401, 403):
            return {
                "status": "AUTHENTICATION_FAILED",
                "http_status": code,
                "provider": prov.provider_name,
                "base_url": prov.base_url,
                "diagnostic": f"Live authentication failed with HTTP {code}. The configured CERTIK_API_KEY was rejected by CertiK Partner API.",
                "authenticated": False,
            }
        elif code == 429:
            return {
                "status": "RATE_LIMITED",
                "http_status": code,
                "provider": prov.provider_name,
                "base_url": prov.base_url,
                "diagnostic": "Live probe returned HTTP 429 Rate Limited. Partner request quotas exceeded.",
                "authenticated": False,
            }
        elif code >= 500:
            return {
                "status": "PROVIDER_ERROR",
                "http_status": code,
                "provider": prov.provider_name,
                "base_url": prov.base_url,
                "diagnostic": f"Upstream CertiK Partner API returned server error HTTP {code}.",
                "authenticated": False,
            }
        elif code == 200:
            return {
                "status": "SUCCESS",
                "http_status": 200,
                "provider": prov.provider_name,
                "base_url": prov.base_url,
                "diagnostic": "Successfully authenticated against live CertiK Partner API. Endpoint is active and reachable.",
                "authenticated": True,
            }
        else:
            return {
                "status": "PROVIDER_ERROR",
                "http_status": code,
                "provider": prov.provider_name,
                "base_url": prov.base_url,
                "diagnostic": f"Live probe returned unexpected HTTP status {code}.",
                "authenticated": False,
            }
    except requests.exceptions.Timeout:
        return {
            "status": "UNAVAILABLE",
            "error_category": "TIMEOUT",
            "provider": prov.provider_name,
            "base_url": prov.base_url,
            "diagnostic": f"Live probe timed out after {prov.timeout_seconds}s.",
            "authenticated": False,
        }
    except requests.exceptions.RequestException as e:
        return {
            "status": "UNAVAILABLE",
            "error_category": "NETWORK_ERROR",
            "provider": prov.provider_name,
            "base_url": prov.base_url,
            "diagnostic": f"Live probe failed with network error: {sanitize_secret_text(str(e))}",
            "authenticated": False,
        }


def query_protocol_security_intelligence(
    protocol_slug: Optional[str] = None,
    protocol_name: Optional[str] = None,
    contract_address: Optional[str] = None,
    chain: Optional[str] = None,
    website: Optional[str] = None,
    provider_project_id: Optional[str] = None,
    provider: Optional[BaseSecurityIntelligenceProvider] = None,
) -> SecurityIntelligenceResult:
    """
    Main entry point for retrieving security intelligence for a protocol.
    Uses configured providers (CertikSkynetProvider by default).
    """
    p_name = protocol_name or protocol_slug or "Unknown Protocol"
    query = ProtocolQuery(
        name=p_name,
        defillama_slug=protocol_slug,
        contract_address=contract_address,
        chain=chain,
        website=website,
        provider_project_id=provider_project_id,
    )

    prov = provider or CertikSkynetProvider()
    return prov.fetch_intelligence(query)

