"""Finite event-owned monitoring batches over native originals and one outbox."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import or_, select

from ..decision.action_policy import select_plan_event
from ..decision.event_confirmation_policy import (
    PROBABILITY_GAP, PENDING_RULE_SUMMARY,
    evaluate_event_confirmation,
)
from ..persistence.db import Database, OutboxRecord, as_utc, utcnow
from ..persistence.event_monitor_records import (
    EventOpportunityCommandRecord,
    EventOpportunityEvaluationRecord,
    EventOpportunityMonitorRecord,
    EventOpportunityNoticeRecord,
)
from ..persistence.event_scenario_records import (
    EventScenarioForecastRecord,
    EventScenarioOutputRecord,
)
from ..persistence.original_preparation import begin_immediate, prepare_originals
from ..ports.event_opportunity import EventOwnedOpportunitySnapshotV2
from ..ports.event_opportunity_monitor import (
    EventOpportunityEvaluation, EventOpportunityEvaluationV2, EventOpportunityNotice,
)
from ..ports.market_path import MarketCandle, aware_utc, canonical_hash, spot_scope
from ..ports.sources import make_notification_envelope
from .market_path_store import MarketPathStore, archive_feature
from .outbox import OutboxService


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _notice_body(evaluation, position):
    """User prose; immutable technical IDs remain in the notice's audit fields."""
    state = evaluation.state_after
    action = (
        "已有多仓达到减险确认条件，请评估减仓。" if state == "triggered" and position == "已有多仓"
        else "空仓新增风险条件已确认，请按预案评估入场与保护。" if state == "triggered"
        else "当前预案已失效，停止按此版本新增风险并检查保护。" if state == "invalidated"
        else "预案观察或退出窗口已结束，请回看当次结果。"
    )
    inputs = evaluation.confirmation_inputs
    actual = ""
    if inputs and inputs.event_return is not None:
        change = format(Decimal(inputs.event_return) * 100, ".4f").rstrip("0").rstrip(".")
        actual = f"当时相对公告前价格锚变动{change}%；"
        if inputs.minute_volume_ratio is not None:
            actual += f"该分钟成交量为公告前基准的{inputs.minute_volume_ratio}倍。"
    reasons = {
        "event_monitor_forecast_superseded": "已有更新的事前预判，请查看最新版本。",
        "event_monitor_bound_original_changed": "绑定的实际结果或预案已修订，需要重新核对。",
        "event_monitor_window_expired": "已到本预案规定的时间边界。",
        "event_monitor_invalidation_confirmed": "公告后价格达到预案保护条件。",
        "event_monitor_exit_condition_confirmed": "公告后价格达到退出保护条件。",
        "event_monitor_confirmation_protocol_changed": "旧监控已触发记录属于不同规则，不能算作新协议命中，请回看原记录。",
    }
    explanation = "".join(reasons[reason] for reason in evaluation.reason_codes if reason in reasons)
    return action + explanation + actual + evaluation.rule_summary + PROBABILITY_GAP + "这是人工决策提醒，不代表账户成交或实际盈亏。"


def _notice(row):
    value = EventOpportunityNotice.model_validate_json(row.payload_json)
    if (
        value.notice_id != row.notice_id
        or value.event_key != row.event_key
        or value.dedupe_key != row.dedupe_key
        or value.created_at != as_utc(row.created_at)
        or canonical_hash(value.model_dump(mode="json")) != row.payload_hash
        or canonical_hash(value.model_dump(mode="json", exclude={"payload_hash"}))
        != value.payload_hash
    ):
        raise ValueError("event_notice_original_invalid")
    return value


