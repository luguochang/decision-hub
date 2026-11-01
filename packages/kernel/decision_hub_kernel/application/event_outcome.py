"""Native event scoring and complete-path paper outcomes; HTTP reads never write."""

import hashlib
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from ..decision.cpi_event_scenario import score_joint_event
from ..decision.event_confirmation_policy import confirmation_spec, evaluate_event_confirmation
from ..decision.event_opportunity_policy import verify_event_policy
from ..persistence.db import as_utc, utcnow
from ..persistence.event_outcome_records import EventOutcomeRecord as Record
from ..persistence.event_realization_records import EventRealizationRecord
from ..persistence.event_scenario_records import (
    EventScenarioForecastRecord,
    EventScenarioOutputRecord,
)
from ..persistence.original_preparation import begin_immediate, prepare_originals
from ..persistence.risk_records import MarketCandleRecord
from ..ports.event_opportunity import EventOwnedOpportunitySnapshotV2
from ..ports.event_outcome import (
    EventAssetOutcome,
    EventJointScore,
    EventOutcomeSnapshot,
    seal_event_outcome,
)
from ..ports.market_path import aware_utc, canonical_hash, path_hashes, spot_scope
from .cpi_pre_event_context import _archive
from .event_owned_opportunity import KIND, read_event_owned_opportunity_in_session
from .event_realization import read_latest_event_realization_for_forecast_in_session
from .event_scenario_autoproduction import read_event_owned_scenario_in_session
from .market_path_store import MarketPathStore


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _method():
    from ..decision import (
        cpi_event_scenario,
        event_confirmation_policy,
        event_opportunity_policy,
    )
    from . import market_path_store

    modules = (
        cpi_event_scenario,
        event_confirmation_policy,
        event_opportunity_policy,
        market_path_store,
    )
    return canonical_hash(
        {
            "protocol": "event-anchored-known-activation-policy-paper.v1",
            "reader_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "dependencies": {
                m.__name__: hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()
                for m in modules
            },
        }
    )


def _replay(policy, bars, *, scheduled_at, activation_known_at):
    """Shared event-confirmation rules over an explicitly simulated close clock.

    The caller authenticates original prices at the outcome cutoff. This paper
    experiment then assumes a bar was available at its close; it never changes
    original market receipts or the actual CPI known-at. No account execution is
    inferred. Both position branches follow the same terminal invalidation as
    the event-owned live monitor.
    """
    verify_event_policy(policy)
    ordered = tuple(sorted(bars, key=lambda bar: bar.open_at))
    by = {bar.open_at: bar for bar in ordered}
    end = scheduled_at + timedelta(seconds=policy.exit_end_seconds)
    entry_end = scheduled_at + timedelta(seconds=policy.entry_end_seconds)
    if len(by) != len(ordered) or not ordered or ordered[-1].close_at != end:
        raise ValueError("event_outcome_replay_path_invalid")
    if any(bar.scope_id != policy.scope_id for bar in ordered):
        raise ValueError("event_outcome_replay_scope_invalid")
    hypothetical = tuple(
        bar.model_copy(update={"received_at": bar.close_at, "accepted_at": bar.close_at})
        for bar in ordered
    )
    entry = reduction = None
    exit_at = exit_price = None
    prior = {"flat": (), "long": ()}
    invalidated = {"flat": False, "long": False}
    for bar in ordered:
        at = bar.close_at
        if at < max(
            scheduled_at + timedelta(seconds=policy.entry_start_seconds), activation_known_at
        ):
            continue
        if at >= end:
            break
        for position in ("flat", "long"):
            if invalidated[position] or (position == "long" and reduction is not None):
                continue
            if at >= entry_end and (position == "long" or entry is None):
                continue
            if position == "flat" and entry is not None and at <= entry.open_at:
                continue
            result = evaluate_event_confirmation(
                policy,
                position_context=position,
                scheduled_at=scheduled_at,
                as_of=at,
                activation_known_at=activation_known_at,
                candles=hypothetical,
                previous=prior[position],
            )
            prior[position] = result.per_condition
            if any(
                condition.status != "ok"
                for condition in (result.entry, result.exit, result.invalidation)
            ):
                raise ValueError("event_outcome_confirmation_invalid")
            fill_at = at + timedelta(seconds=policy.reaction_delay_seconds)
            if result.invalidation.truth == "true":
                invalidated[position] = True
                if position == "flat" and entry is not None and exit_at is None and fill_at < end:
                    fill = by.get(fill_at)
                    if fill is None:
                        raise ValueError("event_outcome_exit_fill_missing")
                    exit_at, exit_price = fill.open_at, fill.open
                continue
            if position == "long":
                if result.entry.truth == "true" and fill_at < entry_end:
                    reduction = by.get(fill_at)
                    if reduction is None:
                        raise ValueError("event_outcome_reduction_fill_missing")
                continue
            if entry is None:
                if (
                    result.exit.truth == "false"
                    and result.entry.truth == "true"
                    and fill_at < entry_end
                ):
                    entry = by.get(fill_at)
                    if entry is None:
                        raise ValueError("event_outcome_entry_fill_missing")
            elif exit_at is None and result.exit.truth == "true" and fill_at < end:
                fill = by.get(fill_at)
                if fill is None:
                    raise ValueError("event_outcome_exit_fill_missing")
                exit_at, exit_price = fill.open_at, fill.open
        if (invalidated["flat"] or exit_at is not None) and (invalidated["long"] or reduction):
            break
    cost = Decimal(policy.fee_fraction_each_side) + Decimal(policy.slippage_fraction_each_side)
    net = Decimal(0)
    if entry is not None:
        if exit_at is None:
            exit_at, exit_price = end, ordered[-1].close
        net = exit_price * (1 - cost) / (entry.open * (1 + cost)) - 1
    anchor = by[scheduled_at + timedelta(seconds=60)].open
    return dict(
        strategy_entered=entry is not None,
        strategy_entry_at=entry.open_at if entry else None,
        strategy_exit_at=exit_at,
        strategy_net_return=net,
        held_long_reduce_at=reduction.open_at if reduction else None,
        held_long_reduction_value_fraction=(
            (reduction.open * (1 - cost) - ordered[-1].close) / anchor if reduction else Decimal(0)
        ),
    )


