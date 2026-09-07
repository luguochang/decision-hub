from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Protocol, cast

from pydantic import AnyUrl

from packages.contracts_py.decision_hub_contracts.models import (
    SearchEvidence,
    SearchQuery,
    SearchResult,
)
from packages.kernel.decision_hub_kernel.application.search import (
    search_evidence_content_hash,
)
from packages.kernel.decision_hub_kernel.ports.search import SearchCapabilityError


class TavilyHttpClient(Protocol):
    async def post(self, url: str, *, headers: Mapping[str, str], json: object) -> object: ...


class _JsonResponse(Protocol):
    status_code: int

    def json(self) -> object: ...


class TavilySearchTransport:
    """Thin Tavily Search API adapter used only as an audited fallback.

    It intentionally returns discovery evidence. Fetching and authority
    promotion remain separate capabilities behind the Hub Evidence Gateway.
    """

    def __init__(
        self,
        *,
        api_key: str,
        client: TavilyHttpClient | None = None,
        endpoint: str = "https://api.tavily.com/search",
        clock: Callable[[], datetime] | None = None,
        provider_id: str = "tavily-search",
        estimated_cost_usd: float = 0.008,
    ) -> None:
        if not api_key.strip():
            raise ValueError("tavily_api_key_required")
        if not endpoint.startswith("https://"):
            raise ValueError("tavily_endpoint_must_use_https")
        self._api_key = api_key
        self._client = client
        self._endpoint = endpoint
        self._clock = clock or (lambda: datetime.now(UTC))
        self._provider_id = provider_id
        if estimated_cost_usd < 0:
            raise ValueError("tavily_cost_estimate_invalid")
        self._estimated_cost_usd = estimated_cost_usd

    @classmethod
    def from_env(cls) -> TavilySearchTransport:
        key = os.getenv("TAVILY_API_KEY", "").strip()
        if not key:
            raise SearchCapabilityError(
                "search_configuration_invalid",
                "Tavily fallback was enabled without TAVILY_API_KEY",
            )
        return cls(
            api_key=key,
            estimated_cost_usd=float(os.getenv("TAVILY_ESTIMATED_COST_USD", "0.008")),
        )

    async def search(self, query: SearchQuery) -> SearchResult:
        if self._client is None:
            import httpx

            async with httpx.AsyncClient(timeout=20.0) as client:
                return await self._search_with_client(client, query)
        return await self._search_with_client(self._client, query)

    async def _search_with_client(
        self, client: TavilyHttpClient, query: SearchQuery
    ) -> SearchResult:
        observed_at = self._clock().astimezone(UTC)
        payload: dict[str, object] = {
            "api_key": self._api_key,
            "query": query.query,
            "max_results": query.max_results,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
        }
        if query.allowed_domains:
            payload["include_domains"] = query.allowed_domains
        try:
            response = await client.post(
                self._endpoint,
                headers={"content-type": "application/json"},
                json=payload,
            )
            status = getattr(response, "status_code", 200)
            if status == 429 or status >= 500:
                raise SearchCapabilityError(
                    "search_provider_failed",
                    f"Tavily returned HTTP {status}",
                    retryable=True,
                )
            if status >= 400:
                raise SearchCapabilityError(
                    "search_provider_denied",
                    f"Tavily returned HTTP {status}",
                )
            body = (
                response if isinstance(response, Mapping) else cast(_JsonResponse, response).json()
            )
            if not isinstance(body, Mapping):
                raise TypeError("Tavily response is not an object")
            results = body.get("results")
            if not isinstance(results, list):
                raise TypeError("Tavily response results are missing")
            completed_at = self._clock().astimezone(UTC)
            evidence: list[SearchEvidence] = []
            for index, item in enumerate(results[: query.max_results]):
                if not isinstance(item, Mapping):
                    continue
                url = item.get("url")
                title = item.get("title")
                snippet = item.get("content")
                if not (
                    isinstance(url, str)
                    and isinstance(title, str)
                    and isinstance(snippet, str)
                    and url.strip()
                    and title.strip()
                    and snippet.strip()
                ):
                    continue
                published_at = _parse_datetime(item.get("published_date"))
                evidence.append(
                    SearchEvidence(
                        evidence_id=f"tavily:{query.request_id}:{index}",
                        title=title.strip(),
                        snippet=snippet.strip(),
                        source_url=AnyUrl(url.strip()),
                        observed_at=observed_at,
                        published_at=published_at,
                        received_at=completed_at,
                        content_hash=search_evidence_content_hash(
                            title=title.strip(),
                            snippet=snippet.strip(),
                            source_url=url.strip(),
                            published_at=published_at,
                        ),
                    )
                )
            if not evidence:
                raise SearchCapabilityError(
                    "search_no_sources",
                    "Tavily returned no usable sources",
                    retryable=True,
                )
            return SearchResult(
                request_id=query.request_id,
                capability_id=query.capability_id,
                provider=self._provider_id,
                evidence=evidence,
                # Translate the provider's credit price through an explicit
                # policy. Never silently report a free/zero search.
                cost_usd=self._estimated_cost_usd,
                completed_at=completed_at,
            )
        except SearchCapabilityError:
            raise
        except (TypeError, ValueError, KeyError) as exc:
            raise SearchCapabilityError(
                "search_output_invalid", "Tavily returned an invalid payload"
            ) from exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise SearchCapabilityError(
                "search_provider_failed", "Tavily request failed", retryable=True
            ) from exc


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)
