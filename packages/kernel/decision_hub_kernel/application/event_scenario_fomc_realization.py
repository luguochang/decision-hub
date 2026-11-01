"""Read explicit statement actuals from original Fed RSS and same-poll HTML captures."""

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
from packages.kernel.decision_hub_kernel.ports.sources import SourceContentSnapshot
from packages.source_adapters.official_feeds.adapter import parse_feed

from ..ports.fomc_source_rules import ADAPTER, RSS_URL, TITLE, fomc_statement_rule
from ..ports.fomc_statement import FomcStatement, parse_fomc_statement

Body = Mapping[str, Any]
VERSION = "official-feed.v2"


def _wire(value: datetime) -> str:
    return aware_utc(value).isoformat().replace("+00:00", "Z")


def _rule(plan: Body, originals: Body) -> Body:
    event = plan["event"]
    reference = event["observation_rule_ref"]
    value = originals.get(reference["ref"])
    if not isinstance(value, Mapping):
        raise ValueError("fomc_statement_rule_original_missing")
    rule = cast(Body, value)
    if canonical_hash(rule) != reference["hash"]:
        raise ValueError("fomc_statement_rule_original_missing")
    if (
        event["family"] != "fomc"
        or event["stage_id"] != "policy_statement"
        or rule != fomc_statement_rule(event)
    ):
        raise ValueError("fomc_statement_rule_invalid")
    return rule


def _raw(
    row: RawCaptureRecord, attempt: SourcePollAttemptRecord, archive: RawArchive, cutoff: datetime
) -> bytes:
    if row.body_ref is None or row.body_hash is None or attempt.completed_at is None:
        raise ValueError("fomc_statement_capture_missing")
    raw = archive.read(row.body_ref)
    if (
        row.context_id != "source:" + attempt.attempt_id
        or row.adapter_version != VERSION
        or row.status_code != 200
        or row.error_code is not None
        or row.byte_count != len(raw)
        or row.body_hash != hashlib.sha256(raw).hexdigest()
        or row.body_ref != "raw://sha256/" + row.body_hash
        or not as_utc(attempt.started_at)
        <= as_utc(row.started_at)
        <= as_utc(row.completed_at)
        <= as_utc(row.recorded_at)
        <= as_utc(attempt.completed_at)
        <= cutoff
    ):
        raise ValueError("fomc_statement_capture_invalid")
    return raw


def _url(value: str, rule: Body) -> bool:
    try:
        target = urlsplit(value)
        return bool(
            not re.search(r"[\x00-\x20\x7f]", value)
            and target.scheme == "https"
            and target.hostname in rule["selector"]["official_hosts"]
            and target.port in {None, 443}
            and not target.username
            and not target.password
            and not target.query
            and not target.fragment
            and re.fullmatch(
                r"/newsevents/pressreleases/monetary"
                + rule["reference_period"].replace("-", "")
                + r"[a-z]\.htm",
                target.path,
            )
        )
    except ValueError:
        return False


def _publisher(raw: bytes, rule: Body) -> tuple[str, datetime] | None:
    matches: list[ElementTree.Element] = []
    for node in ElementTree.fromstring(raw).iter():
        if node.tag.rsplit("}", 1)[-1] not in {"item", "entry"}:
            continue
        titles = [
            (child.text or "").strip() for child in node if child.tag.rsplit("}", 1)[-1] == "title"
        ]
        links = [
            (child.text or child.get("href") or "").strip()
            for child in node
            if child.tag.rsplit("}", 1)[-1] == "link"
        ]
        eligible = [url for url in links if _url(url, rule)]
        if not eligible:
            continue
        if titles != [TITLE] or links != eligible or len(links) != 1:
            raise ValueError("fomc_statement_publisher_item_invalid")
        matches.append(node)
    if len(matches) > 1:
        raise ValueError("fomc_statement_publisher_item_ambiguous")
    if not matches:
        return None
    node = matches[0]
    clocks = [
        (child.text or "").strip()
        for child in node
        if child.tag.rsplit("}", 1)[-1] in {"pubDate", "published"}
    ]
    if len(clocks) != 1:
        raise ValueError("fomc_statement_publisher_clock_invalid")
    public = _public_clock(clocks[0])
    link = next(child for child in node if child.tag.rsplit("}", 1)[-1] == "link")
    return (link.text or link.get("href") or "").strip(), public


def _public_clock(text: str) -> datetime:
    try:
        value = parsedate_to_datetime(text)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError("fomc_statement_publisher_clock_invalid") from error
    public = aware_utc(value)
    weekdays = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    if "," in text and text.split(",", 1)[0] != weekdays[value.weekday()]:
        raise ValueError("fomc_statement_publisher_clock_invalid")
    return public


def _capture(row: RawCaptureRecord) -> dict[str, Any]:
    return CaptureOriginal(
        row.capture_id,
        row.context_id,
        row.url,
        row.status_code,
        row.body_ref,
        row.body_hash,
        row.byte_count,
        row.adapter_version,
        row.error_code,
        as_utc(row.started_at),
        as_utc(row.completed_at),
        as_utc(row.recorded_at),
    ).payload()


