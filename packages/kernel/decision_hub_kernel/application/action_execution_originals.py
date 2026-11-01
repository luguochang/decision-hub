"""Private application draft: authenticate an evaluation, never create a fill.

All public inputs are identities. Returned dataclasses are internal read products,
not HTTP DTOs or execution permission. The consuming service must use
prepare_originals, recheck these bytes and its mutable fences in the writer.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from pathlib import Path
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application import action_monitor as monitor
from packages.kernel.decision_hub_kernel.application.action_review import (
    read_published_branch_in_session,
)
from packages.kernel.decision_hub_kernel.application.market_path_store import MarketPathStore
from packages.kernel.decision_hub_kernel.application.research_run_scope import (
    read_run_spot_admission_in_session,
)
from packages.kernel.decision_hub_kernel.decision.action_features import (
    ActionFeatureSnapshot,
    action_feature_window,
    build_action_features,
)
from packages.kernel.decision_hub_kernel.persistence.action_records import (
    ActionCommandReceiptRecord,
    ActionConditionEvaluationRecord,
)
from packages.kernel.decision_hub_kernel.persistence.action_repository import (
    ActionMonitorClaim,
    ActionRepository,
    StoredCommandReceipt,
)
from packages.kernel.decision_hub_kernel.persistence.db import as_utc
from packages.kernel.decision_hub_kernel.ports.action_monitor import ConditionEvaluationV2
from packages.kernel.decision_hub_kernel.ports.action_plan import (
    ActionPlanHead,
    ActionPlanRevision,
    Condition,
    DecisionSnapshot,
    Opportunity,
)
from packages.kernel.decision_hub_kernel.ports.market_path import (
    aware_utc,
    canonical_hash,
    decimal_text,
)


@dataclass(frozen=True)
class ExecutionU:
    value: Decimal
    unit: str
    measurement_end: datetime
    knowledge_cutoff: datetime
    known_at: datetime
    payload_json: str
    payload_hash: str


@dataclass(frozen=True)
class EvaluationOriginals:
    revision: ActionPlanRevision
    evaluation: ConditionEvaluationV2
    phase: str
    action: str
    reason: str
    condition_ids: tuple[str, ...]
    input_known_at: datetime
    sample_bar_open_at: datetime
    frame: ActionFeatureSnapshot
    risk: ExecutionU | None
    raw_originals: tuple[tuple[str, str], ...]
    feature_originals: tuple[tuple[str, str], ...]

    def verify_bytes(self, store: MarketPathStore) -> None:
        for ref, expected in self.raw_originals:
            if hashlib.sha256(store.archive.read(ref)).hexdigest() != expected:
                raise ValueError("action_execution_raw_original_changed")
        for ref, expected in self.feature_originals:
            if hashlib.sha256(Path(ref).read_bytes()).hexdigest() != expected:
                raise ValueError("action_execution_feature_original_changed")


@dataclass(frozen=True)
class PublicationOriginals:
    opportunity: Opportunity
    decision: DecisionSnapshot
    publication: StoredCommandReceipt
    admission_ref: str
    admission_hash: str
    admission_at: datetime
    display: StoredCommandReceipt | None


def read_publication_in_session(
    session: Session,
    *,
    revision: ActionPlanRevision,
    observed_at: datetime,
    channel: str,
    owner_id: str | None,
) -> PublicationOriginals:
    """Reuse W3's exact own-Decision reader, including mixed retained branches."""
    now = aware_utc(observed_at)
    if channel not in {"shadow_engine", "pc_visible"}:
        raise ValueError("action_execution_channel_unsupported")
    if (channel == "pc_visible") != (owner_id is not None):
        raise ValueError("action_execution_owner_binding_invalid")
    repository = ActionRepository(session)
    decision, _, _ = read_published_branch_in_session(session, revision, now)
    opportunity = repository.get_opportunity(revision.opportunity_id)
    receipt = repository.get_command(
        "system:action-publication:v1",
        "publish_" + canonical_hash([decision.run_id, revision.opportunity_id]),
    )
    if opportunity is None or opportunity.status != "decided" or receipt is None:
        raise ValueError("action_execution_publication_missing")
    admission = read_run_spot_admission_in_session(session, decision.run_id)
    if (
        admission is None
        or admission.scope.scope_id != revision.scope_id
        or admission.event_id != opportunity.event_ref
        or not admission.admitted_at <= revision.published_at <= now
    ):
        raise ValueError("action_execution_run_admission_mismatch")
    display = None
    if channel == "pc_visible":
        # Bounded exact-owner query. Details are verified below, not inferred
        # from JSON filters or an outbox delivery flag.
        rows = session.scalars(
            select(ActionCommandReceiptRecord)
            .where(
                ActionCommandReceiptRecord.owner_id == owner_id,
                ActionCommandReceiptRecord.plan_id == revision.plan_id,
                ActionCommandReceiptRecord.operation == "display",
                ActionCommandReceiptRecord.accepted_at >= revision.published_at,
                ActionCommandReceiptRecord.accepted_at <= now,
            )
            .order_by(ActionCommandReceiptRecord.accepted_at, ActionCommandReceiptRecord.request_id)
            .limit(257)
        ).all()
        if len(rows) > 256:
            raise ValueError("action_execution_display_read_budget_exceeded")
        for row in rows:
            original = repository.get_command(row.owner_id, row.request_id)
            if original is None:
                raise ValueError("action_execution_display_original_missing")
            head = ActionPlanHead.model_validate(original.response.get("head"))
            if head.current_revision_id == revision.revision_id:
                if (
                    head.plan_id != revision.plan_id
                    or original.response.get("operation") != "display"
                ):
                    raise ValueError("action_execution_display_binding_invalid")
                display = original
                break
    return PublicationOriginals(
        opportunity,
        decision,
        receipt,
        "research-run-spot-admission:" + decision.run_id,
        canonical_hash(admission.model_dump(mode="json")),
        admission.admitted_at,
        display,
    )


