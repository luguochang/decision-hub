"""One immutable revision reminder and separately consented research motive.

Uses existing command/outbox/admission tables. No price predicate or trade grant.
"""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    PerpetualPlanReviewTriggerV1,
    PerpetualPlanWatchCommandV1,
    PerpetualPlanWatchReceiptV1,
)

from ..persistence.action_repository import ActionRepository
from ..persistence.db import ProductCommandRecord as Command
from ..persistence.db import RunRecord, as_utc, utcnow
from ..persistence.original_preparation import begin_immediate
from ..persistence.risk_research_records import RiskResearchRequestRecord as Request
from ..persistence.risk_research_records import RiskResearchTriggerRecord as Trigger
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.perpetual_research_schedule import next_hour
from ..ports.sources import make_notification_envelope
from .decision_settings import DecisionSettingsService
from .outbox import OutboxService
from .perpetual_advice_query import PUBLISHER, PerpetualAdviceQuery
from .perpetual_research_binding import canonical, read_perpetual_admission_identity_in_session

PROTOCOL = "risk-research-request.perpetual-plan-review.v1"
KIND = "perpetual_plan_watch"
DUE_KIND = "perpetual_plan_expiry"
ORIGIN_KEYS = (
    "origin_run_id",
    "origin_artifact_id",
    "plan_revision_ref",
    "snapshot_ref",
    "comparison_ref",
    "frozen_forecast_ref",
    "publication_ref",
)


def wire(at):
    return aware_utc(at).isoformat().replace("+00:00", "Z")


def command_key(owner, request):
    return "perpwatchcmd_" + canonical_hash([owner, request])


def watch_id(owner, command):
    return "perpwatch_" + canonical_hash(
        [owner, command.plan_id, command.expected_revision_ref.model_dump(mode="json")]
    )


def _latest(session, identity):
    row = session.scalar(
        select(Command)
        .where(Command.kind == KIND, Command.target_id == identity)
        .order_by(func.json_extract(Command.result_json, "$.state_version").desc())
        .limit(1)
    )
    return (
        _receipt(session, row.owner_id, json.loads(row.result_json)["request_id"]) if row else None
    )


def _receipt(session, owner, request):
    row = session.get(Command, command_key(owner, request), populate_existing=True)
    if row is None:
        return None
    value = PerpetualPlanWatchReceiptV1.model_validate_json(row.result_json)
    body = value.model_dump(mode="json")
    digest = canonical_hash(
        {k: v for k, v in body.items() if k not in {"revision", "payload_hash"}}
    )
    if (
        row.kind != KIND
        or row.owner_id != owner
        or value.owner_id != owner
        or value.request_id != request
        or value.command.request_id != request
        or value.watch_id != watch_id(owner, value.command)
        or row.target_id != value.watch_id
        or row.payload_hash != value.command_hash
        or value.command_hash != canonical_hash(value.command.model_dump(mode="json"))
        or row.result_json != canonical(body)
        or value.payload_hash != digest
        or value.revision.ref != row.request_id
        or value.revision.hash != digest
        or as_utc(row.created_at) != value.accepted_at
    ):
        raise ValueError("perpetual_watch_original_invalid")
    return value


def _seal(body):
    provisional = PerpetualPlanWatchReceiptV1.model_validate(
        {
            **body,
            "revision": {
                "ref": command_key(body["owner_id"], body["request_id"]),
                "hash": "0" * 64,
            },
            "payload_hash": "0" * 64,
        }
    ).model_dump(mode="json")
    digest = canonical_hash(
        {k: v for k, v in provisional.items() if k not in {"revision", "payload_hash"}}
    )
    return PerpetualPlanWatchReceiptV1.model_validate(
        {
            **provisional,
            "revision": {"ref": provisional["revision"]["ref"], "hash": digest},
            "payload_hash": digest,
        }
    )


