"""Deterministic Action monitoring from committed, authenticated closed bars.

The service owns claim/read/complete transactions. It fetches no provider data,
calls no model, and never interprets a condition trigger as an account fill.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..decision.action_features import (
    ACTION_MONITOR_MANIFEST_REF,
    ActionFeatureSnapshot,
    action_feature_manifest,
    action_feature_window,
    build_action_features,
    condition_sample_interval_seconds,
)
from ..decision.action_policy import (
    ActionConditionResult,
    confirmed_bar_streak,
    evaluate_condition_expression,
    select_plan_event,
    transition_action_plan,
    validate_condition_expression,
)
from ..decision.action_research_policy import research_action_catalog
from ..persistence.action_repository import (
    ActionMonitorClaim,
    ActionRepository,
    ActionRepositoryError,
)
from ..persistence.db import Database, utcnow
from ..persistence.original_preparation import PreparedOriginals, begin_immediate, prepare_originals
from ..ports.action_monitor import ConditionEvaluationV2, PerConditionResultV2
from ..ports.action_plan import ActionPlanHead, ActionPlanRevision, Condition, ConditionEvaluation
from ..ports.market_path import MarketPathScope, aware_utc, canonical_hash, spot_scope
from ..ports.sources import make_notification_envelope
from .market_path_store import MarketPathStore, archive_feature
from .outbox import OutboxService


@dataclass(frozen=True)
class _PlanInputs:
    previous: ConditionEvaluation | ConditionEvaluationV2 | None


@dataclass(frozen=True)
class _MarketInputs:
    snapshot: ActionFeatureSnapshot | None
    payload_json: str
    input_hash: str
    raw_originals: tuple[tuple[str, str], ...]
    error: str | None = None


def _body(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _bindings(expression: dict[str, Any]) -> tuple[tuple[str, str, str], ...]:
    if expression["op"] == "compare":
        return ((expression["feature"], expression["scope_id"], expression["unit"]),)
    return tuple(
        dict.fromkeys(binding for child in expression["args"] for binding in _bindings(child))
    )


def _scope_and_policy(revision: ActionPlanRevision) -> tuple[MarketPathScope, dict[str, Any]]:
    for instrument in ("BTC-USDT", "ETH-USDT"):
        scope = spot_scope(instrument)
        if revision.scope_id == scope.scope_id:
            break
    else:
        raise ValueError("action_monitor_scope_unsupported")
    catalog = research_action_catalog(instrument)
    target = catalog["targets"][revision.horizon]
    if (
        revision.cost_policy_ref,
        revision.cost_policy_hash,
        revision.target_spec_ref,
        revision.target_spec_hash,
    ) != (
        catalog["cost_policy"]["ref"],
        catalog["cost_policy"]["hash"],
        target["ref"],
        target["hash"],
    ):
        raise ValueError("action_monitor_frozen_policy_mismatch")
    bindings = [
        item.hash for item in revision.basis_refs if item.ref == ACTION_MONITOR_MANIFEST_REF
    ]
    if bindings != [canonical_hash(action_feature_manifest(instrument))]:
        raise ValueError("action_monitor_manifest_missing_or_changed")
    allowed = frozenset(
        (row["feature"], row["scope_id"], row["unit"]) for row in catalog["features"]
    )
    for purpose in ("entry", "exit", "invalidation"):
        for condition in getattr(revision, purpose):
            if condition.purpose != purpose or condition.calendar_ref != catalog["calendar_ref"]:
                raise ValueError("action_monitor_condition_binding_mismatch")
            if condition.evaluation_seconds != 60:
                raise ValueError("action_monitor_condition_cadence_unsupported")
            expression = condition.expression.model_dump(mode="json")
            validate_condition_expression(expression, allowed_features=allowed)
            actual = set(_bindings(expression))
            declared = {
                (item.metric, item.scope, item.unit) for item in condition.input_requirements
            }
            if declared != actual or any(
                item.source != "okx" for item in condition.input_requirements
            ):
                raise ValueError("action_monitor_condition_inputs_mismatch")
    return scope, catalog


def _unavailable(scope_id: str, as_of: datetime, reason: str) -> _MarketInputs:
    body = {
        "schema_version": "action-monitor-unavailable.v1",
        "scope_id": scope_id,
        "as_of": as_of.isoformat(),
        "reason": reason,
    }
    return _MarketInputs(None, _body(body), canonical_hash(body), (), reason)


def _combine(values: list[ActionConditionResult], *, all_required: bool) -> ActionConditionResult:
    if not values:
        return ActionConditionResult("unknown", "ok", ("action_condition_group_empty",))
    reasons = tuple(dict.fromkeys(reason for value in values for reason in value.reasons))
    refs = tuple(dict.fromkeys(ref for value in values for ref in value.input_refs))
    # Independent protection conditions remain actionable when one has a
    # confirmed fact. Errors within an individual expression still fail closed;
    # every error also remains in the immutable per-condition evaluation.
    if not all_required and any(
        value.status == "ok" and value.truth == "true" for value in values
    ):
        return ActionConditionResult("true", "ok", reasons, refs)
    if any(value.status == "error" for value in values):
        return ActionConditionResult("unknown", "error", reasons, refs)
    truths = {value.truth for value in values}
    if all_required:
        truth = "false" if "false" in truths else "unknown" if "unknown" in truths else "true"
    else:
        truth = "true" if "true" in truths else "unknown" if "unknown" in truths else "false"
    return ActionConditionResult(truth, "ok", reasons, refs)


class ActionMonitorService:
    def __init__(
        self,
        database: Database,
        store: MarketPathStore,
        *,
        feature_root: Path,
        clock: Callable[[], datetime] = utcnow,
        lease_seconds: int = 90,
    ) -> None:
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ValueError("action_monitor_lease_invalid")
        if store.database is not database:
            raise ValueError("action_monitor_store_database_mismatch")
        self.database, self.store = database, store
        self.feature_root, self.clock, self.lease_seconds = Path(feature_root), clock, lease_seconds

    def claim(self, plan_id: str, *, as_of: datetime | None = None) -> ActionMonitorClaim | None:
        now = aware_utc(self.clock())
        with self.database.session() as session:
            begin_immediate(session)
            return ActionRepository(session).claim_monitor(
                plan_id,
                owner="action-monitor:" + uuid.uuid4().hex,
                now=now,
                as_of=as_of,
                lease_seconds=self.lease_seconds,
            )

    def _prepare_plan(self, session: Session, claim: ActionMonitorClaim) -> _PlanInputs:
        repository = ActionRepository(session)
        if (
            repository.get_head(claim.head.plan_id) != claim.head
            or repository.get_revision(claim.revision.revision_id) != claim.revision
        ):
            raise ActionRepositoryError("action_monitor_fence_lost")
        previous = repository.get_latest_evaluation(
            claim.head.plan_id,
            revision_id=claim.revision.revision_id,
        )
        return _PlanInputs(previous)

    def _prepare_market(
        self,
        session: Session,
        scope: MarketPathScope,
        as_of: datetime,
    ) -> _MarketInputs:
        originals: dict[str, str] = {}

        def original(ref: str, digest: str) -> None:
            if ref in originals and originals[ref] != digest:
                raise ValueError("action_monitor_original_identity_changed")
            originals[ref] = digest

        try:
            start, end = action_feature_window(as_of)
            bars = self.store.load_in_session(
                session,
                scope,
                start_at=start,
                end_at=end,
                as_of=as_of,
                on_original=original,
            )
            frame = build_action_features(scope, bars, as_of=as_of)
        except (ValueError, OSError) as error:
            code = str(error)
            if not code.startswith(
                ("market_store_", "action_feature_", "action_monitor_", "raw_capture_")
            ):
                code = "action_monitor_original_unavailable"
            missing = _unavailable(scope.scope_id, as_of, code)
            return _MarketInputs(
                None,
                missing.payload_json,
                missing.input_hash,
                tuple(sorted(originals.items())),
                code,
            )
        return _MarketInputs(
            frame, frame.payload_json, frame.input_hash, tuple(sorted(originals.items()))
        )

    @staticmethod
    def _condition(
        condition: Condition,
        *,
        claim: ActionMonitorClaim,
        previous: PerConditionResultV2 | None,
        market: _MarketInputs,
        catalog: dict[str, Any] | None,
        evaluated_at: datetime,
    ) -> PerConditionResultV2:
        expression = condition.expression.model_dump(mode="json")
        frame = market.snapshot
        features = frame.feature_map() if frame is not None else {}
        if evaluated_at >= claim.head.valid_until:
            raw = ActionConditionResult("unknown", "ok", ("action_plan_expired",))
        elif market.error is not None or catalog is None:
            raw = ActionConditionResult(
                "unknown", "error", (market.error or "action_monitor_policy_missing",)
            )
        elif evaluated_at >= condition.valid_until:
            raw = ActionConditionResult("unknown", "ok", ("action_condition_expired",))
        else:
            raw = evaluate_condition_expression(
                expression,
                features,
                as_of=claim.as_of,
                allowed_features=frozenset(
                    (row["feature"], row["scope_id"], row["unit"]) for row in catalog["features"]
                ),
            )
        referenced = _bindings(expression)
        inputs = [
            features[(name, scope)] for name, scope, _ in referenced if (name, scope) in features
        ]
        if raw.status == "ok" and any(
            (claim.as_of - value.observed_at).total_seconds() > condition.max_age_seconds
            for value in inputs
        ):
            raw = ActionConditionResult(
                "unknown", "ok", ("action_condition_input_stale",), raw.input_refs
            )
        sampled = [
            value.observed_at
            for value in inputs
            if value.quality == "accepted"
            and value.value is not None
            and value.known_at <= claim.as_of
        ]
        sample_at = max(sampled) if sampled else None
        last = previous.last_bar_at if previous is not None else claim.head.last_input_watermark
        count = previous.confirmation_count if previous is not None else 0
        if sample_at is not None and last is not None and sample_at < last:
            raw = ActionConditionResult(
                "unknown", "ok", ("action_monitor_input_time_regressed",), raw.input_refs
            )
        if raw.truth != "true" or raw.status == "error":
            count = 0
        elif sample_at is not None:
            count, _ = confirmed_bar_streak(
                count,
                last,
                sample_at,
                raw.truth,
                required=condition.min_fresh_samples,
                interval_seconds=condition_sample_interval_seconds(
                    tuple(name for name, _, _ in referenced)
                ),
            )
        else:
            count = 0
        if sample_at is not None and (last is None or sample_at > last):
            last = sample_at
        confirmed = (
            raw.truth == "true" and raw.status == "ok" and count >= condition.min_fresh_samples
        )
        truth = "true" if confirmed else "false" if raw.truth == "true" else raw.truth
        return PerConditionResultV2.model_validate(
            {
                "id": condition.id,
                "truth": truth,
                "raw_truth": raw.truth,
                "status": raw.status,
                "reason": ";".join(raw.reasons)[:2000] or None,
                "input_refs": list(raw.input_refs),
                "confirmation_count": count,
                "last_bar_at": last,
                "confirmed": confirmed,
            }
        )

    def _evaluate(
        self,
        claim: ActionMonitorClaim,
        previous: ConditionEvaluation | ConditionEvaluationV2 | None,
        market: _MarketInputs,
        *,
        catalog: dict[str, Any] | None,
        input_ref: str,
        now: datetime,
    ) -> tuple[ConditionEvaluationV2, ActionPlanHead]:
        revision, head = claim.revision, claim.head
        prior: dict[str, PerConditionResultV2] = {}
        if (
            isinstance(previous, ConditionEvaluationV2)
            and previous.revision_id == revision.revision_id
            and previous.state_version_after == head.state_version
        ):
            prior = {item.id: item for item in previous.per_condition}
        conditions = (*revision.entry, *revision.exit, *revision.invalidation)
        results = [
            self._condition(
                c,
                claim=claim,
                previous=prior.get(c.id),
                market=market,
                catalog=catalog,
                evaluated_at=now,
            )
            for c in conditions
        ]
        index = {
            item.id: ActionConditionResult(
                item.truth,
                item.status,
                (item.reason,) if item.reason else (),
                tuple(item.input_refs),
            )
            for item in results
        }
        entry = _combine([index[c.id] for c in revision.entry], all_required=True)
        exit_condition = _combine([index[c.id] for c in revision.exit], all_required=False)
        invalidation = _combine([index[c.id] for c in revision.invalidation], all_required=False)
        expired = now >= head.valid_until
        event = select_plan_event(
            state=head.state,
            action=revision.action,
            at=now,
            valid_until=head.valid_until,
            qualification=revision.advice_qualification,
            invalidation=invalidation,
            exit_condition=exit_condition,
            entry_condition=entry,
        )
        state = (
            "expired"
            if expired and head.state == "proposed"
            else transition_action_plan(head.state, event)
            if event
            else head.state
        )
        terminal = state in {"expired", "invalidated", "completed", "superseded"}
        degraded = (
            market.error is not None
            or any(item.raw_truth == "unknown" or item.status == "error" for item in results)
            or (
                revision.action == "enter_long"
                and any(value.truth == "unknown" for value in (entry, exit_condition, invalidation))
            )
        )
        monitor_state = "stopped" if terminal else "degraded" if degraded else "active"
        watermark = head.last_input_watermark
        if market.snapshot is not None and market.snapshot.closed_bar_at is not None:
            bar_at = market.snapshot.closed_bar_at
            if watermark is None or bar_at > watermark:
                watermark = bar_at
        next_head = ActionPlanHead.model_validate(
            {
                **head.model_dump(mode="python"),
                "state": state,
                "monitor_state": monitor_state,
                "state_version": head.state_version + 1,
                "lease_owner": None,
                "lease_expires_at": None,
                "enter_streak": 0,
                "last_input_watermark": watermark,
                "next_due": None
                if terminal
                else min(now + timedelta(seconds=60), head.valid_until),
            }
        )
        selected = (
            invalidation
            if event == "invalidate"
            else exit_condition
            if head.state == "triggered"
            else entry
        )
        status = (
            "error"
            if ((market.error and not expired) or any(item.status == "error" for item in results))
            else "ok"
        )
        wire = {
            "schema_version": "decision-condition-evaluation.v2",
            "id": "aeval_"
            + canonical_hash([head.plan_id, revision.revision_id, claim.tick_at.isoformat()])[:48],
            "plan_id": head.plan_id,
            "revision_id": revision.revision_id,
            "tick_at": claim.tick_at,
            "as_of": claim.as_of,
            "evaluated_at": now,
            "lease_generation": claim.generation,
            "input_ref": input_ref,
            "input_hash": market.input_hash,
            "truth": "unknown" if status == "error" else selected.truth,
            "status": status,
            "per_condition": [item.model_dump(mode="python") for item in results],
            "state_before": head.state,
            "state_after": state,
            "state_version_before": head.state_version,
            "state_version_after": next_head.state_version,
            "monitor_state_before": head.monitor_state,
            "monitor_state_after": monitor_state,
            "evaluation_hash": "0" * 64,
        }
        draft = ConditionEvaluationV2.model_validate(wire)
        payload = draft.model_dump(mode="json")
        payload["evaluation_hash"] = canonical_hash(
            {k: v for k, v in payload.items() if k != "evaluation_hash"}
        )
        return ConditionEvaluationV2.model_validate(payload), next_head

    def _complete(
        self,
        claim: ActionMonitorClaim,
        plan: PreparedOriginals[_PlanInputs],
        market: _MarketInputs,
        *,
        prepared_market: PreparedOriginals[_MarketInputs] | None,
        catalog: dict[str, Any] | None,
        input_ref: str,
        checkpoint: Callable[[], None] | None,
    ) -> ConditionEvaluationV2:
        with self.database.session() as session:
            begin_immediate(session)
            repository = ActionRepository(session)
            existing = repository.get_evaluation_for_tick(
                claim.head.plan_id,
                revision_id=claim.revision.revision_id,
                tick_at=claim.tick_at,
            )
            if existing is not None:
                if (
                    not isinstance(existing, ConditionEvaluationV2)
                    or existing.input_hash != market.input_hash
                ):
                    raise ActionRepositoryError("action_monitor_tick_conflict")
                return existing
            repository.require_monitor_claim(claim, now=aware_utc(self.clock()))
            plan.verify(session, checkpoint=checkpoint)
            if prepared_market is not None:
                prepared_market.verify(session, checkpoint=checkpoint)
            if hashlib.sha256(Path(input_ref).read_bytes()).hexdigest() != market.input_hash:
                raise ValueError("action_monitor_input_original_changed")
            for reference, digest in market.raw_originals:
                if checkpoint is not None:
                    checkpoint()
                try:
                    actual_hash = hashlib.sha256(self.store.archive.read(reference)).hexdigest()
                except (OSError, ValueError) as error:
                    raise ValueError("action_monitor_raw_original_changed") from error
                if actual_hash != digest:
                    raise ValueError("action_monitor_raw_original_changed")
            # Recheck originals before the intentional mutable-head update.
            plan.verify_after_writes(session, checkpoint=checkpoint)
            if prepared_market is not None:
                prepared_market.verify_after_writes(session, checkpoint=checkpoint)
            now = aware_utc(self.clock())
            repository.require_monitor_claim(claim, now=now)
            evaluation, next_head = self._evaluate(
                claim,
                plan.value.previous,
                market,
                catalog=catalog,
                input_ref=input_ref,
                now=now,
            )
            repository.complete_monitor(claim, evaluation=evaluation, next_head=next_head, now=now)
            if next_head.state != claim.head.state:
                names = {
                    "triggered": "条件已触发",
                    "invalidated": "方案已失效",
                    "expired": "方案已到期",
                    "completed": "方案跟踪已结束",
                }
                position = {"flat": "空仓", "long": "已有多仓", "unknown": "待确认仓位"}
                revision = claim.revision
                scope_parts = revision.scope_id.split(":")
                instrument = scope_parts[3] if len(scope_parts) > 3 else revision.scope_id
                event_conditions = (
                    revision.invalidation
                    if next_head.state == "invalidated"
                    else revision.exit
                    if next_head.state == "completed"
                    else revision.entry
                )
                ids = {item.id for item in event_conditions}
                confirmed_ids = [
                    item.id for item in evaluation.per_condition
                    if item.id in ids and item.confirmed
                ]
                reason = (
                    "有效期已到"
                    if now >= claim.head.valid_until
                    else "已确认条件：" + ", ".join(confirmed_ids)
                )
                message = make_notification_envelope(
                    subject_kind="action_revision",
                    subject_id=revision.revision_id,
                    channel="local",
                    created_at=now,
                    dedupe_key=f"action-revision:{revision.revision_id}:{next_head.state}:local",
                    subject=(f"{instrument} · {revision.horizon} · "
                             f"{position[revision.position_context]} · {names[next_head.state]}"),
                    body=(f"{names[next_head.state]}。{reason}。条件回执：{evaluation.id}。"
                          "这是人工操作方案状态，不代表已成交或已实现盈亏。"),
                )
                OutboxService(self.database).enqueue_subject(message, session=session)
            if checkpoint is not None:
                checkpoint()
            if (
                claim.head.lease_expires_at is None
                or aware_utc(self.clock()) >= claim.head.lease_expires_at
            ):
                raise ActionRepositoryError("action_monitor_fence_lost")
            return evaluation

    def process_due(
        self,
        *,
        max_opportunities: int = 4,
        max_scopes: int = 2,
        checkpoint: Callable[[], None] | None = None,
    ) -> int:
        """One bounded batch; unprocessed heads keep their original due time."""
        as_of = aware_utc(self.clock())
        with self.database.session() as session:
            ids = ActionRepository(session).list_due_monitor_plan_ids(
                now=as_of,
                max_opportunities=max_opportunities,
                max_scopes=max_scopes,
            )
        cache: dict[str, PreparedOriginals[_MarketInputs]] = {}
        completed = 0
        for plan_id in ids:
            if checkpoint is not None:
                checkpoint()
            claim = self.claim(plan_id, as_of=as_of)
            if claim is None:
                continue
            prepared_plan = prepare_originals(
                self.database,
                lambda session, selected=claim: self._prepare_plan(session, selected),
                checkpoint=checkpoint,
            )
            prepared_market = None
            catalog = None
            if aware_utc(self.clock()) >= claim.head.valid_until:
                market = _unavailable(claim.revision.scope_id, as_of, "action_plan_expired")
            else:
                try:
                    scope, catalog = _scope_and_policy(claim.revision)
                except ValueError as error:
                    market = _unavailable(claim.revision.scope_id, as_of, str(error))
                else:
                    if scope.scope_id not in cache:
                        cache[scope.scope_id] = prepare_originals(
                            self.database,
                            lambda session, selected=scope: self._prepare_market(
                                session, selected, as_of
                            ),
                            checkpoint=checkpoint,
                        )
                    prepared_market = cache[scope.scope_id]
                    market = prepared_market.value
            reference, digest = archive_feature(market.payload_json, self.feature_root)
            if digest != market.input_hash:
                raise ValueError("action_monitor_input_hash_mismatch")
            try:
                self._complete(
                    claim,
                    prepared_plan,
                    market,
                    prepared_market=prepared_market,
                    catalog=catalog,
                    input_ref=reference,
                    checkpoint=checkpoint,
                )
            except ActionRepositoryError as error:
                if error.code != "action_monitor_fence_lost":
                    raise
            else:
                completed += 1
        return completed
