"""One-to-one conservative matching; unknown observations are never losses."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import Decimal
from statistics import median
from typing import Any

from ..ports.forecast_alarm import (
    EVENT_CLUSTER_SECONDS,
    MINIMUM_LEAD_SECONDS,
    AlarmOutcomeV1,
    ForecastAlarmReceiptV1,
    ForecastAlarmV1,
)
from ..ports.market_path import aware_utc, canonical_hash


def _key(value: ForecastAlarmV1 | AlarmOutcomeV1) -> tuple[Any, ...]:
    return (
        value.scope_id,
        value.cutoff_at,
        value.window_start_at,
        value.window_end_at,
        value.direction,
        value.threshold,
    )


def _ratio(numerator: int, denominator: int) -> str | None:
    return str(Decimal(numerator) / denominator) if denominator else None


def _interval(value: AlarmOutcomeV1) -> tuple[datetime, datetime]:
    if value.interval_start_at is None or value.interval_end_at is None:
        raise ValueError("forecast_alarm_first_crossing_interval_unavailable")
    return value.interval_start_at, value.interval_end_at


def primary_event_clusters(outcomes: tuple[AlarmOutcomeV1, ...]) -> list[dict[str, Any]]:
    """Primary mature event census, including events with no alert, across assets."""
    events = sorted(
        (
            x
            for x in outcomes
            if x.direction == "long"
            and x.threshold == Decimal(".02")
            and x.event_presence is True
            and x.firstness == "verified"
        ),
        key=lambda x: (x.interval_start_at, x.scope_id, x.original_hash),
    )
    clusters: list[list[AlarmOutcomeV1]] = []
    for event in events:
        if (
            not clusters
            or (_interval(event)[0] - _interval(clusters[-1][-1])[1]).total_seconds()
            > EVENT_CLUSTER_SECONDS
        ):
            clusters.append([])
        clusters[-1].append(event)
    return [
        {
            "block_id": "eventcluster_"
            + canonical_hash(
                [
                    [
                        x.scope_id,
                        x.cutoff_at.isoformat(),
                        _interval(x)[0].isoformat(),
                        x.original_hash,
                    ]
                    for x in cluster
                ]
            ),
            "cutoff_at": min(x.cutoff_at for x in cluster).isoformat(),
            "known_at": max(x.label_known_at for x in cluster).isoformat(),
            "members": [
                {
                    "original_ref": x.original_ref,
                    "original_hash": x.original_hash,
                    "scope_id": x.scope_id,
                    "cutoff_at": x.cutoff_at.isoformat(),
                    "interval_start_at": _interval(x)[0].isoformat(),
                    "interval_end_at": _interval(x)[1].isoformat(),
                }
                for x in cluster
            ],
        }
        for cluster in clusters
    ]


def match_forecast_alarms(
    alarms: tuple[ForecastAlarmV1, ...],
    outcomes: tuple[AlarmOutcomeV1, ...],
    receipts: tuple[ForecastAlarmReceiptV1, ...],
    *,
    expected_slots: tuple[tuple[str, datetime], ...],
    as_of: datetime,
) -> dict[str, Any]:
    clock = aware_utc(as_of)
    slots = set((scope, aware_utc(cutoff)) for scope, cutoff in expected_slots)
    if len(slots) != len(expected_slots):
        raise ValueError("forecast_alarm_duplicate_expected_slot")
    labels: dict[tuple[Any, ...], AlarmOutcomeV1] = {}
    for outcome in outcomes:
        if outcome.label_known_at > clock or (outcome.scope_id, outcome.cutoff_at) not in slots:
            raise ValueError("forecast_alarm_label_identity_invalid")
        key = _key(outcome)
        if key in labels:
            raise ValueError("forecast_alarm_duplicate_label")
        labels[key] = outcome
    if len({x.alarm_id for x in alarms}) != len(alarms):
        raise ValueError("forecast_alarm_duplicate_original")
    if len({(x.method, *_key(x)) for x in alarms}) != len(alarms):
        raise ValueError("forecast_alarm_duplicate_target_window")
    if any(x.known_at > clock or (x.scope_id, x.cutoff_at) not in slots for x in alarms):
        raise ValueError("forecast_alarm_original_identity_invalid")
    alarm_map = {x.alarm_id: x for x in alarms}
    receipt_map = {x.receipt_id: x for x in receipts}
    if len(receipt_map) != len(receipts):
        raise ValueError("forecast_alarm_duplicate_receipt")
    for receipt in receipts:
        alarm = alarm_map.get(receipt.alarm_id)
        if alarm is None or receipt.alarm_hash != alarm.payload_hash or receipt.known_at > clock:
            raise ValueError("forecast_alarm_receipt_identity_invalid")
        if receipt.parent_receipt_id is not None:
            parent = receipt_map.get(receipt.parent_receipt_id)
            if (
                parent is None
                or parent.payload_hash != receipt.parent_receipt_hash
                or parent.alarm_id != receipt.alarm_id
                or parent.owner_id != receipt.owner_id
                or parent.known_at > receipt.known_at
                or parent.receipt_kind
                != ("displayed" if receipt.receipt_kind == "visible" else "visible")
            ):
                raise ValueError("forecast_alarm_receipt_parent_original_changed")
    targets, matches, exclusions = [], [], []
    for scope in sorted({x[0] for x in slots}):
        for method in ("BASE_EMPIRICAL_V1", "COND_VOL_V1"):
            for direction in ("long", "short"):
                for threshold in (Decimal(".02"), Decimal(".05")):
                    target_alarms = sorted(
                        (
                            x
                            for x in alarms
                            if (x.scope_id, x.method, x.direction, x.threshold)
                            == (scope, method, direction, threshold)
                        ),
                        key=lambda x: (x.known_at, x.alarm_id),
                    )
                    target_labels = [
                        x
                        for x in labels.values()
                        if (x.scope_id, x.direction, x.threshold) == (scope, direction, threshold)
                    ]
                    known = [
                        x
                        for x in target_labels
                        if x.event_presence is False
                        or x.event_presence is True
                        and x.firstness == "verified"
                    ]
                    events = [x for x in known if x.event_presence is True]
                    eligible, negative, leads = 0, 0, []
                    used = set()
                    display_eligible = exposure_eligible = display_matches = exposure_matches = 0
                    for alarm in target_alarms:
                        label = labels.get(_key(alarm))
                        if label is None or label not in known:
                            exclusions.append(
                                {"alarm_id": alarm.alarm_id, "reason": "label_or_firstness_unknown"}
                            )
                            continue
                        eligible += 1
                        negative += label.event_presence is False
                        lead = (
                            (_interval(label)[0] - alarm.known_at).total_seconds()
                            if label.event_presence
                            else None
                        )
                        matched = bool(
                            lead is not None
                            and lead >= MINIMUM_LEAD_SECONDS
                            and _key(label) not in used
                        )
                        if matched and lead is not None:
                            used.add(_key(label))
                            leads.append(int(lead))
                            matches.append(
                                {
                                    "alarm_id": alarm.alarm_id,
                                    "label_ref": label.original_ref,
                                    "label_hash": label.original_hash,
                                    "lead_seconds": int(lead),
                                    "resolution_seconds": 60,
                                    "method": method,
                                    "scope_id": scope,
                                    "direction": direction,
                                    "threshold": str(threshold),
                                }
                            )
                        display = [
                            x
                            for x in receipts
                            if x.alarm_id == alarm.alarm_id and x.receipt_kind == "displayed"
                        ]
                        exposure = [
                            x
                            for x in receipts
                            if x.alarm_id == alarm.alarm_id and x.receipt_kind == "visible"
                        ]
                        display_eligible += bool(display)
                        exposure_eligible += bool(exposure)
                        if matched:
                            display_matches += any(
                                (_interval(label)[0] - x.known_at).total_seconds()
                                >= MINIMUM_LEAD_SECONDS
                                for x in display
                            )
                            exposure_matches += any(
                                (_interval(label)[0] - x.known_at).total_seconds()
                                >= MINIMUM_LEAD_SECONDS
                                for x in exposure
                            )
                    expected = sum(x[0] == scope for x in slots)
                    targets.append(
                        {
                            "scope_id": scope,
                            "method": method,
                            "direction": direction,
                            "threshold": str(threshold),
                            "expected_slots": expected,
                            "known_label_count": len(known),
                            "unknown_label_count": expected - len(known),
                            "event_count": len(events),
                            "alarm_count": len(target_alarms),
                            "scored_alarm_count": eligible,
                            "unknown_alarm_count": len(target_alarms) - eligible,
                            "matched_alarm_count": len(leads),
                            "false_alarm_count": negative,
                            "precision": _ratio(len(leads), eligible),
                            "recall": _ratio(len(leads), len(events)),
                            "false_episodes_per_asset_day": _ratio(negative, len(known)),
                            "lead_time": (
                                {
                                    "matched_count": len(leads),
                                    "minimum_seconds": str(min(leads)),
                                    "median_seconds": str(median(leads)),
                                    "resolution_seconds": 60,
                                    "conservative": True,
                                }
                                if leads
                                else None
                            ),
                            "delivery": {
                                "scored_alarm_count": display_eligible,
                                "matched_count": display_matches,
                                "unknown_receipt_count": len(target_alarms)
                                - len(
                                    {
                                        x.alarm_id
                                        for x in receipts
                                        if x.receipt_kind == "displayed"
                                        and x.alarm_id in {a.alarm_id for a in target_alarms}
                                    }
                                ),
                                "precision": _ratio(display_matches, display_eligible),
                                "recall": _ratio(display_matches, len(events))
                                if display_eligible
                                else None,
                            },
                            "exposure": {
                                "scored_alarm_count": exposure_eligible,
                                "matched_count": exposure_matches,
                                "precision": _ratio(exposure_matches, exposure_eligible),
                                "recall": _ratio(exposure_matches, len(events))
                                if exposure_eligible
                                else None,
                            },
                        }
                    )
    primary = [
        x
        for x in targets
        if x["method"] == "COND_VOL_V1" and x["direction"] == "long" and x["threshold"] == "0.02"
    ]
    n_alarm = sum(x["scored_alarm_count"] for x in primary)
    n_event = sum(x["event_count"] for x in primary)
    matched_count = sum(x["matched_alarm_count"] for x in primary)
    primary_leads = [
        x["lead_seconds"]
        for x in matches
        if x["method"] == "COND_VOL_V1" and x["direction"] == "long" and x["threshold"] == "0.02"
    ]
    unknown = sum(x["unknown_label_count"] for x in primary)
    return {
        "schema_version": "forecast-alarm-score.v1",
        "matching_version": "matching.v1",
        "as_of": clock.isoformat(),
        "metrics": {
            "precision": _ratio(matched_count, n_alarm),
            "recall": _ratio(matched_count, n_event),
            "false_episodes_per_asset_day": _ratio(
                sum(x["false_alarm_count"] for x in primary),
                sum(x["known_label_count"] for x in primary),
            ),
            "lead_time": (
                {
                    "matched_count": len(primary_leads),
                    "minimum_seconds": str(min(primary_leads)),
                    "median_seconds": str(median(primary_leads)),
                    "resolution_seconds": 60,
                    "conservative": True,
                }
                if primary_leads
                else None
            ),
            "status": "unknown"
            if not n_alarm and not n_event
            else "partial"
            if unknown
            else "evaluated",
            "reason": "mature_alarm_or_label_unavailable"
            if not n_alarm and not n_event
            else "matched_forecast_alarm_originals",
        },
        "counts": {
            "expected_asset_days": len(slots),
            "candidate_primary_scored_alarms": n_alarm,
            "candidate_primary_events": n_event,
            "candidate_primary_matches": matched_count,
            "candidate_primary_unknown_labels": unknown,
        },
        "by_target": targets,
        "matches": matches,
        "exclusions": exclusions,
        "exclusion_counts": dict(Counter(x["reason"] for x in exclusions)),
        "event_clusters": primary_event_clusters(tuple(labels.values())),
        "probability_qualified": False,
        "trading_authority": False,
    }
