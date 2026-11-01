"""One source-backed macro agenda for a selected perpetual planning window."""

import json
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ..persistence.db import MacroEventVintageRecord, ObservationRecord
from ..ports.market_path import aware_utc, canonical_hash
from .fomc_stage_original import fomc_stage_entries_in_session
from .official_schedule_original import (
    OfficialScheduleNotKnownAtCutoff,
    read_official_schedule_original_in_session,
    read_schedule_coverage_in_session,
    schedule_identity,
)

FAMILIES = {
    "cpi-publication-schedule": "cpi", "bls-calendar": "nfp", "fed-calendar": "fomc",
    "bea-calendar": "pce",
}


def _intersects(original, start, end):
    if original.time_precision == "exact":
        return start < original.scheduled_at <= end
    if (
        original.time_precision != "date"
        or original.scheduled_date is None
        or not original.source_timezone
    ):
        return False
    zone = ZoneInfo(original.source_timezone)
    lower = datetime.combine(original.scheduled_date, time.min, tzinfo=zone).astimezone(UTC)
    upper = datetime.combine(
        original.scheduled_date + timedelta(days=1), time.min, tzinfo=zone
    ).astimezone(UTC)
    return lower < end and upper > start


def collect_event_agenda_in_session(session, *, archive, as_of, instrument_refs, window_end):
    from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
        EventAgendaSnapshotV1,
    )

    at, end = aware_utc(as_of), aware_utc(window_end)
    if not at < end:
        raise ValueError("event_agenda_window_invalid")
    entries, coverage = [], []
    for source_id, family in FAMILIES.items():
        reasons, originals = [], []
        fomc_rule_bound = False
        try:
            ref, known, proof = read_schedule_coverage_in_session(
                session, source_id, archive=archive, cutoff_at=at
            )
        except (ValueError, OSError, KeyError):
            coverage.append(
                dict(
                    family=family,
                    # Before BEA was admitted this exact unavailable shape
                    # was frozen in v1 agendas. An adapter installation alone
                    # must not rewrite their point-in-time source coverage.
                    source_id=None if family == "pce" else source_id,
                    status="unavailable",
                    reason_codes=[
                        "pce_future_schedule_provider_unavailable"
                        if family == "pce"
                        else "schedule_source_unavailable_or_invalid"
                    ],
                    coverage_ref=None,
                    known_at=None,
                    original_json=None,
                )
            )
            continue
        rows = session.scalars(
            select(ObservationRecord)
            .where(
                ObservationRecord.source_id == source_id,
                ObservationRecord.received_at <= at,
                select(MacroEventVintageRecord.vintage_id)
                .where(
                    MacroEventVintageRecord.event_id == ObservationRecord.event_id,
                    MacroEventVintageRecord.created_at <= at,
                )
                .exists(),
            )
            .order_by(ObservationRecord.received_at.desc(), ObservationRecord.observation_id)
        ).all()
        seen = set()
        latest_events = json.loads(proof)["events"]
        admitted_identities = []
        for row in rows:
            if row.event_hint in seen:
                continue
            try:
                original = read_official_schedule_original_in_session(
                    session, row.observation_id, archive=archive, cutoff_at=at
                )
            except OfficialScheduleNotKnownAtCutoff:
                continue
            except (ValueError, OSError):
                seen.add(row.event_hint)
                reasons.append("schedule_original_invalid")
                continue
            seen.add(row.event_hint)
            if (
                original.family != family
                or json.loads(original.original_json)["vintage"]["status"] == "cancelled"
            ):
                continue
            identity = schedule_identity(
                original.event_key,
                original.family,
                original.scheduled_at,
                original.scheduled_date,
                original.time_precision,
                original.source_timezone,
            )
            if identity not in latest_events:
                if _intersects(original, at, end):
                    reasons.append("schedule_not_in_latest_calendar")
                continue
            admitted_identities.append(identity)
            if _intersects(original, at, end):
                originals.append(original)
        for identity in latest_events:
            if identity["family"] != family or identity in admitted_identities:
                continue
            pending = SimpleNamespace(
                **{
                    **identity,
                    "scheduled_at": datetime.fromisoformat(identity["scheduled_at"])
                    if identity["scheduled_at"]
                    else None,
                    "scheduled_date": date.fromisoformat(identity["scheduled_date"])
                    if identity["scheduled_date"]
                    else None,
                }
            )
            if _intersects(pending, at, end):
                reasons.append("schedule_revision_not_admitted")
        for original in originals:
            if family == "fomc":
                try:
                    stages = fomc_stage_entries_in_session(
                        session, original, proof, archive=archive, as_of=at, window_end=end
                    )
                except (ValueError, OSError, KeyError):
                    reasons.append("fomc_policy_original_invalid")
                    stages = None
                if stages is not None:
                    fomc_rule_bound = True
                    entries.extend(stages)
                    continue
            overlap = (
                "agenda-overlap:" + canonical_hash([original.scheduled_at.isoformat()])
                if original.scheduled_at
                else None
            )
            entries.append(
                dict(
                    source_ref=original.source_ref,
                    source_id=original.source_id,
                    event_key=original.event_key,
                    event_id=original.event_id,
                    family=original.family,
                    stage_id="meeting_date" if family == "fomc" else "release",
                    scheduled_at=original.scheduled_at,
                    scheduled_date=original.scheduled_date.isoformat()
                    if original.scheduled_date
                    else None,
                    time_precision=original.time_precision,
                    source_timezone=original.source_timezone,
                    known_at=original.known_at,
                    occurrence_ref=original.occurrence_ref,
                    identity_basis=original.identity_basis,
                    overlap_group=overlap,
                    dependency_refs=[],
                    dependency_status="unknown",
                    materiality_reason="USD macro schedule intersects this perpetual research planning window; no causal effect or probability asserted",
                    next_check_at=None,
                    original_json=original.original_json,
                    raw_capture_refs=list(original.raw_capture_refs),
                )
            )
        if family == "fomc" and not fomc_rule_bound:
            reasons.append("fomc_release_sep_press_conference_times_unavailable")
        if family == "nfp":
            reasons.append("employment_actual_wages_unemployment_revisions_not_bound")
        if family == "pce":
            reasons.append("pce_actual_prior_consensus_and_family_scenario_not_bound")
        coverage.append(
            dict(
                family=family,
                source_id=source_id,
                status="unavailable"
                if any(
                    code in reasons
                    for code in ("schedule_original_invalid", "schedule_revision_not_admitted")
                )
                else "date_only"
                if originals and (
                    (family == "fomc" and not fomc_rule_bound)
                    or (family == "pce"
                        and any(item.time_precision != "exact" for item in originals))
                )
                else "available"
                if any(row["family"] == family for row in entries)
                else "no_upcoming",
                reason_codes=list(dict.fromkeys(reasons)),
                coverage_ref=ref,
                known_at=known,
                original_json=proof,
            )
        )
    entries.sort(
        key=lambda row: (
            str(row["scheduled_at"] or row["scheduled_date"]),
            row["source_id"],
            row["event_key"],
        )
    )
    from .employment_period_original import bind_employment_period_in_session

    entries = [
        bind_employment_period_in_session(session, entry, archive=archive, as_of=at)
        if entry["family"] == "nfp" else entry
        for entry in entries
    ]
    body = EventAgendaSnapshotV1.model_validate(
        dict(
            schema_version="event-agenda-snapshot.v1",
            as_of=at,
            instrument_refs=instrument_refs,
            window_start=at,
            window_end=end,
            window_basis="prepublication_planning",
            entries=entries,
            coverage=coverage,
            coverage_refs=[
                row["coverage_ref"] for row in coverage if row["coverage_ref"] is not None
            ],
            payload_hash="0" * 64,
        )
    ).model_dump(mode="json")
    body["payload_hash"] = canonical_hash(
        {key: value for key, value in body.items() if key != "payload_hash"}
    )
    return EventAgendaSnapshotV1.model_validate(body)


