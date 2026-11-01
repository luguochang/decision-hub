"""Original native error display; no Run reauthorization or raw Provider text."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import DshSessionResult
from packages.kernel.decision_hub_kernel.application.dsh_terminal_receipts import (
    AuthenticatedTerminalMetadata,
    read_authenticated_terminal_metadata_in_session,
    validate_terminal_result_native_metadata,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    read_transport_observation,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    DshSessionLinkRecord,
    RunRoleOutputRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedExecutionRef


def _failed_cause(session: Session, meta: AuthenticatedTerminalMetadata, *, assessed_at) -> dict[str, Any] | None:
    receipt, completion = meta.receipt, meta.receipt.completion
    if completion.terminal_status != "failed":
        return None
    code = "native_execution_failed"
    result_ref = None
    if completion.result_hash is not None:
        # A transport declaration cannot certify anything without the original
        # Host terminal hash. Select at most two matching original result rows.
        rows = session.scalars(select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == receipt.run_id,
            RunRoleOutputRecord.schema_version == "research-transport-observation.v1",
            RunRoleOutputRecord.role.like("research_transport:%"),
            func.json_valid(RunRoleOutputRecord.payload_json) == 1,
            func.json_extract(RunRoleOutputRecord.payload_json, "$.phase") == "result",
            func.json_extract(RunRoleOutputRecord.payload_json, "$.reply.result_hash")
            == completion.result_hash,
        ).order_by(RunRoleOutputRecord.created_at).limit(2)).all()
        if len(rows) != 1:
            raise ValueError("native_failure_result_original_unavailable")
        row = rows[0]
        reference = ObservedExecutionRef(
            output_id=row.output_id, role=row.role, schema_version=row.schema_version,
            payload_hash=row.payload_hash, recorded_at=as_utc(row.created_at),
        )
        observation = read_transport_observation(session, reference)
        # Original self-consistency alone does not establish availability
        # at this read cutoff. Native events and receipt times stay distinct.
        if not completion.completed_at <= observation.recorded_at <= assessed_at:
            raise ValueError("native_failure_result_time_outside_read_cutoff")
        value, context = observation.reply, observation.context
        if (not isinstance(value, DshSessionResult)
                or context.run_id != receipt.run_id or context.event_id != receipt.event_id
                or context.generation != receipt.generation
                or context.dsh_session_id != receipt.dsh_session_id
                or context.first_linked_request_hash != receipt.request_hash
                or context.prompt_hash != receipt.prompt_hash
                or value.run_id != receipt.run_id or value.generation != receipt.generation
                or value.dsh_session_id != receipt.dsh_session_id
                or value.last_seq != completion.last_seq or value.trace_ref != completion.trace_ref
                or value.finished_at != completion.completed_at
                or value.result_hash != completion.result_hash
                or hashlib.sha256((value.final_response + "\0" + value.events_json).encode()).hexdigest()
                != completion.result_hash
                or observation.events_json_hash != hashlib.sha256(value.events_json.encode()).hexdigest()
                or observation.final_response_hash != hashlib.sha256(value.final_response.encode()).hexdigest()):
            raise ValueError("native_failure_result_identity_invalid")
        assert receipt.request_id is not None
        validate_terminal_result_native_metadata(value, receipt.request_id, completion)
        # Same requested turn as the existing native validator; later user
        # turns must not reclassify the native error of this generation.
        prompt_found = False
        ends = []
        for item in json.loads(value.events_json):
            event = item["payload"]["event"]
            data = event["data"]
            source = data.get("source")
            if (event.get("type") == "user/message" and isinstance(source, dict)
                    and source.get("kind") == "user"):
                if source.get("rpcId") == receipt.request_id and not prompt_found:
                    prompt_found = True
                elif prompt_found and source.get("rpcId") != receipt.request_id:
                    break
            elif prompt_found and event.get("type") == "turn/end":
                ends.append(event)
        reason = ends[-1]["data"].get("reason", {})
        native_error = reason.get("error", {}) if isinstance(reason, dict) else {}
        original_code = native_error.get("code") if isinstance(native_error, dict) else None
        if completion.error is not None and completion.error.code == "dsh_turn_error":
            expected = (f"DSH turn failed ({original_code})"
                        if isinstance(original_code, str)
                        and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", original_code)
                        else "DSH turn did not produce an attested result")
            if completion.error.message != expected:
                raise ValueError("native_failure_terminal_body_drift")
            if original_code == "SKILL_CALLBACK_TRANSPORT_TIMEOUT":
                code = "skill_callback_transport_timeout"
        result_ref = dict(output_id=row.output_id, payload_hash=row.payload_hash)
    return dict(
        schema_version="research-authenticated-native-failure.v1", run_id=receipt.run_id,
        event_id=receipt.event_id, generation=receipt.generation, request_id=receipt.request_id,
        request_hash=receipt.request_hash, prompt_hash=receipt.prompt_hash,
        dsh_session_id=receipt.dsh_session_id, upstream_identity_hash=receipt.upstream_identity_hash,
        error_code=code, occurred_at=completion.completed_at.isoformat(),
        recorded_at=receipt.recorded_at.isoformat(), original_output_id=meta.receipt_ref,
        original_payload_hash=meta.receipt_hash, native_result_ref=result_ref,
        source="authenticated_host_terminal", authentication_source="dsh_callback_secret",
    )


def native_failure_projection_in_session(session: Session, run_id: str, *, now=None) -> dict[str, Any]:
    """Current original display; missing historical terminal bindings stay unknown.

    The current Link certifies only its actual generation. It must never be
    fabricated for an older generation. Missing history is an explicit gap.
    """
    coverage = dict(
        schema_version="research-native-failure-coverage.v1", run_id=run_id,
        current_generation=None, status="unknown", earliest_status="unknown",
        covered_generations=[], uncovered_generations=[], reason="missing_link",
    )
    result = dict(first_native_failure=None, current_authenticated_native_failure=None,
                  native_failure_coverage=coverage)
    try:
        assessed_at = as_utc(now or utcnow())
        if assessed_at is None:
            raise ValueError("native_failure_read_cutoff_missing")
        driver = session.connection().connection.driver_connection
        if isinstance(driver, sqlite3.Connection) and not driver.in_transaction:
            session.connection().exec_driver_sql("BEGIN")
        link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
        if link is None:
            return result
        coverage["current_generation"] = link.generation
        if not 1 <= link.generation <= 100:
            coverage["reason"] = "generation_range_unavailable"
            return result
        coverage["uncovered_generations"] = list(range(1, link.generation + 1))
        coverage["reason"] = ("current_not_terminal" if link.state not in {"failed", "completed", "cancelled"}
                              else "missing_or_invalid_original")
        meta = read_authenticated_terminal_metadata_in_session(session, run_id, now=assessed_at)
        if meta is None:
            return result
        cause = _failed_cause(session, meta, assessed_at=assessed_at)
        coverage["covered_generations"] = [link.generation]
        coverage["uncovered_generations"] = list(range(1, link.generation))
        result["current_authenticated_native_failure"] = cause
        if link.generation > 1:
            coverage.update(status="incomplete", reason="historical_binding_unavailable")
            return result
        coverage.update(status="complete", earliest_status="authenticated_failure" if cause else "no_failure",
                        reason="complete")
        result["first_native_failure"] = cause
        return result
    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
        coverage["reason"] = "missing_or_invalid_original"
        return result
    except SQLAlchemyError:
        coverage["reason"] = "read_unconfirmed"
        return result