class _EvaluationReader:
    def __init__(
        self,
        session: Session,
        revision: ActionPlanRevision,
        store: MarketPathStore,
        feature_root: Path,
        observed_at: datetime,
        checkpoint: Callable[[], None] | None,
    ) -> None:
        self.session, self.revision, self.store = session, revision, store
        self.root, self.now = feature_root.resolve(), aware_utc(observed_at)
        self.repository = ActionRepository(session)
        self.scope, self.catalog = monitor._scope_and_policy(revision)
        self.checkpoint = checkpoint
        self.frames: dict[str, ActionFeatureSnapshot] = {}
        self.originals: dict[str, str] = {}
        self.files: dict[str, str] = {}
        self.verified: set[tuple[str, str]] = set()

    def check(self) -> None:
        if self.checkpoint:
            self.checkpoint()

    def raw(self, ref: str, digest: str) -> None:
        if ref in self.originals and self.originals[ref] != digest:
            raise ValueError("action_execution_raw_identity_changed")
        self.originals[ref] = digest

    def evaluation(self, row: ActionConditionEvaluationRecord) -> ConditionEvaluationV2:
        value = self.repository.get_evaluation_for_tick(
            self.revision.plan_id,
            revision_id=self.revision.revision_id,
            tick_at=as_utc(row.tick_at),
        )
        if (
            value is None
            or not isinstance(value, ConditionEvaluationV2)
            or value.id != row.evaluation_id
        ):
            raise ValueError("action_execution_v2_evaluation_required")
        if not self.revision.published_at <= value.as_of <= value.evaluated_at <= self.now:
            raise ValueError("action_execution_evaluation_time_invalid")
        return value

    def previous(self, value: ConditionEvaluationV2) -> ConditionEvaluationV2 | None:
        row = self.session.scalar(
            select(ActionConditionEvaluationRecord)
            .where(
                ActionConditionEvaluationRecord.plan_id == self.revision.plan_id,
                ActionConditionEvaluationRecord.revision_id == self.revision.revision_id,
                ActionConditionEvaluationRecord.tick_at < value.tick_at,
            )
            .order_by(ActionConditionEvaluationRecord.tick_at.desc())
            .limit(1)
        )
        return self.evaluation(row) if row else None

    def frame(self, value: ConditionEvaluationV2) -> ActionFeatureSnapshot:
        if value.id in self.frames:
            return self.frames[value.id]
        self.check()
        path = Path(value.input_ref).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError("action_execution_feature_path_outside_root")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != value.input_hash:
            raise ValueError("action_execution_feature_hash_mismatch")
        self.files[str(path)] = value.input_hash
        start, end = action_feature_window(value.as_of)
        bars = self.store.load_in_session(
            self.session,
            self.scope,
            start_at=start,
            end_at=end,
            as_of=value.as_of,
            on_original=self.raw,
        )
        actual = build_action_features(self.scope, bars, as_of=value.as_of)
        if actual.payload_json.encode() != payload or actual.input_hash != value.input_hash:
            raise ValueError("action_execution_feature_recompute_mismatch")
        self.frames[value.id] = actual
        return actual

    def condition(self, value: ConditionEvaluationV2, condition: Condition) -> None:
        """Verify only the confirmation chain needed for this concrete condition.

        Same-sample repetitions remain in the chain; they cannot create counts.
        The hard bound covers every 60-second tick of the largest 72h Action.
        No unbounded historical query or Python recursion is used.
        """
        chain = []
        cursor = value
        for _ in range(4321):
            self.check()
            key = (cursor.id, condition.id)
            if key in self.verified:
                break
            current = next(item for item in cursor.per_condition if item.id == condition.id)
            previous = self.previous(cursor)
            prior = None
            if previous and previous.state_version_after == cursor.state_version_before:
                prior = next(item for item in previous.per_condition if item.id == condition.id)
            frame = self.frame(cursor)
            watermark = self.frame(previous).closed_bar_at if previous else None
            head = ActionPlanHead.model_validate(
                {
                    "schema_version": "decision-action-plan-head.v1",
                    "plan_id": self.revision.plan_id,
                    "current_revision_id": self.revision.revision_id,
                    "state_version": cursor.state_version_before,
                    "state": cursor.state_before,
                    "monitor_state": cursor.monitor_state_before,
                    "next_due": cursor.tick_at,
                    "valid_until": self.revision.valid_until,
                    "lease_owner": "private-proof-replay",
                    "lease_generation": cursor.lease_generation,
                    "lease_expires_at": cursor.evaluated_at + timedelta(seconds=1),
                    "last_input_watermark": watermark,
                    "enter_streak": 0,
                }
            )
            expected = monitor.ActionMonitorService._condition(
                condition,
                claim=ActionMonitorClaim(
                    head,
                    self.revision,
                    "private-proof-replay",
                    cursor.lease_generation,
                    cursor.tick_at,
                    cursor.as_of,
                ),
                previous=prior,
                market=monitor._MarketInputs(frame, frame.payload_json, frame.input_hash, ()),
                catalog=self.catalog,
                evaluated_at=cursor.evaluated_at,
            )
            if expected != current:
                raise ValueError("action_execution_condition_recompute_mismatch")
            chain.append(key)
            if current.confirmation_count <= 1:
                break
            if prior is None or previous is None:
                raise ValueError("action_execution_confirmation_original_missing")
            cursor = previous
        else:
            raise ValueError("action_execution_confirmation_read_budget_exceeded")
        self.verified.update(chain)

    def risk(self, *, sample_bar_open_at: datetime, cutoff: datetime) -> ExecutionU:
        end = aware_utc(sample_bar_open_at).replace(minute=0, second=0, microsecond=0)
        start = end - timedelta(hours=21)
        bars = self.store.load_in_session(
            self.session,
            self.scope,
            start_at=start,
            end_at=end,
            as_of=cutoff,
            on_original=self.raw,
        )
        expected = tuple(start + timedelta(minutes=i) for i in range(1260))
        if tuple(bar.open_at for bar in bars) != expected:
            raise ValueError("action_execution_u_complete_21h_required")
        # Same frozen median true-range formula as action_features. The W6
        # measurement boundary is independent from its knowledge cutoff, so a
        # late accepted historical bar cannot be relabelled known at hour-end.
        hours = tuple(bars[i : i + 60] for i in range(0, 1260, 60))
        with localcontext() as context:
            context.prec = 50
            context.rounding = ROUND_HALF_EVEN
            ranges = tuple(
                max(
                    max(b.high for b in hour) - min(b.low for b in hour),
                    abs(max(b.high for b in hour) - hours[i][-1].close),
                    abs(min(b.low for b in hour) - hours[i][-1].close),
                )
                for i, hour in enumerate(hours[1:])
            )
            value = median(ranges).quantize(Decimal("0.000000000000000001"))
        if value <= 0:
            raise ValueError("action_execution_positive_u_required")
        known = max(bar.known_at for bar in bars)
        unit = "USDT/" + self.scope.instrument.removesuffix("-USDT")
        payload = {
            "schema_version": "action-execution-risk-basis.v1",
            "scope_id": self.scope.scope_id,
            "value": decimal_text(value),
            "unit": unit,
            "measurement_end": end.isoformat(),
            "knowledge_cutoff": cutoff.isoformat(),
            "known_at": known.isoformat(),
            "boundary": "hour_start_of_trigger_sample_bar",
            "formula": "median_true_range_previous_20_complete_1h_with_prior_close",
            "decimal_places": 18,
            "rounding": "ROUND_HALF_EVEN",
            "basis": [
                {
                    "revision_hash": canonical_hash(bar.semantic_payload()),
                    "known_at": bar.known_at.isoformat(),
                    "capture_ref": bar.capture_ref,
                    "capture_hash": bar.capture_hash,
                    "row_index": bar.row_index,
                }
                for bar in bars
            ],
        }
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return ExecutionU(value, unit, end, cutoff, known, body, canonical_hash(payload))


