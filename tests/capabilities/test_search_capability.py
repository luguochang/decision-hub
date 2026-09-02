from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest
from pydantic import AnyUrl

from packages.contracts_py.decision_hub_contracts.models import (
    CapabilityManifest,
    SearchEvidence,
    SearchQuery,
    SearchResult,
)
from packages.kernel.decision_hub_kernel.application.search import (
    SEARCH_INPUT_SCHEMA_REF,
    SEARCH_OUTPUT_SCHEMA_REF,
    SearchCapabilityService,
    search_evidence_content_hash,
)
from packages.kernel.decision_hub_kernel.application.workbench import WorkbenchAssetService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.search import SearchCapabilityError
from packages.provider_adapters.search import (
    FakeSearchTransport,
    OpenAICompatibleSearchTransport,
    OpenAIResponsesWebSearchTransport,
)

REQUESTED_AT = datetime(2026, 8, 29, 2, 0, tzinfo=UTC)
OBSERVED_AT = REQUESTED_AT + timedelta(seconds=1)
RECEIVED_AT = OBSERVED_AT + timedelta(seconds=1)
COMPLETED_AT = RECEIVED_AT + timedelta(seconds=1)


def _manifest(
    *,
    status: Literal[
        "discovered", "audited", "enabled", "shadow", "rejected", "retired"
    ] = "audited",
    permissions: list[str] | None = None,
    domains: list[str] | None = None,
    max_cost_usd: float | None = 0.05,
    timeout_seconds: float = 1,
) -> CapabilityManifest:
    return CapabilityManifest(
        capability_id="search.web.primary",
        version="1.0.0",
        capability_type="tool",
        provider="fixture-search",
        license="owner-approved",
        input_schema_ref=SEARCH_INPUT_SCHEMA_REF,
        output_schema_ref=SEARCH_OUTPUT_SCHEMA_REF,
        permissions=(
            permissions if permissions is not None else ["read_only", "network:https"]
        ),
        network_domains=domains if domains is not None else ["federalreserve.gov"],
        timeout_seconds=timeout_seconds,
        max_cost_usd=max_cost_usd,
        status=status,
    )


def _query(*, domains: list[str] | None = None, max_cost_usd: float | None = 0.03) -> SearchQuery:
    return SearchQuery(
        request_id="search-request-1",
        capability_id="search.web.primary",
        query="Federal Reserve policy outlook",
        allowed_domains=domains if domains is not None else ["federalreserve.gov"],
        max_results=3,
        max_cost_usd=max_cost_usd,
        observed_at=REQUESTED_AT,
    )


def _evidence(
    *,
    source_url: AnyUrl | str = "https://www.federalreserve.gov/newsevents/speech/example.htm",
    observed_at: datetime = OBSERVED_AT,
    received_at: datetime = RECEIVED_AT,
) -> SearchEvidence:
    title = "Policy outlook"
    snippet = "The Committee remains attentive to inflation and employment risks."
    published_at = REQUESTED_AT - timedelta(hours=1)
    source_url_value = AnyUrl(str(source_url))
    return SearchEvidence(
        evidence_id="search-evidence-1",
        title=title,
        snippet=snippet,
        source_url=source_url_value,
        observed_at=observed_at,
        published_at=published_at,
        received_at=received_at,
        content_hash=search_evidence_content_hash(
            title=title,
            snippet=snippet,
            source_url=str(source_url_value),
            published_at=published_at,
        ),
    )


def _result(
    *, evidence: list[SearchEvidence] | None = None, cost: float | None = 0.01
) -> SearchResult:
    return SearchResult(
        request_id="search-request-1",
        capability_id="search.web.primary",
        provider="fixture-search",
        evidence=evidence or [_evidence()],
        cost_usd=cost,
        completed_at=COMPLETED_AT,
    )


