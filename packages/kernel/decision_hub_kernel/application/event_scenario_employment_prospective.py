"""Resolve a frozen NFP selector through same-poll RSS then the reviewed exact reader."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    EventScenarioRealizationV1,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive

if TYPE_CHECKING:
    from ..persistence.action_quote_repository import RawArchive

from ..persistence.db import RawCaptureRecord, SourcePollAttemptRecord
from ..ports.employment_source_rules import ADAPTER, RSS_URL, employment_selector_rule
from ..ports.market_path import aware_utc, canonical_hash
from .event_scenario_employment_realization import (
    read_employment_capture_original as _raw,
)
from .event_scenario_employment_realization import (
    read_employment_event_scenario_actual_inputs_in_session,
)

Body = Mapping[str, Any]


def _rule(plan: Body, originals: Body) -> Body:
    reference = plan["event"]["observation_rule_ref"]
    rule = originals.get(reference["ref"])
    if not isinstance(rule, dict) or canonical_hash(rule) != reference["hash"]:
        raise ValueError("employment_selector_rule_original_missing")
    period, evidence = rule.get("reference_period"), rule.get("period_evidence_ref")
    if (
        not isinstance(period, str)
        or not isinstance(evidence, dict)
        or rule != employment_selector_rule(plan["event"], period, evidence)
    ):
        raise ValueError("employment_selector_rule_invalid")
    return rule


def _valid_url(value: str, rule: Body) -> bool:
    try:
        target = urlsplit(value)
        path = re.fullmatch(r"/news\.release/archives/empsit_(\d{8})\.htm", target.path)
        expected = datetime.fromisoformat(rule["scheduled_at"].replace("Z", "+00:00"))
        return bool(
            not re.search(r"[\x00-\x20\x7f]", value)
            and target.scheme == "https"
            and target.hostname in rule["selector"]["official_hosts"]
            and target.port in {None, 443}
            and not target.username
            and not target.password
            and not target.query
            and not target.fragment
            and path is not None
            and path[1] == expected.astimezone(ZoneInfo("America/New_York")).strftime("%m%d%Y")
        )
    except ValueError:
        return False


def _selected_url(raw: bytes, rule: Body) -> str | None:
    root = ElementTree.fromstring(raw)
    selected: list[str] = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "item":
            continue
        titles = [(c.text or "").strip() for c in node if c.tag.rsplit("}", 1)[-1] == "title"]
        links = [(c.text or "").strip() for c in node if c.tag.rsplit("}", 1)[-1] == "link"]
        paths = [url for url in links if _valid_url(url, rule)]
        if not paths:
            continue
        if (
            len(titles) != 1
            or not titles[0].startswith(rule["selector"]["title_family"])
            or len(links) != 1
            or paths != links
        ):
            raise ValueError("employment_selector_item_invalid")
        selected.append(links[0])
    if len(selected) > 1:
        raise ValueError("employment_selector_item_ambiguous")
    return selected[0] if selected else None


def _attempt_url(
    session: Session,
    attempt: SourcePollAttemptRecord,
    rule: Body,
    archive: RawArchive | RawBodyArchive,
    cutoff: datetime,
) -> str | None:
    captures = session.scalars(
        select(RawCaptureRecord).where(
            RawCaptureRecord.context_id == "source:" + attempt.attempt_id
        )
    ).all()
    feeds = [row for row in captures if row.url == RSS_URL]
    if len(feeds) != 1:
        raise ValueError("employment_selector_feed_capture_missing_or_ambiguous")
    raw = _raw(feeds[0], attempt, archive, cutoff)
    url = _selected_url(raw, rule)
    if url is None:
        return None
    articles = [row for row in captures if row.url == url]
    if len(articles) != 1:
        raise ValueError("employment_selector_article_capture_missing_or_ambiguous")
    _raw(articles[0], attempt, archive, cutoff)
    return url


def _resolved(
    session: Session,
    plan: Body,
    rule: Body,
    url: str,
    archive: RawArchive | RawBodyArchive,
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
            "vintage_policy",
            "metrics",
        )
    }
    resolved.update(adapter="bls_employment_release_document.v1", source_url=url)
    digest = canonical_hash(resolved)
    reference = dict(ref="derived:nfp-selector-resolution:" + digest, hash=digest)
    result = read_employment_event_scenario_actual_inputs_in_session(
        session,
        plan=dict(event={**plan["event"], "observation_rule_ref": reference}),
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
        period_evidence_ref=rule["period_evidence_ref"],
        resolved_article_url=url,
        resolution_basis="derived_from_same_poll_official_rss",
    )
    digest = canonical_hash(evidence)
    result["realization"]["source_ref"] = dict(
        ref="event-realization-source:" + digest, hash=digest
    )
    EventScenarioRealizationV1.model_validate(result["realization"])
    return result


def read_employment_prospective_actual_inputs_in_session(
    session: Session,
    *,
    plan: Body,
    originals: Body,
    cutoff_at: datetime,
    archive: RawArchive | RawBodyArchive,
) -> dict[str, Any] | None:
    rule = _rule(plan, originals)
    cutoff = aware_utc(cutoff_at)
    if cutoff < datetime.fromisoformat(rule["scheduled_at"].replace("Z", "+00:00")):
        return None
    attempts = session.scalars(
        select(SourcePollAttemptRecord)
        .where(
            SourcePollAttemptRecord.source_id == "bls-releases",
            SourcePollAttemptRecord.status == "succeeded",
            SourcePollAttemptRecord.completed_at <= cutoff,
        )
        .order_by(SourcePollAttemptRecord.completed_at, SourcePollAttemptRecord.attempt_id)
    ).all()
    urls = {_attempt_url(session, attempt, rule, archive, cutoff) for attempt in attempts}
    selected: dict[str, Any] | None = None
    for url in sorted(value for value in urls if value is not None):
        current = _resolved(session, plan, rule, url, archive, cutoff)
        if current is None:
            continue
        if selected is not None and (
            current["source_evidence"]["edition"] != selected["source_evidence"]["edition"]
        ):
            raise ValueError("employment_release_edition_conflict")
        if (
            selected is None
            or current["realization"]["known_at"] < selected["realization"]["known_at"]
        ):
            selected = current
    return selected
