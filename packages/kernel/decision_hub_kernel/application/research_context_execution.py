"""Original Selection to execution bindings; borrowed reads and writes only.

Method V3 makes the Selection requirement sticky. A current proof walks all
generations; a Selection's original Graph prior can walk only strictly earlier
generations. That descending dependency is not a verified flag or a cache.
"""

from __future__ import annotations

import hashlib
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    DshSessionSubmit,
    DshUpstreamIdentity,
    ResearchSessionRequest,
)
from packages.contracts_py.decision_hub_contracts.generated.research_context_execution import (
    ResearchContextSelectionExecutionLinkV2,
    ResearchContextSelectionExecutionLinkV3,
    ResearchContextSelectionRowIdentityV3,
    ResearchContextSelectionRowIdentityV4,
    ResearchExecutionMethodReceiptV3,
    ResearchExecutionRequestReceiptV3,
)

if TYPE_CHECKING:
    from .research_context_selection import (
        ContextSelectionReference as LegacyContextSelectionReference,
    )
    from .research_context_selection import (
        FrozenContextSelection as LegacyFrozenContextSelection,
    )
    from .research_context_selection_v4 import (
        ContextSelectionReference as QualityContextSelectionReference,
    )
    from .research_context_selection_v4 import (
        FrozenContextSelection as QualityFrozenContextSelection,
    )

    ContextSelectionReference = LegacyContextSelectionReference | QualityContextSelectionReference
    FrozenContextSelection = LegacyFrozenContextSelection | QualityFrozenContextSelection
    from .research_execution_binding import FrozenExecutionPreparation
from packages.kernel.decision_hub_kernel.application.research_method import (
    ROLE as METHOD_ROLE,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    canonical_recipe,
    recipe_hash,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    DshSessionLinkRecord,
    DshSessionPromptRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
)

METHOD_SCHEMA = "research-execution-method.v3"
EXECUTION_SCHEMA = "dsh-execution-request.v3"
LINK_SCHEMA = "research-context-selection-execution-link.v2"
MAX_LINK_BYTES = 16 * 1024
_PROOF_STACK: ContextVar[tuple[tuple[str, int], ...]] = ContextVar(
    "research_context_original_proof_stack", default=()
)


def link_role(generation: int) -> str:
    if type(generation) is not int or generation < 1:
        raise ValueError("research_context_generation_invalid")
    return f"context_execution_link:{generation}"


def is_selection_link_role(role: str) -> bool:
    prefix = "context_execution_link:"
    suffix = role.removeprefix(prefix)
    return (
        role.startswith(prefix)
        and suffix.isascii()
        and suffix.isdecimal()
        and not suffix.startswith("0")
    )


def has_selection_link_in_session(session: Session, run_id: str) -> bool:
    roles = session.scalars(
        select(RunRoleOutputRecord.role)
        .where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role.like("context_execution_link:%"),
        )
        .execution_options(populate_existing=True)
    ).all()
    return any(is_selection_link_role(role) for role in roles)


def _json(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="json")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="strict")).hexdigest()


def _identity(row: RunRoleOutputRecord) -> dict[str, Any]:
    return {
        "output_id": row.output_id,
        "run_id": row.run_id,
        "role": row.role,
        "row_schema_version": row.schema_version,
        "payload_hash": row.payload_hash,
        "recorded_at": as_utc(row.created_at),
    }


def selection_identity(reference: ContextSelectionReference) -> dict[str, Any]:
    body = _json(reference)
    body["row_schema_version"] = body.pop("schema_version")
    return body


def _selection_reference(
    identity: ResearchContextSelectionRowIdentityV3 | ResearchContextSelectionRowIdentityV4,
) -> ContextSelectionReference:
    from .research_context_selection import ContextSelectionReference as LegacyReference
    from .research_context_selection_v4 import ContextSelectionReference as QualityReference

    body = _json(identity)
    body["schema_version"] = body.pop("row_schema_version")
    reference_type = (
        QualityReference
        if body["schema_version"] == "research-context-selection.v4"
        else LegacyReference
    )
    return reference_type.model_validate_json(canonical_recipe(body))


