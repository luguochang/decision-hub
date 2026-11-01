"""Prospective PCE rules from explicit authenticated BEA calendar title text."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc, canonical_hash

from .pce_release import MONTHS

ADAPTER = "bea_pce_release_selector.v1"
RSS_URL = "https://apps.bea.gov/rss/rss.xml"
CALENDAR_URL = "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics"
Body = Mapping[str, Any]


def pce_selector_rule(event: Body, period: str) -> dict[str, Any]:
    """Freeze a selection policy, never a prediction of an unseen article URL."""
    if re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", period) is None:
        raise ValueError("pce_selector_period_invalid")
    scheduled = aware_utc(datetime.fromisoformat(event["scheduled_at"].replace("Z", "+00:00")))
    month = MONTHS[int(period[-2:]) - 1]
    return dict(
        schema_version="event-scenario-observation-rule.v1",
        adapter=ADAPTER,
        source_id="bea-news",
        adapter_version="official-feed.v2",
        feed_url=RSS_URL,
        event_key=event["event_key"],
        family="pce",
        stage_id="release",
        reference_period=period,
        scheduled_at=scheduled.isoformat().replace("+00:00", "Z"),
        selector=dict(
            title=f"Personal Income and Outlays, {month} {period[:4]}",
            official_hosts=["www.bea.gov", "bea.gov"],
            path_policy="bea_pio_reference_month_and_release_year.v1",
        ),
        metrics=[
            dict(metric_id=key, unit="percent", reference_period=period, stage_id="release")
            for key in ("headline_mom", "core_mom", "headline_yoy", "core_yoy")
        ],
    )


def pce_source_rule(entry: Body) -> dict[str, Any] | None:
    """The agenda reader already authenticated capture, admission and vintage bindings."""
    original = json.loads(entry["original_json"])
    parsed = original.get("parsed", {})
    text = parsed.get("raw_text", "")
    payload = parsed.get("macro_event", {})
    match = re.match(r"Personal Income and Outlays, ([A-Za-z]+) (20\d{2}) \(scheduled: ", text)
    if (
        entry["source_id"] != "bea-calendar"
        or entry["family"] != "pce"
        or entry["stage_id"] != "release"
        or entry["time_precision"] != "exact"
        or original.get("source_id") != "bea-calendar"
        or parsed.get("source_id") != "bea-calendar"
        or parsed.get("source_url") != CALENDAR_URL
        or payload.get("family") != "pce"
        or payload.get("time_precision") != "exact"
        or payload.get("status") != "scheduled"
        or payload.get("external_event_id") != entry["event_key"]
        or payload.get("scheduled_at") != entry["scheduled_at"]
        or hashlib.sha256(text.encode()).hexdigest() != entry["source_ref"]["hash"]
        or match is None
        or match[1] not in MONTHS
    ):
        return None
    period = f"{match[2]}-{MONTHS.index(match[1]) + 1:02d}"
    rule = pce_selector_rule(entry, period)
    digest = canonical_hash(rule)
    return dict(
        schedule_ref=entry["source_ref"],
        observation_rule_ref=dict(ref="event-source-rule:" + digest, hash=digest),
        rule=rule,
    )


def declared_pce_source_rule(
    event: Body, supported: list[Body], entries: list[Body]
) -> dict[str, Any]:
    """Only the exact server selector derived from this agenda can enter the manifest."""
    reference = event["observation_rule_ref"]
    selected = [item for item in supported if item["observation_rule_ref"] == reference]
    matches = [
        entry
        for entry in entries
        if (
            entry["event_key"] == event["event_key"]
            and entry["family"] == event["family"]
            and entry["stage_id"] == event["stage_id"]
            and entry["scheduled_at"] is not None
            and aware_utc(datetime.fromisoformat(entry["scheduled_at"].replace("Z", "+00:00")))
            == aware_utc(datetime.fromisoformat(event["scheduled_at"].replace("Z", "+00:00")))
        )
    ]
    if len(selected) != 1 or len(matches) != 1:
        raise ValueError("event_native_pce_source_rule_pending")
    expected = pce_source_rule(matches[0])
    if expected is None or selected[0] != expected:
        raise ValueError("event_native_pce_source_rule_pending")
    rule = expected["rule"]
    if rule != pce_selector_rule(event, rule["reference_period"]):
        raise ValueError("event_native_pce_source_rule_pending")
    return dict(
        source_ref=reference,
        role="observation_rule",
        body_json=json.dumps(
            rule,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ),
    )
