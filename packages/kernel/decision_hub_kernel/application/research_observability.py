from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Literal

from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    ErrorProvenance,
    ResearchCapabilityResult,
    ResearchRunCommand,
    ResearchRunCommandResult,
    ResearchSessionResult,
    ResearchTraceEvent,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    ResearchCommandRecord,
    ResearchResultRecord,
    ResearchToolCallReservationRecord,
    ResearchTraceRecord,
    RunRecord,
    as_utc,
    utcnow,
)


@dataclass(frozen=True)
class ToolCallReservation:
    outcome: Literal[
        "reserved", "completed", "failed", "running", "cancelled", "exhausted"
    ]
    tool_calls_started: int
    max_tool_calls: int | None
    result: ResearchCapabilityResult | None = None
    error: ErrorProvenance | None = None


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

    async def emit(self, event: ResearchTraceEvent) -> None:
        self.append_trace(event)

    def reserve_tool_call(
        self,
        *,
        run_id: str,
        request_id: str,
        research_session_id: str,
        generation: int,
        capability_id: str,
    ) -> ToolCallReservation:
        """Atomically reserve one immutable Run-level tool slot.

        The conditional UPDATE is the cross-process concurrency boundary. A
        duplicate request races through the unique reservation key; its losing
        transaction rolls back the counter increment and then reads the winner.
        """

        try:
            with self.database.session() as session:
                existing = session.get(
                    ResearchToolCallReservationRecord, (run_id, request_id)
                )
                if existing is not None:
                    return _tool_reservation_view(existing, session)
                updated = session.execute(
                    update(DshSessionLinkRecord)
                    .where(
                        DshSessionLinkRecord.run_id == run_id,
                        DshSessionLinkRecord.max_tool_calls.is_not(None),
                        DshSessionLinkRecord.tool_calls_started
                        < DshSessionLinkRecord.max_tool_calls,
                    )
                    .values(
                        tool_calls_started=DshSessionLinkRecord.tool_calls_started + 1,
                        updated_at=self.clock(),
                    )
                )
                if int(getattr(updated, "rowcount", 0)) != 1:
                    link = session.get(DshSessionLinkRecord, run_id)
                    if link is None:
                        raise KeyError(run_id)
                    return ToolCallReservation(
                        outcome="exhausted",
                        tool_calls_started=link.tool_calls_started,
                        max_tool_calls=link.max_tool_calls,
                    )
                record = ResearchToolCallReservationRecord(
                    run_id=run_id,
                    request_id=request_id,
                    research_session_id=research_session_id,
                    generation=generation,
                    capability_id=capability_id,
                    status="running",
                    result_json=None,
                    error_json=None,
                    reserved_at=self.clock(),
                    completed_at=None,
                )
                session.add(record)
                session.flush()
                link = session.get(DshSessionLinkRecord, run_id)
                if link is None:  # pragma: no cover - protected by the UPDATE
                    raise KeyError(run_id)
                return ToolCallReservation(
                    outcome="reserved",
                    tool_calls_started=link.tool_calls_started,
                    max_tool_calls=link.max_tool_calls,
                )
        except IntegrityError:
            # Another process committed the same request_id first. The failed
            # transaction, including its counter increment, has been rolled back.
            with self.database.session() as session:
                existing = session.get(
                    ResearchToolCallReservationRecord, (run_id, request_id)
                )
                if existing is None:
                    raise
                return _tool_reservation_view(existing, session)

    def complete_tool_call(
        self,
        *,
        run_id: str,
        request_id: str,
        result: ResearchCapabilityResult,
    ) -> None:
        payload = _canonical_json(result.model_dump(mode="json"))
        with self.database.session() as session:
            record = _required_tool_reservation(session, run_id, request_id)
            if record.status == "completed":
                if record.result_json != payload:
                    raise ValueError("research_tool_result_conflict")
                return
            if record.status != "running":
                raise ValueError("research_tool_terminal_conflict")
            record.status = "completed"
            record.result_json = payload
            record.error_json = None
            record.completed_at = self.clock()
            session.flush()

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

    def append_trace(self, event: ResearchTraceEvent) -> ResearchTraceEvent:
        with self._append_lock:
            return self._append_trace_locked(event)

    def append_trace_once(self, event: ResearchTraceEvent) -> ResearchTraceEvent:
        """Append a capability trace once by its run/event/reference identity."""

        with self._append_lock:
            if event.reference_id is not None and event.event_type in {
                "tool_started",
                "tool_completed",
                "tool_failed",
            }:
                with self.database.session() as session:
                    existing = (
                        session.query(ResearchTraceRecord)
                        .filter_by(
                            run_id=event.run_id,
                            event_type=event.event_type,
                            reference_id=event.reference_id,
                        )
                        .first()
                    )
                    if existing is not None:
                        return _trace_view(existing)
            return self._append_trace_locked(event)

    def _append_trace_locked(self, event: ResearchTraceEvent) -> ResearchTraceEvent:
        semantic = event.model_dump(mode="json", exclude={"sequence_no"})
        event_hash = _payload_hash(semantic)
        with self.database.session() as session:
            run = session.get(RunRecord, event.run_id)
            if run is None:
                raise KeyError(event.run_id)
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

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.database = database
        self.clock = clock

    def apply(self, run_id: str, command: ResearchRunCommand) -> ResearchRunCommandResult:
        with self.database.session() as session:
            existing = session.get(ResearchCommandRecord, command.request_id)
            if existing is not None:
                if (
                    existing.run_id != run_id
                    or existing.command != command.command
                    or existing.reason != command.reason
                ):
                    raise ValueError("research_command_request_reused")
                return _command_view(existing, status="already_applied")

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
                    target_run_id = _command_child_run_id(command.request_id)
                    session.add(
                        RunRecord(
                            run_id=target_run_id,
                            event_id=run.event_id,
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
            session.flush()
            return _command_view(record, status=result_status)


def _trace_view(record: ResearchTraceRecord) -> ResearchTraceEvent:
    return ResearchTraceEvent.model_validate(
        {
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
            "error": (
                json.loads(record.error_provenance_json)
                if record.error_provenance_json
                else None
            ),
        }
    )


def _required_tool_reservation(
    session: Session, run_id: str, request_id: str
) -> ResearchToolCallReservationRecord:
    record = session.get(ResearchToolCallReservationRecord, (run_id, request_id))
    if record is None:
        raise KeyError(f"{run_id}:{request_id}")
    return record


def _tool_reservation_view(
    record: ResearchToolCallReservationRecord, session: Session
) -> ToolCallReservation:
    link = session.get(DshSessionLinkRecord, record.run_id)
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
        outcome=record.status,  # type: ignore[arg-type]
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


def _payload_hash(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
