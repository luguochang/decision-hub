"""Event sidecar selection inside the existing continuing watch transaction.

Actual native review/publication ownership is supplied by the publication
reader. This component authenticates source captures, preserves watch/head
ownership and appends selections; it never creates an executable order.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from sqlalchemy import func, select

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSourceRefV1,
    ScenarioBranchSelectionV1,
)

from ..decision.event_scenario_action_plan import _key, _matches
from ..persistence.action_repository import ActionRepository
from ..persistence.db import ProductCommandRecord as Command
from ..persistence.db import as_utc
from ..persistence.original_preparation import prepare_originals
from ..ports.exact_decimal_text import number
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.perpetual_plan_watch import PerpetualPlanReviewTriggerV3, condition_research_authorized
from ..ports.sources import make_notification_envelope
from .event_scenario_realization import read_event_scenario_actual_inputs_in_session
from .outbox import OutboxService
from .perpetual_condition_inputs import read_perpetual_condition_inputs_in_session
from .perpetual_research_binding import canonical

KIND = "event_scenario_selection_v1"


def _wire(value):
    return aware_utc(value).isoformat().replace("+00:00", "Z")


def _reference(name, body):
    return dict(ref=name, hash=canonical_hash(body))


def _record_key(owner, plan_ref, selection_key):
    return "eventselection_" + canonical_hash([owner, plan_ref, selection_key])


def read_event_scenario_selection_in_session(session, reference):
    """Read this writer's immutable selection record, not a caller-selected flag."""
    row = session.get(Command, reference["ref"], populate_existing=True)
    if row is None or row.kind != KIND:
        raise ValueError("event_selection_original_missing")
    body = json.loads(row.result_json)
    if (
        row.payload_hash != reference["hash"]
        or canonical_hash(body) != row.payload_hash
        or canonical(body) != row.result_json
        or body["owner_id"] != row.owner_id
        or row.target_id != body["event_plan_id"]
        or _record_key(
            row.owner_id, body["selection"]["plan_ref"], body["selection"]["selection_key"]
        )
        != row.request_id
        or as_utc(row.created_at)
        != datetime.fromisoformat(body["recorded_at"].replace("Z", "+00:00"))
    ):
        raise ValueError("event_selection_original_binding_invalid")
    ScenarioBranchSelectionV1.model_validate(body["selection"])
    return body


def event_scenario_advice_view_in_session(session, *, bundle, at):
    if bundle is None:
        return None
    row = session.scalar(
        select(Command)
        .where(
            Command.kind == KIND,
            Command.owner_id == bundle["owner_id"],
            Command.target_id == bundle["plan"]["plan_id"],
            Command.created_at <= aware_utc(at),
            func.json_extract(Command.result_json, "$.selection.plan_ref.hash")
            == bundle["plan_ref"]["hash"],
        )
        .order_by(Command.created_at.desc(), Command.request_id.desc())
        .limit(1)
    )
    record = (
        read_event_scenario_selection_in_session(
            session, _reference(row.request_id, json.loads(row.result_json))
        )
        if row
        else None
    )
    return dict(
        origin_run_id=bundle["origin_run_id"],
        plan=bundle["plan"],
        plan_ref=bundle["plan_ref"],
        review_scope=bundle["review_scope"],
        review_authorization_ref=bundle["review_authorization_ref"],
        publication_ref=bundle["publication_ref"],
        selection=record["selection"] if record else None,
        selection_ref=dict(ref=row.request_id, hash=row.payload_hash) if row else None,
        selection_original=record,
        read_at=_wire(at),
        current_state=(
            "expired"
            if aware_utc(at)
            >= datetime.fromisoformat(bundle["review_scope"]["valid_until"].replace("Z", "+00:00"))
            else "invalidated"
            if record and record["selection"]["status"] == "invalidated"
            else "active"
        ),
    )


def _retained_origin_progress(session, receipt, bundle):
    """Only a primary already followed under this exact continuing consent."""
    from .perpetual_plan_watch_continuous import PROGRESS, _read_progress

    row = session.scalar(
        select(Command)
        .where(
            Command.kind == PROGRESS,
            Command.owner_id == receipt.owner_id,
            Command.target_id == receipt.watch_id,
            func.json_extract(Command.result_json, "$.watch_ref.ref") == receipt.revision.ref,
            func.json_extract(Command.result_json, "$.origin_run_id") == bundle["origin_run_id"],
            func.json_extract(Command.result_json, "$.plan_id").in_(bundle["plan_ids"]),
        )
        .order_by(func.json_extract(Command.result_json, "$.sequence").desc())
        .limit(1)
    )
    return _read_progress(session, row) if row else None


