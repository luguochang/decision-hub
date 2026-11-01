from __future__ import annotations

from packages.kernel.decision_hub_kernel.ports.sources import SourceConnector, SourceManifest

from .adapter import Fetcher, OfficialFeedSource
from .calendar import CalendarFetcher, OfficialCalendarSource
from .bea_release import BEA_SCHEDULE_URL, BEA_SCHEDULE_VERSION, BeaReleaseScheduleSource
from .bls_cpi import BlsPublicApiSource
from .cpi_nowcast import ClevelandCpiNowcastSource
from .cpi_schedule import CpiPublicationScheduleSource
from .cpi_nowcast_history import ClevelandCpiHistorySource
from .cpi_release_history import CpiHistoricalReleaseSource
from .macro_daily import FredDailyBackgroundSource
from packages.kernel.decision_hub_kernel.ports.macro_daily import SERIES as MACRO_DAILY_SERIES, VERSION as MACRO_DAILY_VERSION, source_id as macro_daily_source_id
from .html_calendar import HtmlCalendarFetcher, OfficialHtmlCalendarSource
from .fomc_policy import (
    FomcPolicySource,
    SOURCE_ID as FOMC_POLICY_ID,
    VERSION as FOMC_POLICY_VERSION,
)

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
    # BEA moved its release feed under apps.bea.gov.  The former
    # www.bea.gov/news/rss.xml endpoint returns 404 and would permanently
    # poison Operations health even though the official feed is available.
    "bea-news": ("https://apps.bea.gov/rss/rss.xml", "official", "bea.gov"),
}


def official_source_presets(
    fetcher: Fetcher | None = None,
    calendar_fetcher: CalendarFetcher | None = None,
    html_calendar_fetcher: HtmlCalendarFetcher | None = None,
    *,
    include_calendar: bool = True,
) -> list[SourceConnector]:
    sources: list[SourceConnector] = [
        OfficialFeedSource(
            SourceManifest(
                source_id=source_id,
                source_type="official_feed",
                version="official-feed.v2",
                capabilities=("poll", "rss", "revision", "catchup"),
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
    sources.append(BlsPublicApiSource(SourceManifest(
        source_id="bls-public-api", source_type="official_feed", version="bls-cpi-index-derived.v1",
        capabilities=("poll", "structured_actual", "revision"), authority_level="official",
        poll_interval_seconds=21_600, max_batch=4, allowed_domains=("api.bls.gov",),
    )))
    sources.append(ClevelandCpiNowcastSource(SourceManifest(
        source_id="cleveland-cpi-nowcast", source_type="official_feed", version="cleveland-cpi-nowcast.v1",
        capabilities=("poll", "structured_nowcast", "revision"), authority_level="official",
        poll_interval_seconds=21_600, max_batch=8, allowed_domains=("clevelandfed.org",),
    )))
    sources.append(CpiPublicationScheduleSource(SourceManifest(
        source_id="cpi-publication-schedule", source_type="official_feed", version="cpi-publication-schedule.v1",
        capabilities=("poll", "calendar", "structured_period", "revision"), authority_level="official",
        poll_interval_seconds=21_600, max_batch=12, allowed_domains=("fred.stlouisfed.org", "dol.gov"),
    )))
    sources.append(ClevelandCpiHistorySource(SourceManifest(
        source_id="cleveland-cpi-nowcast-history", source_type="official_feed", version="cpi-nowcast-history.v1",
        capabilities=("poll", "historical_model_training", "revision"), authority_level="official",
        poll_interval_seconds=86_400, max_batch=1, allowed_domains=("clevelandfed.org",),
    )))
    sources.append(CpiHistoricalReleaseSource(SourceManifest(
        source_id="cpi-release-history", source_type="official_feed", version="cpi-release-history.v1",
        capabilities=("poll", "structured_release_history"), authority_level="official",
        poll_interval_seconds=604800, max_batch=2, allowed_domains=("dol.gov",),
    )))
    for series_id in MACRO_DAILY_SERIES:
        sources.append(FredDailyBackgroundSource(SourceManifest(
            source_id=macro_daily_source_id(series_id), source_type="official_feed", version=MACRO_DAILY_VERSION,
            capabilities=("poll", "daily_macro_background", "revision"), authority_level="official",
            poll_interval_seconds=21_600, max_batch=1, allowed_domains=("fred.stlouisfed.org",),
        ), series_id=series_id))
    if include_calendar:
        sources.append(FomcPolicySource(SourceManifest(
            source_id=FOMC_POLICY_ID, source_type="official_feed", version=FOMC_POLICY_VERSION,
            capabilities=("poll", "policy_rule", "revision"), authority_level="official",
            poll_interval_seconds=21_600, max_batch=1, allowed_domains=("federalreserve.gov",),
        )))
        sources.append(
            BeaReleaseScheduleSource(
                SourceManifest(
                    source_id="bea-calendar",
                    source_type="official_feed",
                    version=BEA_SCHEDULE_VERSION,
                    capabilities=("poll", "calendar", "revision"),
                    authority_level="official",
                    poll_interval_seconds=21_600,
                    max_batch=100,
                    allowed_domains=("bea.gov",),
                ),
                BEA_SCHEDULE_URL,
                fetcher=calendar_fetcher,
                source_timezone="America/New_York",
            )
        )
        sources.append(
            OfficialCalendarSource(
                SourceManifest(
                    source_id="bls-calendar",
                    source_type="official_feed",
                    version="official-calendar.v2",
                    capabilities=("poll", "calendar", "revision"),
                    authority_level="official",
                    poll_interval_seconds=900,
                    max_batch=100,
                    allowed_domains=("bls.gov",),
                ),
                "https://www.bls.gov/schedule/news_release/bls.ics",
                fetcher=calendar_fetcher,
                source_timezone="America/New_York",
            )
        )
        for source_id, endpoint, domain, family, title in (
            (
                "fed-calendar",
                "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
                "federalreserve.gov",
                "fomc",
                "Federal Open Market Committee meeting",
            ),
            (
                "ecb-calendar",
                "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html",
                "ecb.europa.eu",
                "ecb_policy",
                "ECB Governing Council monetary policy meeting",
            ),
            (
                "boj-calendar",
                "https://www.boj.or.jp/en/mopo/mpmsche_minu/",
                "boj.or.jp",
                "boj_mpm",
                "Bank of Japan Monetary Policy Meeting",
            ),
        ):
            sources.append(
                OfficialHtmlCalendarSource(
                    SourceManifest(
                        source_id=source_id,
                        source_type="official_feed",
                        version=(
                            "official-html-calendar.v2.1"
                            if source_id == "ecb-calendar"
                            else "official-html-calendar.v2"
                        ),
                        capabilities=(
                            "poll",
                            "calendar",
                            "revision",
                            "http",
                        ),
                        authority_level="official",
                        poll_interval_seconds=21_600,
                        max_batch=100,
                        allowed_domains=(domain,),
                    ),
                    endpoint,
                    family=family,
                    event_title=title,
                    source_timezone={
                        "fed-calendar": "America/New_York",
                        "ecb-calendar": "Europe/Berlin",
                        "boj-calendar": "Asia/Tokyo",
                    }[source_id],
                    fetcher=html_calendar_fetcher,
                )
            )
    return sources
