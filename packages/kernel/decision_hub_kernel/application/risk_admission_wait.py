"""Audited admission-condition credits; never Host inactivity or Run authority."""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from datetime import datetime, timedelta

from sqlalchemy import select

from ..persistence.db import ProductCommandRecord, as_utc
from ..persistence.risk_research_records import RiskResearchTriggerRecord
from ..ports.market_path import canonical_hash

KIND = "risk_admission_condition_wait"
SCHEMA = "risk-admission-condition-wait.v1"
REASON = "study_host_activity_refresh_required"
SOURCE_REASON = "spot_current_original_path_not_fresh"
WAIT_CONDITIONS = {
    REASON: {"fresh_host_preflight", "durable_admission"},
    SOURCE_REASON: {"scope_prepare"},
}
OWNER = "system:risk-research-admission"
DELAY_SECONDS = 20
MAX_GENERATION = 2_147_483_647
FIELDS = {
    "schema_version",
    "request_id",
    "scope_id",
    "instrument",
    "lease_generation",
    "claim_owner",
    "lease_window_started_at",
    "claim_expires_at",
    "recorded_at",
    "available_at",
    "attempts_before",
    "attempts_after",
    "reason_code",
    "stage",
    "initiating_trigger_key",
    "initiating_trigger_hash",
}


class AdmissionConditionWait(ValueError):
    error_code = REASON

    def __init__(self, stage: str, *, reason_code: str = REASON):
        if stage not in WAIT_CONDITIONS.get(reason_code, set()):
            raise ValueError("risk_admission_wait_stage_invalid")
        self.stage, self.error_code = stage, reason_code
        super().__init__(self.error_code)


def _json(value) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _key(request_id: str, generation: int) -> str:
    return "risk-admission-wait:" + canonical_hash([request_id, generation])[:48]


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("risk_admission_wait_time_invalid")
    return as_utc(parsed)


def recognized_condition_waits(session, row, *, now: datetime) -> tuple[dict, ...]:
    """Authenticate only this intent's credits; no permission is returned.

    A legacy row without credit originals must still have generation==attempts.
    Valid appended triggers do not erase the original initiating trigger.
    """
    records = session.scalars(
        select(ProductCommandRecord)
        .where(
            ProductCommandRecord.target_id == row.request_id,
            ProductCommandRecord.kind == KIND,
        )
        .execution_options(populate_existing=True)
    ).all()
    originals = []
    for record in records:
        try:
            body = json.loads(record.result_json)
            if not isinstance(body, dict) or set(body) != FIELDS:
                raise ValueError("shape")
            generation = body["lease_generation"]
            started, expires, recorded, available = (
                _time(body[field])
                for field in (
                    "lease_window_started_at",
                    "claim_expires_at",
                    "recorded_at",
                    "available_at",
                )
            )
            trigger = session.get(
                RiskResearchTriggerRecord,
                body["initiating_trigger_key"],
                populate_existing=True,
            )
            if (
                body["schema_version"] != SCHEMA
                or body["request_id"] != row.request_id
                or body["scope_id"] != row.scope_id
                or body["instrument"] != row.instrument
                or type(generation) is not int
                or not 1 <= generation <= MAX_GENERATION
                or type(body["attempts_before"]) is not int
                or type(body["attempts_after"]) is not int
                or record.request_id != _key(row.request_id, generation)
                or record.owner_id != OWNER
                or record.payload_hash != canonical_hash(body)
                or record.result_json != _json(body)
                or as_utc(record.created_at) != recorded
                or body["stage"] not in WAIT_CONDITIONS.get(body["reason_code"], set())
                or re.fullmatch(r"risk-research:[0-9a-f]{32}", body["claim_owner"]) is None
                or expires != started + timedelta(seconds=60)
                or not as_utc(row.created_at) <= started <= recorded < expires
                or not recorded <= as_utc(row.updated_at) <= now
                or available != recorded + timedelta(seconds=DELAY_SECONDS)
                or trigger is None
                or trigger.request_id != row.request_id
                or trigger.scope_id != row.scope_id
                or trigger.payload_hash != body["initiating_trigger_hash"]
                or canonical_hash(json.loads(trigger.payload_json)) != trigger.payload_hash
                or as_utc(trigger.recorded_at) != as_utc(row.created_at)
                or row.request_id
                != "riskresearch_" + canonical_hash([row.scope_id, trigger.trigger_key])[:40]
            ):
                raise ValueError("binding")
            originals.append(body)
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise ValueError("risk_admission_wait_receipt_invalid") from error
    originals.sort(key=lambda body: body["lease_generation"])
    prior_generation, prior_time = 0, as_utc(row.created_at)
    for refunded_before, body in enumerate(originals):
        generation = body["lease_generation"]
        if (
            generation <= prior_generation
            or generation > row.lease_generation
            or body["attempts_before"] != generation - refunded_before
            or not 1 <= body["attempts_before"] <= 3
            or body["attempts_after"] != body["attempts_before"] - 1
            or _time(body["lease_window_started_at"]) < prior_time
        ):
            raise ValueError("risk_admission_wait_counter_invalid")
        prior_generation, prior_time = generation, _time(body["recorded_at"])
    if (
        type(row.attempts) is not int
        or type(row.lease_generation) is not int
        or not 0 <= row.attempts <= 3
        or not 0 <= row.lease_generation <= MAX_GENERATION
        or row.lease_generation != row.attempts + len(originals)
    ):
        raise ValueError("risk_admission_wait_counter_invalid")
    return tuple(originals)