def _asset(session, forecast, realization, instrument, *, archive, cutoff):
    start = forecast.scheduled_at + timedelta(seconds=60)
    end = forecast.scheduled_at + timedelta(hours=24, seconds=60)
    row = session.scalar(
        select(EventScenarioOutputRecord)
        .where(
            EventScenarioOutputRecord.forecast_id == forecast.forecast_id,
            EventScenarioOutputRecord.kind == KIND,
            EventScenarioOutputRecord.scope_key == instrument,
            EventScenarioOutputRecord.published_at < forecast.scheduled_at,
            EventScenarioOutputRecord.published_at <= cutoff,
        )
        .order_by(
            EventScenarioOutputRecord.published_at.desc(), EventScenarioOutputRecord.output_id
        )
        .limit(1)
    )
    opportunity = (
        read_event_owned_opportunity_in_session(
            session, row.output_id, archive=archive, cutoff_at=cutoff
        )
        if row
        else None
    )
    body = dict(
        instrument=instrument,
        opportunity_ref=opportunity.snapshot_id if opportunity else None,
        opportunity_hash=opportunity.payload_hash if opportunity else None,
        reaction_start_at=start,
        reaction_end_at=end,
        activation_known_at=realization.known_at,
    )
    if cutoff < end:
        return EventAssetOutcome(
            **body, state="waiting_horizon", gaps=("event_24h_horizon_not_mature",)
        )
    scope = spot_scope(instrument)
    first = forecast.scheduled_at - timedelta(minutes=20)
    captures = []
    bars = MarketPathStore(None, archive).load_in_session(
        session,
        scope,
        start_at=first,
        end_at=end,
        as_of=cutoff,
        on_original=lambda ref, digest: captures.append((ref, digest)),
    )
    expected_count = int((end - first).total_seconds() / 60)
    if len(bars) != expected_count or any(
        b.open_at != first + timedelta(minutes=i) for i, b in enumerate(bars)
    ):
        return EventAssetOutcome(
            **body, state="missing_path", gaps=("authenticated_complete_minute_path_missing",)
        )
    semantic, provenance = path_hashes(scope, bars)
    anchor = next(b for b in bars if b.open_at == start)
    body.update(
        return_24h=bars[-1].close / anchor.open - 1,
        path_semantic_hash=semantic,
        path_provenance_hash=provenance,
        capture_originals=tuple(sorted(set(captures))),
    )
    if not isinstance(
        opportunity, EventOwnedOpportunitySnapshotV2
    ) or opportunity.model_valid_until < forecast.scheduled_at - timedelta(seconds=1):
        return EventAssetOutcome(
            **body, state="missing_plan", gaps=("valid_preevent_v2_plan_missing",)
        )
    if (
        opportunity.forecast_id != forecast.forecast_id
        or opportunity.scenario_snapshot_hash != forecast.payload_hash
        or opportunity.event_key != realization.event_key
        or opportunity.instrument != instrument
    ):
        raise ValueError("event_outcome_opportunity_crosswired")
    selected = [p for p in opportunity.plans if p.scenario_id == realization.scenario_id]
    if len(selected) != 1:
        raise ValueError("event_outcome_unique_policy_missing")
    policy = selected[0].policy
    replay = _replay(
        policy, bars, scheduled_at=forecast.scheduled_at, activation_known_at=realization.known_at
    )
    return EventAssetOutcome(
        **body,
        state="scored",
        policy_hash=confirmation_spec(policy).policy_hash,
        source_policy_hash=policy.policy_hash,
        **replay,
        gaps=(
            "paper_rule_replay_not_observed_account_execution",
            "held_long_value_is_one_unit_relative_to_holding_not_account_profit",
            "spread_and_actual_account_fees_unverified",
            "price_availability_at_close_is_simulated_not_native_observed_execution",
            "old_v2_profit_probability_does_not_qualify_event_anchored_protocol",
        ),
    )