def _origin(session, owner, command, at, *, require_current, include_view=False):
    repo = ActionRepository(session)
    revision = repo.get_revision(command.expected_revision_ref.ref)
    if (
        revision is None
        or revision.schema_version != "decision-action-plan-revision.v2"
        or revision.plan_id != command.plan_id
        or revision.payload_hash != command.expected_revision_ref.hash
        or revision.context.owner_id != owner
        or revision.horizon != command.horizon
        or any(
            getattr(revision.context, key) != value
            for key, value in command.partition.model_dump(mode="json").items()
            if key != "profile_ref"
        )
        or revision.context.profile_ref.ref != command.partition.profile_ref
        or revision.context.settings_ref != command.expected_settings_revision
    ):
        raise ValueError("perpetual_watch_revision_mismatch")
    snapshot = repo.get_snapshot(revision.decision_id)
    if snapshot is None:
        raise ValueError("perpetual_watch_snapshot_missing")
    run = session.get(RunRecord, snapshot.run_id, populate_existing=True)
    if run is None:
        raise ValueError("perpetual_watch_origin_run_missing")
    identity = read_perpetual_admission_identity_in_session(session, run.run_id)
    if identity is None:
        raise ValueError("perpetual_watch_admission_missing")
    admission, admission_ref = identity
    if admission["settings_state_version"] != command.expected_settings_state_version:
        raise ValueError("perpetual_watch_settings_mismatch")
    published = repo.get_command(
        PUBLISHER, "publish_" + canonical_hash([run.run_id, admission["opportunity_id"]])
    )
    if published is None:
        raise ValueError("perpetual_watch_publication_missing")
    response = dict(settings_revision=command.expected_settings_revision.model_dump(mode="json"))
    if include_view:
        response.update(run_id=run.run_id, report_id=run.artifact_id)
    original = PerpetualAdviceQuery._published(
        session, repo, run, admission, admission_ref, published, response, at
    )
    if original["revision"]["revision_id"] != revision.revision_id:
        raise ValueError("perpetual_watch_revision_mismatch")
    head = repo.get_head(command.plan_id)
    if require_current and (head is None or head.current_revision_id != revision.revision_id):
        raise ValueError("perpetual_watch_revision_changed")
    origins = dict(
        origin_run_id=run.run_id,
        origin_artifact_id=run.artifact_id,
        plan_revision_ref=command.expected_revision_ref.model_dump(mode="json"),
        snapshot_ref=dict(ref=snapshot.id, hash=snapshot.snapshot_hash),
        comparison_ref=revision.bindings.comparison.model_dump(mode="json"),
        frozen_forecast_ref=revision.bindings.frozen_forecast.model_dump(mode="json"),
        publication_ref=dict(ref=published.request_id, hash=published.receipt_hash),
    )
    if include_view:
        return origins, revision.valid_until, original
    return origins, revision.valid_until


def _settings_current(session, owner, command):
    current = DecisionSettingsService.current_in_session(session, owner, command.partition)
    return bool(
        current
        and current.revision.operation == "upsert"
        and current.state_version == command.expected_settings_state_version
        and (current.revision.revision_id, current.revision.payload_hash)
        == (command.expected_settings_revision.ref, command.expected_settings_revision.hash)
    )


def _due_id(identity):
    return "perpwatchdue_" + canonical_hash(identity)


def _read_due(session, receipt):
    row = session.get(Command, _due_id(receipt.watch_id), populate_existing=True)
    if row is None:
        return None
    body = json.loads(row.result_json)
    if (
        row.kind != DUE_KIND
        or row.owner_id != receipt.owner_id
        or row.target_id != receipt.watch_id
        or row.payload_hash != canonical_hash(body)
        or row.result_json != canonical(body)
        or body["watch_id"] != receipt.watch_id
    ):
        raise ValueError("perpetual_watch_expiry_original_invalid")
    return body