def _service(
    tmp_path: Path,
    result: SearchResult,
    *,
    manifest: CapabilityManifest | None = None,
    enable: bool = True,
) -> tuple[SearchCapabilityService, FakeSearchTransport]:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'search.sqlite3'}")
    database.create_all()
    workbench = WorkbenchAssetService(database)
    selected = manifest or _manifest()
    workbench.register_capability(selected)
    if enable:
        workbench.set_capability_status(selected.capability_id, "enabled")
    transport = FakeSearchTransport(result)
    return SearchCapabilityService(workbench, transport), transport


def _error(service: SearchCapabilityService, query: SearchQuery) -> str:
    with pytest.raises(SearchCapabilityError) as captured:
        asyncio.run(service.search(query))
    return captured.value.error_code


def test_audited_enabled_search_returns_typed_pit_evidence(tmp_path: Path) -> None:
    service, transport = _service(tmp_path, _result())

    result = asyncio.run(service.search(_query()))

    assert result.evidence[0].source_url.host == "www.federalreserve.gov"
    assert result.evidence[0].published_at <= result.evidence[0].observed_at  # type: ignore[operator]
    assert len(transport.calls) == 1


def test_search_is_denied_before_owner_enable_and_before_transport(tmp_path: Path) -> None:
    service, transport = _service(tmp_path, _result(), enable=False)

    assert _error(service, _query()) == "search_capability_not_enabled"
    assert transport.calls == []


@pytest.mark.parametrize(
    ("manifest", "query", "expected"),
    [
        (
            _manifest(permissions=["network:https"]),
            _query(),
            "search_permission_denied",
        ),
        (
            _manifest(domains=["federalreserve.gov"]),
            _query(domains=["example.com"]),
            "search_domain_denied",
        ),
        (
            _manifest(max_cost_usd=0.02),
            _query(max_cost_usd=0.03),
            "search_budget_denied",
        ),
        (
            _manifest(domains=[], permissions=["read_only", "network:https"]),
            _query(domains=[]),
            "search_broad_permission_required",
        ),
    ],
)
def test_manifest_gate_denies_permission_domain_and_budget(
    tmp_path: Path,
    manifest: CapabilityManifest,
    query: SearchQuery,
    expected: str,
) -> None:
    service, transport = _service(tmp_path, _result(), manifest=manifest)

    assert _error(service, query) == expected
    assert transport.calls == []


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            _result(evidence=[_evidence(source_url="https://example.com/policy")]),
            "search_result_domain_denied",
        ),
        (_result(cost=0.04), "search_budget_exceeded"),
        (_result(cost=None), "search_cost_unknown"),
        (
            _result(
                evidence=[
                    _evidence(),
                    _evidence(source_url="https://federalreserve.gov/duplicate"),
                ]
            ),
            "search_evidence_duplicate",
        ),
        (
            _result(evidence=[_evidence(observed_at=REQUESTED_AT - timedelta(seconds=1))]),
            "search_pit_violation",
        ),
        (
            _result(
                evidence=[
                    _evidence().model_copy(update={"content_hash": "0" * 64})
                ]
            ),
            "search_content_hash_mismatch",
        ),
    ],
)
def test_search_output_is_fail_closed(
    tmp_path: Path, result: SearchResult, expected: str
) -> None:
    service, _ = _service(tmp_path, result)

    assert _error(service, _query()) == expected


def test_manifest_timeout_is_enforced(tmp_path: Path) -> None:
    class SlowTransport:
        async def search(self, query: SearchQuery) -> SearchResult:
            await asyncio.sleep(0.05)
            return _result()

    database = Database(f"sqlite+pysqlite:///{tmp_path / 'timeout.sqlite3'}")
    database.create_all()
    workbench = WorkbenchAssetService(database)
    selected = _manifest(timeout_seconds=0.001)
    workbench.register_capability(selected)
    workbench.set_capability_status(selected.capability_id, "enabled")

    assert (
        _error(SearchCapabilityService(workbench, SlowTransport()), _query())
        == "search_timeout"
    )


def test_openai_compatible_seam_only_accepts_canonical_output() -> None:
    valid = OpenAICompatibleSearchTransport(lambda _query: _result().model_dump(mode="json"))
    assert asyncio.run(valid.search(_query())).request_id == "search-request-1"

    invalid = OpenAICompatibleSearchTransport(lambda _query: {"raw": "provider payload"})
    with pytest.raises(SearchCapabilityError) as captured:
        asyncio.run(invalid.search(_query()))
    assert captured.value.error_code == "search_output_invalid"