@contextmanager
def generation_proof(run_id: str, generation: int) -> Generator[None]:
    stack = _PROOF_STACK.get()
    if stack and (stack[-1][0] != run_id or generation >= stack[-1][1]):
        raise ValueError("research_context_original_dependency_cycle")
    token = _PROOF_STACK.set((*stack, (run_id, generation)))
    try:
        yield
    finally:
        _PROOF_STACK.reset(token)


def read_selection_in_session(
    session: Session,
    run_id: str,
    reference: ContextSelectionReference,
    *,
    expected_lease_owner: str | None = None,
    require_current_permission: bool = False,
) -> FrozenContextSelection:
    from .research_context_selection import (
        ContextSelectionReference as LegacyReference,
    )
    from .research_context_selection import (
        ContextSelectionService as LegacyService,
    )
    from .research_context_selection_v4 import (
        ContextSelectionReference as QualityReference,
    )
    from .research_context_selection_v4 import (
        ContextSelectionService as QualityService,
    )

    try:
        if reference.schema_version == "research-context-selection.v4":
            quality_reference = QualityReference.model_validate_json(reference.model_dump_json())
            quality_service = QualityService.__new__(QualityService)
            quality_service.clock = lambda: datetime.now(UTC)
            frozen = quality_service.read_in_session(
                session,
                run_id=run_id,
                reference=quality_reference,
                require_current_permission=require_current_permission,
                expected_lease_owner=expected_lease_owner,
            )
        else:
            legacy_reference = LegacyReference.model_validate_json(reference.model_dump_json())
            legacy_service = LegacyService.__new__(LegacyService)
            legacy_service.clock = lambda: datetime.now(UTC)
            frozen = legacy_service.read_in_session(
                session,
                run_id=run_id,
                reference=legacy_reference,
                require_current_permission=require_current_permission,
                expected_lease_owner=expected_lease_owner,
            )
    except PermissionError as exc:
        # Only the original producer's explicit permission fences are invalid.
        # I/O, busy and unexpected errors retain their existing classification.
        if str(exc) not in {
            "context_selection_deadline_elapsed",
            "context_selection_writer_permission_lost",
            "research_graph_writer_permission_lost",
            "research_graph_final_writer_fence_lost",
        }:
            raise
        raise ValueError(str(exc)) from exc
    if require_current_permission and frozen.receipt.recorded_at > datetime.now(UTC):
        raise ValueError("research_context_selection_from_future")
    return frozen


def assert_selection_permission_in_session(
    session: Session,
    run_id: str,
    reference: ContextSelectionReference,
    expected_lease_owner: str,
    *,
    allow_cancelled: bool = False,
    deadline_at: datetime | None = None,
) -> FrozenContextSelection:
    """Fresh original proof and current read-only fence before each physical RPC."""
    frozen = read_selection_in_session(session, run_id, reference)
    run = session.get(RunRecord, run_id, populate_existing=True)
    now = datetime.now(UTC)
    if not expected_lease_owner or run is None or run.artifact_id is not None:
        raise ValueError("research_context_selection_permission_lost")
    # Product cancellation intentionally clears the lease. This flag is used
    # only by bounded cleanup after the existing durable cancel/Link checks;
    # it never admits a new submission or a material/Graph write.
    if allow_cancelled and run.status == "cancelled":
        return frozen
    expires = as_utc(run.lease_expires_at)
    if (
        run.lease_owner != expected_lease_owner
        or expires is None
        or expires <= now
        or run.status != "running"
    ):
        raise ValueError("research_context_selection_permission_lost")
    # The optional original Method cap can only shorten the Selection clock.
    # Authenticate the original and all non-clock authority before classifying
    # deadline-only failure; simultaneous owner/lease loss stays permission loss.
    effective_deadline = (
        min(frozen.request.deadline_at, deadline_at)
        if deadline_at is not None else frozen.request.deadline_at
    )
    if not allow_cancelled and now >= effective_deadline:
        raise ValueError("research_context_selection_deadline_elapsed")
    return frozen


