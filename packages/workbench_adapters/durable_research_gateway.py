from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from threading import Event

from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    DshRunSessionLinkView,
    ErrorProvenance,
    EvidenceRequirement,
    ResearchCapabilityInvocation,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
    ResearchTraceEvent,
)
from packages.kernel.decision_hub_kernel.application.daily_execution_binding import (
    daily_computation_scope,
    freeze_daily_execution_in_session,
    verify_daily_computation_in_session,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import (
    DshSessionLinkService,
)
from packages.kernel.decision_hub_kernel.application.event_watch import EventWatchService
from packages.kernel.decision_hub_kernel.application.fact_lineage import FactLineageStore
from packages.kernel.decision_hub_kernel.application.fact_store import ResearchFactStore
from packages.kernel.decision_hub_kernel.application.research_binding import (
    FrozenResearchBundle,
    has_prepared_bundle_in_session,
    read_research_bundle_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    CapabilityAdapterReturn,
    CapabilityAdapterReturnCollector,
    ResearchCapabilityError,
    ResearchEvidenceService,
    observe_capability_adapter_return,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    FrozenExecutionPreparation,
    read_bundle_identity_in_session,
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_invocation import (
    ResearchInvocationTiming,
)
from packages.kernel.decision_hub_kernel.application.research_method import ResearchMethodService
from packages.kernel.decision_hub_kernel.application.research_observability import (
    OwnedToolReservation,
    ResearchObservabilityService,
    requires_tool_admission_proof,
)
from packages.kernel.decision_hub_kernel.application.research_pack import ResearchPackService
from packages.kernel.decision_hub_kernel.application.research_task import read_task_request
from packages.kernel.decision_hub_kernel.decision.research_loop import (
    RESEARCH_ACQUISITION_CLOSING_ERROR,
    research_acquisition_close_at,
)
from packages.kernel.decision_hub_kernel.persistence.callback_source_lease import (
    callback_source_lease_scope,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    DshSessionPromptRecord,
    ResearchToolCallReservationRecord,
    ResearchTraceRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.ports.research import ResearchCapabilityGateway
from packages.provider_adapters.research.fact_pack import CryptoMacroFactPack
from packages.provider_adapters.research.raw_capture import (
    CaptureBatch,
    RawBodyArchive,
    capture_scope,
)
from packages.workbench_adapters.research_reuse import record_reusable_batch

# A finite asset comparison contract, separate from provider/venue admission.
_TASK_ASSET_CAPABILITIES = frozenset({
    "spot_market_facts", "market.crypto_derivatives", "market.crypto_crowding",
})
_TASK_ASSET_REQUIREMENTS = frozenset({
    "market_regime_context", "crypto_spot_confirmation", "derivatives_crowding",
})
_TASK_INSTRUMENT_ASSETS = {
    "BTC-USDT": "BTC", "BTC-USDT-SWAP": "BTC", "ETH-USDT": "ETH",
    "ETH-USDT-SWAP": "ETH",
}
_TASK_SYMBOL_ASSETS = {
    alias: asset
    for asset in ("BTC", "ETH")
    for alias in (
        asset, f"{asset}USDT", f"{asset}-USDT", f"{asset}/USDT",
        f"{asset}-USDT-SWAP", f"{asset}/USDT/SWAP",
    )
}
_HISTORICAL_TASK_ASSETS = {
    "crypto.btc_usdt.ohlcv_1h": "BTC",
    "crypto.btc_usdt.ohlcv_1d": "BTC",
    "crypto.eth_usdt.ohlcv_1d": "ETH",
    "crypto.btc_usdt_swap.funding_rate": "BTC",
    "crypto.btc_usdt_swap.open_interest": "BTC",
}


@dataclass(frozen=True)
class _SavedTaskScope:
    request_hash: str
    requirements: dict[str, EvidenceRequirement]
    scope_hash: str | None = None
    market: str | None = None
    instrument: str | None = None


@dataclass(frozen=True)
class _GatewayExecutionScope:
    # Only original immutable fields; no state, sequence or counter in this anchor.
    anchor_json: str
    prepared: FrozenExecutionPreparation


@dataclass(frozen=True)
class _PreparedGatewayAdmission:
    task_scope: _SavedTaskScope | None
    execution_scope: _GatewayExecutionScope
    query: ResearchCapabilityQuery
    daily_lease_owner: str | None


def _admission_scope_binding(scope: _PreparedGatewayAdmission) -> str:
    task = scope.task_scope
    return _canonical_gateway_json({
        "task": None if task is None else {
            "request_hash": task.request_hash, "scope_hash": task.scope_hash,
            "market": task.market, "instrument": task.instrument,
            "requirements": {key: value.model_dump(mode="json")
                             for key, value in task.requirements.items()},
        },
        "execution": scope.execution_scope.anchor_json,
        "query": scope.query.model_dump(mode="json"),
        "lease_owner": scope.daily_lease_owner,
    })


class _AdmissionStopped(RuntimeError):
    """Cooperative stop before provider entry; never a provider failure."""


class _AdmissionOwner:
    def __init__(self, clock):
        self.clock = clock
        self.cancelled = Event()
        self.deadline_at: datetime | None = None
        self.identity: OwnedToolReservation | None = None
        self.query: ResearchCapabilityQuery | None = None
        self.provider_started = False  # lifecycle fact only, not permission

    def check(self):
        if self.cancelled.is_set():
            raise _AdmissionStopped("research_admission_cancelled")
        if self.deadline_at is not None and _aware(self.clock()) >= self.deadline_at:
            raise _AdmissionStopped("research_execution_deadline_elapsed")

    def bind_deadline(self, deadline):
        deadline = _aware(deadline)
        self.deadline_at = deadline if self.deadline_at is None else min(self.deadline_at, deadline)
        self.check()

    def remember(self, identity):
        if self.identity is not None and self.identity != identity:
            raise ValueError("research_admission_multiple_owned_reservations")
        self.identity = identity

    def enter_provider(self):
        self.check()
        self.provider_started = True


@dataclass(frozen=True)
class _GatewayAdmission:
    run_id: str
    run_event_id: str
    effective_query: ResearchCapabilityQuery
    task_scope: _SavedTaskScope | None
    execution_scope: _GatewayExecutionScope | None
    daily_lease_owner: str | None
    admitted_query_hash: str
    timing_receipt: dict[str, str]
    live_invocation: ResearchCapabilityInvocation | None


async def _drain_admission(future, owner):
    cancelled = False
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            cancelled = True
            owner.cancelled.set()
        except BaseException:
            if not future.done():
                raise
            break
    try:
        return future.result(), None, cancelled
    except BaseException as error:
        return None, error, cancelled


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
        requirement_resolver: (
            Callable[[str | None], Mapping[str, EvidenceRequirement]] | None
        ) = None,
        event_watches: EventWatchService | None = None,
        clock: Callable[[], datetime] = utcnow,
        raw_archive: RawBodyArchive | None = None,
        parser_fingerprint: str = "unknown",
        frozen_source=None,
    ) -> None:
        self.inner = inner
        self.links = links
        self.evidence = evidence
        self.facts = ResearchFactStore(evidence.database)
        self.observability = observability
        self.requirements = dict(requirements)
        self.requirement_resolver = requirement_resolver
        self.event_watches = event_watches
        self.clock = clock
        self.raw_archive = raw_archive
        self.parser_fingerprint = parser_fingerprint
        self.frozen_source = frozen_source
        self.lineage = FactLineageStore(evidence.database)

    def scoped_query_catalog(
        self, catalog: dict[str, object], session_id: str,
    ) -> Mapping[str, object]:
        from packages.workbench_adapters.research_catalog import scoped_research_query_catalog

        if self.frozen_source is not None:
            frozen_catalog = self.frozen_source.scoped_query_catalog(catalog, session_id)
            if frozen_catalog is not None:
                return frozen_catalog
        return scoped_research_query_catalog(catalog, self.evidence.database, session_id)

    async def invoke_live(self, invocation: ResearchCapabilityInvocation):
        invocation = ResearchCapabilityInvocation.model_validate_json(invocation.model_dump_json())
        owner = _AdmissionOwner(self.clock)

        def prepare():
            query, live = self._prepare_live_invocation(invocation, owner)
            owner.query = query
            return self._prepare_admission(query, live, owner)

        return await self._run_admission(prepare, owner)

    def _prepare_live_invocation(self, invocation, owner):
        """All preflight Sessions/timing writers belong to this one worker."""
        owner.check()
        link = self.links.get_by_session(invocation.research_session_id)
        if link is None or link.deadline_at is None:
            raise ResearchCapabilityError(
                "research_invocation_timing_unavailable", "Linked execution deadline required",
                origin="gateway", capability_id=invocation.capability_id,
                tool_call_id=invocation.request_id,
            )
        mode = "live"
        cutoff_at = link.deadline_at
        if self.frozen_source is not None and self.frozen_source.assigned(link.run_id):
            # A signed fixed-world assignment uses its immutable request clock.
            # Native calls supply intent only; they cannot select replay or PIT.
            with self.evidence.database.session() as session:
                current = read_prepared_execution_in_session(session, link.run_id)
                if (current.link.run_id, current.link.dsh_session_id,
                    current.link.request_hash, current.link.generation,
                    current.link.deadline_at) != (
                    link.run_id, link.dsh_session_id, link.request_hash,
                    link.generation, link.deadline_at
                ):
                    raise ValueError("harness_frozen_invocation_binding_changed")
                request = current.execution.request
                mode = request.execution_mode
                cutoff_at = request.pit_cutoff_at if mode == "replay" else link.deadline_at
        body = invocation.model_dump(exclude={"schema_version"})
        query = ResearchCapabilityQuery.model_validate({
            **body, "schema_version": "research-capability-query.v1", "mode": mode,
            "round": link.generation, "observed_at": _aware(self.clock()),
            "cutoff_at": cutoff_at, "deadline_at": link.deadline_at,
        })
        with self.evidence.database.session() as session:
            bound = requires_tool_admission_proof(session, link.run_id)
        if bound:
            # Full V2 originals are authenticated together in the ensuing RO
            # preparation, before the timing+reservation writer is opened.
            return query, invocation
        # Legacy timing retains its original full lineage validation.
        self._validate_session(query)
        method = ResearchMethodService(self.evidence.database).get(link.run_id)
        if method is None or method["definition"].get("execution_mode") != "live":
            raise ResearchCapabilityError(
                "research_invocation_mode_unbound", "Frozen live method required",
                origin="gateway", capability_id=query.capability_id, tool_call_id=query.request_id,
            )
        try:
            owner.check()
            timing = ResearchInvocationTiming(
                self.evidence.database, clock=self.clock,
            ).bind(invocation, link)
            issued_at = timing["issued_at"]
            if not isinstance(issued_at, str):
                raise ValueError("research_invocation_timing_corrupt")
            query = query.model_copy(update={"observed_at": datetime.fromisoformat(issued_at)})
        except (ValueError, KeyError, TypeError) as exc:
            raise ResearchCapabilityError(
                "research_invocation_timing_invalid", "Saved call timing failed identity checks",
                origin="gateway", capability_id=query.capability_id, tool_call_id=query.request_id,
            ) from exc
        # Reuse all existing quota, idempotency, receipt and PIT enforcement.
        return query, None

    async def execute(
        self, query: ResearchCapabilityQuery, *,
        _live_invocation: ResearchCapabilityInvocation | None = None,
    ) -> ResearchCapabilityResult:
        query = ResearchCapabilityQuery.model_validate_json(query.model_dump_json())
        owner = _AdmissionOwner(self.clock)
        owner.query = query
        return await self._run_admission(
            lambda: self._prepare_admission(query, _live_invocation, owner), owner,
        )

    async def _run_admission(self, prepare, owner):
        """Join one thread-owned prefix, including cancellation/COMMIT races."""
        loop = asyncio.get_running_loop()

        def invoke_owned():
            owner.check()
            value = prepare()
            owner.check()
            return value

        async def cleanup():
            if owner.provider_started or owner.identity is None:
                return None, None, False
            return await _drain_admission(loop.run_in_executor(
                None, self.observability.cancel_owned_tool_call, owner.identity,
            ), owner)

        try:
            # Do not copy an inherited Session/ContextVar read scope or Windows
            # lease. Every Session/lease opens and closes in the owner thread.
            value, error, cancelled = await _drain_admission(
                loop.run_in_executor(None, invoke_owned), owner,
            )
            if cancelled:
                raise asyncio.CancelledError()
            if error is not None:
                raise error
            owner.check()
            if isinstance(value, ResearchCapabilityResult):
                return value  # authenticated cached terminal read, no new owner
            return await self._execute_admitted(value, owner)
        except asyncio.CancelledError as cancelled:
            owner.cancelled.set()
            _, cleanup_error, _ = await cleanup()
            if cleanup_error is not None:
                cancelled.add_note("research_admission_cleanup_unconfirmed; exact reservation retained; no automatic retry")
            raise
        except _AdmissionStopped as stopped:
            _, cleanup_error, cancelled = await cleanup()
            if cancelled:
                error = asyncio.CancelledError()
                if cleanup_error is not None:
                    error.add_note("research_admission_cleanup_unconfirmed; exact reservation retained; no automatic retry")
                raise error
            if cleanup_error is not None:
                raise RuntimeError("research_admission_cleanup_unconfirmed") from cleanup_error
            if owner.query is None:
                raise
            raise self._execution_error(owner.query, str(stopped)) from stopped
        except Exception as original:
            # A post-COMMIT trace/pre-provider failure still owns the new row.
            # Preserve that error; cleanup must not replace it or detach.
            _, cleanup_error, cancelled = await cleanup()
            if cleanup_error is not None:
                original.add_note("research_admission_cleanup_unconfirmed; exact reservation retained; no automatic retry")
            if cancelled:
                cancellation = asyncio.CancelledError()
                if cleanup_error is not None:
                    cancellation.add_note("research_admission_cleanup_unconfirmed; exact reservation retained; no automatic retry")
                raise cancellation from original
            raise

    def _prepare_admission(self, query, _live_invocation, owner):
        with callback_source_lease_scope(
            owner.check, defer_unselected_recipe_archives=True,
        ) as source_lease:
            return self._prepare_admission_owned(query, _live_invocation, owner, source_lease)

    def _prepare_admission_owned(self, query, _live_invocation, owner, source_lease):
        """Unchanged original validation/reservation policy, on one sync owner."""
        owner.check()
        daily_lease_owner: str | None = None
        admission_originals = None
        try:
            run_id, link, run_event_id = self._validate_session(query)
            if query.round != link.generation:
                query = query.model_copy(update={"round": link.generation})
            with self.evidence.database.session() as session:
                bound = requires_tool_admission_proof(session, run_id)
            if bound:
                def prepare_scope(session, prepared):
                    task = self._task_scope_in_session(session, run_id, query)
                    execution = self._execution_scope_in_session(
                        session, run_id, query, prepared=prepared)
                    if execution is None:
                        raise ValueError("research_execution_admission_binding_missing")
                    effective = self._effective_query(
                        query, link.deadline_at, expected_event_id=run_event_id,
                        frozen_scope=execution,
                    )
                    run = session.get(RunRecord, run_id, populate_existing=True)
                    lease_owner = run.lease_owner if run is not None and run.status == "running" else None
                    scope = _PreparedGatewayAdmission(task, execution, effective, lease_owner)
                    digest = hashlib.sha256(_canonical_gateway_json(
                        effective.model_dump(mode="json")).encode("utf-8")).hexdigest()
                    return scope, _admission_scope_binding(scope), digest

                admission_originals = self.observability.prepare_tool_admission(
                    run_id=run_id, request_id=query.request_id,
                    research_session_id=query.research_session_id, generation=link.generation,
                    capability_id=query.capability_id, prepare_scope=prepare_scope,
                    checkpoint=owner.check, source_lease=source_lease,
                )
                scope = admission_originals.value.scope
                task_scope, execution_scope = scope.task_scope, scope.execution_scope
                daily_lease_owner = scope.daily_lease_owner
            else:
                with self.evidence.database.session() as session:
                    task_scope = self._task_scope_in_session(session, run_id, query)
                    execution_scope = self._execution_scope_in_session(session, run_id, query)
                    current_run = session.get(RunRecord, run_id, populate_existing=True)
                    if current_run is not None and current_run.status == "running":
                        daily_lease_owner = current_run.lease_owner
        except (ResearchCapabilityError, ValueError, KeyError, TypeError) as exc:
            error = (exc if isinstance(exc, ResearchCapabilityError)
                     else self._execution_error(query, getattr(exc, "error_code", str(exc))))
            # A linked session has a durable run to attach the rejection to.
            # Unknown sessions intentionally do not create a fabricated trace
            # row, but all other lineage/terminal rejects remain auditable.
            if self.links.get_by_session(query.research_session_id) is not None:
                try:
                    self._trace_sync(
                        query,
                        event_type="tool_failed",
                        status="denied",
                        summary=f"Capability {query.capability_id} was rejected before execution.",
                        error=error.provenance(),
                        reference_id=f"{query.request_id}:rejected:{error.error_code}",
                    )
                except Exception as audit_error:
                    # Preserve the refusal even if separate audit storage is
                    # unavailable; never convert it into an allowed/retried call.
                    error.add_note("research_denial_audit_unknown:" + type(audit_error).__name__)
            if error is exc:
                raise
            raise error from exc
        # ``round`` is model supplied metadata.  The durable DSH generation is
        # the trusted lineage boundary for a capability call.  In a live run
        # the model may continue the same generation after a tool result and
        # emit the next internal loop number (for example round=2 while the
        # durable generation is still 1).  Rejecting that value used to stop a
        # run after otherwise valid evidence had already been admitted.  Keep
        # the session/event identity checks strict, but canonicalize the
        # untrusted round before routing, persistence and candidate validation.
        # This preserves the PIT and session guarantees without confusing an
        # internal DSH loop with a new durable generation.
        if query.round != link.generation:
            query = query.model_copy(update={"round": link.generation})
        timing_receipt: dict[str, str] = {}
        admitted_query_hash = ""
        try:
            effective_query = (admission_originals.value.scope.query
                               if admission_originals is not None else self._effective_query(
                query,
                link.deadline_at,
                expected_event_id=run_event_id,
                frozen_scope=execution_scope,
            ))
        except ResearchCapabilityError as exc:
            self._trace_sync(
                query,
                event_type="tool_failed",
                status="failed",
                summary=f"Capability {query.capability_id} failed before execution.",
                error=exc.provenance(),
            )
            raise
        def check_admission(session: Session, prepared: FrozenExecutionPreparation) -> str:
            nonlocal effective_query, admitted_query_hash
            owner.check()
            if admission_originals is not None:
                issued = admission_originals.value
                scope = issued.scope
                if (type(scope) is not _PreparedGatewayAdmission
                        or issued.prepared is not prepared
                        or _admission_scope_binding(scope) != issued.scope_binding):
                    raise self._execution_error(effective_query,
                                                "research_execution_admission_binding_changed")
                current_task, current = scope.task_scope, scope.execution_scope
            else:
                current_task = self._task_scope_in_session(session, run_id, effective_query)
                current = self._execution_scope_in_session(
                    session, run_id, effective_query, prepared=prepared)
            if current_task != task_scope:
                raise self._task_scope_error(
                    effective_query, "research_task_request_changed_before_call",
                )
            if (execution_scope is None or current is None
                    or current.anchor_json != execution_scope.anchor_json):
                raise self._execution_error(effective_query,
                                            "research_execution_anchor_changed_before_call")
            existing = session.get(ResearchToolCallReservationRecord,
                (run_id, effective_query.request_id), populate_existing=True)
            if existing is None:
                owner.bind_deadline(prepared.execution.request.deadline_at)
            if existing is None and _aware(self.clock()) >= prepared.execution.request.deadline_at:
                raise self._execution_error(effective_query, "research_execution_deadline_elapsed")
            if existing is None and prepared.execution.request.execution_mode == "live":
                admission_at = _aware(self.clock())
                # Keep the original absolute deadline cause ahead of phase refusal.
                if admission_at >= prepared.execution.request.deadline_at:
                    raise self._execution_error(effective_query, "research_execution_deadline_elapsed")
                if admission_at >= research_acquisition_close_at(prepared.execution.request):
                    raise ResearchCapabilityError(
                        RESEARCH_ACQUISITION_CLOSING_ERROR,
                        "New evidence acquisition stopped to reserve original time for synthesis and review",
                        retryable=False, origin="gateway", cause_code="original_closing_time_reserved",
                        capability_id=effective_query.capability_id,
                        tool_call_id=effective_query.request_id,
                    )
            if _live_invocation is not None:
                timing = ResearchInvocationTiming(self.evidence.database, clock=self.clock
                    ).bind_in_session(session, _live_invocation, prepared.link,
                                      allow_create=existing is None)
                timing_receipt["identity"] = timing.raw_identity_json
                effective_query = effective_query.model_copy(update={
                    "observed_at": timing.issued_at,
                })
            elif effective_query.mode == "live":
                raise self._execution_error(effective_query, "research_invocation_v2_required")
            timing_identity = timing_receipt.get("identity")
            admitted_query_hash = hashlib.sha256(_canonical_gateway_json({
                "schema_version": "research-tool-query-binding.v2",
                "query": effective_query.model_dump(mode="json"),
                "admitted_anchor": json.loads(current.anchor_json),
                "timing_identity": json.loads(timing_identity) if timing_identity else None,
            }).encode("utf-8")).hexdigest()
            owner.check()
            return admitted_query_hash

        def remember(identity):
            # This callback runs only after this call's successful own COMMIT.
            owner.remember(identity)
            if link.deadline_at is not None:
                owner.bind_deadline(link.deadline_at)

        try:
            owner.check()
            admitted_query_hash = hashlib.sha256(_canonical_gateway_json(
                effective_query.model_dump(mode="json"),
            ).encode("utf-8")).hexdigest()
            reserve = partial(self.observability.reserve_tool_call,
                run_id=run_id, request_id=effective_query.request_id,
                research_session_id=effective_query.research_session_id,
                generation=effective_query.round, capability_id=effective_query.capability_id,
                query_hash=admitted_query_hash,
                checkpoint=owner.check, on_new_reservation=remember,
            )
            reservation = (reserve(admission_check=check_admission)
                           if execution_scope is not None and admission_originals is None else
                           reserve(admission_check=check_admission,
                                   admission_originals=admission_originals)
                           if admission_originals is not None else reserve())
        except (ResearchCapabilityError, ValueError, KeyError, TypeError) as exc:
            # This is a verified admission refusal, before any adapter entry.
            # Preserve the refusal even if its separate audit storage fails.
            error = (exc if isinstance(exc, ResearchCapabilityError)
                     else self._execution_error(effective_query,
                         getattr(exc, "error_code", "research_execution_admission_invalid")))
            original_cause = error.__cause__ if error is exc else exc
            try:
                self._trace_sync(effective_query, event_type="tool_failed", status="denied",
                    summary="Capability refused by the original execution admission binding.",
                    error=error.provenance(),
                    reference_id=f"{effective_query.request_id}:rejected:{error.error_code}")
            except Exception as audit_error:
                error.add_note("research_denial_audit_unknown:" + type(audit_error).__name__)
                raise error from original_cause
            if error is exc:
                raise
            raise error from exc
        if reservation.outcome == "denied":
            if reservation.error is None:  # pragma: no cover - corrupt view guard
                raise RuntimeError("research_tool_admission_error_missing")
            error = _error_from_provenance(reservation.error)
            self._trace_sync(
                effective_query, event_type="tool_failed", status="denied",
                summary=(
                    f"Capability {effective_query.capability_id} was rejected before execution."
                ),
                error=reservation.error,
                reference_id=f"{effective_query.request_id}:rejected:{error.error_code}",
            )
            raise error
        if reservation.outcome == "conflict":
            error = ResearchCapabilityError(
                "research_tool_request_conflict",
                "tool request id was reused with a different or unverifiable query",
                origin="gateway",
                cause_code="idempotency_payload_mismatch",
                capability_id=effective_query.capability_id,
                tool_call_id=effective_query.request_id,
            )
            self._trace_sync(
                effective_query,
                event_type="tool_failed",
                status="denied",
                summary="Capability request rejected: reused call identity with changed query.",
                error=error.provenance(),
                reference_id=f"{effective_query.request_id}:rejected:request_conflict",
            )
            raise error
        if reservation.outcome == "completed":
            try:
                with self.evidence.database.session() as session:
                    self._read_adapter_return_in_session(
                        session, run_id, effective_query, admitted_query_hash)
            except (ValueError, KeyError, TypeError) as exc:
                raise ResearchCapabilityError(
                    "research_adapter_return_integrity_invalid",
                    "Original adapter return audit failed integrity checks",
                    origin="gateway", cause_code=str(exc),
                    capability_id=effective_query.capability_id,
                    tool_call_id=effective_query.request_id) from exc
            if reservation.result is None:  # pragma: no cover - corrupt row guard
                raise RuntimeError("research_tool_result_missing")
            if reservation.error is not None:
                # Completed is the actual adapter/cost fact. A prior semantic
                # rejection stays rejected on exact replay, without new writes.
                raise _error_from_provenance(reservation.error)
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
            self._trace_sync(
                effective_query,
                event_type="tool_failed",
                status="denied",
                summary=f"Capability {effective_query.capability_id} was denied by budget.",
                error=error.provenance(),
            )
            raise error
        self._trace_sync(
            effective_query,
            event_type="tool_started",
            status="running",
            summary=f"Capability {effective_query.capability_id} started.",
        )
        owner.check()
        return _GatewayAdmission(run_id, run_event_id, effective_query, task_scope,
            execution_scope, daily_lease_owner, admitted_query_hash, timing_receipt,
            _live_invocation)

    async def _execute_admitted(self, admission, owner):
        # Only DTO/scalar snapshots cross the thread boundary. Adapter clients,
        # daily/return/capture ContextVars and async awaits stay on this loop.
        run_id, run_event_id = admission.run_id, admission.run_event_id
        effective_query = admission.effective_query
        task_scope, execution_scope = admission.task_scope, admission.execution_scope
        daily_lease_owner = admission.daily_lease_owner
        admitted_query_hash, timing_receipt = admission.admitted_query_hash, admission.timing_receipt
        _live_invocation = admission.live_invocation
        scope_error: ResearchCapabilityError | None = None
        semantic_failure = False
        capture = (
            CaptureBatch(
                context_id=f"research:{run_id}:{effective_query.request_id}",
                adapter_version=self.parser_fingerprint,
                archive=self.raw_archive,
                persist=self.lineage.record_capture,
            )
            if self.raw_archive is not None
            else None
        )
        collector = CapabilityAdapterReturnCollector()
        frozen_return = None
        try:
            with daily_computation_scope(
                run_id=run_id, query=effective_query, reservation_query_hash=admitted_query_hash,
                expected_lease_owner=daily_lease_owner,
            ) as daily_collector:
                with observe_capability_adapter_return() as collector:
                    with capture_scope(capture) if capture is not None else nullcontext():
                        if self.frozen_source is not None and self.frozen_source.assigned(run_id):
                            if capture is None:
                                raise ValueError("harness_frozen_reuse_capture_context_required")
                            # Apply the same physical invocation-time projection
                            # as the ordinary adapter boundary. Preserve the
                            # admitted query bytes/hash and the frozen world's PIT.
                            server_received_at = _aware(self.clock())
                            adapter_query = effective_query.model_copy(update={
                                "requested_observed_at": effective_query.observed_at,
                                "observed_at": server_received_at,
                            })
                            owner.enter_provider()
                            frozen_return = self.frozen_source.execute(run_id, adapter_query)
                            capture.reused_evidence.update(frozen_return.reused_evidence)
                            capture.reused_facts.update(frozen_return.reused_facts)
                            result = collector.capture(adapter_query, frozen_return.result, server_received_at)
                        else:
                            owner.enter_provider()
                            result = await self.inner.execute(effective_query)
        except _AdmissionStopped:
            raise
        except asyncio.CancelledError as exc:
            if collector.returned:
                error = ResearchCapabilityError(
                    "research_result_admission_cancelled",
                    "Owner cancellation after adapter return",
                    origin="gateway", cause_code="owner_cancel_after_adapter_return",
                    capability_id=effective_query.capability_id,
                    tool_call_id=effective_query.request_id)
                self._retain_adapter_rejection(
                    run_id, effective_query, admitted_query_hash, collector, error, control=exc)
                raise
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
            if collector.returned:
                self._retain_adapter_rejection(
                    run_id, effective_query, admitted_query_hash, collector, error)
                raise error from exc.__cause__
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
            if collector.returned:
                self._retain_adapter_rejection(
                    run_id, effective_query, admitted_query_hash, collector, error)
                raise error from exc
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
                frozen_return.pit_cutoff_at if frozen_return is not None else
                result.completed_at if effective_query.mode == "live" else effective_query.cutoff_at
            )
            facts = result.facts or []
            active_requirements = (
                task_scope.requirements if task_scope is not None
                else self._active_requirements(run_event_id, run_id=run_id)
            )
            # Evidence and facts are one durable unit. If fact validation or
            # insertion fails, the evidence insert is rolled back as well.
            # The same write transaction orders result admission against owner
            # cancellation and keeps successful work, cost and late audit intact.
            with self.evidence.database.session() as session:
                if session.bind is not None and session.bind.dialect.name == "sqlite":
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                session.get(RunRecord, run_id, with_for_update=True, populate_existing=True)
                cancellation = self.observability.cancellation_cutoff_in_session(session, run_id)
                raw_audit_anchor = ""
                if collector.observation is not None:
                    raw_audit_anchor = self._freeze_adapter_return_in_session(
                        session, run_id, effective_query, admitted_query_hash,
                        collector.observation)
                if execution_scope is not None:
                    try:
                        current_execution = self._execution_scope_in_session(
                            session, run_id, effective_query,
                        )
                        if (current_execution is None or current_execution.anchor_json
                                != execution_scope.anchor_json):
                            raise self._execution_error(effective_query,
                                "research_execution_anchor_changed_during_call")
                        if (_live_invocation is not None and _aware(self.clock())
                                >= execution_scope.prepared.execution.request.deadline_at):
                            raise self._execution_error(effective_query,
                                "research_execution_deadline_elapsed")
                        if _live_invocation is not None:
                            timing = ResearchInvocationTiming(
                                self.evidence.database, clock=self.clock).bind_in_session(
                                    session, _live_invocation, current_execution.prepared.link,
                                    allow_create=False)
                            if (timing.raw_identity_json != timing_receipt.get("identity")
                                    or timing.issued_at != effective_query.observed_at):
                                raise self._execution_error(effective_query,
                                    "research_invocation_timing_changed_during_call")
                    except ResearchCapabilityError as exc:
                        scope_error = exc
                    except (ValueError, TypeError, KeyError) as exc:
                        scope_error = self._execution_error(effective_query, str(exc))
                if task_scope is not None:
                    try:
                        current_scope = self._task_scope_in_session(
                            session, run_id, effective_query,
                        )
                        if (current_scope is None
                                or current_scope != task_scope):
                            raise self._task_scope_error(
                                effective_query, "research_task_request_changed_during_call",
                            )
                    except ResearchCapabilityError as exc:
                        scope_error = exc
                # A successful adapter call cannot become an unexecuted or free
                # call when its saved Task changes. Keep the actual raw result
                # and cost in the original reservation, without new admission.
                persisted = result
                if scope_error is None:
                    try:
                        # Only semantic writes roll back. The actual successful
                        # capability output/cost completes in the outer writer.
                        with session.begin_nested():
                            daily_witness = None
                            if frozen_return is None and (daily_collector.witness is not None or any(
                                "daily_execution_receipt_json" in fact.attributes for fact in facts
                            )):
                                if self.raw_archive is None or collector.observation is None:
                                        raise ValueError(
                                            "daily_execution_actual_archive_return_missing"
                                        )
                                daily_witness = verify_daily_computation_in_session(
                                    session, collector=daily_collector, result=result,
                                        raw_adapter_return=collector.observation,
                                        archive=self.raw_archive,
                                )
                            accepted = self.evidence.accept_candidates_in_session(
                                session, run_id=run_id, capability_id=effective_query.capability_id,
                                candidates=result.evidence_candidates,
                                requirements=active_requirements,
                                cutoff_at=persistence_cutoff, facts=facts,
                            )
                            session.flush()
                            self.lineage.bind_parsed(
                                session, evidence_ids=[item.evidence_id for item in accepted],
                                captures=capture.captures if capture else (),
                                parser_version=self.parser_fingerprint,
                                reused=capture.reused_evidence if capture else None,
                            )
                            fact_writer = self.frozen_source.persist_facts if frozen_return is not None else self.facts.accept_facts_in_session
                            accepted_facts = fact_writer(
                                session, run_id=run_id, capability_id=effective_query.capability_id,
                                facts=facts, cutoff_at=persistence_cutoff,
                                lineage_reuse=capture.reused_facts if capture else None,
                            )
                            persisted = result.model_copy(
                                update={"evidence_candidates": accepted, "facts": accepted_facts})
                            if daily_witness is not None:
                                assert self.raw_archive is not None
                                assert collector.observation is not None
                                session.flush()
                                freeze_daily_execution_in_session(
                                    session, collector=daily_collector, witness=daily_witness,
                                    raw_adapter_return=collector.observation, result=persisted,
                                    archive=self.raw_archive, raw_audit_anchor=raw_audit_anchor,
                                    recorded_at=_aware(self.clock()),
                                    admission_authorized=(
                                        cancellation is None and daily_lease_owner is not None
                                    ),
                                )
                            if cancellation is None and frozen_return is None:
                                record_reusable_batch(
                                    session, run_id=run_id, batch=capture, result=persisted,
                                )
                    except Exception as exc:
                        persisted = result
                        semantic_failure = True
                        scope_error = self._persistence_error(effective_query, exc)
                self.observability.complete_tool_call_in_session(
                    session, run_id=run_id, request_id=effective_query.request_id,
                    result=persisted,
                    semantic_error=(scope_error.provenance()
                                    if execution_scope is not None and scope_error else None),
                )
                self.observability.append_trace_in_session(
                    session,
                    self._trace_event(
                        effective_query, run_id=run_id, event_type="tool_completed",
                        status="succeeded",
                        summary=(
                            f"Capability {effective_query.capability_id} completed with "
                            f"{len(persisted.evidence_candidates)} "
                            + ("raw candidate item(s); semantic admission denied."
                                 if scope_error is not None else "retained evidence item(s).")
                            + raw_audit_anchor
                        ),
                    ),
                    once=True,
                )
                if scope_error is not None:
                    scope_cutoff_note = ""
                    if cancellation is not None:
                        scope_cutoff = (
                            f"accepted cancellation command {cancellation.request_id} at "
                            f"{cancellation.created_at.isoformat()}"
                            if cancellation.created_at is not None
                            else (
                                "cancellation cutoff unknown "
                                "(no verified original command receipt)"
                            )
                        )
                        scope_cutoff_note = f" Cancellation cutoff: {scope_cutoff}."
                    binding_failure = (
                        scope_error.error_code == "research_execution_binding_invalid")
                    self.observability.append_trace_in_session(
                        session,
                        self._trace_event(
                            effective_query, run_id=run_id, event_type="evidence_rejected",
                            status="denied", error=scope_error.provenance(),
                            summary=(
                                ("Original execution binding changed after the adapter executed; "
                                 if binding_failure else
                                 "Semantic admission rejected after the adapter executed; "
                                 if semantic_failure else
                                 "Saved Task scope changed after the adapter executed; ")
                                +
                                "original Task hash "
                                f"{task_scope.request_hash if task_scope else 'unknown'}; "
                                "actual raw result, facts and cost retained in the original "
                                "tool reservation; no new evidence/fact admission or reuse."
                                f"{scope_cutoff_note}"
                            ),
                            reference_type=("execution_binding" if binding_failure else
                                            "semantic_admission" if semantic_failure
                                            else "task_scope"),
                            reference_id=(f"{effective_query.request_id}:"
                                + ("execution_binding_changed" if binding_failure else
                                   "semantic_admission_rejected" if semantic_failure
                                   else "task_scope_changed")),
                        ),
                        once=True,
                    )
                if cancellation is not None and scope_error is None:
                    cutoff = (
                        f"accepted cancellation command {cancellation.request_id} at "
                        f"{cancellation.created_at.isoformat()}"
                        if cancellation.created_at is not None
                        else "cancellation cutoff unknown (no verified original command receipt)"
                    )
                    self.observability.append_trace_in_session(
                        session,
                        self._trace_event(
                            effective_query, run_id=run_id, event_type="evidence_accepted",
                            status="succeeded",
                            summary=(
                                f"Late result for capability call {effective_query.request_id} "
                                f"retained after {cutoff}; actual success, evidence, facts and "
                                "cost retained; no new reusable batch promoted."
                            ),
                            reference_type="cancellation_cutoff",
                            reference_id=(
                                f"{effective_query.request_id}:after-cancel:"
                                f"{cancellation.request_id or 'unknown'}"
                            ),
                        ),
                    )
        except asyncio.CancelledError:
            if execution_scope is not None:
                # The adapter already returned actual success. Persistence is
                # unknown; retain the reservation rather than invent cancellation.
                raise
            self.observability.cancel_tool_call(
                run_id=run_id, request_id=effective_query.request_id
            )
            raise
        except Exception as exc:
            error = self._persistence_error(effective_query, exc)
            if execution_scope is not None or collector.returned:
                # A storage failure cannot establish a failed/free adapter call.
                # The running row blocks automatic replay until exact recovery.
                error.adapter_return_observation = collector.observation
                error.add_note("research_adapter_return_persistence_unknown:" + type(exc).__name__)
                raise error from exc
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

        # Raise after the successful result/cost transaction. This is a Task
        # integrity rejection, never a fabricated adapter failure.
        if scope_error is not None:
            raise scope_error
        return persisted

    def _retain_adapter_rejection(
        self, run_id: str, query: ResearchCapabilityQuery, admitted_query_hash: str,
        collector: CapabilityAdapterReturnCollector, error: ResearchCapabilityError,
        *, control: asyncio.CancelledError | None = None,
    ) -> None:
        """Retain only physical return facts; never run semantic admission here."""
        original = collector.observation
        error.adapter_return_observation = original
        if original is None:
            target = control if control is not None else error
            target.add_note("research_adapter_return_snapshot_unknown; reservation remains running")
            return
        try:
            with self.evidence.database.session() as session:
                if session.get_bind().dialect.name == "sqlite":
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                anchor = self._freeze_adapter_return_in_session(
                    session, run_id, query, admitted_query_hash, original)
                self.observability.complete_tool_call_in_session(
                    session, run_id=run_id, request_id=query.request_id,
                    result=original.result(), semantic_error=error.provenance())
                self.observability.append_trace_in_session(session, self._trace_event(
                    query, run_id=run_id, event_type="tool_completed", status="succeeded",
                    summary="Adapter returned; original typed output and reported cost retained; "
                            "semantic admission denied." + anchor), once=True)
                cutoff = self.observability.cancellation_cutoff_in_session(session, run_id)
                cutoff_note = (f" Original accepted cancellation {cutoff.request_id} at "
                               f"{cutoff.created_at.isoformat()}."
                               if cutoff and cutoff.created_at is not None else
                               " Cancellation cutoff unknown." if cutoff else "")
                self.observability.append_trace_in_session(session, self._trace_event(
                    query, run_id=run_id, event_type="evidence_rejected", status="denied",
                    summary="Kernel rejected the already returned adapter output; raw-only "
                            "retention, no Evidence/Fact/reuse admission." + cutoff_note,
                    error=error.provenance(), reference_type="adapter_result_validation",
                    reference_id=query.request_id + ":returned-result-rejected"))
        except Exception as storage_error:
            target = control if control is not None else error
            target.add_note("research_adapter_return_persistence_unknown:"
                            + type(storage_error).__name__ + "; raw_sha256=" + original.result_hash)
            # The original refusal/control signal wins. The running reservation
            # blocks automatic replay; this note does not claim durable cost.

    def _freeze_adapter_return_in_session(
        self, session: Session, run_id: str, query: ResearchCapabilityQuery,
        admitted_query_hash: str, original: CapabilityAdapterReturn,
    ) -> str:
        record = session.get(ResearchToolCallReservationRecord, (run_id, query.request_id),
                             populate_existing=True, with_for_update=True)
        if (record is None or record.research_session_id != query.research_session_id
                or record.generation != query.round or record.capability_id != query.capability_id
                or record.query_hash != admitted_query_hash):
            raise ValueError("research_adapter_return_reservation_changed")
        actual_query = ResearchCapabilityQuery.model_validate_json(
            original.effective_query_json
        )
        if (
            _canonical_gateway_json(actual_query.model_dump(mode="json"))
            != original.effective_query_json
            or not _effective_adapter_query_matches(
                query, actual_query, original.server_received_at
            )
        ):
            raise ValueError("research_adapter_return_query_changed")
        body = {
            "schema_version": _ADAPTER_RETURN_SCHEMA, "run_id": run_id,
            "request_id": query.request_id, "research_session_id": query.research_session_id,
            "generation": query.round, "capability_id": query.capability_id,
            "reservation_query_hash": admitted_query_hash,
            "effective_adapter_query_json": original.effective_query_json,
            "effective_adapter_query_hash": hashlib.sha256(
                original.effective_query_json.encode("utf-8")).hexdigest(),
            "server_received_at": original.server_received_at.isoformat(),
            "adapter_result_json": original.result_json,
            "adapter_result_hash": original.result_hash,
        }
        payload = _canonical_gateway_json(body)
        if len(payload.encode("utf-8")) > _ADAPTER_AUDIT_MAX_UTF8_BYTES:
            raise ValueError("research_adapter_return_audit_too_large")
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        output_id = "adapter_return_" + digest
        role = _adapter_return_role(query.request_id)
        existing = session.query(RunRoleOutputRecord).filter_by(run_id=run_id, role=role
            ).populate_existing().one_or_none()
        if existing is not None:
            if (existing.output_id != output_id or existing.schema_version != _ADAPTER_RETURN_SCHEMA
                    or existing.payload_hash != digest or existing.payload_json != payload):
                raise ValueError("research_adapter_return_conflict")
        else:
            session.add(RunRoleOutputRecord(output_id=output_id, run_id=run_id, role=role,
                payload_json=payload, payload_hash=digest, schema_version=_ADAPTER_RETURN_SCHEMA,
                created_at=_aware(self.clock())))
            session.flush()
        return _adapter_return_anchor(output_id, digest)

    def _read_adapter_return_in_session(
        self, session: Session, run_id: str, query: ResearchCapabilityQuery,
        admitted_query_hash: str,
    ) -> None:
        """Optional historical absence; a present/current anchored receipt is exact."""
        row = session.query(RunRoleOutputRecord).filter_by(
            run_id=run_id, role=_adapter_return_role(query.request_id)
            ).populate_existing().one_or_none()
        trace = session.query(ResearchTraceRecord).filter_by(
            run_id=run_id, event_type="tool_completed", reference_type="capability_call",
            reference_id=query.request_id).populate_existing().order_by(
                ResearchTraceRecord.sequence_no.asc()).first()
        if row is None:
            if trace is not None and " [adapter-return" in trace.summary:
                raise ValueError("research_adapter_return_missing")
            return
        if len(row.payload_json.encode("utf-8")) > _ADAPTER_AUDIT_MAX_UTF8_BYTES:
            raise ValueError("research_adapter_return_audit_too_large")
        body = json.loads(row.payload_json)
        digest = hashlib.sha256(row.payload_json.encode("utf-8")).hexdigest()
        if (not isinstance(body, dict) or set(body) != _ADAPTER_RETURN_KEYS
                or row.schema_version != _ADAPTER_RETURN_SCHEMA
                or body["schema_version"] != _ADAPTER_RETURN_SCHEMA
                or row.output_id != "adapter_return_" + digest or row.payload_hash != digest
                or _canonical_gateway_json(body) != row.payload_json
                or trace is None
                or not trace.summary.endswith(_adapter_return_anchor(row.output_id, digest))
                or (body["run_id"], body["request_id"], body["research_session_id"],
                    body["generation"], body["capability_id"], body["reservation_query_hash"])
                != (run_id, query.request_id, query.research_session_id, query.round,
                    query.capability_id, admitted_query_hash)):
            raise ValueError("research_adapter_return_invalid")
        raw = body["adapter_result_json"]
        actual_query = body["effective_adapter_query_json"]
        server_received_at = body["server_received_at"]
        if (not isinstance(raw, str) or not isinstance(actual_query, str)
                or not isinstance(server_received_at, str)
                or hashlib.sha256(raw.encode("utf-8")).hexdigest() != body["adapter_result_hash"]
                or hashlib.sha256(actual_query.encode("utf-8")).hexdigest()
                != body["effective_adapter_query_hash"]):
            raise ValueError("research_adapter_return_invalid")
        ResearchCapabilityResult.model_validate_json(raw)
        decoded_query = ResearchCapabilityQuery.model_validate_json(actual_query)
        received_at = datetime.fromisoformat(server_received_at)
        if (
            _canonical_gateway_json(decoded_query.model_dump(mode="json")) != actual_query
            or not _effective_adapter_query_matches(query, decoded_query, received_at)
        ):
            raise ValueError("research_adapter_return_query_changed")

    def _active_requirements(
        self, event_id: str, *, run_id: str | None = None
    ) -> dict[str, EvidenceRequirement]:
        """Use the original complete Task, before any round filtering.

        The durable MCP process is independent from the worker process.  A
        single static ``all_contract_requirements`` map therefore loses the
        event-family projections applied by ``ResearchRequestFactory`` (for
        example, completed-daily macro evidence for a current BTC snapshot).
        A present corrupt Task must fail rather than fall back to today's Event
        or Pack projection. The no-Task branch preserves legacy/canary behavior;
        it is not proof of a v2 policy or complete method binding.
        """

        if run_id is not None:
            with self.evidence.database.session() as session:
                run = session.get(RunRecord, run_id)
                if run is not None:
                    saved = read_task_request(session, run)
                    if saved is not None:
                        if saved[0].event_id != event_id:
                            raise ValueError("research_run_event_mismatch")
                        return {
                            item.requirement_id: item for item in saved[0].evidence_requirements
                        }
            receipt = ResearchPackService(self.evidence.database).get(run_id)
            if receipt:
                pack = CryptoMacroFactPack.from_manifest(receipt["definition"]["evidence_policy"])
                family = self.evidence.database.get_event_type(event_id)
                return {
                    item.requirement_id: item
                    for item in pack.contract_requirements_for_event(family)
                }
        if self.requirement_resolver is None:
            return dict(self.requirements)
        event_family = self.evidence.database.get_event_type(event_id)
        resolved = dict(self.requirement_resolver(event_family))
        if not resolved:
            raise ValueError("research_requirements_unavailable")
        return resolved

    @staticmethod
    def _task_scope_error(query: ResearchCapabilityQuery, code: str) -> ResearchCapabilityError:
        return ResearchCapabilityError(
            code, "Capability query failed its saved complete Task scope",
            origin="gateway", cause_code="saved_task_scope",
            capability_id=query.capability_id, tool_call_id=query.request_id,
        )

    @staticmethod
    def _execution_error(query: ResearchCapabilityQuery, code: str) -> ResearchCapabilityError:
        return ResearchCapabilityError(
            "research_execution_binding_invalid", "Original execution binding was not confirmed",
            origin="gateway", cause_code=code, capability_id=query.capability_id,
            tool_call_id=query.request_id,
        )

    def _execution_scope_in_session(
        self, session: Session, run_id: str, query: ResearchCapabilityQuery, *,
        prepared: FrozenExecutionPreparation | None = None,
    ) -> _GatewayExecutionScope | None:
        if not requires_tool_admission_proof(session, run_id):
            return None
        try:
            prepared = prepared or read_prepared_execution_in_session(session, run_id)
            bundle, bundle_identity = read_bundle_identity_in_session(session, run_id)
            request = prepared.execution.request
            requirement = next((item for item in request.evidence_requirements
                                if item.requirement_id == query.requirement_id), None)
            if (requirement is None or query.capability_id not in request.allowed_capabilities
                    or query.mode != request.execution_mode
                    or (query.capability_id not in (
                        *requirement.preferred_capabilities, *requirement.allowed_fallbacks)
                        and not (query.capability_id == "replay.research"
                                 and request.execution_mode == "replay"))
                    or query.research_session_id != prepared.link.dsh_session_id
                    or (query.mode == "replay" and query.cutoff_at > request.pit_cutoff_at)):
                raise ValueError("research_execution_query_scope_invalid")
            if (requirement.requirement_id == "crypto_spot_confirmation"
                    and "spot_market_facts" in requirement.preferred_capabilities
                    and query.capability_id == "market.crypto_derivatives"
                    and (not query.fields
                         or not set(query.fields) <= {"spot_price", "spot_volume"})):
                # The frozen spot fallback reuses the installed public exchange
                # capability; it cannot grant a derivatives query under a spot slot.
                raise ValueError("research_execution_spot_fields_invalid")
            roles = ("research_execution_method",
                     f"dsh_execution_request:{prepared.link.generation}",
                     f"dsh_prompt_integrity:{prepared.link.generation}")
            raw = []
            for role in roles:
                row = session.query(RunRoleOutputRecord).filter_by(
                    run_id=run_id, role=role).populate_existing().one()
                raw.append({"output_id": row.output_id, "run_id": row.run_id, "role": row.role,
                    "row_schema_version": row.schema_version, "payload_hash": row.payload_hash,
                    "recorded_at": _row_time(row.created_at)})
            prompt = session.get(DshSessionPromptRecord, (run_id, prepared.link.generation),
                                 populate_existing=True)
            if prompt is None:
                raise ValueError("research_execution_prompt_missing")
            return _GatewayExecutionScope(_canonical_gateway_json({
                "bundle": bundle_identity.model_dump(mode="json"), "raw_roles": raw,
                "task_request_hash": bundle.task.request_hash,
                "method_hash": prepared.method.method_hash,
                "actual_full_hash": prepared.execution.request_hash,
                "actual_reduced_hash": prepared.execution.execution_request_hash,
                "whole_prompt_hash": prepared.integrity.prompt_hash,
                "prompt": {"run_id": prompt.run_id, "generation": prompt.generation,
                    "dsh_session_id": prompt.dsh_session_id, "request_id": prompt.request_id,
                    "request_hash": prompt.request_hash,
                    "recorded_at": _row_time(prompt.created_at)},
                "link": {"run_id": prepared.link.run_id,
                    "dsh_session_id": prepared.link.dsh_session_id,
                    "request_hash": prepared.link.request_hash,
                    "generation": prepared.link.generation,
                    "max_tool_calls": prepared.link.max_tool_calls,
                    "deadline_at": (prepared.link.deadline_at.isoformat()
                                    if prepared.link.deadline_at is not None else None),
                    "model_step_timeout_ms": prepared.link.model_step_timeout_ms},
            }), prepared)
        except (ValueError, KeyError, TypeError) as exc:
            raise self._execution_error(query, getattr(exc, "error_code", str(exc))) from exc

    def _task_scope_in_session(
        self, session: Session, run_id: str, query: ResearchCapabilityQuery,
    ) -> _SavedTaskScope | None:
        run = session.get(RunRecord, run_id)
        if run is None:
            raise self._task_scope_error(query, "research_run_not_found")
        try:
            bundle = None
            if has_prepared_bundle_in_session(session, run_id):
                bundle = read_research_bundle_in_session(session, run_id)
                if not isinstance(bundle, FrozenResearchBundle):
                    raise ValueError("research_task_scope_original_unavailable")
                saved = bundle.request, bundle.task.request_hash, "saved_task_request"
            else:
                saved = read_task_request(session, run)
        except (ValueError, KeyError, TypeError) as exc:
            raise self._task_scope_error(query, "research_task_request_invalid") from exc
        if saved is None:
            self._validate_task_query_asset(query, None)
            return None
        request, digest, _source = saved
        requirements = {item.requirement_id: item for item in request.evidence_requirements}
        if query.requirement_id not in requirements:
            raise self._task_scope_error(query, "research_requirement_not_allowed_for_run")
        scope = bundle.task.scope if bundle is not None else None
        saved_scope = _SavedTaskScope(
            digest, requirements,
            scope.scope_hash if scope is not None else None,
            scope.market if scope is not None else None,
            scope.instrument if scope is not None else None,
        )
        self._validate_task_query_asset(query, saved_scope)
        return saved_scope

    def _validate_task_query_asset(
        self, query: ResearchCapabilityQuery, scope: _SavedTaskScope | None,
    ) -> None:
        """Bind asset-specific queries to the authenticated Factory Task original.

        These aliases only compare assets. They do not replace request symbols,
        grant a route, or attest availability of an ETH/venue adapter. Global
        macro capabilities and the explicit FRED historical datasets retain
        their existing permissions.
        """
        asset_symbols: list[str | None] = []
        if query.capability_id in _TASK_ASSET_CAPABILITIES:
            if not query.symbols:
                raise self._task_scope_error(query, "research_task_asset_symbols_required")
            if query.capability_id == "spot_market_facts":
                asset_symbols = [_TASK_INSTRUMENT_ASSETS.get(symbol)
                                 if symbol in {"BTC-USDT", "ETH-USDT"} else None
                                 for symbol in query.symbols]
            else:
                asset_symbols = [_TASK_SYMBOL_ASSETS.get(symbol.strip().upper())
                                 for symbol in query.symbols]
        elif query.capability_id == "data.historical":
            same_asset_requirement = query.requirement_id in _TASK_ASSET_REQUIREMENTS
            if same_asset_requirement and not query.symbols:
                raise self._task_scope_error(query, "research_task_asset_symbols_required")
            for symbol in query.symbols:
                if symbol.lower().startswith("crypto.") or same_asset_requirement:
                    asset_symbols.append(_HISTORICAL_TASK_ASSETS.get(symbol))
        if not asset_symbols:
            return
        if (scope is None or scope.scope_hash is None or scope.market != "crypto"
                or scope.instrument not in _TASK_INSTRUMENT_ASSETS):
            raise self._task_scope_error(query, "research_task_asset_scope_unavailable")
        if any(asset is None for asset in asset_symbols):
            raise self._task_scope_error(query, "research_task_asset_symbol_unbound")
        expected = _TASK_INSTRUMENT_ASSETS[scope.instrument]
        if any(asset != expected for asset in asset_symbols):
            raise self._task_scope_error(query, "research_task_asset_mismatch")

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
            if run.status in {
                "completed", "degraded", "failed", "cancelled", "research_only", "rejected",
            }:
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
            self._task_scope_in_session(session, link.run_id, query)
        try:
            method = ResearchMethodService(self.evidence.database).get(link.run_id)
            if method and method["definition"].get("request_pack_hash"):
                pack = ResearchPackService(self.evidence.database).get(link.run_id)
                if not pack or pack["definition_hash"] != method["definition"]["request_pack_hash"]:
                    raise ValueError("research_pack_binding_mismatch")
            if method and method["request_hash"] != link.request_hash:
                raise ValueError("research_method_request_mismatch")
        except ValueError as exc:
            raise ResearchCapabilityError(
                "research_method_receipt_invalid", "Run method receipt failed integrity checks",
                origin="gateway", capability_id=query.capability_id, tool_call_id=query.request_id,
            ) from exc
        if method is not None:
            allowed = method["definition"].get("allowed_capabilities")
            mode = method["definition"].get("execution_mode")
            if (not isinstance(allowed, list) or query.capability_id not in allowed
                    or (mode is not None and query.mode != mode)):
                raise ResearchCapabilityError(
                    "research_capability_not_allowed_for_run",
                    "Capability is outside this Run's frozen execution scope",
                    origin="gateway", cause_code="run_capability_scope",
                    capability_id=query.capability_id, tool_call_id=query.request_id,
                )
        return link.run_id, link, run_event_id

    def _effective_query(
        self,
        query: ResearchCapabilityQuery,
        deadline_at: datetime | None,
        *,
        expected_event_id: str,
        frozen_scope: _GatewayExecutionScope | None = None,
    ) -> ResearchCapabilityQuery:
        """Clamp live capability PIT to the accepted durable DSH deadline.

        DSH model output may contain a future cutoff because the prompt carries
        the run ceiling. The model must never be able to extend that ceiling;
        a legacy link without a persisted deadline is rejected rather than
        guessed. Freshness is still anchored to the trusted result completion
        time below, so the run ceiling is not used as an age calculation.
        """

        if frozen_scope is not None:
            query = self._frozen_event_window_query(query, frozen_scope)
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
            if query.requested_event_offsets and frozen_scope is None:
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
                # Policy-expectation baselines are pre-event market observations,
                # not EventWatch samples. They remain lineage-bound to the Run,
                # but are supplied by the pricing adapter's own timestamped
                # archive and must not be rejected as an invalid watch offset.
                watch_offsets = tuple(offset for offset in requested if offset != "baseline")
                if any(offset not in watch.window_offsets for offset in watch_offsets):
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
                selected = [samples[offset] for offset in watch_offsets if offset in samples]
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
        # The provider router receives the same trusted ceiling as the
        # durable session. A model-supplied deadline is never allowed to
        # extend it.
        update["deadline_at"] = deadline_at.astimezone(UTC)
        return query.model_copy(update=update)

    def _frozen_event_window_query(
        self, query: ResearchCapabilityQuery, scope: _GatewayExecutionScope,
    ) -> ResearchCapabilityQuery:
        """Use the original full Task's Watch/slots; current Watch is not its basis."""
        request = scope.prepared.execution.request
        if query.event_id is not None and query.event_id != request.event_id:
            raise self._execution_error(query, "research_event_lineage_mismatch")
        if not query.requested_event_offsets:
            return query
        watch = request.event_watch
        if watch is None or query.event_id is None:
            raise self._execution_error(query, "research_event_watch_not_found")
        offsets = [offset for offset in query.requested_event_offsets if offset != "baseline"]
        if any(offset not in watch.window_offsets for offset in offsets):
            raise self._execution_error(query, "research_event_window_offset_invalid")
        original_samples = request.event_window_samples
        if original_samples is None:
            raise self._execution_error(query, "research_event_watch_not_found")
        samples = {item.offset: item for item in original_samples}
        selected = [samples[offset] for offset in offsets if offset in samples]
        if not selected:
            raise self._execution_error(query, "research_event_watch_not_found")
        update: dict[str, object] = {}
        for field, trusted in (("event_at", watch.scheduled_at),
                               ("window_start_at", min(item.target_at for item in selected)),
                               ("window_end_at", max(item.target_at for item in selected))):
            trusted = _aware(trusted)
            supplied = getattr(query, field)
            if supplied is not None and _aware(supplied) != trusted:
                raise self._execution_error(query, "research_event_time_mismatch")
            update[field] = trusted
        return query.model_copy(update=update)

    async def _trace(
        self, query: ResearchCapabilityQuery, *, event_type: str, status: str,
        summary: str, error: ErrorProvenance | None = None,
        reference_id: str | None = None,
    ) -> None:
        self._trace_sync(query, event_type=event_type, status=status,
                         summary=summary, error=error, reference_id=reference_id)

    def _trace_sync(
        self,
        query: ResearchCapabilityQuery,
        *,
        event_type: str,
        status: str,
        summary: str,
        error: ErrorProvenance | None = None,
        reference_id: str | None = None,
    ) -> None:
        link = self.links.get_by_session(query.research_session_id)
        if link is None:
            raise KeyError("research_session_not_found")
        event = self._trace_event(
            query, run_id=link.run_id,
            event_type=event_type, status=status, summary=summary, error=error,
            reference_id=reference_id,
        )
        self.observability.append_trace_once(event)

    def _trace_event(
        self, query: ResearchCapabilityQuery, *, run_id: str, event_type: str,
        status: str, summary: str, error: ErrorProvenance | None = None,
        reference_type: str = "capability_call", reference_id: str | None = None,
    ) -> ResearchTraceEvent:
        return ResearchTraceEvent.model_validate(
            {
                "schema_version": "research-trace-event.v1",
                "run_id": run_id,
                "research_session_id": query.research_session_id,
                "sequence_no": 0,
                "event_type": event_type,
                "occurred_at": _aware(self.clock()),
                "stage": "acquiring_evidence",
                "summary": summary,
                "reference_type": reference_type,
                "reference_id": reference_id or query.request_id,
                "status": status,
                "error_code": error.error_code if error is not None else None,
                "error": error,
            }
        )

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


def _canonical_gateway_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _effective_adapter_query_matches(
    admitted_query: ResearchCapabilityQuery,
    adapter_query: ResearchCapabilityQuery,
    server_received_at: datetime,
) -> bool:
    """Verify the Gateway-owned observation time and optional Router clamp.

    The capability Gateway may only shorten an admitted Run deadline. The
    shortened value is part of the frozen adapter-return bytes so exact replay
    stays auditable without treating a local timeout as a new Run deadline.
    """

    try:
        received_at = _aware(server_received_at)
        actual_deadline = (
            _aware(adapter_query.deadline_at)
            if adapter_query.deadline_at is not None
            else None
        )
        admitted_deadline = (
            _aware(admitted_query.deadline_at)
            if admitted_query.deadline_at is not None
            else None
        )
    except ValueError:
        return False
    if actual_deadline is not None:
        if actual_deadline <= received_at:
            return False
        if admitted_deadline is not None and actual_deadline > admitted_deadline:
            return False
    elif admitted_deadline is not None:
        return False
    expected = admitted_query.model_copy(
        update={
            "requested_observed_at": admitted_query.observed_at,
            "observed_at": received_at,
            "deadline_at": actual_deadline,
        }
    )
    return adapter_query.model_dump(mode="json") == expected.model_dump(mode="json")


_ADAPTER_RETURN_SCHEMA = "research-adapter-return-observation.v1"
_ADAPTER_AUDIT_MAX_UTF8_BYTES = 64 * 1024 * 1024
_ADAPTER_RETURN_KEYS = {"schema_version", "run_id", "request_id", "research_session_id",
    "generation", "capability_id", "reservation_query_hash", "effective_adapter_query_json",
    "effective_adapter_query_hash", "server_received_at", "adapter_result_json",
    "adapter_result_hash"}


def _adapter_return_role(request_id: str) -> str:
    # The existing SQL role column is String(64); the body still holds the full
    # original request ID and any truncated-digest collision is an exact conflict.
    return "research_adapter_return:" + hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:40]


def _adapter_return_anchor(output_id: str, payload_hash: str) -> str:
    return f" [adapter-return.v1:{output_id}:{payload_hash}]"


def _row_time(value: datetime | None) -> str:
    # Match the existing SQLite UTC storage contract; this is not a time witness.
    normalized = as_utc(value)
    if normalized is None:
        raise ValueError("research_execution_row_time_missing")
    return normalized.isoformat()


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
