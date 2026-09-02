from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from packages.kernel.decision_hub_kernel.ports.sources import SourceManifest
from packages.source_adapters.official_feeds import (
    OfficialCalendarSource,
    OfficialFeedSource,
    official_source_presets,
)
from packages.source_adapters.official_feeds.adapter import extract_document_text, parse_feed

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "sources"


def test_official_rss_fixture_preserves_publication_time_and_url() -> None:
    body = (FIXTURES / "fed-press.rss.xml").read_text()

    async def fetcher(_url: str) -> tuple[int, str]:
        return 200, body

    source = OfficialFeedSource(
        SourceManifest(
            source_id="fed-press",
            source_type="official_feed",
            version="fixture.v1",
            authority_level="official",
        ),
        "https://www.federalreserve.gov/feeds/press_all.xml",
        fetcher=fetcher,
    )
    result = asyncio.run(source.poll())
    assert len(result.envelopes) == 1
    envelope = result.envelopes[0]
    assert envelope.published_at is not None
    assert str(envelope.source_url).startswith("https://www.federalreserve.gov/")
    assert envelope.source_type.value == "official_feed"


def test_official_calendar_fixture_becomes_text_envelope() -> None:
    body = (FIXTURES / "bls-calendar.ics").read_bytes()

    async def fetcher(_url: str) -> tuple[int, bytes]:
        return 200, body

    source = OfficialCalendarSource(
        SourceManifest(
            source_id="bls-calendar",
            source_type="official_feed",
            version="fixture.v1",
            authority_level="official",
        ),
        "https://www.bls.gov/schedule/news_release/bls.ics",
        fetcher=fetcher,
    )
    result = asyncio.run(source.poll())
    assert result.envelopes[0].event_hint == "bls-cpi-2026-09"
    assert "Consumer Price Index" in result.envelopes[0].raw_text
    assert result.envelopes[0].observed_at <= result.envelopes[0].received_at


def test_feed_parser_supports_atom_href_json_and_document_body() -> None:
    atom = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>atom-1</id><title>Federal Reserve speech</title>
        <link href="https://www.federalreserve.gov/speeches/atom-1.htm" />
        <updated>2026-08-27T18:00:00Z</updated>
      </entry>
    </feed>"""
    json_feed = (
        '{"items":[{"id":"json-1","title":"BLS release",'
        '"published_at":"2026-08-27T18:00:00Z",'
        '"url":"https://www.bls.gov/news.release.htm"}]}'
    )
    body = (
        "<html><body><nav>navigation</nav><main><h1>Statement</h1>"
        "<p>Policy body</p></main><footer>footer</footer></body></html>"
    )

    atom_item = parse_feed(atom)[0]
    json_item = parse_feed(json_feed, "json")[0]

    assert str(atom_item.url) == "https://www.federalreserve.gov/speeches/atom-1.htm"
    assert json_item.item_id == "json-1"
    assert extract_document_text(body) == "Statement\nPolicy body"


def test_feed_parser_rejects_malformed_xml_and_json() -> None:
    with pytest.raises(ValueError, match="feed xml parse failed"):
        parse_feed("<rss><item>")
    with pytest.raises(ValueError):
        parse_feed("{bad-json", "json")


def test_feed_batch_cursor_does_not_skip_unemitted_items() -> None:
    body = """<rss><channel>
      <item><guid>one</guid><title>one</title><pubDate>2026-08-27T01:00:00Z</pubDate></item>
      <item><guid>two</guid><title>two</title><pubDate>2026-08-27T02:00:00Z</pubDate></item>
      <item><guid>three</guid><title>three</title><pubDate>2026-08-27T03:00:00Z</pubDate></item>
    </channel></rss>"""

    async def fetcher(_url: str) -> tuple[int, str]:
        return 200, body

    source = OfficialFeedSource(
        SourceManifest(
            source_id="fed-batched",
            source_type="official_feed",
            version="fixture.v1",
            authority_level="official",
            max_batch=2,
        ),
        "https://www.federalreserve.gov/fixture.rss",
        fetcher=fetcher,
    )
    first = asyncio.run(source.poll())
    second = asyncio.run(source.poll(first.cursor_after))

    assert [envelope.raw_text for envelope in first.envelopes] == ["one", "two"]
    assert [envelope.raw_text for envelope in second.envelopes] == ["three"]


def test_product_feed_bootstrap_only_establishes_cursor_then_emits_new_items() -> None:
    initial_body = """<rss><channel>
      <item><guid>one</guid><title>one</title><pubDate>2026-08-27T01:00:00Z</pubDate></item>
      <item><guid>two</guid><title>two</title><pubDate>2026-08-27T02:00:00Z</pubDate></item>
      <item><guid>three</guid><title>three</title><pubDate>2026-08-27T03:00:00Z</pubDate></item>
    </channel></rss>"""
    updated_body = """<rss><channel>
      <item><guid>one</guid><title>one</title><pubDate>2026-08-27T01:00:00Z</pubDate></item>
      <item><guid>two</guid><title>two</title><pubDate>2026-08-27T02:00:00Z</pubDate></item>
      <item><guid>three</guid><title>three</title><pubDate>2026-08-27T03:00:00Z</pubDate></item>
      <item><guid>four</guid><title>four</title><pubDate>2026-08-27T04:00:00Z</pubDate></item>
    </channel></rss>"""
    body = initial_body

    async def fetcher(_url: str) -> tuple[int, str]:
        return 200, body

    source = OfficialFeedSource(
        SourceManifest(
            source_id="fed-bounded-bootstrap",
            source_type="official_feed",
            version="fixture.v1",
            authority_level="official",
            max_batch=2,
        ),
        "https://www.federalreserve.gov/fixture.rss",
        fetcher=fetcher,
        bootstrap_latest=True,
    )

    bootstrap = asyncio.run(source.poll())
    no_change = asyncio.run(source.poll(bootstrap.cursor_after))
    body = updated_body
    new_items = asyncio.run(source.poll(bootstrap.cursor_after))

    assert bootstrap.envelopes == ()
    assert bootstrap.cursor_after is not None and bootstrap.cursor_after.endswith("|three")
    assert no_change.envelopes == ()
    assert [envelope.raw_text for envelope in new_items.envelopes] == ["four"]


def test_product_official_sources_can_exclude_unscheduled_calendar_activation() -> None:
    sources = official_source_presets(include_calendar=False)

    assert {source.manifest.source_id for source in sources} == {
        "fed-press",
        "fed-speeches",
        "bls-releases",
        "bea-news",
    }


def test_calendar_batch_cursor_does_not_skip_unemitted_events() -> None:
    body = b"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:one
DTSTART:20260827T010000Z
SUMMARY:One
END:VEVENT
BEGIN:VEVENT
UID:two
DTSTART:20260827T020000Z
SUMMARY:Two
END:VEVENT
BEGIN:VEVENT
UID:three
DTSTART:20260827T030000Z
SUMMARY:Three
END:VEVENT
END:VCALENDAR
"""

    async def fetcher(_url: str) -> tuple[int, bytes]:
        return 200, body

    source = OfficialCalendarSource(
        SourceManifest(
            source_id="calendar-batched",
            source_type="official_feed",
            version="fixture.v1",
            authority_level="official",
            max_batch=2,
        ),
        "https://www.bls.gov/schedule/fixture.ics",
        fetcher=fetcher,
    )
    first = asyncio.run(source.poll())
    second = asyncio.run(source.poll(first.cursor_after))

    assert [envelope.event_hint for envelope in first.envelopes] == ["one", "two"]
    assert [envelope.event_hint for envelope in second.envelopes] == ["three"]
