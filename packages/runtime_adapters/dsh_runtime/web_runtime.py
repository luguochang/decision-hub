from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

from packages.contracts_py.decision_hub_contracts import (
    DshBridgeError,
    DshSessionCompletion,
    DshSessionPrompt,
    DshSessionResult,
    DshSessionSubmit,
    ErrorProvenance,
    ResearchSessionRequest,
    ResearchSessionResult,
    ResearchTraceEvent,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.ports.research import ResearchTraceSink
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError

from .client import DshSdkNotification, DshSdkRun
from .event_mapper import map_notifications
from .profile import build_trusted_web_research_prompt
from .result_mapper import map_evidence_only_result, map_session_result
from .web_host_client import DshHostClient


class DshWebResearchRuntime:
    """Run one product request through a persistent official DSH Web Host."""

    runtime_id = "dsh"
    profile_ref = "decision-research.web.v1"

    def __init__(
        self,
        links: DshSessionLinkService,
        client: DshHostClient,
        *,
        poll_interval_seconds: float = 0.25,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("dsh_web_poll_interval_invalid")
        self.links = links
        self.client = client
        self.poll_interval_seconds = poll_interval_seconds
        self.runtime_version = "dsh-web-unverified"

    async def execute(
        self,
        request: ResearchSessionRequest,
        trace_sink: ResearchTraceSink | None = None,
    ) -> ResearchSessionResult:
        readiness = await self.client.readiness()
        if not readiness.ready:
            raise AgentExecutionError(
                readiness.error_code or "dsh_host_not_ready",
                "DSH Web Host is not ready for a canonical research session",
                retryable=True,
                provider_id="dsh-web",
                origin="dsh",
                cause_code=readiness.error_code or "readiness_failed",
            )
        self.runtime_version = (
            f"dsh-web-{readiness.upstream_identity.source_version}"
            f"+{readiness.upstream_identity.plugin_build_hash[:12]}"
        )
        # The first submission fixes the durable run ceiling. Retries and
        # same-session generations must reuse it even when the caller rebuilds
        # a request with a fresh wall-clock deadline.
        existing_link = self.links.get(request.run_id)
        tool_calls_before = (
            existing_link.tool_calls_started if existing_link is not None else 0
        )
        submit = self._submission(
            request,
            deadline_at=(
                existing_link.deadline_at
                if existing_link is not None and existing_link.deadline_at is not None
                else request.deadline_at
            ),
            request_hash=(
                existing_link.request_hash if existing_link is not None else None
            ),
            max_tool_calls=(
                existing_link.max_tool_calls
                if existing_link is not None and existing_link.max_tool_calls is not None
                else request.execution_budget.max_tool_calls
            ),
        )
        existing_prompt = self.links.get_prompt(request.run_id, submit.generation)
        if existing_prompt is None:
            prompt = build_trusted_web_research_prompt(request)
            self.links.reserve(submit, readiness.upstream_identity)
            self.links.store_prompt(
                DshSessionPrompt(
                    schema_version="dsh-session-prompt.v1",
                    run_id=request.run_id,
                    dsh_session_id=submit.deterministic_session_id,
                    request_id=submit.deterministic_request_id,
                    request_hash=submit.request_hash,
                    generation=submit.generation,
                    prompt=prompt,
                )
            )
        else:
            if existing_prompt.request_hash != submit.request_hash:
                raise AgentExecutionError(
                    "dsh_run_request_conflict",
                    "A different canonical request is already linked to this product Run",
                    provider_id="dsh-web",
                    origin="orchestration",
                    cause_code="request_hash_conflict",
                )
            self.links.reserve(submit, readiness.upstream_identity)
        accepted = await self.client.submit(submit)
        self.links.accepted(accepted)
        fallback_used = False
        try:
            result_payload = await self._wait_for_result(request, submit)
        except asyncio.CancelledError:
            await self._cancel_best_effort(submit, "product_run_cancelled")
            raise
        run = _sdk_run(result_payload)
        traces = map_notifications(
            run.notifications,
            run_id=request.run_id,
            research_session_id=run.session_id,
            received_at=result_payload.finished_at,
        )
        try:
            result = map_session_result(
                run,
                request,
                traces,
                runtime_version=self.runtime_version,
                profile_ref=self.profile_ref,
                started_at=result_payload.started_at,
                finished_at=result_payload.finished_at,
            )
        except AgentExecutionError as exc:
            # A DSH model may return a malformed or unattested synthesis after
            # the capability gateway has already durably recorded valid facts.
            # Keep exact attestation fail-closed, but return those facts as a
            # degraded evidence-only result so the product can explain what
            # happened and the outer graph can continue a bounded round.
            if exc.error_code not in {"dsh_evidence_unattested", "structured_output_invalid"}:
                if trace_sink is not None:
                    for trace in traces:
                        await trace_sink.emit(trace)
                raise
            result = map_evidence_only_result(
                run,
                request,
                traces,
                runtime_version=self.runtime_version,
                profile_ref=self.profile_ref,
                started_at=result_payload.started_at,
                finished_at=result_payload.finished_at,
                failure_code=exc.error_code,
                failure_cause=exc.cause_code or "synthesis_invalid",
            )
            fallback_used = True
            if trace_sink is not None:
                for trace in traces:
                    await trace_sink.emit(trace)
                await trace_sink.emit(
                    ResearchTraceEvent.model_validate(
                        {
                            "schema_version": "research-trace-event.v1",
                            "run_id": request.run_id,
                            "research_session_id": run.session_id,
                            "sequence_no": 0,
                            "event_type": "session_stopped",
                            "occurred_at": result_payload.finished_at,
                            "stage": "synthesis",
                            "summary": (
                                "Synthesis was rejected at Evidence attestation; "
                                f"{len(result.evidence_candidates)} trusted Evidence "
                                "item(s) retained."
                            ),
                            "reference_type": "synthesis",
                            "reference_id": request.run_id,
                            "status": "degraded",
                            "error_code": exc.error_code,
                            "error": ErrorProvenance(
                                error_code=exc.error_code,
                                origin="orchestration",
                                cause_code=exc.cause_code or "synthesis_invalid",
                                capability_id=None,
                                tool_call_id=None,
                                retryable=False,
                                deadline_ms=None,
                            ),
                        }
                    )
                )
        durable_link = self.links.get(request.run_id)
        if durable_link is None:
            raise AgentExecutionError(
                "dsh_tool_budget_unavailable",
                "The durable DSH Run link disappeared before result accounting",
                provider_id="dsh-web",
                origin="orchestration",
                cause_code="dsh_session_link_missing",
            )
        executed_this_generation = durable_link.tool_calls_started - tool_calls_before
        if not 0 <= executed_this_generation <= request.execution_budget.max_tool_calls:
            raise AgentExecutionError(
                "dsh_tool_budget_accounting_invalid",
                "Durable capability execution count exceeded the generation allowance",
                provider_id="dsh-web",
                origin="orchestration",
                cause_code="durable_tool_count_out_of_bounds",
            )
        # DSH notifications include denied tool attempts. The product contract
        # counts only Gateway-reserved adapter executions; rejected attempts
        # remain visible in Trace/ErrorProvenance instead of inflating the cost ledger.
        result = result.model_copy(update={"total_tool_calls": executed_this_generation})
        if trace_sink is not None and not fallback_used:
            for trace in traces:
                await trace_sink.emit(trace)
        return result

    async def _wait_for_result(
        self, request: ResearchSessionRequest, submit: DshSessionSubmit
    ) -> DshSessionResult:
        while True:
            remaining = (request.deadline_at - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                await self._cancel_best_effort(submit, "product_deadline_elapsed")
                raise AgentExecutionError(
                    "provider_timeout",
                    "DSH Web session exceeded the product deadline",
                    retryable=True,
                    provider_id="dsh-web",
                    origin="orchestration",
                    cause_code="dsh_web_deadline_elapsed",
                    deadline_ms=round(request.execution_budget.total_deadline_seconds * 1000),
                )
            status = await self.client.status(submit)
            if status.state not in {"completed", "failed", "cancelled"}:
                self.links.observe(status)
                await asyncio.sleep(min(self.poll_interval_seconds, remaining))
                continue
            if status.state == "completed":
                result = await self.client.result(submit)
                _validate_result(result, submit)
                self.links.complete(
                    DshSessionCompletion(
                        schema_version="dsh-session-completion.v1",
                        run_id=submit.run_id,
                        dsh_session_id=submit.deterministic_session_id,
                        terminal_status="completed",
                        generation=submit.generation,
                        last_seq=result.last_seq,
                        trace_ref=result.trace_ref,
                        result_ref=f"dsh-host://runs/{submit.run_id}/result",
                        result_hash=result.result_hash,
                        completed_at=result.finished_at,
                        error=None,
                    )
                )
                return result
            completion = DshSessionCompletion(
                schema_version="dsh-session-completion.v1",
                run_id=submit.run_id,
                dsh_session_id=submit.deterministic_session_id,
                terminal_status=cast(str, status.state),  # type: ignore[arg-type]
                generation=submit.generation,
                last_seq=status.last_seq,
                trace_ref=None,
                result_ref=None,
                result_hash=None,
                completed_at=status.observed_at,
                error=(
                    DshBridgeError(
                        code=status.error_code or f"dsh_session_{status.state}",
                        message="DSH Web session reached a non-success terminal state",
                        retryable=status.state == "failed",
                    )
                    if status.state == "failed"
                    else None
                ),
            )
            self.links.complete(completion)
            raise AgentExecutionError(
                status.error_code or f"dsh_session_{status.state}",
                "DSH Web session did not complete successfully",
                retryable=status.state == "failed",
                provider_id="dsh-web",
                origin="dsh",
                cause_code=status.state,
            )

    async def _cancel_best_effort(self, submit: DshSessionSubmit, reason: str) -> None:
        try:
            status = await self.client.cancel(submit, reason)
            if status.state == "cancelled":
                self.links.complete(
                    DshSessionCompletion(
                        schema_version="dsh-session-completion.v1",
                        run_id=submit.run_id,
                        dsh_session_id=submit.deterministic_session_id,
                        terminal_status="cancelled",
                        generation=submit.generation,
                        last_seq=status.last_seq,
                        trace_ref=None,
                        result_ref=None,
                        result_hash=None,
                        completed_at=status.observed_at,
                        error=None,
                    )
                )
        except BaseException:
            # Cancellation is best effort; the durable link remains reconcilable.
            return

    @staticmethod
    def _submission(
        request: ResearchSessionRequest,
        *,
        deadline_at: datetime | None = None,
        request_hash: str | None = None,
        max_tool_calls: int | None = None,
    ) -> DshSessionSubmit:
        effective_request_hash = request_hash or _request_hash(request)
        session_id, request_id = DshSessionLinkService.deterministic_ids(
            request.run_id, effective_request_hash, request.current_round
        )
        return DshSessionSubmit(
            schema_version="dsh-session-submit.v1",
            run_id=request.run_id,
            request_hash=effective_request_hash,
            deterministic_session_id=session_id,
            deterministic_request_id=request_id,
            workspace_ref="decision-hub://workspace/default",
            prompt_ref=(
                f"hub://runs/{request.run_id}/prompts/{request.current_round}"
            ),
            agent_preset="decision-research",
            permission_ref="decision-hub://permissions/research-only",
            deadline_at=deadline_at or request.deadline_at,
            model_step_timeout_ms=(
                request.execution_budget.per_model_step_timeout_seconds * 1000
            ),
            max_tool_calls=max_tool_calls or request.execution_budget.max_tool_calls,
            generation=request.current_round,
        )

    async def close(self) -> None:
        await self.client.close()


def _request_hash(request: ResearchSessionRequest) -> str:
    # The bridge request identity belongs to one durable product Run. Evidence
    # rounds are mutable continuation state owned by the outer graph, so they
    # must not create a new DSH Session or collide with the persisted prompt.
    payload = request.model_dump(
        mode="json",
        exclude={
            "current_round",
            "evidence_refs",
            "input_evidence",
            "target_gaps",
            "repair_instructions",
            "deadline_at",
        },
    )
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_result(result: DshSessionResult, submit: DshSessionSubmit) -> None:
    if result.run_id != submit.run_id or result.dsh_session_id != submit.deterministic_session_id:
        raise AgentExecutionError(
            "dsh_protocol_invalid",
            "DSH Host result identity does not match the product Run",
            provider_id="dsh-web",
            origin="dsh",
            cause_code="result_identity_mismatch",
        )
    if result.generation != submit.generation:
        raise AgentExecutionError(
            "dsh_generation_conflict",
            "DSH Host result generation does not match the product Run",
            provider_id="dsh-web",
            origin="dsh",
            cause_code="result_generation_mismatch",
        )
    expected_hash = hashlib.sha256(
        (result.final_response + "\0" + result.events_json).encode("utf-8")
    ).hexdigest()
    if result.result_hash != expected_hash:
        raise AgentExecutionError(
            "dsh_result_hash_mismatch",
            "DSH Host result hash failed deterministic verification",
            provider_id="dsh-web",
            origin="dsh",
            cause_code="result_hash_mismatch",
        )


def _sdk_run(result: DshSessionResult) -> DshSdkRun:
    try:
        raw_events = json.loads(result.events_json)
    except json.JSONDecodeError as exc:
        raise AgentExecutionError(
            "dsh_protocol_invalid",
            "DSH Host result events are not valid JSON",
            provider_id="dsh-web",
            origin="dsh",
            cause_code="events_json_invalid",
        ) from exc
    if not isinstance(raw_events, list):
        raise AgentExecutionError(
            "dsh_protocol_invalid",
            "DSH Host result events must be a JSON array",
            provider_id="dsh-web",
            origin="dsh",
            cause_code="events_shape_invalid",
        )
    notifications: list[DshSdkNotification] = []
    for item in raw_events:
        if not isinstance(item, Mapping):
            raise AgentExecutionError(
                "dsh_protocol_invalid",
                "DSH Host result contains a non-object event",
                provider_id="dsh-web",
                origin="dsh",
                cause_code="event_shape_invalid",
            )
        method = item.get("method")
        payload = item.get("payload")
        if not isinstance(method, str) or not isinstance(payload, Mapping):
            raise AgentExecutionError(
                "dsh_protocol_invalid",
                "DSH Host result event is missing method or payload",
                provider_id="dsh-web",
                origin="dsh",
                cause_code="event_contract_invalid",
            )
        notifications.append(
            DshSdkNotification(method=method, payload=cast(Mapping[str, object], payload))
        )
    return DshSdkRun(
        session_id=result.dsh_session_id,
        final_response=result.final_response,
        finish_reason=result.finish_reason,
        events=(),
        notifications=tuple(notifications),
        session_root=None,
    )
