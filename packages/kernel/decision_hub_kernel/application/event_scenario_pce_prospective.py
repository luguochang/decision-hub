"""Resolve frozen PCE selectors through same-poll native RSS/article originals."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import datetime
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
from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc, canonical_hash
from packages.kernel.decision_hub_kernel.ports.sources import SourceContentSnapshot
from packages.source_adapters.official_feeds.adapter import parse_feed

from ..ports.pce_release import MONTHS
from ..ports.pce_source_rules import ADAPTER, RSS_URL, pce_selector_rule
from .event_scenario_pce_realization import (
    VERSION,
    read_pce_event_scenario_actual_inputs_in_session,
)

Body = Mapping[str, Any]


def _rule(plan: Body, originals: Body) -> Body:
    event = plan["event"]
    reference = event["observation_rule_ref"]
    value = originals.get(reference["ref"])
    if not isinstance(value, Mapping):
        raise ValueError("pce_selector_rule_original_missing")
    rule = cast(Body, value)
    if canonical_hash(rule) != reference["hash"]:
        raise ValueError("pce_selector_rule_original_missing")
    period = rule.get("reference_period")
    if (
        not isinstance(period, str)
        or event["family"] != "pce"
        or event["stage_id"] != "release"
        or rule != pce_selector_rule(event, period)
    ):
        raise ValueError("pce_selector_rule_invalid")
    return rule


def _valid_url(value: str, rule: Body) -> bool:
    try:
        target = urlsplit(value)
        period = rule["reference_period"]
        month = MONTHS[int(period[-2:]) - 1].casefold()
        path = (
            "/news/"
            + rule["scheduled_at"][:4]
            + "/personal-income-and-outlays-"
            + month
            + "-"
            + period[:4]
        )
        return bool(
            not re.search(r"[\x00-\x20\x7f]", value)
            and target.scheme == "https"
            and target.hostname in rule["selector"]["official_hosts"]
            and target.port in {None, 443}
            and not target.username
            and not target.password
            and not target.query
            and not target.fragment
            and target.path == path
        )
    except ValueError:
        return False


def _selected_url(raw: bytes, rule: Body) -> str | None:
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as error:
        raise ValueError("pce_selector_feed_invalid") from error
    matches: list[str] = []
    for node in root.iter():
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
        paths = [url for url in links if _valid_url(url, rule)]
        if rule["selector"]["title"] not in titles and not paths:
            continue
        if titles != [rule["selector"]["title"]] or len(links) != 1 or paths != links:
            raise ValueError("pce_selector_item_invalid")
        matches.append(links[0])
    if len(matches) > 1:
        raise ValueError("pce_selector_item_ambiguous")
    return matches[0] if matches else None


def _raw(
    row: RawCaptureRecord,
    attempt: SourcePollAttemptRecord,
    archive: RawArchive,
    cutoff: datetime,
) -> bytes:
    if row.body_ref is None or row.body_hash is None or attempt.completed_at is None:
        raise ValueError("pce_selector_capture_missing")
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
        raise ValueError("pce_selector_capture_invalid")
    return raw


def _attempt_url(
    session: Session,
    attempt: SourcePollAttemptRecord,
    rule: Body,
    archive: RawArchive,
    cutoff: datetime,
) -> str | None:
    captures = session.scalars(
        select(RawCaptureRecord).where(
            RawCaptureRecord.context_id == "source:" + attempt.attempt_id,
        )
    ).all()
    feeds = [row for row in captures if row.url == RSS_URL]
    if len(feeds) != 1:
        raise ValueError("pce_selector_feed_capture_missing_or_ambiguous")
    feed = feeds[0]
    raw = _raw(feed, attempt, archive, cutoff)
    snapshot = SourceContentSnapshot.model_validate_json(attempt.content_snapshot_json or "{}")
    try:
        items = parse_feed(raw.decode("utf-8-sig"), format_hint="rss")
    except (ValueError, UnicodeError) as error:
        raise ValueError("pce_selector_feed_invalid") from error
    if (
        snapshot.http_status != 200
        or snapshot.parser_version != VERSION
        or snapshot.parse_status != "valid"
        or snapshot.content_hash != feed.body_hash
        or snapshot.parsed_count != len(items)
        or not 1 <= attempt.received_count <= len(items)
        or attempt.error_code is not None
    ):
        raise ValueError("pce_selector_feed_invalid")
    url = _selected_url(raw, rule)
    if url is None:
        return None
    articles = [row for row in captures if row.url == url]
    if len(articles) != 1:
        raise ValueError("pce_selector_article_capture_missing_or_ambiguous")
    _raw(articles[0], attempt, archive, cutoff)
    return url


def _resolved(
    session: Session,
    plan: Body,
    rule: Body,
    url: str,
    archive: RawArchive,
    cutoff: datetime,
) -> dict[str, Any] | None:
    resolved = {
        key: rule[key]
        for key in (
            "schema_version",
            "source_id",
            "adapter_version",
            "event_key",
            "family",
            "stage_id",
            "reference_period",
            "metrics",
        )
    }
    resolved.update(adapter="bea_pce_release_document.v1", source_url=url)
    reference = dict(
        ref="derived:pce-selector-resolution:" + canonical_hash(resolved),
        hash=canonical_hash(resolved),
    )
    derived_plan = dict(event={**plan["event"], "observation_rule_ref": reference})
    result = read_pce_event_scenario_actual_inputs_in_session(
        session,
        plan=derived_plan,
        originals={reference["ref"]: resolved},
        cutoff_at=cutoff,
        archive=archive,
    )
    if result is None:
        return None
    result["realization"]["event"] = plan["event"]
    evidence = result["source_evidence"]
    evidence.update(
        adapter=ADAPTER,
        observation_rule_ref=plan["event"]["observation_rule_ref"],
        frozen_selector=rule["selector"],
        resolved_article_url=url,
        resolution_basis="derived_from_same_poll_official_rss",
    )
    digest = canonical_hash(evidence)
    result["realization"]["source_ref"] = dict(
        ref="event-realization-source:" + digest, hash=digest
    )
    EventScenarioRealizationV1.model_validate(result["realization"])
    return result


def read_pce_prospective_actual_inputs_in_session(
    session: Session,
    *,
    plan: Body,
    originals: Body,
    cutoff_at: datetime,
    archive: RawArchive,
) -> dict[str, Any] | None:
    """Later captures cannot establish knowledge at an earlier report cutoff."""
    rule = _rule(plan, originals)
    cutoff = aware_utc(cutoff_at)
    attempts = session.scalars(
        select(SourcePollAttemptRecord)
        .where(
            SourcePollAttemptRecord.source_id == "bea-news",
            SourcePollAttemptRecord.status == "succeeded",
            SourcePollAttemptRecord.completed_at <= cutoff,
        )
        .order_by(SourcePollAttemptRecord.completed_at, SourcePollAttemptRecord.attempt_id)
    ).all()
    urls = {_attempt_url(session, attempt, rule, archive, cutoff) for attempt in attempts}
    result: dict[str, Any] | None = None
    for url in sorted(value for value in urls if value is not None):
        current = _resolved(session, plan, rule, url, archive, cutoff)
        if current is None:
            continue
        if result is not None and (
            current["realization"]["metrics"] != result["realization"]["metrics"]
            or current["source_evidence"]["publisher_release_number"]
            != result["source_evidence"]["publisher_release_number"]
        ):
            raise ValueError("pce_release_observed_vintage_conflict")
        if result is None or current["realization"]["known_at"] < result["realization"]["known_at"]:
            result = current
    return result
