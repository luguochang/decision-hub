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
from packages.kernel.decision_hub_kernel.application.event_watch import EventWatchService
from packages.kernel.decision_hub_kernel.application.fact_store import ResearchFactStore
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
        event_watches: EventWatchService | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.inner = inner
        self.links = links
        self.evidence = evidence
        self.facts = ResearchFactStore(evidence.database)
        self.observability = observability
        self.requirements = dict(requirements)
        self.event_watches = event_watches
        self.clock = clock

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        run_id, link, run_event_id = self._validate_session(query)
        try:
            effective_query = self._effective_query(
                query,
                link.deadline_at,
                expected_event_id=run_event_id,
            )
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
                result.completed_at if effective_query.mode == "live" else effective_query.cutoff_at
            )
            accepted = self.evidence.accept_candidates(
                run_id=run_id,
                capability_id=effective_query.capability_id,
                candidates=result.evidence_candidates,
                requirements=self.requirements,
                cutoff_at=persistence_cutoff,
            )
            accepted_facts = self.facts.accept_facts(
                run_id=run_id,
                capability_id=effective_query.capability_id,
                facts=result.facts or [],
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

        persisted = result.model_copy(
            update={"evidence_candidates": accepted, "facts": accepted_facts}
        )
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
    ) -> tuple[str, DshRunSessionLinkView, str]:
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
            if query.requested_event_offsets and query.event_id is None:
                raise ResearchCapabilityError(
                    "research_event_lineage_mismatch",
                    "event-window offsets require an explicit event_id",
                    origin="gateway",
                    capability_id=query.capability_id,
                    tool_call_id=query.request_id,
                )
            if query.event_id is not None and query.event_id != run.event_id:
                raise ResearchCapabilityError(
                    "research_event_lineage_mismatch",
                    "capability event_id does not match the linked Hub Run",
                    origin="gateway",
                    capability_id=query.capability_id,
                    tool_call_id=query.request_id,
                )
            run_event_id = run.event_id
        return link.run_id, link, run_event_id

    def _effective_query(
        self,
        query: ResearchCapabilityQuery,
        deadline_at: datetime | None,
        *,
        expected_event_id: str,
    ) -> ResearchCapabilityQuery:
        """Clamp live capability PIT to the accepted durable DSH deadline.

        DSH model output may contain a future cutoff because the prompt carries
        the run ceiling. The model must never be able to extend that ceiling;
        a legacy link without a persisted deadline is rejected rather than
        guessed. Freshness is still anchored to the trusted result completion
        time below, so the run ceiling is not used as an age calculation.
        """

        update: dict[str, object] = {}
        if query.event_id is not None:
            if query.event_id != expected_event_id:
                raise ResearchCapabilityError(
                    "research_event_lineage_mismatch",
                    "capability event_id does not match the linked Hub Run",
                    origin="gateway",
                    capability_id=query.capability_id,
                    tool_call_id=query.request_id,
                )
            if query.requested_event_offsets:
                if self.event_watches is None:
                    raise ResearchCapabilityError(
                        "research_event_watch_not_found",
                        "event-window query requires the EventWatch service",
                        origin="gateway",
                        capability_id=query.capability_id,
                        tool_call_id=query.request_id,
                    )
                watch = self.event_watches.get_watch_by_event_id(query.event_id)
                if watch is None:
                    raise ResearchCapabilityError(
                        "research_event_watch_not_found",
                        "event-window query has no durable EventWatch",
                        origin="gateway",
                        capability_id=query.capability_id,
                        tool_call_id=query.request_id,
                    )
                requested = tuple(query.requested_event_offsets)
                if any(offset not in watch.window_offsets for offset in requested):
                    raise ResearchCapabilityError(
                        "research_event_window_offset_invalid",
                        "event-window query requests an offset outside the durable watch",
                        origin="gateway",
                        capability_id=query.capability_id,
                        tool_call_id=query.request_id,
                    )
                samples = {
                    item.offset: item
                    for item in self.event_watches.list_event_samples(query.event_id)
                }
                selected = [samples[offset] for offset in requested if offset in samples]
                if not selected:
                    raise ResearchCapabilityError(
                        "research_event_watch_not_found",
                        "event-window query has no durable sample slots",
                        origin="gateway",
                        capability_id=query.capability_id,
                        tool_call_id=query.request_id,
                    )
                event_at = watch.scheduled_at.astimezone(UTC)
                window_start = min(item.target_at for item in selected).astimezone(UTC)
                window_end = max(item.target_at for item in selected).astimezone(UTC)
                for field, trusted in (
                    ("event_at", event_at),
                    ("window_start_at", window_start),
                    ("window_end_at", window_end),
                ):
                    supplied = getattr(query, field)
                    if supplied is not None and supplied.astimezone(UTC) != trusted:
                        raise ResearchCapabilityError(
                            "research_event_time_mismatch",
                            f"{field} conflicts with the durable EventWatch",
                            origin="gateway",
                            capability_id=query.capability_id,
                            tool_call_id=query.request_id,
                        )
                    update[field] = trusted
        if query.mode != "live":
            return query.model_copy(update=update) if update else query
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
        update["cutoff_at"] = effective_cutoff
        return query.model_copy(update=update)

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
            str(exc) if str(exc).startswith("research_") else "research_evidence_persist_failed"
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
        provider_attempts=error.provider_attempts or [],
    )
