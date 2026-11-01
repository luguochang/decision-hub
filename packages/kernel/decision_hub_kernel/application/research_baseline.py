"""Server-origin first evidence batch; identities in, authenticated originals out.

The batch does not claim native tool execution or model understanding. Every
provider call still uses the normal durable Gateway and its original budget.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Protocol
from weakref import WeakValueDictionary

from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    ResearchCapabilityInvocation,
    ResearchCapabilityResult,
)

from ..decision.research_baseline import (
    BASELINE_PROFILE,
    compile_baseline_plan,
    run_baseline_waves,
)
from ..decision.sufficiency import assess_evidence_sufficiency
from ..persistence.db import (
    Database,
    DshSessionLinkRecord,
    EvidenceParsedLinkRecord,
    FactVersionRecord,
    ParsedVersionRecord,
    RawCaptureRecord,
    ResearchEvidenceRecord,
    ResearchFactRecord,
    ResearchToolCallReservationRecord,
    RunInputRefRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
    utcnow,
)
from ..persistence.original_preparation import begin_immediate, prepare_originals
from ..ports.market_path import canonical_hash
from ..ports.research_baseline import (
    BaselineBatch,
    BaselineDelivery,
    BaselineError,
    BaselinePlan,
    BaselineRequest,
    BaselineSkipped,
    BaselineSlot,
)
from ..ports.research_observation import ObservedExecutionRef
from .fact_store import _fact_model
from .research_evidence import ResearchCapabilityError, _evidence_model
from .research_execution_binding import (
    read_bundle_identity_in_session,
    read_prepared_execution_in_session,
)
from .research_method import canonical_recipe
from .research_recipe_read_scope import current_recipe_read_scope, recipe_original_read_scope

PLAN_ROLE = "research_baseline_plan:1"
BATCH_ROLE = "research_baseline_batch:1"


class BaselineGateway(Protocol):
    async def invoke_live(
        self, invocation: ResearchCapabilityInvocation
    ) -> ResearchCapabilityResult: ...


def _role(session: Session, run_id: str, role: str) -> RunRoleOutputRecord | None:
    return (
        session.query(RunRoleOutputRecord)
        .filter_by(run_id=run_id, role=role)
        .populate_existing()
        .one_or_none()
    )


def _ref(row: RunRoleOutputRecord) -> ObservedExecutionRef:
    return ObservedExecutionRef(
        output_id=row.output_id,
        role=row.role,
        schema_version=row.schema_version,
        payload_hash=row.payload_hash,
        recorded_at=as_utc(row.created_at),
    )


def _decode(row: RunRoleOutputRecord, contract: Any) -> Any:
    value = contract.model_validate_json(row.payload_json)
    if (
        row.payload_hash != hashlib.sha256(row.payload_json.encode()).hexdigest()
        or row.payload_json != canonical_recipe(value.model_dump(mode="json"))
        or row.schema_version != value.schema_version
        or row.run_id != value.run_id
        or row.output_id != "baseline_" + canonical_hash([row.run_id, row.role])
    ):
        raise ValueError("research_baseline_original_changed")
    instant = value.planned_at if isinstance(value, BaselinePlan) else value.recorded_at
    if as_utc(row.created_at) != instant:
        raise ValueError("research_baseline_original_time_changed")
    return value


def _insert(session: Session, value: Any, role: str, at: datetime) -> RunRoleOutputRecord:
    raw = canonical_recipe(value.model_dump(mode="json"))
    old = _role(session, value.run_id, role)
    if old is not None:
        if old.payload_json != raw:
            raise ValueError("research_baseline_idempotency_conflict")
        return old
    row = RunRoleOutputRecord(
        output_id="baseline_" + canonical_hash([value.run_id, role]),
        run_id=value.run_id,
        role=role,
        schema_version=value.schema_version,
        payload_json=raw,
        payload_hash=hashlib.sha256(raw.encode()).hexdigest(),
        created_at=at,
    )
    session.add(row)
    session.flush()
    return row


def _authorize(session: Session, request: BaselineRequest):
    prepared = read_prepared_execution_in_session(session, request.run_id, request.generation)
    if (
        prepared.execution.dsh_session_id != request.research_session_id
        or prepared.method.request_hash != request.request_hash
    ):
        raise ValueError("research_baseline_identity_mismatch")
    return prepared


def _owner(session: Session, run_id: str, generation: int, now: datetime) -> str:
    run = session.get(RunRecord, run_id, populate_existing=True)
    link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
    if (
        run is None
        or link is None
        or run.status != "running"
        or not run.lease_owner
        or run.lease_expires_at is None
        or as_utc(run.lease_expires_at) <= now
        or link.generation != generation
    ):
        raise ValueError("research_baseline_run_lease_unavailable")
    return run.lease_owner


def read_baseline_plan_in_session(
    session: Session, *, run_id: str
) -> tuple[BaselinePlan, Any, Any, list[Any]] | None:
    """Authenticate the unique plan against its original prepared generation."""
    row = _role(session, run_id, PLAN_ROLE)
    if row is None:
        return None
    plan = _decode(row, BaselinePlan)
    prepared = _authorize(
        session,
        BaselineRequest(
            schema_version="research-baseline-request.v1",
            run_id=run_id,
            research_session_id=plan.research_session_id,
            generation=1,
            request_hash=plan.request_hash,
        ),
    )
    bundle, identity = read_bundle_identity_in_session(session, run_id)
    catalog = json.loads(plan.catalog_json)
    expected = compile_baseline_plan(
        request=prepared.execution.request,
        session_id=plan.research_session_id,
        generation=1,
        request_hash=prepared.method.request_hash,
        execution_request_hash=prepared.execution.execution_request_hash,
        method_hash=prepared.method.method_hash,
        profile_ref=prepared.method.definition.profile_ref,
        instrument=bundle.task.factory_source.study.instrument,
        catalog=catalog,
        planned_at=plan.planned_at,
    )
    if expected != plan or plan.planned_at < prepared.execution.recorded_at:
        raise ValueError("research_baseline_plan_binding_changed")
    from .research_policy_checker import _trajectory_anchor

    anchor, anchors = _trajectory_anchor(session, bundle, identity, prepared)
    return plan, prepared, anchor, [row, *anchors]


def _result_originals(
    session: Session, *, run_id: str, result: ResearchCapabilityResult, recorded_at: datetime
) -> None:
    """Match admitted DTOs, PIT and existing parsed/fact lineage; no source upgrade."""
    evidence_ids = set()
    for candidate in result.evidence_candidates:
        row = session.get(ResearchEvidenceRecord, candidate.evidence_id, populate_existing=True)
        if (
            row is None
            or row.run_id != run_id
            or _evidence_model(row) != candidate
            or as_utc(row.accepted_at) > recorded_at
            or candidate.received_at > recorded_at
        ):
            raise ValueError("research_baseline_evidence_original_changed")
        link = session.get(EvidenceParsedLinkRecord, candidate.evidence_id, populate_existing=True)
        parsed = (
            session.get(ParsedVersionRecord, link.parsed_version_id, populate_existing=True)
            if link
            else None
        )
        if (
            parsed is None
            or parsed.content_hash != candidate.content_hash
            or parsed.content_ref != candidate.content_ref
            or parsed.acquisition_receipt_id != candidate.acquisition_receipt_id
            or as_utc(parsed.recorded_at) > recorded_at
        ):
            raise ValueError("research_baseline_parsed_original_missing")
        for capture_id in json.loads(parsed.capture_ids_json):
            capture = session.get(RawCaptureRecord, capture_id, populate_existing=True)
            if (
                capture is None
                or not capture.body_hash
                or not capture.body_ref
                or not capture.body_ref.endswith(capture.body_hash)
                or capture.status_code is None
                or not 200 <= capture.status_code < 300
                or as_utc(capture.recorded_at) > recorded_at
            ):
                raise ValueError("research_baseline_capture_original_missing")
        evidence_ids.add(candidate.evidence_id)
    for fact in result.facts or []:
        row = session.get(ResearchFactRecord, fact.fact_id, populate_existing=True)
        link = session.get(RunInputRefRecord, fact.fact_id, populate_existing=True)
        version = (
            session.get(FactVersionRecord, link.fact_version_id, populate_existing=True)
            if link
            else None
        )
        if (
            row is None
            or row.run_id != run_id
            or _fact_model(row) != fact
            or fact.evidence_id not in evidence_ids
            or link is None
            or link.run_id != run_id
            or version is None
            or as_utc(version.known_at) > recorded_at
            or json.loads(version.payload_json).get("payload_hash") != fact.payload_hash
        ):
            raise ValueError("research_baseline_fact_original_changed")


def _denial_role(request_id: str) -> str:
    return "research_baseline_denial:" + hashlib.sha256(request_id.encode()).hexdigest()[:40]


def _read_denial(session: Session, plan: BaselinePlan, slot: BaselineSlot, as_of: datetime):
    row = _role(session, plan.run_id, _denial_role(slot.invocation.request_id))
    if row is None:
        return None
    body = json.loads(row.payload_json)
    expected = {
        "schema_version",
        "run_id",
        "generation",
        "request_id",
        "plan_hash",
        "error_code",
        "error_provenance",
        "recorded_at",
    }
    if (
        set(body) != expected
        or body["schema_version"] != "research-baseline-denial.v1"
        or row.schema_version != body["schema_version"]
        or body["run_id"] != plan.run_id
        or body["generation"] != 1
        or body["plan_hash"] != plan.plan_hash
        or body["request_id"] != slot.invocation.request_id
        or not isinstance(body["error_code"], str)
        or body["error_provenance"].get("error_code") != body["error_code"]
        or row.payload_json != canonical_recipe(body)
        or row.payload_hash != hashlib.sha256(row.payload_json.encode()).hexdigest()
        or not plan.planned_at <= as_utc(row.created_at) <= as_of
        or as_utc(row.created_at).isoformat() != body["recorded_at"]
    ):
        raise ValueError("research_baseline_denial_original_changed")
    return row, BaselineError(
        request_id=slot.invocation.request_id,
        capability_id=slot.invocation.capability_id,
        requirement_id=slot.invocation.requirement_id,
        error_code=body["error_code"],
    )


def read_baseline_slots_in_session(session: Session, *, run_id: str, recorded_at: datetime):
    """Actual census from plan plus normal timing/admission/adapter originals."""
    material = read_baseline_plan_in_session(session, run_id=run_id)
    if material is None:
        raise ValueError("research_baseline_plan_missing")
    plan, prepared, anchor, rows = material
    results, errors, calls = [], [], []
    from .research_policy_checker import read_capability_admission_in_session

    for slot in plan.slots:
        invocation = slot.invocation
        reservation = session.get(
            ResearchToolCallReservationRecord,
            (run_id, invocation.request_id),
            populate_existing=True,
        )
        if slot.unavailable_reason is not None:
            if reservation is not None:
                raise ValueError("research_baseline_unavailable_slot_executed")
            errors.append(
                BaselineError(
                    request_id=invocation.request_id,
                    capability_id=invocation.capability_id,
                    requirement_id=invocation.requirement_id,
                    error_code=slot.unavailable_reason,
                )
            )
            continue
        if reservation is None:
            denial = _read_denial(session, plan, slot, recorded_at)
            if denial is not None:
                rows.append(denial[0])
                errors.append(denial[1])
                continue
        if reservation is None or reservation.status == "running":
            raise ValueError("research_baseline_slot_not_terminal")
        args = invocation.model_dump(mode="json", exclude={"schema_version", "research_session_id"})
        call, originals, problem = read_capability_admission_in_session(
            session, reservation, invocation.request_id, args, prepared, anchor
        )
        if problem or call is None:
            raise ValueError(problem or "research_baseline_admission_missing")
        if (
            as_utc(reservation.reserved_at) < plan.planned_at
            or as_utc(reservation.reserved_at) >= plan.acquisition_close_at
            or reservation.completed_at is None
            or as_utc(reservation.completed_at) > recorded_at
        ):
            raise ValueError("research_baseline_slot_time_invalid")
        rows.extend(originals)
        calls.append(call)
        if reservation.status == "completed":
            result = ResearchCapabilityResult.model_validate_json(reservation.result_json or "null")
            if (
                result.request_id != invocation.request_id
                or result.capability_id != invocation.capability_id
                or any(
                    item.research_session_id != invocation.research_session_id
                    for item in result.evidence_candidates
                )
            ):
                raise ValueError("research_baseline_result_binding_changed")
            _result_originals(session, run_id=run_id, result=result, recorded_at=recorded_at)
            results.append(result)
        else:
            error = json.loads(reservation.error_json or "null")
            if not isinstance(error, dict) or not isinstance(error.get("error_code"), str):
                raise ValueError("research_baseline_error_original_missing")
            errors.append(
                BaselineError(
                    request_id=invocation.request_id,
                    capability_id=invocation.capability_id,
                    requirement_id=invocation.requirement_id,
                    error_code=error["error_code"],
                )
            )
    return plan, prepared, rows, results, errors, calls


def _remaining_at(session: Session, run_id: str, maximum: int, at: datetime) -> int:
    # A reservation consumes the original budget once, including terminal errors.
    # Freeze the census at availability; later native calls do not rewrite it.
    count = (
        session.query(ResearchToolCallReservationRecord)
        .filter(
            ResearchToolCallReservationRecord.run_id == run_id,
            ResearchToolCallReservationRecord.reserved_at <= at,
        )
        .count()
    )
    return max(0, maximum - count)


def read_baseline_delivery_in_session(
    session: Session, *, request: BaselineRequest, as_of: datetime
) -> BaselineDelivery | None:
    if current_recipe_read_scope() is not None:
        return _read_baseline_delivery_in_session(session, request=request, as_of=as_of)
    with recipe_original_read_scope(session):
        return _read_baseline_delivery_in_session(session, request=request, as_of=as_of)


def _read_baseline_delivery_in_session(
    session: Session, *, request: BaselineRequest, as_of: datetime
) -> BaselineDelivery | None:
    """Public identity reader. Batch DTOs never authorize their own contents."""
    current = _authorize(session, request)
    if current.method.definition.profile_ref != BASELINE_PROFILE:
        return None
    row = _role(session, request.run_id, BATCH_ROLE)
    if row is None:
        return None
    batch = _decode(row, BaselineBatch)
    plan, prepared, _, results, errors, _ = read_baseline_slots_in_session(
        session, run_id=request.run_id, recorded_at=batch.recorded_at
    )
    plan_row = _role(session, request.run_id, PLAN_ROLE)
    assert plan_row is not None
    coverage = assess_evidence_sufficiency(
        prepared.execution.request.evidence_requirements,
        [item for result in results for item in result.evidence_candidates],
        facts=[item for result in results for item in (result.facts or [])],
        cutoff_at=batch.recorded_at,
        assessed_at=batch.recorded_at,
    )
    if (
        batch.recorded_at > as_of
        or batch.plan_ref != _ref(plan_row)
        or batch.method_hash != current.method.method_hash
        or batch.request_hash != request.request_hash
        or batch.research_session_id != request.research_session_id
        or batch.results != tuple(results)
        or batch.errors != tuple(errors)
        or batch.coverage != coverage
        or batch.status != ("ready" if coverage.status == "sufficient" else "hard_gap")
        or batch.acquisition_close_at != plan.acquisition_close_at
        or batch.remaining_tool_calls
        != _remaining_at(
            session,
            request.run_id,
            prepared.execution.request.execution_budget.max_tool_calls,
            batch.recorded_at,
        )
    ):
        raise ValueError("research_baseline_batch_binding_changed")
    return BaselineDelivery.model_validate_json(
        canonical_recipe(
            {
                **batch.model_dump(mode="json"),
                "schema_version": "research-baseline-delivery.v1",
                "batch_ref": _ref(row).model_dump(mode="json"),
            }
        )
    )


class ResearchBaselineService:
    def __init__(
        self,
        database: Database,
        *,
        gateway: BaselineGateway,
        query_catalog: Mapping[str, Any],
        clock: Callable[[], datetime] = utcnow,
    ):
        self.database, self.gateway, self.clock = database, gateway, clock
        self.catalog = json.loads(canonical_recipe(query_catalog))
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    async def deliver(self, request: BaselineRequest) -> BaselineDelivery | BaselineSkipped:
        # Same-process duplicate pre-steps share the fixed plan. Cross-process
        # collisions are fenced by writer+unique roles and the Gateway reservation.
        lock = self._locks.setdefault(request.run_id, asyncio.Lock())
        async with lock:
            initial = await self._thread(lambda: self._prepare_plan(request))
            if isinstance(initial, (BaselineDelivery, BaselineSkipped)):
                return initial
            if request.generation != 1:
                raise ValueError("research_baseline_original_missing")

            async def invoke(slot: BaselineSlot):
                if slot.unavailable_reason is not None:
                    return None
                if await self._thread(lambda: self._has_denial(initial, slot)):
                    return None
                try:
                    return await self.gateway.invoke_live(slot.invocation)
                except ResearchCapabilityError as exc:
                    # Only a real persisted terminal failure may enter the batch.
                    # Running/transient/not-admitted calls remain resumable gaps.
                    await self._thread(lambda error=exc: self._record_denial(initial, slot, error))
                    return None

            await run_baseline_waves(initial, invoke, lambda: None)
            return await self._thread(lambda: self._finish(request))

    def _has_denial(self, plan: BaselinePlan, slot: BaselineSlot) -> bool:
        with self.database.session() as session:
            return _read_denial(session, plan, slot, self.clock()) is not None

    def _record_denial(
        self, plan: BaselinePlan, slot: BaselineSlot, error: ResearchCapabilityError
    ):
        # This is only an unavailable-at-admission receipt. It never supplies
        # Evidence or asserts a completed provider call without a reservation.
        with self.database.session() as session:
            begin_immediate(session)
            now = self.clock()
            _owner(session, plan.run_id, 1, now)
            original = _role(session, plan.run_id, PLAN_ROLE)
            if original is None or _decode(original, BaselinePlan) != plan:
                raise ValueError("research_baseline_plan_binding_changed")
            reservation = session.get(
                ResearchToolCallReservationRecord,
                (plan.run_id, slot.invocation.request_id),
                populate_existing=True,
            )
            if reservation is not None:
                if reservation.status == "running":
                    raise ValueError("research_baseline_slot_not_terminal")
                return
            if _read_denial(session, plan, slot, now) is not None:
                return
            body = {
                "schema_version": "research-baseline-denial.v1",
                "run_id": plan.run_id,
                "generation": 1,
                "request_id": slot.invocation.request_id,
                "plan_hash": plan.plan_hash,
                "error_code": error.error_code,
                "error_provenance": error.provenance().model_dump(mode="json"),
                "recorded_at": now.isoformat(),
            }
            raw = canonical_recipe(body)
            session.add(
                RunRoleOutputRecord(
                    output_id="baseline_denial_" + canonical_hash(body),
                    run_id=plan.run_id,
                    role=_denial_role(slot.invocation.request_id),
                    schema_version=body["schema_version"],
                    payload_json=raw,
                    payload_hash=hashlib.sha256(raw.encode()).hexdigest(),
                    created_at=now,
                )
            )

    async def _thread(self, function):
        task = asyncio.create_task(asyncio.to_thread(function))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.shield(task)
            raise

    def _prepare_plan(self, request: BaselineRequest):
        def read(session):
            current = _authorize(session, request)
            if current.method.definition.profile_ref != BASELINE_PROFILE:
                return BaselineSkipped(
                    schema_version="research-baseline-skipped.v1",
                    status="skipped",
                    run_id=request.run_id,
                    research_session_id=request.research_session_id,
                    generation=request.generation,
                    request_hash=request.request_hash,
                    reason_code="method_not_opted_in",
                )
            existing = read_baseline_delivery_in_session(
                session, request=request, as_of=self.clock()
            )
            if existing is not None:
                return existing
            material = read_baseline_plan_in_session(session, run_id=request.run_id)
            if material is not None:
                return material[0]
            if request.generation != 1:
                raise ValueError("research_baseline_original_missing")
            bundle, _ = read_bundle_identity_in_session(session, request.run_id)
            return compile_baseline_plan(
                request=current.execution.request,
                session_id=request.research_session_id,
                generation=1,
                request_hash=request.request_hash,
                execution_request_hash=current.execution.execution_request_hash,
                method_hash=current.method.method_hash,
                profile_ref=current.method.definition.profile_ref,
                instrument=bundle.task.factory_source.study.instrument,
                catalog=self.catalog,
                planned_at=self.clock(),
            )

        def scoped_read(session):
            with recipe_original_read_scope(session):
                return read(session)

        prepared = prepare_originals(
            self.database,
            scoped_read,
            link_activity_observations=True,
            link_tool_counter_observations=True,
        )
        value = prepared.value
        if not isinstance(value, BaselinePlan):
            return value
        with self.database.session() as session:
            begin_immediate(session)
            prepared.verify(session)
            _owner(session, request.run_id, request.generation, self.clock())
            _insert(session, value, PLAN_ROLE, value.planned_at)
            prepared.verify_after_writes(session)
        return value

    def _finish(self, request: BaselineRequest) -> BaselineDelivery:
        def read(session):
            existing = read_baseline_delivery_in_session(
                session, request=request, as_of=self.clock()
            )
            if existing is not None:
                return existing
            now = self.clock()
            plan, current, _, results, errors, _ = read_baseline_slots_in_session(
                session, run_id=request.run_id, recorded_at=now
            )
            row = _role(session, request.run_id, PLAN_ROLE)
            assert row is not None
            link = session.get(DshSessionLinkRecord, request.run_id, populate_existing=True)
            coverage = assess_evidence_sufficiency(
                current.execution.request.evidence_requirements,
                [item for result in results for item in result.evidence_candidates],
                facts=[item for result in results for item in (result.facts or [])],
                cutoff_at=now,
                assessed_at=now,
            )
            batch = BaselineBatch(
                schema_version="research-baseline-batch.v1",
                status="ready" if coverage.status == "sufficient" else "hard_gap",
                run_id=request.run_id,
                research_session_id=request.research_session_id,
                generation=1,
                request_hash=request.request_hash,
                method_hash=plan.method_hash,
                plan_ref=_ref(row),
                results=tuple(results),
                errors=tuple(errors),
                coverage=coverage,
                remaining_tool_calls=max(
                    0,
                    current.execution.request.execution_budget.max_tool_calls
                    - link.tool_calls_started,
                ),
                acquisition_close_at=plan.acquisition_close_at,
                recorded_at=now,
                origin="server_baseline",
                skill_execution_verified=False,
                model_understanding_verified=False,
            )
            return (
                batch,
                current.execution.request.evidence_requirements,
                current.execution.request.execution_budget.max_tool_calls,
            )

        def scoped_read(session):
            with recipe_original_read_scope(session):
                return read(session)

        prepared = prepare_originals(
            self.database,
            scoped_read,
            link_activity_observations=True,
            link_tool_counter_observations=True,
        )
        if isinstance(prepared.value, BaselineDelivery):
            return prepared.value
        with self.database.session() as session:
            begin_immediate(session)
            prepared.verify(session)
            writer_at = self.clock()
            _owner(session, request.run_id, request.generation, writer_at)
            value, requirements, maximum = prepared.value
            # Availability is the real short writer time, never preparation start.
            coverage = assess_evidence_sufficiency(
                requirements,
                [item for result in value.results for item in result.evidence_candidates],
                facts=[item for result in value.results for item in (result.facts or [])],
                cutoff_at=writer_at,
                assessed_at=writer_at,
            )
            value = value.model_copy(
                update={
                    "recorded_at": writer_at,
                    "coverage": coverage,
                    "status": "ready" if coverage.status == "sufficient" else "hard_gap",
                    "remaining_tool_calls": _remaining_at(
                        session, request.run_id, maximum, writer_at
                    ),
                }
            )
            row = _insert(session, value, BATCH_ROLE, writer_at)
            prepared.verify_after_writes(session)
            return BaselineDelivery.model_validate_json(
                canonical_recipe(
                    {
                        **value.model_dump(mode="json"),
                        "schema_version": "research-baseline-delivery.v1",
                        "batch_ref": _ref(row).model_dump(mode="json"),
                    }
                )
            )
