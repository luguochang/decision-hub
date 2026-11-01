from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import AnyUrl

from packages.contracts_py.decision_hub_contracts import ResearchCapabilityQuery
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
)
from packages.provider_adapters.official_sources import OfficialDocumentResearchAdapter
from packages.provider_adapters.research import FetchedDocument
from packages.provider_adapters.research.documents import DocumentFetcher
from packages.provider_adapters.research.source_registry import ResearchSourceRegistry

ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / "packs" / "crypto_macro"
OBSERVED = datetime(2026, 9, 4, 13, 0, tzinfo=UTC)
PUBLISHED = datetime(2026, 9, 3, 12, 30, tzinfo=UTC)
FEED_URL = "https://www.federalreserve.gov/feeds/speeches.xml"
SPEECH_URL = "https://www.federalreserve.gov/newsevents/speech/waller20260903a.htm"


def _query(
    target_url: str = FEED_URL, *, requirement_id: str = "event_identity"
) -> ResearchCapabilityQuery:
    return ResearchCapabilityQuery(
        schema_version="research-capability-query.v1",
        request_id="official-event-call-1",
        capability_id="official.macro",
        requirement_id=requirement_id,
        query="Waller speech on 2026-09-03",
        target_url=AnyUrl(target_url),
        symbols=[],
        fields=[],
        allowed_domains=["federalreserve.gov"],
        max_results=10,
        max_cost_usd=0.0,
        research_session_id="official-event-session-1",
        round=1,
        mode="live",
        observed_at=OBSERVED,
        cutoff_at=OBSERVED + timedelta(minutes=1),
        event_id="event-waller-20260903",
    )


def _adapter(fetcher: DocumentFetcher) -> OfficialDocumentResearchAdapter:
    return OfficialDocumentResearchAdapter(
        fetcher=fetcher,
        source_registry=ResearchSourceRegistry.from_pack(PACK_ROOT),
    )


def test_fed_speech_feed_produces_typed_event_identity_facts() -> None:
    body = f"""<?xml version="1.0"?>
    <rss><channel><item>
      <title>Waller, The Economic Outlook and Some Comments on My Policy Communication</title>
      <link>{SPEECH_URL}</link><guid>{SPEECH_URL}</guid>
      <description>Speech at Reuters NEXT Newsmaker Interview</description>
      <pubDate>Thu, 3 Sep 2026 12:30:00 GMT</pubDate>
    </item></channel></rss>"""

    async def fetcher(url: str) -> FetchedDocument:
        return FetchedDocument(
            source_url=url,
            text=body,
            observed_at=OBSERVED,
            received_at=OBSERVED,
        )

    result = asyncio.run(_adapter(fetcher).execute(_query()))

    assert result.provider == "fed.monetary_policy"
    assert len(result.evidence_candidates) == 1
    evidence = result.evidence_candidates[0]
    assert str(evidence.source_url) == SPEECH_URL
    assert evidence.published_at == PUBLISHED
    assert evidence.authority == "official"
    assert evidence.structured_payload_ref == "source-registry://fed.monetary_policy"
    assert {fact.field: fact.value for fact in result.facts or []} == {
        "event_actor": "Waller",
        "event_time": PUBLISHED.isoformat(),
        "revision_status": "original",
    }
    assert all(fact.metric_family == "event.identity" for fact in result.facts or [])
    assert all(fact.event_offset == "t0" for fact in result.facts or [])
    assert all(
        fact.attributes["event_id"] == "event-waller-20260903" for fact in result.facts or []
    )


def test_official_html_requires_trusted_publication_time() -> None:
    async def fetcher(url: str) -> FetchedDocument:
        return FetchedDocument(
            source_url=url,
            text="Waller, The Economic Outlook and Some Comments on Policy Communication",
            observed_at=OBSERVED,
            received_at=OBSERVED,
            published_at=None,
        )

    with pytest.raises(ResearchCapabilityError) as raised:
        asyncio.run(_adapter(fetcher).execute(_query(SPEECH_URL)))
    assert raised.value.error_code == "official_event_identity_incomplete"


