"""Read BEA release observations from existing native RSS/article capture originals."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, cast
from urllib.parse import urlsplit
from xml.etree import ElementTree

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    EventScenarioRealizationV1,
)
from packages.kernel.decision_hub_kernel.persistence.action_quote_repository import RawArchive
from packages.kernel.decision_hub_kernel.persistence.db import (
    RawCaptureRecord,
    SourcePollAttemptRecord,
    as_utc,
)
from packages.kernel.decision_hub_kernel.ports.action_quote import CaptureOriginal
from packages.kernel.decision_hub_kernel.ports.exact_decimal_text import number
from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc, canonical_hash
from packages.kernel.decision_hub_kernel.ports.pce_release import (
    MONTHS,
    PceRelease,
    parse_pce_release,
)
from packages.kernel.decision_hub_kernel.ports.sources import SourceContentSnapshot
from packages.source_adapters.official_feeds.adapter import FeedItem, parse_feed

ADAPTER = "bea_pce_release_document.v1"
RSS_URL = "https://apps.bea.gov/rss/rss.xml"
VERSION = "official-feed.v2"
METRICS = ("headline_mom", "core_mom", "headline_yoy", "core_yoy")
Body = Mapping[str, Any]


def _wire(value: datetime) -> str:
    return aware_utc(value).isoformat().replace("+00:00", "Z")


def _time(value: str) -> datetime:
    return aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _valid_url(value: str, period: str) -> bool:
    try:
        url = urlsplit(value)
        month = MONTHS[int(period[-2:]) - 1].casefold()
        return bool(
            not re.search(r"[\x00-\x20\x7f]", value)
            and url.scheme == "https" and url.hostname in {"www.bea.gov", "bea.gov"}
            and url.port in {None, 443} and not url.username and not url.password
            and not url.query and not url.fragment
            and re.fullmatch(
                r"/news/20\d{2}/personal-income-and-outlays-" + month + "-" + period[:4], url.path,
            )
        )
    except (ValueError, IndexError):
        return False


def _rule(plan: Body, originals: Body) -> Body:
    event = plan["event"]
    reference = event["observation_rule_ref"]
    candidate = originals.get(reference["ref"])
    if not isinstance(candidate, Mapping):
        raise ValueError("pce_release_rule_original_missing")
    rule = cast(Body, candidate)
    if canonical_hash(rule) != reference["hash"]:
        raise ValueError("pce_release_rule_original_missing")
    period = rule.get("reference_period", "")
    source_url = rule.get("source_url")
    if (not isinstance(period, str) or re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", period) is None
            or not isinstance(source_url, str) or not _valid_url(source_url, period)):
        raise ValueError("pce_release_rule_invalid")
    expected = [dict(metric_id=key, unit="percent", reference_period=period, stage_id="release")
                for key in METRICS]
    if (
        rule.get("schema_version") != "event-scenario-observation-rule.v1"
        or rule.get("adapter") != ADAPTER or rule.get("source_id") != "bea-news"
        or rule.get("adapter_version") != VERSION
        or any(rule.get(key) != event[key] for key in ("event_key", "family", "stage_id"))
        or event["family"] != "pce" or event["stage_id"] != "release"
        or rule.get("metrics") != expected
    ):
        raise ValueError("pce_release_rule_invalid")
    return rule


def _raw(
    row: RawCaptureRecord, attempt: SourcePollAttemptRecord, archive: RawArchive, cutoff: datetime,
) -> bytes:
    if row.body_ref is None or row.body_hash is None or attempt.completed_at is None:
        raise ValueError("pce_release_capture_original_missing")
    try:
        raw = archive.read(row.body_ref)
    except (KeyError, OSError) as error:
        raise ValueError("pce_release_capture_original_missing") from error
    if (
        row.context_id != "source:" + attempt.attempt_id or row.adapter_version != VERSION
        or row.status_code != 200 or row.error_code is not None
        or row.byte_count != len(raw) or row.body_hash != hashlib.sha256(raw).hexdigest()
        or row.body_ref != "raw://sha256/" + row.body_hash
        or not as_utc(attempt.started_at) <= as_utc(row.started_at) <= as_utc(row.completed_at)
        <= as_utc(row.recorded_at) <= as_utc(attempt.completed_at) <= cutoff
    ):
        raise ValueError("pce_release_capture_original_invalid")
    return raw


def _captures(
    session: Session, article: RawCaptureRecord,
) -> RawCaptureRecord | None:
    rows = session.scalars(select(RawCaptureRecord).where(
        RawCaptureRecord.context_id == article.context_id,
        RawCaptureRecord.url.in_([RSS_URL, article.url]),
    )).all()
    feeds = [row for row in rows if row.url == RSS_URL]
    articles = [row for row in rows if row.url == article.url]
    if len(feeds) > 1 or len(articles) > 1:
        raise ValueError("pce_release_capture_original_ambiguous")
    return feeds[0] if len(feeds) == 1 and len(articles) == 1 else None


def _declared_public(raw: bytes, url: str, title: str) -> datetime | None:
    matches: list[ElementTree.Element] = []
    for node in ElementTree.fromstring(raw).iter():
        if node.tag.rsplit("}", 1)[-1] not in {"item", "entry"}:
            continue
        links = [(child.text or child.get("href") or "").strip() for child in node
                 if child.tag.rsplit("}", 1)[-1] == "link"]
        if url in links:
            matches.append(node)
    if len(matches) > 1:
        raise ValueError("pce_release_publisher_item_ambiguous")
    if not matches:
        return None
    node = matches[0]
    links = [(child.text or child.get("href") or "").strip() for child in node
             if child.tag.rsplit("}", 1)[-1] == "link"]
    titles = [(child.text or "").strip() for child in node
              if child.tag.rsplit("}", 1)[-1] == "title"]
    if links != [url] or titles != [title] or not title:
        raise ValueError("pce_release_publisher_item_ambiguous")
    clocks = [(child.text or "").strip() for child in node
              if child.tag.rsplit("}", 1)[-1] in {"pubDate", "published"}]
    if len(clocks) > 1:
        raise ValueError("pce_release_publisher_clock_ambiguous")
    if not clocks:
        return None
    try:
        try:
            value = datetime.fromisoformat(clocks[0].replace("Z", "+00:00"))
        except ValueError:
            value = parsedate_to_datetime(clocks[0])
    except (ValueError, TypeError, OverflowError):
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    weekdays = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    if "," in clocks[0] and clocks[0].split(",", 1)[0] != weekdays[value.weekday()]:
        raise ValueError("pce_release_public_clock_invalid")
    return aware_utc(value)


def _item(
    feed: RawCaptureRecord, article: RawCaptureRecord, attempt: SourcePollAttemptRecord,
    raw: bytes, rule: Body,
) -> FeedItem | None:
    snapshot = SourceContentSnapshot.model_validate_json(attempt.content_snapshot_json or "{}")
    try:
        items = parse_feed(raw.decode("utf-8-sig"), format_hint="rss")
    except (UnicodeError, ValueError) as error:
        raise ValueError("pce_release_feed_original_invalid") from error
    if (
        snapshot.http_status != 200 or snapshot.parser_version != VERSION
        or snapshot.parse_status != "valid" or snapshot.content_hash != feed.body_hash
        or snapshot.parsed_count != len(items) or not 1 <= attempt.received_count <= len(items)
        or attempt.error_code is not None or as_utc(feed.completed_at) > as_utc(article.started_at)
    ):
        raise ValueError("pce_release_feed_original_invalid")
    selected = [item for item in items if item.url == rule["source_url"]]
    if len(selected) > 1:
        raise ValueError("pce_release_publisher_item_ambiguous")
    if not selected or selected[0].published_at is None:
        return None
    public = _declared_public(raw, rule["source_url"], selected[0].title)
    if public is None:
        return None
    if public != aware_utc(selected[0].published_at) or public > as_utc(feed.completed_at):
        raise ValueError("pce_release_public_clock_invalid")
    return selected[0]


def _article(raw: bytes, rule: Body, plan: Body, item: FeedItem) -> PceRelease | None:
    try:
        value = parse_pce_release(raw, source_url=rule["source_url"])
    except ValueError:
        return None
    month = MONTHS[int(value.reference_period[-2:]) - 1]
    title = f"Personal Income and Outlays, {month} {value.reference_period[:4]}"
    if (
        value.reference_period != rule["reference_period"]
        or value.released_at != _time(plan["event"]["scheduled_at"])
        or " ".join(item.title.split()) != title
    ):
        return None
    if item.published_at is None or value.released_at > aware_utc(item.published_at):
        raise ValueError("pce_release_public_clock_invalid")
    return value


def _capture_body(row: RawCaptureRecord) -> dict[str, Any]:
    # Same public wire value as the original repository reader, without a private import.
    return CaptureOriginal(
        row.capture_id, row.context_id, row.url, row.status_code, row.body_ref, row.body_hash,
        row.byte_count, row.adapter_version, row.error_code,
        as_utc(row.started_at), as_utc(row.completed_at), as_utc(row.recorded_at),
    ).payload()


def _result(
    plan: Body, rule: Body, value: PceRelease, item: FeedItem,
    feed: RawCaptureRecord, article: RawCaptureRecord, attempt: SourcePollAttemptRecord,
) -> dict[str, Any]:
    if item.published_at is None or attempt.completed_at is None:
        raise ValueError("pce_release_public_clock_missing")
    evidence = dict(
        adapter=ADAPTER, observation_rule_ref=plan["event"]["observation_rule_ref"],
        publication_clock_basis="official_article_embargo_and_rss_pubDate",
        observation_basis="first_retained_native_observation",
        global_first_release_status="not_established",
        publisher_release_number=value.release_number, reference_period=value.reference_period,
        publisher_item=item.model_dump(mode="json"),
        rss_capture=_capture_body(feed), article_capture=_capture_body(article),
        attempt=dict(
            attempt_id=attempt.attempt_id, source_id=attempt.source_id, status=attempt.status,
            trigger_type=attempt.trigger_type, started_at=_wire(as_utc(attempt.started_at)),
            completed_at=_wire(as_utc(attempt.completed_at)), received_count=attempt.received_count,
            cursor_before=attempt.cursor_before, cursor_after=attempt.cursor_after,
            content_snapshot_json=attempt.content_snapshot_json,
        ),
    )
    digest = canonical_hash(evidence)
    values = dict(
        headline_mom=number(value.headline_mom), core_mom=number(value.core_mom),
        headline_yoy=number(value.headline_yoy), core_yoy=number(value.core_yoy),
    )
    realization = dict(
        schema_version="event-scenario-realization.v1", event=plan["event"],
        released_at=_wire(value.released_at), public_at=_wire(item.published_at),
        received_at=_wire(as_utc(article.completed_at)),
        known_at=_wire(as_utc(attempt.completed_at)),
        metrics=[{**row, "value": values[row["metric_id"]]} for row in rule["metrics"]],
        source_ref=dict(ref="event-realization-source:" + digest, hash=digest),
    )
    EventScenarioRealizationV1.model_validate(realization)
    return dict(realization=realization, source_evidence=evidence)


def read_pce_event_scenario_actual_inputs_in_session(
    session: Session, *, plan: Body, originals: Body, cutoff_at: datetime, archive: RawArchive,
) -> dict[str, Any] | None:
    """Earliest retained matching observation, with conflicts left unknown."""
    rule = _rule(plan, originals)
    cutoff = aware_utc(cutoff_at)
    candidates = session.execute(select(RawCaptureRecord, SourcePollAttemptRecord).join(
        SourcePollAttemptRecord,
        RawCaptureRecord.context_id == "source:" + SourcePollAttemptRecord.attempt_id,
    ).where(
        RawCaptureRecord.url == rule["source_url"], SourcePollAttemptRecord.source_id == "bea-news",
        SourcePollAttemptRecord.status == "succeeded",
        SourcePollAttemptRecord.completed_at <= cutoff,
    ).order_by(SourcePollAttemptRecord.completed_at, RawCaptureRecord.capture_id))
    result: dict[str, Any] | None = None
    for article, attempt in candidates:
        feed = _captures(session, article)
        if feed is None:
            continue
        feed_raw = _raw(feed, attempt, archive, cutoff)
        article_raw = _raw(article, attempt, archive, cutoff)
        item = _item(feed, article, attempt, feed_raw, rule)
        if item is None:
            continue
        value = _article(article_raw, rule, plan, item)
        if value is None:
            continue
        current = _result(plan, rule, value, item, feed, article, attempt)
        if result is not None and (
            current["realization"]["metrics"] != result["realization"]["metrics"]
            or current["source_evidence"]["publisher_release_number"]
            != result["source_evidence"]["publisher_release_number"]
        ):
            raise ValueError("pce_release_observed_vintage_conflict")
        if result is None:
            result = current
    return result