class PerpetualPlanFollowupService:
    protocol_version = PROTOCOL
    supported_protocols = (PROTOCOL,)

    def __init__(self, database, entry, *, now=utcnow, channel="local"):
        self.database, self.entry, self.now, self.channel = database, entry, now, channel
        # A scan hint only; all consent/revision/request authority remains durable.
        self._continuous_scan_after = None
        from .perpetual_condition_monitor import PerpetualConditionMonitor

        self.condition_monitor = PerpetualConditionMonitor(
            database,
            getattr(getattr(entry, "binding", None), "source", None),
            now=now,
            channel=channel,
        )
        from .event_scenario_watch import EventScenarioWatch

        self.event_scenario_watch = EventScenarioWatch(self)
        if entry.database is not database or entry.total_deadline_seconds != 3600:
            raise ValueError("perpetual_watch_entry_mismatch")

    def receipt(self, owner, request):
        with self.database.session() as session:
            return _receipt(session, owner, request)

    def save(self, owner, value):
        command = PerpetualPlanWatchCommandV1.model_validate(value)
        if not owner or owner.startswith("system:"):
            raise ValueError("perpetual_watch_owner_invalid")
        with self.database.session() as session:
            begin_immediate(session)
            prior = _receipt(session, owner, command.request_id)
            digest = canonical_hash(command.model_dump(mode="json"))
            if prior:
                if prior.command_hash != digest:
                    raise ValueError("perpetual_watch_request_conflict")
                return prior
            identity = watch_id(owner, command)
            current = _latest(session, identity)
            if (current.state_version if current else 0) != command.expected_state_version:
                raise ValueError("perpetual_watch_state_conflict")
            at = aware_utc(self.now())
            if command.operation == "pause":
                if current is None:
                    raise ValueError("perpetual_watch_missing")
                origins = {key: getattr(current, key) for key in ORIGIN_KEYS}
                expiry = current.review_due_at
                slot = current.auto_review_slot
            else:
                if current and _read_due(session, current):
                    raise ValueError("perpetual_watch_already_processed_use_manual_research")
                if not _settings_current(session, owner, command):
                    raise ValueError("perpetual_watch_settings_changed")
                origins, expiry = _origin(session, owner, command, at, require_current=True)
                slot = next_hour(max(expiry, at)) if command.mode == "remind_and_research" else None
            receipt = _seal(
                dict(
                    schema_version="perpetual-plan-watch-receipt.v1",
                    owner_id=owner,
                    request_id=command.request_id,
                    command=command,
                    command_hash=digest,
                    watch_id=identity,
                    state_version=(current.state_version if current else 0) + 1,
                    state="active" if command.operation == "watch" else "paused",
                    accepted_at=wire(at),
                    review_due_at=wire(expiry),
                    auto_review_slot=wire(slot) if slot else None,
                    **origins,
                )
            )
            session.add(
                Command(
                    request_id=receipt.revision.ref,
                    owner_id=owner,
                    target_id=identity,
                    kind=KIND,
                    payload_hash=digest,
                    result_json=canonical(receipt.model_dump(mode="json")),
                    created_at=at,
                )
            )
            session.flush()
            return receipt

    def current(self, owner, plan_id, revision_ref):
        identity = "perpwatch_" + canonical_hash([owner, plan_id, revision_ref])
        with self.database.session() as session:
            receipt = _latest(session, identity)
            if receipt is None:
                return dict(
                    schema_version="perpetual-plan-watch-view.v1", receipt=None, expiry=None
                )
            return dict(
                schema_version="perpetual-plan-watch-view.v1",
                receipt=receipt.model_dump(mode="json"),
                expiry=_read_due(session, receipt),
            )

    def discover(self):
        from .perpetual_plan_watch_continuous import ContinuousPlanWatch

        ContinuousPlanWatch(self).discover()
        self._discover_once()

    def save_continuous(self, owner, value):
        from .perpetual_plan_watch_continuous import ContinuousPlanWatch

        return ContinuousPlanWatch(self).save(owner, value)

    def receipt_continuous(self, owner, request):
        from .perpetual_plan_watch_continuous import ContinuousPlanWatch

        return ContinuousPlanWatch(self).receipt(owner, request)

    def current_continuous(self, owner, partition, horizon, settings_revision):
        from .perpetual_plan_watch_continuous import ContinuousPlanWatch

        return ContinuousPlanWatch(self).current(owner, partition, horizon, settings_revision)

    def _discover_once(self):
        at = aware_utc(self.now())
        with self.database.session() as session:
            begin_immediate(session)
            ranked = (
                select(
                    Command.request_id,
                    func.row_number()
                    .over(
                        partition_by=Command.target_id,
                        order_by=func.json_extract(Command.result_json, "$.state_version").desc(),
                    )
                    .label("n"),
                )
                .where(Command.kind == KIND)
                .subquery()
            )
            completed = aliased(Command)
            rows = session.scalars(
                select(Command)
                .join(ranked, ranked.c.request_id == Command.request_id)
                .where(
                    ranked.c.n == 1,
                    func.json_extract(Command.result_json, "$.state") == "active",
                    func.json_extract(Command.result_json, "$.review_due_at") <= wire(at),
                    ~select(completed.request_id)
                    .where(completed.kind == DUE_KIND, completed.target_id == Command.target_id)
                    .exists(),
                )
                .order_by(Command.created_at)
                .limit(32)
            ).all()
            for row in rows:
                receipt = _receipt(session, row.owner_id, json.loads(row.result_json)["request_id"])
                if _read_due(session, receipt):
                    continue
                origins, expiry = _origin(
                    session, receipt.owner_id, receipt.command, at, require_current=False
                )
                if (
                    any(
                        origins[key]
                        != (
                            getattr(receipt, key).model_dump(mode="json")
                            if hasattr(getattr(receipt, key), "model_dump")
                            else getattr(receipt, key)
                        )
                        for key in ORIGIN_KEYS
                    )
                    or expiry != receipt.review_due_at
                ):
                    raise ValueError("perpetual_watch_origin_changed")
                head = ActionRepository(session).get_head(receipt.command.plan_id)
                unchanged = (
                    _settings_current(session, receipt.owner_id, receipt.command)
                    and head is not None
                    and head.current_revision_id == receipt.plan_revision_ref.ref
                )
                status = "expired" if unchanged else "settings_or_revision_changed"
                request_id = None
                if receipt.command.mode == "remind_and_research" and unchanged:
                    source = PerpetualPlanReviewTriggerV1(
                        schema_version="perpetual-plan-review-trigger.v1",
                        watch_id=receipt.watch_id,
                        watch_ref=receipt.revision,
                        owner_id=receipt.owner_id,
                        partition=receipt.command.partition,
                        horizon=receipt.command.horizon,
                        motive="revision_expired",
                        expired_at=wire(expiry),
                        observed_at=wire(at),
                        scheduled_at=wire(receipt.auto_review_slot),
                        deadline_at=wire(next_hour(receipt.auto_review_slot)),
                        recorded_at=wire(at),
                        **origins,
                    )
                    body = source.model_dump(mode="json")
                    key = "perpetual_plan_review:" + canonical_hash(receipt.watch_id)
                    instrument = source.partition.instrument_id.removeprefix("okx:")
                    scope = f"crypto:perp:okx:{instrument}:trade:1m"
                    request_id = "riskresearch_" + canonical_hash([scope, key])[:40]
                    missed = at >= source.deadline_at
                    session.add(
                        Request(
                            request_id=request_id,
                            protocol_version=PROTOCOL,
                            scope_id=scope,
                            instrument=source.partition.instrument_id.removeprefix("okx:"),
                            status="failed" if missed else "pending_admission",
                            created_at=at,
                            updated_at=at,
                            available_at=max(at, source.scheduled_at),
                            attempts=0,
                            lease_generation=0,
                            error_code="perpetual_watch_slot_expired" if missed else None,
                        )
                    )
                    session.add(
                        Trigger(
                            trigger_key=key,
                            request_id=request_id,
                            kind="perpetual_plan_review",
                            scope_id=scope,
                            source_ref=receipt.watch_id,
                            payload_json=canonical(body),
                            payload_hash=canonical_hash(body),
                            recorded_at=at,
                        )
                    )
                    if missed:
                        status = "slot_missed"
                message = make_notification_envelope(
                    subject_kind="action_revision",
                    subject_id=receipt.plan_revision_ref.ref,
                    channel=self.channel,
                    dedupe_key=f"perpetual-plan-watch:{receipt.watch_id}:expired:{self.channel}",
                    subject="研究方案已到期",
                    body=f"这版 {receipt.command.horizon} 研究方案已到期；"
                    f"不代表方向反转或下单授权。状态：{status}。"
                    + (
                        "已按单独同意安排下一完整小时复查。"
                        if request_id
                        else "未创建新的付费研究。"
                    ),
                    created_at=at,
                )
                outbox_id = OutboxService(self.database).enqueue_subject(message, session=session)
                event = dict(
                    schema_version="perpetual-plan-expiry.v1",
                    watch_id=receipt.watch_id,
                    watch_ref=receipt.revision.model_dump(mode="json"),
                    expired_at=wire(expiry),
                    observed_at=wire(at),
                    status=status,
                    outbox_id=outbox_id,
                    request_id=request_id,
                )
                session.add(
                    Command(
                        request_id=_due_id(receipt.watch_id),
                        owner_id=receipt.owner_id,
                        target_id=receipt.watch_id,
                        kind=DUE_KIND,
                        payload_hash=canonical_hash(event),
                        result_json=canonical(event),
                        created_at=at,
                    )
                )

    def recognize_in_session(self, session, row, at):
        return read_plan_review_source_in_session(
            session,
            row.request_id,
            row.lease_generation,
            row.lease_owner,
            at=at,
            require_active=False,
            require_claim_live=False,
        )

    def claimable_in_session(self, session, row, at):
        try:
            source = read_plan_review_source_in_session(
                session, row.request_id, None, None, at=at, claim_check=False
            )
        except ValueError as exc:
            row.status, row.error_code = "failed", str(exc)
            return False
        from .perpetual_research_entry import QUESTION
        from .research_tracking import research_admission_guard_in_session
        from .study import stable_id

        study = stable_id(
            "study",
            json.dumps(
                ["crypto", row.instrument, source["source"]["horizon"], QUESTION],
                ensure_ascii=False,
            ),
        )
        guard = research_admission_guard_in_session(
            session, study_id=study, now=at, admission_origin="automatic"
        )
        if guard.reason_code is not None:
            row.error_code = guard.reason_code
            row.available_at = at + timedelta(seconds=60)
            return False
        return True

    async def prepare(self, *args):
        from .perpetual_research_schedule import PerpetualResearchScheduleService

        return await PerpetualResearchScheduleService.prepare(self, *args)

    def admit(self, *args):
        from .perpetual_research_schedule import PerpetualResearchScheduleService

        return PerpetualResearchScheduleService.admit(self, *args)