def _collect(session, forecast_id, *, cutoff, recorded_at, archive, parent=None):
    forecast = read_event_owned_scenario_in_session(
        session, forecast_id, archive=archive, cutoff_at=cutoff
    )
    realization = read_latest_event_realization_for_forecast_in_session(
        session, forecast_id, archive=archive, cutoff_at=cutoff
    )
    if realization is None:
        return None
    if realization.owner_forecast_hash != forecast.payload_hash:
        raise ValueError("event_outcome_realization_forecast_crosswired")
    metrics = {m.metric_id: m.value for m in realization.metrics}
    score = score_joint_event(
        forecast,
        realized_headline_mom=metrics["headline_mom"],
        realized_core_mom=metrics["core_mom"],
        label_known_at=realization.known_at,
        target_definition=realization.target_definition,
    )
    event_score = EventJointScore(
        label=score["label"],
        brier=score["joint_multiclass_brier"],
        log_loss=score["joint_log_loss"],
    )
    assets = tuple(
        _asset(session, forecast, realization, instrument, archive=archive, cutoff=cutoff)
        for instrument in ("BTC-USDT", "ETH-USDT")
    )
    return seal_event_outcome(
        dict(
            event_key=forecast.event_key,
            forecast_id=forecast_id,
            realization_id=realization.realization_id,
            owner_forecast_hash=forecast.payload_hash,
            realization_hash=realization.payload_hash,
            scheduled_at=forecast.scheduled_at,
            as_of=cutoff,
            recorded_at=recorded_at,
            event_score=event_score.model_dump(mode="json"),
            assets=[a.model_dump(mode="json") for a in assets],
            method_hash=_method(),
            revision_parent_ref=parent,
        )
    )


def _latest_row(session, forecast_id, *, cutoff):
    return session.scalar(
        select(Record)
        .where(Record.forecast_id == forecast_id, Record.recorded_at <= cutoff)
        .order_by(Record.recorded_at.desc(), Record.outcome_id.desc())
        .limit(1)
    )


def _validate(row, value, cutoff):
    if (
        row.outcome_id != value.outcome_id
        or row.forecast_id != value.forecast_id
        or row.event_key != value.event_key
        or row.realization_id != value.realization_id
        or row.owner_forecast_hash != value.owner_forecast_hash
        or row.realization_hash != value.realization_hash
        or row.input_hash != value.input_hash
        or row.revision_parent_ref != value.revision_parent_ref
        or row.schema_version != value.schema_version
        or as_utc(row.as_of) != value.as_of
        or as_utc(row.recorded_at) != value.recorded_at
        or value.recorded_at > cutoff
        or row.payload_json != _json(value.model_dump(mode="json"))
        or row.payload_hash != canonical_hash(value.model_dump(mode="json"))
        or value.payload_hash
        != canonical_hash(value.model_dump(mode="json", exclude={"payload_hash"}))
    ):
        raise ValueError("event_outcome_native_original_invalid")