class _FakeResponses:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.payload


class _CancelledResponses(_FakeResponses):
    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        raise asyncio.CancelledError()


class _FakeOpenAIClient:
    def __init__(self, payload: object) -> None:
        self.responses = _FakeResponses(payload)


def _responses_payload(
    *,
    sources: list[dict[str, object]],
    citations: list[dict[str, object]] | None = None,
    text: str = "The source reports the latest policy outlook.",
) -> dict[str, object]:
    effective_citations = citations
    if effective_citations is None and sources:
        first_url = sources[0].get("url")
        effective_citations = (
            [
                {
                    "type": "url_citation",
                    "url": first_url,
                    "title": sources[0].get("title", "Policy outlook"),
                    "start_index": 0,
                    "end_index": len(text),
                }
            ]
            if isinstance(first_url, str)
            else []
        )
    return {
        "id": "resp-search-1",
        "output_text": text,
        "output": [
            {
                "type": "web_search_call",
                "id": "search-call-1",
                "action": {"type": "search", "sources": sources},
            },
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                        "annotations": effective_citations or [],
                    }
                ],
            },
        ],
    }


def test_responses_web_search_uses_sources_and_domain_filter() -> None:
    client = _FakeOpenAIClient(
        _responses_payload(
            sources=[
                {
                    "type": "url",
                    "url": "https://www.federalreserve.gov/newsevents/speech/example.htm",
                    "title": "Policy outlook",
                },
                {
                    "type": "url",
                    "url": "https://www.federalreserve.gov/newsevents/speech/example.htm",
                    "title": "Duplicate source",
                },
            ]
        )
    )
    transport = OpenAIResponsesWebSearchTransport(
        client=client,
        model="gpt-5.5",
        provider_id="sub2api-responses-web-search",
        estimated_cost_per_call_usd=0.01,
        clock=lambda: COMPLETED_AT,
    )

    result = asyncio.run(transport.search(_query()))

    assert result.provider == "sub2api-responses-web-search"
    assert result.cost_usd == 0.01
    assert len(result.evidence) == 1
    assert result.evidence[0].title == "Policy outlook"
    assert result.evidence[0].snippet == "The source reports the latest policy outlook."
    assert result.evidence[0].observed_at == COMPLETED_AT
    assert result.evidence[0].received_at == COMPLETED_AT
    request = client.responses.calls[0]
    assert request["include"] == ["web_search_call.action.sources"]
    assert request["reasoning"] == {"effort": "low"}
    assert request["max_output_tokens"] == 768
    assert request["tools"] == [
        {
            "type": "web_search",
            "filters": {"allowed_domains": ["federalreserve.gov"]},
        }
    ]


def test_responses_web_search_keeps_only_sources_bound_by_structured_citations() -> None:
    cited_url = "https://www.federalreserve.gov/newsevents/speech/current.htm"
    unrelated_url = "https://www.federalreserve.gov/newsevents/speech/legacy.htm"
    text = "The current speech changed the policy outlook."
    client = _FakeOpenAIClient(
        _responses_payload(
            sources=[
                {"type": "url", "url": cited_url, "title": "Current speech"},
                {"type": "url", "url": unrelated_url, "title": "Legacy speech"},
            ],
            citations=[
                {
                    "type": "url_citation",
                    "url": cited_url,
                    "title": "Current speech",
                    "start_index": 0,
                    "end_index": len(text),
                }
            ],
            text=text,
        )
    )
    transport = OpenAIResponsesWebSearchTransport(
        client=client,
        model="gpt-5.5",
        clock=lambda: COMPLETED_AT,
    )

    result = asyncio.run(transport.search(_query()))

    assert [str(item.source_url) for item in result.evidence] == [cited_url]
    assert result.evidence[0].snippet == text
    assert unrelated_url not in {str(item.source_url) for item in result.evidence}


