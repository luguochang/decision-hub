"""Signed-command application path into the existing durable Run queue.

Only real saved settings and archived source preparation can issue the private
new-Run installer. The intent, Study, Run, admission, opportunity, original receipt
and notification commit together. No model execution occurs in this service.
"""

from __future__ import annotations

import json
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from starlette.concurrency import run_in_threadpool

from packages.contracts_py.decision_hub_contracts import ObservationCreate
from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionOpportunityV2,
)

from ..persistence.db import Database, ProductCommandRecord, RunRecord, as_utc, utcnow
from ..persistence.original_preparation import begin_immediate, prepare_originals
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.sources import make_notification_envelope
from .admission import AdmissionService
from .outbox import OutboxService
from .perpetual_close_source import PerpetualCloseSource
from .perpetual_research_binding import (
    ADMISSION_ROLE,
    PerpetualResearchBindingService,
    _json_value,
    _read,
    _save,
    _settings_original,
    canonical,
)
from .perpetual_research_horizon import (
    parse_perpetual_research_command,
    parse_perpetual_research_receipt,
    perpetual_horizon_seconds,
)
from .perpetual_research_inputs import prepared_admission_body
from .research_tracking import research_admission_guard_in_session
from .study import ensure_study, stable_id

KIND = "perpetual_research"
CLOSED_KIND = "perpetual_research_closed"
QUESTION = "perpetual-current-decision.v2"
_ISSUED = {}


def _key(owner, request_id):
    if not owner or len(owner) > 128 or owner.startswith("system:"):
        raise ValueError("perpetual_research_owner_invalid")
    return "perpresearch_" + canonical_hash([owner, request_id])


def _seal(body):
    value = parse_perpetual_research_receipt({**body, "payload_hash": "0" * 64}).model_dump(
        mode="json"
    )
    value["payload_hash"] = canonical_hash(
        {key: item for key, item in value.items() if key != "payload_hash"}
    )
    return parse_perpetual_research_receipt(value)


def _settings(session, owner, command):
    current = _settings_original(session, owner, command.partition)
    ref = dict(ref=current.revision.revision_id, hash=current.revision.payload_hash)
    if (
        ref != command.expected_settings_revision.model_dump(mode="json")
        or current.state_version != command.expected_settings_state_version
    ):
        raise ValueError("perpetual_research_settings_state_conflict")
    return current


@dataclass(frozen=True)
class _IssuedNewRun:
    database: Database
    inputs: object
    window: object
    owner_id: str
    command_hash: str
    planned_run_id: str
    planned_study_id: str
    inst_id: str
    horizon: str
    prepared_at: datetime
    valid_until: datetime
    automatic_source: dict | None = None
    automatic_claim: tuple | None = None


def _require_issued(value, session):
    issued = _ISSUED.get(id(value))
    if (
        issued is None
        or issued[0]() is not value
        or issued[1] != canonical_hash(value.inputs.value)
        or session.get_bind() is not value.database.engine
    ):
        raise ValueError("perpetual_research_unissued_new_run")
    value.inputs._require_issued(session)
    value.window._require_issued(session)


def authorize_perpetual_new_run_in_session(session, study, issued, *, at):
    """RunService's optional server resolver; not a caller verification flag."""
    _require_issued(issued, session)
    current = aware_utc(at)
    if (
        (
            study.study_id,
            study.market,
            study.instrument,
            study.horizon_scope,
            study.identity_basis,
            study.question_key,
        )
        != (
            issued.planned_study_id,
            "crypto",
            issued.inst_id,
            issued.horizon,
            "exact_perpetual_decision_v2",
            QUESTION,
        )
        or study.status != "active"
        or not issued.prepared_at <= current < issued.valid_until
        or not 0 <= (current - issued.window.value.end_at).total_seconds() <= 120
    ):
        raise ValueError("perpetual_research_new_run_scope_or_clock_invalid")
    issued.window.verify(session)
    issued.inputs.verify(session)
    return issued.planned_run_id