def read_evaluation_in_session(
    session: Session,
    *,
    plan_id: str,
    revision_id: str,
    evaluation_id: str,
    store: MarketPathStore,
    feature_root: Path,
    observed_at: datetime,
    checkpoint: Callable[[], None] | None = None,
) -> EvaluationOriginals:
    repository = ActionRepository(session)
    revision = repository.get_revision(revision_id)
    row = session.get(ActionConditionEvaluationRecord, evaluation_id, populate_existing=True)
    if (
        revision is None
        or revision.plan_id != plan_id
        or row is None
        or (row.plan_id, row.revision_id) != (plan_id, revision_id)
    ):
        raise ValueError("action_execution_evaluation_identity_mismatch")
    reader = _EvaluationReader(session, revision, store, feature_root, observed_at, checkpoint)
    value = reader.evaluation(row)
    frame = reader.frame(value)
    conditions = {
        item.id: item for item in (*revision.entry, *revision.exit, *revision.invalidation)
    }
    for condition in conditions.values():
        reader.condition(value, condition)
    index = {item.id: item for item in value.per_condition}

    def known_true(condition: Condition) -> bool:
        return index[condition.id].confirmed and index[condition.id].status == "ok"

    if value.monitor_state_before not in {"active", "degraded"}:
        raise ValueError("action_execution_monitor_was_not_running")
    if value.state_before == "armed" and value.state_after == "triggered":
        if not revision.entry or not all(known_true(c) for c in revision.entry):
            raise ValueError("action_execution_entry_confirmation_missing")
        if revision.action not in {"enter_long", "reduce_long", "exit_long"}:
            raise ValueError("action_execution_action_not_executable")
        if revision.advice_qualification not in {"research_trial", "qualified"}:
            raise ValueError("action_execution_action_not_qualified")
        if any(known_true(c) for c in revision.invalidation):
            raise ValueError("action_execution_already_invalidated")
        if revision.action == "enter_long":
            protections = (*revision.exit, *revision.invalidation)
            if (
                not revision.exit
                or not revision.invalidation
                or any(
                    index[c.id].truth == "unknown" or index[c.id].status == "error"
                    for c in protections
                )
                or any(known_true(c) for c in revision.exit)
            ):
                raise ValueError("action_execution_entry_protection_unavailable")
        phase = {"enter_long": "entry", "reduce_long": "reduce", "exit_long": "exit"}[
            revision.action
        ]
        action, reason, selected = revision.action, "condition_entry", tuple(revision.entry)
    elif value.state_before == "triggered" and value.state_after in {"completed", "invalidated"}:
        reason = "invalidation" if value.state_after == "invalidated" else "condition_exit"
        selected = tuple(
            c
            for c in getattr(revision, "invalidation" if reason == "invalidation" else "exit")
            if known_true(c)
        )
        if not selected:
            # Deadline exits have their own episode-based source. A head expiry
            # cannot manufacture a market-condition fact or close a paper trade.
            raise ValueError("action_execution_protection_confirmation_missing")
        phase, action = "exit", "exit_long"
    else:
        raise ValueError("action_execution_no_new_condition_action")
    refs = {ref for c in selected for ref in index[c.id].input_refs}
    features = tuple(f for f in frame.features if f.source_ref in refs)
    if not features or len(features) != len(refs) or any(f.value is None for f in features):
        raise ValueError("action_execution_trigger_feature_missing")
    known_at = max(f.known_at for f in features)
    # Feature observed_at is a closed-bar boundary, not the bar's opening.
    sample_open = max(f.observed_at for f in features) - timedelta(minutes=1)
    risk = (
        reader.risk(sample_bar_open_at=sample_open, cutoff=value.as_of)
        if phase == "entry"
        else None
    )
    return EvaluationOriginals(
        revision,
        value,
        phase,
        action,
        reason,
        tuple(c.id for c in selected),
        known_at,
        sample_open,
        frame,
        risk,
        tuple(sorted(reader.originals.items())),
        tuple(sorted(reader.files.items())),
    )


