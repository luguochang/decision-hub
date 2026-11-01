"""Frozen statement selectors from authenticated expected FOMC stage originals."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc, canonical_hash

ADAPTER = "fed_fomc_statement_selector.v1"
RSS_URL = "https://www.federalreserve.gov/feeds/press_all.xml"
TITLE = "Federal Reserve issues FOMC statement"
Body = Mapping[str, Any]


def fomc_statement_rule(event: Body) -> dict[str, Any]:
    scheduled = aware_utc(datetime.fromisoformat(event["scheduled_at"].replace("Z", "+00:00")))
    period = scheduled.date().isoformat()
    return dict(
        schema_version="event-scenario-observation-rule.v1",
        adapter=ADAPTER,
        source_id="fed-press",
        adapter_version="official-feed.v2",
        feed_url=RSS_URL,
        event_key=event["event_key"],
        family="fomc",
        stage_id="policy_statement",
        reference_period=period,
        scheduled_at=scheduled.isoformat().replace("+00:00", "Z"),
        selector=dict(
            title=TITLE,
            official_hosts=["www.federalreserve.gov", "federalreserve.gov"],
            path_policy="fed_fomc_statement_date.v1",
        ),
        metrics=[
            dict(metric_id=metric, unit=unit, reference_period=period, stage_id="policy_statement")
            for metric, unit in (
                ("target_range_lower", "percent"),
                ("target_range_upper", "percent"),
                ("rate_change", "basis_point"),
            )
        ],
    )


def fomc_source_rule(entry: Body) -> dict[str, Any] | None:
    original = json.loads(entry["original_json"])
    schedule = original.get("schedule", {})
    if (
        entry["source_id"] != "fed-calendar"
        or entry["family"] != "fomc"
        or entry["stage_id"] != "policy_statement"
        or entry["time_precision"] != "exact"
        or original.get("kind") != "fomc_derived_expected_stage_original"
        or canonical_hash(original) != entry["source_ref"]["hash"]
        or schedule.get("status") != "derived_schedule"
        or schedule.get("meeting_date") != entry["scheduled_date"]
    ):
        return None
    stages = [
        stage for stage in schedule.get("stages", []) if stage.get("stage_id") == entry["stage_id"]
    ]
    if len(stages) != 1:
        return None
    stage = stages[0]
    if (
        stage.get("basis") != "official_date_and_policy_rule"
        or stage.get("publication_status") != "expected"
        or stage.get("source_timezone") != "America/New_York"
        or aware_utc(datetime.fromisoformat(stage["scheduled_at"]))
        != aware_utc(datetime.fromisoformat(entry["scheduled_at"].replace("Z", "+00:00")))
    ):
        return None
    rule = fomc_statement_rule(entry)
    digest = canonical_hash(rule)
    return dict(
        schedule_ref=entry["source_ref"],
        observation_rule_ref=dict(ref="event-source-rule:" + digest, hash=digest),
        rule=rule,
    )


def declared_fomc_source_rule(
    event: Body,
    supported: list[Body],
    entries: list[Body],
) -> dict[str, Any]:
    selected = [
        item for item in supported if item["observation_rule_ref"] == event["observation_rule_ref"]
    ]
    matches = [
        entry
        for entry in entries
        if entry["family"] == event["family"]
        and entry["event_key"] == event["event_key"]
        and entry["stage_id"] == event["stage_id"]
        and entry["scheduled_at"] is not None
        and aware_utc(datetime.fromisoformat(entry["scheduled_at"].replace("Z", "+00:00")))
        == aware_utc(datetime.fromisoformat(event["scheduled_at"].replace("Z", "+00:00")))
    ]
    if len(selected) != 1 or len(matches) != 1:
        raise ValueError("event_native_fomc_source_rule_pending")
    expected = fomc_source_rule(matches[0])
    if (
        expected is None
        or selected[0] != expected
        or expected["rule"] != fomc_statement_rule(event)
    ):
        raise ValueError("event_native_fomc_source_rule_pending")
    return dict(
        source_ref=event["observation_rule_ref"],
        role="observation_rule",
        body_json=json.dumps(
            expected["rule"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ),
    )