def read_event_outcome_in_session(session, outcome_id, *, cutoff_at, archive=None):
    archive = archive or _archive()
    cutoff = aware_utc(cutoff_at)
    row = session.get(Record, outcome_id, populate_existing=True)
    if row is None:
        raise ValueError("event_outcome_original_missing")
    value = EventOutcomeSnapshot.model_validate_json(row.payload_json)
    _validate(row, value, cutoff)
    expected = _collect(
        session,
        value.forecast_id,
        cutoff=value.as_of,
        recorded_at=value.recorded_at,
        archive=archive,
        parent=value.revision_parent_ref,
    )
    if expected != value:
        raise ValueError("event_outcome_original_replay_changed")
    if value.revision_parent_ref:
        parent = session.get(Record, value.revision_parent_ref, populate_existing=True)
        if parent is None:
            raise ValueError("event_outcome_parent_missing")
        prior = EventOutcomeSnapshot.model_validate_json(parent.payload_json)
        _validate(parent, prior, value.recorded_at)
        if prior.forecast_id != value.forecast_id or prior.recorded_at >= value.recorded_at:
            raise ValueError("event_outcome_parent_invalid")
    return value


def read_latest_event_outcome_for_forecast_in_session(
    session, forecast_id, *, cutoff_at, archive=None
):
    cutoff = aware_utc(cutoff_at)
    row = _latest_row(session, forecast_id, cutoff=cutoff)
    if row is None:
        return None
    value = read_event_outcome_in_session(
        session, row.outcome_id, cutoff_at=cutoff, archive=archive
    )
    realization = read_latest_event_realization_for_forecast_in_session(
        session, forecast_id, archive=archive or _archive(), cutoff_at=cutoff
    )
    return value if realization and realization.realization_id == value.realization_id else None


def event_outcome_user_view(snapshot, *, scheduled_at, cutoff_at):
    cutoff = aware_utc(cutoff_at)
    if snapshot is None:
        return dict(
            state="waiting"
            if cutoff < scheduled_at + timedelta(hours=24, seconds=60)
            else "missing",
            outcome_id=None,
            event_score=None,
            assets=[],
            gaps=["authenticated_event_outcome_not_recorded"],
        )
    assets = [
        {
            key: value
            for key, value in a.model_dump(mode="json").items()
            if key
            in {
                "instrument",
                "state",
                "return_24h",
                "strategy_entered",
                "strategy_entry_at",
                "strategy_exit_at",
                "strategy_net_return",
                "held_long_reduce_at",
                "held_long_reduction_value_fraction",
                "paper_replay",
                "confirmation_protocol",
                "strategy_target",
                "simulated_price_availability_at_close",
                "strategy_profit_probability",
                "gaps",
            }
        }
        for a in snapshot.assets
    ]
    state = (
        "complete"
        if all(a.state == "scored" for a in snapshot.assets)
        else "missing"
        if any(a.state in {"missing_path", "missing_plan"} for a in snapshot.assets)
        else "partial"
    )
    return dict(
        state=state,
        outcome_id=snapshot.outcome_id,
        event_score=snapshot.event_score.model_dump(mode="json"),
        assets=assets,
        gaps=[],
    )


class _TrackingArchive:
    def __init__(self, archive):
        self.archive, self.originals = archive, {}

    def read(self, ref):
        body = self.archive.read(ref)
        digest = hashlib.sha256(body).hexdigest()
        if ref in self.originals and self.originals[ref] != digest:
            raise ValueError("event_outcome_archive_changed_during_preparation")
        self.originals[ref] = digest
        return body


