"""SameRun reviewed policies in the existing perpetual watch transaction.

Only this watch writes policy/observation/transition originals. No probabilities,
financial publication originals or existing PAPER protection are changed.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func, select

from packages.contracts_py.decision_hub_contracts.generated import decision_product_v2

from ..decision.perpetual_conditions import decode_perpetual_condition, evaluate_perpetual_condition
from ..persistence.action_repository import ActionRepository
from ..persistence.db import ProductCommandRecord as Command
from ..persistence.db import as_utc
from ..persistence.original_preparation import prepare_originals
from ..persistence.risk_research_records import RiskResearchRequestRecord as Request
from ..persistence.risk_research_records import RiskResearchTriggerRecord as Trigger
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.perpetual_plan_watch import PerpetualPlanReviewTriggerV2, condition_research_authorized
from ..ports.sources import make_notification_envelope
from .outbox import OutboxService
from .perpetual_advice_query import _original
from .perpetual_condition_inputs import read_perpetual_condition_inputs_in_session
from .perpetual_research_binding import canonical
from .perpetual_research_reader import read_perpetual_conditions_in_session

POLICY_KIND = "perpetual_condition_policy_v1"
OBSERVATION_KIND = "perpetual_condition_observation_v1"
TRANSITION_KIND = "perpetual_condition_transition_v1"


def _wire(value):
    return aware_utc(value).isoformat().replace("+00:00", "Z")


def _ref(row):
    return {"ref": row.request_id, "hash": row.payload_hash}


def _read_record(row, *, kind):
    if row is None:
        return None
    body = json.loads(row.result_json)
    if (
        row.kind != kind
        or canonical(body) != row.result_json
        or canonical_hash(body) != row.payload_hash
    ):
        raise ValueError("perpetual_condition_original_invalid")
    if body["owner_id"] != row.owner_id:
        raise ValueError("perpetual_condition_original_owner_mismatch")
    return body


def _event_key(body):
    return "perpconditionevent_" + canonical_hash(
        [
            body["watch_ref"],
            body["plan_revision_ref"],
            body["assumption_ref"],
            body["purpose"],
            body["first_confirmed_bar_ref"],
        ]
    )


def condition_new_exposure_blocked_in_session(session, *, owner_id, plan_id, revision_ref):
    """Conservative exact-revision gate; pausing a watch cannot undo invalidation."""
    rows = session.scalars(
        select(Command).where(
            Command.kind == TRANSITION_KIND,
            Command.owner_id == owner_id,
            Command.target_id == plan_id,
            func.json_extract(Command.result_json, "$.plan_revision_ref.ref")
            == revision_ref["ref"],
        )
    ).all()
    for row in rows:
        body = _read_record(row, kind=TRANSITION_KIND)
        if (
            row.request_id != _event_key(body)
            or body["plan_id"] != plan_id
            or body["plan_revision_ref"] != revision_ref
        ):
            raise ValueError("perpetual_condition_transition_binding_invalid")
        if body["purpose"] == "invalidation" and body["status"] == "invalidated":
            return True
    return False


class PerpetualConditionMonitor:
    def __init__(self, database, source, *, now, channel="local"):
        self.database, self.source, self.now, self.channel = database, source, now, channel

    @staticmethod
    def policy_key(watch_ref, revision_ref):
        return "perpconditionpolicy_" + canonical_hash([watch_ref, revision_ref])

    def prepare_policy(self, owner, command, *, source_run_id=None):
        at = aware_utc(self.now())

        def read(session):
            repo = ActionRepository(session)
            revision = repo.get_revision(command.expected_revision_ref.ref)
            if (
                revision is None
                or revision.payload_hash != command.expected_revision_ref.hash
                or revision.plan_id != command.plan_id
                or revision.context.owner_id != owner
            ):
                raise ValueError("perpetual_condition_policy_plan_mismatch")
            snapshot = repo.get_snapshot(revision.decision_id)
            run_id = snapshot.run_id
            if source_run_id is not None and source_run_id != run_id:
                raise ValueError("perpetual_condition_policy_run_mismatch")
            bindings = revision.bindings.model_dump(mode="json")
            derivation = _original(
                session, run_id, bindings["derivation"], "perpetual-publication-derivation.v1"
            )
            base = {
                "owner_id": owner,
                "plan_id": revision.plan_id,
                "plan_revision_ref": command.expected_revision_ref.model_dump(mode="json"),
                "source_run_id": run_id,
                "candidate_target_ref": bindings["candidate_target"],
                "native_synthesis_ref": bindings["native_synthesis"],
                "instrument_ref": bindings["instrument"],
                "scope_id": revision.context.scope_id,
                "published_at": _wire(revision.published_at),
                "assumptions": [],
                "reason_code": None,
                "recorded_at": _wire(at),
            }
            try:
                from ..ports.research_observation import ObservedExecutionRef

                # These refs came from immutable JSON, not Python model values.
                mapped_ref = ObservedExecutionRef.model_validate_json(
                    canonical(derivation["mapped_original"])
                )
                provider_ref = ObservedExecutionRef.model_validate_json(
                    canonical(derivation["provider_original"])
                )
                result = read_perpetual_conditions_in_session(
                    session,
                    source_run_id=run_id,
                    candidate_target_ref=bindings["candidate_target"],
                    mapped_ref=mapped_ref,
                    provider_emission_ref=provider_ref,
                    known_at=at,
                )
            except (ValueError, OSError) as error:
                return {**base, "reason_code": str(error)[:500]}
            if (
                result.run_id != run_id
                or json.loads(result.candidate_target_ref_json) != bindings["candidate_target"]
            ):
                raise ValueError("perpetual_condition_policy_reader_binding_invalid")
            return {
                **base,
                "assumptions": [json.loads(item.assumption_json) for item in result.declarations],
                "reason_code": result.reason_code,
                "synthesis_ref": json.loads(result.synthesis_ref_json),
                "mapped_ref": result.mapped_ref.model_dump(mode="json")
                if result.mapped_ref
                else None,
                "provider_emission_ref": result.provider_emission_ref.model_dump(mode="json")
                if result.provider_emission_ref
                else None,
                "prepare_emission_ref": result.prepare_emission_ref.model_dump(mode="json")
                if result.prepare_emission_ref
                else None,
            }

        return prepare_originals(self.database, read)

    def install_policy(self, session, receipt, prepared):
        prepared._require_issued(session)
        prepared.verify(session)
        value = prepared.value
        key = self.policy_key(receipt.revision.model_dump(mode="json"), value["plan_revision_ref"])
        if value["reason_code"]:
            key += "_gap_" + canonical_hash(value["reason_code"])[:16]
        row = session.get(Command, key, populate_existing=True)
        if row:
            return row
        body = {
            "schema_version": "perpetual-condition-monitoring-policy.v1",
            **value,
            "watch_id": receipt.watch_id,
            "watch_ref": receipt.revision.model_dump(mode="json"),
        }
        row = Command(
            request_id=key,
            owner_id=receipt.owner_id,
            target_id=value["plan_id"],
            kind=POLICY_KIND,
            payload_hash=canonical_hash(body),
            result_json=canonical(body),
            created_at=self.now(),
        )
        session.add(row)
        session.flush()
        return row

    def read_policy(self, session, receipt, progress):
        key = self.policy_key(
            receipt.revision.model_dump(mode="json"), progress["plan_revision_ref"]
        )
        row = session.get(Command, key, populate_existing=True)
        if row is None:
            row = session.scalar(
                select(Command)
                .where(
                    Command.kind == POLICY_KIND,
                    Command.owner_id == receipt.owner_id,
                    Command.target_id == progress["plan_id"],
                    func.json_extract(Command.result_json, "$.watch_ref.ref")
                    == receipt.revision.ref,
                    func.json_extract(Command.result_json, "$.plan_revision_ref.ref")
                    == progress["plan_revision_ref"]["ref"],
                )
                .order_by(Command.created_at.desc())
                .limit(1)
            )
        value = _read_record(row, kind=POLICY_KIND)
        if value is None:
            return None, None, ()
        if (
            value["watch_ref"] != receipt.revision.model_dump(mode="json")
            or value["plan_id"] != progress["plan_id"]
            or value["source_run_id"] != progress["origin_run_id"]
            or value["plan_revision_ref"] != progress["plan_revision_ref"]
        ):
            raise ValueError("perpetual_condition_policy_origin_mismatch")
        synthesis = _original(
            session,
            value["source_run_id"],
            value["native_synthesis_ref"],
            "research-decision-synthesis.v2",
        )
        if synthesis["candidate_set"]["candidate_target_ref"] != value["candidate_target_ref"]:
            raise ValueError("perpetual_condition_policy_target_mismatch")
        declarations = tuple(
            decode_perpetual_condition(item, synthesis=synthesis) for item in value["assumptions"]
        )
        return row, value, declarations

    def prepare_inputs(self, receipt, progress):
        at = aware_utc(self.now())

        def read(session):
            row, policy, declarations = self.read_policy(session, receipt, progress)
            if not policy:
                return None
            result = {"policy_ref": _ref(row), "closes": (), "reason_code": policy["reason_code"]}
            if not any(item.policy for item in declarations):
                return result
            try:
                if self.source is None:
                    raise ValueError("perpetual_condition_source_not_configured")
                inputs = read_perpetual_condition_inputs_in_session(
                    session,
                    instrument_ref=decision_product_v2.DecisionSourceRefV1.model_validate(
                        policy["instrument_ref"]
                    ),
                    scope_id=policy["scope_id"],
                    as_of=at,
                    source=self.source,
                )
                return {**result, "closes": inputs.closes}
            except (ValueError, OSError) as error:
                return {**result, "reason_code": str(error)[:500]}

        return prepare_originals(self.database, read)

    @staticmethod
    def latest_observation(session, receipt, progress):
        row = session.scalar(
            select(Command)
            .where(
                Command.kind == OBSERVATION_KIND,
                Command.owner_id == receipt.owner_id,
                Command.target_id == receipt.watch_id,
                func.json_extract(Command.result_json, "$.watch_ref.ref") == receipt.revision.ref,
                func.json_extract(Command.result_json, "$.plan_revision_ref.ref")
                == progress["plan_revision_ref"]["ref"],
            )
            .order_by(Command.created_at.desc(), Command.request_id.desc())
            .limit(1)
        )
        return _read_record(row, kind=OBSERVATION_KIND)

    def observe(self, session, receipt, progress, prepared):
        if prepared is None:
            return None
        prepared._require_issued(session)
        prepared.verify(session)
        evidence = prepared.value
        if evidence is None:
            return None
        policy_row, policy, declarations = self.read_policy(session, receipt, progress)
        if _ref(policy_row) != evidence["policy_ref"]:
            raise ValueError("perpetual_condition_prepared_policy_changed")
        at = aware_utc(self.now())
        previous = self.latest_observation(session, receipt, progress)
        by_id = {item["assumption_id"]: item for item in previous["conditions"]} if previous else {}
        states = []
        invalidated = False
        for declaration in declarations:
            state = evaluate_perpetual_condition(
                declaration,
                evidence["closes"],
                previous=by_id.get(declaration.assumption_id),
                as_of=at,
                not_before=datetime.fromisoformat(policy["published_at"].replace("Z", "+00:00")),
            )
            if evidence["reason_code"] and state["status"] not in {
                "confirmed",
                "invalidated",
                "unmonitored",
            }:
                state.update(
                    truth="unknown", status="unknown", reason_codes=[evidence["reason_code"]]
                )
            invalidated |= state["status"] == "invalidated"
            states.append(state)
        # Choose the invalidation motive before any simultaneous confirmation.
        # Every event/notice survives; only the pending research request is shared.
        for state in sorted(states, key=lambda item: item["status"] != "invalidated"):
            if state["status"] in {"confirmed", "invalidated"}:
                event = self._transition(session, receipt, progress, policy_row, state, at)
                state.update(
                    request_id=event.get("request_id"), outbox_ids=event.get("outbox_ids", [])
                )
        body = {
            "schema_version": "perpetual-condition-observation.v1",
            "owner_id": receipt.owner_id,
            "watch_id": receipt.watch_id,
            "watch_ref": receipt.revision.model_dump(mode="json"),
            "plan_id": progress["plan_id"],
            "plan_revision_ref": progress["plan_revision_ref"],
            "source_run_id": progress["origin_run_id"],
            "policy_ref": _ref(policy_row),
            "observed_at": _wire(at),
            "conditions": states,
            "reason_code": evidence["reason_code"],
            "new_exposure_blocked": invalidated,
        }

        def signature(value):
            return {
                **value,
                "observed_at": None,
                "conditions": [{**r, "observed_at": None} for r in value["conditions"]],
            }

        if previous and signature(previous) == signature(body):
            return previous
        key = "perpconditionobservation_" + canonical_hash(body)
        session.add(
            Command(
                request_id=key,
                owner_id=receipt.owner_id,
                target_id=receipt.watch_id,
                kind=OBSERVATION_KIND,
                payload_hash=canonical_hash(body),
                result_json=canonical(body),
                created_at=at,
            )
        )
        session.flush()
        return body

    @staticmethod
    def _pending_successor(session, receipt, progress, at):
        from ..persistence.db import RunRecord
        from .perpetual_plan_followup import PROTOCOL
        from .perpetual_plan_watch_continuous import _read
        from .risk_research_admission import TERMINAL

        rows = session.execute(
            select(Request, Trigger)
            .join(Trigger, Trigger.request_id == Request.request_id)
            .where(
                Request.protocol_version == PROTOCOL,
                Request.status.in_(("pending_admission", "claiming", "queued")),
                Request.created_at <= at,
                Trigger.kind == "perpetual_plan_review",
                Trigger.source_ref == receipt.watch_id,
                func.json_extract(Trigger.payload_json, "$.plan_revision_ref.ref")
                == progress["plan_revision_ref"]["ref"],
            )
            .order_by(Request.created_at, Request.request_id)
        ).all()
        for request, trigger in rows:
            source = json.loads(trigger.payload_json)
            if (
                canonical(source) != trigger.payload_json
                or canonical_hash(source) != trigger.payload_hash
            ):
                raise ValueError("perpetual_condition_pending_source_invalid")
            if (
                source["owner_id"] != receipt.owner_id
                or source["watch_id"] != receipt.watch_id
                or source["plan_revision_ref"] != progress["plan_revision_ref"]
                or source["origin_run_id"] != progress["origin_run_id"]
            ):
                raise ValueError("perpetual_condition_pending_source_binding_invalid")
            original = receipt
            if source["watch_ref"] != receipt.revision.model_dump(mode="json"):
                stored = session.get(Command, source["watch_ref"]["ref"], populate_existing=True)
                if stored is None:
                    raise ValueError("perpetual_condition_pending_consent_missing")
                original = _read(
                    session, receipt.owner_id, json.loads(stored.result_json)["request_id"]
                )
            if (
                original.owner_id != receipt.owner_id
                or original.watch_id != receipt.watch_id
                or original.revision.model_dump(mode="json") != source["watch_ref"]
                or original.command.mode != "remind_and_research"
                or original.command.partition.model_dump(mode="json") != source["partition"]
                or original.command.horizon != source["horizon"]
                or original.state != "active"
            ):
                raise ValueError("perpetual_condition_pending_consent_invalid")
            run = session.get(RunRecord, request.run_id) if request.run_id else None
            if run and run.status in TERMINAL:
                continue
            if request.run_id and run is None:
                raise ValueError("perpetual_condition_pending_run_missing")
            if run is None:
                deadline = datetime.fromisoformat(source["deadline_at"].replace("Z", "+00:00"))
                expired = at >= deadline
                changed = source["watch_ref"] != receipt.revision.model_dump(mode="json")
                if expired or changed:
                    request.status = "failed"
                    request.error_code = (
                        "perpetual_watch_slot_expired"
                        if expired
                        else "perpetual_continuous_changed_or_paused"
                    )
                    request.updated_at = at
                    request.lease_owner, request.lease_expires_at = None, None
                    session.flush()
                    continue
            return request.request_id
        return None

    def _transition(self, session, receipt, progress, policy_row, state, at):
        body = {
            "schema_version": "perpetual-condition-transition.v1",
            "owner_id": receipt.owner_id,
            "plan_id": progress["plan_id"],
            "plan_revision_ref": progress["plan_revision_ref"],
            "watch_id": receipt.watch_id,
            "watch_ref": receipt.revision.model_dump(mode="json"),
            "assumption_ref": state["assumption_ref"],
            "purpose": state["purpose"],
            "status": state["status"],
            "first_confirmed_bar_ref": state["first_confirmed_bar_ref"],
            "observed_at": _wire(at),
            "policy_ref": _ref(policy_row),
            "request_id": None,
            "outbox_ids": [],
        }
        key = _event_key(body)
        existing = session.get(Command, key, populate_existing=True)
        if existing:
            return _read_record(existing, kind=TRANSITION_KIND)
        pending = (
            self._pending_successor(session, receipt, progress, at)
            if condition_research_authorized(receipt)
            else None
        )
        if pending:
            # Do not append motives to a claimed or frozen research input.
            body["request_id"] = pending
        elif condition_research_authorized(receipt):
            from .perpetual_plan_followup import ORIGIN_KEYS, PROTOCOL

            policy = json.loads(policy_row.result_json)
            declaration = next(
                item
                for item in policy["assumptions"]
                if canonical_hash(item) == state["assumption_ref"]["hash"]
            )
            until = datetime.fromisoformat(
                json.loads(declaration["statement"])["valid_until"].replace("Z", "+00:00")
            )
            # The condition event ref cannot hash a body containing its own request/outbox refs.
            proof_ref = {
                "ref": key,
                "hash": canonical_hash(
                    {k: v for k, v in body.items() if k not in {"request_id", "outbox_ids"}}
                ),
            }
            source = PerpetualPlanReviewTriggerV2(
                schema_version="perpetual-plan-review-trigger.v2",
                watch_id=receipt.watch_id,
                watch_ref=receipt.revision,
                owner_id=receipt.owner_id,
                partition=receipt.command.partition,
                horizon=receipt.command.horizon,
                motive="condition_invalidated"
                if state["purpose"] == "invalidation"
                else "condition_confirmed",
                expired_at=None,
                condition_event_ref=proof_ref,
                observed_at=at,
                scheduled_at=at,
                deadline_at=until,
                recorded_at=at,
                **{k: progress[k] for k in ORIGIN_KEYS},
            )
            source_body = source.model_dump(mode="json")
            trigger_key = "perpetual_plan_review:" + canonical_hash(
                [source_body["watch_ref"], source_body["plan_revision_ref"], proof_ref]
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
                    payload_json=canonical(source_body),
                    payload_hash=canonical_hash(source_body),
                    recorded_at=at,
                )
            )
            body["request_id"] = request_id
        label = "确认条件已满足" if state["purpose"] == "confirmation" else "失效条件已触发"
        envelope = make_notification_envelope(
            subject_kind="action_revision",
            subject_id=progress["plan_revision_ref"]["ref"],
            channel=self.channel,
            dedupe_key="perpetual-condition:" + canonical_hash([key, self.channel]),
            subject=label,
            body=f"{receipt.command.horizon} {state['assumption_id']}：{label}。"
            f"原Run：{progress['origin_run_id']}。旧概率不改；新建议需后继独审。"
            + (
                "已暂停这版建议的新增纸面暴露，原仓位保护继续。"
                if state["purpose"] == "invalidation"
                else ""
            ),
            created_at=at,
        )
        body["outbox_ids"] = [
            OutboxService(self.database).enqueue_subject(envelope, session=session)
        ]
        session.add(
            Command(
                request_id=key,
                owner_id=receipt.owner_id,
                target_id=progress["plan_id"],
                kind=TRANSITION_KIND,
                payload_hash=canonical_hash(body),
                result_json=canonical(body),
                created_at=at,
            )
        )
        session.flush()
        return body

    def view(self, session, receipt, progress):
        if not receipt or not progress:
            return None
        row, policy, declarations = self.read_policy(session, receipt, progress)
        latest = self.latest_observation(session, receipt, progress)
        states = latest["conditions"] if latest else []
        if not latest:
            for declaration in declarations:
                states.append(
                    evaluate_perpetual_condition(
                        declaration,
                        (),
                        previous=None,
                        as_of=self.now(),
                        not_before=datetime.fromisoformat(
                            policy["published_at"].replace("Z", "+00:00")
                        ),
                    )
                )
        blocked = condition_new_exposure_blocked_in_session(
            session,
            owner_id=receipt.owner_id,
            plan_id=progress["plan_id"],
            revision_ref=progress["plan_revision_ref"],
        )
        by_id = {item.assumption_id: item for item in declarations}
        for item in states:
            item.pop("first_confirmed_bar_ref", None)
            declaration = by_id.get(item["assumption_id"])
            if declaration and declaration.policy:

                def describe(node):
                    if node["op"] == "compare":
                        symbols = {"gt": ">", "gte": "≥", "lt": "<", "lte": "≤"}
                        return (
                            f"1分钟收盘价 {symbols[node['cmp']]} {node['threshold']} {node['unit']}"
                            f"（源年龄≤{node['max_age_seconds']}秒）"
                        )
                    return (" 且 " if node["op"] == "all" else " 或 ").join(
                        describe(child) for child in node["args"]
                    )

                item["text"] = f"连续 {declaration.policy.min_fresh_samples} 根：" + describe(
                    json.loads(declaration.policy.expression_json)
                )
        return {
            "schema_version": "perpetual-condition-monitoring-view.v1",
            "source_run_id": progress["origin_run_id"],
            "plan_revision_ref": progress["plan_revision_ref"],
            "state": "invalidated"
            if blocked
            else (
                "unregistered"
                if not row or policy["reason_code"]
                else ("unknown" if any(x["status"] == "unknown" for x in states) else "monitoring")
            ),
            "new_exposure_blocked": blocked,
            "observed_at": latest["observed_at"] if latest else None,
            "conditions": states,
            "reason_codes": [policy["reason_code"]]
            if policy and policy["reason_code"]
            else (["condition_policy_not_registered"] if not policy else []),
        }

    async def collect_due(self, watches):
        """Collect at most one fresh page per enabled SWAP on the existing tick."""
        if self.source is None:
            return 0
        import httpx

        from ..persistence.db import RawCaptureRecord
        from ..ports.perpetual_close import VERSION, minute_end
        from .perpetual_condition_inputs import collect_perpetual_condition_page
        from .perpetual_plan_watch_continuous import _latest, _latest_progress

        at = aware_utc(self.now())
        instruments = set()
        with self.database.session() as session:
            for expected_receipt, expected_progress in watches:
                receipt = _latest(session, expected_receipt.watch_id)
                progress = _latest_progress(session, expected_receipt.watch_id)
                if (
                    not receipt
                    or receipt.state != "active"
                    or receipt.revision != expected_receipt.revision
                    or not progress
                    or progress["status"] != "following"
                    or progress["plan_revision_ref"] != expected_progress["plan_revision_ref"]
                ):
                    continue
                _row, policy, declarations = self.read_policy(session, receipt, progress)
                if not policy or not any(item.policy for item in declarations):
                    continue
                inst = policy["scope_id"].split(":")[3]
                recent = session.scalar(
                    select(RawCaptureRecord.capture_id)
                    .where(
                        RawCaptureRecord.adapter_version == VERSION,
                        RawCaptureRecord.context_id.startswith(f"{VERSION}:{inst}:"),
                        RawCaptureRecord.recorded_at >= minute_end(at),
                        RawCaptureRecord.error_code.is_(None),
                        RawCaptureRecord.status_code == 200,
                    )
                    .limit(1)
                )
                if recent is None:
                    instruments.add(inst)
        count = 0
        for inst in sorted(instruments)[:2]:
            try:
                await collect_perpetual_condition_page(self.source, inst_id=inst, as_of=at)
                count += 1
            except (httpx.HTTPError, ValueError, OSError):
                # Captures retain the failure. The deterministic observer exposes
                # missing/stale/invalid evidence; it never invents confirmation.
                continue
        return count


def read_condition_review_source_in_session(
    session, row, trigger, source, stored, *, at, require_active
):
    """Authenticate the committed event, consent, source plan and immediate slot."""
    from .perpetual_plan_followup import ORIGIN_KEYS, _settings_current
    from .perpetual_plan_watch_continuous import _check_origin, _latest, _latest_progress, _read

    receipt = _read(session, source.owner_id, json.loads(stored.result_json)["request_id"])
    event_row = session.get(Command, source.condition_event_ref.ref, populate_existing=True)
    event = _read_record(event_row, kind=TRANSITION_KIND)
    if event is None:
        raise ValueError("perpetual_condition_trigger_event_missing")
    projected = {k: v for k, v in event.items() if k not in {"request_id", "outbox_ids"}}
    body = source.model_dump(mode="json")
    trigger_key = "perpetual_plan_review:" + canonical_hash(
        [body["watch_ref"], body["plan_revision_ref"], body["condition_event_ref"]]
    )
    inst = source.partition.instrument_id.removeprefix("okx:")
    scope = f"crypto:perp:okx:{inst}:trade:1m"
    policy_row = session.get(Command, event["policy_ref"]["ref"], populate_existing=True)
    policy = _read_record(policy_row, kind=POLICY_KIND)
    if policy is None or _ref(policy_row) != event["policy_ref"]:
        raise ValueError("perpetual_condition_trigger_policy_missing")
    matched = [
        item
        for item in policy["assumptions"]
        if canonical_hash(item) == event["assumption_ref"]["hash"]
    ]
    if len(matched) != 1:
        raise ValueError("perpetual_condition_trigger_assumption_missing")
    condition = json.loads(matched[0]["statement"])
    until = datetime.fromisoformat(condition["valid_until"].replace("Z", "+00:00"))
    observed = datetime.fromisoformat(event["observed_at"].replace("Z", "+00:00"))
    expected_status = "invalidated" if condition["purpose"] == "invalidation" else "confirmed"
    if (
        policy["owner_id"] != source.owner_id
        or policy["watch_ref"] != body["watch_ref"]
        or policy["watch_id"] != source.watch_id
        or policy["plan_id"] != event["plan_id"]
        or policy["plan_revision_ref"] != body["plan_revision_ref"]
        or condition["purpose"] != event["purpose"]
        or event["status"] != expected_status
        or event["assumption_ref"]
        != {
            "ref": "decision_model_assumption_" + canonical_hash(matched[0]),
            "hash": canonical_hash(matched[0]),
        }
        or not receipt.accepted_at <= observed <= at
        or event_row.request_id != _event_key(event)
        or canonical_hash(projected) != source.condition_event_ref.hash
        or event["owner_id"] != source.owner_id
        or event["watch_ref"] != body["watch_ref"]
        or event["plan_revision_ref"] != body["plan_revision_ref"]
        or event["request_id"] != row.request_id
        or receipt.revision != source.watch_ref
        or receipt.watch_id != source.watch_id
        or not condition_research_authorized(receipt)
        or receipt.command.partition != source.partition
        or receipt.command.horizon != source.horizon
        or receipt.state != "active"
        or source.motive
        != (
            "condition_invalidated" if event["purpose"] == "invalidation" else "condition_confirmed"
        )
        or event["status"] not in {"confirmed", "invalidated"}
        or source.scheduled_at != observed
        or source.observed_at != observed
        or source.recorded_at != observed
        or source.deadline_at != until
        or observed > at
        or as_utc(event_row.created_at) != observed
        or as_utc(row.created_at) != observed
        or as_utc(trigger.recorded_at) != observed
        or trigger.kind != "perpetual_plan_review"
        or trigger.source_ref != source.watch_id
        or trigger.payload_json != canonical(body)
        or trigger.payload_hash != canonical_hash(body)
        or trigger.trigger_key != trigger_key
        or row.request_id != "riskresearch_" + canonical_hash([scope, trigger_key])[:40]
        or row.scope_id != scope
        or trigger.scope_id != scope
        or row.instrument != inst
    ):
        raise ValueError("perpetual_condition_trigger_invalid")
    revision = ActionRepository(session).get_revision(event["plan_revision_ref"]["ref"])
    proof = {
        "plan_id": event["plan_id"],
        "review_due_at": _wire(revision.valid_until),
        **{k: body[k] for k in ORIGIN_KEYS},
    }
    _check_origin(session, receipt, proof, at, require_current=require_active)
    if (
        policy["source_run_id"] != source.origin_run_id
        or policy["plan_revision_ref"] != body["plan_revision_ref"]
    ):
        raise ValueError("perpetual_condition_trigger_origin_mismatch")
    if require_active:
        current, progress = (
            _latest(session, receipt.watch_id),
            _latest_progress(session, receipt.watch_id),
        )
        if (
            current.revision != receipt.revision
            or current.state != "active"
            or progress["watch_ref"] != body["watch_ref"]
            or progress["plan_revision_ref"] != body["plan_revision_ref"]
            or progress["status"] not in {"following", "invalidated"}
        ):
            raise ValueError("perpetual_continuous_changed_or_paused")
        if not _settings_current(session, receipt.owner_id, receipt.command):
            raise ValueError("perpetual_continuous_settings_changed")
        if at >= source.deadline_at:
            raise ValueError("perpetual_watch_slot_expired")
        if at < source.scheduled_at:
            raise ValueError("perpetual_watch_slot_not_due")
    return {
        "request_id": row.request_id,
        "trigger_ref": {"ref": trigger.trigger_key, "hash": trigger.payload_hash},
        "watch_ref": body["watch_ref"],
        "source": body,
    }