def test_responses_web_search_rejects_sources_without_bound_citations() -> None:
    payload = _responses_payload(
        sources=[
            {
                "type": "url",
                "url": "https://www.federalreserve.gov/newsevents/speech/example.htm",
                "title": "Unbound source",
            }
        ],
        citations=[],
    )
    transport = OpenAIResponsesWebSearchTransport(
        client=_FakeOpenAIClient(payload),
        model="gpt-5.5",
        clock=lambda: COMPLETED_AT,
    )

    with pytest.raises(SearchCapabilityError) as captured:
        asyncio.run(transport.search(_query()))

    assert captured.value.error_code == "search_no_attributed_sources"


def test_responses_web_search_binds_each_citation_to_its_exact_text_span() -> None:
    first_url = "https://www.federalreserve.gov/newsevents/speech/first.htm"
    second_url = "https://www.federalreserve.gov/newsevents/speech/second.htm"
    first_claim = "Inflation remains elevated."
    second_claim = "Policy remains data dependent."
    text = f"{first_claim} {second_claim}"
    second_start = len(first_claim) + 1
    client = _FakeOpenAIClient(
        _responses_payload(
            sources=[
                {"type": "url", "url": first_url, "title": "First speech"},
                {"type": "url", "url": second_url, "title": "Second speech"},
            ],
            citations=[
                {
                    "type": "url_citation",
                    "url": first_url,
                    "title": "First citation title",
                    "start_index": 0,
                    "end_index": len(first_claim),
                },
                {
                    "type": "url_citation",
                    "url": second_url,
                    "title": "Second citation title",
                    "start_index": second_start,
                    "end_index": len(text),
                },
            ],
            text=text,
        )
    )

    result = asyncio.run(
        OpenAIResponsesWebSearchTransport(
            client=client,
            model="gpt-5.5",
            clock=lambda: COMPLETED_AT,
        ).search(_query())
    )

    assert [(item.title, item.snippet) for item in result.evidence] == [
        ("First citation title", first_claim),
        ("Second citation title", second_claim),
    ]


def test_responses_web_search_deduplicates_url_aliases_and_repeated_claims() -> None:
    canonical_url = "https://www.federalreserve.gov/newsevents/speech/example.htm"
    tracked_url = canonical_url + "?utm_source=openai#section"
    text = "Policy remains data dependent."
    citation = {
        "type": "url_citation",
        "title": "Policy outlook",
        "start_index": 0,
        "end_index": len(text),
    }
    client = _FakeOpenAIClient(
        _responses_payload(
            sources=[
                {"type": "url", "url": canonical_url, "title": "Policy outlook"},
                {"type": "url", "url": tracked_url, "title": "Tracked alias"},
            ],
            citations=[
                {**citation, "url": tracked_url},
                {**citation, "url": canonical_url},
            ],
            text=text,
        )
    )

    result = asyncio.run(
        OpenAIResponsesWebSearchTransport(
            client=client,
            model="gpt-5.5",
            clock=lambda: COMPLETED_AT,
        ).search(_query())
    )

    assert len(result.evidence) == 1
    assert str(result.evidence[0].source_url) == canonical_url
    assert result.evidence[0].snippet == text


def test_responses_web_search_does_not_count_one_claim_for_multiple_urls() -> None:
    first_url = "https://www.federalreserve.gov/newsevents/speech/first.htm"
    second_url = "https://www.federalreserve.gov/newsevents/speech/second.htm"
    text = "Policy remains data dependent."
    citation = {
        "type": "url_citation",
        "title": "Policy outlook",
        "start_index": 0,
        "end_index": len(text),
    }
    client = _FakeOpenAIClient(
        _responses_payload(
            sources=[
                {"type": "url", "url": first_url},
                {"type": "url", "url": second_url},
            ],
            citations=[
                {**citation, "url": first_url},
                {**citation, "url": second_url},
            ],
            text=text,
        )
    )

    result = asyncio.run(
        OpenAIResponsesWebSearchTransport(
            client=client,
            model="gpt-5.5",
            clock=lambda: COMPLETED_AT,
        ).search(_query())
    )

    assert len(result.evidence) == 1
    assert result.evidence[0].snippet == text