class EventOutcomeService:
    def __init__(self, database, *, archive=None, clock=utcnow):
        self.database, self.archive, self.clock = database, archive or _archive(), clock

    def process_forecast(self, forecast_id, *, checkpoint=None):
        at = aware_utc(self.clock())
        archive = _TrackingArchive(self.archive)

        def operation(session):
            previous = _latest_row(session, forecast_id, cutoff=at)
            value = _collect(
                session,
                forecast_id,
                cutoff=at,
                recorded_at=at,
                archive=archive,
                parent=previous.outcome_id if previous else None,
            )
            if value and previous and value.input_hash == previous.input_hash:
                return read_event_outcome_in_session(
                    session, previous.outcome_id, cutoff_at=at, archive=archive
                ), True
            return value, False

        prepared = prepare_originals(self.database, operation, checkpoint=checkpoint)
        value, existing = prepared.value
        if value is None:
            return None
        if existing:
            # Already authenticated in one stable RO snapshot; no writer is
            # needed to return the original, and this grants no new authority.
            return value
        with self.database.session() as session:
            begin_immediate(session)
            prepared.verify(session, checkpoint=checkpoint)
            for ref, digest in archive.originals.items():
                if hashlib.sha256(self.archive.read(ref)).hexdigest() != digest:
                    raise ValueError("event_outcome_raw_original_changed")
            body = value.model_dump(mode="json")
            session.add(
                Record(
                    outcome_id=value.outcome_id,
                    event_key=value.event_key,
                    forecast_id=value.forecast_id,
                    realization_id=value.realization_id,
                    owner_forecast_hash=value.owner_forecast_hash,
                    realization_hash=value.realization_hash,
                    as_of=value.as_of,
                    recorded_at=value.recorded_at,
                    input_hash=value.input_hash,
                    revision_parent_ref=value.revision_parent_ref,
                    schema_version=value.schema_version,
                    payload_json=_json(body),
                    payload_hash=canonical_hash(body),
                )
            )
            session.flush()
            prepared.verify_after_writes(session, checkpoint=checkpoint)
        return value

    def process_due(self, *, max_events=4, checkpoint=None):
        now = aware_utc(self.clock())
        with self.database.session() as session:
            rows = session.scalars(
                select(EventScenarioForecastRecord)
                .join(
                    EventRealizationRecord,
                    EventRealizationRecord.forecast_id == EventScenarioForecastRecord.forecast_id,
                )
                .where(
                    EventScenarioForecastRecord.scheduled_at <= now,
                    EventScenarioForecastRecord.scheduled_at >= now - timedelta(days=30),
                    EventScenarioForecastRecord.published_at
                    < EventScenarioForecastRecord.scheduled_at,
                    EventRealizationRecord.recorded_at <= now,
                )
                .order_by(
                    EventScenarioForecastRecord.scheduled_at.desc(),
                    EventScenarioForecastRecord.published_at.desc(),
                    EventScenarioForecastRecord.forecast_id,
                )
            ).all()
            targets = {}
            for row in rows:
                if row.event_key in targets:
                    continue
                targets[row.event_key] = row
            due = []
            method = _method() if targets else None
            for row in targets.values():
                previous = _latest_row(session, row.forecast_id, cutoff=now)
                latest_realization = session.scalar(
                    select(EventRealizationRecord)
                    .where(
                        EventRealizationRecord.forecast_id == row.forecast_id,
                        EventRealizationRecord.recorded_at <= now,
                    )
                    .order_by(
                        EventRealizationRecord.recorded_at.desc(),
                        EventRealizationRecord.realization_id.desc(),
                    )
                    .limit(1)
                )
                if previous is not None:
                    value = EventOutcomeSnapshot.model_validate_json(previous.payload_json)
                    _validate(previous, value, now)
                    same_actual = latest_realization.realization_id == value.realization_id
                    unchanged_method = value.method_hash == method
                    if same_actual and unchanged_method:
                        # A pre-maturity score cannot change before its next
                        # milestone; future events never enter this queue.
                        maturity = as_utc(row.scheduled_at) + timedelta(hours=24, seconds=60)
                        if now < maturity:
                            continue
                        new_price = session.scalar(
                            select(MarketCandleRecord.candle_id)
                            .where(
                                MarketCandleRecord.known_at > value.as_of,
                                MarketCandleRecord.known_at <= now,
                                MarketCandleRecord.open_at
                                >= as_utc(row.scheduled_at) - timedelta(minutes=20),
                                MarketCandleRecord.close_at <= maturity,
                                MarketCandleRecord.instrument.in_(("BTC-USDT", "ETH-USDT")),
                            )
                            .limit(1)
                        )
                        horizon_was_mature = value.as_of >= maturity
                        if horizon_was_mature and new_price is None:
                            continue
                due.append(row.forecast_id)
                if len(due) == max_events:
                    break
        count = 0
        for forecast_id in due:
            if checkpoint:
                checkpoint()
            if self.process_forecast(forecast_id, checkpoint=checkpoint) is not None:
                count += 1
        return count
