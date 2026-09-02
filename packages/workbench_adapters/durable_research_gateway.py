from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from packages.contracts_py.decision_hub_contracts import (
    DshRunSessionLinkView,
    ErrorProvenance,
    EvidenceRequirement,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
    ResearchTraceEvent,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import (
    DshSessionLinkService,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.persistence.db import RunRecord, utcnow
from packages.kernel.decision_hub_kernel.ports.research import ResearchCapabilityGateway


class DurableResearchCapabilityGateway:
    """Persist capability progress at the MCP boundary before returning to DSH.

    This is a composition decorator around the existing deny-by-default
    gateway. It is deliberately not an additional MCP tool or Agent loop.
    """

    def __init__(
        self,
        inner: ResearchCapabilityGateway,
        links: DshSessionLinkService,
        evidence: ResearchEvidenceService,
        observability: ResearchObservabilityService,
        *,
        requirements: Mapping[str, EvidenceRequirement],
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.inner = inner
        self.links = links
        self.evidence = evidence
        self.observability = observability
        self.requirements = dict(requirements)
        self.clock = clock

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        run_id, link = self._validate_session(query)
        try:
            effective_query = self._effective_query(query, link.deadline_at)
        except ResearchCapabilityError as exc:
            await self._trace(
                query,
                event_type="tool_failed",
                status="failed",
                summary=f"Capability {query.capability_id} failed before execution.",
                error=exc.provenance(),
            )
            raise
        reservation = self.observability.reserve_tool_call(
            run_id=run_id,
            request_id=effective_query.request_id,
            research_session_id=effective_query.research_session_id,
            generation=effective_query.round,
            capability_id=effective_query.capability_id,
        )
        if reservation.outcome == "completed":
            if reservation.result is None:  # pragma: no cover - corrupt row guard
                raise RuntimeError("research_tool_result_missing")
            return reservation.result
        if reservation.outcome == "failed":
            if reservation.error is None:  # pragma: no cover - corrupt row guard
                raise RuntimeError("research_tool_error_missing")
            raise _error_from_provenance(reservation.error)
        if reservation.outcome == "running":
            raise ResearchCapabilityError(
                "research_tool_call_in_progress",
                "the idempotent capability request is already running",
                retryable=True,
                origin="gateway",
                cause_code="duplicate_in_progress",
                capability_id=effective_query.capability_id,
                tool_call_id=effective_query.request_id,
            )
        if reservation.outcome == "cancelled":
            raise ResearchCapabilityError(
                "research_tool_call_cancelled",
                "the idempotent capability request was cancelled",
                origin="gateway",
                cause_code="duplicate_cancelled",
                capability_id=effective_query.capability_id,
                tool_call_id=effective_query.request_id,
            )
        if reservation.outcome == "exhausted":
            error = ResearchCapabilityError(
                (
                    "research_tool_budget_unavailable"
                    if reservation.max_tool_calls is None
                    else "research_tool_budget_exhausted"
                ),
                (
                    "the durable tool-call ceiling is unavailable"
                    if reservation.max_tool_calls is None
                    else "the durable tool-call budget was exhausted before execution"
                ),
                origin="gateway",
                cause_code=(
                    "dsh_tool_budget_missing"
                    if reservation.max_tool_calls is None
                    else "max_tool_calls_reached"
                ),
                capability_id=effective_query.capability_id,
                tool_call_id=effective_query.request_id,
            )
            await self._trace(
                effective_query,
                event_type="tool_failed",
                status="denied",
                summary=f"Capability {effective_query.capability_id} was denied by budget.",
                error=error.provenance(),
            )
            raise error
        await self._trace(
            effective_query,
            event_type="tool_started",
            status="running",
            summary=f"Capability {effective_query.capability_id} started.",
        )
        try:
            result = await self.inner.execute(effective_query)
        except asyncio.CancelledError:
            # Cancellation is control flow owned by DSH/worker, not a provider
            # failure. The started trace remains as durable evidence of work.
            self.observability.cancel_tool_call(
                run_id=run_id, request_id=effective_query.request_id
            )
            raise
        except ResearchCapabilityError as exc:
            error = exc.with_context(
                capability_id=effective_query.capability_id,
                tool_call_id=effective_query.request_id,
            )
            self.observability.fail_tool_call(
                run_id=run_id,
                request_id=effective_query.request_id,
                error=error.provenance(),
            )
            await self._trace(
                effective_query,
                event_type="tool_failed",
                status="failed",
                summary=f"Capability {effective_query.capability_id} failed.",
                error=error.provenance(),
            )
            raise error from None
        except Exception as exc:
            error = ResearchCapabilityError(
                "research_capability_failed",
                "research capability adapter failed",
                retryable=True,
                origin="provider",
                cause_code=type(exc).__name__.lower(),
                capability_id=effective_query.capability_id,
                tool_call_id=effective_query.request_id,
            )
            self.observability.fail_tool_call(
                run_id=run_id,
                request_id=effective_query.request_id,
                error=error.provenance(),
            )
            await self._trace(
                effective_query,
                event_type="tool_failed",
                status="failed",
                summary=f"Capability {effective_query.capability_id} failed.",
                error=error.provenance(),
            )
            raise error from exc

        try:
            persistence_cutoff = (
                result.completed_at
                if effective_query.mode == "live"
                else effective_query.cutoff_at
            )
            accepted = self.evidence.accept_candidates(
                run_id=run_id,
                capability_id=effective_query.capability_id,
                candidates=result.evidence_candidates,
                requirements=self.requirements,
                cutoff_at=persistence_cutoff,
            )
        except asyncio.CancelledError:
            self.observability.cancel_tool_call(
                run_id=run_id, request_id=effective_query.request_id
            )
            raise
        except Exception as exc:
            error = self._persistence_error(effective_query, exc)
            self.observability.fail_tool_call(
                run_id=run_id,
                request_id=effective_query.request_id,
                error=error.provenance(),
            )
            await self._trace(
                effective_query,
                event_type="tool_failed",
                status="failed",
                summary=f"Capability {effective_query.capability_id} result was rejected.",
                error=error.provenance(),
            )
            raise error from exc

        persisted = result.model_copy(update={"evidence_candidates": accepted})
        self.observability.complete_tool_call(
            run_id=run_id,
            request_id=effective_query.request_id,
            result=persisted,
        )
        await self._trace(
            effective_query,
            event_type="tool_completed",
            status="succeeded",
            summary=(
                f"Capability {effective_query.capability_id} completed with "
                f"{len(accepted)} retained evidence item(s)."
            ),
        )
        return persisted

    def _validate_session(
        self, query: ResearchCapabilityQuery
    ) -> tuple[str, DshRunSessionLinkView]:
        link = self.links.get_by_session(query.research_session_id)
        if link is None:
            raise ResearchCapabilityError(
                "research_session_not_found",
                "research session is not linked to a durable Hub Run",
                origin="gateway",
                capability_id=query.capability_id,
                tool_call_id=query.request_id,
            )
        if link.state in {"completed", "failed", "cancelled"}:
            raise ResearchCapabilityError(
                "research_session_terminal",
                "research session is already terminal",
                origin="gateway",
                capability_id=query.capability_id,
                tool_call_id=query.request_id,
            )
        if query.round != link.generation:
            raise ResearchCapabilityError(
                "research_round_mismatch",
                "capability round does not match the durable DSH generation",
                origin="gateway",
                cause_code="generation_mismatch",
                capability_id=query.capability_id,
                tool_call_id=query.request_id,
            )
        with self.evidence.database.session() as session:
            run = session.get(RunRecord, link.run_id)
            if run is None:
                raise ResearchCapabilityError(
                    "research_run_not_found",
                    "linked Hub Run does not exist",
                    origin="gateway",
                    capability_id=query.capability_id,
                    tool_call_id=query.request_id,
                )
            if run.status in {"completed", "failed", "cancelled", "research_only", "rejected"}:
                raise ResearchCapabilityError(
                    "research_run_terminal",
                    "linked Hub Run is already terminal",
                    origin="gateway",
                    capability_id=query.capability_id,
                    tool_call_id=query.request_id,
                )
        return link.run_id, link

    @staticmethod
    def _effective_query(
        query: ResearchCapabilityQuery,
        deadline_at: datetime | None,
    ) -> ResearchCapabilityQuery:
        """Clamp live capability PIT to the accepted durable DSH deadline.

        DSH model output may contain a future cutoff because the prompt carries
        the run ceiling. The model must never be able to extend that ceiling;
        a legacy link without a persisted deadline is rejected rather than
        guessed. Freshness is still anchored to the trusted result completion
        time below, so the run ceiling is not used as an age calculation.
        """

        if query.mode != "live":
            return query
        if deadline_at is None:
            raise ResearchCapabilityError(
                "research_deadline_unavailable",
                "live capability requires a durable DSH run deadline",
                origin="gateway",
                cause_code="dsh_deadline_missing",
                capability_id=query.capability_id,
                tool_call_id=query.request_id,
            )
        if deadline_at.tzinfo is None:
            raise ResearchCapabilityError(
                "research_deadline_invalid",
                "durable DSH run deadline must be timezone-aware",
                origin="gateway",
                cause_code="dsh_deadline_timezone_missing",
                capability_id=query.capability_id,
                tool_call_id=query.request_id,
            )
        effective_cutoff = min(query.cutoff_at, deadline_at.astimezone(UTC))
        return query.model_copy(update={"cutoff_at": effective_cutoff})

    async def _trace(
        self,
        query: ResearchCapabilityQuery,
        *,
        event_type: str,
        status: str,
        summary: str,
        error: ErrorProvenance | None = None,
    ) -> None:
        event = ResearchTraceEvent.model_validate(
            {
                "schema_version": "research-trace-event.v1",
                "run_id": self.links.get_by_session(query.research_session_id).run_id,  # type: ignore[union-attr]
                "research_session_id": query.research_session_id,
                "sequence_no": 0,
                "event_type": event_type,
                "occurred_at": _aware(self.clock()),
                "stage": "acquiring_evidence",
                "summary": summary,
                "reference_type": "capability_call",
                "reference_id": query.request_id,
                "status": status,
                "error_code": error.error_code if error is not None else None,
                "error": error,
            }
        )
        self.observability.append_trace_once(event)

    @staticmethod
    def _persistence_error(
        query: ResearchCapabilityQuery, exc: Exception
    ) -> ResearchCapabilityError:
        error_code = (
            str(exc)
            if str(exc).startswith("research_")
            else "research_evidence_persist_failed"
        )
        return ResearchCapabilityError(
            error_code,
            "validated capability result could not be persisted",
            retryable=False,
            origin="gateway",
            cause_code=type(exc).__name__.lower(),
            capability_id=query.capability_id,
            tool_call_id=query.request_id,
        )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("durable_gateway_clock_must_be_aware")
    return value.astimezone(UTC)


def _error_from_provenance(error: ErrorProvenance) -> ResearchCapabilityError:
    return ResearchCapabilityError(
        error.error_code,
        "the idempotent capability request previously failed",
        retryable=error.retryable,
        origin=error.origin,
        cause_code=error.cause_code,
        capability_id=error.capability_id,
        tool_call_id=error.tool_call_id,
        deadline_ms=error.deadline_ms,
    )