def install_perpetual_new_run_in_session(session, run, study, issued, *, at):
    _require_issued(issued, session)
    if (run.run_id, run.study_id, run.strategy_version, run.status) != (
        issued.planned_run_id,
        issued.planned_study_id,
        "research.v1",
        "admitted",
    ):
        raise ValueError("perpetual_research_new_run_install_mismatch")
    if not issued.inputs.source.matches() or not issued.window.source.matches():
        raise ValueError("perpetual_research_new_run_source_changed")
    # Originals were rechecked before own Run inserts in this same continuous
    # BEGIN IMMEDIATE. Only server-created identities and accepted clock change.
    body = json.loads(canonical(prepared_admission_body(issued.inputs.value)))
    if body["schema_version"] in {"perpetual-research-admission.v2", "perpetual-research-admission.v3"}:
        from ..ports.perpetual_research_inputs import PreparedPerpetualAdmissionV2
        from . import perpetual_research_inputs as account_inputs

        pair = PreparedPerpetualAdmissionV2.model_validate(issued.inputs.value)
        authority = pair.account_binding.authority
        if (
            pair.account_binding.run_id != run.run_id
            or authority.owner_id != issued.owner_id
            or authority.planned_run_id != run.run_id
            or authority.command_hash != issued.command_hash
        ):
            raise ValueError("perpetual_research_input_authority_scope_mismatch")
        if authority.origin == "manual_v3":
            valid_origin = issued.automatic_source is None and run.admission_origin == "manual"
        else:
            valid_origin = (
                issued.automatic_source is not None
                and run.admission_origin == "automatic"
                and authority.source_proof_hash == canonical_hash(issued.automatic_source)
                and issued.automatic_claim
                == (authority.claim_request_id, authority.claim_generation, authority.claim_owner)
                and body.get("automatic_source") == issued.automatic_source
            )
        if not valid_origin:
            raise ValueError("perpetual_research_input_authority_scope_mismatch")
        reference = account_inputs.save_account_binding_in_session(
            session, binding=pair.account_binding, commit_at=at
        )
        if reference.model_dump(mode="json") != body["input_binding_ref"]:
            raise ValueError("perpetual_research_account_binding_ref_mismatch")
    body.update(event_id=run.event_id, admitted_at=_json_value(at), recorded_at=_json_value(at))
    if issued.automatic_source is not None or body["schema_version"] == "perpetual-research-admission.v3":
        body["runtime_started_at"] = _json_value(at)
        from .perpetual_research_horizon import (
            PERPETUAL_RESEARCH_RUNTIME_SECONDS, derive_perpetual_deadlines,
        )

        body["runtime_deadline_at"] = _json_value(
            derive_perpetual_deadlines(prepared_at=issued.prepared_at, admitted_at=at)["runtime_deadline_at"]
            if body["schema_version"] == "perpetual-research-admission.v3"
            else at + timedelta(seconds=PERPETUAL_RESEARCH_RUNTIME_SECONDS)
        )
    body["opportunity"].update(
        event_ref=run.event_id, registered_at=_json_value(at), time_start=_json_value(at)
    )
    body["opportunity"] = DecisionOpportunityV2.model_validate(body["opportunity"]).model_dump(
        mode="json"
    )
    from ..persistence.action_repository import ActionRepository

    ActionRepository(session).register_opportunity(
        DecisionOpportunityV2.model_validate(body["opportunity"])
    )
    return _save(session, run.run_id, ADMISSION_ROLE, body)


