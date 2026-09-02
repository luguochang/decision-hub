from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from urllib.parse import urlsplit

from packages.contracts_py.decision_hub_contracts.models import (
    SearchEvidence,
    SearchQuery,
    SearchResult,
)
from packages.kernel.decision_hub_kernel.application.workbench import WorkbenchAssetService
from packages.kernel.decision_hub_kernel.ports.search import (
    SearchCapabilityError,
    SearchTransport,
)

SEARCH_INPUT_SCHEMA_REF = (
    "decision-hub://contracts/evolution-job.v1#/$defs/search_query"
)
SEARCH_OUTPUT_SCHEMA_REF = (
    "decision-hub://contracts/evolution-job.v1#/$defs/search_result"
)
REQUIRED_SEARCH_PERMISSIONS = frozenset({"read_only", "network:https"})
BROAD_SEARCH_PERMISSION = "search:broad"


def search_evidence_content_hash(
    *,
    title: str,
    snippet: str,
    source_url: str,
    published_at: datetime | None,
) -> str:
    payload = {
        "published_at": published_at.isoformat() if published_at is not None else None,
        "snippet": snippet,
        "source_url": source_url,
        "title": title,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class SearchCapabilityService:
    """Fail-closed execution gate around a replaceable search transport."""

    def __init__(self, workbench: WorkbenchAssetService, transport: SearchTransport) -> None:
        self.workbench = workbench
        self.transport = transport

    async def search(self, query: SearchQuery) -> SearchResult:
        try:
            manifest = self.workbench.require_enabled(query.capability_id)
        except PermissionError as exc:
            raise SearchCapabilityError(
                "search_capability_not_enabled",
                "search capability is not enabled or shadow-enabled",
            ) from exc
        if manifest.capability_type != "tool":
            raise SearchCapabilityError(
                "search_capability_type_invalid", "search capability must be a tool"
            )
        missing = REQUIRED_SEARCH_PERMISSIONS - set(manifest.permissions)
        if missing:
            raise SearchCapabilityError(
                "search_permission_denied", "search capability lacks required permissions"
            )
        if manifest.input_schema_ref != SEARCH_INPUT_SCHEMA_REF:
            raise SearchCapabilityError(
                "search_input_schema_invalid", "search input schema is not canonical"
            )
        if manifest.output_schema_ref != SEARCH_OUTPUT_SCHEMA_REF:
            raise SearchCapabilityError(
                "search_output_schema_invalid", "search output schema is not canonical"
            )
        allowed_domains = _authorized_domains(query, manifest.network_domains, manifest.permissions)
        _validate_requested_budget(query.max_cost_usd, manifest.max_cost_usd)
        try:
            async with asyncio.timeout(manifest.timeout_seconds):
                result = await self.transport.search(query)
        except TimeoutError as exc:
            raise SearchCapabilityError(
                "search_timeout", "search capability exceeded its audited timeout", retryable=True
            ) from exc
        self._validate_result(
            query,
            result,
            expected_provider=manifest.provider,
            allowed_domains=allowed_domains,
            manifest_max_cost_usd=manifest.max_cost_usd,
        )
        return result

    @staticmethod
    def _validate_result(
        query: SearchQuery,
        result: SearchResult,
        *,
        expected_provider: str,
        allowed_domains: frozenset[str],
        manifest_max_cost_usd: float | None,
    ) -> None:
        if result.request_id != query.request_id or result.capability_id != query.capability_id:
            raise SearchCapabilityError(
                "search_lineage_mismatch", "search result does not match the request lineage"
            )
        if result.provider != expected_provider:
            raise SearchCapabilityError(
                "search_provider_mismatch", "search result provider differs from the manifest"
            )
        if len(result.evidence) > query.max_results:
            raise SearchCapabilityError(
                "search_result_limit_exceeded", "search result exceeds the requested limit"
            )
        evidence_ids = [item.evidence_id for item in result.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise SearchCapabilityError(
                "search_evidence_duplicate", "search result contains duplicate evidence ids"
            )
        _validate_actual_cost(result.cost_usd, query.max_cost_usd, manifest_max_cost_usd)
        if result.completed_at < query.observed_at:
            raise SearchCapabilityError(
                "search_time_invalid", "search completed before the request was observed"
            )
        for evidence in result.evidence:
            _validate_evidence(query, result, evidence, allowed_domains)


def _authorized_domains(
    query: SearchQuery, manifest_domains: list[str], permissions: list[str]
) -> frozenset[str]:
    audited = frozenset(_normalize_domain(item) for item in manifest_domains)
    requested = frozenset(_normalize_domain(item) for item in query.allowed_domains)
    broad = BROAD_SEARCH_PERMISSION in permissions
    if requested and not broad and not all(
        any(_domain_matches(domain, allowed) for allowed in audited)
        for domain in requested
    ):
        raise SearchCapabilityError(
            "search_domain_denied", "requested domains exceed the audited allowlist"
        )
    if not requested and not audited and not broad:
        raise SearchCapabilityError(
            "search_broad_permission_required", "broad search was not audited"
        )
    return requested or audited


def _normalize_domain(value: str) -> str:
    normalized = value.strip().lower().rstrip(".")
    if not normalized or "://" in normalized or "/" in normalized or ":" in normalized:
        raise SearchCapabilityError(
            "search_domain_invalid", "search domains must be plain DNS host names"
        )
    return normalized.removeprefix(".")


def _domain_matches(host: str, allowed: str) -> bool:
    return host == allowed or host.endswith(f".{allowed}")


def _validate_requested_budget(
    requested: float | None, manifest_budget: float | None
) -> None:
    if manifest_budget is not None and (requested is None or requested > manifest_budget):
        raise SearchCapabilityError(
            "search_budget_denied", "requested search budget exceeds the audited budget"
        )


def _validate_actual_cost(
    actual: float | None,
    requested: float | None,
    manifest_budget: float | None,
) -> None:
    budgets = [value for value in (requested, manifest_budget) if value is not None]
    if budgets and actual is None:
        raise SearchCapabilityError(
            "search_cost_unknown", "search provider did not report cost under a bounded budget"
        )
    if actual is not None and any(actual > budget for budget in budgets):
        raise SearchCapabilityError(
            "search_budget_exceeded", "search provider exceeded an enforced budget"
        )


def _validate_evidence(
    query: SearchQuery,
    result: SearchResult,
    evidence: SearchEvidence,
    allowed_domains: frozenset[str],
) -> None:
    host = (urlsplit(str(evidence.source_url)).hostname or "").lower().rstrip(".")
    if not host or (
        allowed_domains
        and not any(_domain_matches(host, allowed) for allowed in allowed_domains)
    ):
        raise SearchCapabilityError(
            "search_result_domain_denied", "search result URL is outside the effective allowlist"
        )
    if evidence.published_at is not None and evidence.published_at > evidence.observed_at:
        raise SearchCapabilityError(
            "search_pit_violation", "published_at is after observed_at"
        )
    if evidence.observed_at < query.observed_at:
        raise SearchCapabilityError(
            "search_pit_violation", "evidence was observed before this search request"
        )
    if evidence.observed_at > evidence.received_at or evidence.received_at > result.completed_at:
        raise SearchCapabilityError(
            "search_pit_violation", "search timestamps violate PIT ordering"
        )
    expected_hash = search_evidence_content_hash(
        title=evidence.title,
        snippet=evidence.snippet,
        source_url=str(evidence.source_url),
        published_at=evidence.published_at,
    )
    if evidence.content_hash != expected_hash:
        raise SearchCapabilityError(
            "search_content_hash_mismatch", "search evidence content hash is invalid"
        )
