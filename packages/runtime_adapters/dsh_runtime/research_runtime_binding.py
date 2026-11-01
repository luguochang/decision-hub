"""Runtime checkpoints and borrowed V2 preparation; no Host or transaction ownership."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    DshSessionSubmit,
    DshUpstreamIdentity,
    ResearchExecutionRequestReceiptV2,
    ResearchExecutionRequestReceiptV3,
    ResearchSessionRequest,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import (
    DshSessionLinkService,
    execution_request_role,
    prompt_integrity_role,
    read_execution_request,
    read_prompt_integrity,
)
from packages.kernel.decision_hub_kernel.application.dsh_terminal_receipts import (
    read_authenticated_terminal_metadata_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_binding import (
    FrozenResearchBundle,
)
from packages.kernel.decision_hub_kernel.application.research_context_dispatch import (
    ContextSelectionReference,
    parse_context_selection_reference,
)
from packages.kernel.decision_hub_kernel.application.research_context_execution import (
    assert_selection_permission_in_session,
    fence_selection_permission_in_session,
    has_selection_link_in_session,
    link_role,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    research_evidence_content_hash,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    ExecutionPreparationCandidate,
    FrozenExecutionPreparation,
    ResearchExecutionPreparationService,
    commit_execution_candidate_in_session,
    parse_execution_receipt,
    read_bundle_identity_in_session,
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_method import ROLE as METHOD_ROLE
from packages.kernel.decision_hub_kernel.application.research_policy_render_v2 import (
    render_prepared_research_policy,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    native_observation_context_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.application.research_skill_repair_policy import (
    prompt_policy_reference,
    read_repair_policy_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_task import read_task_request
from packages.kernel.decision_hub_kernel.persistence.callback_source_lease import (
    callback_source_lease_scope,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    DshSessionPromptRecord,
    ResearchEvidenceRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import (
    OriginalPreparationReadsetChanged,
    prepare_originals,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from packages.kernel.decision_hub_kernel.ports.research_observation import (
    ResearchNativeObservationContext,
)

from .profile import build_synthesis_tool_repair_prompt, build_trusted_web_research_prompt

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeTaskAnchor:
    run_id: str
    event_id: str
    snapshot_id: str | None
    task_request_hash: str | None
    bundle_hash: str | None = None
    execution_fingerprint: str | None = None
    generation: int = 0
    deadline_at: datetime | None = None
    requires_selection: bool = False


def _row(session: Session, run_id: str, role: str) -> RunRoleOutputRecord | None:
    return session.scalar(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role == role,
        )
        .execution_options(populate_existing=True)
    )


def _row_identity(row: RunRoleOutputRecord) -> list[str]:
    created = cast(datetime | None, as_utc(row.created_at))
    if created is None:
        raise ValueError("research_runtime_original_time_missing")
    return [row.output_id, row.role, row.schema_version, row.payload_hash, created.isoformat()]


def _assert_repair_evidence(
    session: Session,
    bundle: FrozenResearchBundle,
    request: ResearchSessionRequest,
    references: list[str],
    recorded_at: datetime,
) -> None:
    """Check original inputs or real same-Run accepted SQL material, not slot authority."""
    original = {item.evidence_id: item for item in bundle.task.request.input_evidence}
    selected = {item.evidence_id for item in request.input_evidence}
    requirements = {item.requirement_id for item in bundle.task.request.evidence_requirements}
    cutoff = min(request.pit_cutoff_at, recorded_at)
    for reference in references:
        if reference in selected and reference in original:
            if original[reference].received_at <= cutoff:
                continue
            raise ValueError("research_runtime_repair_evidence_invalid")
        row = session.get(ResearchEvidenceRecord, reference, populate_existing=True)
        if (
            row is None
            or row.run_id != request.run_id
            or row.quality != "accepted"
            or row.requirement_id not in requirements
            or row.capability_id not in bundle.task.request.allowed_capabilities
        ):
            raise ValueError("research_runtime_repair_evidence_invalid")
        received, observed, published = (
            cast(datetime | None, as_utc(row.received_at)),
            cast(datetime | None, as_utc(row.observed_at)),
            as_utc(row.published_at),
        )
        if (
            received is None
            or observed is None
            or received > cutoff
            or observed > received
            or (published is not None and published > observed)
            or row.content_hash
            != research_evidence_content_hash(
                requirement_id=row.requirement_id,
                kind=row.kind,
                authority=row.authority,
                source_id=row.source_id,
                source_url=row.source_url,
                published_at=published,
                excerpt=row.excerpt,
                structured_payload_ref=row.structured_payload_ref,
            )
        ):
            raise ValueError("research_runtime_repair_evidence_invalid")


def _perpetual_prompt_kwargs(
    session: Session,
    request: ResearchSessionRequest,
    *,
    selection_reference: ContextSelectionReference | None = None,
    expected_lease_owner: str | None = None,
) -> dict:
    from packages.kernel.decision_hub_kernel.ports.research_synthesis_protocol import (
        synthesis_protocol,
    )
    if synthesis_protocol(request.output_schema_ref) != "decision_v2":
        return {}
    from importlib import import_module
    try:
        module = import_module("packages.kernel.decision_hub_kernel.application.perpetual_research_binding")
    except ModuleNotFoundError as exc:
        raise ValueError("perpetual_research_materials_unavailable") from exc
    reader = getattr(module, "read_perpetual_research_materials_in_session", None)
    if reader is None:
        raise ValueError("perpetual_research_materials_unavailable")
    materials = reader(
        session,
        request,
        selection_reference=selection_reference,
        expected_lease_owner=expected_lease_owner,
    )
    if not isinstance(materials, dict) or not materials:
        raise ValueError("perpetual_research_materials_unavailable")
    return {"perpetual_materials": materials}


def _selection_reference_from_row(row) -> ContextSelectionReference:
    identity = row.model_dump(mode="json")
    identity["schema_version"] = identity.pop("row_schema_version")
    return parse_context_selection_reference(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def _assert_runtime_prompt_source(
    session: Session,
    bundle: FrozenResearchBundle,
    execution: ResearchExecutionRequestReceiptV2 | ResearchExecutionRequestReceiptV3,
    prompt: str,
) -> None:
    """Recognize exact producers; repair JSON remains declarations, not validator witness."""
    request = ResearchSessionRequest.model_validate(execution.request.model_dump(mode="json"))
    selection_reference = (
        _selection_reference_from_row(execution.current_selection_row)
        if isinstance(execution, ResearchExecutionRequestReceiptV3)
        else None
    )
    prefix = render_prepared_research_policy(bundle, request.allowed_capabilities) + "\n\n"
    if execution.turn_kind == "research_round":
        if prompt != prefix + build_trusted_web_research_prompt(
            request,
            **_perpetual_prompt_kwargs(
                session,
                request,
                selection_reference=selection_reference,
            ),
        ):
            raise ValueError("research_runtime_normal_prompt_invalid")
        return
    lines = prompt.removeprefix(prefix).split("\n")
    from packages.kernel.decision_hub_kernel.ports.research_synthesis_protocol import (
        synthesis_protocol,
    )

    if synthesis_protocol(request.output_schema_ref) in {"decision", "decision_v2"}:
        whitelist_marker = "Allowed admitted Evidence IDs:"
        errors_marker = "Validation errors (paths/types/messages only):"
    else:
        whitelist_marker = (
            "Allowed Evidence ID whitelist (copy exact strings; use no other identifier):"
        )
        errors_marker = "Validation errors from the previous object (paths/types/messages only):"
    try:
        if lines.count(whitelist_marker) != 1 or lines.count(errors_marker) != 1:
            raise ValueError("repair_slots")
        references = json.loads(lines[lines.index(whitelist_marker) + 1])
        errors = json.loads(lines[lines.index(errors_marker) + 1])
        if (
            not isinstance(references, list)
            or any(type(item) is not str for item in references)
            or not isinstance(errors, list)
            or any(not isinstance(item, dict) for item in errors)
        ):
            raise ValueError("repair_slots")
        policy_ref = prompt_policy_reference(prompt)
        policy = None
        if policy_ref is not None:
            policy = read_repair_policy_in_session(session, run_id=request.run_id,
                generation=execution.generation, reference=policy_ref, request=request)
            if policy["repair_errors_hash"] != canonical_hash(errors) or policy["repair_evidence_ids_hash"] != canonical_hash(sorted(set(references))):
                raise ValueError("skill_repair_policy_input_changed")
            policy = dict(policy, reference=policy_ref)
        expected = prefix + build_synthesis_tool_repair_prompt(
            request,
            execution.dsh_session_id,
            errors,
            references,
            skill_repair_policy=policy,
            **_perpetual_prompt_kwargs(
                session,
                request,
                selection_reference=selection_reference,
            ),
        )
        if prompt != expected:
            raise ValueError("repair_template")
    except (ValueError, IndexError, TypeError) as exc:
        raise ValueError("research_runtime_repair_prompt_invalid") from exc
    _assert_repair_evidence(session, bundle, request, references, execution.recorded_at)


def _v2_original(
    session: Session, run: RunRecord
) -> tuple[RuntimeTaskAnchor, FrozenExecutionPreparation | None]:
    bundle, identity = read_bundle_identity_in_session(session, run.run_id)
    method_row = _row(session, run.run_id, METHOD_ROLE)
    if method_row is None:
        link = session.get(DshSessionLinkRecord, run.run_id, populate_existing=True)
        prompt = session.scalar(
            select(DshSessionPromptRecord)
            .where(
                DshSessionPromptRecord.run_id == run.run_id,
            )
            .execution_options(populate_existing=True)
        )
        sidecar = session.scalar(
            select(RunRoleOutputRecord)
            .where(
                RunRoleOutputRecord.run_id == run.run_id,
                (RunRoleOutputRecord.role.like("dsh_execution_request:%"))
                | (RunRoleOutputRecord.role.like("dsh_prompt_integrity:%")),
            )
            .execution_options(populate_existing=True)
        )
        if (
            link is not None
            or prompt is not None
            or sidecar is not None
            or has_selection_link_in_session(session, run.run_id)
        ):
            raise ValueError("research_execution_legacy_or_partial")
        return (
            RuntimeTaskAnchor(
                run.run_id,
                run.event_id,
                run.snapshot_id,
                bundle.task.request_hash,
                identity.bundle_hash,
            ),
            None,
        )
    prepared = read_prepared_execution_in_session(session, run.run_id)
    # The Kernel reader authenticates every contiguous row and hash. Runtime
    # separately recognizes every original whole body before reusing any turn.
    rows = session.scalars(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.run_id == run.run_id,
            RunRoleOutputRecord.role.like("dsh_execution_request:%"),
        )
        .execution_options(populate_existing=True)
    ).all()
    for row in rows:
        execution = parse_execution_receipt(row)
        prompt = session.get(
            DshSessionPromptRecord, (run.run_id, execution.generation), populate_existing=True
        )
        assert prompt is not None
        _assert_runtime_prompt_source(session, bundle, execution, prompt.prompt)
    generation = prepared.execution.generation
    execution_row = _row(session, run.run_id, execution_request_role(generation))
    integrity_row = _row(session, run.run_id, prompt_integrity_role(generation))
    assert execution_row is not None and integrity_row is not None
    body = [_row_identity(row) for row in (method_row, execution_row, integrity_row)]
    required = prepared.method.schema_version == "research-execution-method.v3"
    if required:
        assert isinstance(prepared.execution, ResearchExecutionRequestReceiptV3)
        selection = session.get(
            RunRoleOutputRecord,
            prepared.execution.current_selection_row.output_id,
            populate_existing=True,
        )
        derived = _row(session, run.run_id, link_role(generation))
        if selection is None or derived is None:
            raise ValueError("research_context_execution_incomplete")
        body.extend([_row_identity(selection), _row_identity(derived)])
    body.append([hashlib.sha256(prepared.prompt.prompt.encode("utf-8")).hexdigest()])
    fingerprint = hashlib.sha256(json.dumps(body, separators=(",", ":")).encode()).hexdigest()
    return (
        RuntimeTaskAnchor(
            run.run_id,
            run.event_id,
            run.snapshot_id,
            bundle.task.request_hash,
            identity.bundle_hash,
            fingerprint,
            generation,
            prepared.method.definition.deadline_at,
            required,
        ),
        prepared,
    )


def _v2_anchor(session: Session, run: RunRecord) -> RuntimeTaskAnchor:
    return _v2_original(session, run)[0]


def read_runtime_task_anchor(
    session: Session,
    run_id: str,
    *,
    allow_cancelled: bool = False,
) -> RuntimeTaskAnchor:
    """One consistent caller snapshot; no network or transaction ownership."""
    run = session.get(RunRecord, run_id, populate_existing=True)
    if run is None:
        raise ValueError("research_runtime_run_missing")
    from packages.kernel.decision_hub_kernel.application.research_observability import (
        requires_tool_admission_proof,
    )

    if requires_tool_admission_proof(session, run_id):
        if run.status not in {"admitted", "running"} and not (
            allow_cancelled and run.status == "cancelled"
        ):
            raise ValueError("research_execution_run_not_active")
        return _v2_anchor(session, run)
    saved = read_task_request(session, run)
    task_hash = saved[1] if saved else None
    first_execution = session.scalar(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role == execution_request_role(1),
        )
        .execution_options(populate_existing=True)
    )
    first_prompt = session.get(DshSessionPromptRecord, (run_id, 1), populate_existing=True)
    first_integrity = session.scalar(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role == prompt_integrity_role(1),
        )
        .execution_options(populate_existing=True)
    )
    if first_prompt is not None and first_integrity is not None:
        read_prompt_integrity(session, first_prompt)
    if task_hash is not None and first_prompt is not None and first_execution is None:
        # A Task-bearing retry needs its original execution anchor. Reading
        # history remains supported; this does not create a missing receipt.
        raise ValueError("research_runtime_original_execution_missing")
    if first_execution is not None:
        if task_hash is None or first_prompt is None:
            raise ValueError("research_runtime_original_task_missing")
        # The persisted execution receipt binds the ORIGINAL full Task hash,
        # even if the executed first request was a filtered subset.
        read_prompt_integrity(session, first_prompt)
        read_execution_request(session, first_prompt, task_hash)
    return RuntimeTaskAnchor(run_id, run.event_id, run.snapshot_id, task_hash)


def verify_runtime_task_anchor(
    session: Session,
    anchor: RuntimeTaskAnchor,
    *,
    allow_cancelled: bool = False,
) -> None:
    if read_runtime_task_anchor(session, anchor.run_id, allow_cancelled=allow_cancelled) != anchor:
        raise ValueError("research_runtime_original_task_changed")


def verify_runtime_task_and_selection(
    session: Session,
    anchor: RuntimeTaskAnchor,
    reference: ContextSelectionReference,
    expected_lease_owner: str,
    request_json: str,
    *,
    allow_cancelled: bool = False,
) -> None:
    """Reuse only the original just authenticated in this physical read callback.

    No Prepared/Selection verdict is accepted from a caller or retained after
    return. A different Selection still executes the original complete reader.
    """
    from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
        _local_original_read,  # pyright: ignore[reportPrivateUsage]
    )

    def original_selection() -> None:
        selected = assert_selection_permission_in_session(
            session,
            anchor.run_id,
            reference,
            expected_lease_owner,
            allow_cancelled=allow_cancelled,
            deadline_at=anchor.deadline_at,
        )
        original_request = json.dumps(
            selected.request.model_dump(mode="json"),
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        if original_request != request_json:
            raise ValueError("research_context_selected_request_conflict")

    if not anchor.requires_selection:
        verify_runtime_task_anchor(session, anchor, allow_cancelled=allow_cancelled)
        original_selection()
        return
    with _local_original_read(session) as boundary:
        if not boundary.unchanged():
            verify_runtime_task_anchor(session, anchor, allow_cancelled=allow_cancelled)
            original_selection()
            return
        run = session.get(RunRecord, anchor.run_id, populate_existing=True)
        if run is None or (
            run.status not in {"admitted", "running"}
            and not (allow_cancelled and run.status == "cancelled")
        ):
            raise ValueError("research_execution_run_not_active")
        actual, prepared = _v2_original(session, run)
        if actual != anchor:
            raise ValueError("research_runtime_original_task_changed")
        if prepared is None or not isinstance(
            prepared.execution, ResearchExecutionRequestReceiptV3
        ):
            original_selection()
            return
        # The complete Kernel reader has just authenticated this full row
        # identity, whole Selection request, every generation and link hash.
        original = _selection_reference_from_row(prepared.execution.current_selection_row)
        supplied = parse_context_selection_reference(reference.model_dump_json())
        if original.model_dump_json() != supplied.model_dump_json():
            # A newly selected next round is legitimately different before its
            # preparation. Never certify it from the previous execution.
            original_selection()
            return
        certified_request = json.dumps(
            prepared.execution.request.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if certified_request != request_json:
            raise ValueError("research_context_selected_request_conflict")
        if not boundary.unchanged():
            original_selection()
            return
        # This is the existing read-only permission predicate, evaluated now.
        # Snapshot integrity never substitutes for live clock/lease authority.
        run = session.get(RunRecord, anchor.run_id, populate_existing=True)
        now = datetime.now(UTC)
        if not expected_lease_owner or run is None or run.artifact_id is not None:
            raise ValueError("research_context_selection_permission_lost")
        if allow_cancelled and run.status == "cancelled":
            if not boundary.unchanged():
                original_selection()
            return
        expires = as_utc(run.lease_expires_at)
        if (
            run.lease_owner != expected_lease_owner
            or expires is None
            or expires <= now
            or run.status != "running"
        ):
            raise ValueError("research_context_selection_permission_lost")
        if not boundary.unchanged():
            original_selection()
            return
        # A deadline-only rejection must not conceal changed originals or a
        # lost owner/lease. Cleanup skips only the execution clock, as does the
        # full Selection reader; it still checks every other permission above.
        if not allow_cancelled and now >= min(
            prepared.execution.request.deadline_at, prepared.method.definition.deadline_at
        ):
            raise ValueError("research_context_selection_deadline_elapsed")


def _runtime_link_identity(link: DshSessionLinkRecord | None) -> tuple | None:
    if link is None:
        return None
    return (
        link.run_id, link.dsh_session_id, link.generation, link.request_hash,
        link.upstream_identity_json, link.plugin_build_hash, as_utc(link.deadline_at),
        link.max_tool_calls, link.model_step_timeout_ms,
    )


class _RuntimeBindingLinkAdvanced(ValueError):
    pass


def authenticate_runtime_binding(
    database: Database,
    anchor: RuntimeTaskAnchor,
    selected: tuple[ContextSelectionReference, str, str] | None,
    *,
    deadline_at: datetime,
    allow_cancelled: bool = False,
) -> None:
    """At most one full re-authentication with the original fixed authority.

    A selected request fixes its owner and immutable selection across both
    attempts. Without one, only the existing authenticated terminal-Link
    advance can qualify; never adopt a new owner from a retry snapshot.
    Both attempts stay in this original native Future. Each attempt closes its
    snapshots and source handles before another can begin; no RPC is made here.
    The original request/anchor deadlines and all source checks remain intact.
    """
    for attempt in range(2):
        try:
            return _authenticate_runtime_binding_snapshot(
                database, anchor, selected, deadline_at=deadline_at,
                allow_cancelled=allow_cancelled,
            )
        except OriginalPreparationReadsetChanged:
            if attempt or selected is None or not isinstance(selected[1], str) or not selected[1]:
                raise
            # The failed call has unwound every source handle and RO snapshot.
            # Re-read all originals, then repeat the independent fresh fence;
            # neither this conflict nor the first prepared value grants rights.
        except _RuntimeBindingLinkAdvanced:
            if attempt:
                raise ValueError("research_runtime_binding_link_unstable") from None


def _authenticate_runtime_binding_snapshot(
    database: Database,
    anchor: RuntimeTaskAnchor,
    selected: tuple[ContextSelectionReference, str, str] | None,
    *,
    deadline_at: datetime,
    allow_cancelled: bool = False,
) -> None:
    """Full originals followed by an independent fresh RO identity/rights cut.

    All Sessions and immutable source handles belong to this one native call.
    No prepared verdict or lease escapes for reuse by another operation.
    """
    operation_id = uuid4().hex
    deadlines = [deadline_at]
    if anchor.deadline_at is not None:
        deadlines.append(anchor.deadline_at)
    if selected is not None:
        deadlines.append(ResearchSessionRequest.model_validate_json(selected[2]).deadline_at)
    deadline_at = min(deadlines)

    def checkpoint() -> None:
        if not allow_cancelled and datetime.now(UTC) >= deadline_at:
            raise ValueError("research_context_selection_deadline_elapsed")

    def original(session: Session) -> tuple[str | None, tuple | None]:
        if selected is None:
            verify_runtime_task_anchor(session, anchor, allow_cancelled=allow_cancelled)
        else:
            verify_runtime_task_and_selection(
                session, anchor, *selected, allow_cancelled=allow_cancelled,
            )
        if source_lease is not None and anchor.bundle_hash is not None:
            # The strict original reader may consume a retained recipe capsule
            # outside the live consumer inventory. Hold those authenticated
            # declarations/bytes and registry identities through the fresh cut.
            source_lease.extend_authenticated_selection(session, anchor.run_id)
        run = session.get(RunRecord, anchor.run_id, populate_existing=True)
        link = session.get(DshSessionLinkRecord, anchor.run_id, populate_existing=True)
        return (
            run.lease_owner if run is not None else None,
            _runtime_link_identity(link),
        )

    def phase(name: str, state: str, started: float) -> None:
        # Fixed phase strings, opaque operation ID and elapsed time only.
        try:
            log.info(
                "research_binding_phase operation_id=%s phase=%s state=%s elapsed_ms=%d",
                operation_id, name, state, round((perf_counter() - started) * 1000),
            )
        except Exception:
            pass

    with callback_source_lease_scope(checkpoint) as source_lease:
        started = perf_counter()
        phase("binding_original_auth", "started", started)
        try:
            prepared = prepare_originals(
                database, original, checkpoint=checkpoint, source_lease=source_lease,
                link_activity_observations=True, link_tool_counter_observations=True,
            )
        except BaseException:
            phase("binding_original_auth", "error", started)
            raise
        phase("binding_original_auth", "finished", started)
        started = perf_counter()
        phase("binding_fresh_fence", "started", started)
        try:
            # prepare_originals closed its slow snapshot. This BEGIN belongs to
            # a new Session/physical transaction and sees intervening commits.
            with database.engine.connect() as connection, Session(bind=connection) as session:
                connection.exec_driver_sql("PRAGMA query_only=ON")
                try:
                    connection.exec_driver_sql("BEGIN")
                    try:
                        prepared.verify_read_snapshot(session, checkpoint=checkpoint)
                    except OriginalPreparationReadsetChanged as error:
                        matched = next((query for query in prepared.queries if
                            hashlib.sha256(query.statement.encode()).hexdigest()
                            == error.query_fingerprint), None)
                        table_match = re.search(
                            r"(?is)\bFROM\s+([a-z_]+)\b", matched.statement,
                        ) if matched is not None else None
                        table = table_match.group(1).lower() if table_match else "unknown"
                        if table not in {
                            "runs", "dsh_session_links", "run_role_outputs",
                            "dsh_session_prompts", "research_tool_call_reservations",
                            "research_trace_events", "research_evidence",
                        }:
                            table = "other"
                        # Opaque fingerprints and fixed table labels only. No
                        # SQL, parameters, evidence text or credentials.
                        log.warning(
                            "research_binding_readset_conflict run_id=%s "
                            "query_fingerprint=%s table=%s",
                            anchor.run_id, error.query_fingerprint, table,
                        )
                        if (selected is not None and isinstance(selected[1], str)
                                and selected[1]):
                            # Preserve the exact second conflict. The outer
                            # bounded retry owns the only selected-request retry.
                            raise
                        # Legacy unselected callers retain the narrower named
                        # terminal-Link rule; no general owner-changing retry.
                        link = session.get(
                            DshSessionLinkRecord, anchor.run_id, populate_existing=True,
                        )
                        if (matched is not None
                                and re.search(
                                    r"(?is)\bFROM\s+dsh_session_links\s+WHERE\b",
                                    matched.statement,
                                )
                                and not re.search(
                                    r"(?is)\bJOIN\b", matched.statement,
                                )
                                and link is not None
                                and _runtime_link_identity(link) == prepared.value[1]
                                and link.state in {"completed", "failed", "cancelled"}):
                            if read_authenticated_terminal_metadata_in_session(
                                session, anchor.run_id, now=datetime.now(UTC),
                            ) is not None:
                                checkpoint()
                                raise _RuntimeBindingLinkAdvanced from error
                        raise
                    # Readset replay itself can take time. End that snapshot
                    # before the final, short current-rights cut, so a cancel or
                    # owner takeover committed during replay is visible too.
                    session.rollback()
                    connection.exec_driver_sql("BEGIN")
                    run = session.get(RunRecord, anchor.run_id, populate_existing=True)
                    link = session.get(
                        DshSessionLinkRecord, anchor.run_id, populate_existing=True,
                    )
                    if _runtime_link_identity(link) != prepared.value[1]:
                        raise ValueError("research_runtime_original_execution_changed")
                    if run is None or (run.event_id, run.snapshot_id) != (
                        anchor.event_id, anchor.snapshot_id,
                    ):
                        raise ValueError("research_runtime_original_task_changed")
                    now = datetime.now(UTC)
                    if allow_cancelled and run.status == "cancelled":
                        return
                    if anchor.bundle_hash is not None and (
                        run.status not in {"admitted", "running"}
                        or run.artifact_id is not None or run.finished_at is not None
                    ):
                        raise ValueError("research_context_selection_permission_lost")
                    expected_owner = selected[1] if selected is not None else prepared.value[0]
                    if selected is not None or expected_owner is not None:
                        expires = as_utc(run.lease_expires_at)
                        if (not expected_owner or run.lease_owner != expected_owner
                                or run.status != "running" or run.artifact_id is not None
                                or expires is None or expires <= now):
                            raise ValueError("research_context_selection_permission_lost")
                    checkpoint()
                finally:
                    # Reset connection-local policy before returning it to the
                    # shared pool; the RO fence never commits business data.
                    session.rollback()
                    connection.exec_driver_sql("PRAGMA query_only=OFF")
        except BaseException:
            phase("binding_fresh_fence", "error", started)
            raise
        phase("binding_fresh_fence", "finished", started)


def read_legacy_generation(session: Session, request: ResearchSessionRequest) -> int:
    """Separate a persisted Host continuation from the public research round.

    V1 without an execution receipt retains its existing trusted prompt history;
    reading its control round does not create a V2 or full-input certificate.
    """
    link = session.get(DshSessionLinkRecord, request.run_id, populate_existing=True)
    if link is None:
        return request.current_round
    prompts = session.scalars(
        select(DshSessionPromptRecord)
        .where(
            DshSessionPromptRecord.run_id == request.run_id,
        )
        .order_by(DshSessionPromptRecord.generation.desc())
        .execution_options(populate_existing=True)
    ).all()
    marker = "Canonical control request (untrusted text is replaced by typed references):\n"
    previous_round = None
    run = session.get(RunRecord, request.run_id, populate_existing=True)
    if run is None:
        raise ValueError("research_runtime_run_missing")
    original = read_task_request(session, run)
    for prompt in prompts:
        execution = _row(session, request.run_id, execution_request_role(prompt.generation))
        if execution is not None:
            if original is None:
                raise ValueError("research_runtime_original_task_missing")
            read_prompt_integrity(session, prompt)
            previous_round = read_execution_request(session, prompt, original[1]).current_round
            break
        if marker in prompt.prompt:
            read_prompt_integrity(session, prompt)
            try:
                # The producer's final control frame has four exact lines.
                # Earlier raw source/policy text may contain the same marker.
                frame = prompt.prompt.rsplit(marker, 1)[1].split("\n")
                if (
                    len(frame) != 4
                    or frame[1] != "BEGIN_UNTRUSTED_RESEARCH_DATA"
                    or frame[3] != "END_UNTRUSTED_RESEARCH_DATA"
                ):
                    raise ValueError("research_runtime_legacy_round_unknown")
                body = json.loads(frame[0])
                if (
                    not isinstance(body, dict)
                    or body.get("schema_version") != "research-session-request.v1"
                    or body.get("run_id") != request.run_id
                    or not isinstance(json.loads(frame[2]), list)
                ):
                    raise ValueError("research_runtime_legacy_round_unknown")
                previous_round = body["current_round"]
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError("research_runtime_legacy_round_unknown") from exc
            if type(previous_round) is not int or previous_round < 1:
                raise ValueError("research_runtime_legacy_round_unknown")
            break
    if previous_round is None:
        # An unexecuted legacy reservation has no new turn to distinguish.
        if not prompts:
            return link.generation
        raise ValueError("research_runtime_legacy_round_unknown")
    if request.current_round < previous_round:
        raise ValueError("research_execution_round_conflict")
    return link.generation if request.current_round == previous_round else link.generation + 1


@dataclass(frozen=True)
class RuntimePreparedTurn:
    anchor: RuntimeTaskAnchor
    prepared: FrozenExecutionPreparation
    request: ResearchSessionRequest
    submit: DshSessionSubmit
    tool_calls_before: int
    observation_context: ResearchNativeObservationContext
    skill_repair_policy: dict | None = None


def prepare_runtime_turn_in_session(
    session: Session,
    database: Database,
    expected: RuntimeTaskAnchor,
    request: ResearchSessionRequest,
    upstream: DshUpstreamIdentity,
    *,
    runtime_id: str,
    runtime_version: str,
    profile_ref: str,
    prompt_policy_hash: str,
    repair_errors: Sequence[Mapping[str, object]] | None = None,
    repair_evidence_ids: list[str] | None = None,
    skill_repair_policy_ref: dict | None = None,
    selection_reference: ContextSelectionReference | None = None,
    expected_lease_owner: str | None = None,
    _candidate_only: bool = False,
) -> RuntimePreparedTurn | RuntimeTurnCandidate:
    """Same physical writer transaction: expected proof, quota and five-piece preparation.

    Policy prefix plus the profile producer generates the whole body here.
    Frozen policy metadata remains prepared; this is not checker execution.
    """
    actual = read_runtime_task_anchor(session, expected.run_id)
    initial_winner = bool(
        expected.generation == 0
        and expected.execution_fingerprint is None
        and expected.bundle_hash is not None
        and actual.generation == 1
        and (
            actual.run_id,
            actual.event_id,
            actual.snapshot_id,
            actual.task_request_hash,
            actual.bundle_hash,
        )
        == (
            expected.run_id,
            expected.event_id,
            expected.snapshot_id,
            expected.task_request_hash,
            expected.bundle_hash,
        )
    )
    if actual != expected and not initial_winner:
        raise ValueError("research_runtime_original_task_changed")
    if expected.bundle_hash is None:
        raise ValueError("research_runtime_prepared_bundle_required")
    bundle, _identity = read_bundle_identity_in_session(session, request.run_id)
    if actual.requires_selection and selection_reference is None:
        raise ValueError("research_context_selection_required")
    if selection_reference is not None:
        if not expected_lease_owner:
            raise ValueError("research_context_expected_owner_required")
        selected = assert_selection_permission_in_session(
            session, request.run_id, selection_reference, expected_lease_owner
        )
        if selected.request.model_dump(mode="json") != request.model_dump(mode="json"):
            raise ValueError("research_context_selected_request_conflict")
    current = (
        read_prepared_execution_in_session(session, request.run_id) if actual.generation else None
    )
    same_turn = False
    turn_kind = "synthesis_repair" if repair_errors is not None else "research_round"
    if current is None:
        generation, deadline, digest = 1, request.deadline_at, research_request_hash(request)
        max_calls = request.execution_budget.max_tool_calls
        model_ms = request.execution_budget.per_model_step_timeout_seconds * 1000
        before = 0
    else:
        definition = current.method.definition
        before = current.link.tool_calls_started
        deadline, digest = definition.deadline_at, current.method.request_hash
        max_calls = definition.execution_budget.max_tool_calls
        model_ms = definition.execution_budget.per_model_step_timeout_seconds * 1000
        round_number = current.execution.request.current_round
        if request.current_round < round_number:
            raise ValueError("research_execution_round_conflict")
        if request.current_round == round_number:
            if request.model_dump(mode="json") != current.execution.request.model_dump(mode="json"):
                raise ValueError("research_execution_generation_conflict")
            same_turn = repair_errors is None or current.execution.turn_kind == "synthesis_repair"
        if same_turn:
            generation, turn_kind = current.execution.generation, current.execution.turn_kind
        else:
            generation = current.execution.generation + 1
        if turn_kind == "synthesis_repair" and not same_turn:
            # The full current reader just authenticated every contiguous
            # generation and its raw row identity. Count those same rows under
            # this writer lock, rather than re-running the whole history N times.
            rows = session.scalars(
                select(RunRoleOutputRecord)
                .where(
                    RunRoleOutputRecord.run_id == request.run_id,
                    RunRoleOutputRecord.role.like("dsh_execution_request:%"),
                )
                .execution_options(populate_existing=True)
            ).all()
            turns = [parse_execution_receipt(row) for row in rows]
            used = sum(turn.turn_kind == "synthesis_repair" for turn in turns)
            ceiling = min(
                definition.execution_budget.max_structured_repairs,
                request.execution_budget.max_structured_repairs,
            )
            if used >= ceiling:
                raise ValueError("research_runtime_repair_budget_exhausted")
    session_id, request_id = DshSessionLinkService.deterministic_ids(
        request.run_id,
        digest,
        generation,
    )
    submit = DshSessionSubmit(
        schema_version="dsh-session-submit.v1",
        run_id=request.run_id,
        request_hash=digest,
        deterministic_session_id=session_id,
        deterministic_request_id=request_id,
        workspace_ref="decision-hub://workspace/default",
        prompt_ref=f"hub://runs/{request.run_id}/prompts/{generation}",
        agent_preset="decision-research",
        permission_ref="decision-hub://permissions/research-only",
        deadline_at=deadline,
        max_tool_calls=max_calls,
        model_step_timeout_ms=model_ms,
        generation=generation,
    )
    policy = None
    policy_ref = (prompt_policy_reference(current.prompt.prompt) if same_turn and current is not None else skill_repair_policy_ref)
    if same_turn and skill_repair_policy_ref is not None and policy_ref != skill_repair_policy_ref:
        raise ValueError("skill_repair_policy_replay_conflict")
    if policy_ref is not None:
        if (repair_errors is None and not same_turn) or turn_kind != "synthesis_repair":
            raise ValueError("skill_repair_policy_turn_invalid")
        policy = read_repair_policy_in_session(session, run_id=request.run_id, generation=generation, reference=policy_ref, request=request)
        if repair_errors is not None and (policy["repair_errors_hash"] != canonical_hash(repair_errors) or policy["repair_evidence_ids_hash"] != canonical_hash(sorted(set(repair_evidence_ids or [])))):
            raise ValueError("skill_repair_policy_input_changed")
        if not same_turn and policy["policy"] == "same_version_reexecute.v1" and datetime.now(UTC) >= datetime.fromisoformat(policy["acquisition_close_at"]):
            raise ValueError("skill_reexecution_acquisition_window_closed")
        policy = dict(policy, reference=policy_ref)
    if same_turn:
        assert current is not None
        prompt = current.prompt.prompt
        if initial_winner and (
            current.execution.turn_kind != "research_round"
            or prompt
            != render_prepared_research_policy(bundle, request.allowed_capabilities)
            + "\n\n"
            + build_trusted_web_research_prompt(
                request,
                **_perpetual_prompt_kwargs(
                    session,
                    request,
                    selection_reference=selection_reference,
                    expected_lease_owner=expected_lease_owner,
                ),
            )
        ):
            raise ValueError("research_execution_generation_conflict")
    else:
        if repair_errors is not None:
            _assert_repair_evidence(
                session, bundle, request, repair_evidence_ids or [], datetime.now(UTC)
            )
        body = (
            build_synthesis_tool_repair_prompt(
                request, session_id, repair_errors, repair_evidence_ids or [],
                skill_repair_policy=policy,
                **_perpetual_prompt_kwargs(
                    session,
                    request,
                    selection_reference=selection_reference,
                    expected_lease_owner=expected_lease_owner,
                ),
            )
            if repair_errors is not None
            else build_trusted_web_research_prompt(
                request,
                **_perpetual_prompt_kwargs(
                    session,
                    request,
                    selection_reference=selection_reference,
                    expected_lease_owner=expected_lease_owner,
                ),
            )
        )
        prompt = (
            render_prepared_research_policy(bundle, request.allowed_capabilities) + "\n\n" + body
        )
    prepared = ResearchExecutionPreparationService(database).prepare_in_session(
        session,
        request,
        submit,
        upstream,
        prompt,
        runtime_id=runtime_id,
        runtime_version=runtime_version,
        profile_ref=profile_ref,
        prompt_policy_hash=prompt_policy_hash,
        recorded_at=datetime.now(UTC),
        turn_kind=turn_kind,
        selection_reference=selection_reference,
        expected_lease_owner=expected_lease_owner,
        _candidate_only=_candidate_only,
    )
    if _candidate_only:
        if type(prepared) is not ExecutionPreparationCandidate:
            raise ValueError("research_runtime_preparation_invalid")
        return RuntimeTurnCandidate(prepared, request, submit, before, policy)
    final_anchor = read_runtime_task_anchor(session, request.run_id)
    observation = native_observation_context_in_session(session, request.run_id, prepared=prepared)
    if selection_reference is not None:
        fence_selection_permission_in_session(
            session,
            request.run_id,
            expected_lease_owner or "",
            prepared.method.definition.deadline_at,
        )
    return RuntimePreparedTurn(
        final_anchor,
        prepared,
        request,
        submit,
        before,
        observation,
        policy,
    )


@dataclass(frozen=True)
class RuntimeTurnCandidate:
    execution: ExecutionPreparationCandidate
    request: ResearchSessionRequest
    submit: DshSessionSubmit
    tool_calls_before: int
    skill_repair_policy: dict | None


def _saved_turn_anchor(session: Session, prepared: FrozenExecutionPreparation) -> RuntimeTaskAnchor:
    """Bounded real-row identity, after issued full originals and exact publication."""
    run_id, generation = prepared.method.run_id, prepared.execution.generation
    roles = (METHOD_ROLE, execution_request_role(generation), prompt_integrity_role(generation))
    rows = [_row(session, run_id, role) for role in roles]
    if any(row is None for row in rows):
        raise ValueError("research_runtime_preparation_incomplete")
    body = [_row_identity(row) for row in rows]
    required = prepared.method.schema_version == "research-execution-method.v3"
    if required:
        selected = session.get(RunRoleOutputRecord,
            prepared.execution.current_selection_row.output_id, populate_existing=True)
        derived = _row(session, run_id, link_role(generation))
        if selected is None or derived is None:
            raise ValueError("research_context_execution_incomplete")
        body.extend((_row_identity(selected), _row_identity(derived)))
    body.append([hashlib.sha256(prepared.prompt.prompt.encode("utf-8")).hexdigest()])
    run = session.get(RunRecord, run_id, populate_existing=True)
    if run is None:
        raise ValueError("research_runtime_run_missing")
    return RuntimeTaskAnchor(run_id, run.event_id, run.snapshot_id,
        prepared.execution.task_request_hash, prepared.method.definition.bundle.bundle_hash,
        hashlib.sha256(json.dumps(body, separators=(",", ":")).encode()).hexdigest(),
        generation, prepared.method.definition.deadline_at, required)


def prepare_runtime_turn(
    database: Database, expected: RuntimeTaskAnchor, request: ResearchSessionRequest,
    upstream: DshUpstreamIdentity, *, checkpoint, stage, deadline_at: datetime, **options,
) -> RuntimePreparedTurn:
    """One native owner/thread: complete RO originals, exact short writer, commit."""
    effective = min(expected.deadline_at or request.deadline_at, request.deadline_at, deadline_at)

    def check() -> None:
        checkpoint()
        if datetime.now(UTC) >= effective:
            raise ValueError("research_context_selection_deadline_elapsed")

    with callback_source_lease_scope(check) as lease:
        def build(session: Session) -> RuntimeTurnCandidate:
            value = prepare_runtime_turn_in_session(session, database, expected, request,
                upstream, _candidate_only=True, **options)
            if type(value) is not RuntimeTurnCandidate:
                raise ValueError("research_runtime_preparation_invalid")
            if lease is not None:
                lease.extend_authenticated_selection(session, request.run_id)
            check()
            return value

        stage("ro_auth_started")
        originals = prepare_originals(database, build, checkpoint=check, source_lease=lease,
            write_set=lambda value: value.execution.writes)
        stage("ro_auth_completed")
        check()
        with database.session() as session:
            originals._require_issued(session)
            # The owned callback admits its sole writer only after the slow RO
            # snapshot and source parsing have completed and closed.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            stage("begin_acquired")
            connection = session.connection()
            native = connection.connection.driver_connection
            attempted_boundary = False

            def authorize(action, _first, _second, _database, _trigger):
                nonlocal attempted_boundary
                if action in {sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT}:
                    attempted_boundary = True
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            if not isinstance(native, sqlite3.Connection):
                raise ValueError("research_runtime_preparation_sqlite_required")
            native.set_authorizer(authorize)
            try:
                saved = commit_execution_candidate_in_session(session, originals, checkpoint=check)
                candidate = originals.value
                anchor = _saved_turn_anchor(session, saved)
                observation = native_observation_context_in_session(session, request.run_id, prepared=saved)
                result = RuntimePreparedTurn(anchor, saved, saved.execution.request, candidate.submit,
                    candidate.tool_calls_before, observation, candidate.skill_repair_policy)
                check()
                if attempted_boundary or not native.in_transaction:
                    raise ValueError("research_runtime_preparation_transaction_changed")
                stage("operation_completed")
            finally:
                # The owning Database.session alone performs the final commit
                # or rollback after this continuous physical writer scope.
                native.set_authorizer(None)
        stage("commit_done")
        return result