class EventScenarioWatch:
    def __init__(self, parent):
        self.parent = parent
        self.database, self.now, self.channel = parent.database, parent.now, parent.channel
        self.source = getattr(getattr(parent.entry, "binding", None), "source", None)

    def _quote(self, session, bundle, branch, position, at):
        inputs = branch["comparison_inputs_by_position"][position]
        reference = inputs["price_bridge"]["forecast_price_ref"]
        rule = bundle["originals"].get(reference["ref"])
        expected = dict(
            schema_version="event-scenario-target-start-price.v1",
            kind="target_start_close",
            target_ref=branch["target_ref"],
            price_basis="trade_1m_close",
            ratio_rounding="decimal18_half_even",
        )
        shared = dict(
            schema_version="event-scenario-target-start-price.v1",
            kind="target_start_close",
            event_key=bundle["plan"]["event"]["event_key"],
            instrument_ref=bundle["plan"]["context"]["instrument_ref"],
            window_start=branch["target"]["window_start"],
            window_end=branch["target"]["window_end"],
            anchor=branch["target"]["anchor"],
            price_basis="trade_1m_close",
            ratio_rounding="decimal18_half_even",
        )
        if rule not in (expected, shared) or canonical_hash(rule) != reference["hash"]:
            raise ValueError("event_selection_target_start_price_rule_unavailable")
        if self.source is None:
            raise ValueError("event_selection_price_source_unavailable")
        closes = read_perpetual_condition_inputs_in_session(
            session,
            instrument_ref=DecisionSourceRefV1.model_validate(
                bundle["plan"]["context"]["instrument_ref"]
            ),
            scope_id=bundle["plan"]["context"]["scope_id"],
            as_of=at,
            source=self.source,
        ).closes
        start = datetime.fromisoformat(branch["target"]["window_start"].replace("Z", "+00:00"))
        anchors = [row for row in closes if row.close_at == start]
        current = [row for row in closes if start <= row.close_at <= at]
        if len(anchors) != 1 or not current:
            raise ValueError("event_selection_target_start_or_current_close_missing")
        entry, latest = anchors[0], max(current, key=lambda row: row.close_at)
        with localcontext() as context:
            context.prec = 80
            ratio = number(
                (Decimal(latest.price) / Decimal(entry.price)).quantize(
                    Decimal("0.000000000000000001"), rounding=ROUND_HALF_EVEN
                )
            )

        def close_body(row):
            return {
                key: _wire(value) if isinstance(value, datetime) else value
                for key, value in asdict(row).items()
            }

        original = dict(
            schema_version="event-scenario-price-bridge-original.v1",
            rule_ref=reference,
            target_start=close_body(entry),
            current=close_body(latest),
            ratio=ratio,
        )
        quote = dict(
            quote_ref=_reference("event-price-bridge:" + canonical_hash(original), original),
            forecast_price_ref=reference,
            observed_at=_wire(latest.close_at),
            known_at=_wire(max(entry.recorded_at, latest.recorded_at)),
            comparison_to_forecast_price_ratio=ratio,
        )
        return quote, original

    def _current_account(self, session, bundle, branch, receipt):
        plan, originals = bundle["plan"], bundle["originals"]
        position_ref, account_ref = plan["context"]["position_ref"], branch["account_ref"]
        position_body = originals.get(position_ref["ref"]) if position_ref else None
        position = (
            position_body.get("state", "unknown") if isinstance(position_body, dict) else "unknown"
        )
        if receipt.command.partition.account_kind == "paper":
            if self.source is None:
                raise ValueError("event_selection_current_account_source_unavailable")
            from .perpetual_paper import PerpetualPaperService

            service = PerpetualPaperService(self.database, self.source, now=self.now)
            account, current_position = service.account_reader(
                session, receipt.owner_id, receipt.command.partition
            )
            account_body, current_position_body = (
                account.model_dump(mode="json"),
                current_position.model_dump(mode="json"),
            )
            if account_ref is not None:
                account_ref = dict(ref=account_ref["ref"], hash=canonical_hash(account_body))
            if position_ref is not None:
                position_ref = dict(
                    ref=position_ref["ref"], hash=canonical_hash(current_position_body)
                )
            position = current_position.state
        return (
            position if position in {"flat", "long", "short"} else "flat",
            account_ref,
            position_ref,
        )

    def prepare_inputs(self, receipt, progress):
        from .event_scenario_publication import (
            read_latest_published_event_scenario_plan_in_session,
            read_published_event_scenario_plan_in_session,
        )

        at = aware_utc(self.now())

        def read(session):
            bundle = read_published_event_scenario_plan_in_session(
                session, run_id=progress["origin_run_id"], owner_id=receipt.owner_id, at=at
            )
            if bundle is None:
                bundle = read_latest_published_event_scenario_plan_in_session(
                    session,
                    owner_id=receipt.owner_id,
                    settings_ref=receipt.command.expected_settings_revision.model_dump(mode="json"),
                    partition=receipt.command.partition.model_dump(mode="json"),
                    horizon=receipt.command.horizon,
                    at=at,
                )
            if bundle is None:
                return None
            origin = progress
            if bundle["origin_run_id"] != progress["origin_run_id"]:
                origin = _retained_origin_progress(session, receipt, bundle)
                if origin is None:
                    return None
            if (
                origin["plan_id"] not in bundle["plan_ids"]
                or bundle["owner_id"] != receipt.owner_id
                or bundle["origin_run_id"] != origin["origin_run_id"]
            ):
                raise ValueError("event_selection_published_watch_origin_mismatch")
            plan = bundle["plan"]
            result = dict(
                bundle=bundle,
                origin_progress=origin,
                realization=None,
                source_evidence=None,
                quote=None,
                quote_original=None,
                reason_codes=[],
                account_unknown=False,
            )
            try:
                if self.source is None or not hasattr(self.source, "archive"):
                    raise ValueError("event_actual_archive_unavailable")
                actual = read_event_scenario_actual_inputs_in_session(
                    session,
                    plan=plan,
                    originals=bundle["originals"],
                    cutoff_at=at,
                    archive=self.source.archive,
                )
                if actual is not None:
                    result.update(actual)
            except (ValueError, OSError) as error:
                result["reason_codes"].append(str(error)[:256])
            branches = plan["branches"]
            if not branches:
                return result
            branch = branches[0]
            if result["realization"] is not None:
                values = {
                    _key(row): Decimal(row["value"]) for row in result["realization"]["metrics"]
                }
                needed = {
                    _key(term)
                    for row in plan["scenario_predicates"]
                    for clause in row["predicate"]["any_of"]
                    for term in clause["all_of"]
                }
                if needed <= values.keys():
                    matches = {
                        row["scenario_id"]
                        for row in plan["scenario_predicates"]
                        if _matches(row["predicate"], values)
                    }
                    covered = [row for row in branches if row["scenario_id"] in matches]
                    if len(covered) == 1:
                        branch = covered[0]
            position, account_ref, position_ref = (
                "flat",
                branch["account_ref"],
                plan["context"]["position_ref"],
            )
            try:
                position, account_ref, position_ref = self._current_account(
                    session, bundle, branch, receipt
                )
            except (ValueError, OSError) as error:
                result["reason_codes"].append(str(error)[:256])
                result["account_unknown"] = True
            try:
                result["quote"], result["quote_original"] = self._quote(
                    session, bundle, branch, position, at
                )
            except (ValueError, OSError) as error:
                result["reason_codes"].append(str(error)[:256])
            result["inputs"] = dict(
                schema_version="event-scenario-selection-inputs.v1",
                plan_ref=bundle["plan_ref"],
                review_scope=bundle["review_scope"],
                review_authorization_ref=bundle["review_authorization_ref"],
                realization_ref=(
                    _reference(
                        "event-realization:" + canonical_hash(result["realization"]),
                        result["realization"],
                    )
                    if result["realization"] is not None
                    else None
                ),
                position_branch=position,
                quote=result["quote"],
                cost_ref=branch["cost_ref"],
                funding_ref=branch["funding_ref"],
                margin_ref=branch["margin_ref"],
                fill_assumption_ref=branch["fill_assumption_ref"],
                account_ref=account_ref,
                settings_ref=plan["context"]["settings_ref"],
                position_ref=position_ref,
                domain_values=[],
                requires_conditional_fill=False,
            )
            return result

        return prepare_originals(self.database, read)

    def _conditions(self, session, receipt, progress):
        observation = self.parent.condition_monitor.latest_observation(session, receipt, progress)
        if observation is None:
            return []
        reference = _reference(
            "perpconditionobservation_" + canonical_hash(observation), observation
        )
        result = []
        for row in observation["conditions"]:
            state = "unknown"
            if row["status"] == "invalidated":
                state = "invalidated"
            elif row["purpose"] == "confirmation" and row["status"] == "confirmed":
                state = "satisfied"
            elif row["purpose"] == "invalidation" and row["truth"] == "false":
                state = "satisfied"
            result.append(
                dict(
                    evaluation_ref=reference,
                    assumption_ref=row["assumption_ref"],
                    state=state,
                    evaluated_at=observation["observed_at"],
                    known_at=observation["observed_at"],
                    evidence_refs=[row["first_confirmed_bar_ref"]]
                    if row["first_confirmed_bar_ref"]
                    else [],
                )
            )
        return result

    def observe(self, session, receipt, progress, prepared):
        if prepared is None:
            return None
        prepared._require_issued(session)
        prepared.verify(session)
        value = prepared.value
        if value is None or "inputs" not in value:
            return None
        from .perpetual_plan_watch_continuous import _check_origin, _latest, _latest_progress

        current, latest = (
            _latest(session, receipt.watch_id),
            _latest_progress(session, receipt.watch_id),
        )
        origin = value["origin_progress"]
        head = ActionRepository(session).get_head(origin["plan_id"])
        if (
            current is None
            or current.revision != receipt.revision
            or current.state != "active"
            or latest is None
            or latest["plan_revision_ref"] != progress["plan_revision_ref"]
            or head is None
            or head.current_revision_id != origin["plan_revision_ref"]["ref"]
        ):
            raise ValueError("event_selection_watch_or_head_changed")
        from ..ports.event_scenario_action_plan import select_event_scenario_action_plan

        at = aware_utc(self.now())
        _check_origin(session, receipt, origin, at, require_current=True)
        progress = origin
        bundle = value["bundle"]
        evaluations = self._conditions(session, receipt, progress)
        selection = select_event_scenario_action_plan(
            bundle["plan"], value["realization"], evaluations, value["inputs"], as_of=_wire(at)
        )
        from .perpetual_condition_monitor import condition_new_exposure_blocked_in_session

        blocked = condition_new_exposure_blocked_in_session(
            session,
            owner_id=receipt.owner_id,
            plan_id=progress["plan_id"],
            revision_ref=progress["plan_revision_ref"],
        )
        if (
            head.state in {"invalidated", "superseded"}
            or progress["status"] == "invalidated"
            or blocked
            or value["account_unknown"]
        ):
            selection.update(
                status="invalidated"
                if progress["status"] == "invalidated"
                or blocked
                or head.state in {"invalidated", "superseded"}
                else "pending_evidence",
                selected_candidate_ref=None,
                selected_comparison_ref=None,
                selected_action_id=None,
                recomparison=None,
                unit_expected_net_if_assumed_fills_usdt=None,
            )
            selection["unknown_reasons"].append(
                "watch_or_head_invalidated"
                if selection["status"] == "invalidated"
                else "current_account_original_unknown"
            )
        selection["unknown_reasons"] = sorted(
            set(selection["unknown_reasons"] + value["reason_codes"])
        )
        # Re-key after application-level invalidation/current-account guards.
        selection["selection_key"] = canonical_hash(
            {
                key: item
                for key, item in selection.items()
                if key
                not in {"selection_key", "observed_at", "recomparison", "selected_comparison_ref"}
            }
        )
        key = _record_key(receipt.owner_id, bundle["plan_ref"], selection["selection_key"])
        old = session.get(Command, key, populate_existing=True)
        if old is not None:
            return read_event_scenario_selection_in_session(
                session, dict(ref=old.request_id, hash=old.payload_hash)
            )
        outbox_ids = []
        if selection["status"] in {
            "selected_preapproved_branch",
            "invalidated",
            "uncovered_requires_research",
            "unsupported_fill_distribution",
        }:
            status_label = {
                "selected_preapproved_branch": "已匹配事前复核方案",
                "invalidated": "原条件方案已失效",
                "uncovered_requires_research": "已超出复核范围，需重新研究",
                "unsupported_fill_distribution": "条件成交分布尚未支持",
            }[selection["status"]]
            action_label = {
                "open_long": "开多",
                "open_short": "开空",
                "no_change": "维持",
                "reduce_half": "减半",
                "close": "平仓",
                None: "待重新研究",
            }.get(selection["selected_action_id"], "待重新研究")
            message = make_notification_envelope(
                subject_kind="action_revision",
                subject_id=progress["plan_revision_ref"]["ref"],
                channel=self.channel,
                dedupe_key="event-scenario-selection:" + key,
                subject="事件情景方案更新",
                body=(
                    f"已核对事件情景方案：{status_label}。"
                    f"条件首选：{action_label}。仅研究提醒，未授权执行。"
                ),
                created_at=at,
            )
            outbox_ids.append(
                OutboxService(self.database).enqueue_subject(message, session=session)
            )
        body = dict(
            schema_version="event-scenario-watch-selection.v1",
            owner_id=receipt.owner_id,
            watch_id=receipt.watch_id,
            watch_ref=receipt.revision.model_dump(mode="json"),
            plan_id=progress["plan_id"],
            plan_revision_ref=progress["plan_revision_ref"],
            event_plan_id=bundle["plan"]["plan_id"],
            origin_run_id=progress["origin_run_id"],
            publication_ref=bundle["publication_ref"],
            selection=selection,
            realization=value["realization"],
            source_evidence=value["source_evidence"],
            quote_original=value["quote_original"],
            condition_evaluations=evaluations,
            recorded_at=_wire(at),
            outbox_ids=outbox_ids,
        )
        session.add(
            Command(
                request_id=key,
                owner_id=receipt.owner_id,
                target_id=body["event_plan_id"],
                kind=KIND,
                payload_hash=canonical_hash(body),
                result_json=canonical(body),
                created_at=at,
            )
        )
        session.flush()
        if selection["status"] == "uncovered_requires_research":
            self._successor(session, receipt, progress, bundle, body, key, at)
        return body

    def _successor(self, session, receipt, progress, bundle, record, key, at):
        """One exact uncovered motive, separately covered by condition-research consent."""
        if not condition_research_authorized(receipt):
            return None
        until = datetime.fromisoformat(bundle["review_scope"]["valid_until"].replace("Z", "+00:00"))
        if at >= until:
            return None
        pending = self.parent.condition_monitor._pending_successor(session, receipt, progress, at)
        if pending:
            return pending
        from ..persistence.risk_research_records import RiskResearchRequestRecord as Request
        from ..persistence.risk_research_records import RiskResearchTriggerRecord as Trigger
        from .perpetual_plan_followup import ORIGIN_KEYS, PROTOCOL

        selection_ref = _reference(key, record)
        source = PerpetualPlanReviewTriggerV3(
            schema_version="perpetual-plan-review-trigger.v3",
            watch_id=receipt.watch_id,
            watch_ref=receipt.revision,
            owner_id=receipt.owner_id,
            partition=receipt.command.partition,
            horizon=receipt.command.horizon,
            motive="event_scenario_uncovered",
            expired_at=None,
            event_plan_ref=bundle["plan_ref"],
            selection_ref=selection_ref,
            observed_at=at,
            scheduled_at=at,
            deadline_at=until,
            recorded_at=at,
            **{name: progress[name] for name in ORIGIN_KEYS},
        )
        body = source.model_dump(mode="json")
        trigger_key = "perpetual_plan_review:" + canonical_hash(
            [body["watch_ref"], body["plan_revision_ref"], body["selection_ref"]]
        )
        inst = source.partition.instrument_id.removeprefix("okx:")
        scope = f"crypto:perp:okx:{inst}:trade:1m"
        request_id = "riskresearch_" + canonical_hash([scope, trigger_key])[:40]
        session.add(
            Request(
                request_id=request_id,
                protocol_version=PROTOCOL,
                scope_id=scope,
                instrument=inst,
                status="pending_admission",
                created_at=at,
                updated_at=at,
                available_at=at,
                attempts=0,
                lease_generation=0,
            )
        )
        session.add(
            Trigger(
                trigger_key=trigger_key,
                request_id=request_id,
                kind="perpetual_plan_review",
                scope_id=scope,
                source_ref=receipt.watch_id,
                payload_json=canonical(body),
                payload_hash=canonical_hash(body),
                recorded_at=at,
            )
        )
        return request_id


