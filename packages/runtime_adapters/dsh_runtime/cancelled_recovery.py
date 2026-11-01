"""Recover cancelled Run links from original receipts without creating work."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    DshBridgeError,
    DshSessionCompletion,
    DshSessionResult,
    DshSessionStatus,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import (
    TERMINAL_STATES,
    DshSessionLinkService,
    read_execution_request,
    read_prompt_integrity,
)
from packages.kernel.decision_hub_kernel.application.dsh_terminal_receipts import (
    read_authenticated_terminal_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_binding import (
    BUNDLE_ROLES,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    EXECUTION_SCHEMA,
    INTEGRITY_SCHEMA,
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    ROLE as METHOD_ROLE,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    ResearchMethodService,
    recipe_hash,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.application.research_task import read_task_request
from packages.kernel.decision_hub_kernel.persistence.db import (
    DshSessionLinkRecord,
    DshSessionPromptRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
)

from .cancellation_projection import DshCancellationProjector
from .web_host_client import DshRecoveryBinding, DshRecoveryHostClient


def _utc(value: datetime) -> datetime:
    normalized = as_utc(value)
    assert normalized is not None
    return normalized


@dataclass(frozen=True)
class _V2RecoveryBinding(DshRecoveryBinding):
    """Internal original-row witness; no additional Host request or permission."""

    native_fingerprint: str


def _uses_v2(session: Session, run_id: str) -> bool:
    """Short original-row probe; unknown/partial native pieces never choose V1."""
    method_schema = session.scalar(
        select(RunRoleOutputRecord.schema_version).where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role == METHOD_ROLE,
        )
    )
    v2_sidecar = session.scalar(
        select(RunRoleOutputRecord.output_id)
        .where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.schema_version.in_((EXECUTION_SCHEMA, INTEGRITY_SCHEMA)),
        )
        .limit(1)
    )
    from packages.kernel.decision_hub_kernel.application.research_observability import (
        requires_tool_admission_proof,
    )

    return (
        requires_tool_admission_proof(session, run_id)
        or v2_sidecar is not None
        or (method_schema is not None and method_schema != "research-execution-method.v1")
    )


def _load_v2_binding(session: Session, run_id: str) -> _V2RecoveryBinding:
    # The Kernel reads the original Source/Task/scope and authenticates the
    # complete Method + all generations, in this same consistent snapshot.
    prepared = read_prepared_execution_in_session(session, run_id)
    link, method, execution = prepared.link, prepared.method, prepared.execution
    if (
        link.deadline_at is None
        or link.max_tool_calls is None
        or link.model_step_timeout_ms is None
    ):
        raise ValueError("recovery_frozen_budget_missing")
    rows = session.scalars(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.run_id == run_id,
            (RunRoleOutputRecord.role.in_((*BUNDLE_ROLES, METHOD_ROLE)))
            | (RunRoleOutputRecord.role.like("dsh_execution_request:%"))
            | (RunRoleOutputRecord.role.like("dsh_prompt_integrity:%"))
            | (RunRoleOutputRecord.role.like("context_execution_link:%"))
            | (RunRoleOutputRecord.role.like("context_select.%")),
        )
        .order_by(RunRoleOutputRecord.role)
        .execution_options(populate_existing=True)
    ).all()
    prompts = session.scalars(
        select(DshSessionPromptRecord)
        .where(
            DshSessionPromptRecord.run_id == run_id,
        )
        .order_by(DshSessionPromptRecord.generation)
        .execution_options(populate_existing=True)
    ).all()
    # read_prepared_execution_in_session has checked continuity from generation
    # one. Fingerprint whole original rows, including time and whole prompt,
    # so a self-consistently re-signed body during RPC is still a changed proof.
    witness = {
        "schema_version": "internal-recovery-original-rows.v2",
        "rows": [
            {
                "output_id": row.output_id,
                "run_id": row.run_id,
                "role": row.role,
                "schema_version": row.schema_version,
                "payload_hash": row.payload_hash,
                "raw_payload_sha256": hashlib.sha256(row.payload_json.encode("utf-8")).hexdigest(),
                "created_at": _utc(row.created_at).isoformat(),
            }
            for row in rows
        ],
        "prompts": [
            {
                "run_id": row.run_id,
                "generation": row.generation,
                "dsh_session_id": row.dsh_session_id,
                "request_id": row.request_id,
                "request_hash": row.request_hash,
                "prompt_hash": hashlib.sha256(row.prompt.encode("utf-8")).hexdigest(),
                "created_at": _utc(row.created_at).isoformat(),
            }
            for row in prompts
        ],
    }
    return _V2RecoveryBinding(
        run_id=run_id,
        dsh_session_id=link.dsh_session_id,
        generation=link.generation,
        request_hash=method.request_hash,
        task_request_hash=execution.task_request_hash,
        method_hash=method.method_hash,
        prompt_hash=prepared.integrity.prompt_hash,
        first_prompt_hash=hashlib.sha256(prompts[0].prompt.encode("utf-8")).hexdigest(),
        first_execution_request_hash=method.definition.policy_binding.first_execution_request_hash,
        execution_request_hash=execution.request_hash,
        deadline_at=link.deadline_at,
        max_tool_calls=link.max_tool_calls,
        model_step_timeout_ms=link.model_step_timeout_ms,
        native_fingerprint=recipe_hash(witness),
    )


def _load_binding(session: Session, run_id: str) -> DshRecoveryBinding:
    run = session.get(RunRecord, run_id, populate_existing=True)
    link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
    if run is None or run.status != "cancelled" or link is None:
        raise ValueError("dsh_product_cancel_not_authorized")
    if (
        link.deadline_at is None
        or link.max_tool_calls is None
        or link.model_step_timeout_ms is None
    ):
        raise ValueError("recovery_frozen_budget_missing")
    if _uses_v2(session, run_id):
        return _load_v2_binding(session, run_id)
    saved = read_task_request(session, run)
    if saved is None:
        raise ValueError("recovery_task_receipt_missing")
    task, task_hash, _source = saved
    prompt = session.get(DshSessionPromptRecord, (run_id, link.generation))
    first_prompt = session.get(DshSessionPromptRecord, (run_id, 1))
    if prompt is None or first_prompt is None:
        raise ValueError("recovery_prompt_missing")
    expected_session, expected_request = DshSessionLinkService.deterministic_ids(
        run_id,
        link.request_hash,
        link.generation,
    )
    _first_session, first_request_id = DshSessionLinkService.deterministic_ids(
        run_id,
        link.request_hash,
        1,
    )
    if (
        link.dsh_session_id != expected_session
        or prompt.dsh_session_id != expected_session
        or prompt.request_id != expected_request
        or prompt.request_hash != link.request_hash
        or prompt.generation != link.generation
        or first_prompt.dsh_session_id != expected_session
        or first_prompt.request_id != first_request_id
        or first_prompt.request_hash != link.request_hash
        or first_prompt.generation != 1
        or _utc(prompt.created_at) < _utc(first_prompt.created_at)
    ):
        raise ValueError("recovery_prompt_binding_mismatch")
    prompt_hash = read_prompt_integrity(session, prompt)
    first_prompt_hash = prompt_hash
    if first_prompt.generation != prompt.generation:
        first_prompt_hash = read_prompt_integrity(session, first_prompt)
    # The graph filters requirements and selects bounded context before the
    # first runtime call. Global Task and actual executed requests have separate
    # immutable receipts; their hashes must never be silently substituted.
    request = read_execution_request(session, first_prompt, task_hash)
    active_request = read_execution_request(session, prompt, task_hash)
    if research_request_hash(request) != link.request_hash:
        raise ValueError("recovery_execution_identity_mismatch")
    stable_fields = (
        "request_id",
        "run_id",
        "event_id",
        "trigger_snapshot_id",
        "domain_pack_ref",
        "role_profile_ref",
        "execution_mode",
        "research_mode",
        "pit_cutoff_at",
        "allowed_capabilities",
        "output_schema_ref",
        "deadline_at",
    )
    for actual in (request, active_request):
        if any(getattr(actual, name) != getattr(task, name) for name in stable_fields):
            raise ValueError("recovery_execution_task_scope_mismatch")
        budget = actual.execution_budget.model_dump(mode="json")
        original_budget = task.execution_budget.model_dump(mode="json")
        ceiling = original_budget.pop("max_tool_calls")
        allowance = budget.pop("max_tool_calls")
        if budget != original_budget or not 0 < allowance <= ceiling:
            raise ValueError("recovery_execution_task_budget_mismatch")
        originals = {item.requirement_id: item for item in task.evidence_requirements}
        for requirement in actual.evidence_requirements:
            original = originals.get(requirement.requirement_id)
            if original is None or (
                requirement.model_dump(exclude={"preferred_capabilities"})
                != original.model_dump(exclude={"preferred_capabilities"})
            ):
                raise ValueError("recovery_execution_requirement_mismatch")
            routes = set(original.preferred_capabilities) | set(original.allowed_fallbacks)
            if set(task.allowed_capabilities) == {"replay.research"}:
                routes.add("replay.research")
            if not set(requirement.preferred_capabilities) <= (
                routes & set(task.allowed_capabilities)
            ):
                raise ValueError("recovery_execution_capability_mismatch")
    if (
        request.current_round != 1
        or active_request.current_round > link.generation
        or active_request.execution_budget.max_tool_calls > request.execution_budget.max_tool_calls
    ):
        raise ValueError("recovery_execution_round_mismatch")
    row = session.scalar(
        select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role == METHOD_ROLE,
        )
    )
    if row is None:
        raise ValueError("recovery_method_receipt_missing")
    method: dict[str, Any] = ResearchMethodService._read(row)  # pyright: ignore[reportPrivateUsage]
    definition = method["definition"]
    if (
        row.output_id != "method_" + hashlib.sha256(run_id.encode()).hexdigest()[:32]
        or method["request_hash"] != link.request_hash
        or _utc(row.created_at) > _utc(first_prompt.created_at)
        or definition.get("runtime_id") != "dsh"
        or definition.get("upstream_identity") != json.loads(link.upstream_identity_json)
        or definition.get("domain_pack_ref") != request.domain_pack_ref
        or definition.get("role_profile_ref") != request.role_profile_ref
        or definition.get("execution_mode") != request.execution_mode
        or definition.get("research_mode") != request.research_mode
        or definition.get("allowed_capabilities") != sorted(set(request.allowed_capabilities))
        or definition.get("output_schema_ref") != request.output_schema_ref
    ):
        raise ValueError("recovery_method_binding_mismatch")
    # The original method and task attest the same initial ceiling. Do not use
    # current Pack values, wall time, workspace defaults, or legacy NULL fill.
    budget = request.execution_budget.model_dump(mode="json")
    if (
        definition.get("execution_budget") != budget
        or link.max_tool_calls != budget["max_tool_calls"]
        or link.model_step_timeout_ms != budget["per_model_step_timeout_seconds"] * 1000
        or as_utc(link.deadline_at) != request.deadline_at
    ):
        raise ValueError("recovery_frozen_budget_mismatch")
    # Canonical link validation checks the stored upstream identity, state and
    # numeric ceilings without importing a current provider configuration.
    DshSessionLinkService._view(link)  # pyright: ignore[reportPrivateUsage]
    return DshRecoveryBinding(
        run_id=run_id,
        dsh_session_id=link.dsh_session_id,
        generation=link.generation,
        request_hash=link.request_hash,
        task_request_hash=task_hash,
        method_hash=method["method_hash"],
        prompt_hash=prompt_hash,
        first_prompt_hash=first_prompt_hash,
        first_execution_request_hash=recipe_hash(request.model_dump(mode="json")),
        execution_request_hash=recipe_hash(active_request.model_dump(mode="json")),
        deadline_at=_utc(link.deadline_at),
        max_tool_calls=link.max_tool_calls,
        model_step_timeout_ms=link.model_step_timeout_ms,
    )


def _assert_status(status: DshSessionStatus, binding: DshRecoveryBinding) -> None:
    if (
        status.run_id != binding.run_id
        or status.dsh_session_id != binding.dsh_session_id
        or status.generation != binding.generation
    ):
        raise ValueError("recovery_status_identity_mismatch")


def _assert_result(result: DshSessionResult, binding: DshRecoveryBinding) -> None:
    if (
        result.run_id != binding.run_id
        or result.dsh_session_id != binding.dsh_session_id
        or result.generation != binding.generation
        or result.result_hash
        != hashlib.sha256(
            (result.final_response + "\0" + result.events_json).encode("utf-8")
        ).hexdigest()
    ):
        raise ValueError("recovery_result_identity_or_hash_mismatch")


class CancelledRunReconciler:
    """Fair cursor with bounded per-Run backoff; restart rediscovers DB gaps."""

    def __init__(
        self,
        projector: DshCancellationProjector,
        client: object,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.projector = projector
        self.client = client
        self._now = monotonic
        self._cursor = ""
        self._backoff: OrderedDict[str, tuple[float, int]] = OrderedDict()
        self._tick_lock = asyncio.Lock()

    def _defer(self, run_id: str) -> None:
        failures = min(self._backoff.get(run_id, (0, 0))[1] + 1, 5)
        self._backoff[run_id] = (self._now() + min(15 * (2**failures), 300), failures)
        self._backoff.move_to_end(run_id)
        while len(self._backoff) > 1024:
            self._backoff.popitem(last=False)

    async def tick(self, *, limit: int = 4, time_budget_seconds: float = 2) -> dict[str, Any]:
        if not 1 <= limit <= 64 or not 0 < time_budget_seconds <= 30:
            raise ValueError("recovery_tick_budget_invalid")
        counts: dict[str, Any] = dict(
            scanned=0,
            attempted=0,
            confirmed=0,
            pending=0,
            skipped=0,
            backoff=0,
            limitations={},
        )

        def limited(code: str) -> None:
            limitations = counts["limitations"]
            limitations[code] = limitations.get(code, 0) + 1

        if self._tick_lock.locked():
            limited("recovery_tick_busy")
            return counts
        if not isinstance(self.client, DshRecoveryHostClient):
            limited("recovery_host_unsupported")
            return counts
        client = self.client
        until = self._now() + time_budget_seconds

        def remaining() -> float:
            return max(0.0, until - self._now())

        async with self._tick_lock:
            cursor = self._cursor

            def page(session: Session) -> list[str]:
                query = (
                    select(DshSessionLinkRecord.run_id)
                    .join(
                        RunRecord,
                        RunRecord.run_id == DshSessionLinkRecord.run_id,
                    )
                    .where(
                        RunRecord.status == "cancelled",
                        DshSessionLinkRecord.state.not_in(TERMINAL_STATES),
                    )
                )
                rows = list(
                    session.scalars(
                        query.where(
                            DshSessionLinkRecord.run_id > cursor,
                        )
                        .order_by(DshSessionLinkRecord.run_id)
                        .limit(limit)
                    )
                )
                if not rows and cursor:
                    rows = list(
                        session.scalars(
                            query.order_by(
                                DshSessionLinkRecord.run_id,
                            ).limit(limit)
                        )
                    )
                return rows

            found = await self.projector.read(page, timeout_seconds=min(0.25, remaining()))
            if found.state != "ready" or found.value is None:
                limited(found.limitation or "recovery_scan_pending")
                return counts
            for run_id in found.value:
                self._cursor = run_id
                counts["scanned"] += 1
                if self._backoff.get(run_id, (0, 0))[0] > self._now():
                    counts["backoff"] += 1
                    continue
                if remaining() <= 0:
                    counts["pending"] += 1
                    limited("recovery_tick_budget_elapsed")
                    self._defer(run_id)
                    continue
                probe = await self.projector.read(
                    lambda session, target=run_id: _uses_v2(session, target),
                    timeout_seconds=min(0.25, remaining()),
                )
                if probe.state != "ready" or probe.value is None:
                    counts["skipped" if probe.state == "invalid" else "pending"] += 1
                    limited(probe.limitation or "recovery_binding_pending")
                    self._defer(run_id)
                    continue
                # Complete native proof is more expensive than the original
                # V1 reader. This recovery-only wait stays inside the caller's
                # tick budget; it is not a model deadline or a new permission.
                proof_wait = 1.0 if probe.value else 0.25
                binding_read = await self.projector.read(
                    lambda session, target=run_id: _load_binding(session, target),
                    timeout_seconds=min(proof_wait, remaining()),
                )
                if binding_read.state != "ready" or binding_read.value is None:
                    counts["skipped" if binding_read.state == "invalid" else "pending"] += 1
                    limited(binding_read.limitation or "recovery_binding_pending")
                    self._defer(run_id)
                    continue
                binding = binding_read.value
                if remaining() <= 0:
                    counts["pending"] += 1
                    limited("recovery_tick_budget_elapsed")
                    self._defer(run_id)
                    continue
                counts["attempted"] += 1

                async def verify_original(
                    original: DshRecoveryBinding = binding,
                    wait_ceiling: float = proof_wait,
                ) -> None:
                    if not isinstance(original, _V2RecoveryBinding):
                        return
                    checked = await self.projector.read(
                        lambda session: _load_binding(session, original.run_id) == original,
                        timeout_seconds=min(wait_ceiling, remaining()),
                    )
                    if checked.state == "invalid" or (
                        checked.state == "ready" and checked.value is not True
                    ):
                        raise ValueError("recovery_binding_changed")
                    if checked.state != "ready":
                        raise ValueError("recovery_binding_unavailable")

                try:
                    async with asyncio.timeout(remaining()):
                        await verify_original()
                        status = await client.recovery_status(binding)
                        await verify_original()
                        _assert_status(status, binding)
                        if status.state not in TERMINAL_STATES:
                            if not isinstance(binding, _V2RecoveryBinding):
                                unchanged_binding = await self.projector.read(
                                    lambda session, original=binding: (
                                        _load_binding(session, original.run_id) == original
                                    ),
                                    timeout_seconds=min(0.25, remaining()),
                                )
                                if unchanged_binding.state != "ready":
                                    raise ValueError("recovery_cancel_authorization_unavailable")
                                if unchanged_binding.value is not True:
                                    raise ValueError("recovery_binding_changed")
                            await verify_original()
                            status = await client.recovery_cancel(binding, "product_run_cancelled")
                            await verify_original()
                            _assert_status(status, binding)
                        if status.state not in TERMINAL_STATES:
                            raise ValueError("recovery_host_not_terminal")
                        if isinstance(binding, _V2RecoveryBinding) and status.state == "cancelled":
                            # This Web status is not the original Host completion.
                            # Read its authenticated callback proof; never mint
                            # terminal time/trace from the reduced status reply.
                            def authenticated_cancelled(
                                session: Session,
                                target: str = run_id,
                                original: DshRecoveryBinding = binding,
                            ) -> bool:
                                if _load_binding(session, target) != original:
                                    raise ValueError("recovery_binding_changed")
                                proof = read_authenticated_terminal_in_session(
                                    session, target, now=datetime.now(UTC),
                                )
                                return bool(
                                    proof is not None
                                    and proof.completion.run_id == original.run_id
                                    and proof.completion.dsh_session_id == original.dsh_session_id
                                    and proof.completion.generation == original.generation
                                    and proof.completion.terminal_status == "cancelled"
                                )

                            observed = await self.projector.read(
                                authenticated_cancelled,
                                timeout_seconds=min(proof_wait, remaining()),
                            )
                            if observed.state == "ready" and observed.value is True:
                                counts["confirmed"] += 1
                                self._backoff.pop(run_id, None)
                                continue
                            limited(observed.limitation or "recovery_awaiting_authenticated_completion")
                            counts["pending"] += 1
                            self._defer(run_id)
                            continue
                        result: DshSessionResult | None = None
                        if status.state == "completed":
                            await verify_original()
                            result = await client.recovery_result(binding)
                            await verify_original()
                            _assert_result(result, binding)
                            if result.last_seq < status.last_seq:
                                raise ValueError("recovery_result_sequence_regression")
                        if status.state == "failed" and status.error_code is None:
                            raise ValueError("recovery_failed_error_missing")
                        error = None
                        if status.state == "failed":
                            assert status.error_code is not None
                            error = DshBridgeError(
                                code=status.error_code,
                                message="DSH Host reported a failed turn",
                                retryable=False,
                            )
                        completion = DshSessionCompletion.model_validate(
                            {
                                "schema_version": "dsh-session-completion.v1",
                                "run_id": binding.run_id,
                                "dsh_session_id": binding.dsh_session_id,
                                "terminal_status": status.state,
                                "generation": binding.generation,
                                "last_seq": result.last_seq if result else status.last_seq,
                                "trace_ref": result.trace_ref if result else None,
                                "result_ref": f"dsh-host://runs/{run_id}/result"
                                if result
                                else None,
                                "result_hash": result.result_hash if result else None,
                                "completed_at": result.finished_at
                                if result
                                else status.observed_at,
                                "error": error,
                            }
                        )

                        def unchanged(
                            session: Session,
                            target: str = run_id,
                            original: DshRecoveryBinding = binding,
                        ) -> None:
                            if _load_binding(session, target) != original:
                                raise ValueError("recovery_binding_changed")

                        projected = await self.projector.project(
                            completion,
                            binding.request_hash,
                            timeout_seconds=min(proof_wait, remaining()),
                            binding_validator=unchanged,
                        )
                        if projected.state == "confirmed":
                            counts["confirmed"] += 1
                            self._backoff.pop(run_id, None)
                            continue
                        limited(projected.limitation or "recovery_projection_pending")
                except asyncio.CancelledError:
                    raise
                except TimeoutError:
                    limited("recovery_rpc_budget_elapsed")
                except ValueError as exc:
                    code = str(exc)
                    limited(
                        code
                        if code
                        in {
                            "recovery_status_identity_mismatch",
                            "recovery_result_identity_or_hash_mismatch",
                            "recovery_result_sequence_regression",
                            "recovery_cancel_authorization_unavailable",
                            "recovery_host_not_terminal",
                            "recovery_failed_error_missing",
                            "recovery_binding_changed",
                            "recovery_binding_unavailable",
                        }
                        else "recovery_response_invalid"
                    )
                except Exception:
                    limited("recovery_host_unavailable")
                counts["pending"] += 1
                self._defer(run_id)
        return counts
