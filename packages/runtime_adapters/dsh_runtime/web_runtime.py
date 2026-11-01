from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Literal, TypeVar, cast

from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    DshBridgeError,
    DshHostReadiness,
    DshSessionAccepted,
    DshSessionCompletion,
    DshSessionPrompt,
    DshSessionResult,
    DshSessionStatus,
    DshSessionSubmit,
    ErrorProvenance,
    ResearchSessionRequest,
    ResearchSessionResult,
    ResearchTraceEvent,
)
from packages.contracts_py.decision_hub_contracts.binding_contract_base import (
    binding_schema_registry,
    shape_validation_scope,
)
from packages.contracts_py.decision_hub_contracts.context_execution_contract_base import (
    context_execution_registry,
)
from packages.contracts_py.decision_hub_contracts.context_selection_v3_contract_base import (
    context_selection_v3_schema_registry,
)
from packages.contracts_py.decision_hub_contracts.context_selection_v4_contract_base import (
    context_selection_v4_schema_registry,
)
from packages.contracts_py.decision_hub_contracts.execution_contract_base import (
    execution_schema_registry,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.dsh_terminal_receipts import (
    read_authenticated_terminal_in_session,
    validate_terminal_result_native_metadata,
)
from packages.kernel.decision_hub_kernel.application.research_context_dispatch import (
    ContextSelectionReference,
    parse_context_selection_reference,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    ResearchMethodService,
    method_definition,
)
from packages.kernel.decision_hub_kernel.application.research_pack import ResearchPackService
from packages.kernel.decision_hub_kernel.application.research_policy import (
    RENDERER_HASH,
    render_research_policy,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_sha,
    store_mapped_observation,
    store_mapped_perpetual_observation,
    store_transport_observation,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash as _request_hash,
)
from packages.kernel.decision_hub_kernel.application.research_review import (
    research_result_content_hash,
)
from packages.kernel.decision_hub_kernel.application.research_skill_preparation import (
    SkillPreparation,
    SkillPreparationObservationPending,
    SkillPreparationObservationScope,
)
from packages.kernel.decision_hub_kernel.application.research_tool_accounting import (
    normalize_generation_tool_accounting,
)
from packages.kernel.decision_hub_kernel.application.research_tool_accounting import (
    normalize_tool_accounting as _normalize_tool_accounting,
)
from packages.kernel.decision_hub_kernel.persistence.db import DshSessionLinkRecord, RunRecord
from packages.kernel.decision_hub_kernel.persistence.sqlite_busy import is_sqlite_initial_begin_busy
from packages.kernel.decision_hub_kernel.ports.research import ResearchTraceSink
from packages.kernel.decision_hub_kernel.ports.research_observation import (
    ObservedAgentExecutionError,
    ObservedExecutionRef,
    ObservedResearchResult,
    ResearchMappedObservation,
    ResearchMappedObservationV2,
    ResearchNativeObservationContext,
    ResearchObservedError,
    ResearchPerpetualSynthesisObservation,
    ResearchSynthesisSelectionWitness,
    ResearchTransportObservation,
)
from packages.kernel.decision_hub_kernel.ports.research_synthesis_protocol import (
    DECISION_OUTPUT_SCHEMA_REF,
    PERPETUAL_OUTPUT_SCHEMA_REFS,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.orchestration.owned_work import HeavyWorkAdmission

from .cancellation_projection import DshCancellationProjector, ProjectionPending, ProjectionRead
from .cancelled_recovery import CancelledRunReconciler
from .client import DshSdkNotification, DshSdkRun
from .event_mapper import map_notifications
from .profile import (
    PROMPT_POLICY_HASH,
    build_synthesis_tool_repair_prompt,
    build_trusted_web_research_prompt,
)
from .research_runtime_binding import (
    RuntimePreparedTurn,
    RuntimeTaskAnchor,
    authenticate_runtime_binding,
    prepare_runtime_turn,
    read_legacy_generation,
    read_runtime_task_anchor,
    verify_runtime_task_anchor,
    verify_runtime_task_and_selection,
)
from .result_mapper import (
    MappedPerpetualSynthesis,
    evidence_only_selection,
    map_evidence_only_result,
    map_session_result_with_witness,
    synthesis_evidence_ref_errors,
    trusted_evidence_ids,
)
from .terminal_queue_recovery import (
    TerminalQueueAuthority,
    TerminalQueueReconciler,
    terminal_queue_authority_in_session,
)
from .web_host_client import DshHostClient

_T = TypeVar("_T")
_TERMINAL_QUEUE_BUDGET_SECONDS = 15.0
_OWNER_CANCEL_RPC_SECONDS = 5.0
_OWNER_CANCEL_AUDIT_SECONDS = 1.0
log = logging.getLogger(__name__)


@dataclass
class _RuntimeObservations:
    context: ResearchNativeObservationContext | None = None
    latest: ObservedExecutionRef | None = None
    latest_generation: int = 0
    result_refs: dict[str, ObservedExecutionRef] = field(default_factory=dict)
    seen_states: set[tuple[str, str, int, str, str, str | None]] = field(default_factory=set)
    mapping_payload: DshSessionResult | None = None
    selection: ResearchSynthesisSelectionWitness | None = None
    perpetual_synthesis: MappedPerpetualSynthesis | None = None
    pending_transports: dict[str, ProjectionPending[ObservedExecutionRef]] = field(
        default_factory=dict,
    )


def _safe_cancel_code(value: str | None) -> str | None:
    return value if value and re.fullmatch(r"[a-z][a-z0-9_]{0,100}", value) else None


_CATALOG_TERMINAL_FAILURE = re.compile(
    r"^Research capability catalog failed "
    r"\((research_catalog_transport_(?:timeout|unavailable|cancelled)); "
    r"phase=(fetch|response_body)(?:; deadline_ms=([1-9][0-9]{0,4}))?\)$"
)


def _terminal_failure_error(
    status: DshSessionStatus,
    original: DshSessionCompletion | None,
) -> AgentExecutionError:
    """Project only an authenticated, bounded native failure provenance.

    The Host deliberately redacts arbitrary SDK/provider text. This recognizes
    only its closed catalog marker and carries the actual local timeout into the
    normal ErrorProvenance ledger. All other terminal failures retain the
    established conservative projection.
    """
    error = original.error if original is not None else None
    match = _CATALOG_TERMINAL_FAILURE.fullmatch(error.message) if error else None
    if match is not None and error is not None and match.group(1) == error.code:
        code, _phase, raw_deadline = match.groups()
        deadline_ms = int(raw_deadline) if raw_deadline is not None else None
        valid_deadline = (
            (code == "research_catalog_transport_timeout"
             and deadline_ms is not None and deadline_ms <= 30_000)
            or (code != "research_catalog_transport_timeout" and deadline_ms is None)
        )
        if valid_deadline:
            return AgentExecutionError(
                code,
                error.message,
                retryable=error.retryable,
                provider_id="dsh-web",
                origin="transport",
                cause_code=code,
                deadline_ms=deadline_ms,
            )
    return AgentExecutionError(
        status.error_code or f"dsh_session_{status.state}",
        "DSH Web session did not complete successfully",
        retryable=status.state == "failed",
        provider_id="dsh-web",
        origin="dsh",
        cause_code=status.state,
    )


def _uses_native_execution(session: Session, run_id: str) -> bool:
    from packages.kernel.decision_hub_kernel.application.research_observability import (
        requires_tool_admission_proof,
    )

    return requires_tool_admission_proof(session, run_id)


class SkillPreparationPendingExecutionError(AgentExecutionError):
    """Trusted native observation only; Worker must authenticate Task anew."""

    def __init__(self, wait: SkillPreparationObservationPending, anchor: RuntimeTaskAnchor, projector):
        observed = wait.observation
        pending = getattr(observed, "pending", None)
        if (type(wait) is not SkillPreparationObservationPending
                or type(observed) is not ProjectionRead
                or type(anchor) is not RuntimeTaskAnchor
                or type(projector) is not DshCancellationProjector
                or type(wait.scope) is not SkillPreparationObservationScope
                or (wait.scope.run_id, wait.scope.generation) != (wait.run_id, wait.generation)
                or type(pending) is not ProjectionPending
                or not DshCancellationProjector.owns_pending(
                    projector, pending, read_only=wait.phase == "read", observation_scope=wait.scope
                )
                or observed.state != "pending"
                or observed.limitation != "cancellation_projection_wait_elapsed"
                or wait.phase not in {"read", "write"}
                or wait.run_id != anchor.run_id or wait.generation != anchor.generation
                or not anchor.bundle_hash or not anchor.task_request_hash
                or not anchor.execution_fingerprint or anchor.generation < 1
                or anchor.deadline_at is None or datetime.now(UTC) >= anchor.deadline_at
                or re.fullmatch(r"[a-f0-9]{32}", pending.operation_id) is None):
            raise ValueError("skill_preparation_observation_invalid")
        current = pending.poll()
        # A completed invalid operation has actual permanent failure evidence.
        # A ready first result still needs normal original/checkpoint readback.
        if current.state not in {"pending", "ready"} or (
                current.state == "pending" and current.limitation != "cancellation_projection_wait_elapsed"):
            raise ValueError(current.limitation or "skill_preparation_observation_invalid")
        super().__init__(
            "research_skill_preparation_failed", "Skill selection observation is pending before dispatch",
            retryable=True, provider_id="dsh-web", origin="orchestration",
            cause_code="cancellation_projection_wait_elapsed",
        )
        self.anchor, self.observation_phase, self.pending_observation = anchor, wait.phase, pending


def _skill_preparation_error(error, *, run_id, anchor, projector):
    if type(error) is SkillPreparationObservationPending and error.run_id == run_id:
        try:
            return SkillPreparationPendingExecutionError(error, anchor, projector)
        except ValueError as invalid:
            error = invalid
    return AgentExecutionError(
        "research_skill_preparation_failed", "Skill selection was not confirmed before dispatch",
        retryable=False, provider_id="dsh-web", origin="orchestration", cause_code=str(error),
    )


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
        readiness_grace_seconds: float = 10.0,
        terminal_reconciliation_seconds: float = 3.0,
        binding_wait_seconds: float = 5.0,
        proof_read_wait_seconds: float = 15.0,
        preparation_confirmation_wait_seconds: float = 30.0,
        mapping_confirmation_wait_seconds: float = 30.0,
        status_confirmation_wait_seconds: float = 30.0,
        skill_preparation: SkillPreparation | None = None,
    ) -> None:
        self.skill_preparation = skill_preparation
        if poll_interval_seconds <= 0:
            raise ValueError("dsh_web_poll_interval_invalid")
        if readiness_grace_seconds <= 0:
            raise ValueError("dsh_web_readiness_grace_invalid")
        if not 0 < terminal_reconciliation_seconds <= 10:
            raise ValueError("dsh_web_terminal_reconciliation_invalid")
        if not 0 < binding_wait_seconds <= 5:
            raise ValueError("dsh_web_binding_wait_invalid")
        if not 0 < proof_read_wait_seconds <= 15:
            raise ValueError("dsh_web_proof_read_wait_invalid")
        if not 0 < preparation_confirmation_wait_seconds <= 30:
            raise ValueError("dsh_web_preparation_confirmation_wait_invalid")
        if not 0 < mapping_confirmation_wait_seconds <= 30:
            raise ValueError("dsh_web_mapping_confirmation_wait_invalid")
        if not 0 < status_confirmation_wait_seconds <= 30:
            raise ValueError("dsh_web_status_confirmation_wait_invalid")
        # Canonical schema construction is pure initialization before any Run.
        # It does not read the database or authenticate a Task/permission.
        binding_schema_registry()
        execution_schema_registry()
        context_execution_registry()
        context_selection_v3_schema_registry()
        context_selection_v4_schema_registry()
        self.links = links
        self.client = client
        self.poll_interval_seconds = poll_interval_seconds
        self.readiness_grace_seconds = readiness_grace_seconds
        self.terminal_reconciliation_seconds = terminal_reconciliation_seconds
        self.binding_wait_seconds = binding_wait_seconds
        self.proof_read_wait_seconds = proof_read_wait_seconds
        self.preparation_confirmation_wait_seconds = preparation_confirmation_wait_seconds
        self.mapping_confirmation_wait_seconds = mapping_confirmation_wait_seconds
        self.status_confirmation_wait_seconds = status_confirmation_wait_seconds
        self.runtime_version = "dsh-web-unverified"
        self.cancellation_projector = DshCancellationProjector(links)
        self.cancelled_reconciler = CancelledRunReconciler(self.cancellation_projector, client)
        self.terminal_queue_reconciler = TerminalQueueReconciler(
            self.cancellation_projector,
            client,
        )
        self._terminal_queue_task: asyncio.Task[dict[str, int]] | None = None
        self._maintenance_closed = False
        self._skill_repair_policy: ContextVar[dict | None] = ContextVar("skill_repair_policy", default=None)
        self._task_anchor: ContextVar[RuntimeTaskAnchor | None] = ContextVar(
            "dsh_runtime_task_anchor",
            default=None,
        )
        self._binding_deadline: ContextVar[datetime | None] = ContextVar(
            "dsh_runtime_binding_deadline",
            default=None,
        )
        self._proof_operation_deadline: ContextVar[datetime | None] = ContextVar(
            "dsh_runtime_proof_operation_deadline",
            default=None,
        )
        self._selection: ContextVar[tuple[ContextSelectionReference, str, str] | None] = ContextVar(
            "dsh_runtime_original_selection",
            default=None,
        )
        self._observations: ContextVar[_RuntimeObservations | None] = ContextVar(
            "dsh_runtime_observations",
            default=None,
        )

    async def execute(
        self,
        request: ResearchSessionRequest,
        trace_sink: ResearchTraceSink | None = None,
    ) -> ResearchSessionResult:
        returned = await self._execute_entry(request, trace_sink, require_observed=False)
        return returned.result if isinstance(returned, ObservedResearchResult) else returned

    async def execute_observed(
        self,
        request: ResearchSessionRequest,
        trace_sink: ResearchTraceSink | None = None,
    ) -> ObservedResearchResult:
        returned = await self._execute_entry(request, trace_sink, require_observed=True)
        assert isinstance(returned, ObservedResearchResult)
        return returned

    async def execute_selected_observed(
        self,
        request: ResearchSessionRequest,
        selection_reference: ContextSelectionReference,
        *,
        expected_lease_owner: str,
        trace_sink: ResearchTraceSink | None = None,
    ) -> ObservedResearchResult:
        """Explicit original Selection and caller owner, never read-latest inference."""
        original = parse_context_selection_reference(selection_reference.model_dump_json())
        selected_request_json = json.dumps(
            ResearchSessionRequest.model_validate(request.model_dump(mode="json")).model_dump(
                mode="json"
            ),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        token = self._selection.set((original, expected_lease_owner, selected_request_json))
        try:
            returned = await self._execute_entry(request, trace_sink, require_observed=True)
            assert isinstance(returned, ObservedResearchResult)
            return returned
        finally:
            self._selection.reset(token)

    async def _execute_entry(
        self,
        request: ResearchSessionRequest,
        trace_sink: ResearchTraceSink | None,
        *,
        require_observed: bool,
    ) -> ResearchSessionResult | ObservedResearchResult:
        # Snapshot caller-owned mutable fields before the first await.
        request = ResearchSessionRequest.model_validate(request.model_dump(mode="json"))
        # Each coroutine owns its anchor; concurrent Runs cannot overwrite it.
        anchor = await self._read_task_anchor(request.run_id, deadline_at=request.deadline_at)
        if anchor.requires_selection and self._selection.get() is None:
            raise AgentExecutionError(
                "research_method_binding_failed",
                "Original Selection is required",
                cause_code="research_context_selection_required",
            )
        if require_observed and anchor.bundle_hash is None:
            raise AgentExecutionError(
                "research_observation_unavailable",
                "Legacy execution has no native V2 observation binding",
                cause_code="research_observation_legacy_unsupported",
            )
        repair_policy_token = self._skill_repair_policy.set(None)
        anchor_token = self._task_anchor.set(anchor)
        deadline_token = self._binding_deadline.set(
            min(request.deadline_at, anchor.deadline_at)
            if anchor.deadline_at is not None
            else request.deadline_at
        )
        observation_token = self._observations.set(_RuntimeObservations())
        active_submission: DshSessionSubmit | None = None

        def track_submission(submission: DshSessionSubmit) -> None:
            nonlocal active_submission
            active_submission = submission

        try:
            result = await self._execute(request, trace_sink, track_submission=track_submission)
            if anchor.bundle_hash is not None:
                return await self._mapped_observation(result)
            return result
        except AgentExecutionError as exc:
            # An owner Stop can revoke active permission before the watcher
            # cancels this coroutine. Send cleanup before awaiting error storage.
            await self._cancel_active_owner_best_effort(
                active_submission,
                caller="runtime_agent_error",
                error=exc,
            )
            await self._quiesce_failed_invocation(active_submission)
            if exc.error_code != "research_observation_unavailable":
                try:
                    reference = await self._record_transport(None, error=exc)
                except AgentExecutionError:
                    # A storage failure cannot rewrite the original transport,
                    # protocol or native-binding failure as a successful result.
                    exc.add_note("The failure observation was not durably confirmed")
                else:
                    if reference is not None:
                        # Carry this confirmed failure; no read-latest inference.
                        # Chaining preserves the original Provider cause as well.
                        raise ObservedAgentExecutionError(exc, reference) from exc
            raise
        except Exception:
            # A fresh status writer may reject after owner Stop wins its CAS.
            # It raises ValueError rather than AgentExecutionError; cleanup of
            # this authenticated invocation must still run before Worker returns.
            await self._cancel_active_owner_best_effort(
                active_submission,
                caller="runtime_exception",
            )
            await self._quiesce_failed_invocation(active_submission)
            raise
        except asyncio.CancelledError:
            # External shutdown or a lost lease is never owner Stop authority.
            await self._cancel_active_owner_best_effort(
                active_submission,
                caller="runtime_cancelled_error",
            )
            raise
        finally:
            self._observations.reset(observation_token)
            self._skill_repair_policy.reset(repair_policy_token)
            self._task_anchor.reset(anchor_token)
            self._binding_deadline.reset(deadline_token)

    async def _read_task_anchor(
        self,
        run_id: str,
        *,
        deadline_at: datetime,
    ) -> RuntimeTaskAnchor:
        # The bounded short probe reads persisted schema footprints, never a
        # caller flag. Partial v2 still enters full validation and fails closed.
        probe = await self._read_binding_with_contention_wait(
            lambda session: _uses_native_execution(session, run_id),
            timeout_seconds=min(
                0.25, self.proof_read_wait_seconds,
                max(0.0, (deadline_at - datetime.now(UTC)).total_seconds()),
            ),
            deadline_at=deadline_at,
            pre_host_preparation=True,
        )
        if probe.state != "ready" or probe.value is None:
            raise AgentExecutionError(
                "research_method_binding_failed",
                "Research schema probe was not confirmed",
                retryable=probe.state == "pending",
                provider_id="dsh-web",
                origin="orchestration",
                cause_code=probe.limitation or "research_runtime_binding_unavailable",
            )
        budget = (
            min(
                15.0,
                self.proof_read_wait_seconds,
                max(0.0, (deadline_at - datetime.now(UTC)).total_seconds()),
            )
            if probe.value
            else 0.25
        )

        def read_original(session: Session) -> RuntimeTaskAnchor:
            with shape_validation_scope():
                return read_runtime_task_anchor(session, run_id)

        read = await self._read_binding_with_contention_wait(
            read_original,
            timeout_seconds=budget,
            deadline_at=deadline_at,
            pre_host_preparation=True,
        )
        if read.state == "ready" and read.value is not None:
            return read.value
        raise AgentExecutionError(
            "research_method_binding_failed",
            "Original research Task could not be verified before a Host operation",
            retryable=read.state == "pending",
            provider_id="dsh-web",
            origin="orchestration",
            cause_code=read.limitation or "research_runtime_binding_unavailable",
        )

    async def _read_binding_with_contention_wait(
        self, verify: Callable[[Session], _T], *, timeout_seconds: float,
        deadline_at: datetime, status_initial_writer_busy: bool = False,
        pre_host_preparation: bool = False,
        continue_until_deadline: bool = False,
        owned_operation: Callable[[], _T] | None = None,
    ) -> ProjectionRead[_T]:
        """Wait for fresh RO proof inside its original, never replenished budget.

        A slot rejection submitted nothing. Native SQLite read contention has
        finished its Session before re-entry. A submitted Future is only polled;
        no pending operation, Host call, or uncertain writer is replayed here.
        """
        loop = asyncio.get_running_loop()
        started = loop.time()
        # Initial schema/Task authentication precedes every Host operation.
        # Its <=15s observation window is not a deadline for a healthy native
        # RO Future. Terminal receipt reconciliation has the same property: a
        # submitted read is only observed until its original absolute deadline.
        # The separate status case follows only a known unacquired BEGIN.
        # Physical-operation binding passes the same preparation mode. Other
        # callers retain their own explicit wait budget. Nothing is replayed.
        expires = started + max(0.0, (
            (deadline_at - datetime.now(UTC)).total_seconds()
            if status_initial_writer_busy or pre_host_preparation or continue_until_deadline
            else timeout_seconds
        ))
        pending: ProjectionPending[_T] | None = None
        last = ProjectionRead[_T]("pending", limitation="cancellation_projection_busy")
        slot_rejections = 0
        same_future_polls = 0
        accepted_ro_submissions = 0
        pending_observed = False

        def finish(outcome: ProjectionRead[_T]) -> ProjectionRead[_T]:
            if slot_rejections or pending_observed:
                # One closed diagnostic at return; no per-poll logging, payload,
                # SQL, caller parameters, model data or inferred thread purpose.
                known_reasons = {
                    "cancellation_projection_busy", "cancellation_projection_read_busy",
                    "cancellation_projection_wait_elapsed", "cancellation_projection_db_busy",
                    "cancellation_projection_closed", "cancellation_projection_failed",
                    "research_runtime_original_task_changed", "research_execution_run_not_active",
                    "research_context_selection_permission_lost",
                    "research_context_selection_deadline_elapsed",
                }
                reason = (
                    outcome.limitation if outcome.limitation in known_reasons
                    else "original_verifier_rejected" if outcome.state == "invalid"
                    else "binding_read_unavailable" if outcome.state == "pending" else None
                )
                try:
                    log.info("research_binding_read_wait %s", json.dumps({
                        "schema_version": "research-binding-read-wait.v1",
                        "original_budget_seconds": timeout_seconds,
                        "known_initial_status_writer_busy": status_initial_writer_busy,
                        **({"pre_host_preparation": True} if pre_host_preparation else {}),
                        "elapsed_ms": max(0, round((loop.time() - started) * 1000)),
                        "slot_rejections": slot_rejections,
                        "same_future_polls": same_future_polls,
                        "accepted_ro_submissions": accepted_ro_submissions,
                        "final_state": outcome.state if outcome.state in {"ready", "pending", "invalid"} else "unknown",
                        "reason_code": reason,
                        "absolute_deadline_elapsed": datetime.now(UTC) >= deadline_at,
                        "proof_budget_elapsed": loop.time() >= expires,
                    }, sort_keys=True))
                except Exception:
                    # Diagnostics cannot turn rejected proof into authorization,
                    # change transaction state, or fail an otherwise valid read.
                    pass
            return outcome

        try:
            while True:
                remaining = min(
                    timeout_seconds, expires - loop.time(),
                    (deadline_at - datetime.now(UTC)).total_seconds(),
                )
                if remaining <= 0:
                    return finish(last)
                if pending is not None:
                    same_future_polls += 1
                    outcome = pending.poll()
                else:
                    outcome = (
                        await self.cancellation_projector.read_owned(
                            owned_operation, timeout_seconds=remaining,
                        )
                        if owned_operation is not None else
                        await self.cancellation_projector.read(verify, timeout_seconds=remaining)
                    )
                    if (outcome.state in {"ready", "invalid"}
                            or outcome.pending is not None or outcome.limitation in {
                        "cancellation_projection_read_busy", "cancellation_projection_db_busy",
                        "cancellation_projection_failed",
                    }):
                        # The Projector contract reports these only after accepting
                        # a real Future; slot/closed/unsupported admission did not.
                        accepted_ro_submissions += 1
                    if (outcome.state == "pending"
                            and outcome.limitation == "cancellation_projection_busy"):
                        slot_rejections += 1
                if outcome.state != "pending":
                    if outcome.state == "invalid":
                        return finish(outcome)
                    if loop.time() >= expires or datetime.now(UTC) >= deadline_at:
                        return finish(ProjectionRead(
                            "pending", limitation="cancellation_projection_wait_elapsed",
                        ))
                    return finish(outcome)
                last = outcome
                if outcome.pending is not None:
                    pending_observed = True
                    pending = outcome.pending
                elif outcome.limitation not in {
                    "cancellation_projection_busy", "cancellation_projection_read_busy",
                }:
                    return finish(outcome)
                elif pending is not None:
                    # The original operation has finished with a known RO busy.
                    pending = None
                remaining = min(
                    timeout_seconds, expires - loop.time(),
                    (deadline_at - datetime.now(UTC)).total_seconds(),
                )
                if remaining <= 0:
                    return finish(last)
                await asyncio.sleep(min(0.025, remaining))
        except asyncio.CancelledError:
            if owned_operation is not None and pending is not None:
                # This exact submitted native callback owns its Sessions and
                # HANDLEs until completion, including between observation windows.
                while pending.poll().pending is not None:
                    try:
                        await asyncio.sleep(0.025)
                    except asyncio.CancelledError:
                        continue
            raise

    async def _assert_task_binding(
        self, *, allow_cancelled: bool = False, status_initial_writer_busy: bool = False,
    ) -> None:
        anchor = self._task_anchor.get()
        if anchor is None:
            raise AgentExecutionError(
                "research_method_binding_failed",
                "Research operation has no Task anchor",
                provider_id="dsh-web",
                origin="orchestration",
                cause_code="research_runtime_anchor_missing",
            )
        # The original execution deadline is distinct from the per-read proof
        # budget. Expiry rejects work; it does not attest current permission.
        absolute_deadline = self._binding_deadline.get()
        active_native = anchor.bundle_hash is not None and not allow_cancelled
        if active_native and absolute_deadline is not None and datetime.now(UTC) >= absolute_deadline:
            raise AgentExecutionError(
                "provider_timeout", "DSH Web session exceeded the product deadline",
                retryable=True, provider_id="dsh-web", origin="orchestration",
                cause_code="dsh_web_deadline_elapsed",
            )
        # Capture context-local inputs here; a DB thread has its own ContextVar context.
        selected = self._selection.get()

        def verify(session: Session):
            # One actual DB thread callback. Fresh originals, hashes and current
            # permissions are re-read; only identical pure JSON shapes reuse.
            with shape_validation_scope():
                if selected is None:
                    verify_runtime_task_anchor(session, anchor, allow_cancelled=allow_cancelled)
                else:
                    verify_runtime_task_and_selection(
                        session,
                        anchor,
                        selected[0],
                        selected[1],
                        selected[2],
                        allow_cancelled=allow_cancelled,
                    )

        deadlines = [
            value
            for value in (
                self._binding_deadline.get(),
                self._proof_operation_deadline.get(),
            )
            if value is not None
        ]
        proof_deadline = min(deadlines) if deadlines else datetime.now(UTC)

        def owned_verify() -> None:
            with shape_validation_scope():
                authenticate_runtime_binding(
                    self.links.database, anchor, selected,
                    deadline_at=proof_deadline, allow_cancelled=allow_cancelled,
                )
        timeout_seconds = (
            min(
                15.0,
                self.proof_read_wait_seconds,
                max(0.0, (proof_deadline - datetime.now(UTC)).total_seconds()),
            )
            if anchor.bundle_hash is not None and not allow_cancelled
            else self.binding_wait_seconds
            if anchor.bundle_hash is not None
            else 0.25
        )
        read = (
            await self._read_binding_with_contention_wait(
                verify, timeout_seconds=timeout_seconds, deadline_at=proof_deadline,
                status_initial_writer_busy=status_initial_writer_busy,
                pre_host_preparation=True, owned_operation=owned_verify,
            )
            if anchor.bundle_hash is not None and not allow_cancelled
            else await self.cancellation_projector.read(verify, timeout_seconds=timeout_seconds)
        )
        deadline_only = (
            read.state == "invalid"
            and read.limitation == "research_context_selection_deadline_elapsed"
        )
        if active_native and (
            deadline_only
            or (absolute_deadline is not None
                and datetime.now(UTC) >= absolute_deadline
                and (read.state == "ready" or (
                    read.state == "pending"
                    and read.limitation == "cancellation_projection_wait_elapsed"
                )))
        ):
            raise AgentExecutionError(
                "provider_timeout", "DSH Web session exceeded the product deadline",
                retryable=True, provider_id="dsh-web", origin="orchestration",
                cause_code=(read.limitation if deadline_only else "dsh_web_deadline_elapsed"),
            )
        if read.state == "ready":
            return
        raise AgentExecutionError(
            "research_method_binding_failed",
            "Original research Task changed or could not be verified",
            retryable=read.state == "pending",
            provider_id="dsh-web",
            origin="orchestration",
            cause_code=read.limitation or "research_runtime_binding_unavailable",
        )

    async def _execute(
        self,
        request: ResearchSessionRequest,
        trace_sink: ResearchTraceSink | None,
        *,
        track_submission: Callable[[DshSessionSubmit], None],
    ) -> ResearchSessionResult:
        initial = self._task_anchor.get()
        readiness_deadline = (
            min(request.deadline_at, initial.deadline_at)
            if (initial is not None and initial.deadline_at is not None)
            else request.deadline_at
        )
        readiness = await self._wait_for_readiness(deadline_at=readiness_deadline)
        runtime_version = (
            f"dsh-web-{readiness.upstream_identity.source_version}"
            f"+{readiness.upstream_identity.plugin_build_hash[:12]}"
        )
        # An observation field is never the authority for a concurrent Run.
        self.runtime_version = runtime_version
        anchor = self._task_anchor.get()
        assert anchor is not None
        if anchor.bundle_hash is not None:
            turn = await self._prepare_v2_turn(request, readiness, runtime_version)
            request, submit = turn.request, turn.submit
            tool_calls_before = turn.tool_calls_before
        else:
            # The first submission fixes the durable run ceiling. Retries and
            # same-session generations must reuse it even when the caller rebuilds
            # a request with a fresh wall-clock deadline.
            existing_link = self.links.get(request.run_id)
            tool_calls_before = existing_link.tool_calls_started if existing_link is not None else 0
            generation = await self._legacy_generation(request)
            submit = self._submission(
                request,
                generation=generation,
                deadline_at=(
                    existing_link.deadline_at
                    if existing_link is not None and existing_link.deadline_at is not None
                    else request.deadline_at
                ),
                request_hash=(existing_link.request_hash if existing_link is not None else None),
                max_tool_calls=(
                    existing_link.max_tool_calls
                    if existing_link is not None and existing_link.max_tool_calls is not None
                    else request.execution_budget.max_tool_calls
                ),
            )
            definition = method_definition(
                request,
                submit,
                readiness.upstream_identity,
                runtime_id=self.runtime_id,
                runtime_version=runtime_version,
                profile_ref=self._profile_for(request),
                prompt_policy_hash=PROMPT_POLICY_HASH,
            )
            try:
                pack_receipt = ResearchPackService(self.links.database).get(request.run_id)
                if pack_receipt is not None:
                    definition["request_pack_hash"] = pack_receipt["definition_hash"]
                policy = pack_receipt["definition"].get("research_policy") if pack_receipt else None
                instruction = render_research_policy(policy, request.allowed_capabilities)
                if instruction:
                    definition["research_policy_renderer_hash"] = RENDERER_HASH
                    definition["research_policy_prompt_hash"] = hashlib.sha256(
                        instruction.encode()
                    ).hexdigest()
                ResearchMethodService(self.links.database).freeze(
                    request.run_id,
                    definition,
                    request_hash=submit.request_hash,
                    existing_session=existing_link is not None,
                )
            except ValueError as exc:
                raise AgentExecutionError(
                    "research_method_binding_failed",
                    "Research method receipt changed or failed integrity verification",
                    provider_id="dsh-web",
                    origin="orchestration",
                    cause_code="research_method_binding_failed",
                ) from exc
            existing_prompt = self.links.get_prompt(request.run_id, submit.generation)
            if existing_prompt is None:
                prompt = build_trusted_web_research_prompt(request)
                if instruction:
                    prompt = instruction + "\n\n" + prompt
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
                    ),
                    execution_request=request,
                )
            else:
                if instruction and not existing_prompt.prompt.startswith(instruction + "\n\n"):
                    raise AgentExecutionError(
                        "research_method_binding_failed",
                        "Saved prompt does not match the frozen Pack instructions",
                        provider_id="dsh-web",
                        origin="orchestration",
                        cause_code="research_policy_prompt_mismatch",
                    )
                if existing_prompt.request_hash != submit.request_hash:
                    raise AgentExecutionError(
                        "dsh_run_request_conflict",
                        "A different canonical request is already linked to this product Run",
                        provider_id="dsh-web",
                        origin="orchestration",
                        cause_code="request_hash_conflict",
                    )
                self.links.reserve(submit, readiness.upstream_identity)
        track_submission(submit)
        accepted_before_submit = self._durable_accepted(submit)
        accepted = (
            accepted_before_submit
            if accepted_before_submit is not None
            else await self._retry_host_call(
                lambda: self.client.submit(submit),
                deadline_at=submit.deadline_at,
                operation="submit",
                recover_after_failure=lambda error: self._recover_new_durable_acceptance(
                    submit, accepted_before_submit, error
                ),
                at_most_once_after_send=True,
            )
        )
        _validate_reply_identity(accepted, submit, operation="accepted")
        self.links.accepted(accepted)
        fallback_used = False
        result_payload = await self._wait_for_result(request, submit)
        # The durable Host result records the real remote execution. Re-read
        # before projecting it into a product research result; a drift must not
        # turn a returned transcript into a validated product report.
        await self._assert_task_binding()
        run = _sdk_run(result_payload)
        traces = map_notifications(
            run.notifications,
            run_id=request.run_id,
            research_session_id=run.session_id,
            received_at=result_payload.finished_at,
            request=request,
        )
        original_anchor = self._task_anchor.get()
        accepted_synthesis_only = (
            original_anchor is not None and original_anchor.bundle_hash is not None
        )
        try:
            mapped = map_session_result_with_witness(
                run,
                request,
                traces,
                runtime_version=runtime_version,
                profile_ref=self._profile_for(request),
                started_at=result_payload.started_at,
                finished_at=result_payload.finished_at,
                accepted_synthesis_only=accepted_synthesis_only,
            )
            result = mapped.result
            self._mapping_selected(result_payload, mapped.selection, perpetual_synthesis=mapped.perpetual_synthesis)
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
            evidence_only = map_evidence_only_result(
                run,
                request,
                traces,
                runtime_version=runtime_version,
                profile_ref=self._profile_for(request),
                started_at=result_payload.started_at,
                finished_at=result_payload.finished_at,
                failure_code=exc.error_code,
                failure_cause=exc.cause_code or "synthesis_invalid",
            )
            repairable = (
                request.execution_budget.max_structured_repairs >= 1
                and evidence_only.final_coverage.status == "sufficient"
                and (
                    exc.error_code == "structured_output_invalid"
                    or (
                        exc.error_code == "dsh_evidence_unattested"
                        and exc.cause_code == "synthesis_attestation"
                    )
                )
            )
            if repairable:
                # A same-run synthesis repair returns a later DSH generation,
                # but any native independent review was created during the
                # original generation. Preserve the original start timestamp
                # so review time validation covers the full logical run.
                initial_result_started_at = result_payload.started_at
                validation_errors = (
                    synthesis_evidence_ref_errors(
                        run,
                        request,
                        accepted_only=accepted_synthesis_only,
                    )
                    if exc.error_code == "dsh_evidence_unattested"
                    else [
                        {
                            "loc": [],
                            "type": "structured_output_invalid",
                            "msg": str(exc),
                        }
                    ]
                )
                try:
                    previous_last_seq = _latest_event_seq(run.notifications)
                    result_payload = await self._repair_synthesis(
                        request,
                        submit,
                        readiness,
                        runtime_version=runtime_version,
                        validation_errors=validation_errors,
                        allowed_evidence_ids=trusted_evidence_ids(
                            run,
                            request,
                            accepted_only=accepted_synthesis_only,
                        ),
                        track_submission=track_submission,
                    )
                    await self._assert_task_binding()
                    repaired_run = _sdk_run(result_payload)
                    run = repaired_run
                    traces = map_notifications(
                        run.notifications,
                        run_id=request.run_id,
                        research_session_id=run.session_id,
                        received_at=result_payload.finished_at,
                        request=request,
                    )
                    _validate_repair_tool_boundary(
                        run.notifications,
                        after_seq=previous_last_seq,
                        skill_repair_policy=self._skill_repair_policy.get(),
                    )
                    mapped = map_session_result_with_witness(
                        run,
                        request,
                        traces,
                        runtime_version=runtime_version,
                        profile_ref=self._profile_for(request),
                        started_at=initial_result_started_at,
                        finished_at=result_payload.finished_at,
                        accepted_synthesis_only=accepted_synthesis_only,
                    )
                    policy = self._skill_repair_policy.get()
                    if policy is not None:
                        try:
                            verify_repair = getattr(self.skill_preparation, "verify_repair_result", None)
                            if verify_repair is None:
                                raise ValueError("skill_repair_consumption_reader_required")
                            await verify_repair(self.cancellation_projector,
                                run_id=request.run_id, generation=policy["generation"], mapped=mapped,
                                deadline_at=request.deadline_at, clock=lambda: datetime.now(UTC))
                        except ValueError as error:
                            raise AgentExecutionError("structured_output_invalid", "Repair method consumption is not verified",
                                provider_id="dsh-web", origin="orchestration", cause_code=str(error)) from error
                    result = mapped.result
                    self._mapping_selected(result_payload, mapped.selection, perpetual_synthesis=mapped.perpetual_synthesis)
                except AgentExecutionError as repaired_exc:
                    if repaired_exc.error_code not in {
                        "dsh_evidence_unattested",
                        "structured_output_invalid",
                    }:
                        raise
                    result = map_evidence_only_result(
                        run,
                        request,
                        traces,
                        runtime_version=runtime_version,
                        profile_ref=self._profile_for(request),
                        started_at=initial_result_started_at,
                        finished_at=result_payload.finished_at,
                        failure_code=repaired_exc.error_code,
                        failure_cause=repaired_exc.cause_code or "synthesis_repair_invalid",
                    )
                    fallback_used = True
                    self._mapping_selected(result_payload, evidence_only_selection())
                    exc = repaired_exc
            else:
                result = evidence_only
                fallback_used = True
                self._mapping_selected(result_payload, evidence_only_selection())
            if trace_sink is not None:
                if fallback_used:
                    for trace in traces:
                        await trace_sink.emit(trace)
                    await trace_sink.emit(
                        _synthesis_rejected_trace(
                            request,
                            run,
                            result,
                            result_payload,
                            exc,
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
        result = await self._account_result_tool_calls(
            result,
            request,
            generation=result_payload.generation,
            research_session_id=result_payload.dsh_session_id,
            tool_calls_started=durable_link.tool_calls_started,
            tool_calls_before=tool_calls_before,
        )
        if trace_sink is not None and not fallback_used:
            for trace in traces:
                await trace_sink.emit(trace)
        await self._assert_task_binding()
        return result

    async def _account_result_tool_calls(
        self,
        result: ResearchSessionResult,
        request: ResearchSessionRequest,
        *,
        generation: int,
        research_session_id: str,
        tool_calls_started: int,
        tool_calls_before: int,
    ) -> ResearchSessionResult:
        if request.output_schema_ref in PERPETUAL_OUTPUT_SCHEMA_REFS:
            # Recovery reuses the complete native generation. Read its complete
            # Gateway reservations, as the original writer does, rather than
            # only the calls added after this invocation resumed.
            deadline = min(request.deadline_at, self._binding_deadline.get() or request.deadline_at)
            read = await self._read_binding_with_contention_wait(
                lambda session: normalize_generation_tool_accounting(
                    session, result, run_id=request.run_id, generation=generation,
                    research_session_id=research_session_id,
                    max_tool_calls=request.execution_budget.max_tool_calls,
                ),
                timeout_seconds=min(self.proof_read_wait_seconds,
                                    max(0.0, (deadline - datetime.now(UTC)).total_seconds())),
                deadline_at=deadline,
                continue_until_deadline=True,
            )
            if read.state != "ready" or read.value is None:
                raise AgentExecutionError(
                    "dsh_tool_budget_accounting_invalid" if read.state == "invalid"
                    else "dsh_tool_budget_unavailable",
                    "Original generation Gateway accounting could not be confirmed",
                    retryable=read.state == "pending",
                    provider_id="dsh-web",
                    origin="orchestration",
                    cause_code=read.limitation or "durable_tool_count_unavailable",
                )
            return read.value
        executed_this_generation = tool_calls_started - tool_calls_before
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
        return _normalize_tool_accounting(result, executed_this_generation)

    async def _legacy_generation(self, request: ResearchSessionRequest) -> int:
        result = await self.cancellation_projector.read(
            lambda session: read_legacy_generation(session, request),
        )
        if result.state == "ready" and result.value is not None:
            return result.value
        raise AgentExecutionError(
            "research_method_binding_failed",
            "Original research round could not be verified",
            retryable=result.state == "pending",
            provider_id="dsh-web",
            origin="orchestration",
            cause_code=result.limitation or "research_runtime_binding_unavailable",
        )

    async def _record_transport(
        self,
        reply: DshSessionAccepted | DshSessionStatus | DshSessionResult | None,
        *,
        phase: str | None = None,
        error: AgentExecutionError | None = None,
        timeout_seconds: float | None = None,
        confirmation_deadline_at: datetime | None = None,
    ) -> ObservedExecutionRef | None:
        observations = self._observations.get()
        if observations is None or observations.context is None:
            # No V2 upgrade of the original legacy path or readiness response.
            return
        context = observations.context
        key = None
        if isinstance(reply, DshSessionStatus):
            phase = phase or "status"
            key = (
                reply.run_id,
                reply.dsh_session_id,
                reply.generation,
                phase,
                reply.state,
                reply.error_code,
            )
            if key in observations.seen_states:
                return
        elif isinstance(reply, DshSessionAccepted):
            phase = "accepted"
        elif isinstance(reply, DshSessionResult):
            phase = "result"
        else:
            phase = "error"
        value = ResearchTransportObservation.model_validate(
            {
                "schema_version": "research-transport-observation.v1",
                "context": context,
                "phase": phase,
                "reply": reply,
                "error_code": (error.cause_code or error.error_code) if error is not None else None,
                "error": ResearchObservedError.from_error(error) if error is not None else None,
                "recorded_at": datetime.now(UTC),
                "events_json_hash": hashlib.sha256(reply.events_json.encode()).hexdigest()
                if isinstance(reply, DshSessionResult)
                else None,
                "final_response_hash": hashlib.sha256(reply.final_response.encode()).hexdigest()
                if isinstance(reply, DshSessionResult)
                else None,
            }
        )

        def record(session: Session) -> ObservedExecutionRef:
            with shape_validation_scope():
                return store_transport_observation(session, value)

        # Exact re-entry observes this invocation's one pending write. It never
        # starts a replacement writer or invents a later observation timestamp.
        # _store's JSON round-trip makes defaults explicit before its identity
        # hash. Use that same complete projection for this private locator/key.
        identity = observation_sha(value.model_dump(mode="json", exclude={"recorded_at"}))
        reported_code = (
            _safe_cancel_code(reply.error_code)
            if isinstance(reply, DshSessionStatus) else None
        )
        diagnostic = {
            "schema_version": "research-transport-confirmation.v1",
            "run_id": context.run_id,
            "dsh_session_id": context.dsh_session_id,
            "generation": context.generation,
            "context_hash": observation_sha(context.model_dump(mode="json")),
            "phase": phase,
            "observation_identity": identity,
            "expected_output_id": "research_transport_" + identity,
            "expected_role": "research_transport:" + identity,
            "reported_native_error_code": reported_code,
            "native_error_authentication": "unverified" if reported_code else "unknown",
        }
        latest_stage: str | None = None
        observed_operation_id: str | None = None

        def stage(operation_id: str, stage_name: str, code: str | None) -> None:
            nonlocal latest_stage, observed_operation_id
            latest_stage, observed_operation_id = stage_name, operation_id
            fields = {
                **diagnostic, "operation_id": operation_id, "stage": stage_name,
                "latest_stage": stage_name,
                "storage_state": "submission_pending" if stage_name == "queued" else stage_name,
                "storage_code": _safe_cancel_code(code),
                "observed_at": datetime.now(UTC).isoformat(),
            }
            emit = log.warning if stage_name == "failed" else log.info
            emit("research_transport_confirmation %s", json.dumps(fields, sort_keys=True))

        def report(
            outcome: ProjectionRead[ObservedExecutionRef],
            handle: ProjectionPending[ObservedExecutionRef] | None,
        ) -> None:
            # Diagnostic only: late completion never mutates Latest,
            # Run/Link, permission, generation, or mapped-result state.
            fields = {
                **diagnostic,
                "operation_id": handle.operation_id if handle else observed_operation_id,
                "latest_stage": handle.latest_stage if handle else latest_stage,
                "observed_at": datetime.now(UTC).isoformat(),
                "storage_state": "confirmed" if outcome.state == "ready" else outcome.state,
                "storage_code": _safe_cancel_code(outcome.limitation),
                "output_id": outcome.value.output_id if outcome.value is not None else None,
                "payload_hash": outcome.value.payload_hash if outcome.value is not None else None,
                "reference": (
                    outcome.value.model_dump(mode="json") if outcome.value is not None else None
                ),
            }
            log.warning("research_transport_confirmation %s", json.dumps(fields, sort_keys=True))

        # Admission rejection submitted no callback. Retain this exact typed
        # reply/value while waiting for a slot; once accepted, poll only that
        # Future. An uncertain write or a finished rejection is never replayed.
        # Only the normal Host-call seam supplies its original deadline. Direct
        # cancellation/error/grace-period audits retain the original short wait.
        # A retryable transport failure is part of that same invocation: wait
        # for its one accepted audit Future within the immutable Run deadline,
        # then retry the idempotent Host operation. Native/provider failures do
        # not gain this longer confirmation window.
        deadlines = [
            deadline for deadline in (
                confirmation_deadline_at,
                self._binding_deadline.get(), self._proof_operation_deadline.get(),
            ) if deadline is not None
        ]
        confirmation_deadline = (
            min(deadlines)
            if (confirmation_deadline_at is not None and timeout_seconds is None
                and phase in {"accepted", "status", "result", "error"} and deadlines)
            else None
        )
        pending = observations.pending_transports.get(identity)
        while True:
            if (confirmation_deadline is not None
                    and datetime.now(UTC) >= confirmation_deadline):
                written = ProjectionRead(
                    "pending", limitation="cancellation_projection_wait_elapsed", pending=pending,
                )
                break
            if observations.context != context:
                raise AgentExecutionError(
                    "research_observation_invalid",
                    "The transport confirmation belongs to a different Runtime context",
                    cause_code="research_observation_context_changed",
                )
            if pending is not None:
                written = pending.poll()
            else:
                write_wait = (
                    self.binding_wait_seconds if timeout_seconds is None else timeout_seconds
                )
                if confirmation_deadline is not None:
                    write_wait = min(
                        write_wait,
                        max(0.0, (confirmation_deadline - datetime.now(UTC)).total_seconds()),
                    )
                written = await self.cancellation_projector.write(
                    record, timeout_seconds=write_wait, stage_observer=stage,
                )
                if written.pending is not None:
                    pending = written.pending
                    observations.pending_transports[identity] = pending
                    report(written, pending)
                    handle = pending
                    pending.observe(lambda outcome: report(outcome, handle))
            waiting_for_slot = (
                pending is None and observed_operation_id is None
                and written.state == "pending"
                and written.limitation == "cancellation_projection_busy"
            )
            waiting_for_original = written.state == "pending" and written.pending is not None
            if confirmation_deadline is None or not (waiting_for_slot or waiting_for_original):
                break
            remaining = (confirmation_deadline - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.025, remaining))
        if (written.state == "ready" and confirmation_deadline is not None
                and datetime.now(UTC) >= confirmation_deadline):
            # Keep the real late commit as diagnostic evidence; it cannot make
            # an expired invocation current or authorize another Host operation.
            report(written, pending)
            raise AgentExecutionError(
                "research_observation_unavailable",
                "Transport confirmation exceeded the original invocation deadline",
                retryable=True, cause_code="cancellation_projection_wait_elapsed",
            )
        if written.state != "ready" or written.value is None:
            if written.pending is None:
                report(written, pending)
            failure = AgentExecutionError(
                "research_observation_unavailable",
                "The actual transport observation was not durably confirmed",
                retryable=written.state == "pending",
                cause_code=written.limitation or "research_observation_write_unconfirmed",
            )
            if pending is not None:
                failure.add_note(
                    f"Transport audit operation {pending.operation_id} remains unconfirmed"
                )
            raise failure
        if observations.context != context:
            raise AgentExecutionError(
                "research_observation_invalid",
                "The confirmed transport belongs to a different Runtime context",
                cause_code="research_observation_context_changed",
            )
        observations.latest = written.value
        observations.latest_generation = context.generation
        if isinstance(reply, DshSessionResult):
            observations.result_refs[observation_sha(reply.model_dump(mode="json"))] = written.value
        if isinstance(reply, DshSessionStatus):
            assert key is not None
            observations.seen_states.add(key)
        return written.value

    def _mapping_selected(
        self,
        payload: DshSessionResult,
        selection: ResearchSynthesisSelectionWitness,
        *, perpetual_synthesis: MappedPerpetualSynthesis | None = None,
    ) -> None:
        observations = self._observations.get()
        if observations is None or observations.context is None:
            return
        # _sdk_run preserves every original array position. Retain complete
        # decoded event identity, including wrapper keys it does not consume.
        if selection.kind == "tool_capture":
            events = json.loads(payload.events_json)
            call_index, result_index = selection.call_event_index, selection.result_event_index
            if (
                not isinstance(events, list)
                or call_index is None
                or result_index is None
                or not 0 <= call_index < result_index < len(events)
            ):
                raise AgentExecutionError(
                    "research_observation_invalid",
                    "Selected Tool events have no original transcript witness",
                )
            selection = selection.model_copy(
                update={
                    "call_event_hash": observation_sha(events[call_index]),
                    "result_event_hash": observation_sha(events[result_index]),
                }
            )
        observations.mapping_payload, observations.selection = payload, selection
        if perpetual_synthesis is not None:
            native = perpetual_synthesis.selection
            events = json.loads(payload.events_json)
            start, end = native.call_event_index, native.result_event_index
            if start is None or end is None or not 0 <= start < end < len(events):
                raise AgentExecutionError("research_observation_invalid", "V2 selected Tool events have no original native witness")
            native = native.model_copy(update={"call_event_hash": observation_sha(events[start]), "result_event_hash": observation_sha(events[end])})
            perpetual_synthesis = replace(perpetual_synthesis, selection=native)
        observations.perpetual_synthesis = perpetual_synthesis

    async def _mapped_observation(self, result: ResearchSessionResult) -> ObservedResearchResult:
        observations = self._observations.get()
        expected = self._task_anchor.get()
        if (
            observations is None
            or observations.context is None
            or expected is None
            or observations.mapping_payload is None
            or observations.selection is None
            or observations.latest is None
        ):
            raise AgentExecutionError(
                "research_observation_unavailable",
                "The mapper has no original Runtime transport witness",
            )
        payload = observations.mapping_payload
        reference = observations.result_refs.get(observation_sha(payload.model_dump(mode="json")))
        if reference is None:
            raise AgentExecutionError(
                "research_observation_unavailable",
                "The selected mapping input has no confirmed transport receipt",
            )
        common = dict(
            context=observations.context,
            mapping_input_ref=reference,
            latest_transport_ref=observations.latest,
            mapping_input_generation=payload.generation,
            latest_transport_generation=observations.latest_generation,
            result=result,
            result_content_hash=research_result_content_hash(result),
            recorded_at=datetime.now(UTC),
        )
        if observations.perpetual_synthesis is not None:
            native = observations.perpetual_synthesis
            value = ResearchMappedObservationV2(schema_version="research-mapped-observation.v2", **common,
                perpetual_synthesis=ResearchPerpetualSynthesisObservation.model_validate({
                    "synthesis": native.synthesis.model_dump(mode="json"), "review_request": native.review_request.model_dump(mode="json"),
                    "authorization": native.authorization.model_dump(mode="json") if native.authorization else None,
                    "selection": native.selection.model_dump(mode="json"), "raw_candidate_hash": native.raw_candidate_hash,
                    "review_wrapper_json": native.review_wrapper_json, "review_call_id": native.review_call_id,
                    "review_call_event_index": native.review_call_event_index, "review_result_event_index": native.review_result_event_index,
                }))
        else:
            value = ResearchMappedObservation(schema_version="research-mapped-observation.v1", selection=observations.selection, **common)

        def map_original(session: Session) -> ObservedExecutionRef:
            with shape_validation_scope():
                store = store_mapped_perpetual_observation if isinstance(value, ResearchMappedObservationV2) else store_mapped_observation
                return store(
                    session,
                    value,
                    binding_validator=lambda current: verify_runtime_task_anchor(current, expected),
                )

        # Mapping replays full native originals in this physical writer. Its
        # confirmation purpose differs from a short raw transport audit write.
        # The one Future may finish after a pending wait; never replay it or
        # admit the mapped result before its actual commit is confirmed.
        deadlines = [
            deadline
            for deadline in (
                expected.deadline_at,
                self._binding_deadline.get(),  # Original Task and caller request minimum.
                self._proof_operation_deadline.get(),
            )
            if deadline is not None
        ]
        confirmation_deadline = min(deadlines) if deadlines else datetime.now(UTC)
        pending: ProjectionPending[ObservedExecutionRef] | None = None

        async def drain_original(
            handle: ProjectionPending[ObservedExecutionRef],
        ) -> ProjectionRead[ObservedExecutionRef]:
            # This invocation owns this exact native writer until its Session
            # exits. Draining is not evidence of rollback or permission to admit.
            observed = handle.poll()
            while observed.pending is handle:
                await asyncio.sleep(0.025)
                observed = handle.poll()
            return observed

        def deadline_error() -> AgentExecutionError:
            return AgentExecutionError(
                "provider_timeout",
                "Mapped observation consumed the original deadline",
                retryable=True, provider_id="dsh-web", origin="orchestration",
                cause_code="dsh_web_mapping_deadline_elapsed",
            )

        remaining = (confirmation_deadline - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            raise deadline_error()
        try:
            written = await self.cancellation_projector.write(
                map_original,
                timeout_seconds=min(30.0, self.mapping_confirmation_wait_seconds, remaining),
                # Cancellation before the pending handle is returned still owns
                # and drains the same physical writer, including repeated cancel.
                drain_on_cancel=True,
            )
            pending = written.pending
            while pending is not None and written.pending is pending:
                remaining = (confirmation_deadline - datetime.now(UTC)).total_seconds()
                if remaining <= 0:
                    written = await drain_original(pending)
                    break
                await asyncio.sleep(min(0.025, remaining))
                written = pending.poll()
        except asyncio.CancelledError:
            if pending is not None:
                while True:
                    try:
                        await drain_original(pending)
                        break
                    except asyncio.CancelledError:
                        continue
            raise
        if written.state != "ready" or written.value is None:
            raise AgentExecutionError(
                "research_observation_unavailable",
                "The mapped observation failed original native writer verification",
                retryable=written.state == "pending",
                cause_code=written.limitation or "research_observation_mapping_unconfirmed",
            )
        # A real late commit remains auditable, but cannot authorize an expired
        # invocation to hand its mapped result to the caller.
        if datetime.now(UTC) >= confirmation_deadline:
            raise deadline_error()
        return ObservedResearchResult(result=result, observation_ref=written.value)

    def _profile_for(self, request: ResearchSessionRequest) -> str:
        # This chooses the candidate identifier only. Preparation independently
        # authenticates the saved method; parsing a row grants no new capability.
        anchor = self._task_anchor.get()
        if anchor is None or anchor.bundle_hash is None:
            return self.profile_ref
        from packages.kernel.decision_hub_kernel.decision.research_baseline import (
            baseline_profile_for,
        )
        from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
        with self.links.database.session() as session:
            row = session.query(RunRoleOutputRecord).filter_by(
                run_id=request.run_id, role="research_execution_method").one_or_none()
            if row is not None:
                value = json.loads(row.payload_json).get("definition", {}).get("profile_ref")
                if not isinstance(value, str) or not value:
                    raise ValueError("research_baseline_saved_profile_invalid")
                return value
        return baseline_profile_for(request, self.profile_ref)

    async def _prepare_v2_turn(
        self,
        request: ResearchSessionRequest,
        readiness: DshHostReadiness,
        runtime_version: str,
        *,
        repair_errors: Sequence[Mapping[str, object]] | None = None,
        repair_evidence_ids: list[str] | None = None,
    ) -> RuntimePreparedTurn:
        expected = self._task_anchor.get()
        if expected is None:
            raise AgentExecutionError(
                "research_method_binding_failed",
                "Missing preparation checkpoint",
                provider_id="dsh-web",
                origin="orchestration",
                cause_code="research_runtime_anchor_missing",
            )
        selected = self._selection.get()
        # The projector uses a plain thread executor, so task ContextVars
        # must be resolved here rather than read inside the writer callback.
        selected_profile = self._profile_for(request)
        policy_ref = None
        self._skill_repair_policy.set(None)
        before_repair = getattr(self.skill_preparation, "before_repair", None)
        if repair_errors is not None and before_repair is not None:
            try:
                policy_ref = await before_repair(self.cancellation_projector, run_id=request.run_id,
                    source_generation=expected.generation, request=request, errors=repair_errors,
                    evidence_ids=repair_evidence_ids or [], lease_owner=selected[1] if selected else None,
                    deadline_at=min(expected.deadline_at or request.deadline_at, request.deadline_at),
                    clock=lambda: datetime.now(UTC))
            except ValueError as error:
                raise AgentExecutionError("structured_output_invalid", "Skill repair policy was not authorized",
                    provider_id="dsh-web", origin="orchestration", cause_code=str(error)) from error

        def prepare(checkpoint, phase) -> RuntimePreparedTurn:
            with shape_validation_scope():
                return prepare_runtime_turn(
                    self.links.database,
                    expected,
                    request,
                    readiness.upstream_identity,
                    checkpoint=checkpoint,
                    stage=phase,
                    deadline_at=confirmation_deadline,
                    runtime_id=self.runtime_id,
                    runtime_version=runtime_version,
                    profile_ref=selected_profile,
                    prompt_policy_hash=PROMPT_POLICY_HASH,
                    repair_errors=repair_errors,
                    repair_evidence_ids=repair_evidence_ids,
                    skill_repair_policy_ref=policy_ref,
                    selection_reference=selected[0] if selected is not None else None,
                    expected_lease_owner=selected[1] if selected is not None else None,
                )

        # The observation window is not a preparation deadline. An accepted
        # physical writer keeps its exact Future until completion; it is never
        # submitted again or handed to Worker recovery while still running.
        confirmation_deadline = min(
            expected.deadline_at or request.deadline_at,
            request.deadline_at,
            self._proof_operation_deadline.get() or request.deadline_at,
        )
        started = monotonic()
        latest_stage: str | None = None
        operation_id: str | None = None
        pending: ProjectionPending[RuntimePreparedTurn] | None = None
        written: ProjectionRead[RuntimePreparedTurn] | None = None
        saw_pending = False
        final_state = "unavailable"

        def report(state: str, code: str | None = None) -> None:
            # Fixed lifecycle receipts only: no prompts, SQL, payloads, or
            # per-poll logging. This is diagnostic, never execution authority.
            log.warning("research_preparation_confirmation %s", json.dumps({
                "run_id": request.run_id, "operation_id": operation_id,
                "stage": latest_stage, "state": state,
                "elapsed_seconds": round(monotonic() - started, 3),
                "observed_at": datetime.now(UTC).isoformat(),
                "code": _safe_cancel_code(code),
                "native_result_state": written.state if written is not None else None,
            }, sort_keys=True))

        def stage(identity: str, name: str, code: str | None) -> None:
            nonlocal operation_id, latest_stage
            operation_id, latest_stage = identity, name
            if name in {"begin_acquired", "operation_completed", "commit_done", "failed"}:
                report(name, code)

        async def drain_original(
            handle: ProjectionPending[RuntimePreparedTurn],
        ) -> ProjectionRead[RuntimePreparedTurn]:
            # Only the handle returned by this invocation's write is observed.
            # Completion means the native callback and its Session have exited;
            # it is not evidence of rollback (a late commit remains possible).
            result = handle.poll()
            while result.pending is handle:
                await asyncio.sleep(0.025)
                result = handle.poll()
            return result

        def deadline_error() -> AgentExecutionError:
            return AgentExecutionError(
                "provider_timeout",
                "Execution preparation consumed the original deadline",
                retryable=True, provider_id="dsh-web", origin="orchestration",
                cause_code="dsh_web_preparation_deadline_elapsed",
            )

        remaining = (confirmation_deadline - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            raise deadline_error()
        try:
            written = await self.cancellation_projector.write_owned(
                prepare,
                timeout_seconds=min(30.0, self.preparation_confirmation_wait_seconds, remaining),
                stage_observer=stage,
            )
            pending = written.pending
            if written.state == "pending" and pending is not None:
                saw_pending = True
                report("pending", written.limitation)
                while written.pending is pending:
                    remaining = (confirmation_deadline - datetime.now(UTC)).total_seconds()
                    if remaining <= 0:
                        # Deadline ends authority, not the native thread. Wait
                        # for its exit before releasing execution ownership.
                        written = await drain_original(pending)
                        break
                    await asyncio.sleep(min(0.025, remaining))
                    written = pending.poll()
            final_state = (
                "deadline_elapsed" if datetime.now(UTC) >= confirmation_deadline
                else written.state
            )
        except asyncio.CancelledError:
            # If the initial write was cancelled, its opt-in native drain has
            # already completed. Later cancellation drains this same handle;
            # repeated cancel signals cannot release a still-running callback.
            if pending is not None:
                pending.request_cancel()
                while True:
                    try:
                        written = await drain_original(pending)
                        break
                    except asyncio.CancelledError:
                        continue
            final_state = "cancelled_after_native_exit"
            raise
        finally:
            if saw_pending or final_state == "cancelled_after_native_exit":
                report(final_state, written.limitation if written is not None else None)
        if written.state != "ready" or written.value is None:
            # A completed callback's real invalid/busy/error classification is
            # preserved. No retry or second prepare is performed here.
            raise AgentExecutionError(
                "research_method_binding_failed",
                "Execution preparation was not confirmed",
                retryable=written.state == "pending",
                provider_id="dsh-web", origin="orchestration",
                cause_code=written.limitation or "research_runtime_preparation_unavailable",
            )
        turn = written.value
        # Late commits can be recovered by a later authenticated invocation.
        # They cannot advance this expired invocation's anchor or enter Host.
        if datetime.now(UTC) >= min(confirmation_deadline, turn.submit.deadline_at):
            raise deadline_error()
        self._skill_repair_policy.set(turn.skill_repair_policy)
        self._task_anchor.set(turn.anchor)
        observations = self._observations.get()
        if observations is not None:
            observations.context = turn.observation_context
        if (self.skill_preparation is None
                and request.output_schema_ref == DECISION_OUTPUT_SCHEMA_REF
                and turn.submit.generation == 1):
            raise _skill_preparation_error(
                ValueError("skill_decision_selection_not_confirmed:not_installed"),
                run_id=request.run_id, anchor=turn.anchor, projector=self.cancellation_projector,
            )
        if self.skill_preparation is not None:
            try:
                skill_status = await self.skill_preparation.after_prepared(
                    self.cancellation_projector, run_id=request.run_id,
                    generation=turn.submit.generation, deadline_at=turn.submit.deadline_at,
                    clock=lambda: datetime.now(UTC),
                    expected_lease_owner=selected[1] if selected is not None else None,
                )
                if (request.output_schema_ref == DECISION_OUTPUT_SCHEMA_REF
                        and turn.submit.generation == 1
                        and skill_status not in {"selected", "original_selection_retained"}):
                    # A decision Task cannot enter its first model step without
                    # the real selected original. Legacy and repair scope stay
                    # frozen; slow preparation continues its existing Future.
                    safe_status = skill_status if isinstance(skill_status, str) and re.fullmatch(r"[a-z_]{1,80}", skill_status) else "unknown"
                    raise ValueError("skill_decision_selection_not_confirmed:" + safe_status)
            except ValueError as error:
                raise _skill_preparation_error(
                    error, run_id=request.run_id, anchor=turn.anchor, projector=self.cancellation_projector
                ) from error
        return turn

    async def _repair_synthesis(
        self,
        request: ResearchSessionRequest,
        submit: DshSessionSubmit,
        readiness: DshHostReadiness,
        *,
        runtime_version: str,
        validation_errors: Sequence[Mapping[str, object]],
        allowed_evidence_ids: list[str],
        track_submission: Callable[[DshSessionSubmit], None] | None = None,
    ) -> DshSessionResult:
        """Run one same-session generation that may only resubmit synthesis."""

        anchor = self._task_anchor.get()
        if anchor is not None and anchor.bundle_hash is not None:
            turn = await self._prepare_v2_turn(
                request,
                readiness,
                runtime_version,
                repair_errors=validation_errors,
                repair_evidence_ids=allowed_evidence_ids,
            )
            repair_submit = turn.submit
            if track_submission is not None:
                track_submission(repair_submit)
            accepted_before_submit = self._durable_accepted(repair_submit)
            accepted = (
                accepted_before_submit
                if accepted_before_submit is not None
                else await self._retry_host_call(
                    lambda: self.client.submit(repair_submit),
                    deadline_at=repair_submit.deadline_at,
                    operation="synthesis_repair_submit",
                    recover_after_failure=lambda error: self._recover_new_durable_acceptance(
                        repair_submit, accepted_before_submit, error
                    ),
                    at_most_once_after_send=True,
                )
            )
            _validate_reply_identity(accepted, repair_submit, operation="accepted")
            self.links.accepted(accepted)
            return await self._wait_for_result(turn.request, repair_submit)

        generation = submit.generation + 1
        session_id, request_id = DshSessionLinkService.deterministic_ids(
            request.run_id,
            submit.request_hash,
            generation,
        )
        repair_submit = submit.model_copy(
            update={
                "deterministic_session_id": session_id,
                "deterministic_request_id": request_id,
                "prompt_ref": f"hub://runs/{request.run_id}/prompts/{generation}",
                "generation": generation,
            }
        )
        prompt = build_synthesis_tool_repair_prompt(
            request,
            session_id,
            validation_errors,
            allowed_evidence_ids,
        )
        pack = ResearchPackService(self.links.database).get(request.run_id)
        policy = pack["definition"].get("research_policy") if pack else None
        instruction = render_research_policy(policy, request.allowed_capabilities)
        if instruction:
            prompt = instruction + "\n\n" + prompt
        existing_prompt = self.links.get_prompt(request.run_id, generation)
        self.links.reserve(repair_submit, readiness.upstream_identity)
        if existing_prompt is None:
            self.links.store_prompt(
                DshSessionPrompt(
                    schema_version="dsh-session-prompt.v1",
                    run_id=request.run_id,
                    dsh_session_id=session_id,
                    request_id=request_id,
                    request_hash=submit.request_hash,
                    generation=generation,
                    prompt=prompt,
                ),
                execution_request=request,
            )
        elif (
            existing_prompt.dsh_session_id != session_id
            or existing_prompt.request_id != request_id
            or existing_prompt.request_hash != submit.request_hash
            or existing_prompt.prompt != prompt
        ):
            raise AgentExecutionError(
                "dsh_run_request_conflict",
                "The persisted synthesis repair prompt differs from this retry",
                provider_id="dsh-web",
                origin="orchestration",
                cause_code="repair_prompt_conflict",
            )
        if track_submission is not None:
            track_submission(repair_submit)
        accepted_before_submit = self._durable_accepted(repair_submit)
        accepted = (
            accepted_before_submit
            if accepted_before_submit is not None
            else await self._retry_host_call(
                lambda: self.client.submit(repair_submit),
                deadline_at=repair_submit.deadline_at,
                operation="synthesis_repair_submit",
                recover_after_failure=lambda error: self._recover_new_durable_acceptance(
                    repair_submit, accepted_before_submit, error
                ),
                at_most_once_after_send=True,
            )
        )
        _validate_reply_identity(accepted, repair_submit, operation="accepted")
        self.links.accepted(accepted)
        return await self._wait_for_result(request, repair_submit)

    async def _wait_for_result(
        self, request: ResearchSessionRequest, submit: DshSessionSubmit
    ) -> DshSessionResult:
        while True:
            # The durable DSH link owns the Run deadline. A caller may rebuild a
            # request while recovering a worker, but that must not extend the
            # original product budget or make a late Host result unreachable.
            remaining = (submit.deadline_at - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                partial = await self._cancel_and_recover_partial(submit, "product_deadline_elapsed")
                if partial is not None:
                    return partial
                raise AgentExecutionError(
                    "provider_timeout",
                    "DSH Web session exceeded the product deadline",
                    retryable=True,
                    provider_id="dsh-web",
                    origin="orchestration",
                    cause_code="dsh_web_deadline_elapsed",
                    deadline_ms=round(request.execution_budget.total_deadline_seconds * 1000),
                )
            try:
                status = await self._retry_host_call(
                    lambda: self.client.status(submit),
                    deadline_at=submit.deadline_at,
                    operation="status",
                )
            except AgentExecutionError as exc:
                if exc.retryable and datetime.now(UTC) >= submit.deadline_at:
                    partial = await self._cancel_and_recover_partial(
                        submit, "product_deadline_elapsed"
                    )
                    if partial is not None:
                        return partial
                # Keep the original timeout or transport/binding provenance.
                raise
            _validate_reply_identity(status, submit, operation="status")
            if status.state not in {"completed", "failed", "cancelled"}:
                try:
                    await self._observe_status(status, submit)
                except AgentExecutionError as exc:
                    if not (
                        exc.retryable
                        and datetime.now(UTC) >= submit.deadline_at
                    ):
                        raise
                    partial = await self._cancel_and_recover_partial(
                        submit, "product_deadline_elapsed"
                    )
                    if partial is not None:
                        return partial
                    raise AgentExecutionError(
                        "provider_timeout",
                        "DSH Web session exceeded the product deadline",
                        retryable=True,
                        provider_id="dsh-web",
                        origin="orchestration",
                        cause_code="dsh_web_deadline_elapsed",
                        deadline_ms=round(
                            request.execution_budget.total_deadline_seconds * 1000
                        ),
                    ) from exc
                current_remaining = (
                    submit.deadline_at - datetime.now(UTC)
                ).total_seconds()
                if current_remaining > 0:
                    await asyncio.sleep(
                        min(self.poll_interval_seconds, current_remaining)
                    )
                continue
            status, original = await self._terminal_original(status, submit)
            if status.state == "completed":
                result = await self._retry_host_call(
                    lambda: self.client.result(submit),
                    deadline_at=submit.deadline_at,
                    operation="result",
                )
                _validate_result(result, submit)
                await self._complete_terminal_result(status, submit, result, original)
                return result
            if status.state == "failed":
                # An authenticated failed original with neither result field is
                # authoritative evidence that no result snapshot exists. Do
                # not ask the Host to manufacture or bind one after terminal;
                # preserve the original failure instead. Any missing original
                # or partially populated binding still needs the Host's normal
                # result authority and validation below.
                if (
                    original is not None
                    and original.result_ref is None
                    and original.result_hash is None
                ):
                    raise _terminal_failure_error(status, original)
                # A provider can fail after one or more capability calls have
                # already returned trusted evidence. The Host exposes that
                # bounded turn transcript as a failed partial result. Preserve
                # the terminal failure while allowing the normal attestation
                # mapper to recover only successful research tool results.
                try:
                    partial = await self._retry_host_call(
                        lambda: self.client.result(submit),
                        deadline_at=submit.deadline_at,
                        operation="failed_result",
                    )
                except AgentExecutionError as exc:
                    if not _terminal_result_unavailable(exc):
                        raise
                else:
                    _validate_result(partial, submit)
                    await self._complete_terminal_result(status, submit, partial, original)
                    return partial
            if status.state == "cancelled":
                try:
                    async with asyncio.timeout(self.terminal_reconciliation_seconds):
                        await self._assert_task_binding()
                        partial = await self.client.result(submit)
                        await self._record_transport(partial)
                except TimeoutError:
                    partial = None
                except AgentExecutionError as exc:
                    if not _terminal_result_unavailable(exc):
                        raise
                    partial = None
                if partial is not None:
                    _validate_result(partial, submit)
                    await self._complete_terminal_result(status, submit, partial, original)
                    return partial
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
            if original is None:
                status, original = await self._complete_empty_terminal(status, submit, completion)
            raise _terminal_failure_error(status, original)

    async def _cancel_and_recover_partial(
        self, submit: DshSessionSubmit, reason: str
    ) -> DshSessionResult | None:
        """Stop model work at the deadline, then reconcile local trusted receipts.

        The short grace period does not extend model or tool execution. It only
        lets the loopback Host project a turn/end event and the already-written
        research results into a canonical evidence-only payload.
        """

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.terminal_reconciliation_seconds
        try:
            async with asyncio.timeout(self.terminal_reconciliation_seconds):

                def accepted_original(session: Session) -> bool:
                    link = session.get(DshSessionLinkRecord, submit.run_id, populate_existing=True)
                    return bool(
                        link is not None
                        and link.accepted_at is not None
                        and link.dsh_session_id == submit.deterministic_session_id
                        and link.generation == submit.generation
                        and link.request_hash == submit.request_hash
                    )

                accepted = await self.cancellation_projector.read(accepted_original)
                if accepted.state != "ready" or accepted.value is not True:
                    return None
                await self._assert_task_binding(allow_cancelled=True)
                status = await self.client.cancel(submit, reason)
                await self._record_transport(status, phase="cancel")
                _validate_reply_identity(status, submit, operation="cancel")
                while status.state not in {"completed", "failed", "cancelled"}:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        return None
                    await asyncio.sleep(min(self.poll_interval_seconds, remaining))
                    await self._assert_task_binding(allow_cancelled=True)
                    status = await self.client.status(submit)
                    await self._record_transport(status)
                    _validate_reply_identity(status, submit, operation="status")
                status, original = await self._terminal_original(status, submit)
                try:
                    await self._assert_task_binding(allow_cancelled=True)
                    partial = await self.client.result(submit)
                    await self._record_transport(partial)
                except AgentExecutionError as exc:
                    if _terminal_result_unavailable(exc):
                        if original is None and status.state in {"failed", "cancelled"}:
                            # Retain the real terminal reply even when the Host
                            # has no partial transcript. Never invent a result.
                            await self._complete_empty_terminal(
                                status, submit,
                                DshSessionCompletion(
                                    schema_version="dsh-session-completion.v1",
                                    run_id=submit.run_id,
                                    dsh_session_id=submit.deterministic_session_id,
                                    terminal_status=(
                                        "failed" if status.state == "failed" else "cancelled"
                                    ),
                                    generation=submit.generation,
                                    last_seq=status.last_seq,
                                    trace_ref=None,
                                    result_ref=None,
                                    result_hash=None,
                                    completed_at=status.observed_at,
                                    error=(
                                        DshBridgeError(
                                            code=status.error_code or "dsh_session_failed",
                                            message=(
                                                "DSH Web returned terminal failure "
                                                "without a partial result"
                                            ),
                                            retryable=True,
                                        )
                                        if status.state == "failed"
                                        else None
                                    ),
                                )
                            )
                        return None
                    raise
                _validate_result(partial, submit)
                await self._complete_terminal_result(status, submit, partial, original)
                return partial
        except AgentExecutionError as exc:
            if exc.error_code in {
                "research_method_binding_failed",
                "dsh_protocol_invalid",
                "dsh_generation_conflict",
            }:
                raise
            return None
        except (TimeoutError, ValueError):
            # The primary timeout remains authoritative when the Host cannot
            # materialize a trusted partial result within the cleanup grace.
            return None

    async def _terminal_original(
        self, status: DshSessionStatus, submit: DshSessionSubmit,
    ) -> tuple[DshSessionStatus, DshSessionCompletion | None]:
        """A transport observation cannot replace an authenticated callback.

        Read the original receipt and its Task in one fresh physical snapshot.
        This grants no result: bytes, hash and snapshot cutoff are checked
        separately, and a missing result still exposes the original failure.
        """
        def read_original(session: Session) -> DshSessionCompletion | None:
            link = session.get(DshSessionLinkRecord, submit.run_id, populate_existing=True)
            if link is None or (
                link.dsh_session_id != submit.deterministic_session_id
                or link.generation != submit.generation
                or link.request_hash != submit.request_hash
            ):
                raise ValueError("dsh_terminal_original_submission_invalid")
            verified = read_authenticated_terminal_in_session(
                session, submit.run_id, now=datetime.now(UTC),
            )
            return verified.completion if verified is not None else None

        now = datetime.now(UTC)
        remaining = (submit.deadline_at - now).total_seconds()
        # After Stop/deadline this is only local reconciliation of existing
        # receipts, inside the caller's bounded cleanup grace; no model work.
        reconciliation_deadline = (
            submit.deadline_at
            if remaining > 0
            else now + timedelta(seconds=self.terminal_reconciliation_seconds)
        )
        read = await self._read_binding_with_contention_wait(
            read_original,
            timeout_seconds=min(
                self.proof_read_wait_seconds,
                remaining if remaining > 0 else self.terminal_reconciliation_seconds,
            ),
            deadline_at=reconciliation_deadline,
            continue_until_deadline=True,
        )
        if read.state != "ready":
            raise AgentExecutionError(
                "research_terminal_original_unavailable",
                "The original authenticated Host terminal could not be verified",
                retryable=read.state == "pending", provider_id="dsh-web",
                origin="orchestration",
                cause_code=read.limitation or "dsh_terminal_original_read_pending",
            )
        original = read.value
        if original is None:
            return status, None
        return DshSessionStatus.model_validate({
            **status.model_dump(mode="json"),
            "state": original.terminal_status,
            "last_seq": original.last_seq,
            "observed_at": original.completed_at,
            "error_code": original.error.code if original.error else None,
        }), original

    async def _complete_terminal_result(
        self,
        status: DshSessionStatus,
        submit: DshSessionSubmit,
        result: DshSessionResult,
        original: DshSessionCompletion | None = None,
    ) -> None:
        if status.state not in {"completed", "failed", "cancelled"}:
            raise ValueError("dsh_terminal_result_state_invalid")
        if original is not None and original.result_hash is not None:
            _validate_result(result, submit)
            _validate_terminal_result_metadata(result, submit, original)
            trace = f"dsh://sessions/{submit.deterministic_session_id}?last_seq={result.last_seq}"
            if (
                original.run_id != submit.run_id
                or original.dsh_session_id != submit.deterministic_session_id
                or original.generation != submit.generation
                or original.terminal_status != status.state
                or original.last_seq != status.last_seq
                or original.result_ref != f"dsh-host://runs/{submit.run_id}/result"
                or original.result_hash != result.result_hash
                or original.trace_ref != result.trace_ref or result.trace_ref != trace
                or result.last_seq > original.last_seq
                or (
                    result.finished_at > original.completed_at
                    if original.terminal_status == "cancelled"
                    else original.completed_at != result.finished_at
                )
                or result.started_at > result.finished_at
            ):
                raise AgentExecutionError(
                    "dsh_terminal_result_original_mismatch",
                    "The returned result differs from the authenticated terminal original",
                    provider_id="dsh-web", origin="dsh",
                    cause_code="terminal_result_snapshot_mismatch",
                )
            # Callback already committed these exact bytes. The drain cursor
            # may exceed the result snapshot cursor; neither is rewritten.
            return
        error = (
            DshBridgeError(
                code=status.error_code or "dsh_session_failed",
                message="DSH Web session failed after returning trusted research tool results",
                retryable=True,
            )
            if status.state == "failed"
            else None
        )
        completion = DshSessionCompletion(
                schema_version="dsh-session-completion.v1",
                run_id=submit.run_id,
                dsh_session_id=submit.deterministic_session_id,
                terminal_status=cast(str, status.state),  # type: ignore[arg-type]
                generation=submit.generation,
                last_seq=result.last_seq,
                trace_ref=result.trace_ref,
                result_ref=f"dsh-host://runs/{submit.run_id}/result",
                result_hash=result.result_hash,
                completed_at=result.finished_at,
                error=error,
        )
        try:
            await self._complete_original_result(completion, submit)
        except ValueError as error:
            if str(error) != "dsh_terminal_conflict":
                raise
            # A callback can commit after the first RO observation. Reconcile
            # once against its authenticated original, without another writer
            # or remote result/model call. A legacy Link grants no authority.
            confirmed, late = await self._terminal_original(status, submit)
            if late is None or late.result_hash is None:
                raise
            await self._complete_terminal_result(confirmed, submit, result, late)

    async def _complete_empty_terminal(
        self, status: DshSessionStatus, submit: DshSessionSubmit,
        completion: DshSessionCompletion,
    ) -> tuple[DshSessionStatus, DshSessionCompletion | None]:
        try:
            await self._complete_original_result(completion, submit)
        except ValueError as error:
            if str(error) != "dsh_terminal_conflict":
                raise
            confirmed, late = await self._terminal_original(status, submit)
            if late is None:
                raise
            return confirmed, late
        return status, None

    async def _wait_for_readiness(self, *, deadline_at: datetime) -> DshHostReadiness:
        remaining = (deadline_at - datetime.now(UTC)).total_seconds()
        if remaining <= max(0.001, self.poll_interval_seconds):
            raise AgentExecutionError(
                "provider_timeout",
                "DSH Web readiness exceeded the product deadline",
                retryable=True,
                provider_id="dsh-web",
                origin="orchestration",
                cause_code="dsh_web_readiness_deadline_elapsed",
            )
        async def require_ready() -> DshHostReadiness:
            readiness = await self.client.readiness()
            if readiness.ready:
                return readiness
            error_code = readiness.error_code or "dsh_host_not_ready"
            raise AgentExecutionError(
                error_code,
                "DSH Web Host is not ready for a canonical research session",
                retryable=error_code
                in {"host_hub_unreachable", "host_not_ready", "dsh_host_not_ready"},
                provider_id="dsh-web",
                origin="dsh",
                cause_code=error_code,
            )

        return await self._retry_host_call(
            require_ready,
            deadline_at=deadline_at,
            operation="readiness",
            network_grace_seconds=self.readiness_grace_seconds,
        )

    async def _complete_original_result(
        self,
        completion: DshSessionCompletion,
        submit: DshSessionSubmit,
    ) -> None:
        """Confirm the returned reply; observe one original proof and writer.

        Original authentication keeps its caller/Task deadline. The local
        storage slice starts at the first physical writer attempt. Only a known
        unacquired BEGIN can retry; cancellation drains its original callback.
        """
        completion = DshSessionCompletion.model_validate(completion.model_dump(mode="json"))
        confirmation_deadline = min(value for value in (
            submit.deadline_at, self._binding_deadline.get(),
            self._proof_operation_deadline.get(),
        ) if value is not None)
        writer_deadline: datetime | None = None
        initial_writer_busy: Exception | None = None
        while True:
            token = self._proof_operation_deadline.set(confirmation_deadline)
            try:
                await self._assert_task_binding()
            finally:
                self._proof_operation_deadline.reset(token)
            now = datetime.now(UTC)
            if now >= confirmation_deadline:
                raise AgentExecutionError(
                    "research_completion_storage_pending",
                    "The original completed Host result could not be durably confirmed",
                    retryable=True, provider_id="dsh-web", origin="orchestration",
                    cause_code="dsh_completion_initial_writer_busy",
                ) from initial_writer_busy
            if (writer_deadline is None
                    or (initial_writer_busy is not None and now >= writer_deadline)):
                # Only a definitely unacquired BEGIN may observe another local
                # writer slice; its original absolute deadline never changes.
                writer_deadline = min(
                    confirmation_deadline,
                    now + timedelta(seconds=self.status_confirmation_wait_seconds),
                )
            # A private runner owns this exact callback through Session close;
            # it introduces no shared queue or another native executor.
            try:
                await HeavyWorkAdmission().run_thread(self.links.complete, completion)
                return
            except Exception as error:
                if not is_sqlite_initial_begin_busy(error):
                    raise
                initial_writer_busy = error
                remaining = (writer_deadline - datetime.now(UTC)).total_seconds()
                if remaining <= 0:
                    continue
                await asyncio.sleep(min(self.poll_interval_seconds, remaining))

    async def _observe_status(
        self,
        status: DshSessionStatus,
        submit: DshSessionSubmit,
    ) -> None:
        """Confirm this exact reply without repeating Host/model operations.

        Full RO proof has its original caller/Task ceiling. A 30s storage slice
        begins at actual writer admission, never before original proof. Only an
        unacquired BEGIN permits another attempt; every accepted callback drains.
        """
        selected = self._selection.get()
        confirmation_deadline = min(value for value in (
            submit.deadline_at, self._binding_deadline.get(),
            self._proof_operation_deadline.get(),
        ) if value is not None)
        writer_deadline: datetime | None = None
        initial_writer_busy: Exception | None = None
        while True:
            known_initial_busy = initial_writer_busy is not None
            token = self._proof_operation_deadline.set(confirmation_deadline)
            try:
                await self._assert_task_binding(
                    status_initial_writer_busy=known_initial_busy,
                )
            finally:
                self._proof_operation_deadline.reset(token)
            now = datetime.now(UTC)
            if now >= confirmation_deadline:
                raise AgentExecutionError(
                    "research_status_storage_pending",
                    "The original Host status could not be durably confirmed",
                    retryable=True, provider_id="dsh-web", origin="orchestration",
                    cause_code="dsh_status_initial_writer_busy",
                ) from initial_writer_busy
            if writer_deadline is None or (known_initial_busy and now >= writer_deadline):
                if writer_deadline is not None:
                    log.warning(
                        "dsh_status_confirmation_pending run_id=%s session=%s "
                        "generation=%s request_hash=%s",
                        _safe_cancel_code(submit.run_id),
                        _safe_cancel_code(submit.deterministic_session_id),
                        submit.generation, submit.request_hash,
                    )
                # Renew a local slice only after a known unstarted BEGIN. The
                # submission/binding/caller deadline remains the original one.
                writer_deadline = min(
                    confirmation_deadline,
                    now + timedelta(seconds=self.status_confirmation_wait_seconds),
                )
            try:
                await HeavyWorkAdmission().run_thread(
                    self.links.observe,
                    status,
                    expected_request_hash=submit.request_hash,
                    expected_lease_owner=selected[1] if selected is not None else None,
                )
                return
            except Exception as error:
                if not is_sqlite_initial_begin_busy(error):
                    raise
                initial_writer_busy = error
                remaining = (writer_deadline - datetime.now(UTC)).total_seconds()
                if remaining > 0:
                    await asyncio.sleep(min(self.poll_interval_seconds, remaining))

    async def _retry_host_call(
        self,
        call: Callable[[], Awaitable[_T]],
        *,
        deadline_at: datetime,
        operation: str,
        network_grace_seconds: float | None = None,
        recover_after_failure: Callable[[AgentExecutionError], _T | None] | None = None,
        at_most_once_after_send: bool = False,
    ) -> _T:
        """Retry only transient Host transport failures within the Run budget.

        DSH Web is a separate process. A short restart, proxy hiccup, or HTTP
        5xx must not turn a Host session that later completed into a product
        failure. Authentication, protocol, permission, and other non-retryable
        errors still fail immediately. The helper is intentionally local to
        this adapter so it does not introduce another orchestration state
        machine.
        """

        attempts = 0
        last_error: AgentExecutionError | None = None
        # A readiness grace measures real Host attempts. Complete local Task
        # authentication before starting it; once started it never moves.
        network_deadline: datetime | None = None
        proof_deadline = min(value for value in (
            deadline_at, self._binding_deadline.get(), self._proof_operation_deadline.get(),
        ) if value is not None)
        while True:
            effective_deadline = min(deadline_at, network_deadline or deadline_at)
            remaining = (effective_deadline - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                if last_error is not None:
                    raise _retry_deadline_error(last_error, attempts, operation)
                raise AgentExecutionError(
                    "provider_timeout",
                    f"DSH Web {operation} exceeded the product deadline",
                    retryable=True,
                    provider_id="dsh-web",
                    origin="orchestration",
                    cause_code=f"dsh_web_{operation}_deadline_elapsed",
                )
            # Local original authentication is not a Host transport attempt.
            # A pending read still owns its real slot; re-entering it here can
            # submit duplicate proof reads. Preserve its exact original error.
            proof_token = self._proof_operation_deadline.set(proof_deadline)
            try:
                await self._assert_task_binding()
            finally:
                self._proof_operation_deadline.reset(proof_token)
            if datetime.now(UTC) >= effective_deadline:
                # A valid local read can consume the last original budget.
                continue
            if network_grace_seconds is not None and network_deadline is None:
                network_deadline = min(
                    deadline_at,
                    datetime.now(UTC) + timedelta(seconds=network_grace_seconds),
                )
                effective_deadline = network_deadline
            remaining = (effective_deadline - datetime.now(UTC)).total_seconds()
            try:
                log.info("research_host_phase operation=%s state=send_started", operation)
                reply = await call()
                log.info("research_host_phase operation=%s state=reply_received", operation)
                if isinstance(reply, (DshSessionAccepted, DshSessionStatus, DshSessionResult)):
                    await self._record_transport(
                        reply, confirmation_deadline_at=effective_deadline,
                    )
                return reply
            except asyncio.CancelledError:
                raise
            except AgentExecutionError as exc:
                if exc.error_code == "research_observation_unavailable":
                    # The real DB future may still commit. Pending storage is
                    # not a transient Host error and cannot authorize another RPC.
                    raise
                try:
                    await self._record_transport(
                        None,
                        error=exc,
                        confirmation_deadline_at=(
                            effective_deadline
                            if exc.retryable and exc.origin == "transport"
                            else None
                        ),
                    )
                except AgentExecutionError as audit_error:
                    # The real call's failure remains primary. An uncertain
                    # audit forbids retrying the Host call or its writer.
                    exc.add_note(
                        "Failure audit is unconfirmed: "
                        + (audit_error.cause_code or audit_error.error_code)
                    )
                    for note in getattr(audit_error, "__notes__", ()):
                        exc.add_note(note)
                    raise exc from audit_error
                if not exc.retryable:
                    raise
                if recover_after_failure is not None:
                    recovered = recover_after_failure(exc)
                    if recovered is not None:
                        return recovered
                if at_most_once_after_send and exc.origin == "transport":
                    # A PUT may have reached the Host even when its reply was
                    # lost. Without an exact durable acceptance there is no
                    # safe evidence that replay cannot overlap the first
                    # handler, so preserve the original transport provenance.
                    exc.add_note("At-most-once Host submit forbids automatic PUT replay")
                    raise
                attempts += 1
                last_error = exc
                # Keep the retry budget short and deterministic: 1x, 2x, 4x,
                # then a capped delay. The product deadline remains the hard
                # ceiling, so a Host restart cannot create an unbounded wait.
                remaining = (
                    effective_deadline - datetime.now(UTC)
                ).total_seconds()
                delay = min(
                    self.poll_interval_seconds * (2 ** min(attempts - 1, 3)),
                    remaining,
                )
                if delay <= 0:
                    raise _retry_deadline_error(exc, attempts, operation) from exc
                await asyncio.sleep(delay)

    def _durable_accepted(
        self, submit: DshSessionSubmit
    ) -> DshSessionAccepted | None:
        """Recover an exact Host acceptance callback after its HTTP reply was lost.

        The Host records acceptance through a separate authenticated callback
        before it finishes prompt assembly and returns HTTP 202.  A timeout on
        that response must not resubmit or fail a session whose immutable link
        already proves the same command generation.
        """

        link = self.links.get(submit.run_id)
        expected_session, expected_request = DshSessionLinkService.deterministic_ids(
            submit.run_id, submit.request_hash, submit.generation
        )
        if (
            link is None
            or link.accepted_at is None
            or link.dsh_session_id != submit.deterministic_session_id
            or submit.deterministic_session_id != expected_session
            or submit.deterministic_request_id != expected_request
            or link.request_hash != submit.request_hash
            or link.generation != submit.generation
            or link.deadline_at is None
            or link.deadline_at.astimezone(UTC)
            != submit.deadline_at.astimezone(UTC)
            or link.max_tool_calls != submit.max_tool_calls
            or link.model_step_timeout_ms != submit.model_step_timeout_ms
            or link.state
            not in {
                "admitted",
                "running",
                "idle",
                "completed",
                "failed",
                "cancelled",
            }
        ):
            return None
        return DshSessionAccepted(
            schema_version="dsh-session-accepted.v1",
            run_id=submit.run_id,
            dsh_session_id=submit.deterministic_session_id,
            accepted_at=link.accepted_at,
            generation=submit.generation,
        )

    def _recover_new_durable_acceptance(
        self,
        submit: DshSessionSubmit,
        accepted_before_submit: DshSessionAccepted | None,
        error: AgentExecutionError,
    ) -> DshSessionAccepted | None:
        """Recover only a newly persisted acceptance behind a lost HTTP reply.

        An explicit Host HTTP error can occur after the acceptance callback but
        before prompt assembly or queueing finishes.  Treating that callback as
        a successful submit would hide the Host failure and leave an idle Link.
        The shortcut therefore applies only to a client response timeout and
        only when this call changed the exact tuple from unaccepted to accepted.
        """

        if accepted_before_submit is not None:
            return None
        accepted = self._durable_accepted(submit)
        if accepted is None:
            return None
        if (
            error.origin == "transport"
            and error.error_code == "provider_timeout"
            and error.cause_code == "dsh_host_timeout"
        ):
            return accepted
        # Once the Host durably accepted this exact tuple, another PUT could
        # repeat the prompt/model/tool path.  Preserve the original failure and
        # let the Worker close Run + Link atomically; a user retry creates a new
        # product Run with a new audit identity.
        error.add_note("Exact durable acceptance forbids automatic submit replay")
        raise error

    async def _quiesce_failed_invocation(self, submit: DshSessionSubmit | None) -> None:
        """Native-only drain for this invocation while its original owner still holds the lease.

        This never completes a Link, changes the primary exception, or recovers
        a historical failed Run. A missing/late proof is only unconfirmed.
        """
        selected = self._selection.get()
        drain = getattr(self.client, "before_failed_terminal_quiesce", None)
        if submit is None or selected is None or not callable(drain):
            return
        expected_owner = selected[1]
        if not expected_owner:
            return

        def original(session: Session) -> TerminalQueueAuthority:
            value = terminal_queue_authority_in_session(session, submit.run_id)
            if (
                value.purpose != "before_failed_terminal"
                or value.lease_owner != expected_owner
                or value.run_id != submit.run_id
                or value.dsh_session_id != submit.deterministic_session_id
                or value.generation != submit.generation
                or value.request_hash != submit.request_hash
            ):
                raise ValueError("terminal_queue_failed_invocation_mismatch")
            return value

        diagnostic = {
            "run_id": submit.run_id,
            "dsh_session_id": submit.deterministic_session_id,
            "generation": submit.generation,
            "request_hash": submit.request_hash,
            "native_terminal_projected": False,
        }

        def report(state: str, code: str | None = None) -> None:
            log.warning("dsh_failed_invocation_quiescence %s", json.dumps({
                **diagnostic, "state": state, "code": _safe_cancel_code(code),
                "observed_at": datetime.now(UTC).isoformat(),
            }, sort_keys=True))

        try:
            async with asyncio.timeout(self.terminal_reconciliation_seconds):
                before = await self.cancellation_projector.read(
                    original, timeout_seconds=self.terminal_reconciliation_seconds,
                )
                if before.state != "ready" or before.value is None:
                    report("unconfirmed", before.limitation)
                    return
                authority = before.value
                receipt = await drain(authority)
                after = await self.cancellation_projector.read(
                    original, timeout_seconds=self.terminal_reconciliation_seconds,
                )
                if (
                    after.state != "ready" or after.value is None
                    or after.value.model_dump(exclude={"observed_at"})
                    != authority.model_dump(exclude={"observed_at"})
                    or any(getattr(receipt, key) != getattr(authority, key) for key in (
                        "run_id", "dsh_session_id", "generation", "request_hash",
                        "original_hash", "terminal_hash", "native_state", "terminal_at",
                    ))
                    or receipt.quiet is not True
                    or receipt.observed_at < authority.observed_at
                    or receipt.observed_at > datetime.now(UTC)
                ):
                    report("unconfirmed", "terminal_queue_original_changed")
                    return
                report("quiet_confirmed")
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            report("unconfirmed", "terminal_queue_failed_invocation_wait_elapsed")
        except Exception:
            report("unconfirmed", "terminal_queue_failed_invocation_unconfirmed")

    async def _cancel_active_owner_best_effort(
        self,
        submit: DshSessionSubmit | None,
        *,
        caller: Literal["runtime_agent_error", "runtime_cancelled_error", "runtime_exception"],
        error: AgentExecutionError | None = None,
    ) -> None:
        """Clean up this authenticated invocation, not a recovered historical session.

        A Stop revokes the active execution proof. Its separate fresh terminal
        authority permits only stopping the exact prepared Host tuple; it never
        permits execution, result admission, or changing a historical terminal.
        """
        anchor = self._task_anchor.get()
        loop = asyncio.get_running_loop()
        started = loop.time()
        diagnostic: dict[str, object] = {
            "caller": caller,
            "run_id": submit.run_id if submit else (anchor.run_id if anchor else None),
            "dsh_session_id": submit.deterministic_session_id if submit else None,
            "generation": submit.generation if submit else None,
            "request_hash": submit.request_hash if submit else None,
            "authority": "not_checked",
            "reply_validated": False,
            "audit_state": "not_attempted",
            "projection_state": "not_attempted",
            "original_error_code": _safe_cancel_code(error.error_code) if error else None,
            "original_cause_code": _safe_cancel_code(error.cause_code) if error else None,
        }

        def report(stage: str, limitation: str | None = None) -> None:
            diagnostic["stage"] = stage
            diagnostic["elapsed_seconds"] = round(loop.time() - started, 6)
            if limitation is not None or "limitation" not in diagnostic:
                diagnostic["limitation"] = _safe_cancel_code(limitation)
            # Warnings remain visible in a worker without an INFO handler.
            # This closed diagnostic never includes prompt text or exception messages.
            log.warning("dsh_owner_cancel %s", json.dumps(diagnostic, sort_keys=True))

        report("entered")
        if submit is None or anchor is None or anchor.run_id != submit.run_id:
            report("skipped", "owner_cancel_context_missing_or_mismatched")
            return
        if anchor.generation > 0 and anchor.generation != submit.generation:
            report("skipped", "owner_cancel_anchor_generation_mismatched")
            return
        session_id, request_id = DshSessionLinkService.deterministic_ids(
            submit.run_id,
            submit.request_hash,
            submit.generation,
        )
        if (session_id, request_id) != (
            submit.deterministic_session_id,
            submit.deterministic_request_id,
        ):
            report("skipped", "owner_cancel_submission_identity_mismatched")
            return

        def owner_cancelled(session: Session) -> bool:
            # DshCancellationProjector.read owns one fresh physical RO snapshot.
            run = session.get(RunRecord, submit.run_id, populate_existing=True)
            link = session.get(DshSessionLinkRecord, submit.run_id, populate_existing=True)
            return bool(
                run is not None
                and run.status == "cancelled"
                and run.error_code == "cancelled_by_owner"
                and run.finished_at is not None
                and run.lease_owner is None
                and run.lease_expires_at is None
                and run.event_id == anchor.event_id
                and run.snapshot_id == anchor.snapshot_id
                and link is not None
                and link.dsh_session_id == session_id
                and link.generation == submit.generation
                and link.request_hash == submit.request_hash
            )

        owner_authorized = False
        interrupted = False

        async def cleanup() -> None:
            nonlocal owner_authorized
            stage = "authority"
            try:
                async with asyncio.timeout(_OWNER_CANCEL_RPC_SECONDS):
                    if anchor.bundle_hash is None:
                        # Keep the original legacy full-proof cleanup semantics.
                        if await self.cancellation_projector.authorizes_cancel(
                            submit.run_id,
                            session_id,
                            submit.generation,
                            submit.request_hash,
                        ):
                            owner_authorized = True
                            diagnostic["authority"] = "legacy_authorized"
                            report("legacy_cleanup")
                            await self._cancel_best_effort(submit, "product_run_cancelled")
                            report("legacy_cleanup_unconfirmed")
                        else:
                            diagnostic["authority"] = "not_authorized"
                            report("skipped", "owner_cancel_not_authorized")
                        return
                    authorized = await self.cancellation_projector.read(owner_cancelled)
                    if authorized.state != "ready" or authorized.value is not True:
                        diagnostic["authority"] = authorized.state
                        report("skipped", authorized.limitation or "owner_cancel_not_authorized")
                        return
                    owner_authorized = True
                    diagnostic["authority"] = "authorized"
                    stage = "rpc"
                    report("rpc_started")
                    # No accepted_at requirement: Host admission can precede its
                    # DB acknowledgement. The Host still fences this exact tuple.
                    status = await self.client.cancel(submit, "product_run_cancelled")
                    stage = "reply_identity"
                    _validate_reply_identity(status, submit, operation="cancel")
                    diagnostic.update(
                        {
                            "reply_validated": True,
                            "reply_state": status.state,
                            "reply_last_seq": status.last_seq,
                            "reply_error_code": _safe_cancel_code(status.error_code),
                            "reply_sha256": observation_sha(status.model_dump(mode="json")),
                        }
                    )
                    report("reply_validated")
                # A near-five-second reply still receives its independent short
                # audit tail. No RPC or uncertain writer callback is replayed.
                stage = "audit_tail"
                async with asyncio.timeout(_OWNER_CANCEL_AUDIT_SECONDS):
                    stage = "audit"
                    try:
                        async with asyncio.timeout(0.75):
                            reference = await self._record_transport(
                                status,
                                phase="cancel",
                                timeout_seconds=0.75,
                            )
                    except AgentExecutionError as exc:
                        diagnostic["audit_state"] = "pending" if exc.retryable else "unconfirmed"
                        diagnostic["audit_error_code"] = _safe_cancel_code(
                            exc.cause_code or exc.error_code,
                        )
                    except TimeoutError:
                        diagnostic["audit_state"] = "pending"
                        diagnostic["audit_error_code"] = "owner_cancel_audit_wait_elapsed"
                    else:
                        if reference is not None:
                            diagnostic["audit_state"] = "confirmed"
                            diagnostic["audit_output_id"] = reference.output_id
                            diagnostic["audit_payload_hash"] = reference.payload_hash
                        else:
                            observations = self._observations.get()
                            diagnostic["audit_state"] = (
                                "deduplicated"
                                if observations is not None and observations.context is not None
                                else "context_unavailable"
                            )
                    report("audit_finished")
                    # A status reply proves this cancel RPC's observed state,
                    # not the Host's full completion. Its timestamp and absent
                    # trace must not preempt the authenticated callback writer.
                    # The Run is already cancelled; keep the exact transport
                    # observation and let the original completion finish Link.
                    diagnostic["projection_state"] = (
                        "awaiting_authenticated_completion"
                        if status.state == "cancelled"
                        else "reply_not_cancelled"
                    )
                    report("finished")
            except TimeoutError:
                diagnostic[f"{stage}_state"] = "pending"
                report("pending", f"owner_cancel_{stage}_wait_elapsed")
            except AgentExecutionError as exc:
                report("unconfirmed", exc.cause_code or exc.error_code)
            except (Exception, asyncio.CancelledError) as exc:
                # Preserve the original exception and leave unconfirmed cleanup
                # to the existing durable recovery, without replaying this RPC.
                diagnostic["error_class"] = type(exc).__name__
                report("unconfirmed", f"owner_cancel_{stage}_unconfirmed")
                return

        pending = asyncio.create_task(cleanup(), name=f"dsh-owner-stop:{submit.run_id}")
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                # The owner's one-shot watcher may cancel during AgentError
                # cleanup. Await this same bounded task; never start another RPC.
                interrupted = True
                continue
        await pending
        diagnostic["owner_authorized"] = owner_authorized
        diagnostic["caller_interrupted"] = interrupted
        report("joined")
        if interrupted and not owner_authorized:
            # A shutdown while authority was false/pending remains control flow.
            raise asyncio.CancelledError

    async def _cancel_best_effort(self, submit: DshSessionSubmit, reason: str) -> None:
        try:
            await self._assert_task_binding(allow_cancelled=True)
            status = await self.client.cancel(submit, reason)
            await self._record_transport(status, phase="cancel")
            _validate_reply_identity(status, submit, operation="cancel")
            if (
                status.state == "cancelled"
                and status.run_id == submit.run_id
                and status.dsh_session_id == submit.deterministic_session_id
                and status.generation == submit.generation
            ):
                await self.cancellation_projector.project(
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
                    ),
                    submit.request_hash,
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
        generation: int | None = None,
    ) -> DshSessionSubmit:
        effective_request_hash = request_hash or _request_hash(request)
        generation = request.current_round if generation is None else generation
        session_id, request_id = DshSessionLinkService.deterministic_ids(
            request.run_id, effective_request_hash, generation
        )
        return DshSessionSubmit(
            schema_version="dsh-session-submit.v1",
            run_id=request.run_id,
            request_hash=effective_request_hash,
            deterministic_session_id=session_id,
            deterministic_request_id=request_id,
            workspace_ref="decision-hub://workspace/default",
            prompt_ref=(f"hub://runs/{request.run_id}/prompts/{generation}"),
            agent_preset="decision-research",
            permission_ref="decision-hub://permissions/research-only",
            deadline_at=deadline_at or request.deadline_at,
            model_step_timeout_ms=(request.execution_budget.per_model_step_timeout_seconds * 1000),
            max_tool_calls=max_tool_calls or request.execution_budget.max_tool_calls,
            generation=generation,
        )

    async def reconcile_cancelled_runs(
        self,
        *,
        limit: int = 4,
        time_budget_seconds: float = 2,
    ) -> dict[str, object]:
        result = await self.cancelled_reconciler.tick(
            limit=limit,
            time_budget_seconds=time_budget_seconds,
        )
        if self._maintenance_closed or not callable(getattr(self.client, "terminal_quiesce", None)):
            return result
        task = self._terminal_queue_task
        if task is not None and task.done():
            terminal = {"pending": 1} if task.cancelled() else task.result()
            result["terminal_queues"] = terminal
            for key in ("scanned", "attempted", "confirmed", "pending", "skipped"):
                result[key] = int(result.get(key, 0)) + terminal.get(key, 0)
            self._terminal_queue_task = None
        if self._terminal_queue_task is None:
            # Terminal authority is read afresh by the reconciler. One bounded
            # Run per background task; no new task can overlap an existing one.
            self._terminal_queue_task = asyncio.create_task(
                self._reconcile_terminal_queue_once(),
                name="dsh-terminal-queue-recovery",
            )
        if "terminal_queues" not in result:
            result["terminal_queues"] = {"pending": 1, "in_flight": True}
            result["pending"] = int(result.get("pending", 0)) + 1
        return result

    async def _reconcile_terminal_queue_once(self) -> dict[str, int]:
        try:
            async with asyncio.timeout(_TERMINAL_QUEUE_BUDGET_SECONDS):
                return await self.terminal_queue_reconciler.tick(
                    limit=1,
                    time_budget_seconds=_TERMINAL_QUEUE_BUDGET_SECONDS,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Expiry or unavailable authority cannot confirm SDK quiet. They
            # remain maintenance pending without blocking research scheduling.
            return {
                "scanned": 0,
                "attempted": 0,
                "confirmed": 0,
                "pending": 1,
                "skipped": 0,
                "backoff": 0,
            }

    async def close(self) -> None:
        self._maintenance_closed = True
        task = self._terminal_queue_task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if self._terminal_queue_task is task:
                self._terminal_queue_task = None
        try:
            await self.client.close()
        finally:
            await self.cancellation_projector.close()


def _terminal_result_unavailable(error: AgentExecutionError) -> bool:
    return error.error_code == "host_terminal_result_unavailable" or (
        error.error_code == "dsh_host_conflict" and error.cause_code == "http_409"
    )


def _retry_deadline_error(
    error: AgentExecutionError, attempts: int, operation: str
) -> AgentExecutionError:
    """Retain the last stable transport provenance when the Run expires."""

    return AgentExecutionError(
        error.error_code,
        f"DSH Web {operation} retry budget expired: {error}",
        retryable=True,
        attempt=max(error.attempt, attempts),
        provider_id=error.provider_id or "dsh-web",
        model=error.model,
        origin=error.origin,
        cause_code=error.cause_code or f"dsh_web_{operation}_deadline_elapsed",
        capability_id=error.capability_id,
        tool_call_id=error.tool_call_id,
        deadline_ms=error.deadline_ms,
    )


def _validate_reply_identity(
    reply: DshSessionAccepted | DshSessionStatus,
    submit: DshSessionSubmit,
    *,
    operation: str,
) -> None:
    """A physical reply's identity never chooses the durable Run to mutate."""
    if reply.run_id != submit.run_id or reply.dsh_session_id != submit.deterministic_session_id:
        raise AgentExecutionError(
            "dsh_protocol_invalid",
            f"DSH Host {operation} identity does not match the product Run",
            provider_id="dsh-web",
            origin="dsh",
            cause_code=f"{operation}_identity_mismatch",
        )
    if reply.generation != submit.generation:
        raise AgentExecutionError(
            "dsh_generation_conflict",
            f"DSH Host {operation} generation does not match the product Run",
            provider_id="dsh-web",
            origin="dsh",
            cause_code=f"{operation}_generation_mismatch",
        )


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


def _validate_terminal_result_metadata(
    result: DshSessionResult, submit: DshSessionSubmit, original: DshSessionCompletion,
) -> None:
    try:
        validate_terminal_result_native_metadata(result, submit.deterministic_request_id, original)
    except ValueError as error:
        raise AgentExecutionError(
            "dsh_terminal_result_original_mismatch",
            "The result metadata differs from the authenticated native snapshot",
            provider_id="dsh-web", origin="dsh", cause_code="terminal_native_metadata_mismatch",
        ) from error


def _sdk_run(result: DshSessionResult) -> DshSdkRun:
    from .reviewer_instruction_bindings import RESERVED_METHOD, notification_for_result

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
        if method == RESERVED_METHOD:
            raise AgentExecutionError(
                "dsh_protocol_invalid", "DSH Host raw events contain reserved metadata",
                provider_id="dsh-web", origin="dsh", cause_code="reserved_metadata_in_events",
            )
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
    try:
        metadata = notification_for_result(result, notifications)
    except ValueError as exc:
        raise AgentExecutionError(
            "dsh_protocol_invalid", "DSH Host reviewer instruction binding is invalid",
            provider_id="dsh-web", origin="dsh", cause_code="review_instruction_binding_invalid",
        ) from exc
    if metadata is not None:
        notifications.append(metadata)
    return DshSdkRun(
        session_id=result.dsh_session_id,
        final_response=result.final_response,
        finish_reason=result.finish_reason,
        events=(),
        notifications=tuple(notifications),
        session_root=None,
    )


def _latest_event_seq(notifications: tuple[DshSdkNotification, ...]) -> int:
    latest = 0
    for notification in notifications:
        event = notification.payload.get("event")
        if not isinstance(event, Mapping):
            continue
        sequence = event.get("seq")
        if isinstance(sequence, int) and not isinstance(sequence, bool):
            latest = max(latest, sequence)
    return latest


def _validate_repair_tool_boundary(
    notifications: tuple[DshSdkNotification, ...],
    *,
    after_seq: int,
    skill_repair_policy: dict | None = None,
) -> None:
    repair_tools: list[str] = []
    for notification in notifications:
        if notification.method != "session.event":
            continue
        event = notification.payload.get("event")
        if not isinstance(event, Mapping) or event.get("type") != "tool/call":
            continue
        sequence = event.get("seq")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= after_seq:
            continue
        data = event.get("data")
        tool_name = _nested_tool_name(data)
        if tool_name is None:
            raise AgentExecutionError(
                "structured_output_invalid",
                "The synthesis repair generation emitted an unidentified tool call",
                provider_id="dsh-web",
                origin="orchestration",
                cause_code="repair_tool_unidentified",
            )
        repair_tools.append(tool_name)
    if skill_repair_policy is not None:
        from packages.kernel.decision_hub_kernel.application.research_skill_repair_policy import (
            LATE_MODE,
            LATE_TOOLS,
            MODE,
            TOOLS,
        )
        allowed = LATE_TOOLS if skill_repair_policy.get("policy") == LATE_MODE else TOOLS
        if (skill_repair_policy.get("policy") not in (MODE, LATE_MODE)
            or skill_repair_policy.get("allowed_tools") != list(allowed)
            or any(name not in allowed for name in repair_tools)
            or not set(allowed).issubset(repair_tools)
            or len(repair_tools) > skill_repair_policy["max_tool_calls"] - skill_repair_policy["tool_calls_before"]):
            raise AgentExecutionError("structured_output_invalid", "Repair crossed the frozen method tool boundary",
                provider_id="dsh-web", origin="orchestration", cause_code="skill_repair_tool_forbidden")
        return
    if len(repair_tools) != 1 or not repair_tools[0].endswith("decision_hub_synthesis_submit"):
        raise AgentExecutionError(
            "structured_output_invalid",
            "The synthesis repair generation crossed its synthesis-only tool boundary",
            provider_id="dsh-web",
            origin="orchestration",
            cause_code="repair_tool_forbidden",
        )


def _nested_tool_name(value: object) -> str | None:
    if isinstance(value, Mapping):
        for key in ("toolName", "name"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
        for candidate in value.values():
            nested = _nested_tool_name(candidate)
            if nested is not None:
                return nested
    elif isinstance(value, list | tuple):
        for candidate in value:
            nested = _nested_tool_name(candidate)
            if nested is not None:
                return nested
    return None


def _synthesis_rejected_trace(
    request: ResearchSessionRequest,
    run: DshSdkRun,
    result: ResearchSessionResult,
    result_payload: DshSessionResult,
    error: AgentExecutionError,
) -> ResearchTraceEvent:
    return ResearchTraceEvent.model_validate(
        {
            "schema_version": "research-trace-event.v1",
            "run_id": request.run_id,
            "research_session_id": run.session_id,
            "sequence_no": 0,
            "event_type": "session_stopped",
            "occurred_at": result_payload.finished_at,
            "stage": "synthesis",
            "summary": (
                "Synthesis was rejected after the bounded repair; "
                f"{len(result.evidence_candidates)} trusted Evidence item(s) retained."
            ),
            "reference_type": "synthesis",
            "reference_id": request.run_id,
            "status": "degraded",
            "error_code": error.error_code,
            "error": ErrorProvenance(
                error_code=error.error_code,
                origin="orchestration",
                cause_code=error.cause_code or "synthesis_invalid",
                capability_id=None,
                tool_call_id=None,
                retryable=False,
                deadline_ms=None,
            ),
        }
    )