def fence_selection_permission_in_session(
    session: Session,
    run_id: str,
    expected_lease_owner: str,
    deadline_at: datetime,
) -> None:
    """Last SQL permission fence after original proof, under the caller writer.

    This does not authenticate a Selection. The caller must first verify the
    immutable receipt and request; no additional proof read follows this fence.
    """
    from .research_execution_binding import (
        _require_transaction,  # pyright: ignore[reportPrivateUsage]
    )

    _require_transaction(session)
    now = datetime.now(UTC)
    if not expected_lease_owner or now >= deadline_at:
        raise ValueError("research_context_selection_permission_lost")
    result = session.execute(
        update(RunRecord)
        .where(
            RunRecord.run_id == run_id,
            RunRecord.status == "running",
            RunRecord.artifact_id.is_(None),
            RunRecord.lease_owner == expected_lease_owner,
            RunRecord.lease_expires_at > now,
        )
        .values(lease_owner=expected_lease_owner)
        .execution_options(synchronize_session=False)
    )
    if getattr(result, "rowcount", None) != 1:
        raise ValueError("research_context_selection_permission_lost")


def build_method_v3_in_session(
    session: Session,
    request: ResearchSessionRequest,
    submit: DshSessionSubmit,
    identity: DshUpstreamIdentity,
    *,
    selection: FrozenContextSelection,
    runtime_id: str,
    runtime_version: str,
    profile_ref: str,
    prompt_policy_hash: str,
    recorded_at: datetime,
) -> ResearchExecutionMethodReceiptV3:
    from .research_execution_binding import build_method_v2_in_session

    if selection.reference.schema_version == "research-context-selection.v4":
        from .research_run_scope import read_run_spot_admission_in_session

        admitted = read_run_spot_admission_in_session(session, request.run_id)
        if admitted is None or identity.plugin_build_hash != admitted.effective.plugin_build_hash:
            raise ValueError("research_spot_execution_installed_identity_changed")
    old = build_method_v2_in_session(
        session,
        request,
        submit,
        identity,
        runtime_id=runtime_id,
        runtime_version=runtime_version,
        profile_ref=profile_ref,
        prompt_policy_hash=prompt_policy_hash,
        recorded_at=recorded_at,
    )
    definition = _json(old.definition)
    definition.update(
        schema_version=METHOD_SCHEMA,
        context_selection_binding={
            "mode": "required_before_rpc",
            "selection_schema": selection.reference.schema_version,
            "ranking_rules_ref": selection.receipt.selector_identity.ranking_rules_ref,
            "time_rule_ref": "research-context.received-cutoff.v1",
            "first_selection_row": selection_identity(selection.reference),
            "first_selection_payload_hash": selection.receipt.selection_payload_hash,
            "first_selection_full_request_hash": selection.receipt.current_full_request_hash,
            "first_selection_full_request_hash_kind": "canonical_research_session_request.v1",
        },
    )
    body = _json(old)
    body.update(
        schema_version=METHOD_SCHEMA, definition=definition, method_hash=recipe_hash(definition)
    )
    return ResearchExecutionMethodReceiptV3.model_validate_json(canonical_recipe(body))


def _link_body(
    session: Session, prepared: FrozenExecutionPreparation, selection: FrozenContextSelection
) -> dict[str, Any]:
    from .dsh_sessions import execution_request_role, prompt_integrity_role
    from .research_execution_binding import _role_row

    execution = prepared.execution
    execution_row = _role_row(session, prepared.method.run_id, execution_request_role(execution.generation))
    integrity_row = _role_row(session, prepared.method.run_id, prompt_integrity_role(execution.generation))
    if execution_row is None or integrity_row is None:
        raise ValueError("research_context_execution_incomplete")
    return link_body_from_rows(prepared, selection, execution_row, integrity_row)