def admission_accounting_valid(session, row, *, now: datetime) -> bool:
    try:
        recognized_condition_waits(session, row, now=now)
        return True
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def defer_owned_condition(session, row, triggers, error: AdmissionConditionWait, *, now: datetime):
    """Caller holds the writer and has just authenticated exact owner/generation.

    Save credit plus pending state atomically. Never decrement lease_generation.
    """
    recognized_condition_waits(session, row, now=now)
    if (
        row.status != "claiming"
        or row.run_id is not None
        or row.event_id is not None
        or row.attempts < 1
        or row.lease_expires_at is None
        or as_utc(row.lease_expires_at) != as_utc(row.updated_at) + timedelta(seconds=60)
        or re.fullmatch(r"risk-research:[0-9a-f]{32}", row.lease_owner or "") is None
        or not as_utc(row.updated_at) <= now < as_utc(row.lease_expires_at)
    ):
        raise ValueError("risk_admission_wait_permission_lost")
    initiating = next(
        (
            trigger
            for trigger in triggers
            if as_utc(trigger.recorded_at) == as_utc(row.created_at)
            and row.request_id
            == "riskresearch_" + canonical_hash([row.scope_id, trigger.trigger_key])[:40]
        ),
        None,
    )
    if initiating is None:
        raise ValueError("risk_admission_wait_trigger_missing")
    available = now + timedelta(seconds=DELAY_SECONDS)
    body = {
        "schema_version": SCHEMA,
        "request_id": row.request_id,
        "scope_id": row.scope_id,
        "instrument": row.instrument,
        "lease_generation": row.lease_generation,
        "claim_owner": row.lease_owner,
        "lease_window_started_at": as_utc(row.updated_at).isoformat(),
        "claim_expires_at": as_utc(row.lease_expires_at).isoformat(),
        "recorded_at": now.isoformat(),
        "available_at": available.isoformat(),
        "attempts_before": row.attempts,
        "attempts_after": row.attempts - 1,
        "reason_code": error.error_code,
        "stage": error.stage,
        "initiating_trigger_key": initiating.trigger_key,
        "initiating_trigger_hash": initiating.payload_hash,
    }
    key = _key(row.request_id, row.lease_generation)
    if session.get(ProductCommandRecord, key) is not None:
        raise ValueError("risk_admission_wait_duplicate_credit")
    session.add(
        ProductCommandRecord(
            request_id=key,
            owner_id=OWNER,
            target_id=row.request_id,
            kind=KIND,
            payload_hash=canonical_hash(body),
            result_json=_json(body),
            created_at=now,
        )
    )
    row.attempts -= 1
    row.status, row.error_code = "pending_admission", error.error_code
    row.available_at, row.updated_at = available, now
    row.lease_owner = row.lease_expires_at = None
    return body


async def observe_owned(awaitable):
    """Keep one accepted operation until its own completion, also on cancellation."""
    operation = asyncio.ensure_future(awaitable)
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        while not operation.done():
            try:
                await asyncio.shield(operation)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if operation.done():
            try:
                operation.result()
            except BaseException:
                pass
        raise


class ContinuousAdmissionWriter:
    """An unexpired claim may finish inside its one uninterrupted SQLite writer.

    This is not a new lease. The claim cannot enter after expiry, and no authority
    survives this transaction. Full source/Host/spot guards still run as before.
    The SQLite authorizer forbids even raw driver COMMIT/ROLLBACK or SAVEPOINT.
    Clear it before the owning Database.session performs its final commit/rollback.
    """

    def __init__(self, session, row, *, owner, generation, acquired_at):
        self.session, self.row = session, row
        self.connection = session.connection()
        self.native = self.connection.connection.driver_connection
        self.transaction = session.get_transaction()
        self.connection_transaction = self.connection.get_transaction()
        self.acquired_at = acquired_at
        self.fence = self._fence(row)
        self.boundary_attempted = False
        if (
            self.connection.dialect.name != "sqlite"
            or not isinstance(self.native, sqlite3.Connection)
            or not self.native.in_transaction
            or self.transaction is None
            or not self.transaction.is_active
            or self.connection_transaction is None
            or not self.connection_transaction.is_active
            or session.in_nested_transaction()
            or self.connection.in_nested_transaction()
            or row.status != "claiming"
            or row.lease_owner != owner
            or row.lease_generation != generation
            or row.run_id is not None
            or row.event_id is not None
            or row.lease_expires_at is None
            or not as_utc(row.updated_at) <= acquired_at < as_utc(row.lease_expires_at)
            or as_utc(row.lease_expires_at) != as_utc(row.updated_at) + timedelta(seconds=60)
        ):
            raise ValueError("risk_admission_writer_permission_invalid")

    @staticmethod
    def _fence(row):
        return (
            row.request_id,
            row.status,
            row.lease_owner,
            row.lease_generation,
            row.attempts,
            row.updated_at,
            row.lease_expires_at,
            row.event_id,
            row.run_id,
        )

    def __enter__(self):
        def authorize(action, _arg1, _arg2, _database, _trigger):
            if action in (sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT):
                self.boundary_attempted = True
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        self.native.set_authorizer(authorize)
        return self

    def complete(self, row, *, completed_at):
        if (
            self.boundary_attempted
            or completed_at < self.acquired_at
            or self.session.get_transaction() is not self.transaction
            or not self.transaction.is_active
            or self.session.in_nested_transaction()
            or self.session.connection() is not self.connection
            or self.connection.closed
            or self.connection.invalidated
            or self.connection.get_transaction() is not self.connection_transaction
            or not self.connection_transaction.is_active
            or self.connection.in_nested_transaction()
            or self.connection.connection.driver_connection is not self.native
            or not self.native.in_transaction
            or row is not self.row
            or self._fence(row) != self.fence
        ):
            raise ValueError("risk_admission_writer_continuity_lost")

    def __exit__(self, _type, _value, _traceback):
        self.native.set_authorizer(None)