def test_redirect_to_unregistered_domain_fails_before_evidence_creation() -> None:
    async def fetcher(_url: str) -> FetchedDocument:
        return FetchedDocument(
            source_url="https://unknown.example/copied-speech",
            text="Waller, copied speech",
            observed_at=OBSERVED,
            received_at=OBSERVED,
            published_at=PUBLISHED,
        )

    with pytest.raises(ResearchCapabilityError) as raised:
        asyncio.run(_adapter(fetcher).execute(_query(SPEECH_URL)))
    assert raised.value.error_code == "research_source_unknown"


def test_official_event_parser_rejects_policy_delta_instead_of_relabelling_facts() -> None:
    fetched = False

    async def fetcher(url: str) -> FetchedDocument:
        nonlocal fetched
        fetched = True
        return FetchedDocument(
            source_url=url,
            text="Waller current policy statement without a trusted baseline",
            observed_at=OBSERVED,
            received_at=OBSERVED,
            published_at=PUBLISHED,
        )

    with pytest.raises(ResearchCapabilityError) as raised:
        asyncio.run(
            _adapter(fetcher).execute(_query(SPEECH_URL, requirement_id="policy_or_data_delta"))
        )

    assert raised.value.error_code == "research_capability_requirement_unsupported"
    assert fetched is False


@pytest.mark.parametrize("prefix", ["", "\ufeff"])
def test_feed_selects_requested_event_instead_of_first_entry(prefix: str) -> None:
    body = prefix + f'''<?xml version="1.0"?><rss><channel>
    <item><title>Miller, Other speech</title>
    <link>https://www.federalreserve.gov/newsevents/speech/miller20260904a.htm</link>
    <pubDate>Fri, 4 Sep 2026 12:30:00 GMT</pubDate></item>
    <item><title>Waller, Requested speech</title><link>{SPEECH_URL}</link>
    <pubDate>Thu, 3 Sep 2026 12:30:00 GMT</pubDate></item>
    </channel></rss>'''

    async def fetcher(url: str) -> FetchedDocument:
        return FetchedDocument(url, body, OBSERVED, OBSERVED)

    result = asyncio.run(_adapter(fetcher).execute(_query()))
    assert str(result.evidence_candidates[0].source_url) == SPEECH_URL
    assert next(f.value for f in result.facts or [] if f.field == "event_actor") == "Waller"


@pytest.mark.parametrize("query_text", ["latest official speech", "Waller speech on 2026-09-02"])
def test_feed_does_not_bind_an_unmatched_entry_to_request_event(query_text: str) -> None:
    body = f'''<rss><channel><item><title>Waller, Requested speech</title>
    <link>{SPEECH_URL}</link><pubDate>Thu, 3 Sep 2026 12:30:00 GMT</pubDate>
    </item></channel></rss>'''

    async def fetcher(url: str) -> FetchedDocument:
        return FetchedDocument(url, body, OBSERVED, OBSERVED)

    query = _query().model_copy(update={"query": query_text})
    with pytest.raises(ResearchCapabilityError) as raised:
        asyncio.run(_adapter(fetcher).execute(query))
    assert raised.value.error_code == "official_event_target_unresolved"


def test_feed_rejects_ambiguous_same_actor_same_date() -> None:
    body = '<rss><channel>' + ''.join(
        f'''<item><title>Waller, Speech {suffix}</title>
        <link>https://www.federalreserve.gov/newsevents/speech/waller20260903{suffix}.htm</link>
        <pubDate>Thu, 3 Sep 2026 12:30:00 GMT</pubDate></item>'''
        for suffix in ("a", "b")
    ) + '</channel></rss>'

    async def fetcher(url: str) -> FetchedDocument:
        return FetchedDocument(url, body, OBSERVED, OBSERVED)

    with pytest.raises(ResearchCapabilityError) as raised:
        asyncio.run(_adapter(fetcher).execute(_query()))
    assert raised.value.error_code == "official_event_target_unresolved"
