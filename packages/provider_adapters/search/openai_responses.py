from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from packages.contracts_py.decision_hub_contracts.models import (
    SearchEvidence,
    SearchQuery,
    SearchResult,
)
from packages.kernel.decision_hub_kernel.application.search import (
    search_evidence_content_hash,
)
from packages.kernel.decision_hub_kernel.ports.search import SearchCapabilityError


class _ResponsesApi(Protocol):
    async def create(self, **kwargs: object) -> object: ...


class ResponsesClient(Protocol):
    @property
    def responses(self) -> _ResponsesApi: ...


_TRACKING_QUERY_KEYS = frozenset(
    {
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "openai",
        "ref_src",
    }
)


@dataclass(frozen=True)
class _CitationBinding:
    source_url: str
    title: str
    excerpts: tuple[str, ...]


class OpenAIResponsesWebSearchTransport:
    """OpenAI SDK transport for opt-in Responses ``web_search`` discovery.

    This adapter only converts provider output into the canonical SearchResult.
    Permission, domain, PIT and budget enforcement remains outside the adapter.
    """

    def __init__(
        self,
        *,
        client: ResponsesClient,
        model: str,
        provider_id: str = "openai-compatible-responses-web-search",
        estimated_cost_per_call_usd: float = 0.01,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("search_model_required")
        if estimated_cost_per_call_usd < 0:
            raise ValueError("search_cost_estimate_invalid")
        self._client = client
        self._model = model
        self._provider_id = provider_id
        self._estimated_cost_per_call_usd = estimated_cost_per_call_usd
        self._clock = clock or (lambda: datetime.now(UTC))

    @classmethod
    def from_env(cls) -> OpenAIResponsesWebSearchTransport:
        api_key = _first_nonempty(
            "DECISION_HUB_SEARCH_API_KEY",
            "OPENAI_API_KEY",
            "SUB2API_API_KEY",
        )
        if api_key is None:
            raise SearchCapabilityError(
                "search_configuration_invalid",
                "web.search was enabled without a provider API key",
            )
        base_url = _first_nonempty(
            "DECISION_HUB_SEARCH_BASE_URL",
            "OPENAI_BASE_URL",
            "SUB2API_BASE_URL",
        )
        model = _first_nonempty(
            "DECISION_HUB_SEARCH_MODEL",
            "DECISION_HUB_MODEL",
        ) or "gpt-5.5"
        provider_id = os.getenv(
            "DECISION_HUB_SEARCH_PROVIDER_ID",
            "openai-compatible-responses-web-search",
        )
        estimated_cost = float(
            os.getenv("DECISION_HUB_SEARCH_ESTIMATED_COST_PER_CALL_USD", "0.01")
        )

        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        return cls(
            client=cast(ResponsesClient, client),
            model=model,
            provider_id=provider_id,
            estimated_cost_per_call_usd=estimated_cost,
        )

    async def search(self, query: SearchQuery) -> SearchResult:
        if (
            query.max_cost_usd is not None
            and query.max_cost_usd < self._estimated_cost_per_call_usd
        ):
            raise SearchCapabilityError(
                "search_budget_denied",
                "search budget is below the configured minimum call estimate",
            )
        tool: dict[str, object] = {"type": "web_search"}
        if query.allowed_domains:
            tool["filters"] = {"allowed_domains": query.allowed_domains}
        try:
            response = await self._client.responses.create(
                model=self._model,
                input=(
                    "Perform one focused web search for current, directly relevant sources. "
                    "Return a concise factual summary and do not invent citations.\n\n"
                    f"Query: {query.query}"
                ),
                tools=[tool],
                include=["web_search_call.action.sources"],
                reasoning={"effort": "low"},
                max_output_tokens=768,
            )
            payload = _response_mapping(response)
            completed_at = self._clock().astimezone(UTC)
            sources, call_count = _sources(payload)
            if not sources:
                raise SearchCapabilityError(
                    "search_no_sources",
                    "web search completed without explicit action.sources",
                    retryable=True,
                )
            evidence = _canonical_evidence(
                bindings=_citation_bindings(payload, sources),
                observed_at=completed_at,
                max_results=query.max_results,
            )
            if not evidence:
                raise SearchCapabilityError(
                    "search_no_attributed_sources",
                    "web search sources were not bound to structured output citations",
                )
            estimated_cost = max(1, call_count) * self._estimated_cost_per_call_usd
            if query.max_cost_usd is not None and estimated_cost > query.max_cost_usd:
                raise SearchCapabilityError(
                    "search_budget_exceeded",
                    "web search exceeded the requested estimated cost budget",
                )
            return SearchResult(
                request_id=query.request_id,
                capability_id=query.capability_id,
                provider=self._provider_id,
                evidence=evidence,
                cost_usd=estimated_cost,
                completed_at=completed_at,
            )
        except SearchCapabilityError:
            raise
        except (TypeError, ValueError) as exc:
            raise SearchCapabilityError(
                "search_output_invalid",
                "Responses web search returned an invalid source payload",
            ) from exc
        except asyncio.CancelledError:
            # Cancellation is lifecycle control flow, not a provider failure.
            raise
        except BaseException as exc:
            raise SearchCapabilityError(
                "search_provider_failed",
                "Responses web search request failed",
                retryable=True,
            ) from exc


def _response_mapping(response: object) -> Mapping[str, object]:
    if isinstance(response, Mapping):
        return cast(Mapping[str, object], response)
    dump = getattr(response, "model_dump", None)
    if not callable(dump):
        raise TypeError("Responses result is not serializable")
    payload = dump(mode="json")
    if not isinstance(payload, Mapping):
        raise TypeError("Responses result did not serialize to an object")
    return cast(Mapping[str, object], payload)


def _sources(payload: Mapping[str, object]) -> tuple[list[Mapping[str, object]], int]:
    output = payload.get("output")
    if not isinstance(output, Sequence) or isinstance(output, (str, bytes)):
        return [], 0
    found: list[Mapping[str, object]] = []
    call_count = 0
    for item in output:
        if not isinstance(item, Mapping) or item.get("type") != "web_search_call":
            continue
        call_count += 1
        action = item.get("action")
        if not isinstance(action, Mapping):
            continue
        sources = action.get("sources")
        if not isinstance(sources, Sequence) or isinstance(sources, (str, bytes)):
            continue
        found.extend(
            cast(Mapping[str, object], source)
            for source in sources
            if isinstance(source, Mapping)
        )
    return found, call_count


def _citation_bindings(
    payload: Mapping[str, object],
    sources: Sequence[Mapping[str, object]],
) -> list[_CitationBinding]:
    discovered: dict[str, Mapping[str, object]] = {}
    for source in sources:
        raw_url = source.get("url")
        if not isinstance(raw_url, str) or not raw_url.strip():
            raise ValueError("web search source URL is missing")
        normalized_url = _normalized_url(raw_url)
        discovered.setdefault(normalized_url, source)

    excerpts_by_url: dict[str, list[str]] = {}
    title_by_url: dict[str, str] = {}
    claim_owner: dict[str, str] = {}
    ordered_urls: list[str] = []
    output = payload.get("output")
    if not isinstance(output, Sequence) or isinstance(output, (str, bytes)):
        return []
    for item in output:
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, Sequence) or isinstance(content, (str, bytes)):
            continue
        for block in content:
            if not isinstance(block, Mapping) or block.get("type") not in {"output_text", "text"}:
                continue
            text = block.get("text")
            annotations = block.get("annotations")
            if not isinstance(text, str) or not text.strip():
                continue
            if annotations is None:
                continue
            if not isinstance(annotations, Sequence) or isinstance(annotations, (str, bytes)):
                raise ValueError("web search output annotations are invalid")
            for annotation in annotations:
                if not isinstance(annotation, Mapping) or annotation.get("type") != "url_citation":
                    continue
                raw_url = annotation.get("url")
                raw_title = annotation.get("title")
                start = annotation.get("start_index")
                end = annotation.get("end_index")
                if (
                    not isinstance(raw_url, str)
                    or not raw_url.strip()
                    or not isinstance(raw_title, str)
                    or not raw_title.strip()
                    or not isinstance(start, int)
                    or isinstance(start, bool)
                    or not isinstance(end, int)
                    or isinstance(end, bool)
                ):
                    raise ValueError("web search URL citation is invalid")
                normalized_url = _normalized_url(raw_url)
                if normalized_url not in discovered:
                    raise ValueError("web search citation URL was absent from action.sources")
                excerpt = _citation_excerpt(text, start, end)
                if not excerpt:
                    raise ValueError("web search citation span is invalid")
                claim_key = _claim_key(excerpt)
                previous_owner = claim_owner.get(claim_key)
                if previous_owner is not None and previous_owner != normalized_url:
                    # One generated claim cannot establish several independent sources.
                    continue
                claim_owner[claim_key] = normalized_url
                if normalized_url not in excerpts_by_url:
                    excerpts_by_url[normalized_url] = []
                    title_by_url[normalized_url] = raw_title.strip()
                    ordered_urls.append(normalized_url)
                if excerpt not in excerpts_by_url[normalized_url]:
                    excerpts_by_url[normalized_url].append(excerpt)

    return [
        _CitationBinding(
            source_url=url,
            title=title_by_url[url],
            excerpts=tuple(excerpts_by_url[url]),
        )
        for url in ordered_urls
        if excerpts_by_url[url]
    ]