def read_plan_review_source_in_session(
    session,
    key,
    generation,
    claim_owner,
    *,
    at,
    require_active=True,
    require_claim_live=True,
    run_id=None,
    original_only=False,
    claim_check=True,
):
    row = session.get(Request, key, populate_existing=True)
    if row is None or row.protocol_version != PROTOCOL:
        raise ValueError("perpetual_watch_request_invalid")
    if (
        not original_only
        and run_id is not None
        and (row.run_id != run_id or row.status not in {"queued", "completed"})
    ):
        raise ValueError("perpetual_watch_claim_lost")
    if (
        not original_only
        and run_id is None
        and claim_check
        and (
            row.status != "claiming"
            or row.lease_owner != claim_owner
            or row.lease_generation != generation
            or row.lease_expires_at is None
            or require_claim_live
            and at >= as_utc(row.lease_expires_at)
        )
    ):
        raise ValueError("perpetual_watch_claim_lost")
    triggers = session.scalars(select(Trigger).where(Trigger.request_id == key)).all()
    if len(triggers) != 1:
        raise ValueError("perpetual_watch_trigger_invalid")
    trigger = triggers[0]
    from ..ports.perpetual_plan_watch import (
        PerpetualPlanReviewTriggerV2,
        PerpetualPlanReviewTriggerV3,
    )

    raw_source = json.loads(trigger.payload_json)
    source = (
        PerpetualPlanReviewTriggerV3.model_validate(raw_source)
        if raw_source.get("schema_version") == "perpetual-plan-review-trigger.v3"
        else
        PerpetualPlanReviewTriggerV2.model_validate(raw_source)
        if raw_source.get("schema_version") == "perpetual-plan-review-trigger.v2"
        else PerpetualPlanReviewTriggerV1.model_validate(raw_source)
    )
    body = source.model_dump(mode="json")
    stored = session.get(Command, source.watch_ref.ref, populate_existing=True)
    if stored is None:
        raise ValueError("perpetual_watch_original_missing")
    if stored.kind == "perpetual_plan_watch_v2":
        from .perpetual_plan_watch_continuous import read_continuous_source_in_session

        return read_continuous_source_in_session(
            session, row, trigger, source, stored, at=at, require_active=require_active
        )
    receipt = _receipt(session, source.owner_id, json.loads(stored.result_json)["request_id"])
    scope = f"crypto:perp:okx:{source.partition.instrument_id.removeprefix('okx:')}:trade:1m"
    if (
        trigger.kind != "perpetual_plan_review"
        or trigger.source_ref != source.watch_id
        or trigger.payload_json != canonical(body)
        or trigger.payload_hash != canonical_hash(body)
        or row.scope_id != scope
        or trigger.scope_id != scope
        or row.instrument != source.partition.instrument_id.removeprefix("okx:")
        or trigger.trigger_key != "perpetual_plan_review:" + canonical_hash(source.watch_id)
        or key != "riskresearch_" + canonical_hash([scope, trigger.trigger_key])[:40]
        or receipt.revision != source.watch_ref
        or receipt.state != "active"
        or receipt.watch_id != source.watch_id
        or receipt.command.mode != "remind_and_research"
        or receipt.command.partition != source.partition
        or receipt.command.horizon != source.horizon
        or receipt.review_due_at != source.expired_at
        or receipt.auto_review_slot != source.scheduled_at
        or source.deadline_at != next_hour(source.scheduled_at)
        or source.scheduled_at != next_hour(max(source.expired_at, receipt.accepted_at))
        or not source.expired_at <= source.observed_at == source.recorded_at <= at
        or not receipt.accepted_at <= source.recorded_at
        or as_utc(trigger.recorded_at) != source.recorded_at
        or as_utc(row.created_at) != source.recorded_at
        or any(
            body[k]
            != (
                getattr(receipt, k).model_dump(mode="json")
                if hasattr(getattr(receipt, k), "model_dump")
                else getattr(receipt, k)
            )
            for k in ORIGIN_KEYS
        )
    ):
        raise ValueError("perpetual_watch_trigger_invalid")
    origins, expiry = _origin(
        session, source.owner_id, receipt.command, at, require_current=require_active
    )
    if any(origins[k] != body[k] for k in ORIGIN_KEYS) or expiry != source.expired_at:
        raise ValueError("perpetual_watch_origin_changed")
    if require_active:
        current = _latest(session, source.watch_id)
        if current.revision != source.watch_ref or current.state != "active":
            raise ValueError("perpetual_watch_changed_or_paused")
        if not _settings_current(session, source.owner_id, receipt.command):
            raise ValueError("perpetual_watch_settings_changed")
        if at >= source.deadline_at:
            raise ValueError("perpetual_watch_slot_expired")
        if at < source.scheduled_at:
            raise ValueError("perpetual_watch_slot_not_due")
    return dict(
        request_id=key,
        trigger_ref=dict(ref=trigger.trigger_key, hash=trigger.payload_hash),
        watch_ref=source.watch_ref.model_dump(mode="json"),
        source=body,
    )