def link_body_from_rows(
    prepared: FrozenExecutionPreparation, selection: FrozenContextSelection,
    execution_row: RunRoleOutputRecord, integrity_row: RunRoleOutputRecord,
) -> dict[str, Any]:
    """Pure encoding of authenticated canonical row identities; grants no permission."""
    method, execution = prepared.method, prepared.execution
    if not isinstance(method, ResearchExecutionMethodReceiptV3) or not isinstance(
        execution, ResearchExecutionRequestReceiptV3
    ):
        raise ValueError("research_context_execution_schema_mixed")
    for row in (execution_row, integrity_row):
        if row.run_id != method.run_id or row.payload_hash != _hash(row.payload_json):
            raise ValueError("research_context_execution_incomplete")
    return {
        "schema_version": "research-context-selection-execution-link.v3"
        if selection.reference.schema_version == "research-context-selection.v4"
        else LINK_SCHEMA,
        "link_id": "ctxlink_" + _hash(f"{method.run_id}\0{execution.generation}"),
        "run_id": method.run_id,
        "event_id": selection.request.event_id,
        "selection_id": selection.reference.output_id,
        "selection_row_identity": selection_identity(selection.reference),
        "selection_payload_hash": selection.receipt.selection_payload_hash,
        "graph_round": selection.receipt.graph_round,
        "generation": execution.generation,
        "turn_kind": execution.turn_kind,
        "dsh_session_id": execution.dsh_session_id,
        "request_id": execution.request_id,
        "first_link_request_hash": method.request_hash,
        "first_link_request_hash_kind": "dsh_link_identity.v1",
        "original_task_request_hash": execution.task_request_hash,
        "original_task_request_hash_kind": "canonical_research_session_request.v1",
        "bundle_hash": method.definition.bundle.bundle_hash,
        "selection_full_request_hash": selection.receipt.current_full_request_hash,
        "selection_full_request_hash_kind": "canonical_research_session_request.v1",
        "actual_execution_full_hash": execution.request_hash,
        "actual_execution_full_hash_kind": "canonical_research_session_request.v1",
        "actual_execution_reduced_hash": execution.execution_request_hash,
        "actual_execution_reduced_hash_kind": "dsh_link_identity.v1",
        "first_execution_request_full_hash": (
            method.definition.policy_binding.first_execution_request_hash
        ),
        "first_execution_request_full_hash_kind": "canonical_research_session_request.v1",
        "method_hash": method.method_hash,
        "execution_row_identity": _identity(execution_row),
        "prompt_integrity_row_identity": _identity(integrity_row),
        "recorded_at": execution.recorded_at,
        "link_payload_hash": "0" * 64,
        "link_payload_hash_kind": "canonical_context_selection_execution_link.v3"
        if selection.reference.schema_version == "research-context-selection.v4"
        else "canonical_context_selection_execution_link.v2",
    }


def _encoded_link(
    body: dict[str, Any],
) -> tuple[ResearchContextSelectionExecutionLinkV2 | ResearchContextSelectionExecutionLinkV3, str]:
    link_type = (
        ResearchContextSelectionExecutionLinkV3
        if body["schema_version"] == "research-context-selection-execution-link.v3"
        else ResearchContextSelectionExecutionLinkV2
    )
    value = link_type.model_validate(body)
    body = _json(value)
    body["link_payload_hash"] = recipe_hash(
        {k: v for k, v in body.items() if k != "link_payload_hash"}
    )
    value = link_type.model_validate_json(canonical_recipe(body))
    text = canonical_recipe(_json(value))
    if len(text.encode("utf-8", errors="strict")) > MAX_LINK_BYTES:
        raise ValueError("research_context_execution_final_bytes_exceeded")
    return value, text


def freeze_link_in_session(
    session: Session, prepared: FrozenExecutionPreparation, selection: FrozenContextSelection
) -> None:
    from .research_execution_binding import (
        _flush_stage,  # pyright: ignore[reportPrivateUsage]
        _role_row,  # pyright: ignore[reportPrivateUsage]
    )

    value, text = _encoded_link(_link_body(session, prepared, selection))
    role = link_role(value.generation)
    existing = _role_row(session, value.run_id, role)
    if existing is not None:
        verify_generation_in_session(session, prepared)
        return
    if session.get(RunRoleOutputRecord, value.link_id, populate_existing=True) is not None:
        raise ValueError("research_context_execution_id_collision")
    session.add(
        RunRoleOutputRecord(
            output_id=value.link_id,
            run_id=value.run_id,
            role=role,
            schema_version=value.schema_version,
            payload_json=text,
            payload_hash=_hash(text),
            created_at=value.recorded_at,
        )
    )
    _flush_stage(session, "selection_link")