def _result(
    plan: Body,
    rule: Body,
    value: FomcStatement,
    public: datetime,
    feed: RawCaptureRecord,
    article: RawCaptureRecord,
    attempt: SourcePollAttemptRecord,
) -> dict[str, Any]:
    if attempt.completed_at is None:
        raise ValueError("fomc_statement_parent_incomplete")
    evidence = dict(
        adapter=ADAPTER,
        observation_rule_ref=plan["event"]["observation_rule_ref"],
        observation_basis="first_retained_native_observation",
        global_first_release_status="not_established",
        publication_clock_basis="official_statement_date_releaseTime_and_rss_pubDate",
        selector=rule["selector"],
        statement_action=value.action,
        statement_text_hash=value.statement_text_hash,
        rate_change_basis="explicit_statement_action_and_size",
        publisher_item=dict(title=TITLE, url=article.url, published_at=_wire(public)),
        rss_capture=_capture(feed),
        article_capture=_capture(article),
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
    values = dict(
        target_range_lower=number(value.target_lower),
        target_range_upper=number(value.target_upper),
        rate_change=number(value.rate_change_bp),
    )
    realization = dict(
        schema_version="event-scenario-realization.v1",
        event=plan["event"],
        released_at=_wire(value.released_at),
        public_at=_wire(public),
        received_at=_wire(as_utc(article.completed_at)),
        known_at=_wire(as_utc(attempt.completed_at)),
        metrics=[{**metric, "value": values[metric["metric_id"]]} for metric in rule["metrics"]],
        source_ref=dict(ref="event-realization-source:" + digest, hash=digest),
    )
    EventScenarioRealizationV1.model_validate(realization)
    return dict(realization=realization, source_evidence=evidence)


def _attempt(
    session: Session,
    parent: SourcePollAttemptRecord,
    plan: Body,
    rule: Body,
    archive: RawArchive,
    cutoff: datetime,
) -> dict[str, Any] | None:
    captures = session.scalars(
        select(RawCaptureRecord).where(
            RawCaptureRecord.context_id == "source:" + parent.attempt_id,
        )
    ).all()
    feeds = [row for row in captures if row.url == RSS_URL]
    if len(feeds) != 1:
        raise ValueError("fomc_statement_feed_capture_missing_or_ambiguous")
    feed = feeds[0]
    raw = _raw(feed, parent, archive, cutoff)
    snapshot = SourceContentSnapshot.model_validate_json(parent.content_snapshot_json or "{}")
    items = parse_feed(raw.decode("utf-8-sig"), format_hint="rss")
    if (
        snapshot.http_status != 200
        or snapshot.parser_version != VERSION
        or snapshot.parse_status != "valid"
        or snapshot.content_hash != feed.body_hash
        or snapshot.parsed_count != len(items)
        or not 1 <= parent.received_count <= len(items)
        or parent.error_code is not None
    ):
        raise ValueError("fomc_statement_feed_invalid")
    selected = _publisher(raw, rule)
    if selected is None:
        return None
    url, public = selected
    articles = [row for row in captures if row.url == url]
    if len(articles) != 1:
        raise ValueError("fomc_statement_article_capture_missing_or_ambiguous")
    article = articles[0]
    value = parse_fomc_statement(_raw(article, parent, archive, cutoff), source_url=url)
    scheduled = aware_utc(datetime.fromisoformat(rule["scheduled_at"].replace("Z", "+00:00")))
    if as_utc(feed.completed_at) > as_utc(
        article.started_at
    ) or not value.released_at == scheduled <= public <= as_utc(feed.completed_at):
        raise ValueError("fomc_statement_public_clock_invalid")
    return _result(plan, rule, value, public, feed, article, parent)


def read_fomc_event_scenario_actual_inputs_in_session(
    session: Session,
    *,
    plan: Body,
    originals: Body,
    cutoff_at: datetime,
    archive: RawArchive,
) -> dict[str, Any] | None:
    rule = _rule(plan, originals)
    cutoff = aware_utc(cutoff_at)
    scheduled = aware_utc(datetime.fromisoformat(rule["scheduled_at"].replace("Z", "+00:00")))
    parents = session.scalars(
        select(SourcePollAttemptRecord)
        .where(
            SourcePollAttemptRecord.source_id == "fed-press",
            SourcePollAttemptRecord.status == "succeeded",
            SourcePollAttemptRecord.completed_at >= scheduled,
            SourcePollAttemptRecord.completed_at <= cutoff,
        )
        .order_by(SourcePollAttemptRecord.completed_at, SourcePollAttemptRecord.attempt_id)
    ).all()
    result: dict[str, Any] | None = None
    for parent in parents:
        current = _attempt(session, parent, plan, rule, archive, cutoff)
        if current is None:
            continue
        if result is not None and (
            current["realization"]["metrics"] != result["realization"]["metrics"]
            or current["source_evidence"]["statement_text_hash"]
            != result["source_evidence"]["statement_text_hash"]
        ):
            raise ValueError("fomc_statement_observed_vintage_conflict")
        if result is None:
            result = current
    return result
