"""Durable call metadata, excluding prompts, provider bodies and credentials."""

import asyncio
import hashlib
import json
import uuid

from packages.kernel.decision_hub_kernel.persistence.db import (
    EvolutionJobRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    EvolutionPlanningCallRecord as Call,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError

SAFE_CODES = {
    "provider_transport_error",
    "unknown_runtime_error",
    "rate_limited",
    "timeout",
    "server_error",
    "provider_timeout",
    "provider_rate_limited",
    "provider_unavailable",
    "provider_authentication_failed",
    "provider_request_invalid",
    "structured_output_invalid",
    "candidate_evidence_out_of_scope",
    "configuration_invalid",
    "runtime_recipe_changed",
    "provider_error",
    "context_length_exceeded",
    "invalid_json_schema",
    "rate_limit_exceeded",
    "insufficient_quota",
    "invalid_api_key",
    "model_not_found",
}


def _code(value):
    return value if isinstance(value, str) and value in SAFE_CODES else None


class AuditedPlanningRuntime:
    def __init__(self, database, runtime, job_id):
        self.database, self.runtime, self.job_id = database, runtime, job_id
        with database.session() as session:
            job = session.get(EvolutionJobRecord, job_id)
            if job is None or job.status != "running":
                raise ValueError("planning_audit_requires_claimed_job")
            self.attempt = job.attempt

    async def execute(self, request):
        identity = uuid.uuid4().hex
        body = json.dumps(
            {
                "role": request.role,
                "text": request.text,
                "evidence": request.evidence,
                "instructions": request.instructions,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        details = {
            "schema_version": "evolution-planning-call.v1",
            "request_hash": hashlib.sha256(body.encode()).hexdigest(),
            "text_characters": len(request.text),
            "evidence_count": len(request.evidence),
            "deadline_at": request.deadline_at.isoformat(),
        }
        with self.database.session() as session:
            session.add(
                Call(
                    call_id=identity,
                    job_id=self.job_id,
                    job_attempt=self.attempt,
                    role=request.role,
                    started_at=utcnow(),
                    status="running",
                    details_json=json.dumps(details),
                )
            )
        try:
            result = await self.runtime.execute(request)
        except BaseException as exc:
            details["error_code"] = (
                _code(getattr(exc, "error_code", None)) or "planning_call_failed"
            )
            details["cause_code"] = _code(getattr(exc, "cause_code", None))
            details["retryable"] = isinstance(exc, AgentExecutionError) and exc.retryable
            cause = exc.__cause__
            status = getattr(cause, "status_code", None)
            details["http_status"] = (
                status if type(status) is int and 100 <= status <= 599 else None
            )
            provider_body = getattr(cause, "body", None)
            if isinstance(provider_body, dict):
                error = provider_body.get("error", provider_body)
                if isinstance(error, dict):
                    details["provider_error_code"] = _code(error.get("code"))
            self._finish(
                identity,
                "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                details,
            )
            raise
        details.update(
            latency_ms=result.latency_ms,
            result_hash=hashlib.sha256(
                json.dumps(result.payload, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest(),
        )
        self._finish(identity, "succeeded", details)
        return result

    def _finish(self, identity, status, details):
        with self.database.session() as session:
            row = session.get(Call, identity)
            row.status, row.finished_at = status, utcnow()
            row.details_json = json.dumps(details, ensure_ascii=False, sort_keys=True)


def planning_calls(database, job_id, *, limit=100):
    with database.session() as session:
        if session.get(EvolutionJobRecord, job_id) is None:
            raise KeyError(job_id)
        rows = (
            session.query(Call)
            .filter_by(job_id=job_id)
            .order_by(Call.started_at.desc(), Call.call_id)
            .limit(max(1, min(limit, 200)))
            .all()
        )
        return [
            {
                "call_id": r.call_id,
                "job_id": r.job_id,
                "job_attempt": r.job_attempt,
                "role": r.role,
                "started_at": as_utc(r.started_at).isoformat(),
                "finished_at": as_utc(r.finished_at).isoformat() if r.finished_at else None,
                "status": r.status,
                "details": json.loads(r.details_json),
            }
            for r in rows
        ]