def verify_generation_in_session(session: Session, prepared: FrozenExecutionPreparation) -> None:
    from .research_execution_binding import _role_row  # pyright: ignore[reportPrivateUsage]

    execution, method = prepared.execution, prepared.method
    if not isinstance(method, ResearchExecutionMethodReceiptV3) or not isinstance(
        execution, ResearchExecutionRequestReceiptV3
    ):
        raise ValueError("research_context_execution_schema_mixed")
    selection = read_selection_in_session(
        session, method.run_id, _selection_reference(execution.current_selection_row)
    )
    if (
        execution.current_selection_payload_hash != selection.receipt.selection_payload_hash
        or execution.request_hash != selection.receipt.current_full_request_hash
        or _json(execution.request) != _json(selection.request)
    ):
        raise ValueError("research_context_execution_selection_changed")
    binding = method.definition.context_selection_binding
    if execution.generation == 1 and (
        _json(binding.first_selection_row) != _json(execution.current_selection_row)
        or binding.first_selection_payload_hash != execution.current_selection_payload_hash
        or binding.first_selection_full_request_hash != execution.request_hash
    ):
        raise ValueError("research_context_first_selection_changed")
    value, text = _encoded_link(_link_body(session, prepared, selection))
    row = _role_row(session, method.run_id, link_role(execution.generation))
    if (
        row is None
        or row.output_id != value.link_id
        or row.schema_version != value.schema_version
        or row.payload_hash != _hash(text)
        or row.payload_json != text
        or as_utc(row.created_at) != value.recorded_at
    ):
        raise ValueError("research_context_execution_link_changed")


def read_prepared_v3_in_session(
    session: Session, run_id: str, generation: int | None = None
) -> FrozenExecutionPreparation:
    from .research_execution_binding import (
        _assert_link,  # pyright: ignore[reportPrivateUsage]
        _read_generation,  # pyright: ignore[reportPrivateUsage]
        _read_method_row,  # pyright: ignore[reportPrivateUsage]
        _role_row,  # pyright: ignore[reportPrivateUsage]
    )

    row = _role_row(session, run_id, METHOD_ROLE)
    if row is None or row.schema_version != METHOD_SCHEMA:
        raise ValueError("research_context_method_required")
    method, bundle = _read_method_row(session, row)
    if not isinstance(method, ResearchExecutionMethodReceiptV3):
        raise ValueError("research_context_method_required")
    link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
    run = session.get(RunRecord, run_id, populate_existing=True)
    if link is None or run is None or run.runtime_version != method.definition.runtime_version:
        raise ValueError("research_context_execution_incomplete")
    _assert_link(method, link)
    target = link.generation if generation is None else generation
    if type(target) is not int or not 1 <= target <= link.generation:
        raise ValueError("research_context_generation_invalid")
    stack = _PROOF_STACK.get()
    if stack:
        # Only a real original dependency read may shorten validation. It must
        # explicitly request an earlier generation of this same Run.
        if generation is None or stack[-1][0] != run_id or target >= stack[-1][1]:
            raise ValueError("research_context_original_dependency_cycle")
        end = target
    else:
        end = link.generation
    generations = session.scalars(
        select(DshSessionPromptRecord.generation)
        .where(
            DshSessionPromptRecord.run_id == run_id,
        )
        .order_by(DshSessionPromptRecord.generation)
    ).all()
    if list(generations) != list(range(1, link.generation + 1)):
        raise ValueError("research_context_generation_incomplete")
    roles = session.scalars(
        select(RunRoleOutputRecord.role).where(
            RunRoleOutputRecord.run_id == run_id,
            (RunRoleOutputRecord.role.like("dsh_execution_request:%"))
            | (RunRoleOutputRecord.role.like("dsh_prompt_integrity:%"))
            | (RunRoleOutputRecord.role.like("context_execution_link:%")),
        )
    ).all()
    expected = {
        role
        for number in generations
        for role in (
            f"dsh_execution_request:{number}",
            f"dsh_prompt_integrity:{number}",
            link_role(number),
        )
    }
    if set(roles) != expected or len(roles) != len(expected):
        raise ValueError("research_context_generation_incomplete")
    found = None
    for number in range(1, end + 1):
        with generation_proof(run_id, number):
            prepared = _read_generation(session, method, bundle, link, number)
        if number == target:
            found = prepared
    if found is None:
        raise ValueError("research_context_generation_incomplete")
    return found
