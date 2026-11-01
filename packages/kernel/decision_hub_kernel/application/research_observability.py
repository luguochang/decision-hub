from __future__ import annotations

import hashlib
import json
import sqlite3
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from threading import Lock, get_ident
from typing import Literal

from pydantic import ValidationError
from sqlalchemy import event, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    ErrorProvenance,
    ResearchCapabilityResult,
    ResearchRunCommand,
    ResearchRunCommandReceipt,
    ResearchRunCommandResult,
    ResearchSessionResult,
    ResearchTraceEvent,
)
from packages.kernel.decision_hub_kernel.application.research_binding import (
    has_prepared_bundle_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    FrozenExecutionPreparation,
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    SCHEMA as LEGACY_METHOD_SCHEMA,
)
from packages.kernel.decision_hub_kernel.application.research_run_scope import (
    SpotAdmissionResolver,
    freeze_run_spot_admission_in_session,
    require_run_research_scope_in_session,
)
from packages.kernel.decision_hub_kernel.application.study import (
    bind_run_study,
    research_profile_for_study,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    ProductCommandRecord,
    ResearchCommandRecord,
    ResearchResultRecord,
    ResearchToolCallReservationRecord,
    ResearchTraceRecord,
    RunRecord,
    RunRoleOutputRecord,
    StudyRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import (
    PreparedOriginals, begin_immediate, prepare_originals,
)
from packages.kernel.decision_hub_kernel.persistence.callback_source_lease import CallbackSourceLease


@dataclass(frozen=True)
class OwnedToolReservation:
    """Exact immutable identity; ownership is reported only after own COMMIT."""

    run_id: str
    request_id: str
    research_session_id: str
    generation: int
    capability_id: str
    query_hash: str


@dataclass(frozen=True)
class ToolCallReservation:
    outcome: Literal[
        "reserved",
        "completed",
        "failed",
        "running",
        "cancelled",
        "exhausted",
        "conflict",
        "denied",
    ]
    tool_calls_started: int
    max_tool_calls: int | None
    result: ResearchCapabilityResult | None = None
    error: ErrorProvenance | None = None


@dataclass(frozen=True)
class CancellationCutoff:
    """Original accepted owner command, or an explicitly unknown legacy cutoff."""

    request_id: str | None
    created_at: datetime | None


ToolAdmissionCheck = Callable[[Session, FrozenExecutionPreparation], str]


@dataclass(frozen=True)
class ToolAdmissionPreparation:
    run_id: str
    request_id: str
    research_session_id: str
    generation: int
    capability_id: str
    query_hash: str
    prepared: FrozenExecutionPreparation
    scope: object
    scope_binding: str
    lease_owner: str | None


def _admission_payload(candidate: ToolAdmissionPreparation) -> str:
    # Guard the nested DTOs too: frozen dataclasses alone do not freeze models.
    return json.dumps({
        "identity": [candidate.run_id, candidate.request_id,
                     candidate.research_session_id, candidate.generation,
                     candidate.capability_id, candidate.query_hash, candidate.lease_owner],
        "prepared": {key: getattr(candidate.prepared, key).model_dump(mode="json")
                     for key in ("method", "link", "prompt", "execution", "integrity")},
        "scope_binding": candidate.scope_binding,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def requires_tool_admission_proof(session: Session, run_id: str) -> bool:
    """Recognized V2 rows/sidecars or unknown Method cannot enter legacy admission."""
    if has_prepared_bundle_in_session(session, run_id):
        return True
    rows = session.query(RunRoleOutputRecord).filter_by(run_id=run_id).populate_existing().all()
    for row in rows:
        if row.role == "research_execution_method" and row.schema_version != LEGACY_METHOD_SCHEMA:
            return True
        suffix = row.role.removeprefix("context_execution_link:")
        if (
            row.role.startswith("context_execution_link:")
            and suffix.isascii()
            and suffix.isdecimal()
            and not suffix.startswith("0")
        ):
            return True
        for prefix, legacy_schema in (
            ("dsh_execution_request:", "dsh-execution-request.v1"),
            ("dsh_prompt_integrity:", "dsh-prompt-integrity.v1"),
        ):
            suffix = row.role.removeprefix(prefix)
            if (
                row.role.startswith(prefix)
                and suffix.isdecimal()
                and int(suffix) >= 1
                and row.schema_version != legacy_schema
            ):
                return True
    return False


class ResearchObservabilityService:
    """Persist only canonical product results and normalized trace events."""

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.database = database
        self.clock = clock
        # Capability calls can complete concurrently inside the single MCP
        # process. Serialize the read/allocate/write sequence so per-run
        # trace sequence numbers remain monotonic under parallel calls.
        self._append_lock = Lock()
        self._tool_admission_issues = {}

    def prepare_tool_admission(
        self, *, run_id: str, request_id: str, research_session_id: str,
        generation: int, capability_id: str, prepare_scope, checkpoint=None,
        source_lease=None,
    ) -> PreparedOriginals[ToolAdmissionPreparation]:
        """Issue one complete RO authentication, on its eventual writer's thread."""
        if source_lease is not None and type(source_lease) is not CallbackSourceLease:
            raise ValueError("research_tool_admission_source_lease_invalid")

        def read(session):
            prepared = read_prepared_execution_in_session(session, run_id)
            if (prepared.link.run_id, prepared.link.dsh_session_id,
                prepared.link.generation) != (run_id, research_session_id, generation):
                raise ValueError("research_tool_admission_binding_changed")
            scope, binding, query_hash = prepare_scope(session, prepared)
            run = session.get(RunRecord, run_id, populate_existing=True)
            if run is None:
                raise ValueError("research_tool_admission_run_missing")
            if source_lease is not None:
                source_lease.extend_authenticated_selection(session, run_id)
            return ToolAdmissionPreparation(
                run_id, request_id, research_session_id, generation, capability_id,
                query_hash, prepared, scope, binding, run.lease_owner,
            )

        originals = prepare_originals(
            self.database, read, checkpoint=checkpoint, source_lease=source_lease,
            link_activity_observations=True, link_tool_counter_observations=True,
        )
        if source_lease is not None:
            source_lease.seal_authenticated_selection(run_id)
        key = id(originals)
        candidate = originals.value
        reference = weakref.ref(originals, lambda _ref: self._tool_admission_issues.pop(key, None))
        self._tool_admission_issues[key] = (
            reference, candidate, candidate.prepared, candidate.scope,
            _admission_payload(candidate), get_ident(),
        )
        return originals

    def _require_tool_admission(self, session, originals, identity):
        if type(originals) is not PreparedOriginals:
            raise ValueError("research_tool_admission_unissued")
        originals._require_issued(session)
        issued = self._tool_admission_issues.get(id(originals))
        candidate = originals.value
        if (issued is None or issued[0]() is not originals
                or type(candidate) is not ToolAdmissionPreparation
                or issued[1] is not candidate or issued[2] is not candidate.prepared
                or issued[3] is not candidate.scope or issued[5] != get_ident()
                or issued[4] != _admission_payload(candidate)
                or (candidate.run_id, candidate.request_id, candidate.research_session_id,
                    candidate.generation, candidate.capability_id, candidate.query_hash) != identity):
            raise ValueError("research_tool_admission_unissued")
        return candidate

    def _fresh_tool_admission(self, session, candidate, checkpoint):
        checkpoint()
        run = session.get(RunRecord, candidate.run_id, populate_existing=True)
        link = session.get(DshSessionLinkRecord, candidate.run_id, populate_existing=True)
        now = as_utc(self.clock())
        original = candidate.prepared.link
        if (run is None or link is None or run.artifact_id is not None
                or (candidate.lease_owner is not None and (
                    run.status != "running" or run.lease_owner != candidate.lease_owner
                    or as_utc(run.lease_expires_at) is None
                    or as_utc(run.lease_expires_at) <= now))
                or (candidate.lease_owner is None and (
                    run.status != "admitted" or run.lease_owner is not None))
                or link.state in {"completed", "failed", "cancelled"}
                or (link.dsh_session_id, link.generation, link.request_hash,
                    link.plugin_build_hash, as_utc(link.deadline_at)) != (
                    original.dsh_session_id, original.generation, original.request_hash,
                    original.upstream_identity.plugin_build_hash, as_utc(original.deadline_at))
                or as_utc(link.deadline_at) is None or now >= as_utc(link.deadline_at)
                or link.max_tool_calls is None or link.tool_calls_started > link.max_tool_calls):
            raise ValueError("research_tool_admission_permission_lost")
        checkpoint()

    async def emit(self, event: ResearchTraceEvent) -> None:
        self.append_trace(event)

    def tool_calls_started(self, run_id: str) -> int | None:
        """Return the durable audited call count for a research Run."""

        with self.database.session() as session:
            link = session.get(DshSessionLinkRecord, run_id)
            return None if link is None else link.tool_calls_started

    def reserve_tool_call(
        self,
        *,
        run_id: str,
        request_id: str,
        research_session_id: str,
        generation: int,
        capability_id: str,
        query_hash: str,
        admission_check: ToolAdmissionCheck | None = None,
        checkpoint: Callable[[], None] | None = None,
        on_new_reservation: Callable[[OwnedToolReservation], None] | None = None,
        admission_originals: PreparedOriginals[ToolAdmissionPreparation] | None = None,
    ) -> ToolCallReservation:
        """Own the complete admission writer; notify new ownership after COMMIT.

        Cancellation racing COMMIT is drained by the caller and audited against
        this exact identity. A failed/rolled-back insert or an idempotent existing
        row never grants cleanup ownership.
        """
        check = checkpoint or (lambda: None)

        def attempt(*, require_existing: bool = False):
            created: list[OwnedToolReservation] = []
            check()
            with self.database.session() as session:
                if session.bind is not None and session.bind.dialect.name == "sqlite":
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                check()
                if admission_originals is not None:
                    self._require_tool_admission(session, admission_originals, (
                        run_id, request_id, research_session_id, generation, capability_id, query_hash,
                    ))
                if require_existing and session.get(
                    ResearchToolCallReservationRecord, (run_id, request_id),
                    populate_existing=True,
                ) is None:
                    raise ValueError("research_tool_integrity_retry_original_missing")
                value = self.reserve_tool_call_in_session(
                    session, run_id=run_id, request_id=request_id,
                    research_session_id=research_session_id, generation=generation,
                    capability_id=capability_id, query_hash=query_hash,
                    admission_check=admission_check, checkpoint=check,
                    _on_new_insert=created.append,
                    _admission_originals=admission_originals,
                )
                check()
            # Session/transaction has exited successfully on this SAME thread.
            # Do not checkpoint between COMMIT and delivering its exact ownership.
            if created and on_new_reservation is not None:
                on_new_reservation(created[0])
            return value

        try:
            return attempt()
        except IntegrityError:
            # Lost insert rolled back, so it cannot cancel the other producer.
            return attempt(require_existing=True)

    def reserve_tool_call_in_session(
        self, session: Session, *, run_id: str, request_id: str,
        research_session_id: str, generation: int, capability_id: str, query_hash: str,
        admission_check: ToolAdmissionCheck | None = None,
        checkpoint: Callable[[], None] | None = None,
        _on_new_insert: Callable[[OwnedToolReservation], None] | None = None,
        _admission_originals: PreparedOriginals[ToolAdmissionPreparation] | None = None,
    ) -> ToolCallReservation:
        check = checkpoint or (lambda: None)
        candidate = None
        if _admission_originals is not None:
            candidate = self._require_tool_admission(session, _admission_originals, (
                run_id, request_id, research_session_id, generation, capability_id, query_hash,
            ))
            _admission_originals.verify(session, checkpoint=check)
            self._fresh_tool_admission(session, candidate, check)
        value = self._reserve_tool_call_in_session(
            session, run_id=run_id, request_id=request_id,
            research_session_id=research_session_id, generation=generation,
            capability_id=capability_id, query_hash=query_hash,
            admission_check=admission_check, checkpoint=check,
            _on_new_insert=_on_new_insert, _admission_originals=_admission_originals,
        )
        if candidate is not None:
            _admission_originals.verify_after_writes(session, checkpoint=check)
            self._fresh_tool_admission(session, candidate, check)
            # Actual COMMIT remains fenced even for a borrowed writer.
            event.listen(session, "before_commit", lambda current:
                self._fresh_tool_admission(current, candidate, check), once=True)
        return value

    def _reserve_tool_call_in_session(
        self,
        session: Session,
        *,
        run_id: str,
        request_id: str,
        research_session_id: str,
        generation: int,
        capability_id: str,
        query_hash: str,
        admission_check: ToolAdmissionCheck | None = None,
        checkpoint: Callable[[], None] | None = None,
        _on_new_insert: Callable[[OwnedToolReservation], None] | None = None,
        _admission_originals: PreparedOriginals[ToolAdmissionPreparation] | None = None,
    ) -> ToolCallReservation:
        """Borrow the actual writer; never begin, commit or close its Session."""
        check = checkpoint or (lambda: None)
        check()
        if not session.in_transaction():
            raise ValueError("research_tool_transaction_required")
        connection = session.connection()
        if connection.dialect.name == "sqlite":
            driver = connection.connection.driver_connection
            if not isinstance(driver, sqlite3.Connection) or not driver.in_transaction:
                raise ValueError("research_tool_transaction_required")
        run = session.get(RunRecord, run_id, populate_existing=True, with_for_update=True)
        link = session.get(
            DshSessionLinkRecord, run_id, populate_existing=True, with_for_update=True
        )
        if link is None:
            raise KeyError(run_id)
        error_code = (
            "research_run_not_found"
            if run is None
            else "research_session_binding_mismatch"
            if link.dsh_session_id != research_session_id or link.generation != generation
            else None
        )
        if error_code is not None:
            return _denied_tool_reservation(
                link, error_code, capability_id=capability_id, request_id=request_id
            )
        assert run is not None
        existing = session.get(
            ResearchToolCallReservationRecord, (run_id, request_id), populate_existing=True
        )
        v2 = requires_tool_admission_proof(session, run_id)
        prepared = None
        if v2:
            if admission_check is None:
                return _denied_tool_reservation(
                    link,
                    "research_tool_v2_admission_required",
                    capability_id=capability_id,
                    request_id=request_id,
                )
            if _admission_originals is None:
                prepared = read_prepared_execution_in_session(session, run_id)
            else:
                candidate = self._require_tool_admission(session, _admission_originals, (
                    run_id, request_id, research_session_id, generation, capability_id, query_hash,
                ))
                prepared = candidate.prepared
            if capability_id not in prepared.execution.request.allowed_capabilities:
                return _denied_tool_reservation(
                    link,
                    "research_capability_not_allowed_for_run",
                    capability_id=capability_id,
                    request_id=request_id,
                )
        if existing is None:
            error_code = (
                "research_run_terminal"
                if run.status
                in {"completed", "degraded", "failed", "cancelled", "research_only", "rejected"}
                else "research_session_terminal"
                if link.state in {"completed", "failed", "cancelled"}
                else "research_run_not_active"
                if v2 and run.status not in {"admitted", "running"}
                else None
            )
            if error_code is not None:
                return _denied_tool_reservation(
                    link, error_code, capability_id=capability_id, request_id=request_id
                )
            generation_count = (
                session.scalar(
                    session.query(func.count(ResearchToolCallReservationRecord.request_id))
                    .filter_by(run_id=run_id, generation=generation)
                    .statement
                )
                or 0
            )
            if (
                link.max_tool_calls is None
                or link.tool_calls_started >= link.max_tool_calls
                or (
                    prepared is not None
                    and generation_count
                    >= prepared.execution.request.execution_budget.max_tool_calls
                )
            ):
                return ToolCallReservation(
                    outcome="exhausted",
                    tool_calls_started=link.tool_calls_started,
                    max_tool_calls=link.max_tool_calls,
                )
        if prepared is not None:
            assert admission_check is not None
            query_hash = admission_check(session, prepared)
        check()
        if existing is not None:
            conflict = (
                existing.query_hash != query_hash
                or existing.research_session_id != research_session_id
                or existing.generation != generation
                or existing.capability_id != capability_id
            )
            return _tool_reservation_view(existing, session, conflict=conflict)
        updated = session.execute(
            update(DshSessionLinkRecord)
            .where(
                DshSessionLinkRecord.run_id == run_id,
                DshSessionLinkRecord.max_tool_calls.is_not(None),
                DshSessionLinkRecord.tool_calls_started < DshSessionLinkRecord.max_tool_calls,
            )
            .values(
                tool_calls_started=DshSessionLinkRecord.tool_calls_started + 1,
                updated_at=self.clock(),
            )
        )
        if int(getattr(updated, "rowcount", 0)) != 1:
            return ToolCallReservation(
                outcome="exhausted",
                tool_calls_started=link.tool_calls_started,
                max_tool_calls=link.max_tool_calls,
            )
        session.add(
            ResearchToolCallReservationRecord(
                run_id=run_id,
                request_id=request_id,
                research_session_id=research_session_id,
                generation=generation,
                capability_id=capability_id,
                query_hash=query_hash,
                status="running",
                result_json=None,
                error_json=None,
                reserved_at=self.clock(),
                completed_at=None,
            )
        )
        session.flush()
        if _on_new_insert is not None:
            _on_new_insert(OwnedToolReservation(
                run_id, request_id, research_session_id, generation, capability_id, query_hash,
            ))
        check()
        link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
        assert link is not None
        return ToolCallReservation(
            outcome="reserved",
            tool_calls_started=link.tool_calls_started,
            max_tool_calls=link.max_tool_calls,
        )

    def complete_tool_call(
        self,
        *,
        run_id: str,
        request_id: str,
        result: ResearchCapabilityResult,
    ) -> None:
        with self.database.session() as session:
            self.complete_tool_call_in_session(
                session,
                run_id=run_id,
                request_id=request_id,
                result=result,
            )

    def complete_tool_call_in_session(
        self,
        session: Session,
        *,
        run_id: str,
        request_id: str,
        result: ResearchCapabilityResult,
        semantic_error: ErrorProvenance | None = None,
    ) -> None:
        """Commit the actual result together with evidence and cutoff audit."""
        payload = _canonical_json(result.model_dump(mode="json"))
        error_payload = (
            _canonical_json(semantic_error.model_dump(mode="json"))
            if semantic_error is not None
            else None
        )
        record = _required_tool_reservation(session, run_id, request_id)
        if record.status == "completed":
            if record.result_json != payload or record.error_json != error_payload:
                raise ValueError("research_tool_result_conflict")
            return
        if record.status != "running":
            raise ValueError("research_tool_terminal_conflict")
        record.status = "completed"
        record.result_json = payload
        record.error_json = error_payload
        record.completed_at = self.clock()
        session.flush()

    def cancellation_cutoff_in_session(
        self,
        session: Session,
        run_id: str,
    ) -> CancellationCutoff | None:
        """Never infer the original cancellation time from mutable Run fields."""
        return ResearchCommandService.cancellation_cutoff_in_session(session, run_id)

    def fail_tool_call(
        self,
        *,
        run_id: str,
        request_id: str,
        error: ErrorProvenance,
    ) -> None:
        payload = _canonical_json(error.model_dump(mode="json"))
        with self.database.session() as session:
            record = _required_tool_reservation(session, run_id, request_id)
            if record.status == "failed":
                if record.error_json != payload:
                    raise ValueError("research_tool_error_conflict")
                return
            if record.status != "running":
                raise ValueError("research_tool_terminal_conflict")
            record.status = "failed"
            record.result_json = None
            record.error_json = payload
            record.completed_at = self.clock()
            session.flush()

    def cancel_tool_call(self, *, run_id: str, request_id: str) -> None:
        with self.database.session() as session:
            record = _required_tool_reservation(session, run_id, request_id)
            if record.status == "cancelled":
                return
            if record.status != "running":
                raise ValueError("research_tool_terminal_conflict")
            record.status = "cancelled"
            record.completed_at = self.clock()
            session.flush()

    def cancel_owned_tool_call(self, identity: OwnedToolReservation) -> None:
        """Cancel only our committed, never-executed reservation and audit atomically."""
        with self.database.session() as session:
            begin_immediate(session)
            record = session.get(ResearchToolCallReservationRecord,
                                 (identity.run_id, identity.request_id), populate_existing=True)
            if record is None:
                # Ownership is granted only after our successful COMMIT. A
                # missing original cannot certify cancellation or its audit.
                raise ValueError("research_tool_owned_original_missing")
            if (record.run_id, record.request_id, record.research_session_id,
                record.generation, record.capability_id, record.query_hash) != (
                identity.run_id, identity.request_id, identity.research_session_id,
                identity.generation, identity.capability_id, identity.query_hash,
            ):
                raise ValueError("research_tool_owned_identity_changed")
            if record.status == "running":
                record.status = "cancelled"
                record.completed_at = self.clock()
                session.flush()
            elif record.status != "cancelled":
                raise ValueError("research_tool_owned_terminal_conflict")
            self.append_trace_in_session(session, ResearchTraceEvent(
                schema_version="research-trace-event.v1", run_id=identity.run_id,
                research_session_id=identity.research_session_id, sequence_no=0,
                event_type="tool_failed", occurred_at=as_utc(record.completed_at),
                stage="acquiring_evidence", status="denied",
                summary="Owned admission stopped before adapter entry; reservation and consumed budget retained.",
                reference_type="capability_call",
                reference_id=identity.request_id + ":admission-stopped",
                error_code="research_tool_admission_stopped",
            ), once=True)

    def append_trace(self, event: ResearchTraceEvent) -> ResearchTraceEvent:
        with self._append_lock:
            return self._append_trace_locked(event)

    def append_trace_once(self, event: ResearchTraceEvent) -> ResearchTraceEvent:
        """Append a capability trace once by its Run/event/reference namespace."""

        with self._append_lock:
            return self._append_trace_locked(event, once=True)

    def _append_trace_locked(
        self,
        event: ResearchTraceEvent,
        *,
        once: bool = False,
    ) -> ResearchTraceEvent:
        with self.database.session() as session:
            if session.bind is not None and session.bind.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            return self.append_trace_in_session(session, event, once=once)

    def append_trace_in_session(
        self,
        session: Session,
        event: ResearchTraceEvent,
        *,
        once: bool = False,
    ) -> ResearchTraceEvent:
        """Append inside a caller's already serialized Run write transaction.

        Do not acquire the process-local append lock here: another appender may
        hold it while waiting for this transaction's SQLite write lock.
        """
        if event.error_verification is not None:
            raise ValueError("research_trace_read_projection_not_writable")
        semantic = event.model_dump(mode="json", exclude={"sequence_no", "error_verification"})
        event_hash = _payload_hash(semantic)
        run = session.get(RunRecord, event.run_id)
        if run is None:
            raise KeyError(event.run_id)
        if (
            once
            and event.reference_id is not None
            and event.event_type
            in {
                "tool_started",
                "tool_completed",
                "tool_failed",
            }
        ):
            existing = (
                session.query(ResearchTraceRecord)
                .filter_by(
                    run_id=event.run_id,
                    event_type=event.event_type,
                    reference_type=event.reference_type,
                    reference_id=event.reference_id,
                )
                .order_by(ResearchTraceRecord.sequence_no.asc())
                .first()
            )
            if existing is not None:
                # Existing duplicates remain immutable; replay selects the
                # earliest original row without changing its sequence or hash.
                return _trace_view(existing)
        existing = (
            session.query(ResearchTraceRecord)
            .filter_by(run_id=event.run_id, event_hash=event_hash)
            .first()
        )
        if existing is not None:
            return _trace_view(existing)
        sequence_no = (
            session.query(func.max(ResearchTraceRecord.sequence_no))
            .filter_by(run_id=event.run_id)
            .scalar()
            or 0
        ) + 1
        record = ResearchTraceRecord(
            run_id=event.run_id,
            research_session_id=event.research_session_id,
            sequence_no=sequence_no,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            stage=event.stage,
            summary=event.summary,
            reference_type=event.reference_type,
            reference_id=event.reference_id,
            status=event.status,
            error_code=event.error_code,
            error_provenance_json=(
                _canonical_json(event.error.model_dump(mode="json"))
                if event.error is not None
                else None
            ),
            event_hash=event_hash,
            created_at=self.clock(),
        )
        session.add(record)
        session.flush()
        return _trace_view(record)

    def list_trace(self, run_id: str, *, after: int = 0) -> list[ResearchTraceEvent]:
        if after < 0:
            raise ValueError("research_trace_after_invalid")
        with self.database.session() as session:
            rows = (
                session.query(ResearchTraceRecord)
                .filter(
                    ResearchTraceRecord.run_id == run_id,
                    ResearchTraceRecord.sequence_no > after,
                )
                .order_by(ResearchTraceRecord.sequence_no.asc())
                .all()
            )
            return [_trace_view(row) for row in rows]

    def get_result(self, run_id: str) -> ResearchSessionResult | None:
        with self.database.session() as session:
            row = session.get(ResearchResultRecord, run_id)
            return _result_view(row) if row is not None else None

    def save_result_in_session(
        self,
        session: Session,
        run_id: str,
        result: ResearchSessionResult,
    ) -> ResearchSessionResult:
        payload = result.model_dump(mode="json")
        payload_hash = _payload_hash(payload)
        existing = session.get(ResearchResultRecord, run_id)
        if existing is not None:
            if existing.payload_hash != payload_hash:
                raise ValueError("research_result_conflict")
            return _result_view(existing)
        session.add(
            ResearchResultRecord(
                run_id=run_id,
                schema_version=result.schema_version,
                payload_json=_canonical_json(payload),
                payload_hash=payload_hash,
                created_at=self.clock(),
            )
        )
        return result


class ResearchCommandService:
    """Apply owner commands without mutating historical research artifacts."""

    TERMINAL = {"completed", "degraded", "failed", "cancelled"}
    KIND = "research_command"
    LEGACY_INTERNAL_OWNER = "legacy-research-command-internal"

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = utcnow,
        spot_admission_resolver: SpotAdmissionResolver | None = None,
    ) -> None:
        self.database = database
        self.clock = clock
        self.spot_admission_resolver = spot_admission_resolver

    def apply(
        self,
        run_id: str,
        command: ResearchRunCommand,
        *,
        owner_id: str = LEGACY_INTERNAL_OWNER,
    ) -> ResearchRunCommandResult:
        if not 1 <= len(owner_id) <= 128:
            raise ValueError("research_command_owner_invalid")
        if not 1 <= len(command.request_id) <= 128:
            raise ValueError("research_command_request_id_invalid")
        digest = _command_payload_hash(owner_id, run_id, command)
        with self.database.session() as session:
            # Reserve the SQLite write transaction before reading Run state.
            # Cancellation and Commit's conditional publication UPDATE then
            # linearize on the same write lock, including concurrent replays.
            begin_immediate(session)
            existing = session.get(ProductCommandRecord, command.request_id)
            if existing is not None:
                if (
                    existing.owner_id != owner_id
                    or existing.target_id != run_id
                    or existing.kind != self.KIND
                    or existing.payload_hash != digest
                ):
                    raise ValueError("research_command_request_reused")
                receipt = self._receipt_view(existing)
                if receipt.command != command:
                    raise ValueError("research_command_receipt_invalid")
                return receipt.result.model_copy(
                    update={
                        "status": (
                            "already_applied" if receipt.result.status == "accepted" else "rejected"
                        )
                    }
                )
            if session.get(ResearchCommandRecord, command.request_id) is not None:
                # Old rows contain no owner binding. Neither a replay nor a GET
                # may invent consent/ownership from a newly supplied header.
                raise ValueError("research_command_legacy_owner_unknown")

            run = session.get(RunRecord, run_id)
            if run is None or run.strategy_version != "research.v1":
                raise KeyError(run_id)
            now = self.clock()
            target_run_id: str | None = None
            result_status = "accepted"
            if command.command == "cancel":
                if run.status in self.TERMINAL and run.status != "cancelled":
                    result_status = "rejected"
                else:
                    run.status = "cancelled"
                    run.error_code = "cancelled_by_owner"
                    run.updated_at = now
                    run.finished_at = now
                    run.lease_owner = None
                    run.lease_expires_at = None
            elif command.command in {"retry", "recheck"}:
                allowed = (
                    run.status in {"failed", "cancelled", "degraded"}
                    if command.command == "retry"
                    else run.status in {"completed", "degraded"}
                )
                if not allowed:
                    result_status = "rejected"
                else:
                    study_id = bind_run_study(session, run, now, legacy=not run.study_id)
                    study = session.get(StudyRecord, study_id)
                    if research_profile_for_study(study) is not None:
                        # This profile is reissued only from its signed saved-settings
                        # command. A generic child would lose its exact admission identity.
                        result_status = "rejected"
                    else:
                        target_run_id = _command_child_run_id(command.request_id)
                        current_spot = None
                        if study.market == "crypto" and study.instrument in {"BTC-USDT", "ETH-USDT"}:
                            require_run_research_scope_in_session(session, run)
                            if self.spot_admission_resolver is None:
                                raise ValueError("spot_current_admission_not_prepared")
                            current_spot = self.spot_admission_resolver(session, study, now)
                        child = RunRecord(
                            run_id=target_run_id,
                            event_id=run.event_id,
                            study_id=study_id,
                            idempotency_key=f"research-command:{command.request_id}",
                            status="admitted",
                            strategy_version="research.v1",
                            runtime_version="pending",
                            admission_origin="manual",
                            priority=100,
                            available_at=now,
                            parent_run_id=run_id,
                            created_at=now,
                            updated_at=now,
                        )
                        session.add(child)
                        if current_spot is not None:
                            session.flush()
                            freeze_run_spot_admission_in_session(
                                session,
                                run=child,
                                study=study,
                                current=current_spot,
                            )
            record = ResearchCommandRecord(
                request_id=command.request_id,
                run_id=run_id,
                command=command.command,
                reason=command.reason,
                target_run_id=target_run_id,
                status=result_status,
                created_at=now,
            )
            session.add(record)
            result = _command_view(record, status=result_status)
            receipt = ResearchRunCommandReceipt(
                schema_version="research-run-command-receipt.v1",
                request_id=command.request_id,
                owner_id=owner_id,
                source_run_id=run_id,
                command=command,
                result=result,
            )
            session.add(
                ProductCommandRecord(
                    request_id=command.request_id,
                    owner_id=owner_id,
                    target_id=run_id,
                    kind=self.KIND,
                    payload_hash=digest,
                    result_json=receipt.model_dump_json(),
                    created_at=now,
                )
            )
            session.flush()
            return result

    def receipt(self, run_id: str, request_id: str, *, owner_id: str) -> ResearchRunCommandReceipt:
        with self.database.session() as session:
            row = session.get(ProductCommandRecord, request_id)
            if (
                row is None
                or row.owner_id != owner_id
                or row.target_id != run_id
                or row.kind != self.KIND
            ):
                raise KeyError("research_command_receipt_not_found")
            return self._receipt_view(row)

    @staticmethod
    def cancellation_cutoff_in_session(
        session: Session,
        run_id: str,
    ) -> CancellationCutoff | None:
        """Resolve an original accepted cancellation without inventing legacy consent."""
        run = session.get(RunRecord, run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status != "cancelled":
            return None
        command = (
            session.query(ResearchCommandRecord)
            .filter_by(run_id=run_id, command="cancel", status="accepted")
            .order_by(ResearchCommandRecord.created_at, ResearchCommandRecord.request_id)
            .first()
        )
        if command is not None:
            product = session.get(ProductCommandRecord, command.request_id)
            if product is None or product.kind != ResearchCommandService.KIND:
                return CancellationCutoff(None, None)
            try:
                receipt = ResearchCommandService._receipt_view(product)
            except (ValueError, TypeError, KeyError):
                return CancellationCutoff(None, None)
            if (
                receipt.source_run_id == run_id
                and receipt.command.command == "cancel"
                and receipt.command.reason == command.reason
                and receipt.result.status == "accepted"
                and receipt.result.created_at == as_utc(command.created_at)
            ):
                return CancellationCutoff(receipt.request_id, receipt.result.created_at)
        return CancellationCutoff(None, None)

    @staticmethod
    def _receipt_view(row: ProductCommandRecord) -> ResearchRunCommandReceipt:
        receipt = ResearchRunCommandReceipt.model_validate_json(row.result_json)
        expected_target = (
            _command_child_run_id(row.request_id)
            if (
                receipt.result.status == "accepted"
                and receipt.command.command in {"retry", "recheck"}
            )
            else None
        )
        if (
            receipt.request_id != row.request_id
            or receipt.owner_id != row.owner_id
            or receipt.source_run_id != row.target_id
            or receipt.command.request_id != row.request_id
            or receipt.result.request_id != row.request_id
            or receipt.result.command != receipt.command.command
            or receipt.result.source_run_id != row.target_id
            or receipt.result.status not in {"accepted", "rejected"}
            or receipt.result.target_run_id != expected_target
            or receipt.result.created_at != as_utc(row.created_at)
            or _command_payload_hash(receipt.owner_id, receipt.source_run_id, receipt.command)
            != row.payload_hash
        ):
            raise ValueError("research_command_receipt_invalid")
        return receipt


def _trace_view(record: ResearchTraceRecord) -> ResearchTraceEvent:
    return read_trace_projection(record)


def read_trace_projection(record: ResearchTraceRecord) -> ResearchTraceEvent:
    """Read one original Trace, limiting only the explicit historical null array.

    The temporary default array validates other fields; it is never returned as
    evidence of zero attempts. This projection cannot be appended as a new Trace.
    """
    raw_error = json.loads(record.error_provenance_json) if record.error_provenance_json else None
    payload = {
        "schema_version": "research-trace-event.v1",
        "run_id": record.run_id,
        "research_session_id": record.research_session_id,
        "sequence_no": record.sequence_no,
        "event_type": record.event_type,
        "occurred_at": as_utc(record.occurred_at),
        "stage": record.stage,
        "summary": record.summary,
        "reference_type": record.reference_type,
        "reference_id": record.reference_id,
        "status": record.status,
        "error_code": record.error_code,
        "error": raw_error,
    }
    try:
        return ResearchTraceEvent.model_validate(payload)
    except ValidationError:
        if not (
            isinstance(raw_error, dict)
            and "provider_attempts" in raw_error
            and raw_error["provider_attempts"] is None
        ):
            raise
    other_fields = {name: value for name, value in raw_error.items() if name != "provider_attempts"}
    other_error = ErrorProvenance.model_validate(other_fields)
    projected = ResearchTraceEvent.model_validate(payload | {"error": None})
    original_semantic = projected.model_dump(
        mode="json", exclude={"sequence_no", "error_verification"}
    )
    original_semantic["error"] = raw_error
    if (
        other_error.error_code != record.error_code
        or _payload_hash(original_semantic) != record.event_hash
    ):
        raise ValueError("research_trace_legacy_provenance_binding_invalid")
    return projected.model_copy(
        update={
            "error_verification": "legacy_provider_attempts_unverifiable",
        }
    )


def _required_tool_reservation(
    session: Session, run_id: str, request_id: str
) -> ResearchToolCallReservationRecord:
    record = session.get(
        ResearchToolCallReservationRecord, (run_id, request_id), populate_existing=True
    )
    if record is None:
        raise KeyError(f"{run_id}:{request_id}")
    return record


def _denied_tool_reservation(
    link: DshSessionLinkRecord,
    error_code: str,
    *,
    capability_id: str,
    request_id: str,
) -> ToolCallReservation:
    return ToolCallReservation(
        outcome="denied",
        tool_calls_started=link.tool_calls_started,
        max_tool_calls=link.max_tool_calls,
        error=ErrorProvenance(
            error_code=error_code,
            origin="gateway",
            cause_code="atomic_tool_admission",
            capability_id=capability_id,
            tool_call_id=request_id,
            retryable=False,
            deadline_ms=None,
        ),
    )


def _tool_reservation_view(
    record: ResearchToolCallReservationRecord, session: Session, *, conflict: bool = False
) -> ToolCallReservation:
    link = session.get(DshSessionLinkRecord, record.run_id, populate_existing=True)
    if link is None:
        raise KeyError(record.run_id)
    result = (
        ResearchCapabilityResult.model_validate_json(record.result_json)
        if record.result_json is not None
        else None
    )
    error = (
        ErrorProvenance.model_validate_json(record.error_json)
        if record.error_json is not None
        else None
    )
    return ToolCallReservation(
        outcome="conflict" if conflict else record.status,  # type: ignore[arg-type]
        tool_calls_started=link.tool_calls_started,
        max_tool_calls=link.max_tool_calls,
        result=result,
        error=error,
    )


def _result_view(record: ResearchResultRecord) -> ResearchSessionResult:
    return ResearchSessionResult.model_validate_json(record.payload_json)


def _command_view(
    record: ResearchCommandRecord,
    *,
    status: str,
) -> ResearchRunCommandResult:
    return ResearchRunCommandResult.model_validate(
        {
            "schema_version": "research-run-command-result.v1",
            "request_id": record.request_id,
            "command": record.command,
            "source_run_id": record.run_id,
            "target_run_id": record.target_run_id,
            "status": status,
            "created_at": as_utc(record.created_at),
        }
    )


def _command_child_run_id(request_id: str) -> str:
    return f"run_{hashlib.sha256(request_id.encode('utf-8')).hexdigest()[:32]}"


def _command_payload_hash(owner_id: str, run_id: str, command: ResearchRunCommand) -> str:
    return _payload_hash([owner_id, run_id, command.model_dump(mode="json")])


def _payload_hash(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