def read_activation_in_session(
    session: Session,
    *,
    plan_id: str,
    revision_id: str,
    evaluation_id: str,
    store: MarketPathStore,
    feature_root: Path,
    observed_at: datetime,
    checkpoint: Callable[[], None] | None = None,
) -> EvaluationOriginals:
    """Authenticate the first available inherited-position protection, without a buy."""
    repository = ActionRepository(session)
    revision = repository.get_revision(revision_id)
    row = session.get(ActionConditionEvaluationRecord, evaluation_id, populate_existing=True)
    if (
        revision is None
        or revision.plan_id != plan_id
        or revision.position_context != "long"
        or row is None
        or (row.plan_id, row.revision_id) != (plan_id, revision_id)
    ):
        raise ValueError("action_execution_inherited_activation_identity_mismatch")
    reader = _EvaluationReader(session, revision, store, feature_root, observed_at, checkpoint)
    value = reader.evaluation(row)
    if (
        value.monitor_state_before not in {"active", "degraded"}
        or value.state_before not in {"armed", "triggered"}
        or revision.advice_qualification not in {"research_trial", "qualified"}
    ):
        raise ValueError("action_execution_inherited_protection_unavailable")
    frame = reader.frame(value)
    for condition in (*revision.entry, *revision.exit, *revision.invalidation):
        reader.condition(value, condition)
    if frame.closed_bar_at is None:
        raise ValueError("action_execution_inherited_closed_bar_required")
    sample_open = frame.closed_bar_at - timedelta(minutes=1)
    risk = reader.risk(sample_bar_open_at=sample_open, cutoff=value.as_of)
    return EvaluationOriginals(
        revision,
        value,
        "activation",
        revision.action,
        "inherited_protection_activation",
        (),
        max(risk.known_at, value.as_of),
        sample_open,
        frame,
        risk,
        tuple(sorted(reader.originals.items())),
        tuple(sorted(reader.files.items())),
    )
