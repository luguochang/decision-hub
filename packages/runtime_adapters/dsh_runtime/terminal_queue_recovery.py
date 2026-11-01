"""Strict native-only cleanup authority; never writes a business terminal state."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    RunRecord,
    as_utc,
    utcnow,
)

from .cancelled_recovery import _load_v2_binding, _uses_v2

BUSINESS_TERMINAL = frozenset(
    {"completed", "degraded", "research_only", "rejected", "failed", "cancelled"}
)
NATIVE_TERMINAL = frozenset({"completed", "failed", "cancelled"})


class TerminalQueueAuthority(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["dsh-terminal-queue-authority.internal.v1"]
    purpose: Literal["before_failed_terminal", "acknowledged_terminal"]
    run_id: str
    dsh_session_id: str
    generation: int = Field(ge=1)
    request_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    original_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    terminal_hash: str | None = Field(pattern=r"^[a-f0-9]{64}$")
    hub_status: str
    lease_owner: str | None
    lease_expires_at: AwareDatetime | None
    native_state: str
    terminal_at: AwareDatetime | None
    observed_at: AwareDatetime


class TerminalQueueReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["dsh-terminal-queue-receipt.internal.v1"]
    run_id: str
    dsh_session_id: str
    generation: int = Field(ge=1)
    request_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    original_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    terminal_hash: str | None = Field(pattern=r"^[a-f0-9]{64}$")
    native_state: str
    terminal_at: AwareDatetime | None
    last_seq: int = Field(ge=0)
    observed_at: AwareDatetime
    quiet: Literal[True]


def terminal_queue_authority_in_session(
    session: Session,
    run_id: str,
    *,
    assessed_at: datetime | None = None,
) -> TerminalQueueAuthority:
    now = assessed_at or utcnow()
    run = session.get(RunRecord, run_id, populate_existing=True)
    link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
    if run is None or link is None:
        raise ValueError("terminal_queue_binding_missing")
    if (
        run.status in BUSINESS_TERMINAL
        and run.lease_owner is None
        and run.lease_expires_at is None
        and link.state in NATIVE_TERMINAL
        and link.terminal_at is not None
    ):
        purpose = "acknowledged_terminal"
    elif (
        run.status == "running"
        and run.lease_owner
        and run.lease_expires_at is not None
        and as_utc(run.lease_expires_at) > now
        and link.state not in NATIVE_TERMINAL
        and link.terminal_at is None
    ):
        purpose = "before_failed_terminal"
    else:
        raise ValueError("terminal_queue_cleanup_not_authorized")
    # Unknown/partial/V1 cannot obtain an automatic native cleanup authority.
    # Original cancelled V1 recovery retains its separate existing path.
    if not _uses_v2(session, run_id):
        raise ValueError("terminal_queue_original_proof_unavailable")
    original = _load_v2_binding(session, run_id)
    material = json.dumps(
        asdict(original),
        default=lambda value: value.isoformat(),
        sort_keys=True,
        separators=(",", ":"),
    )
    terminal_hash = None
    if purpose == "acknowledged_terminal":
        terminal_material = {
            "hub_status": run.status,
            "finished_at": as_utc(run.finished_at),
            "error_code": run.error_code,
            "event_id": run.event_id,
            "study_id": run.study_id,
            "parent_run_id": run.parent_run_id,
            "idempotency_key": run.idempotency_key,
            "native_state": link.state,
            "terminal_at": as_utc(link.terminal_at),
            "native_error_code": link.error_code,
            "result_hash": link.result_hash,
            "result_ref": link.result_ref,
            "trace_ref": link.trace_ref,
        }
        terminal_hash = hashlib.sha256(
            json.dumps(
                terminal_material,
                default=lambda value: value.isoformat(),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    return TerminalQueueAuthority(
        schema_version="dsh-terminal-queue-authority.internal.v1",
        purpose=purpose,
        run_id=run_id,
        dsh_session_id=link.dsh_session_id,
        generation=link.generation,
        request_hash=link.request_hash,
        original_hash=hashlib.sha256(material.encode()).hexdigest(),
        terminal_hash=terminal_hash,
        hub_status=run.status,
        lease_owner=run.lease_owner,
        lease_expires_at=as_utc(run.lease_expires_at),
        native_state=link.state,
        terminal_at=as_utc(link.terminal_at),
        observed_at=now,
    )


def _authority_link_identity(link: DshSessionLinkRecord) -> tuple[object, ...]:
    return (link.run_id, link.dsh_session_id, link.request_hash, link.generation,
            link.upstream_identity_json, link.plugin_build_hash, as_utc(link.deadline_at),
            link.max_tool_calls, link.model_step_timeout_ms)


def read_failed_terminal_authority(
    database: Database, run_id: str, *, checkpoint: Callable[[], None],
    stage: Callable[[str], None] = lambda _name: None,
) -> TerminalQueueAuthority:
    """Full owned originals, then an independent fresh failure-cleanup fence.

    Only the active original Task may request this pre-terminal cleanup. This
    does not authorize a model, repair, business failure write or a later Task.
    Existing acknowledged-terminal maintenance uses the legacy separate reader.
    """
    from sqlalchemy.orm import Session

    from packages.kernel.decision_hub_kernel.persistence.callback_source_lease import (
        callback_source_lease_scope,
    )
    from packages.kernel.decision_hub_kernel.persistence.original_preparation import (
        prepare_originals,
    )

    def original(session: Session):
        proof = terminal_queue_authority_in_session(session, run_id)
        if lease is not None:
            lease.extend_authenticated_selection(session, run_id)
        if proof.purpose != "before_failed_terminal":
            raise ValueError("terminal_queue_cleanup_not_authorized")
        run = session.get(RunRecord, run_id, populate_existing=True)
        link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
        assert run is not None and link is not None
        if link.deadline_at is None or as_utc(link.deadline_at) <= utcnow():
            raise ValueError("dsh_host_read_deadline_elapsed")
        return proof, (run.event_id, run.snapshot_id), _authority_link_identity(link)

    with callback_source_lease_scope(checkpoint) as lease:
        stage("original_auth_started")
        originals = prepare_originals(database, original, checkpoint=checkpoint,
            source_lease=lease, link_activity_observations=True,
            link_tool_counter_observations=True)
        stage("original_auth_completed")
        checkpoint()
        proof, run_identity, link_identity = originals.value
        # One held physical Connection avoids returning a query_only connection
        # to the pool before the OFF reset, including cancellation/rollback.
        with database.engine.connect() as connection, Session(bind=connection) as session:
            connection.exec_driver_sql("PRAGMA query_only=ON")
            try:
                connection.exec_driver_sql("BEGIN")
                stage("readset_started")
                originals.verify_read_snapshot(session, checkpoint=checkpoint)
                stage("readset_completed")
                # Readset replay may itself be slow. Close it before the short
                # permission cut so cancellation during replay becomes visible.
                session.rollback()
                connection.exec_driver_sql("BEGIN")
                run = session.get(RunRecord, run_id, populate_existing=True)
                link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
                now = utcnow()
                if (run is None or link is None
                        or (run.event_id, run.snapshot_id) != run_identity
                        or _authority_link_identity(link) != link_identity
                        or run.status != "running" or run.artifact_id is not None
                        or run.finished_at is not None or not proof.lease_owner
                        or run.lease_owner != proof.lease_owner
                        or run.lease_expires_at is None or as_utc(run.lease_expires_at) <= now
                        or link.state in NATIVE_TERMINAL or link.terminal_at is not None):
                    raise ValueError("terminal_queue_cleanup_not_authorized")
                if link.deadline_at is None or as_utc(link.deadline_at) <= now:
                    raise ValueError("dsh_host_read_deadline_elapsed")
                checkpoint()
                stage("fresh_permission_confirmed")
                return proof.model_copy(update={
                    "lease_expires_at": as_utc(run.lease_expires_at),
                    "native_state": link.state, "observed_at": now,
                })
            finally:
                session.rollback()
                connection.exec_driver_sql("PRAGMA query_only=OFF")


class TerminalQueueReconciler:
    def __init__(self, projector, client):
        self.projector, self.client = projector, client
        self.cursor = ""
        self.backoff: OrderedDict[str, float] = OrderedDict()

    async def tick(self, *, limit: int = 4, time_budget_seconds: float = 2) -> dict[str, int]:
        counts = dict(scanned=0, attempted=0, confirmed=0, pending=0, skipped=0, backoff=0)
        if not callable(getattr(self.client, "terminal_quiesce", None)):
            return counts

        def page(session):
            query = (
                select(RunRecord.run_id)
                .join(
                    DshSessionLinkRecord,
                    DshSessionLinkRecord.run_id == RunRecord.run_id,
                )
                .where(
                    RunRecord.status.in_(BUSINESS_TERMINAL),
                    RunRecord.lease_owner.is_(None),
                    RunRecord.lease_expires_at.is_(None),
                    DshSessionLinkRecord.state.in_(NATIVE_TERMINAL),
                    DshSessionLinkRecord.terminal_at.is_not(None),
                )
            )
            values = list(
                session.scalars(
                    query.where(RunRecord.run_id > self.cursor)
                    .order_by(RunRecord.run_id)
                    .limit(limit)
                )
            )
            if not values and self.cursor:
                values = list(session.scalars(query.order_by(RunRecord.run_id).limit(limit)))
            return values

        async with asyncio.timeout(time_budget_seconds):
            selected = await self.projector.read(page, timeout_seconds=min(1, time_budget_seconds))
            if selected.state != "ready" or selected.value is None:
                counts["pending"] += 1
                return counts
            for run_id in selected.value:
                self.cursor = run_id
                counts["scanned"] += 1
                if self.backoff.get(run_id, 0) > time.monotonic():
                    counts["backoff"] += 1
                    continue
                self.backoff[run_id] = time.monotonic() + 30
                self.backoff.move_to_end(run_id)
                while len(self.backoff) > 256:
                    self.backoff.popitem(last=False)
                try:
                    before = await self.projector.read(
                        lambda session, selected_id=run_id: terminal_queue_authority_in_session(
                            session, selected_id
                        ),
                        timeout_seconds=min(1, time_budget_seconds),
                    )
                    if before.state != "ready" or before.value is None:
                        counts["skipped"] += 1
                        continue
                    authority = before.value
                    counts["attempted"] += 1
                    receipt = await self.client.terminal_quiesce(authority)
                    after = await self.projector.read(
                        lambda session, selected_id=run_id: terminal_queue_authority_in_session(
                            session, selected_id
                        ),
                        timeout_seconds=min(1, time_budget_seconds),
                    )
                    if (
                        after.state != "ready"
                        or after.value is None
                        or after.value.model_dump(exclude={"observed_at"})
                        != authority.model_dump(exclude={"observed_at"})
                        or receipt.run_id != authority.run_id
                        or receipt.dsh_session_id != authority.dsh_session_id
                        or receipt.generation != authority.generation
                        or receipt.request_hash != authority.request_hash
                        or receipt.original_hash != authority.original_hash
                        or receipt.terminal_hash != authority.terminal_hash
                        or receipt.native_state != authority.native_state
                        or receipt.terminal_at != authority.terminal_at
                    ):
                        raise ValueError("terminal_queue_original_changed")
                    counts["confirmed"] += 1
                    self.backoff[run_id] = time.monotonic() + 300
                except asyncio.CancelledError:
                    raise
                except Exception:
                    counts["pending"] += 1
        return counts
