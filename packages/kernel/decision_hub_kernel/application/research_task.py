"""Freeze the complete graph input before any per-round filtering or execution."""

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import cast

from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import ResearchSessionRequest
from packages.kernel.decision_hub_kernel.application.research_method import canonical_recipe
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    DshSessionPromptRecord,
    ResearchResultRecord,
    ResearchTraceRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
    utcnow,
)

ROLE = "research_task_request"
SCHEMA = "research-task-request.v1"


class ResearchTaskError(ValueError):
    def __init__(self, code: str) -> None:
        self.error_code = code
        super().__init__(code)


def read_task_request(
    session: Session, run: RunRecord, *, assessed_at: datetime | None = None
) -> tuple[ResearchSessionRequest, str, str] | None:
    row = session.query(RunRoleOutputRecord).filter_by(run_id=run.run_id, role=ROLE).one_or_none()
    try:
        from .research_binding import (
            FrozenResearchBundle,
            has_prepared_bundle_in_session,
            read_research_bundle_in_session,
        )

        if has_prepared_bundle_in_session(session, run.run_id):
            bundle = read_research_bundle_in_session(session, run.run_id, assessed_at=assessed_at)
            if not isinstance(bundle, FrozenResearchBundle):
                raise ValueError("invalid_task_bundle")
            return bundle.request, bundle.task.request_hash, "saved_task_request"
        if row is None:
            return None
        body = json.loads(row.payload_json)
        request = ResearchSessionRequest.model_validate(body["request"])
        digest = hashlib.sha256(canonical_recipe(body["request"]).encode()).hexdigest()
        first_prompt = session.get(DshSessionPromptRecord, (run.run_id, 1))
        source = body.get("source")
        original_at = cast(datetime, as_utc(row.created_at))
        if source is not None:
            if source["kind"] != "initial_checkpoint" or len(source["payload_hash"]) != 64:
                raise ValueError("invalid_task_source")
            original_at = cast(
                datetime, as_utc(datetime.fromisoformat(source["original_recorded_at"]))
            )
            if original_at > cast(datetime, as_utc(row.created_at)) or not source["checkpoint_id"]:
                raise ValueError("invalid_task_source_time")
        if (
            row.schema_version != SCHEMA
            or body["schema_version"] != SCHEMA
            or body["run_id"] != run.run_id
            or request.run_id != run.run_id
            or request.event_id != run.event_id
            or request.current_round != 1
            or body["request_hash"] != digest
            or row.payload_hash != hashlib.sha256(row.payload_json.encode()).hexdigest()
            or (run.snapshot_id and run.snapshot_id != request.trigger_snapshot_id)
            or (assessed_at and original_at > assessed_at)
            or (first_prompt and original_at > cast(datetime, as_utc(first_prompt.created_at)))
        ):
            raise ValueError("invalid_task_request")
        return request, digest, "saved_checkpoint" if source else "saved_task_request"
    except (ValueError, KeyError, TypeError) as exc:
        raise ResearchTaskError("research_task_request_invalid") from exc


class ResearchTaskService:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.database, self.clock = database, clock

    def get(self, run_id: str) -> ResearchSessionRequest | None:
        with self.database.session() as session:
            return self.get_in_session(session, run_id)

    def get_in_session(self, session: Session, run_id: str) -> ResearchSessionRequest | None:
        """Read a full Task through the caller session without committing it."""
        run = session.get(RunRecord, run_id, populate_existing=True)
        if run is None:
            raise KeyError(run_id)
        saved = read_task_request(session, run)
        return saved[0] if saved else None

    def freeze(self, request: ResearchSessionRequest) -> ResearchSessionRequest:
        with self.database.session() as session:
            if session.get_bind().dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            run = session.get(RunRecord, request.run_id)
            if (
                run is None
                or run.event_id != request.event_id
                or request.current_round != 1
                or (run.snapshot_id and run.snapshot_id != request.trigger_snapshot_id)
            ):
                raise ResearchTaskError("research_task_request_identity_mismatch")
            saved = read_task_request(session, run)
            if saved:
                if saved[0] != request:
                    raise ResearchTaskError("research_task_request_changed")
                return saved[0]
            if (
                session.get(DshSessionLinkRecord, run.run_id)
                or session.get(ResearchResultRecord, run.run_id)
                or session.query(ResearchTraceRecord).filter_by(run_id=run.run_id).first()
            ):
                raise ResearchTaskError("research_task_request_missing_before_execution")
            payload = request.model_dump(mode="json")
            body = {
                "schema_version": SCHEMA,
                "run_id": run.run_id,
                "request": payload,
                "request_hash": hashlib.sha256(canonical_recipe(payload).encode()).hexdigest(),
            }
            encoded = canonical_recipe(body)
            session.add(
                RunRoleOutputRecord(
                    output_id="task_" + hashlib.sha256(run.run_id.encode()).hexdigest()[:32],
                    run_id=run.run_id,
                    role=ROLE,
                    schema_version=SCHEMA,
                    payload_json=encoded,
                    payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
                    created_at=self.clock(),
                )
            )
            return request
