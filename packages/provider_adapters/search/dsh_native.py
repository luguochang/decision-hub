from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Protocol, cast
from urllib.parse import urlsplit

from pydantic import AnyUrl

from packages.contracts_py.decision_hub_contracts.models import (
    SearchEvidence,
    SearchQuery,
    SearchResult,
)
from packages.kernel.decision_hub_kernel.application.search import search_evidence_content_hash
from packages.kernel.decision_hub_kernel.ports.search import SearchCapabilityError


class DshSearchHttpClient(Protocol):
    async def post(self, url: str, *, headers: Mapping[str, str], json: object) -> object: ...


class _JsonResponse(Protocol):
    status_code: int

    def json(self) -> object: ...


class DshNativeWebSearchTransport:
    """DeepSeek Anthropic-compatible native web_search transport.

    DSH remains the harness owner; this adapter is only the typed boundary for
    the native Search route. Results are discovery evidence until fetched and
    attested by the Hub.
    """

    def __init__(
        self,
        *,
        api_key: str,
        client: DshSearchHttpClient | None = None,
        endpoint: str = "https://api.deepseek.com/anthropic/v1/messages",
        model: str = "deepseek-v4-flash",
        clock: Callable[[], datetime] | None = None,
        provider_id: str = "dsh-native-web-search",
        estimated_cost_usd: float = 0.01,
        verify_tls: bool = True,
    ) -> None:
        if not api_key.strip():
            raise ValueError("dsh_search_api_key_required")
        if not endpoint.startswith("https://"):
            raise ValueError("dsh_search_endpoint_must_use_https")
        self._api_key, self._client, self._endpoint = api_key, client, endpoint
        self._model, self._clock = model, clock or (lambda: datetime.now(UTC))
        self._provider_id, self._estimated_cost_usd = provider_id, estimated_cost_usd
        self._verify_tls = verify_tls

    @classmethod
    def from_env(cls) -> DshNativeWebSearchTransport:
        key = next(
            (
                os.getenv(name, "").strip()
                for name in ("DEEPSEEK_API_KEY", "DECISION_HUB_DSH_API_KEY")
                if os.getenv(name, "").strip()
            ),
            "",
        )
        if not key:
            raise SearchCapabilityError(
                "search_configuration_invalid", "DSH native search requires DEEPSEEK_API_KEY"
            )
        return cls(
            api_key=key,
            endpoint=os.getenv(
                "DECISION_HUB_DSH_SEARCH_ENDPOINT", "https://api.deepseek.com/anthropic/v1/messages"
            ),
            model=os.getenv("DECISION_HUB_DSH_SEARCH_MODEL", "deepseek-v4-flash"),
            estimated_cost_usd=float(
                os.getenv("DECISION_HUB_DSH_SEARCH_ESTIMATED_COST_USD", "0.01")
            ),
            verify_tls=os.getenv("DECISION_HUB_DSH_SEARCH_VERIFY_TLS", "1").strip().lower()
            not in {"0", "false", "no", "off"},
        )

    async def search(self, query: SearchQuery) -> SearchResult:
        if self._client is None:
            import httpx

            async with httpx.AsyncClient(timeout=20.0, verify=self._verify_tls) as client:
                return await self._search_with_client(client, query)
        return await self._search_with_client(self._client, query)

    async def _search_with_client(
        self, client: DshSearchHttpClient, query: SearchQuery
    ) -> SearchResult:
        observed_at = self._clock().astimezone(UTC)
        try:
            response = await client.post(
                self._endpoint,
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self._model,
                    "max_tokens": 2048,
                    "messages": [
                        {
                            "role": "user",
                            "content": _search_prompt(query),
                        }
                    ],
                    "tools": [{"type": "web_search_20260209", "name": "web_search"}],
                },
            )
            status = getattr(response, "status_code", 200)
            if status == 429 or status >= 500:
                raise SearchCapabilityError(
                    "search_provider_failed",
                    f"DSH native route returned HTTP {status}",
                    retryable=True,
                )
            if status >= 400:
                raise SearchCapabilityError(
                    "search_provider_denied", f"DSH native route returned HTTP {status}"
                )
            payload = (
                response if isinstance(response, Mapping) else cast(_JsonResponse, response).json()
            )
            evidence = _evidence_from_payload(
                cast(Mapping[str, object], payload), query, observed_at
            )
            if not evidence:
                raise SearchCapabilityError(
                    "search_no_sources",
                    "DSH native search returned no structured sources",
                    retryable=True,
                )
            completed_at = self._clock().astimezone(UTC)
            return SearchResult(
                request_id=query.request_id,
                capability_id=query.capability_id,
                provider=self._provider_id,
                evidence=evidence,
                cost_usd=self._estimated_cost_usd,
                completed_at=completed_at,
            )
        except SearchCapabilityError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise SearchCapabilityError(
                "search_provider_failed", "DSH native search request failed", retryable=True
            ) from exc


def _evidence_from_payload(
    payload: Mapping[str, object], query: SearchQuery, observed_at: datetime
) -> list[SearchEvidence]:
    blocks: list[Mapping[str, object]] = []
    content = payload.get("content")
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        blocks.extend(
            cast(Mapping[str, object], item) for item in content if isinstance(item, Mapping)
        )
    output = payload.get("output")
    if isinstance(output, Sequence) and not isinstance(output, (str, bytes)):
        blocks.extend(
            cast(Mapping[str, object], item) for item in output if isinstance(item, Mapping)
        )
    found: list[SearchEvidence] = []
    for index, block in enumerate(blocks):
        raw = (
            block.get("web_search_tool_result")
            or block.get("search_results")
            or block.get("results")
        )
        if isinstance(block.get("type"), str) and "web_search" in str(block.get("type")):
            raw = block.get("content") or block.get("results") or raw
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            continue
        for result_index, item in enumerate(raw):
            if not isinstance(item, Mapping):
                continue
            url, title = item.get("url"), item.get("title") or item.get("name")
            snippet = item.get("snippet") or item.get("content") or item.get("text")
            if not (
                isinstance(url, str) and isinstance(title, str) and url.strip() and title.strip()
            ):
                continue
            if query.allowed_domains:
                host = (urlsplit(url).hostname or "").lower().rstrip(".")
                if not any(
                    host == domain or host.endswith(f".{domain}")
                    for domain in query.allowed_domains
                ):
                    continue
            if not isinstance(snippet, str) or not snippet.strip():
                # The native route sometimes returns only title/url and an
                # encrypted body. Keep this as a locator candidate; fetch is
                # still mandatory before it can satisfy a financial fact.
                snippet = f"Search result locator only; fetch required: {title.strip()}"
            published_at = _parse_datetime(item.get("published_at") or item.get("published_date"))
            title, snippet, url = title.strip(), snippet.strip(), url.strip()
            found.append(
                SearchEvidence(
                    evidence_id=f"dsh-native:{query.request_id}:{index}:{result_index}",
                    title=title,
                    snippet=snippet,
                    source_url=AnyUrl(url),
                    observed_at=observed_at,
                    published_at=published_at,
                    received_at=observed_at,
                    content_hash=search_evidence_content_hash(
                        title=title, snippet=snippet, source_url=url, published_at=published_at
                    ),
                )
            )
    return found[: query.max_results]


def _search_prompt(query: SearchQuery) -> str:
    domains = ""
    if query.allowed_domains:
        domains = f" Restrict results to these domains: {', '.join(query.allowed_domains)}."
    return (
        f"Search current sources for: {query.query}.{domains} "
        "Return structured source URLs and titles."
    )


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo and parsed.utcoffset() else None
