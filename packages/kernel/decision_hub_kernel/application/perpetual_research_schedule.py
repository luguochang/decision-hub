"""Owner consent and periodic triggers feeding the existing admission/Run queues."""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import func, select

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSettingsPartitionV1,
)

from ..persistence.db import ProductCommandRecord, RunRecord, as_utc, utcnow
from ..persistence.original_preparation import begin_immediate, prepare_originals
from ..persistence.perpetual_research_schedule_records import (
    PerpetualResearchScheduleRecord as Schedule,
)
from ..persistence.risk_research_records import RiskResearchRequestRecord as AdmissionRequest
from ..persistence.risk_research_records import RiskResearchTriggerRecord as Trigger
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.perpetual_research_schedule import (
    EVENT_PROTOCOL,
    LEGACY_PROTOCOL,
    PROTOCOL,
    SUPPORTED_PROTOCOLS,
    PerpetualEventMotiveV1,
    PerpetualEventResearchTriggerV1,
    PerpetualPeriodicTriggerV1,
    next_hour,
    parse_perpetual_research_schedule_command,
    parse_perpetual_research_schedule_receipt,
)
from .perpetual_research_binding import _settings_original, canonical
from .perpetual_research_horizon import (
    PERPETUAL_RESEARCH_RUNTIME_SECONDS,
    parse_perpetual_research_command,
)
from .risk_admission_wait import ContinuousAdmissionWriter

KIND = "perpetual_research_schedule"


def schedule_id(owner, partition, horizon):
    return "perpschedule_" + canonical_hash([owner, partition.model_dump(mode="json"), horizon])


def command_key(owner, request):
    return "perpschedulecmd_" + canonical_hash([owner, request])