@pytest.mark.parametrize(
    "citation",
    [
        {
            "type": "url_citation",
            "url": "https://example.com/not-in-tool-sources",
            "title": "Unattested source",
            "start_index": 0,
            "end_index": 10,
        },
        {
            "type": "url_citation",
            "url": "https://www.federalreserve.gov/newsevents/speech/example.htm",
            "title": "Out of bounds",
            "start_index": 0,
            "end_index": 1000,
        },
        {
            "type": "url_citation",
            "url": "https://www.federalreserve.gov/newsevents/speech/example.htm",
            "title": "Boolean offset",
            "start_index": False,
            "end_index": 10,
        },
    ],
)
def test_responses_web_search_rejects_unattested_or_invalid_citations(
    citation: dict[str, object],
) -> None:
    source_url = "https://www.federalreserve.gov/newsevents/speech/example.htm"
    transport = OpenAIResponsesWebSearchTransport(
        client=_FakeOpenAIClient(
            _responses_payload(
                sources=[{"type": "url", "url": source_url}],
                citations=[citation],
            )
        ),
        model="gpt-5.5",
        clock=lambda: COMPLETED_AT,
    )

    with pytest.raises(SearchCapabilityError) as captured:
        asyncio.run(transport.search(_query()))

    assert captured.value.error_code == "search_output_invalid"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"output": []}, "search_no_sources"),
        (
            _responses_payload(sources=[{"type": "url", "url": "not-a-url"}]),
            "search_output_invalid",
        ),
    ],
)
def test_responses_web_search_fails_closed(payload: object, expected: str) -> None:
    client = _FakeOpenAIClient(payload)
    transport = OpenAIResponsesWebSearchTransport(
        client=client,
        model="gpt-5.5",
        provider_id="sub2api-responses-web-search",
        estimated_cost_per_call_usd=0.01,
        clock=lambda: COMPLETED_AT,
    )

    with pytest.raises(SearchCapabilityError) as captured:
        asyncio.run(transport.search(_query()))

    assert captured.value.error_code == expected


def test_responses_web_search_preserves_cancellation() -> None:
    client = _FakeOpenAIClient(_responses_payload(sources=[]))
    client.responses = _CancelledResponses(client.responses.payload)
    transport = OpenAIResponsesWebSearchTransport(
        client=client,
        model="gpt-5.5",
        provider_id="sub2api-responses-web-search",
        estimated_cost_per_call_usd=0.01,
        clock=lambda: COMPLETED_AT,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(transport.search(_query()))


def test_responses_web_search_rejects_budget_before_provider_call() -> None:
    client = _FakeOpenAIClient(_responses_payload(sources=[]))
    transport = OpenAIResponsesWebSearchTransport(
        client=client,
        model="gpt-5.5",
        estimated_cost_per_call_usd=0.01,
        clock=lambda: COMPLETED_AT,
    )

    with pytest.raises(SearchCapabilityError) as captured:
        asyncio.run(transport.search(_query(max_cost_usd=0.009)))

    assert captured.value.error_code == "search_budget_denied"
    assert client.responses.calls == []


def test_responses_web_search_rejects_estimated_cost_overrun() -> None:
    payload = _responses_payload(
        sources=[
            {
                "type": "url",
                "url": "https://www.federalreserve.gov/newsevents/speech/example.htm",
            }
        ]
    )
    output = payload["output"]
    assert isinstance(output, list)
    output.append(
        {
            "type": "web_search_call",
            "id": "search-call-2",
            "action": {"type": "search", "sources": []},
        }
    )
    client = _FakeOpenAIClient(payload)
    transport = OpenAIResponsesWebSearchTransport(
        client=client,
        model="gpt-5.5",
        estimated_cost_per_call_usd=0.01,
        clock=lambda: COMPLETED_AT,
    )

    with pytest.raises(SearchCapabilityError) as captured:
        asyncio.run(transport.search(_query(max_cost_usd=0.01)))

    assert captured.value.error_code == "search_budget_exceeded"
