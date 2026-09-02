from __future__ import annotations

from packages.kernel.decision_hub_kernel.ports.sources import SourceConnector, SourceManifest

from .adapter import Fetcher, OfficialFeedSource
from .calendar import CalendarFetcher, OfficialCalendarSource

OFFICIAL_SOURCE_CONFIG = {
    "fed-press": (
        "https://www.federalreserve.gov/feeds/press_all.xml",
        "official",
        "federalreserve.gov",
    ),
    "fed-speeches": (
        "https://www.federalreserve.gov/feeds/speeches.xml",
        "official",
        "federalreserve.gov",
    ),
    "bls-releases": (
        "https://www.bls.gov/feed/bls_latest.rss",
        "official",
        "bls.gov",
    ),
    "bea-news": ("https://www.bea.gov/news/rss.xml", "official", "bea.gov"),
}


def official_source_presets(
    fetcher: Fetcher | None = None,
    calendar_fetcher: CalendarFetcher | None = None,
    *,
    include_calendar: bool = True,
) -> list[SourceConnector]:
    sources: list[SourceConnector] = [
        OfficialFeedSource(
            SourceManifest(
                source_id=source_id,
                source_type="official_feed",
                version="official-feed.v1",
                capabilities=("poll", "rss", "revision"),
                authority_level=authority,
                poll_interval_seconds=60,
                max_batch=10,
                allowed_domains=(domain,),
            ),
            endpoint,
            fetcher=fetcher,
            include_document_body=True,
            document_fetcher=fetcher,
            bootstrap_latest=True,
        )
        for source_id, (endpoint, authority, domain) in OFFICIAL_SOURCE_CONFIG.items()
    ]
    if include_calendar:
        sources.append(
            OfficialCalendarSource(
                SourceManifest(
                    source_id="bls-calendar",
                    source_type="official_feed",
                    version="official-calendar.v1",
                    capabilities=("poll", "calendar", "revision"),
                    authority_level="official",
                    poll_interval_seconds=900,
                    max_batch=100,
                    allowed_domains=("bls.gov",),
                ),
                "https://www.bls.gov/schedule/news_release/bls.ics",
                fetcher=calendar_fetcher,
            )
        )
    return sources
