"""Employment actuals from the existing selected RSS/document ingestion originals."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit
from xml.etree import ElementTree

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    EventScenarioRealizationV1,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from packages.source_adapters.official_feeds.adapter import (
    FeedItem,
    extract_document_text,
    parse_feed,
)

if TYPE_CHECKING:
    from ..persistence.action_quote_repository import RawArchive

from ..persistence.db import ObservationRecord, RawCaptureRecord, SourcePollAttemptRecord, as_utc
from ..ports.employment_release import EmploymentRelease, parse_employment_release
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.sources import SourceContentSnapshot

ADAPTER = "bls_employment_release_document.v1"
RSS_URL = "https://www.bls.gov/feed/bls_latest.rss"
METRICS = (
    ("nonfarm_payroll_change", "persons"),
    ("unemployment_rate", "percent"),
    ("average_hourly_earnings_mom", "percent"),
)


def _utc(value: datetime | str | None) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    result = as_utc(value)
    if result is None:
        raise ValueError("employment_release_source_clock_missing")
    return result


def _wire(value: datetime | str | None) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _rule(plan: dict[str, Any], originals: dict[str, Any]) -> dict[str, Any]:
    event = plan["event"]
    reference = event["observation_rule_ref"]
    rule = originals.get(reference["ref"])
    if not isinstance(rule, dict) or canonical_hash(rule) != reference["hash"]:
        raise ValueError("employment_release_rule_original_missing")
    period = rule.get("reference_period", "")
    expected = [
        dict(metric_id=name, unit=unit, reference_period=period, stage_id="release")
        for name, unit in METRICS
    ]
    if (
        rule.get("schema_version") != "event-scenario-observation-rule.v1"
        or rule.get("adapter") != ADAPTER
        or rule.get("source_id") != "bls-releases"
        or rule.get("adapter_version") != "official-feed.v2"
        or rule.get("vintage_policy") != "current_reference_month_first_estimate"
        or event["family"] != "nfp"
        or event["stage_id"] != "release"
        or any(rule.get(key) != event[key] for key in ("event_key", "family", "stage_id"))
        or not isinstance(period, str)
        or not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", period)
        or rule.get("metrics") != expected
    ):
        raise ValueError("employment_release_reviewed_rule_invalid")
    url = urlsplit(rule.get("source_url", ""))
    if (
        url.scheme != "https"
        or url.hostname not in {"www.bls.gov", "bls.gov"}
        or url.username
        or url.password
        or url.port not in {None, 443}
        or url.query
        or url.fragment
        or not re.fullmatch(r"/news\.release/archives/empsit_\d{8}\.htm", url.path)
    ):
        raise ValueError("employment_release_official_edition_url_required")
    return rule


def _raw(
    row: RawCaptureRecord,
    attempt: SourcePollAttemptRecord,
    archive: RawArchive | RawBodyArchive,
    cutoff: datetime,
) -> bytes:
    if row.body_ref is None:
        raise ValueError("employment_release_capture_original_invalid")
    raw = archive.read(row.body_ref)
    if (
        row.context_id != "source:" + attempt.attempt_id
        or row.adapter_version != "official-feed.v2"
        or row.status_code != 200
        or row.error_code is not None
        or row.byte_count != len(raw)
        or row.body_hash != hashlib.sha256(raw).hexdigest()
        or row.body_ref != "raw://sha256/" + cast(str, row.body_hash)
        or attempt.error_code is not None
        or not _utc(attempt.started_at)
        <= _utc(row.started_at)
        <= _utc(row.completed_at)
        <= _utc(row.recorded_at)
        <= _utc(attempt.completed_at)
        <= cutoff
    ):
        raise ValueError("employment_release_capture_original_invalid")
    return raw


def _item(
    raw: bytes,
    url: str,
    attempt: SourcePollAttemptRecord,
    feed: RawCaptureRecord,
    expected: datetime,
) -> FeedItem | None:
    items = parse_feed(raw.decode("utf-8-sig"))
    snapshot = SourceContentSnapshot.model_validate_json(attempt.content_snapshot_json or "{}")
    if (
        snapshot.http_status != 200
        or snapshot.parser_version != "official-feed.v2"
        or snapshot.parse_status != "valid"
        or snapshot.content_hash != feed.body_hash
        or snapshot.parsed_count != len(items)
        or attempt.received_count < 1
    ):
        raise ValueError("employment_release_feed_snapshot_invalid")
    chosen = [item for item in items if item.url == url]
    if len(chosen) != 1 or chosen[0].published_at is None:
        return None
    stamps = []
    for node in ElementTree.fromstring(raw).iter():
        if node.tag.rsplit("}", 1)[-1] != "item":
            continue
        links = [
            (child.text or "").strip() for child in node if child.tag.rsplit("}", 1)[-1] == "link"
        ]
        dates = [
            (child.text or "").strip()
            for child in node
            if child.tag.rsplit("}", 1)[-1] == "pubDate"
        ]
        if url in links:
            if links != [url] or len(dates) > 1:
                raise ValueError("employment_release_rss_item_ambiguous")
            stamps.extend(dates)
    if len(stamps) != 1 or not stamps[0]:
        return None
    stamp = parsedate_to_datetime(stamps[0])
    if (
        stamp.tzinfo is None
        or stamp.utcoffset() is None
        or stamps[0].split(",", 1)[0] != stamp.strftime("%a")
        or _utc(stamp) != expected
        or _utc(chosen[0].published_at) != expected
        or expected > _utc(feed.completed_at)
        or "employment situation" not in chosen[0].title.casefold()
    ):
        raise ValueError("employment_release_rss_publication_clock_or_family_invalid")
    return chosen[0]


def _observation(
    session: Session, item: FeedItem, raw: bytes, attempt: SourcePollAttemptRecord
) -> dict[str, Any]:
    text = (item.title + "\n" + extract_document_text(raw.decode("utf-8-sig"))).strip()
    digest = hashlib.sha256(text.encode()).hexdigest()
    rows = session.scalars(
        select(ObservationRecord).where(
            ObservationRecord.source_id == "bls-releases",
            ObservationRecord.source_url == item.url,
            ObservationRecord.content_hash == digest,
        )
    ).all()
    if (
        len(rows) != 1
        or rows[0].text != text
        or rows[0].source_type != "official_feed"
        or rows[0].event_hint != "bls-releases"
        or _utc(rows[0].published_at) != _utc(item.published_at)
        or _utc(rows[0].observed_at) != _utc(item.published_at)
        or not _utc(item.published_at) <= _utc(rows[0].received_at) <= _utc(attempt.completed_at)
    ):
        raise ValueError("employment_release_admitted_observation_invalid")
    row = rows[0]
    return dict(
        observation_id=row.observation_id,
        content_hash=digest,
        published_at=_wire(row.published_at),
        received_at=_wire(row.received_at),
    )


def _capture(row: RawCaptureRecord) -> dict[str, Any]:
    value = {
        name: getattr(row, name)
        for name in (
            "capture_id",
            "context_id",
            "url",
            "status_code",
            "body_ref",
            "body_hash",
            "byte_count",
            "adapter_version",
            "error_code",
        )
    }
    value.update(
        {name: _wire(getattr(row, name)) for name in ("started_at", "completed_at", "recorded_at")}
    )
    return value


def _candidate(
    session: Session,
    article: RawCaptureRecord,
    attempt: SourcePollAttemptRecord,
    rule: dict[str, Any],
    archive: RawArchive | RawBodyArchive,
    cutoff: datetime,
    release_at: datetime,
) -> tuple[EmploymentRelease, dict[str, Any]] | None:
    feeds = session.scalars(
        select(RawCaptureRecord).where(
            RawCaptureRecord.context_id == article.context_id, RawCaptureRecord.url == RSS_URL
        )
    ).all()
    if not feeds:
        return None
    if len(feeds) != 1:
        raise ValueError("employment_release_rss_capture_ambiguous")
    feed = feeds[0]
    rss, raw = _raw(feed, attempt, archive, cutoff), _raw(article, attempt, archive, cutoff)
    if _utc(feed.completed_at) > _utc(article.started_at):
        raise ValueError("employment_release_feed_article_sequence_invalid")
    item = _item(rss, rule["source_url"], attempt, feed, release_at)
    if item is None:
        return None
    edition = parse_employment_release(
        raw,
        source_url=rule["source_url"],
        expected_period=rule["reference_period"],
        expected_release_at=release_at,
    )
    evidence = dict(
        adapter=ADAPTER,
        vintage_policy=rule["vintage_policy"],
        publication_clock_basis="official_rss_pubDate_and_document_embargo",
        publisher_item=item.model_dump(mode="json"),
        rss_capture=_capture(feed),
        article_capture=_capture(article),
        observation=_observation(session, item, raw, attempt),
        edition={**asdict(edition), "released_at": _wire(edition.released_at)},
        attempt=dict(
            attempt_id=attempt.attempt_id,
            source_id=attempt.source_id,
            status=attempt.status,
            received_count=attempt.received_count,
            started_at=_wire(attempt.started_at),
            completed_at=_wire(attempt.completed_at),
            content_snapshot_json=attempt.content_snapshot_json,
        ),
    )
    return edition, evidence


def _result(
    plan: dict[str, Any],
    rule: dict[str, Any],
    edition: EmploymentRelease,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    evidence["observation_rule_ref"] = plan["event"]["observation_rule_ref"]
    values = dict(
        nonfarm_payroll_change=edition.payroll_change,
        unemployment_rate=edition.unemployment_rate,
        average_hourly_earnings_mom=edition.earnings_mom,
    )
    digest = canonical_hash(evidence)
    realization = dict(
        schema_version="event-scenario-realization.v1",
        event=plan["event"],
        released_at=_wire(edition.released_at),
        public_at=_wire(edition.released_at),
        received_at=evidence["article_capture"]["completed_at"],
        known_at=evidence["attempt"]["completed_at"],
        metrics=[{**row, "value": values[row["metric_id"]]} for row in rule["metrics"]],
        source_ref=dict(ref="event-realization-source:" + digest, hash=digest),
    )
    EventScenarioRealizationV1.model_validate(realization)
    return dict(realization=realization, source_evidence=evidence)


def read_employment_event_scenario_actual_inputs_in_session(
    session: Session,
    *,
    plan: dict[str, Any],
    originals: dict[str, Any],
    cutoff_at: datetime,
    archive: RawArchive | RawBodyArchive,
) -> dict[str, Any] | None:
    """Reparse admitted publisher bytes; earliest known edition never inherits later changes."""
    rule = _rule(plan, originals)
    cutoff, release_at = aware_utc(cutoff_at), _utc(plan["event"]["scheduled_at"])
    if cutoff < release_at:
        return None
    rows = session.execute(
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
            SourcePollAttemptRecord.completed_at >= release_at - timedelta(days=2),
        )
        .order_by(SourcePollAttemptRecord.completed_at, RawCaptureRecord.capture_id)
    )
    selected: tuple[EmploymentRelease, dict[str, Any]] | None = None
    attempts = set()
    for article, attempt in rows:
        if attempt.attempt_id in attempts:
            raise ValueError("employment_release_article_capture_ambiguous")
        attempts.add(attempt.attempt_id)
        value = _candidate(session, article, attempt, rule, archive, cutoff, release_at)
        if value is None:
            continue
        if selected is not None and value[0] != selected[0]:
            raise ValueError("employment_release_edition_conflict")
        if selected is None:
            selected = value
    if selected is None:
        return None
    return _result(plan, rule, selected[0], selected[1])


# Explicit reuse seams for prospective evidence, retaining the original validators.
read_employment_capture_original = _raw
read_employment_edition_original_in_session = _candidate
