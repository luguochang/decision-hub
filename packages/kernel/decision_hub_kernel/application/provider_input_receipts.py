"""Immutable authenticated Host callbacks; no model understanding or paid authority.

Each short writer authenticates actual prepared originals and its current lease.
No transaction crosses a provider stream. Reads never create or backfill receipts.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from functools import wraps
from typing import Any, Concatenate, cast

from pydantic import ValidationError
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import ResearchSynthesisCandidate
from packages.contracts_py.decision_hub_contracts.generated.provider_input import (
    ResearchProviderFinishRecorded,
    ResearchProviderFinishSubmit,
    ResearchProviderInputRecorded,
    ResearchProviderInputRef,
    ResearchProviderInputSubmit,
    ResearchProviderToolEmissionRecorded,
    ResearchProviderToolEmissionSubmit,
)
from packages.kernel.decision_hub_kernel.application.model_dispatch import (
    ModelDispatchService,
    binding_role,
)
from packages.kernel.decision_hub_kernel.application.provider_callback_diagnostics import (
    ProviderCallbackDiagnostics,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    native_observation_context_in_session,
    observation_json,
    observation_sha,
)
from packages.kernel.decision_hub_kernel.persistence.callback_source_lease import (
    CallbackSourceLease,
    callback_source_lease_scope,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    DshSessionPromptRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.persistence.operation_phases import OperationPhases
from packages.kernel.decision_hub_kernel.persistence.original_preparation import (
    ExecutionBoundary,
    begin_immediate,
    current_execution_boundary,
    prepare_originals,
)
from packages.kernel.decision_hub_kernel.persistence.sqlite_busy import is_sqlite_initial_begin_busy
from packages.kernel.decision_hub_kernel.ports.provider_input import (
    ProviderFinishOriginal,
    ProviderInputOriginal,
    ProviderToolEmissionOriginal,
)
from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedExecutionRef


def _begin_provider_callback(
    session: Session, *, checkpoint: Callable[[], None], deadline_at: datetime,
) -> None:
    """Only fresh, untouched initial SQLite BEGIN contention; no work replay.

    At most 256 attempts with <=100ms native wait each. The original callback
    checkpoint and execution deadline always win; this is no new time budget.
    Restore the connection's original timeout before any original writer work.
    """
    checkpoint()
    if deadline_at.tzinfo is None or deadline_at.utcoffset() is None:
        raise ValueError("research_provider_writer_deadline_invalid")
    if deadline_at <= utcnow():
        raise PermissionError("original_preparation_deadline_elapsed")
    connection = session.connection()
    if connection.dialect.name != "sqlite":
        begin_immediate(session)
        return
    native = connection.connection.driver_connection
    if not isinstance(native, sqlite3.Connection) or native.in_transaction:
        raise ValueError("original_writer_fresh_transaction_required")
    if session.new or session.dirty or session.deleted or session.get_nested_transaction():
        raise ValueError("original_writer_fresh_session_required")
    changes, transaction = native.total_changes, session.get_transaction()

    def require_initial_unchanged() -> None:
        if (
            session.connection() is not connection
            or connection.connection.driver_connection is not native
            or session.get_transaction() is not transaction
            or native.in_transaction or native.total_changes != changes
            or session.new or session.dirty or session.deleted
            or session.get_nested_transaction()
        ):
            raise ValueError("research_provider_writer_initial_state_changed")

    row = native.execute("PRAGMA busy_timeout").fetchone()
    if row is None or len(row) != 1 or type(row[0]) is not int or not 0 <= row[0] <= 2_147_483_647:
        raise ValueError("research_provider_writer_busy_timeout_invalid")
    original_timeout = row[0]
    failure: BaseException | None = None
    try:
        for attempt in range(256):
            checkpoint()
            require_initial_unchanged()
            remaining_ms = (deadline_at - utcnow()).total_seconds() * 1000
            if remaining_ms < 1:
                raise PermissionError("original_preparation_deadline_elapsed")
            native.execute(f"PRAGMA busy_timeout={min(100, int(remaining_ms))}")
            try:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            except Exception as error:
                if not is_sqlite_initial_begin_busy(error):
                    raise
                require_initial_unchanged()
                checkpoint()
                if deadline_at <= utcnow():
                    raise PermissionError("original_preparation_deadline_elapsed")
                if attempt == 255:
                    raise
            else:
                if (
                    session.connection() is not connection
                    or connection.connection.driver_connection is not native
                    or session.get_transaction() is not transaction
                    or not native.in_transaction or native.total_changes != changes
                    or session.new or session.dirty or session.deleted
                    or session.get_nested_transaction()
                ):
                    raise ValueError("research_provider_writer_initial_state_changed")
                checkpoint()
                if deadline_at <= utcnow():
                    raise PermissionError("original_preparation_deadline_elapsed")
                return
    except BaseException as error:
        failure = error
        raise
    finally:
        try:
            native.execute(f"PRAGMA busy_timeout={original_timeout}")
            if native.execute("PRAGMA busy_timeout").fetchone() != (original_timeout,):
                raise ValueError("research_provider_writer_busy_timeout_restore_failed")
        except Exception as restore_error:
            # Discard an uncertain timeout/connection. No business writes ran.
            try:
                connection.invalidate()
            except Exception:
                restore_error.add_note("research_provider_writer_connection_invalidation_failed")
            if failure is not None:
                failure.add_note("research_provider_writer_busy_timeout_restore_failed")
                raise failure from restore_error
            raise ValueError("research_provider_writer_busy_timeout_restore_failed") from restore_error


MAX_BYTES = 1_000_000
MAX_INPUT_BYTES = 8 * 1024 * 1024
SYNTHESIS_TOOL = "decision_hub_synthesis_submit"
type Submit = (
    ResearchProviderInputSubmit | ResearchProviderToolEmissionSubmit | ResearchProviderFinishSubmit
)
type Original = ProviderInputOriginal | ProviderToolEmissionOriginal | ProviderFinishOriginal


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def borrowed_provider_read[R, **P](
    operation: Callable[Concatenate[Session, P], R],
) -> Callable[Concatenate[Session, P], R]:
    """Preserve the actual caller connection, TX, authorizer and pending UOW."""

    @wraps(operation)
    def read(session: Session, /, *args: P.args, **kwargs: P.kwargs) -> R:
        connection = session.connection()
        native = connection.connection.driver_connection
        if not isinstance(native, sqlite3.Connection) or not native.in_transaction:
            raise ValueError("research_provider_physical_snapshot_required")
        changes, transaction = native.total_changes, session.get_transaction()
        uow = tuple(set(objects) for objects in (session.new, session.dirty, session.deleted))

        def guard(
            _connection: Any,
            _cursor: Any,
            statement: str,
            _parameters: Any,
            _context: Any,
            _many: bool,
        ) -> None:
            if not statement.lstrip().upper().startswith("SELECT "):
                raise ValueError("research_provider_read_sql_forbidden")

        event.listen(connection, "before_cursor_execute", guard)
        try:
            with session.no_autoflush:
                return operation(session, *args, **kwargs)
        finally:
            event.remove(connection, "before_cursor_execute", guard)
            if (
                session.connection() is not connection
                or not native.in_transaction
                or session.get_transaction() is not transaction
                or native.total_changes != changes
                or uow
                != tuple(set(objects) for objects in (session.new, session.dirty, session.deleted))
            ):
                raise ValueError("research_provider_read_snapshot_changed")

    return read


def _permission(session: Session, run_id: str, owner: str | None) -> RunRecord:
    from .research_graph_binding import (
        _permission as require,  # pyright: ignore[reportPrivateUsage]
    )

    return require(session, run_id, owner)


def _fence(session: Session, run_id: str, owner: str | None) -> None:
    from .research_graph_binding import _fence as require  # pyright: ignore[reportPrivateUsage]

    require(session, run_id, owner)


def strict_json(text: str) -> Any:
    return _strict_json(text, max_bytes=MAX_BYTES)


def strict_input_json(text: str) -> Any:
    """The complete messages/system/tools original has its own byte budget."""
    return _strict_json(text, max_bytes=MAX_INPUT_BYTES)


def _strict_json(text: str, *, max_bytes: int) -> Any:
    if len(text.encode("utf-8")) > max_bytes:
        raise ValueError("research_provider_bytes_exceeded")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("research_provider_duplicate_json_key")
            result[key] = value
        return result

    def invalid(_: str) -> Any:
        raise ValueError("research_provider_nonfinite_json")

    try:
        parsed = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)
        count = 0

        def bound(value: Any, depth: int) -> None:
            nonlocal count
            count += 1
            if depth > 64 or count > 100_000:
                raise ValueError("research_provider_json_complexity_exceeded")
            if isinstance(value, dict):
                for key, item in value.items():
                    key.encode("utf-8")
                    bound(item, depth + 1)
            elif isinstance(value, list):
                for item in value:
                    bound(item, depth + 1)
            elif isinstance(value, str):
                value.encode("utf-8")
            elif type(value) is float and not math.isfinite(value):
                invalid("")

        bound(parsed, 0)
        return parsed
    except (RecursionError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("research_provider_json_invalid") from exc


def tool_arguments_json(value: Any) -> str:
    """Sorted UTF-16 keys / ECMAScript finite number wire identity.

    Preserve raw bytes separately. Integers outside the JS safe range cannot be
    used to confer a cross-language identity. This is not a DTO normalization.
    """
    if value is None or type(value) in {bool, str}:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if type(value) is int:
        if abs(value) > 2**53 - 1:
            raise ValueError("research_provider_unsafe_integer")
        return str(value)
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("research_provider_nonfinite_json")
        if value == 0:
            return "0"
        text = repr(value).lower()
        if 1e-6 <= abs(value) < 1e21:
            return (
                format(Decimal(text), "f").rstrip("0").rstrip(".")
                if "." in format(Decimal(text), "f")
                else format(Decimal(text), "f")
            )
        mantissa, exponent = text.split("e")
        mantissa = mantissa.rstrip("0").rstrip(".") if "." in mantissa else mantissa
        number = int(exponent)
        return mantissa + "e" + ("+" if number >= 0 else "-") + str(abs(number))
    if isinstance(value, list):
        return "[" + ",".join(tool_arguments_json(item) for item in value) + "]"
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        values = cast(dict[str, Any], value)
        return (
            "{"
            + ",".join(
                tool_arguments_json(key) + ":" + tool_arguments_json(values[key])
                for key in sorted(values, key=lambda item: item.encode("utf-16-be"))
            )
            + "}"
        )
    raise ValueError("research_provider_json_value_invalid")


def _digest(payload: Submit, prefix: str) -> str:
    identity = [payload.run_id, payload.generation, payload.root_session_id]
    if isinstance(payload, ResearchProviderToolEmissionSubmit):
        identity.append(payload.call_id)
    else:
        identity.extend([payload.execution_session_id, payload.dispatch_id])
    return sha(prefix + "\0" + observation_json(identity))[:40]


def _address(payload: Submit) -> tuple[str, str, str]:
    prefix, schema = (
        ("model_input", "research-provider-input.v1")
        if isinstance(payload, ResearchProviderInputSubmit)
        else ("model_emit", "research-provider-tool-emission.v1")
        if isinstance(payload, ResearchProviderToolEmissionSubmit)
        else ("model_finish", "research-provider-finish.v1")
    )
    digest = _digest(payload, prefix)
    return prefix + "_" + digest, prefix + ":" + digest, schema


def row_reference(row: RunRoleOutputRecord) -> ObservedExecutionRef:
    recorded = as_utc(cast(datetime | None, row.created_at))
    if sha(row.payload_json) != row.payload_hash or recorded is None:
        raise ValueError("research_provider_original_hash_invalid")
    return ObservedExecutionRef(
        output_id=row.output_id,
        role=row.role,
        schema_version=row.schema_version,
        payload_hash=row.payload_hash,
        recorded_at=recorded,
    )


def _read_row(
    session: Session, output_id: str, expected_hash: str | None = None
) -> tuple[Original, ObservedExecutionRef]:
    row = session.get(RunRoleOutputRecord, output_id, populate_existing=True)
    if row is None:
        raise ValueError("research_provider_original_missing")
    ref = row_reference(row)
    if expected_hash is not None and ref.payload_hash != expected_hash:
        raise ValueError("research_provider_original_reference_invalid")
    model = {
        "research-provider-input.v1": ProviderInputOriginal,
        "research-provider-tool-emission.v1": ProviderToolEmissionOriginal,
        "research-provider-finish.v1": ProviderFinishOriginal,
    }.get(row.schema_version)
    if model is None:
        raise ValueError("research_provider_original_schema_invalid")
    value = model.model_validate_json(row.payload_json)
    address = _address(value.submit)
    if (
        address != (row.output_id, row.role, row.schema_version)
        or row.run_id != value.submit.run_id
        or row.payload_json != observation_json(value.model_dump(mode="json"))
        or as_utc(value.known_at) != ref.recorded_at
    ):
        raise ValueError("research_provider_original_identity_invalid")
    return value, ref


@borrowed_provider_read
def read_provider_input_in_session(
    session: Session, reference: ResearchProviderInputRef
) -> tuple[ProviderInputOriginal, ObservedExecutionRef]:
    value, ref = _read_row(session, reference.output_id, reference.payload_hash)
    if not isinstance(value, ProviderInputOriginal):
        raise ValueError("research_provider_input_reference_invalid")
    _validate_input(value.submit)
    context = value.native_context
    submit = value.submit
    if (
        context.run_id != submit.run_id
        or context.generation != submit.generation
        or context.dsh_session_id != submit.root_session_id
        or context.first_linked_request_hash != submit.request_hash
        or context.prompt_hash != value.prompt_hash
    ):
        raise ValueError("research_provider_native_context_invalid")
    for source_ref in (*value.source_refs, value.model_binding_ref):
        source = session.get(RunRoleOutputRecord, source_ref.output_id, populate_existing=True)
        if source is None or source.run_id != submit.run_id or row_reference(source) != source_ref:
            raise ValueError("research_provider_source_original_changed")
    if not set(context.rows).issubset(set(value.source_refs)):
        raise ValueError("research_provider_source_original_missing")
    prompt = session.get(
        DshSessionPromptRecord, (submit.run_id, submit.generation), populate_existing=True
    )
    if (
        prompt is None
        or sha(prompt.prompt) != value.prompt_hash
        or prompt.request_id != value.prompt_request_id
        or prompt.dsh_session_id != submit.root_session_id
        or prompt.request_hash != submit.request_hash
    ):
        raise ValueError("research_provider_prompt_original_changed")
    if submit.previous_input_ref is not None:
        # One hop suffices to authenticate a declared predecessor; no latest
        # dispatch heuristic and no recursive/cyclic caller traversal.
        previous, _ = _read_row(
            session, submit.previous_input_ref.output_id, submit.previous_input_ref.payload_hash
        )
        if (
            not isinstance(previous, ProviderInputOriginal)
            or previous.known_at > value.known_at
            or (
                previous.submit.run_id,
                previous.submit.generation,
                previous.submit.root_session_id,
                previous.submit.execution_session_id,
                previous.submit.host_scope,
            )
            != (
                submit.run_id,
                submit.generation,
                submit.root_session_id,
                submit.execution_session_id,
                submit.host_scope,
            )
            or previous.submit.dispatch_id == submit.dispatch_id
        ):
            raise ValueError("research_provider_previous_input_invalid")
    return value, ref


def _validate_input(payload: ResearchProviderInputSubmit) -> None:
    value = strict_input_json(payload.input_json)
    if (
        sha(payload.input_json) != payload.input_hash
        or not isinstance(value, dict)
        or set(value) != {"messages", "system", "tools"}
        or not isinstance(value["messages"], list)
        or not isinstance(value["tools"], list)
        or payload.temperature is not None
        and not math.isfinite(payload.temperature)
    ):
        raise ValueError("research_provider_input_invalid")
    if payload.execution_session_id == payload.root_session_id:
        if payload.parent_session_id is not None:
            raise ValueError("research_provider_lineage_invalid")
    elif payload.parent_session_id in {None, payload.execution_session_id}:
        raise ValueError("research_provider_lineage_invalid")


def _linked_input(
    session: Session, payload: ResearchProviderToolEmissionSubmit | ResearchProviderFinishSubmit
) -> ProviderInputOriginal:
    original, _ = read_provider_input_in_session(session, payload.input_ref)
    if any(
        getattr(original.submit, name) != getattr(payload, name)
        for name in (
            "run_id",
            "generation",
            "root_session_id",
            "execution_session_id",
            "dispatch_id",
            "request_hash",
            "plugin_build_hash",
        )
    ):
        raise ValueError("research_provider_dispatch_identity_invalid")
    return original


def _candidate(payload: ResearchProviderToolEmissionSubmit) -> str | None:
    arguments = strict_json(payload.raw_arguments_json)
    if not isinstance(arguments, dict) or (
        sha(payload.raw_arguments_json) != payload.raw_arguments_hash
        or sha(tool_arguments_json(arguments)) != payload.arguments_hash
    ):
        raise ValueError("research_provider_tool_arguments_invalid")
    if payload.tool_name != SYNTHESIS_TOOL:
        return None
    # Exact representation repairs already implemented by the installed Host
    # synthesis Tool. No claim, Evidence ID or probability is invented.
    if not isinstance(arguments.get("candidate"), dict):
        return None
    if arguments["candidate"].get("schema_version") == "research-decision-synthesis.v2":
        from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
            ActionReviewRequestV2,
        )

        from ..ports.research_action_synthesis import normalize_action_synthesis

        if set(arguments) != {"candidate", "review_request"}:
            return None
        try:
            synthesis = normalize_action_synthesis(arguments["candidate"])
            packet = ActionReviewRequestV2.model_validate(arguments["review_request"])
        except ValueError:
            # Invalid full-product references remain original invalid provider
            # blocks. They cannot acquire a valid identity via spot projection.
            return None
        if packet.synthesis != synthesis:
            return None
        return observation_sha(synthesis.model_dump(mode="json"))
    if set(arguments) != {"candidate"}:
        return None
    if arguments["candidate"].get("schema_version") == "research-decision-synthesis.v1":
        from ..ports.research_action_synthesis import normalize_action_synthesis

        try:
            synthesis = normalize_action_synthesis(arguments["candidate"])
        except ValidationError:
            return None
        # This original field still identifies the old research projection.
        # raw_arguments_hash/arguments_hash bind the complete provider block;
        # only the frozen request's protocol and Tool gate can accept it.
        return observation_sha(synthesis.research_candidate_v1.model_dump(mode="json"))
    normalized = json.loads(json.dumps(arguments["candidate"]))
    causal = normalized.get("causal_case")
    if (
        "horizons" not in normalized
        and isinstance(causal, dict)
        and isinstance(causal.get("horizons"), list)
    ):
        normalized["horizons"] = causal.pop("horizons")

    def unique(value: Any) -> Any:
        return (
            list(dict.fromkeys(value))
            if isinstance(value, list) and all(isinstance(item, str) for item in value)
            else value
        )

    if isinstance(normalized.get("horizons"), list):
        for horizon in normalized["horizons"]:
            if isinstance(horizon, dict) and "evidence_refs" in horizon:
                horizon["evidence_refs"] = unique(horizon["evidence_refs"])
    if isinstance(causal, dict):
        cited = []
        for name in ("main_chain", "opposite_chain"):
            if isinstance(causal.get(name), list):
                for link in causal[name]:
                    if isinstance(link, dict) and "evidence_refs" in link:
                        link["evidence_refs"] = unique(link["evidence_refs"])
                        if isinstance(link["evidence_refs"], list):
                            cited.extend(
                                item for item in link["evidence_refs"] if isinstance(item, str)
                            )
        causal["evidence_refs"] = unique(causal.get("evidence_refs", cited))
    try:
        candidate = ResearchSynthesisCandidate.model_validate(normalized)
    except ValidationError:
        # Retain the actual invalid provider block; it is not a successful
        # synthesis. Normal Tool schema errors may still be repaired later.
        return None
    return observation_sha(candidate.model_dump(mode="json"))


def _usage(payload: ResearchProviderFinishSubmit) -> tuple[int | None, int | None, int | None]:
    body = strict_json(payload.finish_json)
    if (
        sha(payload.finish_json) != payload.finish_hash
        or not isinstance(body, dict)
        or set(body) != {"finish", "usage_chunks"}
        or not isinstance(body["usage_chunks"], list)
        or len(body["usage_chunks"]) > 64
    ):
        raise ValueError("research_provider_finish_invalid")
    finish = body["finish"]
    if finish is not None and (
        not isinstance(finish, dict)
        or set(finish) != {"type", "reason"}
        or finish["type"] != "finish"
        or not isinstance(finish["reason"], dict)
        or not isinstance(finish["reason"].get("kind"), str)
    ):
        raise ValueError("research_provider_finish_invalid")
    if payload.status == "completed" and (finish is None or payload.error_code is not None):
        raise ValueError("research_provider_finish_missing")
    kind = finish["reason"]["kind"] if finish is not None else None
    if payload.status == "completed" and kind not in {"stop", "tool-calls", "max-tokens"}:
        raise ValueError("research_provider_finish_invalid")
    usages = []
    for chunk in body["usage_chunks"]:
        if (
            not isinstance(chunk, dict)
            or set(chunk) != {"type", "usage"}
            or chunk["type"] != "usage"
            or not isinstance(chunk["usage"], dict)
        ):
            raise ValueError("research_provider_usage_chunk_invalid")
        usages.append(chunk["usage"])
    # SDK stream accounting is not a run-wide fee ledger. Until its exact
    # counter semantics are complete, missing or multiple usage chunks stay
    # unknown rather than being summed or filled with zero.
    if len(usages) != 1:
        return None, None, None
    usage = usages[0]

    def counter(key: str) -> int | None:
        value = usage.get(key)
        return value if type(value) is int and value >= 0 else None

    return counter("inputTokens"), counter("outputTokens"), counter("totalTokens")


@borrowed_provider_read
def read_provider_emission_in_session(
    session: Session,
    *,
    run_id: str,
    generation: int,
    root_session_id: str,
    call_id: str,
    execution_session_id: str,
) -> tuple[ProviderToolEmissionOriginal, ObservedExecutionRef] | None:
    digest = sha("model_emit\0" + observation_json([run_id, generation, root_session_id, call_id]))[
        :40
    ]
    row = session.get(RunRoleOutputRecord, "model_emit_" + digest, populate_existing=True)
    if row is None:
        return None
    value, ref = _read_row(session, row.output_id)
    if (
        not isinstance(value, ProviderToolEmissionOriginal)
        or value.submit.execution_session_id != execution_session_id
    ):
        raise ValueError("research_provider_emission_scope_invalid")
    input_original = _linked_input(session, value.submit)
    if value.known_at < input_original.known_at or value.candidate_hash != _candidate(value.submit):
        raise ValueError("research_provider_emission_original_invalid")
    return value, ref


@borrowed_provider_read
def read_provider_finish_in_session(
    session: Session, input_original: ProviderInputOriginal
) -> tuple[ProviderFinishOriginal, ObservedExecutionRef] | None:
    payload = input_original.submit
    digest = sha(
        "model_finish\0"
        + observation_json(
            [
                payload.run_id,
                payload.generation,
                payload.root_session_id,
                payload.execution_session_id,
                payload.dispatch_id,
            ]
        )
    )[:40]
    row = session.get(RunRoleOutputRecord, "model_finish_" + digest, populate_existing=True)
    if row is None:
        return None
    value, ref = _read_row(session, row.output_id)
    if (
        not isinstance(value, ProviderFinishOriginal)
        or _linked_input(session, value.submit) != input_original
        or value.known_at < input_original.known_at
        or _usage(value.submit) != (value.input_tokens, value.output_tokens, value.total_tokens)
    ):
        raise ValueError("research_provider_finish_original_invalid")
    return value, ref


@dataclass(frozen=True)
class _Prepared:
    boundary: ExecutionBoundary
    input: ProviderInputOriginal
    owner: str


class ProviderInputReceiptService:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.database, self.clock = database, clock

    def _prepare(
        self, session: Session, payload: Submit, *, diagnostics: OperationPhases | None = None,
    ) -> _Prepared:
        from .research_recipe_read_scope import recipe_original_read_scope

        phases = diagnostics or OperationPhases()
        with recipe_original_read_scope(session, diagnostics=phases):
            return self._prepare_original(session, payload, diagnostics=phases)

    def _prepare_original(
        self, session: Session, payload: Submit, *, diagnostics: OperationPhases | None = None,
    ) -> _Prepared:
        phases = diagnostics or OperationPhases()
        with phases.phase("auth_prepared_execution"):
            if isinstance(payload, ResearchProviderInputSubmit):
                _validate_input(payload)
                model_payload = payload
            else:
                model_payload = _linked_input(session, payload).submit
            saved = read_prepared_execution_in_session(session, payload.run_id)
        with phases.phase("auth_current_permission"):
            boundary = current_execution_boundary(session, payload.run_id, prepared=saved)
            actual_run = session.get(RunRecord, payload.run_id, populate_existing=True)
            if actual_run is None:
                raise KeyError(payload.run_id)
            run = _permission(session, payload.run_id, actual_run.lease_owner)
            link = session.get(DshSessionLinkRecord, payload.run_id, populate_existing=True)
            if link is None or (
                link.dsh_session_id,
                link.generation,
                link.request_hash,
                link.plugin_build_hash,
            ) != (
                payload.root_session_id,
                payload.generation,
                payload.request_hash,
                payload.plugin_build_hash,
            ):
                raise ValueError("research_provider_current_execution_mismatch")
            ModelDispatchService._require_active_link(link)  # pyright: ignore[reportPrivateUsage]
        with phases.phase("auth_model_context"):
            model_role = binding_role(
                None
                if model_payload.execution_session_id == model_payload.root_session_id
                else model_payload.execution_session_id
            )
            model_row = session.scalar(
                select(RunRoleOutputRecord)
                .filter_by(run_id=payload.run_id, role=model_role)
                .execution_options(populate_existing=True)
            )
            if model_row is None:
                raise ValueError("research_provider_model_binding_missing")
            binding = ModelDispatchService._read(model_row)  # pyright: ignore[reportPrivateUsage]
            if (
                any(
                    getattr(binding, name) != getattr(model_payload, name)
                    for name in (
                        "provider",
                        "model",
                        "reasoning_effort",
                        "temperature",
                        "max_tokens",
                        "stop_sequences_hash",
                        "request_hash",
                        "plugin_build_hash",
                        "parent_session_id",
                    )
                )
                or binding.dsh_session_id != payload.root_session_id
            ):
                raise ValueError("research_provider_model_binding_mismatch")
            context = native_observation_context_in_session(session, payload.run_id, prepared=saved)
            refs = list(context.rows)
            selection = getattr(saved.execution, "current_selection_row", None)
            if selection is not None:
                row = session.get(RunRoleOutputRecord, selection.output_id, populate_existing=True)
                if row is None or row.payload_hash != selection.payload_hash:
                    raise ValueError("research_provider_selection_missing")
                refs.append(row_reference(row))
            recipe = session.scalar(
                select(RunRoleOutputRecord)
                .filter_by(run_id=payload.run_id, role="graph_recipe_selection")
                .execution_options(populate_existing=True)
            )
            if recipe is not None:
                refs.append(row_reference(recipe))
            now = self.clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("research_provider_server_clock_invalid")
            if model_payload.host_scope.started_at > now:
                raise ValueError("research_provider_host_clock_invalid")
            body = ProviderInputOriginal(
                schema_version="research-provider-input.v1",
                submit=model_payload,
                native_context=context,
                source_refs=tuple(refs),
                prompt_request_id=saved.prompt.request_id,
                prompt_hash=sha(saved.prompt.prompt),
                model_binding_ref=row_reference(model_row),
                known_at=now.astimezone(UTC),
            )
            assert run.lease_owner is not None
            return _Prepared(boundary, body, run.lease_owner)

    def _record(
        self, payload: Submit, *, request_check: Callable[[], None] | None = None,
        diagnostics: ProviderCallbackDiagnostics | None = None,
    ) -> tuple[Original, ObservedExecutionRef]:
        with callback_source_lease_scope(
            request_check, defer_unselected_recipe_archives=True,
        ) as source_lease:
            return self._record_in_source_scope(
                payload, request_check=request_check, diagnostics=diagnostics,
                source_lease=source_lease,
            )

    def _record_in_source_scope(
        self, payload: Submit, *, request_check: Callable[[], None] | None = None,
        diagnostics: ProviderCallbackDiagnostics | None = None,
        source_lease: CallbackSourceLease | None = None,
    ) -> tuple[Original, ObservedExecutionRef]:
        check = request_check or (lambda: None)
        payload = type(payload).model_validate_json(payload.model_dump_json())
        kind = (
            "input" if isinstance(payload, ResearchProviderInputSubmit)
            else "emission" if isinstance(payload, ResearchProviderToolEmissionSubmit) else "finish"
        )
        diagnostics = diagnostics or ProviderCallbackDiagnostics(payload, kind)
        phases = diagnostics.operation_phases
        check()

        def authenticate(session: Session) -> _Prepared:
            value = self._prepare(session, payload, diagnostics=phases)
            if source_lease is not None:
                source_lease.extend_authenticated_selection(session, payload.run_id)
            return value

        prepared = prepare_originals(
            self.database,
            authenticate,
            link_activity_observations=True,
            link_tool_counter_observations=True,
            diagnostics=phases,
            checkpoint=check,
            source_lease=source_lease,
        )
        check()
        if source_lease is not None:
            source_lease.seal_authenticated_selection(payload.run_id)
        check()
        with self.database.session() as session:

            def before_commit(_session: Session) -> None:
                check()

            event.listen(session, "before_commit", before_commit)
            with phases.phase("writer_wait"):
                _begin_provider_callback(
                    session, checkpoint=check, deadline_at=prepared.value.boundary.deadline_at,
                )
            phases.writer_acquired(session)
            diagnostics.observe_commit(session)
            check()
            prepared.verify(session, diagnostics=phases, checkpoint=check)
            prepared.value.boundary.require_current_link(session, payload.run_id)
            _permission(session, payload.run_id, prepared.value.owner)
            check()
            output_id, role, schema = _address(payload)
            existing = session.get(RunRoleOutputRecord, output_id, populate_existing=True)
            if existing is not None:
                value, ref = _read_row(session, output_id)
                if value.submit != payload:
                    raise ValueError("research_provider_callback_conflict")
                if isinstance(value, ProviderInputOriginal):
                    with diagnostics.phase("input_verify"):
                        read_provider_input_in_session(
                            session,
                            ResearchProviderInputRef(
                                output_id=ref.output_id, payload_hash=ref.payload_hash
                            ),
                        )
                elif isinstance(value, ProviderToolEmissionOriginal):
                    _linked_input(session, value.submit)
                    if _candidate(value.submit) != value.candidate_hash:
                        raise ValueError("research_provider_emission_original_invalid")
                else:
                    _linked_input(session, value.submit)
                    if _usage(value.submit) != (
                        value.input_tokens,
                        value.output_tokens,
                        value.total_tokens,
                    ):
                        raise ValueError("research_provider_finish_original_invalid")
            else:
                now = prepared.value.input.known_at
                if isinstance(payload, ResearchProviderInputSubmit):
                    value = prepared.value.input
                elif isinstance(payload, ResearchProviderToolEmissionSubmit):
                    original = _linked_input(session, payload)
                    if now < original.known_at:
                        raise ValueError("research_provider_callback_clock_invalid")
                    value = ProviderToolEmissionOriginal(
                        schema_version="research-provider-tool-emission.v1",
                        submit=payload,
                        candidate_hash=_candidate(payload),
                        known_at=now,
                    )
                else:
                    original = _linked_input(session, payload)
                    if now < original.known_at:
                        raise ValueError("research_provider_callback_clock_invalid")
                    input_tokens, output_tokens, total_tokens = _usage(payload)
                    value = ProviderFinishOriginal(
                        schema_version="research-provider-finish.v1",
                        submit=payload,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=total_tokens,
                        known_at=now,
                    )
                encoded = observation_json(value.model_dump(mode="json"))
                row = RunRoleOutputRecord(
                    output_id=output_id,
                    run_id=payload.run_id,
                    role=role,
                    schema_version=schema,
                    payload_json=encoded,
                    payload_hash=sha(encoded),
                    created_at=value.known_at,
                )
                session.add(row)
                session.flush()
                ref = row_reference(row)
                if isinstance(value, ProviderInputOriginal):
                    with diagnostics.phase("input_verify"):
                        read_provider_input_in_session(
                            session,
                            ResearchProviderInputRef(
                                output_id=ref.output_id, payload_hash=ref.payload_hash
                            ),
                        )
            with diagnostics.phase("postwrite"):
                prepared.verify_after_writes(session, diagnostics=phases, checkpoint=check)
                prepared.value.boundary.require_current_link(session, payload.run_id)
                _fence(session, payload.run_id, prepared.value.owner)
                check()
        diagnostics.event("return", "finished")
        return value, ref

    def record_input(
        self, payload: ResearchProviderInputSubmit, **kwargs: Any
    ) -> ResearchProviderInputRecorded:
        value, ref = self._record(payload, **kwargs)
        assert isinstance(value, ProviderInputOriginal)
        return ResearchProviderInputRecorded.model_validate_json(
            observation_json(
                dict(
                    schema_version="research-provider-input-recorded.v1",
                    reference=ref.model_dump(mode="json"),
                    dispatch_id=payload.dispatch_id,
                    input_hash=payload.input_hash,
                    known_at=value.known_at.isoformat(),
                    producer_authenticated=True,
                    proof_scope="dsh_llm_generate_options",
                )
            )
        )

    @staticmethod
    def _emission_recorded(
        value: ProviderToolEmissionOriginal, ref: ObservedExecutionRef
    ) -> ResearchProviderToolEmissionRecorded:
        payload = value.submit
        fields = payload.model_dump(
            mode="json",
            include={
                "input_ref",
                "run_id",
                "generation",
                "root_session_id",
                "execution_session_id",
                "dispatch_id",
                "call_id",
                "tool_name",
                "raw_arguments_hash",
                "arguments_hash",
            },
        )
        return ResearchProviderToolEmissionRecorded.model_validate_json(
            observation_json(
                dict(
                    schema_version="research-provider-tool-emission-recorded.v1",
                    **fields,
                    reference=ref.model_dump(mode="json"),
                    candidate_hash=value.candidate_hash,
                    proof_scope="dsh_llm_generate_options",
                )
            )
        )

    def record_emission(
        self, payload: ResearchProviderToolEmissionSubmit, **kwargs: Any
    ) -> ResearchProviderToolEmissionRecorded:
        value, ref = self._record(payload, **kwargs)
        assert isinstance(value, ProviderToolEmissionOriginal)
        return self._emission_recorded(value, ref)

    def record_finish(
        self, payload: ResearchProviderFinishSubmit, **kwargs: Any
    ) -> ResearchProviderFinishRecorded:
        value, ref = self._record(payload, **kwargs)
        assert isinstance(value, ProviderFinishOriginal)
        return ResearchProviderFinishRecorded.model_validate_json(
            observation_json(
                dict(
                    schema_version="research-provider-finish-recorded.v1",
                    reference=ref.model_dump(mode="json"),
                    input_ref=payload.input_ref.model_dump(mode="json"),
                    dispatch_id=payload.dispatch_id,
                    finish_hash=payload.finish_hash,
                    status=payload.status,
                    known_at=value.known_at.isoformat(),
                    proof_scope="dsh_llm_generate_options",
                )
            )
        )

    def get_emission(
        self, run_id: str, call_id: str, *, generation: int, execution_session_id: str
    ) -> ResearchProviderToolEmissionRecorded | None:
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            link = session.get(DshSessionLinkRecord, run_id)
            if link is None:
                raise KeyError(run_id)
            saved = read_provider_emission_in_session(
                session,
                run_id=run_id,
                generation=generation,
                root_session_id=link.dsh_session_id,
                call_id=call_id,
                execution_session_id=execution_session_id,
            )
            return self._emission_recorded(*saved) if saved else None