def read_event_scenario_review_source_in_session(
    session,
    row,
    trigger,
    source,
    stored,
    *,
    at,
    require_active,
):
    """Authenticate the immutable uncovered selection and its continuing consent."""
    from .event_scenario_publication import read_published_event_scenario_plan_in_session
    from .perpetual_plan_followup import ORIGIN_KEYS, _settings_current
    from .perpetual_plan_watch_continuous import _check_origin, _latest, _latest_progress, _read

    receipt = _read(session, source.owner_id, json.loads(stored.result_json)["request_id"])
    source_body = source.model_dump(mode="json")
    record = read_event_scenario_selection_in_session(session, source_body["selection_ref"])
    bundle = read_published_event_scenario_plan_in_session(
        session, run_id=source.origin_run_id, owner_id=source.owner_id, at=at
    )
    if bundle is None:
        raise ValueError("event_selection_trigger_publication_missing")
    observed = datetime.fromisoformat(record["recorded_at"].replace("Z", "+00:00"))
    until = datetime.fromisoformat(bundle["review_scope"]["valid_until"].replace("Z", "+00:00"))
    trigger_key = "perpetual_plan_review:" + canonical_hash(
        [source_body["watch_ref"], source_body["plan_revision_ref"], source_body["selection_ref"]]
    )
    inst = source.partition.instrument_id.removeprefix("okx:")
    scope = f"crypto:perp:okx:{inst}:trade:1m"
    if (
        record["selection"]["status"] != "uncovered_requires_research"
        or record["selection"]["plan_ref"] != source_body["event_plan_ref"]
        or bundle["plan_ref"] != source_body["event_plan_ref"]
        or record["owner_id"] != source.owner_id
        or record["watch_ref"] != source_body["watch_ref"]
        or record["plan_revision_ref"] != source_body["plan_revision_ref"]
        or record["origin_run_id"] != source.origin_run_id
        or record["publication_ref"] != bundle["publication_ref"]
        or record["plan_id"] not in bundle["plan_ids"]
        or receipt.revision != source.watch_ref
        or receipt.watch_id != source.watch_id
        or not condition_research_authorized(receipt)
        or receipt.state != "active"
        or receipt.command.partition != source.partition
        or receipt.command.horizon != source.horizon
        or not receipt.accepted_at <= observed <= at
        or source.observed_at != observed
        or source.recorded_at != observed
        or source.scheduled_at != observed
        or source.deadline_at != until
        or source.motive != "event_scenario_uncovered"
        or trigger.kind != "perpetual_plan_review"
        or trigger.source_ref != source.watch_id
        or trigger.payload_json != canonical(source_body)
        or trigger.payload_hash != canonical_hash(source_body)
        or trigger.trigger_key != trigger_key
        or row.request_id != "riskresearch_" + canonical_hash([scope, trigger_key])[:40]
        or row.scope_id != scope
        or trigger.scope_id != scope
        or row.instrument != inst
        or as_utc(row.created_at) != observed
        or as_utc(trigger.recorded_at) != observed
    ):
        raise ValueError("event_selection_trigger_original_invalid")
    revision = ActionRepository(session).get_revision(record["plan_revision_ref"]["ref"])
    if revision is None:
        raise ValueError("event_selection_trigger_revision_missing")
    proof = dict(
        plan_id=record["plan_id"],
        review_due_at=_wire(revision.valid_until),
        **{name: source_body[name] for name in ORIGIN_KEYS},
    )
    _check_origin(session, receipt, proof, at, require_current=require_active)
    if require_active:
        current, progress = (
            _latest(session, receipt.watch_id),
            _latest_progress(session, receipt.watch_id),
        )
        retained = _retained_origin_progress(session, receipt, bundle)
        if (
            current.revision != receipt.revision
            or current.state != "active"
            or progress["watch_ref"] != source_body["watch_ref"]
            or retained is None
            or retained["plan_revision_ref"] != source_body["plan_revision_ref"]
            or not _settings_current(session, receipt.owner_id, receipt.command)
            or not source.scheduled_at <= at < source.deadline_at
        ):
            raise ValueError("event_selection_trigger_changed_or_expired")
    return dict(
        request_id=row.request_id,
        trigger_ref=dict(ref=trigger_key, hash=trigger.payload_hash),
        watch_ref=source_body["watch_ref"],
        source=source_body,
    )