def _sealed(body):
    # revision identifies the immutable non-self-referential receipt body.
    provisional = parse_perpetual_research_schedule_receipt(
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
    return parse_perpetual_research_schedule_receipt(
        {
            **provisional,
            "revision": {"ref": provisional["revision"]["ref"], "hash": digest},
            "payload_hash": digest,
        }
    )


def read_schedule_receipt_in_session(session, owner, request):
    row = session.get(ProductCommandRecord, command_key(owner, request), populate_existing=True)
    if row is None:
        return None
    receipt = parse_perpetual_research_schedule_receipt(json.loads(row.result_json))
    body = receipt.model_dump(mode="json")
    digest = canonical_hash(
        {k: v for k, v in body.items() if k not in {"revision", "payload_hash"}}
    )
    if (
        row.kind != KIND
        or row.owner_id != owner
        or receipt.owner_id != owner
        or receipt.request_id != request
        or receipt.command.request_id != request
        or row.target_id != receipt.schedule_id
        or receipt.schedule_id
        != schedule_id(owner, receipt.command.partition, receipt.command.horizon)
        or row.payload_hash != receipt.command_hash
        or receipt.command_hash != canonical_hash(receipt.command.model_dump(mode="json"))
        or row.result_json != canonical(body)
        or receipt.payload_hash != digest
        or receipt.revision.ref != row.request_id
        or receipt.revision.hash != digest
        or as_utc(row.created_at) != receipt.accepted_at
    ):
        raise ValueError("perpetual_schedule_original_invalid")
    return receipt


def _current_receipt(session, row):
    original = session.get(ProductCommandRecord, row.revision_id, populate_existing=True)
    if original is None:
        raise ValueError("perpetual_schedule_original_missing")
    request = json.loads(original.result_json)["request_id"]
    receipt = read_schedule_receipt_in_session(session, row.owner_id, request)
    if (
        receipt.revision.hash != row.revision_hash
        or receipt.state_version != row.state_version
        or receipt.state != row.state
        or receipt.command.horizon != row.horizon
        or canonical(receipt.command.partition.model_dump(mode="json")) != row.partition_json
    ):
        raise ValueError("perpetual_schedule_head_invalid")
    return receipt


def _automatic_root(triggers, protocol):
    periodic = [trigger for trigger in triggers if trigger.kind == "perpetual_periodic"]
    event = [trigger for trigger in triggers if trigger.kind == "perpetual_event"]
    if protocol == LEGACY_PROTOCOL:
        if len(periodic) != 1 or len(triggers) != 1:
            raise ValueError("perpetual_schedule_trigger_invalid")
        return periodic[0], PerpetualPeriodicTriggerV1.model_validate_json(periodic[0].payload_json)
    if protocol == PROTOCOL:
        if len(periodic) != 1 or event:
            raise ValueError("perpetual_schedule_trigger_invalid")
        return periodic[0], PerpetualPeriodicTriggerV1.model_validate_json(periodic[0].payload_json)
    if protocol == EVENT_PROTOCOL:
        if periodic or len(event) != 1 or len(triggers) < 2:
            raise ValueError("perpetual_schedule_trigger_invalid")
        return event[0], PerpetualEventResearchTriggerV1.model_validate_json(event[0].payload_json)
    raise ValueError("perpetual_schedule_trigger_invalid")


def read_automatic_source_in_session(
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
):
    row = session.get(AdmissionRequest, key, populate_existing=True)
    from .perpetual_plan_followup import PROTOCOL as PLAN_PROTOCOL
    from .perpetual_plan_followup import read_plan_review_source_in_session

    if row is not None and row.protocol_version == PLAN_PROTOCOL:
        return read_plan_review_source_in_session(
            session,
            key,
            generation,
            claim_owner,
            at=at,
            require_active=require_active,
            require_claim_live=require_claim_live,
            run_id=run_id,
            original_only=original_only,
        )

    if (
        row is None
        or row.protocol_version not in SUPPORTED_PROTOCOLS
        or (
            not original_only
            and run_id is not None
            and (row.run_id != run_id or row.status not in {"queued", "completed"})
        )
        or (
            not original_only
            and run_id is None
            and (
                row.status != "claiming"
                or row.lease_owner != claim_owner
                or row.lease_generation != generation
                or row.lease_expires_at is None
                or (require_claim_live and not at < as_utc(row.lease_expires_at))
            )
        )
    ):
        raise ValueError("perpetual_schedule_claim_lost")
    triggers = session.scalars(select(Trigger).where(Trigger.request_id == key)).all()
    trigger, source = _automatic_root(triggers, row.protocol_version)
    body = source.model_dump(mode="json")
    schedule = session.get(Schedule, source.schedule_id, populate_existing=True)
    original_row = session.get(
        ProductCommandRecord, source.schedule_ref.ref, populate_existing=True
    )
    if original_row is None:
        raise ValueError("perpetual_schedule_original_missing")
    original = read_schedule_receipt_in_session(
        session, source.owner_id, json.loads(original_row.result_json)["request_id"]
    )
    if (
        trigger.source_ref != source.schedule_id
        or trigger.payload_json != canonical(body)
        or trigger.payload_hash != canonical_hash(body)
        or trigger.scope_id != row.scope_id
        or row.instrument != source.partition.instrument_id.removeprefix("okx:")
        or row.scope_id != f"crypto:perp:okx:{row.instrument}:trade:1m"
        or original.revision != source.schedule_ref
        or original.command.partition != source.partition
        or original.schedule_id != source.schedule_id
        or original.command.horizon != source.horizon
        or original.state != "active"
        or row.request_id
        != "riskresearch_" + canonical_hash([row.scope_id, trigger.trigger_key])[:40]
        or not original.accepted_at <= source.scheduled_at <= source.recorded_at <= at
        or as_utc(trigger.recorded_at) != source.recorded_at
        or as_utc(row.created_at) != source.recorded_at
    ):
        raise ValueError("perpetual_schedule_trigger_invalid")
    if trigger.kind == "perpetual_periodic":
        if trigger.trigger_key != "perpetual_periodic:" + canonical_hash(
            [source.schedule_ref.model_dump(mode="json"), body["scheduled_at"]]
        ) or source.deadline_at != next_hour(source.scheduled_at):
            raise ValueError("perpetual_schedule_trigger_invalid")
    elif trigger.kind == "perpetual_event":
        trigger_events = [item.model_dump(mode="json") for item in source.trigger_events]
        if (
            source.scheduled_at != source.recorded_at
            or source.deadline_at != source.scheduled_at + timedelta(seconds=3600)
            or trigger_events != sorted(trigger_events, key=canonical)
            or len({canonical(item) for item in trigger_events}) != len(trigger_events)
            or trigger.trigger_key
            != "perpetual_event:"
            + canonical_hash([source.schedule_ref.model_dump(mode="json"), trigger_events])
        ):
            raise ValueError("perpetual_schedule_trigger_invalid")
    else:
        raise ValueError("perpetual_schedule_trigger_invalid")
    if require_active and (
        schedule is None
        or schedule.state != "active"
        or schedule.revision_id != source.schedule_ref.ref
        or schedule.revision_hash != source.schedule_ref.hash
    ):
        raise ValueError("perpetual_schedule_changed_or_paused")
    if require_active and at >= source.deadline_at:
        raise ValueError("perpetual_schedule_slot_expired")
    proof = dict(
        request_id=key,
        trigger_ref={"ref": trigger.trigger_key, "hash": trigger.payload_hash},
        schedule_ref=source.schedule_ref.model_dump(mode="json"),
        source=body,
    )
    if row.protocol_version in {PROTOCOL, EVENT_PROTOCOL}:
        motives = []
        for linked in sorted(
            (item for item in triggers if item is not trigger), key=lambda item: item.trigger_key
        ):
            motive = PerpetualEventMotiveV1.model_validate_json(linked.payload_json)
            value = motive.model_dump(mode="json")
            if (
                linked.kind != "perpetual_event_motive"
                or linked.scope_id != row.scope_id
                or linked.source_ref != motive.event_ref.ref
                or linked.payload_hash != canonical_hash(value)
                or linked.payload_json != canonical(value)
                or linked.trigger_key
                != "perpetual_event_motive:"
                + canonical_hash([key, value["event_ref"], motive.stage_id])
                or motive.schedule_ref != source.schedule_ref
                or motive.owner_id != source.owner_id
                or motive.partition != source.partition
                or motive.horizon != source.horizon
                or motive.scheduled_at != source.scheduled_at
                or motive.deadline_at != source.deadline_at
                or not motive.known_at <= motive.recorded_at <= at
                or not source.recorded_at <= motive.recorded_at < source.deadline_at
                or as_utc(linked.recorded_at) != motive.recorded_at
            ):
                raise ValueError("perpetual_event_motive_invalid")
            motives.append(value)
        if row.protocol_version == EVENT_PROTOCOL:
            actual_events = sorted(
                (
                    dict(
                        event_ref=value["event_ref"],
                        event_key=value["event_key"],
                        stage_id=value["stage_id"],
                    )
                    for value in motives
                ),
                key=canonical,
            )
            expected_events = [item.model_dump(mode="json") for item in source.trigger_events]
            if actual_events != expected_events:
                raise ValueError("perpetual_event_motive_invalid")
        proof["event_motives"] = motives
    return proof


def _motive_originals(session, proof, archive):
    from .official_schedule_original import read_official_schedule_original_in_session

    originals = []
    for value in proof.get("event_motives", ()):
        motive = PerpetualEventMotiveV1.model_validate(value)
        original = read_official_schedule_original_in_session(
            session, motive.event_ref.ref, archive=archive, cutoff_at=motive.recorded_at
        )
        if (
            original.source_ref != motive.event_ref.model_dump(mode="json")
            or original.source_id != motive.source_id
            or original.event_key != motive.event_key
            or original.family != motive.family
            or original.known_at != motive.known_at
            or motive.stage_id != ("meeting_date" if original.family == "fomc" else "release")
        ):
            raise ValueError("perpetual_event_motive_original_changed")
        originals.append(original.original_json)
    return tuple(originals)


def read_current_paper_consent_in_session(session, proof):
    """Reopen the immutable consent selected by an authenticated source proof.

    None means the original older consent retains its saved-account semantics.
    No caller policy or today's latest head can promote that original.
    """
    from ..ports.perpetual_research_inputs import INPUT_POLICY

    source = proof["source"]
    reference = proof.get("watch_ref") or proof.get("schedule_ref")
    row = session.get(ProductCommandRecord, reference["ref"], populate_existing=True)
    if row is None:
        raise ValueError("perpetual_research_automatic_consent_missing")
    if source["schema_version"] in {
        "perpetual-plan-review-trigger.v1",
        "perpetual-plan-review-trigger.v2",
        "perpetual-plan-review-trigger.v3",
    }:
        from .perpetual_plan_watch_continuous import KIND as CONTINUOUS_KIND
        from .perpetual_plan_watch_continuous import _read

        if row.kind != CONTINUOUS_KIND:
            return None
        receipt = _read(session, source["owner_id"], json.loads(row.result_json)["request_id"])
        if receipt.command.mode != "remind_and_research":
            raise ValueError("perpetual_research_automatic_consent_mode_invalid")
        if source["schema_version"] in {
            "perpetual-plan-review-trigger.v2", "perpetual-plan-review-trigger.v3"
        }:
            from ..ports.perpetual_plan_watch import condition_research_authorized
            if not condition_research_authorized(receipt):
                raise ValueError("perpetual_research_automatic_condition_consent_missing")
    else:
        receipt = read_schedule_receipt_in_session(
            session, source["owner_id"], json.loads(row.result_json)["request_id"]
        )
        if receipt.schema_version == "perpetual-research-schedule-receipt.v1":
            return None
    if (
        receipt.revision.model_dump(mode="json") != reference
        or receipt.owner_id != source["owner_id"]
        or receipt.command.partition.model_dump(mode="json") != source["partition"]
        or receipt.command.horizon != source["horizon"]
        or receipt.command.input_policy != INPUT_POLICY
        or receipt.state != "active"
    ):
        raise ValueError("perpetual_research_automatic_consent_scope_invalid")
    return receipt


def authenticate_automatic_admission_in_session(session, admission):
    """Historical automatic authority remains readable after consent is paused."""
    from datetime import datetime

    proof = admission.get("automatic_source")
    if proof is None:
        if admission["schema_version"] == "perpetual-research-admission.v3":
            from .perpetual_research_horizon import authenticate_perpetual_deadlines
            from .perpetual_research_inputs import read_input_binding_identity_in_session

            run = session.get(RunRecord, admission["run_id"], populate_existing=True)
            at = aware_utc(datetime.fromisoformat(admission["admitted_at"]))
            if run is None or run.admission_origin != "manual" or as_utc(run.created_at) != at:
                raise ValueError("perpetual_research_automatic_authority_required")
            authenticate_perpetual_deadlines(admission)
            binding = read_input_binding_identity_in_session(session, admission)
            if binding.authority.origin != "manual_v3":
                raise ValueError("perpetual_research_automatic_authority_required")
            return
        if "runtime_deadline_at" in admission or "runtime_started_at" in admission:
            raise ValueError("perpetual_research_automatic_authority_required")
        return
    run = session.get(RunRecord, admission["run_id"], populate_existing=True)
    at = aware_utc(datetime.fromisoformat(admission["admitted_at"]))
    actual = read_automatic_source_in_session(
        session,
        proof["request_id"],
        None,
        None,
        at=at,
        require_active=False,
        run_id=admission["run_id"],
    )
    if actual.get("event_motives"):
        from pathlib import Path

        from packages.provider_adapters.research.raw_capture import RawBodyArchive

        _motive_originals(session, actual, RawBodyArchive(Path(admission["archive_root"])))
    from .perpetual_research_horizon import authenticate_perpetual_deadlines

    frozen_policy = authenticate_perpetual_deadlines(admission)
    if (
        actual != proof
        or run is None
        or run.admission_origin != "automatic"
        or as_utc(run.created_at) != at
        or aware_utc(datetime.fromisoformat(admission["runtime_started_at"])) != at
        or aware_utc(datetime.fromisoformat(admission["runtime_deadline_at"]))
        != at + timedelta(seconds=(
            frozen_policy["runtime_seconds"] if frozen_policy is not None
            else PERPETUAL_RESEARCH_RUNTIME_SECONDS
        ))
        or admission["owner_id"] != proof["source"]["owner_id"]
        or admission["partition"] != proof["source"]["partition"]
        or admission["context"]["horizon"] != proof["source"]["horizon"]
        or (admission["schema_version"] != "perpetual-research-admission.v3"
            and aware_utc(datetime.fromisoformat(admission["valid_until"]))
            != aware_utc(datetime.fromisoformat(proof["source"]["deadline_at"])))
    ):
        raise ValueError("perpetual_schedule_admission_authority_invalid")


class PerpetualResearchScheduleService:
    protocol_version = PROTOCOL
    supported_protocols = SUPPORTED_PROTOCOLS

    def __init__(self, database, entry, *, now=utcnow):
        self.database, self.entry, self.now = database, entry, now
        if entry.database is not database:
            raise ValueError("perpetual_schedule_database_mismatch")
        if entry.total_deadline_seconds != PERPETUAL_RESEARCH_RUNTIME_SECONDS:
            raise ValueError("perpetual_schedule_runtime_policy_unsupported")

    def recognize_in_session(self, session, row, at):
        return read_automatic_source_in_session(
            session,
            row.request_id,
            row.lease_generation,
            row.lease_owner,
            at=at,
            require_active=False,
            require_claim_live=False,
        )

    def receipt(self, owner, request):
        with self.database.session() as session:
            return read_schedule_receipt_in_session(session, owner, request)

    def save(self, owner, value):
        command = parse_perpetual_research_schedule_command(value)
        if not owner or owner.startswith("system:"):
            raise ValueError("perpetual_schedule_owner_invalid")
        with self.database.session() as session:
            begin_immediate(session)
            prior = read_schedule_receipt_in_session(session, owner, command.request_id)
            digest = canonical_hash(command.model_dump(mode="json"))
            if prior:
                if prior.command_hash != digest:
                    raise ValueError("perpetual_schedule_request_conflict")
                return prior
            identity = schedule_id(owner, command.partition, command.horizon)
            row = session.get(Schedule, identity, populate_existing=True)
            version = row.state_version if row else 0
            if row:
                _current_receipt(session, row)
            if version != command.expected_state_version:
                raise ValueError("perpetual_schedule_state_conflict")
            if command.operation == "enable":
                _settings_original(session, owner, command.partition)
            at = aware_utc(self.now())
            state = "active" if command.operation == "enable" else "paused"
            receipt = _sealed(
                dict(
                    schema_version=(
                        "perpetual-research-schedule-receipt.v2"
                        if command.schema_version == "perpetual-research-schedule-command.v2"
                        else "perpetual-research-schedule-receipt.v1"
                    ),
                    owner_id=owner,
                    request_id=command.request_id,
                    command=command,
                    command_hash=digest,
                    schedule_id=identity,
                    state_version=version + 1,
                    state=state,
                    accepted_at=at,
                )
            )
            if row is None:
                row = Schedule(
                    schedule_id=identity,
                    owner_id=owner,
                    partition_json=canonical(command.partition.model_dump(mode="json")),
                    horizon=command.horizon,
                    created_at=at,
                )
                session.add(row)
            row.state, row.state_version = state, version + 1
            row.revision_id, row.revision_hash = receipt.revision.ref, receipt.revision.hash
            # Consent starts at the next full UTC hour, never a near-expired slot.
            # No historical Run is created and an old slot is never extended.
            row.next_due_at, row.updated_at = next_hour(at), at
            session.add(
                ProductCommandRecord(
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

    def current(self, owner, partition, horizon):
        partition = DecisionSettingsPartitionV1.model_validate(partition)
        with self.database.session() as session:
            identity = schedule_id(owner, partition, horizon)
            row = session.get(Schedule, identity, populate_existing=True)
            if row is None:
                return dict(
                    schema_version="perpetual-research-schedule-view.v1",
                    owner_id=owner,
                    partition=partition.model_dump(mode="json"),
                    horizon=horizon,
                    state="not_enabled",
                    state_version=0,
                    revision=None,
                    next_due_at=None,
                    last_slot=None,
                    missed_slots=0,
                    receipt=None,
                )
            original = _current_receipt(session, row)
            triggers = session.scalars(
                select(Trigger)
                .where(
                    Trigger.source_ref == identity,
                    Trigger.kind.in_(("perpetual_periodic", "perpetual_event")),
                )
                .order_by(
                    func.json_extract(Trigger.payload_json, "$.scheduled_at").desc(),
                    Trigger.trigger_key.desc(),
                )
            ).all()
            last = session.get(AdmissionRequest, triggers[0].request_id) if triggers else None
            at = aware_utc(self.now())
            for trigger in triggers:
                request = session.get(AdmissionRequest, trigger.request_id)
                if request is None:
                    raise ValueError("perpetual_schedule_request_missing")
                if request.protocol_version in SUPPORTED_PROTOCOLS:
                    source = read_automatic_source_in_session(
                        session,
                        request.request_id,
                        None,
                        None,
                        at=at,
                        require_active=False,
                        original_only=True,
                    )
                    if (
                        source["source"]["owner_id"] != owner
                        or source["source"]["partition"] != partition.model_dump(mode="json")
                        or source["source"]["horizon"] != horizon
                    ):
                        raise ValueError("perpetual_schedule_original_invalid")
            missed = sum(
                1
                for item in triggers
                if item.kind == "perpetual_periodic"
                if (request := session.get(AdmissionRequest, item.request_id)) is not None
                and request.error_code == "perpetual_schedule_slot_expired"
            )
            return dict(
                schema_version="perpetual-research-schedule-view.v1",
                owner_id=owner,
                partition=partition.model_dump(mode="json"),
                horizon=horizon,
                state=row.state,
                state_version=row.state_version,
                revision=original.revision.model_dump(mode="json"),
                next_due_at=as_utc(row.next_due_at).isoformat().replace("+00:00", "Z"),
                last_slot=None
                if last is None
                else dict(
                    request_id=last.request_id,
                    status=last.status
                    if last.protocol_version in SUPPORTED_PROTOCOLS
                    else "unsupported_protocol",
                    run_id=last.run_id if last.protocol_version in SUPPORTED_PROTOCOLS else None,
                    run_status=(
                        session.get(RunRecord, last.run_id).status
                        if last.protocol_version in SUPPORTED_PROTOCOLS
                        and last.run_id
                        and session.get(RunRecord, last.run_id)
                        else None
                    ),
                    error_code=last.error_code,
                    protocol_version=last.protocol_version,
                ),
                missed_slots=missed,
                receipt=original.model_dump(mode="json"),
            )

    def discover(self):
        at = aware_utc(self.now())
        with self.database.session() as session:
            begin_immediate(session)
            rows = session.scalars(
                select(Schedule)
                .where(Schedule.state == "active", Schedule.next_due_at <= at)
                .order_by(Schedule.next_due_at)
                .limit(8)
            ).all()
            for row in rows:
                receipt = _current_receipt(session, row)
                due = as_utc(row.next_due_at)
                # A bounded tick advances durable missed slots; never backfills research.
                for _ in range(24):
                    if due > at:
                        break
                    source = PerpetualPeriodicTriggerV1(
                        schema_version="perpetual-research-periodic-trigger.v1",
                        schedule_id=row.schedule_id,
                        schedule_ref=receipt.revision,
                        owner_id=row.owner_id,
                        partition=receipt.command.partition,
                        horizon=row.horizon,
                        scheduled_at=due,
                        deadline_at=next_hour(due),
                        recorded_at=at,
                    )
                    body = source.model_dump(mode="json")
                    key = "perpetual_periodic:" + canonical_hash(
                        [body["schedule_ref"], body["scheduled_at"]]
                    )
                    instrument = source.partition.instrument_id.removeprefix("okx:")
                    scope = f"crypto:perp:okx:{instrument}:trade:1m"
                    request_id = "riskresearch_" + canonical_hash([scope, key])[:40]
                    if session.get(Trigger, key) is None:
                        expired = source.deadline_at <= at
                        session.add(
                            AdmissionRequest(
                                request_id=request_id,
                                protocol_version=PROTOCOL,
                                scope_id=scope,
                                instrument=source.partition.instrument_id.removeprefix("okx:"),
                                status="failed" if expired else "pending_admission",
                                created_at=at,
                                updated_at=at,
                                available_at=at,
                                attempts=0,
                                lease_generation=0,
                                error_code="perpetual_schedule_slot_expired" if expired else None,
                            )
                        )
                        session.add(
                            Trigger(
                                trigger_key=key,
                                request_id=request_id,
                                kind="perpetual_periodic",
                                scope_id=scope,
                                source_ref=row.schedule_id,
                                payload_json=canonical(body),
                                payload_hash=canonical_hash(body),
                                recorded_at=at,
                            )
                        )
                    due = next_hour(due)
                row.next_due_at = due
        self.stage_pending_event_motives(at)

    def stage_pending_event_motives(self, at):
        """Attach source motivations only before claim; no new event-only Run."""
        from .perpetual_event_agenda import collect_event_agenda_in_session
        from .perpetual_research_horizon import perpetual_horizon_seconds

        with self.database.session() as reader:
            keys = list(
                reader.scalars(
                    select(AdmissionRequest.request_id)
                    .where(
                        AdmissionRequest.protocol_version == PROTOCOL,
                        AdmissionRequest.status == "pending_admission",
                        AdmissionRequest.run_id.is_(None),
                    )
                    .order_by(AdmissionRequest.created_at)
                    .limit(8)
                )
            )
        for key in keys:
            with self.database.session() as reader:
                try:
                    proof = read_automatic_source_in_session(
                        reader, key, None, None, at=at, original_only=True
                    )
                except ValueError:
                    continue
            source = PerpetualPeriodicTriggerV1.model_validate(proof["source"])

            def prepare(reader, source=source):
                saved = _settings_original(reader, source.owner_id, source.partition)
                return collect_event_agenda_in_session(
                    reader,
                    archive=self.entry.binding.source.archive,
                    as_of=at,
                    instrument_refs=[saved.revision.values.instrument],
                    window_end=source.deadline_at
                    + timedelta(seconds=perpetual_horizon_seconds(source.horizon)),
                ).model_dump(mode="json")

            try:
                prepared = prepare_originals(self.database, prepare)
            except ValueError:
                # Keep the request pending; admission exposes the settings/source gap.
                continue
            with self.database.session() as session:
                begin_immediate(session)
                row = session.get(AdmissionRequest, key, populate_existing=True)
                if (
                    row is None
                    or row.status != "pending_admission"
                    or row.protocol_version != PROTOCOL
                ):
                    continue
                prepared.verify(session)
                current = read_automatic_source_in_session(
                    session, key, None, None, at=at, original_only=True
                )
                if current["source"] != proof["source"]:
                    raise ValueError("perpetual_schedule_source_changed")
                for entry in prepared.value["entries"]:
                    motive = PerpetualEventMotiveV1(
                        schema_version="perpetual-event-motive.v1",
                        schedule_ref=source.schedule_ref,
                        owner_id=source.owner_id,
                        partition=source.partition,
                        horizon=source.horizon,
                        scheduled_at=source.scheduled_at,
                        deadline_at=source.deadline_at,
                        event_ref=entry["source_ref"],
                        source_id=entry["source_id"],
                        event_key=entry["event_key"],
                        family=entry["family"],
                        stage_id=entry["stage_id"],
                        known_at=entry["known_at"],
                        recorded_at=at,
                    )
                    body = motive.model_dump(mode="json")
                    identity = "perpetual_event_motive:" + canonical_hash(
                        [key, body["event_ref"], motive.stage_id]
                    )
                    if session.get(Trigger, identity) is None:
                        session.add(
                            Trigger(
                                trigger_key=identity,
                                request_id=key,
                                kind="perpetual_event_motive",
                                scope_id=row.scope_id,
                                source_ref=motive.event_ref.ref,
                                payload_json=canonical(body),
                                payload_hash=canonical_hash(body),
                                recorded_at=at,
                            )
                        )

    def claimable_in_session(self, session, row, at):
        triggers = session.scalars(
            select(Trigger).where(Trigger.request_id == row.request_id)
        ).all()
        try:
            _, body = _automatic_root(triggers, row.protocol_version)
        except ValueError:
            row.status, row.error_code = "failed", "perpetual_schedule_trigger_missing"
            return False
        schedule = session.get(Schedule, body.schedule_id)
        if body.deadline_at <= at:
            row.status, row.error_code = "failed", "perpetual_schedule_slot_expired"
            return False
        if (
            schedule is None
            or schedule.state != "active"
            or schedule.revision_id != body.schedule_ref.ref
        ):
            row.status, row.error_code = "failed", "perpetual_schedule_changed_or_paused"
            return False
        from .perpetual_research_entry import QUESTION
        from .research_tracking import research_admission_guard_in_session
        from .study import stable_id

        study_id = stable_id(
            "study",
            json.dumps(["crypto", row.instrument, body.horizon, QUESTION], ensure_ascii=False),
        )
        guard = research_admission_guard_in_session(
            session, study_id=study_id, now=at, admission_origin="automatic"
        )
        if guard.reason_code is not None:
            row.error_code = guard.reason_code
            row.available_at = at + timedelta(seconds=60)
            return False
        return True

    async def prepare(self, key, generation, claim_owner):
        from starlette.concurrency import run_in_threadpool

        def read():
            with self.database.session() as session:
                original = read_automatic_source_in_session(
                    session, key, generation, claim_owner, at=aware_utc(self.now())
                )
                if original["source"]["schema_version"] in {
                    "perpetual-plan-review-trigger.v1",
                    "perpetual-plan-review-trigger.v2",
                    "perpetual-plan-review-trigger.v3",
                }:
                    from packages.contracts_py.decision_hub_contracts.generated import (
                        decision_product_v2,
                    )

                    from ..ports.perpetual_plan_watch import (
                        PerpetualPlanReviewTriggerV2,
                        PerpetualPlanReviewTriggerV3,
                    )
                    source_type = (
                        PerpetualPlanReviewTriggerV3
                        if original["source"]["schema_version"]
                        == "perpetual-plan-review-trigger.v3"
                        else PerpetualPlanReviewTriggerV2
                        if original["source"]["schema_version"]
                        == "perpetual-plan-review-trigger.v2"
                        else decision_product_v2.PerpetualPlanReviewTriggerV1
                    )
                    source = source_type.model_validate(original["source"])
                elif original["source"]["schema_version"] == "perpetual-research-event-trigger.v1":
                    source = PerpetualEventResearchTriggerV1.model_validate(original["source"])
                else:
                    source = PerpetualPeriodicTriggerV1.model_validate(original["source"])
                saved = _settings_original(session, source.owner_id, source.partition)
                consent = read_current_paper_consent_in_session(session, original)
                current = consent is not None
                command = parse_perpetual_research_command(
                    dict(
                        schema_version="perpetual-research-command.v3"
                        if current
                        else (
                            "perpetual-research-command.v1"
                            if source.horizon == "4h"
                            else "perpetual-research-command.v2"
                        ),
                        **({"input_policy": consent.command.input_policy} if current else {}),
                        request_id=key,
                        partition=source.partition.model_dump(mode="json"),
                        horizon=source.horizon,
                        expected_settings_revision=dict(
                            ref=saved.revision.revision_id, hash=saved.revision.payload_hash
                        ),
                        expected_settings_state_version=saved.state_version,
                    )
                )
                return source, command, original

        source, command, original = await run_in_threadpool(read)
        motives = await run_in_threadpool(
            prepare_originals,
            self.database,
            lambda reader: _motive_originals(reader, original, self.entry.binding.source.archive),
        )
        inst_id = source.partition.instrument_id.removeprefix("okx:")
        collected = await self.entry.collect_window(inst_id=inst_id)
        history = await self.entry.collect_history(inst_id=inst_id)
        issued = await run_in_threadpool(
            lambda: self.entry.prepare(
                source.owner_id,
                command,
                collected,
                automatic_claim=(key, generation, claim_owner),
                history=history,
            )
        )
        return source.owner_id, command, issued, motives

    def admit(self, key, generation, engine, prepared):
        owner, command, issued, motives = prepared
        self.entry.verify_archives(issued)
        with self.database.session() as reader:
            if (
                _motive_originals(
                    reader, issued.automatic_source, self.entry.binding.source.archive
                )
                != motives.value
            ):
                raise ValueError("perpetual_event_motive_original_changed")
        with self.database.session() as session:
            begin_immediate(session)
            at = aware_utc(self.now())
            row = session.get(AdmissionRequest, key, populate_existing=True)
            if not engine._owns(row, generation, at):
                return False
            motives.verify(session)
            with ContinuousAdmissionWriter(
                session, row, owner=engine.owner_id, generation=generation, acquired_at=at
            ) as writer:
                original = read_automatic_source_in_session(
                    session, key, generation, engine.owner_id, at=at
                )
                if original != issued.automatic_source:
                    raise ValueError("perpetual_schedule_source_changed")
                receipt = self.entry.commit_in_session(
                    session, owner, command, issued, admission_origin="automatic"
                )
                writer.complete(row, completed_at=aware_utc(self.now()))
                row.status, row.run_id, row.event_id = "queued", receipt.run_id, receipt.event_id
                row.updated_at, row.lease_owner, row.lease_expires_at = (
                    aware_utc(self.now()),
                    None,
                    None,
                )
                row.error_code = None
                return True