def _normalized_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if scheme not in {"http", "https"} or not hostname:
        raise ValueError("web search source URL is invalid")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("web search source URL credentials are forbidden")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("web search source URL port is invalid") from exc
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        netloc = f"{hostname}:{port}"
    query_items = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in _TRACKING_QUERY_KEYS
    ]
    return urlunsplit(
        (
            scheme,
            netloc,
            parsed.path or "/",
            urlencode(query_items, doseq=True),
            "",
        )
    )


def _citation_excerpt(text: str, start: int, end: int) -> str:
    if start < 0 or end <= start or end > len(text):
        return ""
    return text[start:end].strip()[:20000]


def _claim_key(excerpt: str) -> str:
    normalized = " ".join(excerpt.split()).casefold()
    return hashlib.sha256(normalized.encode()).hexdigest()


def _canonical_evidence(
    *,
    bindings: Sequence[_CitationBinding],
    observed_at: datetime,
    max_results: int,
) -> list[SearchEvidence]:
    result: list[SearchEvidence] = []
    seen: set[str] = set()
    for binding in bindings:
        normalized_url = binding.source_url
        if normalized_url in seen:
            continue
        seen.add(normalized_url)
        excerpt = "\n".join(binding.excerpts)[:20000]
        title = binding.title
        content_hash = search_evidence_content_hash(
            title=title,
            snippet=excerpt,
            source_url=normalized_url,
            published_at=None,
        )
        evidence_id = "search_" + hashlib.sha256(
            json.dumps(
                {"content_hash": content_hash, "url": normalized_url},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:32]
        result.append(
            SearchEvidence.model_validate(
                {
                    "evidence_id": evidence_id,
                    "title": title,
                    "snippet": excerpt,
                    "source_url": normalized_url,
                    "observed_at": observed_at,
                    "published_at": None,
                    "received_at": observed_at,
                    "content_hash": content_hash,
                }
            )
        )
        if len(result) >= max_results:
            break
    return result


def _first_nonempty(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None