def enqueue_event_notice_in_session(
    session,
    *,
    database,
    event_key,
    reference_period,
    transition,
    subject,
    body,
    created_at,
    dedupe_key,
    instrument=None,
    position_context=None,
    forecast_id=None,
    opportunity_id=None,
    activation_id=None,
    evaluation_id=None,
    reason_codes=(),
):
    """Atomic immutable intent for lifecycle and pre-release coverage messages."""
    at = aware_utc(created_at)
    existing = session.scalar(
        select(EventOpportunityNoticeRecord).where(
            EventOpportunityNoticeRecord.dedupe_key == dedupe_key
        )
    )
    wire = dict(
        notice_id="event-notice-" + canonical_hash(dedupe_key)[:48],
        event_key=event_key,
        reference_period=reference_period,
        instrument=instrument,
        position_context=position_context,
        transition=transition,
        forecast_id=forecast_id,
        opportunity_id=opportunity_id,
        activation_id=activation_id,
        evaluation_id=evaluation_id,
        reason_codes=tuple(reason_codes),
        subject=subject,
        body=body,
        created_at=at,
        dedupe_key=dedupe_key,
        payload_hash="0" * 64,
    )
    if existing is not None:
        original = _notice(existing)
        # A replay uses its original server time. Content/owner conflicts still fail.
        wire["created_at"] = original.created_at
    value = EventOpportunityNotice(**wire)
    value = value.model_copy(
        update={
            "payload_hash": canonical_hash(value.model_dump(mode="json", exclude={"payload_hash"}))
        }
    )
    if existing is not None:
        if original != value:
            raise ValueError("event_notice_dedupe_payload_conflict")
        return original
    envelope = make_notification_envelope(
        subject_kind="event_opportunity",
        subject_id=value.notice_id,
        channel="local",
        dedupe_key=dedupe_key,
        subject=subject,
        body=body,
        created_at=at,
    )
    outbox_id = OutboxService(database).enqueue_subject(envelope, session=session)
    session.add(
        EventOpportunityNoticeRecord(
            notice_id=value.notice_id,
            event_key=event_key,
            dedupe_key=dedupe_key,
            outbox_id=outbox_id,
            payload_json=_json(value.model_dump(mode="json")),
            payload_hash=canonical_hash(value.model_dump(mode="json")),
            created_at=at,
        )
    )
    session.flush()
    return value


@dataclass(frozen=True)
class _Claim:
    monitor_id: str
    position: str
    generation: int
    owner: str
    expires_at: datetime
    version: int
    state: str
    activation_id: str | None
    opportunity_id: str | None
    opportunity_hash: str | None
    watermark: datetime | None
    previous: EventOpportunityEvaluation | EventOpportunityEvaluationV2 | None


@dataclass(frozen=True)
class _EventInputs:
    event_key: str
    reference_period: str
    scheduled_at: datetime
    activation: object | None
    opportunity: EventOwnedOpportunitySnapshotV2 | None
    error: str | None
    raw_originals: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class _ConfirmationMarketInputs:
    candles: tuple[MarketCandle, ...]
    payload_json: str
    input_hash: str
    raw_originals: tuple[tuple[str, str], ...]
    error: str | None = None


def _missing_market(scope_id, as_of, reason):
    body = _json({"schema_version": "event-confirmation-market-unavailable.v1",
                  "scope_id": scope_id, "as_of": as_of.isoformat(), "reason": reason})
    return _ConfirmationMarketInputs((), body, hashlib.sha256(body.encode()).hexdigest(), (), reason)


