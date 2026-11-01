"""Append-only continuing consent and bounded authenticated publication cycles.

No timer, model execution, price predicate or execution permission lives here.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSettingsPartitionV1,
    DecisionSourceRefV1,
    PerpetualPlanReviewTriggerV1,
)

from ..persistence.action_repository import ActionRepository
from ..persistence.db import ProductCommandRecord as Command
from ..persistence.db import RunRecord, RunRoleOutputRecord, as_utc
from ..persistence.original_preparation import begin_immediate
from ..persistence.risk_research_records import RiskResearchRequestRecord as Request
from ..persistence.risk_research_records import RiskResearchTriggerRecord as Trigger
from ..ports.exact_decimal_text import number
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.perpetual_plan_watch import (
    PerpetualPlanWatchReceiptV2,
    PerpetualPlanWatchReceiptV3,
    parse_continuous_watch_command,
)
from ..ports.perpetual_research_inputs import INPUT_POLICY
from ..ports.perpetual_research_schedule import next_hour
from ..ports.sources import make_notification_envelope
from .outbox import OutboxService
from .perpetual_advice_query import PUBLISHER
from .perpetual_plan_followup import ORIGIN_KEYS, PROTOCOL, _origin, _settings_current, wire
from .perpetual_research_binding import ADMISSION_ROLE, canonical

KIND = "perpetual_plan_watch_v2"
PROGRESS = "perpetual_plan_watch_progress"


def identity(owner, partition, horizon, settings):
    return "perpwatchcontinuous_" + canonical_hash([owner, partition, horizon, settings])


def command_key(owner, request):
    return "perpwatchcontinuouscmd_" + canonical_hash([owner, request])


def _read(session, owner, request):
    row = session.get(Command, command_key(owner, request), populate_existing=True)
    if row is None:
        return None
    model = (
        PerpetualPlanWatchReceiptV3
        if json.loads(row.result_json).get("schema_version") == "perpetual-plan-watch-receipt.v3"
        else PerpetualPlanWatchReceiptV2
    )
    receipt = model.model_validate_json(row.result_json)
    body = receipt.model_dump(mode="json")
    digest = canonical_hash(
        {k: v for k, v in body.items() if k not in {"revision", "payload_hash"}}
    )
    cmd = receipt.command
    if (
        row.kind != KIND
        or row.owner_id != owner
        or receipt.owner_id != owner
        or receipt.request_id != request
        or cmd.request_id != request
        or receipt.watch_id
        != identity(
            owner,
            cmd.partition.model_dump(mode="json"),
            cmd.horizon,
            cmd.expected_settings_revision.model_dump(mode="json"),
        )
        or row.target_id != receipt.watch_id
        or row.payload_hash != receipt.command_hash
        or receipt.command_hash != canonical_hash(cmd.model_dump(mode="json"))
        or row.result_json != canonical(body)
        or receipt.payload_hash != digest
        or receipt.revision.ref != row.request_id
        or receipt.revision.hash != digest
        or receipt.accepted_at != as_utc(row.created_at)
    ):
        raise ValueError("perpetual_continuous_original_invalid")
    return receipt


def _latest(session, watch):
    row = session.scalar(
        select(Command)
        .where(Command.kind == KIND, Command.target_id == watch)
        .order_by(func.json_extract(Command.result_json, "$.state_version").desc())
        .limit(1)
    )
    return _read(session, row.owner_id, json.loads(row.result_json)["request_id"]) if row else None


def _seal(body):
    model = (
        PerpetualPlanWatchReceiptV3
        if body["schema_version"] == "perpetual-plan-watch-receipt.v3"
        else PerpetualPlanWatchReceiptV2
    )
    provisional = model.model_validate(
        {
            **body,
            "revision": dict(ref=command_key(body["owner_id"], body["request_id"]), hash="0" * 64),
            "payload_hash": "0" * 64,
        }
    ).model_dump(mode="json")
    digest = canonical_hash(
        {k: v for k, v in provisional.items() if k not in {"revision", "payload_hash"}}
    )
    return model.model_validate(
        {
            **provisional,
            "revision": dict(ref=provisional["revision"]["ref"], hash=digest),
            "payload_hash": digest,
        }
    )


def _comparison_hash(value):
    numeric_fields = {
        "value",
        "quantity_before",
        "quantity_after",
        "threshold_fraction",
        "assumed_slippage_fraction_per_leg",
        "assumed_taker_rate",
        "actual_account_fee_rate",
        "trade_cost_multiplier",
        "assumed_settlement_rate",
        "fill_probability",
        "trigger_probability",
        "no_fill_mass",
        "opportunity_net_profit_probability",
        "liquidation_probability",
        "net_profit",
        "zero",
        "net_loss",
    }

    def comparable(item, field=""):
        if isinstance(item, dict):
            if set(item) == {"ref", "hash"}:
                return item
            return {key: comparable(child, key) for key, child in item.items()}
        if isinstance(item, list):
            return [comparable(child, field) for child in item]
        if isinstance(item, str) and (field in numeric_fields or field.endswith("_usdt")):
            exact = Decimal(item)
            return number(Decimal(0) if exact.is_zero() else exact)
        return item

    return canonical_hash(comparable(value))


def result_hash(view):
    """Compare declared results, retaining numbers/conditions/qualification.

    Identity, as-of and publication times are intentionally outside this result
    comparison. Exact references still remain on every durable cycle.
    """
    candidate = view["revision"]["selected_candidate"]
    action = {
        key: candidate[key]
        for key in (
            "action",
            "target_side",
            "position_branch",
            "quantity_before",
            "quantity_after",
            "size_mode",
            "unit_notional_usdt",
            "condition_refs",
            "nested_action_ref",
            "exit_ref",
            "unknown_reasons",
        )
    }

    def estimate(value):
        return {key: value[key] for key in ("probabilities", "kind", "qualification", "gaps")}

    results = [
        {
            key: row[key]
            for key in (
                "action",
                "target_side",
                "feasibility",
                "rejection_codes",
                "unit_expected_net_if_assumed_fills_usdt",
                "worst_listed_loss_usdt",
                "expected_downside_usdt",
                "expected_turnover_usdt",
                "scenario_net_pnls_usdt",
            )
        }
        for row in view["comparison"]["candidate_results"]
    ]
    for result, row in zip(results, view["comparison"]["candidate_results"], strict=True):
        result["outcome"] = {
            k: v for k, v in row["outcome"].items() if k not in {"target_ref", "source_ref"}
        }
    return _comparison_hash(
        dict(
            action=action,
            results=results,
            target={
                k: view["target"][k]
                for k in (
                    "horizon",
                    "duration_seconds",
                    "price_basis",
                    "threshold_fraction",
                    "conditioning_ref",
                    "method_ref",
                    "label_rule_ref",
                )
            },
            costs=view["cost_assumptions"],
            occurrence=estimate(view["event_probability"]["occurrence_distribution"]),
            event_outcome=estimate(view["event_probability"]["outcome_given_occurrence"]),
            direction=estimate(view["market_distribution"]["direction_distribution"]),
            candidate_probability=estimate(view["candidate_probability"]),
            gaps=view["gaps"],
            qualification=view["revision"]["advice_qualification"],
        )
    )


def _progress_id(receipt, revision_ref, status):
    return "perpwatchprogress_" + canonical_hash(
        [receipt.revision.model_dump(mode="json"), revision_ref, status]
    )


def _read_progress(session, row):
    if row is None:
        return None
    body = json.loads(row.result_json)
    if (
        row.kind != PROGRESS
        or row.target_id != body["watch_id"]
        or row.owner_id != body["owner_id"]
        or row.payload_hash != canonical_hash(body)
        or row.result_json != canonical(body)
        or as_utc(row.created_at)
        != aware_utc(datetime.fromisoformat(body["observed_at"].replace("Z", "+00:00")))
    ):
        raise ValueError("perpetual_continuous_progress_invalid")
    stored = session.get(Command, body["watch_ref"]["ref"], populate_existing=True)
    if stored is None:
        raise ValueError("perpetual_continuous_original_missing")
    receipt = _read(session, row.owner_id, json.loads(stored.result_json)["request_id"])
    if (
        receipt.revision.model_dump(mode="json") != body["watch_ref"]
        or receipt.watch_id != body["watch_id"]
    ):
        raise ValueError("perpetual_continuous_progress_invalid")
    if row.request_id != _progress_id(receipt, body["plan_revision_ref"], body["status"]):
        raise ValueError("perpetual_continuous_progress_invalid")
    return body


def _latest_progress(session, watch):
    return _read_progress(
        session,
        session.scalar(
            select(Command)
            .where(Command.kind == PROGRESS, Command.target_id == watch)
            .order_by(func.json_extract(Command.result_json, "$.sequence").desc())
            .limit(1)
        ),
    )


def _command_for(receipt, progress):
    return receipt.command.model_copy(
        update={
            "plan_id": progress["plan_id"],
            "expected_revision_ref": DecisionSourceRefV1.model_validate(
                progress["plan_revision_ref"]
            ),
        }
    )


def _check_origin(session, receipt, progress, at, *, require_current):
    origins, expiry = _origin(
        session,
        receipt.owner_id,
        _command_for(receipt, progress),
        at,
        require_current=require_current,
    )
    if (
        any(origins[k] != progress[k] for k in ORIGIN_KEYS)
        or wire(expiry) != progress["review_due_at"]
    ):
        raise ValueError("perpetual_continuous_origin_changed")


class ContinuousPlanWatch:
    def __init__(self, parent):
        self.parent = parent
        self.database, self.now, self.channel = parent.database, parent.now, parent.channel

    def receipt(self, owner, request):
        with self.database.session() as session:
            return _read(session, owner, request)

    def current(self, owner, partition, horizon, settings_revision):
        partition = DecisionSettingsPartitionV1.model_validate(partition)
        settings = DecisionSourceRefV1.model_validate(settings_revision)
        if horizon not in {"4h", "24h", "72h"}:
            raise ValueError("perpetual_continuous_horizon_invalid")
        watch = identity(
            owner, partition.model_dump(mode="json"), horizon, settings.model_dump(mode="json")
        )
        with self.database.session() as session:
            receipt = _latest(session, watch)
            progress = _latest_progress(session, watch) if receipt else None
            if progress:
                _check_origin(
                    session, receipt, progress, aware_utc(self.now()), require_current=False
                )
            return dict(
                schema_version="perpetual-plan-watch-view.v3",
                receipt=receipt.model_dump(mode="json") if receipt else None,
                progress={**progress, "payload_hash": canonical_hash(progress)}
                if progress
                else None,
                condition_monitoring=self.parent.condition_monitor.view(session, receipt, progress),
            )

    def save(self, owner, value):
        cmd = parse_continuous_watch_command(value)
        if not owner or owner.startswith("system:"):
            raise ValueError("perpetual_continuous_owner_invalid")
        at = aware_utc(self.now())
        prepared_policy = (
            self.parent.condition_monitor.prepare_policy(owner, cmd)
            if cmd.operation == "watch"
            else None
        )
        with self.database.session() as session:
            begin_immediate(session)
            prior = _read(session, owner, cmd.request_id)
            digest = canonical_hash(cmd.model_dump(mode="json"))
            if prior:
                if prior.command_hash != digest:
                    raise ValueError("perpetual_continuous_request_conflict")
                return prior
            watch = identity(
                owner,
                cmd.partition.model_dump(mode="json"),
                cmd.horizon,
                cmd.expected_settings_revision.model_dump(mode="json"),
            )
            current = _latest(session, watch)
            if (current.state_version if current else 0) != cmd.expected_state_version:
                raise ValueError("perpetual_continuous_state_conflict")
            previous = _latest_progress(session, watch)
            if cmd.operation == "pause":
                if current is None:
                    raise ValueError("perpetual_continuous_missing")
                origins = {k: getattr(current, k) for k in ORIGIN_KEYS}
                expiry, view = current.review_due_at, None
            else:
                if not _settings_current(session, owner, cmd):
                    raise ValueError("perpetual_continuous_settings_changed")
                origins, expiry, view = _origin(
                    session, owner, cmd, at, require_current=True, include_view=True
                )
            receipt = _seal(
                dict(
                    schema_version=(
                        "perpetual-plan-watch-receipt.v3"
                        if cmd.schema_version == "perpetual-plan-watch-command.v3"
                        else "perpetual-plan-watch-receipt.v2"
                    ),
                    owner_id=owner,
                    request_id=cmd.request_id,
                    command=cmd,
                    command_hash=digest,
                    watch_id=watch,
                    state_version=(current.state_version if current else 0) + 1,
                    state="active" if cmd.operation == "watch" else "paused",
                    accepted_at=wire(at),
                    review_due_at=wire(expiry),
                    auto_review_slot=wire(next_hour(expiry))
                    if cmd.mode == "remind_and_research"
                    else None,
                    **origins,
                )
            )
            session.add(
                Command(
                    request_id=receipt.revision.ref,
                    owner_id=owner,
                    target_id=watch,
                    kind=KIND,
                    payload_hash=digest,
                    result_json=canonical(receipt.model_dump(mode="json")),
                    created_at=at,
                )
            )
            session.flush()
            if view:
                self._append(
                    session,
                    receipt,
                    self._from_view(receipt, view),
                    "following",
                    at,
                    previous=previous,
                )
                self.parent.condition_monitor.install_policy(session, receipt, prepared_policy)
            elif previous:
                self._append(session, receipt, previous, "paused", at, previous=previous)
            return receipt

    def _from_view(self, receipt, view):
        cmd = receipt.command.model_copy(
            update={
                "plan_id": view["revision"]["plan_id"],
                "expected_revision_ref": DecisionSourceRefV1(
                    ref=view["revision"]["revision_id"], hash=view["revision"]["payload_hash"]
                ),
            }
        )
        # The view is authenticated again in the discovery transaction before use.
        return dict(
            plan_id=cmd.plan_id,
            origin_run_id=view["run_id"],
            origin_artifact_id=view["report_id"],
            plan_revision_ref=cmd.expected_revision_ref.model_dump(mode="json"),
            snapshot_ref=dict(ref=view["snapshot"]["id"], hash=view["snapshot"]["snapshot_hash"]),
            comparison_ref=view["revision"]["bindings"]["comparison"],
            frozen_forecast_ref=view["revision"]["bindings"]["frozen_forecast"],
            publication_ref=None,
            review_due_at=view["revision"]["valid_until"],
            published_at=view["revision"]["published_at"],
            result_hash=result_hash(view),
        )

    def _append(
        self, session, receipt, source, status, at, *, previous, request_id=None, outbox_ids=()
    ):
        ref = source["plan_revision_ref"]
        key = _progress_id(receipt, ref, status)
        existing = session.get(Command, key, populate_existing=True)
        if existing:
            return _read_progress(session, existing)
        command = _command_for(receipt, source)
        origins, expiry = _origin(session, receipt.owner_id, command, at, require_current=False)
        body = dict(
            schema_version="perpetual-plan-watch-progress.v1",
            owner_id=receipt.owner_id,
            watch_id=receipt.watch_id,
            watch_ref=receipt.revision.model_dump(mode="json"),
            sequence=(previous["sequence"] if previous else 0) + 1,
            status=status,
            observed_at=wire(at),
            plan_id=source["plan_id"],
            published_at=source["published_at"],
            review_due_at=wire(expiry),
            auto_review_slot=wire(next_hour(expiry))
            if receipt.command.mode == "remind_and_research"
            else None,
            result_hash=source["result_hash"],
            request_id=request_id,
            outbox_ids=list(outbox_ids),
            **origins,
        )
        session.add(
            Command(
                request_id=key,
                owner_id=receipt.owner_id,
                target_id=receipt.watch_id,
                kind=PROGRESS,
                payload_hash=canonical_hash(body),
                result_json=canonical(body),
                created_at=at,
            )
        )
        session.flush()
        return body

    def _successor(self, session, receipt, progress, at):
        cmd, column = receipt.command, RunRoleOutputRecord.payload_json
        filters = [
            RunRoleOutputRecord.role == ADMISSION_ROLE,
            func.json_extract(column, "$.owner_id") == receipt.owner_id,
            func.json_extract(column, "$.context.horizon") == cmd.horizon,
            func.json_extract(column, "$.settings_revision.revision_id")
            == cmd.expected_settings_revision.ref,
            func.json_extract(column, "$.settings_revision.payload_hash")
            == cmd.expected_settings_revision.hash,
            func.json_extract(column, "$.settings_state_version")
            == cmd.expected_settings_state_version,
        ]
        filters.extend(
            func.json_extract(column, "$.partition." + k) == v
            for k, v in cmd.partition.model_dump(mode="json").items()
        )
        runs = session.scalars(
            select(RunRecord)
            .join(RunRoleOutputRecord, RunRoleOutputRecord.run_id == RunRecord.run_id)
            .where(*filters, RunRecord.status.in_(("completed", "degraded")))
            .order_by(RunRecord.created_at.desc(), RunRecord.run_id.desc())
            .limit(32)
        ).all()
        repo = ActionRepository(session)
        initial = repo.get_revision(receipt.plan_revision_ref.ref)
        candidates = []
        for run in runs:
            admission = json.loads(
                session.scalar(
                    select(RunRoleOutputRecord.payload_json).where(
                        RunRoleOutputRecord.run_id == run.run_id,
                        RunRoleOutputRecord.role == ADMISSION_ROLE,
                    )
                )
            )
            published = repo.get_command(
                PUBLISHER, "publish_" + canonical_hash([run.run_id, admission["opportunity_id"]])
            )
            if published is None or published.accepted_at > at:
                continue
            branches = published.response.get("branches", [])
            if len(branches) != 1:
                raise ValueError("perpetual_continuous_publication_invalid")
            revision = repo.get_revision(branches[0]["revision_id"])
            if revision is None or revision.position_context != initial.position_context:
                raise ValueError("perpetual_continuous_branch_changed")
            if revision.published_at <= datetime.fromisoformat(
                progress["published_at"].replace("Z", "+00:00")
            ):
                continue
            head = repo.get_head(revision.plan_id)
            if (
                head is None
                or head.current_revision_id != revision.revision_id
                or head.state in {"invalidated", "superseded"}
            ):
                continue
            candidate = cmd.model_copy(
                update={
                    "plan_id": revision.plan_id,
                    "expected_revision_ref": DecisionSourceRefV1(
                        ref=revision.revision_id, hash=revision.payload_hash
                    ),
                }
            )
            _, _, view = _origin(
                session, receipt.owner_id, candidate, at, require_current=True, include_view=True
            )
            candidates.append(view)
        return (
            max(
                candidates,
                key=lambda v: (v["revision"]["published_at"], v["revision"]["revision_id"]),
            )
            if candidates
            else None
        )

    def _notice(self, session, receipt, progress, status, at):
        changed = status == "changed"
        message = make_notification_envelope(
            subject_kind="action_revision",
            subject_id=progress["plan_revision_ref"]["ref"],
            channel=self.channel,
            dedupe_key="perpetual-plan-continuous:"
            + canonical_hash(
                [
                    receipt.watch_id,
                    receipt.revision.model_dump(mode="json"),
                    progress["plan_revision_ref"],
                    status,
                    self.channel,
                ]
            ),
            subject="持续跟踪的研究结果已变化" if changed else "持续跟踪的研究方案状态已变化",
            body=f"{receipt.command.horizon} 方案状态：{status}。"
            f"原研究 Run：{progress['origin_run_id']}。仅研究提醒，不授权交易。",
            created_at=at,
        )
        return OutboxService(self.database).enqueue_subject(message, session=session)

    def discover(self):
        with self.database.session() as session:
            due_ids = [row.request_id for row in self._due_commands(session)]
        prepared_conditions = self._prepare_conditions(due_ids)
        prepared_events = self._prepare_event_scenarios(due_ids)
        at = aware_utc(self.now())
        with self.database.session() as session:
            begin_immediate(session)
            rows = [session.get(Command, key, populate_existing=True) for key in due_ids]
            for row in rows:
                if row is None:
                    continue
                current = _latest(session, row.target_id)
                if (
                    current is None
                    or current.revision.ref != row.request_id
                    or current.state != "active"
                ):
                    continue
                receipt = _read(session, row.owner_id, json.loads(row.result_json)["request_id"])
                progress = _latest_progress(session, receipt.watch_id)
                if progress is None:
                    raise ValueError("perpetual_continuous_progress_missing")
                _check_origin(session, receipt, progress, at, require_current=False)
                if not _settings_current(session, receipt.owner_id, receipt.command):
                    if progress["status"] != "settings_changed":
                        notice = self._notice(session, receipt, progress, "settings_changed", at)
                        self._append(
                            session,
                            receipt,
                            progress,
                            "settings_changed",
                            at,
                            previous=progress,
                            outbox_ids=[notice],
                        )
                    continue
                successor = self._successor(session, receipt, progress, at)
                if successor:
                    value = self._from_view(receipt, successor)
                    notices = (
                        [self._notice(session, receipt, value, "changed", at)]
                        if value["result_hash"] != progress["result_hash"]
                        else []
                    )
                    progress = self._append(
                        session,
                        receipt,
                        value,
                        "following",
                        at,
                        previous=progress,
                        outbox_ids=notices,
                    )
                if progress["status"] == "following":
                    condition_key = (receipt.revision.ref, progress["plan_revision_ref"]["ref"])
                    observation = self.parent.condition_monitor.observe(
                        session, receipt, progress, prepared_conditions.get(condition_key)
                    )
                    if observation and observation["new_exposure_blocked"]:
                        progress = self._append(
                            session,
                            receipt,
                            progress,
                            "invalidated",
                            at,
                            previous=progress,
                        )
                head = ActionRepository(session).get_head(progress["plan_id"])
                event_key = (receipt.revision.ref, progress["plan_revision_ref"]["ref"])
                self.parent.event_scenario_watch.observe(
                    session, receipt, progress, prepared_events.get(event_key)
                )
                if progress["status"] not in {"following", "invalidated"}:
                    continue
                if (
                    head is None
                    or head.current_revision_id != progress["plan_revision_ref"]["ref"]
                    or head.state in {"invalidated", "superseded"}
                ):
                    if progress["status"] != "invalidated":
                        notice = self._notice(session, receipt, progress, "invalidated", at)
                        self._append(
                            session,
                            receipt,
                            progress,
                            "invalidated",
                            at,
                            previous=progress,
                            outbox_ids=[notice],
                        )
                    continue
                expiry = datetime.fromisoformat(progress["review_due_at"].replace("Z", "+00:00"))
                if at < expiry:
                    continue
                status, key = "expired", None
                if receipt.command.mode == "remind_and_research":
                    key = self.parent.condition_monitor._pending_successor(
                        session, receipt, progress, at
                    )
                if receipt.command.mode == "remind_and_research" and key is None:
                    slot = next_hour(expiry)
                    source = PerpetualPlanReviewTriggerV1(
                        schema_version="perpetual-plan-review-trigger.v1",
                        watch_id=receipt.watch_id,
                        watch_ref=receipt.revision,
                        owner_id=receipt.owner_id,
                        partition=receipt.command.partition,
                        horizon=receipt.command.horizon,
                        motive="revision_expired",
                        expired_at=expiry,
                        observed_at=at,
                        scheduled_at=slot,
                        deadline_at=next_hour(slot),
                        recorded_at=at,
                        **{k: progress[k] for k in ORIGIN_KEYS},
                    )
                    body = source.model_dump(mode="json")
                    trigger_key = "perpetual_plan_review:" + canonical_hash(
                        [receipt.watch_id, body["watch_ref"], body["plan_revision_ref"]]
                    )
                    instrument = source.partition.instrument_id.removeprefix("okx:")
                    scope = f"crypto:perp:okx:{instrument}:trade:1m"
                    key = "riskresearch_" + canonical_hash([scope, trigger_key])[:40]
                    missed = at >= source.deadline_at
                    session.add(
                        Request(
                            request_id=key,
                            protocol_version=PROTOCOL,
                            scope_id=scope,
                            instrument=source.partition.instrument_id.removeprefix("okx:"),
                            status="failed" if missed else "pending_admission",
                            created_at=at,
                            updated_at=at,
                            available_at=max(at, slot),
                            attempts=0,
                            lease_generation=0,
                            error_code="perpetual_watch_slot_expired" if missed else None,
                        )
                    )
                    session.add(
                        Trigger(
                            trigger_key=trigger_key,
                            request_id=key,
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
                notice = self._notice(session, receipt, progress, status, at)
                self._append(
                    session,
                    receipt,
                    progress,
                    status,
                    at,
                    previous=progress,
                    request_id=key,
                    outbox_ids=[notice],
                )
        if rows:
            # Advance only after a successful transaction, never on rollback.
            self.parent._continuous_scan_after = rows[-1].target_id

    def _due_commands(self, session):
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
        terminal = aliased(Command)
        eligible = (
            select(Command)
            .join(ranked, ranked.c.request_id == Command.request_id)
            .where(
                ranked.c.n == 1,
                func.json_extract(Command.result_json, "$.state") == "active",
                ~select(terminal.request_id)
                .where(
                    terminal.kind == PROGRESS,
                    terminal.target_id == Command.target_id,
                    func.json_extract(terminal.result_json, "$.status") == "settings_changed",
                    func.json_extract(terminal.result_json, "$.watch_ref.ref")
                    == Command.request_id,
                )
                .exists(),
            )
            .order_by(Command.target_id)
        )
        # The parent survives helper construction on each discovery tick.
        # Resetting this hint on restart merely rechecks durable originals.
        after = self.parent._continuous_scan_after
        rows = session.scalars(
            eligible.where(Command.target_id > after).limit(32) if after else eligible.limit(32)
        ).all()
        if after and len(rows) < 32:
            rows.extend(
                session.scalars(
                    eligible.where(Command.target_id <= after).limit(32 - len(rows))
                ).all()
            )
        return rows

    def _prepare_event_scenarios(self, due_ids):
        """Use the same consent batch and prepare originals before the writer."""
        with self.database.session() as session:
            active = []
            for key in due_ids:
                row = session.get(Command, key, populate_existing=True)
                if row is None:
                    continue
                receipt = _latest(session, row.target_id)
                if (
                    receipt is None
                    or receipt.revision.ref != row.request_id
                    or receipt.state != "active"
                ):
                    continue
                progress = _latest_progress(session, receipt.watch_id)
                if progress and progress["status"] in {
                    "following", "invalidated", "expired", "slot_missed"
                }:
                    active.append((receipt, progress))
        return {
            (receipt.revision.ref, progress["plan_revision_ref"]["ref"]):
            self.parent.event_scenario_watch.prepare_inputs(receipt, progress)
            for receipt, progress in active
        }

    def _prepare_conditions(self, due_ids):
        """Prepare exactly the same bounded cursor batch that the writer consumes."""
        with self.database.session() as session:
            active = []
            for key in due_ids:
                row = session.get(Command, key, populate_existing=True)
                if row is None:
                    continue
                receipt = _latest(session, row.target_id)
                if (
                    receipt is None
                    or receipt.revision.ref != row.request_id
                    or receipt.state != "active"
                ):
                    continue
                progress = _latest_progress(session, receipt.watch_id)
                if progress and progress["status"] == "following":
                    active.append((receipt, progress))
        result = {}
        monitor = self.parent.condition_monitor
        for receipt, progress in active:
            with self.database.session() as session:
                policy_row, policy, _declarations = monitor.read_policy(session, receipt, progress)
            if policy_row is None or policy["reason_code"]:
                prepared = monitor.prepare_policy(
                    receipt.owner_id,
                    _command_for(receipt, progress),
                    source_run_id=progress["origin_run_id"],
                )
                with self.database.session() as session:
                    begin_immediate(session)
                    current = _latest(session, receipt.watch_id)
                    latest = _latest_progress(session, receipt.watch_id)
                    if (
                        current.revision != receipt.revision
                        or latest["plan_revision_ref"] != progress["plan_revision_ref"]
                    ):
                        continue
                    monitor.install_policy(session, receipt, prepared)
            result[(receipt.revision.ref, progress["plan_revision_ref"]["ref"])] = (
                monitor.prepare_inputs(receipt, progress)
            )
        return result


def read_continuous_source_in_session(session, row, trigger, source, stored, *, at, require_active):
    if source.schema_version == "perpetual-plan-review-trigger.v3":
        from .event_scenario_watch import read_event_scenario_review_source_in_session

        return read_event_scenario_review_source_in_session(
            session, row, trigger, source, stored, at=at, require_active=require_active
        )
    if source.schema_version == "perpetual-plan-review-trigger.v2":
        from .perpetual_condition_monitor import read_condition_review_source_in_session

        return read_condition_review_source_in_session(
            session,
            row,
            trigger,
            source,
            stored,
            at=at,
            require_active=require_active,
        )
    receipt = _read(session, source.owner_id, json.loads(stored.result_json)["request_id"])
    body = source.model_dump(mode="json")
    scope = f"crypto:perp:okx:{source.partition.instrument_id.removeprefix('okx:')}:trade:1m"
    trigger_key = "perpetual_plan_review:" + canonical_hash(
        [receipt.watch_id, body["watch_ref"], body["plan_revision_ref"]]
    )
    proof = None
    for status in ("expired", "slot_missed"):
        proof = _read_progress(
            session,
            session.get(
                Command,
                _progress_id(receipt, body["plan_revision_ref"], status),
                populate_existing=True,
            ),
        )
        if proof:
            break
    if (
        proof is None
        or receipt.revision != source.watch_ref
        or receipt.watch_id != source.watch_id
        or receipt.state != "active"
        or receipt.command.mode != "remind_and_research"
        or receipt.command.input_policy != INPUT_POLICY
        or receipt.command.partition != source.partition
        or receipt.command.horizon != source.horizon
        or trigger.kind != "perpetual_plan_review"
        or trigger.source_ref != source.watch_id
        or trigger.payload_json != canonical(body)
        or trigger.payload_hash != canonical_hash(body)
        or trigger.trigger_key != trigger_key
        or row.request_id != "riskresearch_" + canonical_hash([scope, trigger_key])[:40]
        or row.scope_id != scope
        or trigger.scope_id != scope
        or row.instrument != source.partition.instrument_id.removeprefix("okx:")
        or source.scheduled_at != next_hour(source.expired_at)
        or source.deadline_at != next_hour(source.scheduled_at)
        or not receipt.accepted_at <= source.recorded_at <= at
        or not source.expired_at <= source.observed_at == source.recorded_at
        or as_utc(row.created_at) != source.recorded_at
        or as_utc(trigger.recorded_at) != source.recorded_at
        or proof["request_id"] != row.request_id
        or proof["review_due_at"] != wire(source.expired_at)
        or any(proof[k] != body[k] for k in ORIGIN_KEYS)
    ):
        raise ValueError("perpetual_continuous_trigger_invalid")
    _check_origin(session, receipt, proof, at, require_current=require_active)
    if require_active:
        current, progress = (
            _latest(session, receipt.watch_id),
            _latest_progress(session, receipt.watch_id),
        )
        if (
            current.revision != receipt.revision
            or current.state != "active"
            or progress["plan_revision_ref"] != proof["plan_revision_ref"]
            or progress["status"] not in {"expired", "slot_missed"}
            or progress["watch_ref"] != proof["watch_ref"]
        ):
            raise ValueError("perpetual_continuous_changed_or_paused")
        if not _settings_current(session, receipt.owner_id, receipt.command):
            raise ValueError("perpetual_continuous_settings_changed")
        if at >= source.deadline_at:
            raise ValueError("perpetual_watch_slot_expired")
        if at < source.scheduled_at:
            raise ValueError("perpetual_watch_slot_not_due")
    return dict(
        request_id=row.request_id,
        trigger_ref=dict(ref=trigger.trigger_key, hash=trigger.payload_hash),
        watch_ref=body["watch_ref"],
        source=body,
    )
