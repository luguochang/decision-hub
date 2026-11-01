"""Freeze server-owned live call timing without changing explicit PIT queries."""

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    DshRunSessionLinkView,
    ResearchCapabilityInvocation,
)
from packages.kernel.decision_hub_kernel.application.research_method import canonical_recipe
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
    utcnow,
)

ROLE_PREFIX = "research_tool_time:"
SCHEMA = "research-tool-time.v1"


@dataclass(frozen=True)
class InvocationTimingReceipt:
    issued_at: datetime
    raw_identity_json: str


class ResearchInvocationTiming:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] = utcnow):
        self.database, self.clock = database, clock

    def bind(self, invocation: ResearchCapabilityInvocation, link: DshRunSessionLinkView):
        from .research_observability import requires_tool_admission_proof

        with self.database.session() as session:
            if requires_tool_admission_proof(session, link.run_id):
                raise ValueError("research_invocation_v2_writer_required")
        if link.deadline_at is None:
            raise ValueError("research_invocation_deadline_missing")
        intent_hash = hashlib.sha256(canonical_recipe(
            invocation.model_dump(mode="json")
        ).encode()).hexdigest()
        role = ROLE_PREFIX + hashlib.sha256(invocation.request_id.encode()).hexdigest()[:40]
        identity = {
            "schema_version": SCHEMA, "run_id": link.run_id,
            "research_session_id": link.dsh_session_id, "request_id": invocation.request_id,
            "intent_hash": intent_hash, "generation": link.generation,
            "deadline_at": link.deadline_at.isoformat(),
        }
        with self.database.session() as session:
            if session.get_bind().dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            if session.get(RunRecord, link.run_id, with_for_update=True) is None:
                raise ValueError("research_invocation_run_missing")
            row = session.query(RunRoleOutputRecord).filter_by(
                run_id=link.run_id, role=role,
            ).one_or_none()
            if row:
                if row.schema_version != SCHEMA or row.payload_hash != hashlib.sha256(
                    row.payload_json.encode()
                ).hexdigest():
                    raise ValueError("research_invocation_timing_corrupt")
                body = json.loads(row.payload_json)
                if {k: body.get(k) for k in identity} != identity:
                    raise ValueError("research_invocation_identity_changed")
                return body
            issued = as_utc(self.clock())
            assert issued is not None
            body = {**identity, "issued_at": issued.isoformat()}
            encoded = canonical_recipe(body)
            session.add(RunRoleOutputRecord(
                output_id="timing_" + hashlib.sha256(
                    (link.run_id + ":" + role).encode()
                ).hexdigest()[:32],
                run_id=link.run_id, role=role, payload_json=encoded,
                payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
                schema_version=SCHEMA, created_at=self.clock(),
            ))
            session.flush()
            return body

    def bind_in_session(
        self, session: Session, invocation: ResearchCapabilityInvocation,
        link: DshRunSessionLinkView, *, allow_create: bool = True,
    ) -> InvocationTimingReceipt:
        """Borrow admission's physical writer; one original instant, no own commit."""
        if not session.in_transaction():
            raise ValueError("research_invocation_writer_required")
        connection = session.connection()
        if connection.dialect.name == "sqlite":
            driver = connection.connection.driver_connection
            if not isinstance(driver, sqlite3.Connection) or not driver.in_transaction:
                raise ValueError("research_invocation_writer_required")
        if invocation.research_session_id != link.dsh_session_id or link.deadline_at is None:
            raise ValueError("research_invocation_identity_changed")
        role = ROLE_PREFIX + hashlib.sha256(invocation.request_id.encode()).hexdigest()[:40]
        output_id = "timing_" + hashlib.sha256((link.run_id + ":" + role).encode()).hexdigest()[:32]
        identity = {
            "schema_version": SCHEMA, "run_id": link.run_id,
            "research_session_id": link.dsh_session_id, "request_id": invocation.request_id,
            "intent_hash": hashlib.sha256(canonical_recipe(
                invocation.model_dump(mode="json")).encode()).hexdigest(),
            "generation": link.generation, "deadline_at": link.deadline_at.isoformat(),
        }
        row = session.query(RunRoleOutputRecord).filter_by(
            run_id=link.run_id, role=role).populate_existing().one_or_none()
        if row is None:
            if not allow_create:
                raise ValueError("research_invocation_timing_missing")
            instant = self.clock()
            if instant.tzinfo is None or instant.utcoffset() is None:
                raise ValueError("research_invocation_clock_invalid")
            issued = as_utc(instant)
            assert issued is not None
            if issued >= link.deadline_at:
                raise ValueError("research_invocation_deadline_elapsed")
            encoded = canonical_recipe({**identity, "issued_at": issued.isoformat()})
            row = RunRoleOutputRecord(output_id=output_id, run_id=link.run_id, role=role,
                payload_json=encoded, payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
                schema_version=SCHEMA, created_at=issued)
            session.add(row)
            session.flush()
        if (row.output_id != output_id or row.schema_version != SCHEMA
                or row.payload_hash != hashlib.sha256(row.payload_json.encode()).hexdigest()):
            raise ValueError("research_invocation_timing_corrupt")
        body = json.loads(row.payload_json)
        if not isinstance(body, dict) or set(body) != {*identity, "issued_at"}:
            raise ValueError("research_invocation_timing_corrupt")
        if {key: body.get(key) for key in identity} != identity:
            raise ValueError("research_invocation_identity_changed")
        issued = datetime.fromisoformat(body["issued_at"])
        if issued.tzinfo is None or issued.utcoffset() is None:
            raise ValueError("research_invocation_timing_corrupt")
        created = as_utc(row.created_at)
        if (created is None or created != issued or issued >= link.deadline_at
                or canonical_recipe(body) != row.payload_json):
            raise ValueError("research_invocation_timing_corrupt")
        return InvocationTimingReceipt(issued_at=issued, raw_identity_json=canonical_recipe({
            "output_id": row.output_id, "run_id": row.run_id, "role": row.role,
            "schema_version": row.schema_version, "payload_hash": row.payload_hash,
            "created_at": created.isoformat(),
        }))