class EventOpportunityMonitorService:
    def __init__(
        self,
        database: Database,
        store: MarketPathStore,
        *,
        feature_root: Path,
        archive=None,
        clock: Callable[[], datetime] = utcnow,
        lease_seconds: int = 90,
    ):
        if store.database is not database or not 1 <= lease_seconds <= 300:
            raise ValueError("event_monitor_configuration_invalid")
        self.database, self.store = database, store
        self.archive, self.clock = archive or store.archive, clock
        self.feature_root, self.lease_seconds = Path(feature_root), lease_seconds

    def _prepare_market(self, session, scope, scheduled_at, as_of):
        """Authenticate only the fixed 20-minute baseline and latest closed bar.

        Rolling6h/hour features are preplan background; their availability never
        gates this independent event-confirmation protocol.
        """
        originals = {}

        def original(ref, digest):
            if ref in originals and originals[ref] != digest:
                raise ValueError("event_monitor_raw_original_changed")
            originals[ref] = digest

        try:
            end = scheduled_at.replace(second=0, microsecond=0)
            baseline = self.store.load_in_session(
                session, scope, start_at=end - timedelta(minutes=20), end_at=end,
                as_of=as_of, on_original=original,
            )
            current_end = as_of.replace(second=0, microsecond=0)
            current = self.store.load_in_session(
                session, scope, start_at=current_end - timedelta(minutes=2), end_at=current_end,
                as_of=as_of, on_original=original,
            )
            candles = tuple({bar.close_at: bar for bar in (*baseline, *current)}.values())
            body = _json({
                "schema_version": "event-confirmation-market.v1", "scope_id": scope.scope_id,
                "scheduled_at": scheduled_at.isoformat(), "as_of": as_of.isoformat(),
                "candles": [bar.model_dump(mode="json") for bar in candles],
            })
            return _ConfirmationMarketInputs(candles, body, hashlib.sha256(body.encode()).hexdigest(), tuple(sorted(originals.items())))
        except (ValueError, OSError) as exc:
            missing = _missing_market(scope.scope_id, as_of, str(exc) if isinstance(exc, ValueError) else "event_monitor_original_unavailable")
            return _ConfirmationMarketInputs((), missing.payload_json, missing.input_hash, tuple(sorted(originals.items())), missing.error)

    def _prepare_event(self, session, forecast_id, instrument, cutoff):
        from .event_owned_opportunity import KIND, read_event_owned_opportunity_in_session
        from .event_realization import read_latest_event_realization_for_forecast_in_session
        from .event_scenario_autoproduction import read_event_owned_scenario_in_session

        raw = {}
        parent_archive = self.archive

        class _ArchiveReads:
            def read(self, ref):
                content = parent_archive.read(ref)
                digest = hashlib.sha256(content).hexdigest()
                if ref in raw and raw[ref] != digest:
                    raise ValueError("event_monitor_raw_original_changed")
                raw[ref] = digest
                return content

        archive = _ArchiveReads()
        forecast = read_event_owned_scenario_in_session(
            session, forecast_id, archive=archive, cutoff_at=cutoff
        )
        latest = session.scalar(
            select(EventScenarioForecastRecord)
            .where(
                EventScenarioForecastRecord.event_key == forecast.event_key,
                EventScenarioForecastRecord.published_at < forecast.scheduled_at,
                EventScenarioForecastRecord.published_at <= cutoff,
            )
            .order_by(
                EventScenarioForecastRecord.published_at.desc(),
                EventScenarioForecastRecord.forecast_id,
            )
            .limit(1)
        )
        if latest and latest.forecast_id != forecast_id:
            return _EventInputs(
                forecast.event_key,
                forecast.reference_period,
                forecast.scheduled_at,
                None,
                None,
                "event_monitor_forecast_superseded",
                tuple(sorted(raw.items())),
            )
        if cutoff < forecast.scheduled_at:
            return _EventInputs(
                forecast.event_key,
                forecast.reference_period,
                forecast.scheduled_at,
                None,
                None,
                None,
                tuple(sorted(raw.items())),
            )
        activation = None
        opportunity = None
        error = None
        try:
            activation = read_latest_event_realization_for_forecast_in_session(
                session, forecast_id, archive=archive, cutoff_at=cutoff
            )
            row = session.scalar(
                select(EventScenarioOutputRecord)
                .where(
                    EventScenarioOutputRecord.forecast_id == forecast_id,
                    EventScenarioOutputRecord.kind == KIND,
                    EventScenarioOutputRecord.scope_key == instrument,
                    EventScenarioOutputRecord.published_at < forecast.scheduled_at,
                    EventScenarioOutputRecord.published_at <= cutoff,
                )
                .order_by(
                    EventScenarioOutputRecord.published_at.desc(),
                    EventScenarioOutputRecord.output_id,
                )
                .limit(1)
            )
            if row is not None:
                opportunity = read_event_owned_opportunity_in_session(
                    session, row.output_id, archive=archive, cutoff_at=cutoff
                )
                if not isinstance(opportunity, EventOwnedOpportunitySnapshotV2):
                    raise ValueError("event_monitor_policy_version_required")
                if (
                    opportunity.forecast_id != forecast_id
                    or opportunity.event_key != forecast.event_key
                    or opportunity.instrument != instrument
                    or opportunity.model_valid_until < forecast.scheduled_at - timedelta(seconds=1)
                ):
                    raise ValueError("event_monitor_preplan_stale_or_crosswired")
            elif cutoff >= forecast.scheduled_at:
                error = "event_monitor_preplan_missing"
        except (ValueError, OSError) as exc:
            error = (
                str(exc) if isinstance(exc, ValueError) else "event_monitor_original_unavailable"
            )
            opportunity = None
        return _EventInputs(
            forecast.event_key,
            forecast.reference_period,
            forecast.scheduled_at,
            activation,
            opportunity,
            error,
            tuple(sorted(raw.items())),
        )

    def _claim(self, forecast_id, instrument, event, tick):
        at = aware_utc(self.clock())
        result = []
        with self.database.session() as session:
            begin_immediate(session)
            for position in ("flat", "long"):
                ident = "event-monitor-" + canonical_hash([forecast_id, instrument, position])[:48]
                row = session.get(EventOpportunityMonitorRecord, ident)
                if row is None:
                    row = EventOpportunityMonitorRecord(
                        monitor_id=ident,
                        event_key=event.event_key,
                        forecast_id=forecast_id,
                        instrument=instrument,
                        position_context=position,
                        state="waiting",
                        state_version=0,
                        lease_generation=0,
                        next_due=at,
                        created_at=at,
                        updated_at=at,
                    )
                    session.add(row)
                    session.flush()
                if (
                    row.state in ("invalidated", "expired")
                    or row.next_due is None
                    or (
                        as_utc(row.next_due) > at
                        and event.error != "event_monitor_forecast_superseded"
                    )
                    or (row.lease_expires_at and as_utc(row.lease_expires_at) > at)
                ):
                    continue
                existing = session.scalar(
                    select(EventOpportunityEvaluationRecord).where(
                        EventOpportunityEvaluationRecord.monitor_id == ident,
                        EventOpportunityEvaluationRecord.tick_at == tick,
                    )
                )
                if existing is not None:
                    continue
                prior = (
                    session.get(EventOpportunityEvaluationRecord, row.latest_evaluation_id)
                    if row.latest_evaluation_id
                    else None
                )
                previous = self._evaluation(prior) if prior else None
                row.lease_generation += 1
                row.lease_owner = "event-monitor:" + uuid4().hex
                row.lease_expires_at = at + timedelta(seconds=self.lease_seconds)
                result.append(
                    _Claim(
                        ident,
                        position,
                        row.lease_generation,
                        row.lease_owner,
                        as_utc(row.lease_expires_at),
                        row.state_version,
                        row.state,
                        row.activation_id,
                        row.opportunity_id,
                        row.opportunity_hash,
                        as_utc(row.last_input_watermark) if row.last_input_watermark else None,
                        previous,
                    )
                )
        return result

    @staticmethod
    def _evaluation(row):
        raw = json.loads(row.payload_json)
        cls = EventOpportunityEvaluationV2 if raw.get("schema_version") == "event-opportunity-evaluation.v2" else EventOpportunityEvaluation
        value = cls.model_validate(raw)
        if (
            row.evaluation_id != value.evaluation_id
            or row.monitor_id != value.monitor_id
            or row.event_key != value.event_key
            or as_utc(row.tick_at) != value.tick_at
            or canonical_hash(value.model_dump(mode="json")) != row.payload_hash
            or canonical_hash(value.model_dump(mode="json", exclude={"evaluation_hash"}))
            != value.evaluation_hash
        ):
            raise ValueError("event_monitor_evaluation_original_invalid")
        return value

    def _evaluate(self, claim, event, market, forecast_id, instrument, tick, asof, now, input_ref):
        activation, opportunity = event.activation, event.opportunity
        activation_id = activation.realization_id if activation else None
        scenario = activation.scenario_id if activation else None
        opp_id = opportunity.snapshot_id if opportunity else None
        opp_hash = opportunity.payload_hash if opportunity else None
        reasons = []
        per = []
        confirmation = None
        state = claim.state
        data_status = "available"
        until = event.scheduled_at + timedelta(hours=6)
        if claim.state == "triggered":
            until = event.scheduled_at + timedelta(hours=24, seconds=60)
        changed = (claim.activation_id and activation_id != claim.activation_id) or (
            claim.opportunity_id
            and (opp_id != claim.opportunity_id or opp_hash != claim.opportunity_hash)
        )
        if event.error == "event_monitor_forecast_superseded":
            state, reasons = "invalidated", [event.error]
        elif claim.state == "triggered" and claim.previous and not isinstance(claim.previous, EventOpportunityEvaluationV2):
            state, reasons = "invalidated", ["event_monitor_confirmation_protocol_changed"]
        elif now >= until:
            state, reasons = "expired", ["event_monitor_window_expired"]
        elif changed:
            state, reasons = "invalidated", ["event_monitor_bound_original_changed"]
        elif asof < event.scheduled_at:
            state, data_status, reasons = (
                "waiting",
                "awaiting_release",
                ["event_monitor_awaiting_release"],
            )
        elif event.error or not activation or not opportunity:
            state = "triggered" if claim.state == "triggered" else "missing"
            data_status = "missing"
            reasons = [
                event.error
                or (
                    "event_monitor_actual_release_missing"
                    if not activation
                    else "event_monitor_preplan_missing"
                )
            ]
        elif asof < max(opportunity.entry_valid_from, activation.known_at):
            state, reasons = "waiting", ["event_monitor_awaiting_confirmation_window"]
        else:
            selected = [p for p in opportunity.plans if p.scenario_id == scenario]
            if len(selected) != 1:
                raise ValueError("event_monitor_unique_scenario_required")
            policy = selected[0].policy
            if policy.scope_id != spot_scope(instrument).scope_id or policy.scenario_id != scenario:
                raise ValueError("event_monitor_policy_binding_invalid")
            confirmation = evaluate_event_confirmation(
                policy, position_context=claim.position, scheduled_at=event.scheduled_at,
                as_of=asof, activation_known_at=activation.known_at, candles=market.candles,
                previous=claim.previous.per_condition
                if isinstance(claim.previous, EventOpportunityEvaluationV2) and not changed else (),
            )
            per = list(confirmation.per_condition)
            entry, exit_condition, invalid = confirmation.entry, confirmation.exit, confirmation.invalidation
            event_name = select_plan_event(
                state="triggered" if claim.state == "triggered" else "armed",
                action="enter_long" if claim.position == "flat" else "reduce_long",
                at=now,
                valid_until=until,
                qualification="research_trial",
                invalidation=invalid,
                exit_condition=exit_condition,
                entry_condition=entry,
            )
            if event_name == "invalidate":
                state, reasons = "invalidated", ["event_monitor_invalidation_confirmed"]
            elif event_name == "expire":
                state, reasons = "expired", ["event_monitor_window_expired"]
            elif event_name == "settle":
                state, reasons = "expired", ["event_monitor_exit_condition_confirmed"]
            elif event_name == "trigger":
                state, reasons = "triggered", ["event_monitor_entry_confirmed"]
            elif (
                market.error
                or any(p.raw_truth == "unknown" or p.status == "error" for p in per)
                or entry.truth == "unknown"
            ):
                state = "triggered" if claim.state == "triggered" else "missing"
                data_status, reasons = (
                    "missing",
                    [market.error or "event_monitor_condition_inputs_missing"],
                )
            else:
                state = "triggered" if claim.state == "triggered" else "waiting"
                reasons = ["event_monitor_awaiting_price_confirmation"]
            reasons.extend(p.reason for p in per if p.reason)
        value = EventOpportunityEvaluationV2(
            evaluation_id="event-eval-" + canonical_hash([claim.monitor_id, tick.isoformat()])[:48],
            monitor_id=claim.monitor_id,
            event_key=event.event_key,
            forecast_id=forecast_id,
            instrument=instrument,
            position_context=claim.position,
            activation_id=activation_id,
            opportunity_id=opp_id,
            opportunity_hash=opp_hash,
            scenario_id=scenario,
            tick_at=tick,
            as_of=asof,
            evaluated_at=now,
            generation=claim.generation,
            state_version_before=claim.version,
            state_version_after=claim.version + 1,
            state_before=claim.state,
            state_after=state,
            data_status=data_status,
            reason_codes=tuple(dict.fromkeys(reasons)),
            per_condition=tuple(per),
            input_ref=input_ref,
            input_hash=market.input_hash if market else None,
            evaluation_hash="0" * 64,
            confirmation_policy_hash=confirmation.policy_hash if confirmation else None,
            rule_summary=confirmation.rule_summary if confirmation else PENDING_RULE_SUMMARY,
            strategy_probability_gap=PROBABILITY_GAP,
            confirmation_inputs=confirmation.inputs if confirmation else None,
        )
        return value.model_copy(
            update={
                "evaluation_hash": canonical_hash(
                    value.model_dump(mode="json", exclude={"evaluation_hash"})
                )
            }
        )

    def process_event(self, forecast_id: str, instrument: str, *, checkpoint=None) -> int:
        spot_scope(instrument)
        asof = aware_utc(self.clock())
        tick = asof.replace(second=0, microsecond=0)
        prepared = prepare_originals(
            self.database,
            lambda session: self._prepare_event(session, forecast_id, instrument, asof),
            checkpoint=checkpoint,
        )
        event = prepared.value
        claims = self._claim(forecast_id, instrument, event, tick)
        if not claims:
            return 0
        prepared_market = None
        if (
            event.activation
            and event.opportunity
            and not event.error
            and asof >= event.opportunity.entry_valid_from
        ):
            prepared_market = prepare_originals(
                self.database,
                lambda session: self._prepare_market(session, spot_scope(instrument), event.scheduled_at, asof),
                checkpoint=checkpoint,
            )
            market = prepared_market.value
        else:
            market = _missing_market(
                spot_scope(instrument).scope_id,
                asof,
                event.error or "event_monitor_awaiting_release_or_inputs",
            )
        reference, digest = archive_feature(market.payload_json, self.feature_root)
        if digest != market.input_hash:
            raise ValueError("event_monitor_input_hash_mismatch")
        completed = 0
        for claim in claims:
            if checkpoint:
                checkpoint()
            now = aware_utc(self.clock())
            evaluation = self._evaluate(
                claim, event, market, forecast_id, instrument, tick, asof, now, reference
            )
            with self.database.session() as session:
                begin_immediate(session)
                row = session.get(
                    EventOpportunityMonitorRecord, claim.monitor_id, populate_existing=True
                )
                if (
                    row is None
                    or row.lease_owner != claim.owner
                    or row.lease_generation != claim.generation
                    or row.state_version != claim.version
                    or not row.lease_expires_at
                    or as_utc(row.lease_expires_at) <= aware_utc(self.clock())
                ):
                    continue
                prepared.verify(session, checkpoint=checkpoint)
                if prepared_market:
                    prepared_market.verify(session, checkpoint=checkpoint)
                if hashlib.sha256(Path(reference).read_bytes()).hexdigest() != market.input_hash:
                    raise ValueError("event_monitor_input_original_changed")
                for ref, raw_hash in market.raw_originals:
                    if hashlib.sha256(self.store.archive.read(ref)).hexdigest() != raw_hash:
                        raise ValueError("event_monitor_raw_original_changed")
                for ref, raw_hash in event.raw_originals:
                    if hashlib.sha256(self.archive.read(ref)).hexdigest() != raw_hash:
                        raise ValueError("event_monitor_raw_original_changed")
                session.add(
                    EventOpportunityEvaluationRecord(
                        evaluation_id=evaluation.evaluation_id,
                        monitor_id=claim.monitor_id,
                        event_key=event.event_key,
                        tick_at=tick,
                        payload_json=_json(evaluation.model_dump(mode="json")),
                        payload_hash=canonical_hash(evaluation.model_dump(mode="json")),
                        created_at=now,
                    )
                )
                row.activation_id, row.opportunity_id = (
                    evaluation.activation_id,
                    evaluation.opportunity_id,
                )
                row.opportunity_hash, row.scenario_id = (
                    evaluation.opportunity_hash,
                    evaluation.scenario_id,
                )
                row.state, row.state_version = (
                    evaluation.state_after,
                    evaluation.state_version_after,
                )
                row.latest_evaluation_id, row.updated_at = evaluation.evaluation_id, now
                if evaluation.confirmation_inputs and evaluation.confirmation_inputs.closed_bar_at:
                    bar = evaluation.confirmation_inputs.closed_bar_at
                    if row.last_input_watermark is None or bar > as_utc(row.last_input_watermark):
                        row.last_input_watermark = bar
                row.next_due = (
                    None
                    if row.state in ("invalidated", "expired")
                    else event.scheduled_at + timedelta(seconds=60)
                    if asof < event.scheduled_at
                    else now + timedelta(seconds=60)
                )
                row.lease_owner = row.lease_expires_at = None
                user_visible_transition = (
                    evaluation.state_after != claim.state
                    and evaluation.state_after in ("triggered", "invalidated", "expired")
                    # A newer forecast retires the old monitor for audit continuity.
                    # It is not a new market signal and must not generate four
                    # BTC/ETH x flat/long notifications on every forecast revision.
                    and "event_monitor_forecast_superseded" not in evaluation.reason_codes
                )
                if user_visible_transition:
                    labels = {
                        "triggered": "条件已触发",
                        "invalidated": "预案已失效",
                        "expired": "预案已到期",
                    }
                    label = labels[evaluation.state_after]
                    position = "空仓" if claim.position == "flat" else "已有多仓"
                    enqueue_event_notice_in_session(
                        session,
                        database=self.database,
                        event_key=event.event_key,
                        reference_period=event.reference_period,
                        instrument=instrument,
                        position_context=claim.position,
                        transition=evaluation.state_after,
                        forecast_id=forecast_id,
                        opportunity_id=evaluation.opportunity_id,
                        activation_id=evaluation.activation_id,
                        evaluation_id=evaluation.evaluation_id,
                        reason_codes=evaluation.reason_codes,
                        created_at=now,
                        dedupe_key=(
                            f"event:{claim.monitor_id}:"
                            f"{evaluation.opportunity_id}:{evaluation.state_after}:local"
                        ),
                        subject=(
                            f"{instrument} · CPI {event.reference_period} · {position} · {label}"
                        ),
                        body=_notice_body(evaluation, position),
                    )
                # Recheck originals after intentional writes, then retain a final lease fence.
                prepared.verify_after_writes(session, checkpoint=checkpoint)
                if prepared_market:
                    prepared_market.verify_after_writes(session, checkpoint=checkpoint)
                if aware_utc(self.clock()) >= claim.expires_at:
                    raise ValueError("event_monitor_fence_lost")
            completed += 1
        return completed

    def process_due(self, *, max_events=4, checkpoint=None) -> int:
        now = aware_utc(self.clock())
        with self.database.session() as session:
            due = session.scalars(
                select(EventOpportunityMonitorRecord)
                .where(
                    EventOpportunityMonitorRecord.next_due <= now,
                    or_(
                        EventOpportunityMonitorRecord.lease_expires_at.is_(None),
                        EventOpportunityMonitorRecord.lease_expires_at <= now,
                    ),
                )
                .order_by(EventOpportunityMonitorRecord.next_due)
                .limit(max_events * 2)
            ).all()
            targets = list(dict.fromkeys((r.forecast_id, r.instrument) for r in due))
            forecasts = session.scalars(
                select(EventScenarioForecastRecord)
                .where(
                    EventScenarioForecastRecord.published_at <= now,
                    EventScenarioForecastRecord.published_at
                    < EventScenarioForecastRecord.scheduled_at,
                    EventScenarioForecastRecord.scheduled_at
                    >= now - timedelta(hours=24, seconds=60),
                )
                .order_by(
                    EventScenarioForecastRecord.scheduled_at,
                    EventScenarioForecastRecord.published_at.desc(),
                )
                .limit(max_events * 3)
            ).all()
            seen = set()
            for forecast in forecasts:
                if forecast.event_key in seen:
                    continue
                seen.add(forecast.event_key)
                older = session.scalars(
                    select(EventOpportunityMonitorRecord).where(
                        EventOpportunityMonitorRecord.event_key == forecast.event_key,
                        EventOpportunityMonitorRecord.forecast_id != forecast.forecast_id,
                        EventOpportunityMonitorRecord.state.not_in(("invalidated", "expired")),
                    )
                ).all()
                for old in older:
                    target = (old.forecast_id, old.instrument)
                    if target not in targets:
                        targets.append(target)
                for instrument in ("BTC-USDT", "ETH-USDT"):
                    target = (forecast.forecast_id, instrument)
                    existing = session.scalar(
                        select(EventOpportunityMonitorRecord.monitor_id)
                        .where(
                            EventOpportunityMonitorRecord.forecast_id == forecast.forecast_id,
                            EventOpportunityMonitorRecord.instrument == instrument,
                        )
                        .limit(1)
                    )
                    if existing is None and target not in targets:
                        targets.append(target)
        total = 0
        for forecast_id, instrument in targets[:max_events]:
            if checkpoint:
                checkpoint()
            total += self.process_event(forecast_id, instrument, checkpoint=checkpoint)
        return total

    def get_status(self, forecast_id, instrument):
        with self.database.session() as session:
            rows = session.scalars(
                select(EventOpportunityMonitorRecord)
                .where(
                    EventOpportunityMonitorRecord.forecast_id == forecast_id,
                    EventOpportunityMonitorRecord.instrument == instrument,
                )
                .order_by(EventOpportunityMonitorRecord.position_context)
            ).all()
            result = []
            for row in rows:
                previous = (
                    session.get(EventOpportunityEvaluationRecord, row.latest_evaluation_id)
                    if row.latest_evaluation_id
                    else None
                )
                evaluated = self._evaluation(previous) if previous else None
                current_protocol = isinstance(evaluated, EventOpportunityEvaluationV2)
                result.append(
                    dict(
                        monitor_id=row.monitor_id,
                        forecast_id=row.forecast_id,
                        instrument=row.instrument,
                        position_context=row.position_context,
                        state=row.state,
                        state_version=row.state_version,
                        activation_id=row.activation_id,
                        opportunity_id=row.opportunity_id,
                        scenario_id=row.scenario_id,
                        next_due=as_utc(row.next_due) if row.next_due else None,
                        confirmation_protocol=evaluated.confirmation_protocol if current_protocol else None,
                        strategy_target=evaluated.strategy_target if current_protocol else None,
                        rule_summary=evaluated.rule_summary if current_protocol else "旧版监控历史记录；尚无公告锚定协议回执。",
                        strategy_probability_status="unknown",
                        strategy_probability_gap=PROBABILITY_GAP,
                        latest_evaluation=evaluated.model_dump(mode="json") if evaluated else None,
                    )
                )
            return result

    def list_notices(
        self,
        *,
        event_key=None,
        owner_id=None,
        limit=50,
        include_superseded=False,
    ):
        requested_limit = min(max(limit, 1), 100)
        with self.database.session() as session:
            query = select(EventOpportunityNoticeRecord)
            if event_key:
                query = query.where(EventOpportunityNoticeRecord.event_key == event_key)
            if not include_superseded:
                # Old releases wrote revision-retirement notices before that
                # transition was made audit-only.  Filter those immutable rows
                # before applying the user-facing limit so a long supersession
                # history cannot hide an older actionable notice.
                query = query.where(
                    EventOpportunityNoticeRecord.payload_json.not_like(
                        '%"event_monitor_forecast_superseded"%'
                    )
                )
            rows = session.scalars(
                query.order_by(
                    EventOpportunityNoticeRecord.created_at.desc(),
                    EventOpportunityNoticeRecord.notice_id,
                ).limit(requested_limit)
            ).all()
            result = []
            for row in rows:
                notice = _notice(row)
                value = notice.model_dump(mode="json")
                outbox = session.get(OutboxRecord, row.outbox_id)
                query = select(EventOpportunityCommandRecord).where(
                    EventOpportunityCommandRecord.notice_id == row.notice_id
                )
                if owner_id is not None:
                    query = query.where(EventOpportunityCommandRecord.owner_id == owner_id)
                commands = (
                    session.scalars(query.order_by(EventOpportunityCommandRecord.recorded_at)).all()
                    if owner_id
                    else []
                )
                value.update(
                    outbox_id=row.outbox_id,
                    delivery_status=outbox.dispatch_status if outbox else "missing",
                    provider_accepted_at=as_utc(outbox.provider_accepted_at)
                    if outbox and outbox.provider_accepted_at
                    else None,
                    display_at=next(
                        (as_utc(c.recorded_at) for c in commands if c.command == "display"), None
                    ),
                    ack_at=next(
                        (as_utc(c.recorded_at) for c in commands if c.command == "ack"), None
                    ),
                )
                result.append(value)
            return result

    def record_command(self, notice_id, *, owner_id, request_id, command):
        if (
            command not in ("display", "ack")
            or not owner_id
            or not request_id
            or max(len(owner_id), len(request_id)) > 128
        ):
            raise ValueError("event_notice_command_invalid")
        with self.database.session() as session:
            begin_immediate(session)
            notice = session.get(EventOpportunityNoticeRecord, notice_id)
            if notice is None:
                return None
            _notice(notice)
            prior = session.scalar(
                select(EventOpportunityCommandRecord).where(
                    EventOpportunityCommandRecord.notice_id == notice_id,
                    EventOpportunityCommandRecord.owner_id == owner_id,
                    EventOpportunityCommandRecord.request_id == request_id,
                )
            )
            if prior is not None:
                if prior.command != command:
                    raise ValueError("event_notice_command_conflict")
                return dict(
                    command_id=prior.command_id,
                    notice_id=notice_id,
                    command=prior.command,
                    recorded_at=as_utc(prior.recorded_at),
                )
            ident = "event-command-" + canonical_hash([notice_id, owner_id, request_id])[:48]
            at = aware_utc(self.clock())
            session.add(
                EventOpportunityCommandRecord(
                    command_id=ident,
                    notice_id=notice_id,
                    owner_id=owner_id,
                    request_id=request_id,
                    command=command,
                    recorded_at=at,
                )
            )
            return dict(command_id=ident, notice_id=notice_id, command=command, recorded_at=at)
