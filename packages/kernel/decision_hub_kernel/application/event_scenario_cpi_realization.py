"""CPI monthly release values from native RSS and article capture originals.

Uses the installed official-feed source. Publisher-declared publication time,
local receipt time and committed knowledge time remain different clocks.
Index retrievals and calendar times cannot manufacture a release observation.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from sqlalchemy import select

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    EventScenarioRealizationV1,
)
from packages.source_adapters.official_feeds.adapter import parse_feed

from ..persistence.action_quote_repository import _capture
from ..persistence.db import RawCaptureRecord, SourcePollAttemptRecord, as_utc
from ..ports.exact_decimal_text import number
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.sources import SourceContentSnapshot

ADAPTER = "bls_cpi_release_document.v1"
RSS_URL = "https://www.bls.gov/feed/bls_latest.rss"
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
METRICS = {"All items": "headline_mom", "All items less food and energy": "core_mom"}


def _wire(value):
    return aware_utc(value).isoformat().replace("+00:00", "Z")


def _time(value):
    return aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _text(node):
    return " ".join(node.get_text(" ", strip=True).split())


def _rule(plan, originals):
    event = plan["event"]
    reference = event["observation_rule_ref"]
    rule = originals.get(reference["ref"])
    if rule is None or canonical_hash(rule) != reference["hash"]:
        raise ValueError("cpi_release_rule_original_missing")
    url = urlsplit(rule.get("source_url", ""))
    period = rule.get("reference_period", "")
    expected = [
        dict(metric_id=key, unit="percent", reference_period=period, stage_id="release")
        for key in ("headline_mom", "core_mom")
    ]
    if (
        rule.get("schema_version") != "event-scenario-observation-rule.v1"
        or rule.get("adapter") != ADAPTER
        or rule.get("source_id") != "bls-releases"
        or rule.get("adapter_version") != "official-feed.v2"
        or any(rule.get(key) != event[key] for key in ("event_key", "family", "stage_id"))
        or event["family"] != "cpi"
        or event["stage_id"] != "release"
        or not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", period)
        or rule.get("metrics") != expected
        or url.scheme != "https"
        or url.hostname not in {"www.bls.gov", "bls.gov"}
        or url.port not in {None, 443}
        or url.username
        or url.password
        or url.query
        or url.fragment
        or not re.fullmatch(r"/news\.release/(?:archives/cpi_\d{8}\.htm|cpi\.htm)", url.path)
    ):
        raise ValueError("cpi_release_rule_invalid")
    return rule


def _article(raw, rule, event):
    soup = BeautifulSoup(raw, "html.parser")
    text = _text(soup)
    periods = set(re.findall(r"CONSUMER PRICE INDEX\s*[–—-]\s*([A-Z]+)\s+(20\d{2})", text))
    if len(periods) != 1:
        return None
    month, year = next(iter(periods))
    if month.title() not in MONTHS:
        return None
    period = f"{year}-{MONTHS.index(month.title()) + 1:02d}"
    if period != rule["reference_period"]:
        return None
    clocks = re.findall(
        r"Transmission of material in this release is embargoed until\s+"
        r"(\d{1,2}:\d{2})\s*([ap])\.m\.\s*\(ET\)\s*"
        r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s*"
        r"([A-Za-z]+ \d{1,2}, 20\d{2})",
        text,
    )
    if len(clocks) != 1:
        return None
    minute, ampm, weekday, day = clocks[0]
    date = datetime.strptime(day, "%B %d, %Y")
    if date.strftime("%A") != weekday:
        return None
    clock = datetime.strptime(minute + " " + ampm + "m", "%I:%M %p").time()
    declared = datetime.combine(date.date(), clock, ZoneInfo("America/New_York")).astimezone(UTC)
    if declared != _time(event["scheduled_at"]):
        return None
    tables = [
        table
        for table in soup.find_all("table")
        if table.caption is not None
        and _text(table.caption).startswith("Table A.")
        and "CPI-U" in _text(table.caption)
    ]
    if len(tables) != 1:
        return None
    table = tables[0]
    groups = [
        cell
        for cell in table.find_all("th")
        if cell.get("colspan") == "7"
        and "Seasonally adjusted changes from preceding month" in _text(cell)
    ]
    if len(groups) != 1:
        return None
    # Table A has seven monthly SA columns followed by one annual NSA column.
    # Check the last monthly label as well as the column group before indexing.
    head = table.find("thead")
    if head is None:
        return None
    first_header = head.find("tr")
    physical_groups = first_header.find_all(["th", "td"], recursive=False) if first_header else []
    if (
        len(physical_groups) != 3
        or physical_groups[1] is not groups[0]
        or physical_groups[0].get("colspan", "1") != "1"
        or physical_groups[2].get("colspan", "1") != "1"
        or "Unadjusted" not in _text(physical_groups[2])
        or "12" not in _text(physical_groups[2])
    ):
        return None
    label = re.compile(re.escape(month[:3].title()) + r"(?:t)?\.?\s+" + year)
    monthly_labels = [
        _text(cell)
        for cell in head.find_all(["th", "td"])
        if re.fullmatch(r"[A-Za-z]{3,4}\.?\s+20\d{2}", _text(cell))
    ]
    if len(monthly_labels) < 7 or not label.fullmatch(monthly_labels[6]):
        return None
    values = {}
    for row in table.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if not cells or _text(cells[0]) not in METRICS:
            continue
        key = METRICS[_text(cells[0])]
        data = cells[1:]
        if key in values or len(data) != 8 or not re.fullmatch(r"-?\d+(?:\.\d+)?", _text(data[6])):
            return None
        values[key] = number(Decimal(_text(data[6])))
    return values if set(values) == {"headline_mom", "core_mom"} else None


def _raw(capture, attempt, archive, cutoff):
    raw = archive.read(capture.body_ref)
    start, completed, recorded = (
        as_utc(value) for value in (capture.started_at, capture.completed_at, capture.recorded_at)
    )
    if (
        capture.context_id != "source:" + attempt.attempt_id
        or capture.adapter_version != "official-feed.v2"
        or capture.status_code != 200
        or capture.error_code is not None
        or capture.byte_count != len(raw)
        or capture.body_hash != hashlib.sha256(raw).hexdigest()
        or capture.body_ref != "raw://sha256/" + capture.body_hash
        or not as_utc(attempt.started_at)
        <= start
        <= completed
        <= recorded
        <= as_utc(attempt.completed_at)
        <= cutoff
    ):
        raise ValueError("cpi_release_capture_original_invalid")
    return raw


def read_cpi_event_scenario_actual_inputs_in_session(
    session, *, plan, originals, cutoff_at, archive
):
    """Reparse exact publisher records; no network, writes or synthetic clocks."""
    rule = _rule(plan, originals)
    cutoff = aware_utc(cutoff_at)
    # The installed RSS connector fetches an article only for a selected feed
    # item. A two-day event window avoids scanning unrelated retained history.
    candidates = session.execute(
        select(RawCaptureRecord, SourcePollAttemptRecord)
        .join(
            SourcePollAttemptRecord,
            RawCaptureRecord.context_id == "source:" + SourcePollAttemptRecord.attempt_id,
        )
        .where(
            RawCaptureRecord.url == rule["source_url"],
            SourcePollAttemptRecord.source_id == "bls-releases",
            SourcePollAttemptRecord.status == "succeeded",
            SourcePollAttemptRecord.completed_at <= cutoff,
            SourcePollAttemptRecord.completed_at
            >= _time(plan["event"]["scheduled_at"]) - timedelta(days=2),
        )
        .order_by(SourcePollAttemptRecord.completed_at, RawCaptureRecord.capture_id)
    )
    for article, attempt in candidates:
        feeds = session.scalars(
            select(RawCaptureRecord).where(
                RawCaptureRecord.context_id == article.context_id,
                RawCaptureRecord.url == RSS_URL,
                RawCaptureRecord.adapter_version == "official-feed.v2",
            )
        ).all()
        if len(feeds) != 1 or attempt.received_count < 1:
            continue
        feed = feeds[0]
        feed_raw = _raw(feed, attempt, archive, cutoff)
        article_raw = _raw(article, attempt, archive, cutoff)
        snapshot = SourceContentSnapshot.model_validate_json(attempt.content_snapshot_json or "{}")
        if (
            snapshot.http_status != 200
            or snapshot.parser_version != "official-feed.v2"
            or snapshot.parse_status != "valid"
            or snapshot.content_hash != feed.body_hash
            or snapshot.parsed_count is None
            or snapshot.parsed_count < 1
            or as_utc(feed.completed_at) > as_utc(article.started_at)
        ):
            raise ValueError("cpi_release_feed_original_invalid")
        items = [
            item
            for item in parse_feed(feed_raw.decode("utf-8-sig"))
            if item.url == rule["source_url"]
        ]
        if len(items) != 1 or items[0].published_at is None:
            continue
        public = aware_utc(items[0].published_at)
        if public > as_utc(feed.completed_at):
            raise ValueError("cpi_release_public_clock_invalid")
        values = _article(article_raw, rule, plan["event"])
        if values is None:
            continue
        # Publisher time is not our first-known time. Even a retrospective
        # archive/feed observation keeps its actual current received/known_at.
        evidence = dict(
            adapter=ADAPTER,
            observation_rule_ref=plan["event"]["observation_rule_ref"],
            publication_clock_basis="official_rss_pubDate",
            publisher_item=items[0].model_dump(mode="json"),
            rss_capture=_capture(feed).payload(),
            article_capture=_capture(article).payload(),
            attempt=dict(
                attempt_id=attempt.attempt_id,
                source_id=attempt.source_id,
                status=attempt.status,
                started_at=_wire(as_utc(attempt.started_at)),
                completed_at=_wire(as_utc(attempt.completed_at)),
                received_count=attempt.received_count,
                content_snapshot_json=attempt.content_snapshot_json,
            ),
        )
        digest = canonical_hash(evidence)
        realization = dict(
            schema_version="event-scenario-realization.v1",
            event=plan["event"],
            released_at=_wire(public),
            public_at=_wire(public),
            received_at=_wire(as_utc(article.completed_at)),
            known_at=_wire(as_utc(attempt.completed_at)),
            metrics=[{**row, "value": values[row["metric_id"]]} for row in rule["metrics"]],
            source_ref=dict(ref="event-realization-source:" + digest, hash=digest),
        )
        EventScenarioRealizationV1.model_validate(realization)
        return dict(realization=realization, source_evidence=evidence)
    return None