class PerpetualResearchEntryService:
    def __init__(
        self,
        database: Database,
        binding_service: PerpetualResearchBindingService,
        *,
        total_deadline_seconds: int,
        collect_window: Callable | None = None,
        now: Callable[[], datetime] = utcnow,
    ):
        if binding_service.database is not database:
            raise ValueError("perpetual_research_entry_database_mismatch")
        if type(total_deadline_seconds) is not int or total_deadline_seconds <= 0:
            raise ValueError("perpetual_research_entry_budget_invalid")
        self.database, self.binding, self.now = database, binding_service, now
        self.total_deadline_seconds = total_deadline_seconds
        self.closes = PerpetualCloseSource(database, binding_service.source)
        self.collect_window = collect_window or self.closes.collect_window
        self._network_closes = self.closes.collect_window
        from .perpetual_history_cache import PerpetualHistoryCache

        self._history_cache = PerpetualHistoryCache(binding_service.source)

    async def collect_history(self, *, inst_id):
        """Optional 1H history for the base rate; only alongside real network closes."""
        # Recorded/injected close collectors (replay, tests) never trigger public I/O.
        if self.collect_window != self._network_closes:
            return None
        return await self._history_cache.collect(inst_id=inst_id)

    def receipt(self, owner_id, request_id):
        with self.database.session() as session:
            return self._receipt_in_session(session, owner_id, request_id)

    def _receipt_in_session(self, session, owner_id, request_id):
        row = session.get(ProductCommandRecord, _key(owner_id, request_id), populate_existing=True)
        if row is None:
            return None
        if row.kind == CLOSED_KIND:
            from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
                PerpetualResearchClosureReceiptV1,
            )

            value = PerpetualResearchClosureReceiptV1.model_validate(json.loads(row.result_json))
            body = value.model_dump(mode="json")
            if (
                row.owner_id != owner_id
                or value.owner_id != owner_id
                or value.request_id != request_id
                or row.target_id != request_id
                or value.command.request_id != request_id
                or row.payload_hash != value.command_hash
                or value.command_hash != canonical_hash(body["command"])
                or value.payload_hash
                != canonical_hash({k: v for k, v in body.items() if k != "payload_hash"})
                or canonical(body) != row.result_json
                or as_utc(row.created_at) != value.closed_at
            ):
                raise ValueError("perpetual_research_command_original_invalid")
            return value
        value = parse_perpetual_research_receipt(json.loads(row.result_json))
        body = value.model_dump(mode="json")
        admission, admission_ref = _read(session, value.run_id, ADMISSION_ROLE)
        run = session.get(RunRecord, value.run_id, populate_existing=True)
        if (
            row.kind != KIND
            or row.owner_id != owner_id
            or value.owner_id != owner_id
            or value.request_id != request_id
            or row.target_id != value.run_id
            or row.payload_hash != value.command_hash
            or body["payload_hash"]
            != canonical_hash({k: v for k, v in body.items() if k != "payload_hash"})
            or canonical(body) != row.result_json
            or as_utc(row.created_at) != value.accepted_at
            or admission_ref != body["admission_ref"]
            or admission["owner_id"] != owner_id
            or admission["partition"] != body["partition"]
            or admission["opportunity_id"] != value.opportunity_id
            or admission["context"]["horizon"] != getattr(value, "horizon", "4h")
            or run is None
            or (run.event_id, run.study_id) != (value.event_id, value.study_id)
            or admission["event_id"] != value.event_id
            or admission["study_id"] != value.study_id
            or body["settings_revision"]
            != dict(
                ref=admission["settings_revision"]["revision_id"],
                hash=admission["settings_revision"]["payload_hash"],
            )
        ):
            raise ValueError("perpetual_research_command_original_invalid")
        # Historical command receipt intentionally ignores current settings head,
        # source availability, Run completion and active execution generation.
        is_current = value.schema_version == "perpetual-research-receipt.v3"
        if is_current != (admission["schema_version"] in {"perpetual-research-admission.v2", "perpetual-research-admission.v3"}):
            raise ValueError("perpetual_research_command_original_invalid")
        if is_current:
            from .perpetual_research_inputs import read_input_binding_identity_in_session

            read_input_binding_identity_in_session(session, admission)
        return value

    def close_unaccepted(self, owner_id, command):
        """Fence a late manual writer using its existing unique command slot."""
        from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
            PerpetualResearchClosureReceiptV1,
        )

        from ..persistence.risk_research_records import RiskResearchRequestRecord

        command = parse_perpetual_research_command(command)
        command_body = command.model_dump(mode="json")
        digest = canonical_hash(command_body)
        with self.database.session() as session:
            begin_immediate(session)
            old = self._receipt_in_session(session, owner_id, command.request_id)
            if old is not None:
                if old.command_hash != digest:
                    raise ValueError("perpetual_research_request_conflict")
                return old
            if session.get(RiskResearchRequestRecord, command.request_id) is not None:
                raise ValueError("perpetual_research_automatic_request_requires_schedule_pause")
            at = aware_utc(self.now())
            body = dict(
                schema_version="perpetual-research-closure-receipt.v1",
                owner_id=owner_id,
                request_id=command.request_id,
                command_hash=digest,
                command=command_body,
                status="closed_unaccepted",
                reason_code="owner_closed_unaccepted",
                closed_at=_json_value(at),
            )
            body = PerpetualResearchClosureReceiptV1.model_validate(
                {**body, "payload_hash": "0" * 64}
            ).model_dump(mode="json")
            body["payload_hash"] = canonical_hash(
                {k: v for k, v in body.items() if k != "payload_hash"}
            )
            value = PerpetualResearchClosureReceiptV1.model_validate(body)
            session.add(
                ProductCommandRecord(
                    request_id=_key(owner_id, command.request_id),
                    owner_id=owner_id,
                    target_id=command.request_id,
                    kind=CLOSED_KIND,
                    payload_hash=digest,
                    result_json=canonical(value.model_dump(mode="json")),
                    created_at=at,
                )
            )
            session.flush()
            return value

    def _existing_command(self, session, owner, command):
        old = self._receipt_in_session(session, owner, command.request_id)
        if old is not None:
            if old.command_hash != canonical_hash(command.model_dump(mode="json")):
                raise ValueError("perpetual_research_request_conflict")
            if old.status == "closed_unaccepted":
                raise ValueError("perpetual_research_command_closed")
        return old

    def _precheck(self, owner, command):
        with self.database.session() as session:
            old = self._existing_command(session, owner, command)
            if old is not None:
                if old.command_hash != canonical_hash(command.model_dump(mode="json")):
                    raise ValueError("perpetual_research_request_conflict")
                return old
            _settings(session, owner, command)
        return None

    async def create(self, owner_id, command):
        command = parse_perpetual_research_command(command)
        old = await run_in_threadpool(self._precheck, owner_id, command)
        if old is not None:
            return old
        inst_id = command.partition.instrument_id.split(":", 1)[1]
        collected = await self.collect_window(inst_id=inst_id)
        history = await self.collect_history(inst_id=inst_id)
        issued = await run_in_threadpool(
            lambda: self.prepare(owner_id, command, collected, history=history)
        )
        return await run_in_threadpool(self.commit, owner_id, command, issued)

    def prepare(self, owner, command, collected, *, automatic_claim=None, history=None):
        command = parse_perpetual_research_command(command)
        is_current = command.schema_version == "perpetual-research-command.v3"
        with self.database.session() as session:
            current = _settings(session, owner, command)
            instrument = current.revision.values.instrument
            automatic_source = None
            if automatic_claim is not None:
                from .perpetual_research_schedule import read_automatic_source_in_session

                key, generation, claim_owner = automatic_claim
                automatic_source = read_automatic_source_in_session(
                    session, key, generation, claim_owner, at=aware_utc(self.now())
                )
        window = self.closes.prepare_perpetual_close_window(
            instrument_ref=instrument, capture_ids=collected.capture_ids, as_of=collected.as_of
        )
        at = aware_utc(self.now())
        deadline_policy = None
        if is_current:
            from .perpetual_research_horizon import (
                PERPETUAL_RESEARCH_RUNTIME_SECONDS, derive_perpetual_deadlines,
                perpetual_deadline_policy_original,
            )

            if self.total_deadline_seconds != PERPETUAL_RESEARCH_RUNTIME_SECONDS:
                raise ValueError("perpetual_research_entry_budget_invalid")
            deadline_policy, _ = perpetual_deadline_policy_original()
            until = derive_perpetual_deadlines(prepared_at=at)["candidate_valid_until"]
        else:
            until = at + timedelta(seconds=self.total_deadline_seconds)
            if automatic_source is not None:
                until = min(until, datetime.fromisoformat(automatic_source["source"]["deadline_at"]))
        inst = command.partition.instrument_id.split(":", 1)[1]
        study_id = stable_id(
            "study", json.dumps(["crypto", inst, command.horizon, QUESTION], ensure_ascii=False)
        )
        run_id = "run_" + uuid4().hex
        input_authority = None
        if is_current:
            from .perpetual_research_inputs import (
                issue_automatic_input_authority,
                issue_manual_input_authority,
            )

            issuer = (
                issue_manual_input_authority
                if automatic_source is None
                else issue_automatic_input_authority
            )
            input_authority = issuer(
                command,
                authenticated_owner=owner,
                planned_run_id=run_id,
                database=self.database,
                prepared_at=at,
                valid_until=until,
                deadline_policy=deadline_policy,
                **(
                    {"automatic_claim": automatic_claim, "automatic_source": automatic_source}
                    if automatic_source is not None
                    else {}
                ),
            )

        def read(session):
            saved = _settings(session, owner, command)
            if (
                not aware_utc(collected.as_of) <= at
                or not 0 <= (at - window.value.end_at).total_seconds() <= 120
            ):
                raise ValueError("perpetual_research_window_stale_or_clock_invalid")
            body = self.binding.prepare_admission_body_in_session(
                session,
                run_id=run_id,
                event_id=None,
                study_id=study_id,
                inst_id=inst,
                owner_id=owner,
                partition=command.partition,
                saved=saved,
                window=window,
                at=at,
                until=until,
                horizon=command.horizon,
                input_authority=input_authority,
            )
            admission = body["admission"] if is_current else body
            if automatic_source is not None:
                admission["automatic_source"] = automatic_source
            if history is not None:
                from .perpetual_base_rate_source import read_direction_base_rate_in_session

                horizon_seconds = perpetual_horizon_seconds(command.horizon)
                admission["direction_base_rate"] = read_direction_base_rate_in_session(
                    session,
                    self.binding.source.archive,
                    inst_id=inst,
                    capture_ids=history.capture_ids,
                    as_of=min(aware_utc(history.as_of), at),
                    threshold_fraction=next(
                        item.fraction
                        for item in window.value.thresholds
                        if item.duration_seconds == horizon_seconds
                    ),
                    horizon_seconds=horizon_seconds,
                )
            return body

        inputs = prepare_originals(self.database, read)
        issued = _IssuedNewRun(
            self.database,
            inputs,
            window,
            owner,
            canonical_hash(command.model_dump(mode="json")),
            run_id,
            study_id,
            inst,
            command.horizon,
            at,
            until,
            automatic_source,
            automatic_claim,
        )
        issued_key = id(issued)
        _ISSUED[issued_key] = (
            weakref.ref(issued, lambda _ref, key=issued_key: _ISSUED.pop(key, None)),
            canonical_hash(inputs.value),
        )
        return issued

    def verify_archives(self, issued):
        with self.database.session() as reader:
            _require_issued(issued, reader)
            self.binding.source.read_contract_in_session(reader, issued.window.value.instrument_ref)
            body = prepared_admission_body(issued.inputs.value)
            if "event_agenda" in body:
                from .perpetual_event_agenda import read_event_agenda_in_session

                read_event_agenda_in_session(
                    reader, body["event_agenda"], archive=self.binding.source.archive
                )
            for body_ref in {item.body_ref for item in issued.window.value.closes}:
                self.binding.source.archive.read(body_ref)

    def commit(self, owner, command, issued):
        command = parse_perpetual_research_command(command)
        old = self._precheck(owner, command)
        if old is not None:
            return old
        self.verify_archives(issued)
        with self.database.session() as session:
            begin_immediate(session)
            return self.commit_in_session(session, owner, command, issued)

    def commit_in_session(self, session, owner, command, issued, *, admission_origin="manual"):
        from .run import RunService

        command = parse_perpetual_research_command(command)
        old = self._existing_command(session, owner, command)
        digest = canonical_hash(command.model_dump(mode="json"))
        if old is not None:
            if old.command_hash != digest:
                raise ValueError("perpetual_research_request_conflict")
            return old
        _require_issued(issued, session)
        if issued.owner_id != owner or issued.command_hash != digest:
            raise ValueError("perpetual_research_issued_command_mismatch")
        at = aware_utc(self.now())
        if (admission_origin == "automatic") != (issued.automatic_source is not None):
            raise ValueError("perpetual_research_automatic_authority_required")
        if issued.automatic_source is not None:
            from .perpetual_research_schedule import read_automatic_source_in_session

            key, generation, claim_owner = issued.automatic_claim
            if (
                read_automatic_source_in_session(session, key, generation, claim_owner, at=at)
                != issued.automatic_source
            ):
                raise ValueError("perpetual_schedule_source_changed")
        _settings(session, owner, command)
        issued.window.verify(session)
        issued.inputs.verify(session)
        event_id, _, _ = AdmissionService(self.database, clock=lambda: at).admit(
            ObservationCreate(
                text=(
                    f"{'Authorized automatic slot requested' if admission_origin == 'automatic' else 'Owner requested'} current {issued.inst_id} perpetual {issued.horizon} research. "
                    f"Request namespace: {_key(owner, command.request_id)}. "
                    "This is a research intent, not market evidence."
                ),
                source_id="decision-desk-perpetual-research",
                source_type="manual",
                language="en",
                observed_at=at,
                event_hint="crypto_market_snapshot",
            ),
            idempotency_key=_key(owner, command.request_id),
            session=session,
        )
        study_id = ensure_study(
            session,
            question_key=QUESTION,
            title=issued.inst_id + " " + issued.horizon + " perpetual research",
            market="crypto",
            instrument=issued.inst_id,
            horizon_scope=issued.horizon,
            identity_basis="exact_perpetual_decision_v2",
            now=at,
        )
        if study_id != issued.planned_study_id:
            raise ValueError("perpetual_research_new_study_identity_mismatch")
        guard = research_admission_guard_in_session(
            session, study_id=study_id, now=at, admission_origin=admission_origin
        )
        if guard.reason_code is not None:
            raise ValueError(guard.reason_code)
        runs = RunService(
            self.database,
            clock=lambda: at,
            perpetual_admission_resolver=lambda _session, _study, _at: issued,
        )
        run_id, _ = runs.create(
            event_id,
            idempotency_key=_key(owner, command.request_id),
            strategy_version="research.v1",
            admission_origin=admission_origin,
            study_id=study_id,
            session=session,
        )
        admission, admission_ref = _read(session, run_id, ADMISSION_ROLE)
        is_current = command.schema_version == "perpetual-research-command.v3"
        if is_current:
            receipt_version = "perpetual-research-receipt.v3"
            receipt_fields = dict(
                horizon=command.horizon,
                input_policy=command.input_policy,
                input_binding_ref=admission["input_binding_ref"],
            )
        else:
            receipt_version = (
                "perpetual-research-receipt.v1"
                if command.horizon == "4h"
                else "perpetual-research-receipt.v2"
            )
            receipt_fields = {} if command.horizon == "4h" else {"horizon": command.horizon}
        receipt = _seal(
            dict(
                schema_version=receipt_version,
                **receipt_fields,
                owner_id=owner,
                request_id=command.request_id,
                command_hash=digest,
                partition=command.partition.model_dump(mode="json"),
                settings_revision=command.expected_settings_revision.model_dump(mode="json"),
                event_id=event_id,
                study_id=study_id,
                run_id=run_id,
                admission_ref=admission_ref,
                opportunity_id=admission["opportunity_id"],
                accepted_at=at,
                status="queued",
                financial_qualified=False,
            )
        )
        session.add(
            ProductCommandRecord(
                request_id=_key(owner, command.request_id),
                owner_id=owner,
                target_id=run_id,
                kind=KIND,
                payload_hash=digest,
                result_json=canonical(receipt.model_dump(mode="json")),
                created_at=at,
            )
        )
        OutboxService(self.database).enqueue_subject(
            make_notification_envelope(
                subject_kind="event_opportunity",
                subject_id=admission["opportunity_id"],
                channel="local",
                dedupe_key="perpetual-research:" + _key(owner, command.request_id),
                subject=issued.inst_id + " " + issued.horizon + " research queued",
                body=(
                    "Research request accepted. Advice, financial qualification "
                    "and paper execution remain pending."
                ),
                created_at=at,
            ),
            session=session,
        )
        session.flush()
        if is_current:
            # Both new originals, receipt and business writes share the existing
            # caller-owned transaction. A failed recheck rolls the entire Run back.
            issued.inputs.verify_after_writes(session)
            issued.window.verify_after_writes(session)
            if self._receipt_in_session(session, owner, command.request_id) != receipt:
                raise ValueError("perpetual_research_input_receipt_install_mismatch")
        return receipt
