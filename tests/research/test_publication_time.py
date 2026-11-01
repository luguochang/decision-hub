from datetime import UTC, datetime, timedelta

import pytest

from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.provider_adapters.research.publication_time import html_publication_time
from tests.research.test_durable_research_gateway import NOW, _candidate, _database, _requirement

URL = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm"


@pytest.mark.parametrize("zone,hour", [("EDT", 18), ("EST", 19)])
def test_fed_release_uses_article_day_and_explicit_release_timezone(zone, hour):
    html = ('<p class="article__time">September 16, 2026</p>'
            f'<p>For release at 2:00 p.m. {zone}</p><footer>Updated September 30</footer>')
    assert html_publication_time(html, URL) == datetime(2026, 9, 16, hour, tzinfo=UTC)
    assert html_publication_time(html, "https://untrusted.example/pressrelease") is None


@pytest.mark.parametrize("html", [
    '<meta property="article:published_time" content="2026-09-16">',
    '<meta property="article:published_time" content="2026-09-16T14:00:00">',
    '<meta property="article:modified_time" content="2026-09-30T14:00:00Z">',
    '<time datetime="2026-09-16T14:00:00Z">Last modified</time>',
    '<p class="article__time">September 16, 2026</p>',
    '<meta property="article:published_time" content="2026-09-16T14:00:00Z">'
    '<meta itemprop="datePublished" content="2026-09-17T14:00:00Z">',
])
def test_uncertain_or_modified_times_do_not_invent_publication(html):
    assert html_publication_time(html, URL) is None


def test_explicit_offset_publication_metadata():
    assert html_publication_time(
        '<meta property="article:published_time" content="2026-09-16T14:00:00-04:00">',
        "https://example.org/article",
    ) == datetime(2026, 9, 16, 18, tzinfo=UTC)


@pytest.mark.parametrize("published,status,quality", [
    (None, "unknown", "candidate"),
    (NOW - timedelta(days=14), "stale", "stale"),
    (NOW - timedelta(minutes=2), "fresh", "accepted"),
])
def test_refetch_never_makes_undated_or_old_documents_fresh(tmp_path, published, status, quality):
    db, run_id, session_id = _database(tmp_path)
    candidates = ResearchEvidenceService(db).accept_candidates(
        run_id=run_id, capability_id="official.macro",
        candidates=[_candidate(session_id, published_at=published)],
        requirements={"event_identity": _requirement()}, cutoff_at=NOW + timedelta(seconds=2),
    )
    assert candidates[0].freshness_status == status
    assert candidates[0].quality == quality