def read_event_agenda_in_session(session, value, *, archive):
    from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
        EventAgendaSnapshotV1,
    )

    saved = EventAgendaSnapshotV1.model_validate(
        value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    )
    wire = saved.model_dump(mode="json")
    if saved.payload_hash != canonical_hash(
        {key: body for key, body in wire.items() if key != "payload_hash"}
    ):
        raise ValueError("agenda_original_changed")
    actual = collect_event_agenda_in_session(
        session,
        archive=archive,
        as_of=saved.as_of,
        instrument_refs=saved.instrument_refs,
        window_end=saved.window_end,
    )
    if actual.model_dump(mode="json") != wire:
        raise ValueError("agenda_original_changed")
    return saved


def event_agenda_summary(value):
    """Readable frozen context, not a prediction or an independent report."""
    lines = ["窗口事件议程（研究前规划范围；不代表最终价格目标窗口）"]
    families = dict(cpi="CPI", nfp="就业", fomc="FOMC", pce="PCE")
    reasons = dict(
        pce_future_schedule_provider_unavailable="未来公布日程尚无可认证来源",
        pce_actual_prior_consensus_and_family_scenario_not_bound="PCE公布值、前值、共识与专用事件情景尚未绑定",
        fomc_release_sep_press_conference_times_unavailable="决议、SEP与发布会的具体时刻尚不可用",
        employment_actual_wages_unemployment_revisions_not_bound="未绑定就业、工资、失业率发布值及修订",
        schedule_source_unavailable_or_invalid="官方日程原件暂不可认证",
        schedule_original_invalid="部分日程原件校验失败，覆盖不完整",
        schedule_not_in_latest_calendar="旧日期已不在最新官方日历中，未继续沿用",
        schedule_revision_not_admitted="最新日历日期尚未形成可认证事件原件",
    )
    for row in value["entries"]:
        moment = (
            row["scheduled_at"]
            if row["time_precision"] == "exact"
            else str(row["scheduled_date"]) + "（具体时间未公布）"
        )
        lines.append(
            f"{families[row['family']]}：{moment}；事件依赖未知；来源 {row['source_ref']['ref']}"
        )
    for row in value["coverage"]:
        if row["status"] == "unavailable" or row["reason_codes"]:
            lines.append(
                families[row["family"]]
                + "："
                + "；".join(reasons.get(code, "覆盖尚待核验") for code in row["reason_codes"])
            )
    return lines
