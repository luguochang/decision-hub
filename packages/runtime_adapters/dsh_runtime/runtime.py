from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import ValidationError

from packages.contracts_py.decision_hub_contracts import (
    ResearchSessionRequest,
    ResearchSessionResult,
    ResearchTraceEvent,
)
from packages.kernel.decision_hub_kernel.ports.research import ResearchTraceSink
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.errors import map_runtime_error

from .client import DshRuntimeConfig, DshSdkClient, DshSdkNotification, DshSdkRun
from .event_mapper import map_notifications
from .profile import (
    build_research_prompt,
    build_structured_repair_prompt,
    inspect_profile,
)
from .result_mapper import (
    map_session_result,
    synthesis_evidence_ref_errors,
    trusted_evidence_ids,
)


class DshResearchRuntime:
    runtime_id = "dsh"

    def __init__(
        self,
        config: DshRuntimeConfig | None = None,
        client: DshSdkClient | None = None,
    ) -> None:
        self.config = config or DshRuntimeConfig.from_env()
        self.profile = inspect_profile(self.config.profile_path)
        self.runtime_version = f"dsh-sdk-{self.config.sdk_version}"
        self.profile_ref = self.profile.profile_ref
        self._client = client or DshSdkClient(self.config)

    async def execute(
        self,
        request: ResearchSessionRequest,
        trace_sink: ResearchTraceSink | None = None,
    ) -> ResearchSessionResult:
        now = datetime.now(UTC)
        remaining = (request.deadline_at - now).total_seconds()
        if remaining <= 0:
            raise AgentExecutionError(
                "provider_timeout",
                "research session deadline elapsed before DSH execution",
                retryable=True,
                origin="orchestration",
                cause_code="deadline_elapsed",
                deadline_ms=round(request.execution_budget.total_deadline_seconds * 1000),
            )
        timeout = min(remaining, self.config.request_timeout_seconds)
        research_session_id = _session_id(request)
        prompt = build_research_prompt(request, research_session_id)
        started_at = datetime.now(UTC)
        try:
            async with asyncio.timeout(timeout):
                run = await self._client.run(prompt, session_id=research_session_id)
        except asyncio.CancelledError:
            # The product lifecycle owns cancellation and outer deadlines. Never
            # relabel cooperative cancellation as a provider/runtime failure.
            raise
        except AgentExecutionError:
            raise
        except TimeoutError as exc:
            await self._client.close()
            raise AgentExecutionError(
                "provider_timeout",
                "DSH research session exceeded its bounded deadline",
                retryable=True,
                origin="transport",
                cause_code="dsh_request_timeout",
                deadline_ms=round(timeout * 1000),
            ) from exc
        except BaseException as exc:
            raise map_runtime_error(
                exc,
                provider_id="dsh",
                fallback_code="dsh_runtime_failed",
            ) from exc
        finished_at = datetime.now(UTC)
        traces = _traces(run, request, finished_at)
        try:
            result = self._map_result(run, request, traces, started_at, finished_at)
        except AgentExecutionError as exc:
            if (
                not _is_repairable_synthesis_error(exc)
                or request.execution_budget.max_structured_repairs < 1
            ):
                raise
            validation_errors = _validation_errors(exc)
            if exc.error_code == "dsh_evidence_unattested":
                validation_errors = synthesis_evidence_ref_errors(run, request)
                if not validation_errors:
                    raise
            repair_run = await self._repair_structured_output(
                request,
                run,
                research_session_id,
                exc,
                validation_errors,
                trusted_evidence_ids(run, request),
            )
            repair_notifications = _new_notifications(
                run.notifications, repair_run.notifications
            )
            repair_traces = map_notifications(
                repair_notifications,
                run_id=request.run_id,
                research_session_id=repair_run.session_id,
                received_at=datetime.now(UTC),
            )
            if any(item.event_type == "tool_started" for item in repair_traces):
                raise AgentExecutionError(
                    exc.error_code,
                    "DSH synthesis repair attempted a forbidden tool call",
                    attempt=2,
                    cause_code="repair_tool_forbidden",
                ) from exc
            run = _merge_runs(run, repair_run, repair_notifications)
            finished_at = datetime.now(UTC)
            traces = _traces(run, request, finished_at)
            try:
                result = self._map_result(run, request, traces, started_at, finished_at)
            except AgentExecutionError as repaired_exc:
                if _is_repairable_synthesis_error(repaired_exc):
                    raise AgentExecutionError(
                        repaired_exc.error_code,
                        "DSH synthesis remained invalid after one bounded repair",
                        attempt=2,
                        origin=repaired_exc.origin,
                        cause_code=repaired_exc.cause_code,
                    ) from repaired_exc
                raise
        if trace_sink is not None:
            for trace in traces:
                await trace_sink.emit(trace)
        return result

    def _map_result(
        self,
        run: DshSdkRun,
        request: ResearchSessionRequest,
        traces: list[ResearchTraceEvent],
        started_at: datetime,
        finished_at: datetime,
    ) -> ResearchSessionResult:
        return map_session_result(
            run,
            request,
            traces,
            runtime_version=self.runtime_version,
            profile_ref=self.profile_ref,
            started_at=started_at,
            finished_at=finished_at,
        )

    async def _repair_structured_output(
        self,
        request: ResearchSessionRequest,
        initial_run: DshSdkRun,
        research_session_id: str,
        error: AgentExecutionError,
        validation_errors: list[dict[str, object]],
        allowed_evidence_ids: list[str],
    ) -> DshSdkRun:
        remaining = (request.deadline_at - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            raise AgentExecutionError(
                "provider_timeout",
                "research deadline elapsed before structured repair",
                retryable=True,
                attempt=2,
                origin="orchestration",
                cause_code="repair_deadline_elapsed",
            ) from error
        prompt = build_structured_repair_prompt(
            request,
            research_session_id,
            validation_errors,
            allowed_evidence_ids,
        )
        try:
            async with asyncio.timeout(min(remaining, self.config.request_timeout_seconds)):
                repaired = await self._client.run(prompt, session_id=research_session_id)
        except TimeoutError as exc:
            await self._client.close()
            raise AgentExecutionError(
                "provider_timeout",
                "DSH structured repair exceeded the remaining deadline",
                retryable=True,
                attempt=2,
                origin="transport",
                cause_code="dsh_repair_timeout",
                deadline_ms=round(remaining * 1000),
            ) from exc
        if repaired.session_id != initial_run.session_id:
            raise AgentExecutionError(
                "dsh_protocol_invalid",
                "DSH structured repair changed session identity",
                attempt=2,
            )
        return repaired

    async def close(self) -> None:
        await self._client.close()


def _session_id(request: ResearchSessionRequest) -> str:
    digest = hashlib.sha256(request.request_id.encode("utf-8")).hexdigest()[:12]
    run_suffix = request.run_id[-16:].replace("_", "-")
    return f"decision-research-{run_suffix}-{digest}"


def _is_repairable_synthesis_error(error: AgentExecutionError) -> bool:
    return error.error_code == "structured_output_invalid" or (
        error.error_code == "dsh_evidence_unattested"
        and error.cause_code == "synthesis_attestation"
    )


def _traces(
    run: DshSdkRun, request: ResearchSessionRequest, received_at: datetime
) -> list[ResearchTraceEvent]:
    return map_notifications(
        run.notifications,
        run_id=request.run_id,
        research_session_id=run.session_id,
        received_at=received_at,
    )


def _validation_errors(error: AgentExecutionError) -> list[dict[str, object]]:
    cause = error.__cause__
    if not isinstance(cause, ValidationError):
        return [{"loc": [], "type": "structured_output_invalid", "msg": str(error)}]
    errors: list[dict[str, object]] = []
    for item in cause.errors(include_url=False, include_input=False)[:20]:
        errors.append(
            {
                "loc": [str(part) for part in item["loc"]],
                "type": str(item["type"]),
                "msg": str(item["msg"]),
            }
        )
    return errors


def _notification_key(notification: DshSdkNotification) -> str:
    return json.dumps(
        {"method": notification.method, "payload": notification.payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _new_notifications(
    initial: Sequence[DshSdkNotification], repaired: Sequence[DshSdkNotification]
) -> tuple[DshSdkNotification, ...]:
    remaining: dict[str, int] = {}
    for item in initial:
        key = _notification_key(item)
        remaining[key] = remaining.get(key, 0) + 1
    result: list[DshSdkNotification] = []
    for item in repaired:
        key = _notification_key(item)
        count = remaining.get(key, 0)
        if count:
            remaining[key] = count - 1
        else:
            result.append(item)
    return tuple(result)


def _merge_runs(
    initial: DshSdkRun,
    repaired: DshSdkRun,
    repair_notifications: tuple[DshSdkNotification, ...],
) -> DshSdkRun:
    return DshSdkRun(
        session_id=initial.session_id,
        final_response=repaired.final_response,
        finish_reason=repaired.finish_reason,
        events=(*initial.events, *repaired.events),
        notifications=(*initial.notifications, *repair_notifications),
        session_root=repaired.session_root or initial.session_root,
    )
