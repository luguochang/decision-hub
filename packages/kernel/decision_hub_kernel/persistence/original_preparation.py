"""One-call original preparation in a read snapshot, followed by byte rechecks.

These receipts contain no execution permission. The consuming writer must
check its current Run/owner/lease/generation and perform its own final CAS.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import weakref
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import binding_contract_base as _binding_shapes

from .db import Database, DshSessionLinkRecord, RunRoleOutputRecord, as_utc, utcnow
from .operation_phases import OperationPhases
from .sqlite_busy import is_sqlite_initial_begin_busy

if TYPE_CHECKING:
    from ..application.dsh_terminal_receipts import VerifiedHostTerminal
    from ..application.research_execution_binding import FrozenExecutionPreparation
    from ..application.research_graph_binding import FrozenGraphFinal
    from ..ports.research_observation import ResearchTransportObservation
    from .callback_source_lease import CallbackSourceLease


_CURRENT_SHAPE_SCOPE = (
    _binding_shapes.shape_validation_scope,
    _binding_shapes.shape_validation_scope.__code__,
    _binding_shapes.shape_validation_scope.__wrapped__,
    _binding_shapes.shape_validation_scope.__wrapped__.__code__,
)


@contextmanager
def _current_shape_read_scope() -> Generator[None]:
    """Only pure shape successes; original reads and writer fences remain full."""
    scope, code, wrapped, wrapped_code = _CURRENT_SHAPE_SCOPE

    def require_original() -> None:
        if (_binding_shapes.shape_validation_scope is not scope or scope.__code__ is not code
                or scope.__wrapped__ is not wrapped or wrapped.__code__ is not wrapped_code
                or scope.__closure__ is None or len(scope.__closure__) != 1
                or scope.__closure__[0].cell_contents is not wrapped):
            raise ValueError("original_preparation_shape_scope_changed")

    require_original()
    try:
        with scope():
            yield
    finally:
        require_original()


@contextmanager
def _sqlite_checkpoint_scope(
    native: sqlite3.Connection, checkpoint: Callable[[], None] | None,
) -> Generator[None]:
    """Only an abort signal; never a substitute for original verification."""
    if checkpoint is None:
        yield
        return
    checkpoint()
    stopped: list[Exception] = []

    def progress() -> int:
        try:
            checkpoint()
        except Exception as error:
            if not stopped:
                stopped.append(error)
            return 1
        return 0

    native.set_progress_handler(progress, 1000)
    try:
        yield
        checkpoint()
    except Exception:
        if stopped:
            raise stopped[0]
        checkpoint()
        raise
    finally:
        native.set_progress_handler(None, 0)


class SourceGuard(Protocol):
    def matches(self) -> bool: ...


@dataclass(frozen=True)
class _ConsumerSourceGuard:
    graph: SourceGuard
    root: Path
    files: tuple[tuple[str, str], ...]

    @classmethod
    def capture(cls, graph: SourceGuard, root: Path) -> _ConsumerSourceGuard:
        return cls(graph, root, cls._files(root))

    @staticmethod
    def _files(root: Path) -> tuple[tuple[str, str], ...]:
        # Publication and model consumers are outside the Graph recipe closure.
        # Include their direct application/decision/persistence source inventory
        # without changing any frozen Graph recipe or retained implementation.
        kernel = root / "packages/kernel/decision_hub_kernel"
        return tuple(
            sorted(
                (path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
                for folder in ("application", "decision", "persistence")
                for path in (kernel / folder).glob("*.py")
            )
        )

    def matches(self) -> bool:
        try:
            return self.graph.matches() and self._files(self.root) == self.files
        except OSError:
            return False

    def matches_identity(self) -> bool:
        """For held immutable byte leases only; no independent source authority."""
        kernel = self.root / "packages/kernel/decision_hub_kernel"
        inventory = tuple(sorted(
            path.relative_to(self.root).as_posix()
            for folder in ("application", "decision", "persistence")
            for path in (kernel / folder).glob("*.py")
        ))
        return self.graph.matches_identity() and inventory == tuple(path for path, _ in self.files)


@dataclass(frozen=True)
class ExecutionBoundary:
    generation: int
    session_id: str
    request_hash: str
    plugin_build_hash: str
    deadline_at: datetime

    def require_current_link(self, session: Session, run_id: str) -> None:
        link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
        if (
            link is None
            or link.generation != self.generation
            or link.dsh_session_id != self.session_id
            or link.request_hash != self.request_hash
            or link.plugin_build_hash != self.plugin_build_hash
            or as_utc(link.deadline_at) != self.deadline_at
        ):
            raise PermissionError("original_preparation_generation_changed")
        if self.deadline_at <= utcnow():
            raise PermissionError("original_preparation_deadline_elapsed")


def current_execution_boundary(
    session: Session,
    run_id: str,
    *,
    prepared: FrozenExecutionPreparation | None = None,
) -> ExecutionBoundary:
    """RO full original proof plus current implementation gate, not permission."""
    from ..application.research_context_selection import (
        _selector_identity,  # pyright: ignore[reportPrivateUsage]
    )
    from ..application.research_execution_binding import read_prepared_execution_in_session
    from ..application.research_graph_dispatch import (
        read_graph_recipe_selection_for_run_in_session,
    )

    # The public current recipe reader rejects retained recipes for execution.
    # Read-only archive dispatch is reserved for already-completed replay.
    read_graph_recipe_selection_for_run_in_session(session, run_id)
    saved = prepared or read_prepared_execution_in_session(session, run_id)
    execution = saved.execution
    if execution.schema_version == "dsh-execution-request.v3":
        if execution.current_selection_row.row_schema_version == "research-context-selection.v4":
            from ..application.research_context_selection_v4 import (
                _selector_identity as selected_identity,  # pyright: ignore[reportPrivateUsage]
            )
        else:
            selected_identity = _selector_identity
        row = session.get(
            RunRoleOutputRecord, execution.current_selection_row.output_id, populate_existing=True
        )
        if row is None or json.loads(row.payload_json)["selector_identity"] != selected_identity():
            raise ValueError("context_selection_implementation_unavailable")
    return ExecutionBoundary(
        saved.link.generation,
        saved.link.dsh_session_id,
        saved.link.request_hash,
        saved.method.definition.upstream_identity.plugin_build_hash,
        execution.request.deadline_at,
    )


@dataclass(frozen=True)
class CompletedResultConsumerBoundary(ExecutionBoundary):
    """An on-time original terminal permits consumption, never new execution.

    The original reader and PreparedOriginals authenticate the whole receipt,
    Graph and readset. Fresh writers still require the same terminal bytes and
    current Run/owner/lease fences; no current-clock execution gate is relaxed.
    """

    terminal_receipt_ref: str
    terminal_receipt_hash: str
    completed_at: datetime
    last_seq: int
    result_ref: str
    result_hash: str
    trace_ref: str

    def require_current_link(self, session: Session, run_id: str) -> None:
        link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
        if (
            link is None
            or link.generation != self.generation
            or link.dsh_session_id != self.session_id
            or link.request_hash != self.request_hash
            or link.plugin_build_hash != self.plugin_build_hash
            or as_utc(link.deadline_at) != self.deadline_at
        ):
            raise PermissionError("original_preparation_generation_changed")
        row = session.get(
            RunRoleOutputRecord, self.terminal_receipt_ref, populate_existing=True,
        )
        if (
            self.completed_at > self.deadline_at
            or link.state != "completed" or link.error_code is not None
            or as_utc(link.terminal_at) != self.completed_at
            or link.last_seq != self.last_seq or link.result_ref != self.result_ref
            or link.result_hash != self.result_hash or link.trace_ref != self.trace_ref
            or row is None or row.run_id != run_id
            or row.role != f"dsh_authenticated_terminal:{self.generation}"
            or row.schema_version != "dsh-host-terminal-receipt.v1"
            or row.payload_hash != self.terminal_receipt_hash
            or hashlib.sha256(row.payload_json.encode()).hexdigest()
            != self.terminal_receipt_hash
        ):
            raise PermissionError("original_preparation_completed_result_changed")


def _bind_completed_result_consumer(
    run_id: str,
    boundary: ExecutionBoundary,
    terminal: VerifiedHostTerminal,
    original: FrozenGraphFinal,
    transport: ResearchTransportObservation,
) -> CompletedResultConsumerBoundary:
    """Bind already-authenticated originals; declarations alone confer nothing."""
    from packages.contracts_py.decision_hub_contracts import DshSessionResult

    from ..application.dsh_sessions import DshSessionLinkService
    from ..application.dsh_terminal_receipts import validate_terminal_result_native_metadata

    completion, context, result = terminal.completion, original.receipt.native_context, transport.reply
    if (
        completion.terminal_status != "completed" or completion.error is not None
        or completion.completed_at > boundary.deadline_at
        or completion.run_id != run_id or completion.generation != boundary.generation
        or completion.dsh_session_id != boundary.session_id
        or completion.result_ref != f"dsh-host://runs/{run_id}/result"
        or completion.result_hash is None or completion.trace_ref is None
        or original.receipt.run_id != run_id or context.run_id != run_id
        or context.generation != boundary.generation
        or context.dsh_session_id != boundary.session_id
        or context.first_linked_request_hash != boundary.request_hash
        or context.original_task_request_hash != terminal.task_request_hash
        or context.prompt_hash != terminal.prompt_hash
        or transport.context != context or transport.phase != "result"
        or not isinstance(result, DshSessionResult)
    ):
        raise PermissionError("original_preparation_completed_result_unavailable")
    if (
        result.run_id != run_id or result.generation != boundary.generation
        or result.dsh_session_id != boundary.session_id
        or result.last_seq != completion.last_seq
        or result.trace_ref != completion.trace_ref
        or result.trace_ref != f"dsh://sessions/{boundary.session_id}?last_seq={result.last_seq}"
        or result.finished_at != completion.completed_at
        or result.started_at > result.finished_at
        or result.result_hash != completion.result_hash
        or hashlib.sha256((result.final_response + "\0" + result.events_json).encode()).hexdigest()
        != completion.result_hash
        or transport.events_json_hash != hashlib.sha256(result.events_json.encode()).hexdigest()
        or transport.final_response_hash != hashlib.sha256(result.final_response.encode()).hexdigest()
    ):
        raise PermissionError("original_preparation_completed_result_mismatch")
    # The observation context carries the business Research request ID. Native
    # user/message.source.rpcId instead binds the authenticated Host turn.
    session_id, native_request_id = DshSessionLinkService.deterministic_ids(
        run_id, boundary.request_hash, boundary.generation,
    )
    if session_id != boundary.session_id:
        raise PermissionError("original_preparation_completed_result_mismatch")
    validate_terminal_result_native_metadata(result, native_request_id, completion)
    return CompletedResultConsumerBoundary(
        boundary.generation, boundary.session_id, boundary.request_hash,
        boundary.plugin_build_hash, boundary.deadline_at,
        terminal.receipt_ref, terminal.receipt_hash, completion.completed_at,
        completion.last_seq, completion.result_ref, completion.result_hash, completion.trace_ref,
    )


def current_result_consumer_boundary(
    session: Session, run_id: str, *, original: FrozenGraphFinal,
) -> ExecutionBoundary:
    """Review/Commit only: consume an exact on-time completed Host result.

    Incomplete/failure paths keep the original active execution deadline. Only
    an authenticated completed original can cross it, under unchanged writer
    permission, source/readset checks and final publication CAS.
    """
    from ..application.dsh_terminal_receipts import read_authenticated_terminal_in_session
    from ..application.research_publication_binding import read_transport_observation

    boundary = current_execution_boundary(session, run_id)
    link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
    if link is None or link.state != "completed":
        boundary.require_current_link(session, run_id)
        return boundary
    terminal = read_authenticated_terminal_in_session(session, run_id, now=utcnow())
    if terminal is None:
        raise PermissionError("original_preparation_completed_result_unavailable")
    transport = read_transport_observation(session, original.receipt.latest_observation_ref)
    completed = _bind_completed_result_consumer(run_id, boundary, terminal, original, transport)
    completed.require_current_link(session, run_id)
    return completed


def begin_immediate(session: Session) -> None:
    """Retry only an untouched initial BEGIN, once; never retry business work."""
    connection = session.connection()
    if connection.dialect.name != "sqlite":
        return
    native = connection.connection.driver_connection
    if not isinstance(native, sqlite3.Connection) or native.in_transaction:
        raise ValueError("original_writer_fresh_transaction_required")
    if session.new or session.dirty or session.deleted or session.get_nested_transaction():
        raise ValueError("original_writer_fresh_session_required")
    changes = native.total_changes
    for attempt in range(2):
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            return
        except Exception as error:
            if (
                attempt
                or not is_sqlite_initial_begin_busy(error)
                or native.in_transaction
                or native.total_changes != changes
            ):
                raise


def _scalar(value: Any) -> Any:
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float and math.isfinite(value):
        return ["float", value.hex()]
    if type(value) is bytes:
        return ["bytes", value.hex()]
    if isinstance(value, datetime):
        return ["datetime", value.isoformat()]
    raise ValueError("original_preparation_scalar_invalid")


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _volatile(column: str, statement: str) -> bool:
    # Lease renewal is not a changed original. Its owner and actual expiry are
    # checked independently in the writer, never taken from this snapshot.
    if column in {"runs_lease_expires_at", "runs_updated_at"}:
        return True
    # A same-generation status callback can advance these observations while
    # authentication runs. Keep all identity, state, terminal and deadline
    # columns in the readset; the writer also checks current execution rights.
    observations = {"last_seq", "last_seen_at", "updated_at"}
    if column in {f"dsh_session_links_{key}" for key in observations}:
        return True
    if column in observations and bool(
        re.search(r"(?is)\bFROM\s+dsh_session_links\s+WHERE\b", statement)
        and not re.search(r"(?is)\bJOIN\b", statement)
    ):
        return True
    return column in {"lease_expires_at", "updated_at"} and bool(
        re.search(r"(?is)\bFROM\s+runs\s+WHERE\b", statement)
        and not re.search(r"(?is)\bJOIN\b", statement)
    )


def _link_activity(column: str, value: Any, statement: str) -> bool:
    return value in {"admitted", "running", "idle"} and (
        column == "dsh_session_links_state"
        or column == "state"
        and bool(
            re.search(r"(?is)\bFROM\s+dsh_session_links\s+WHERE\b", statement)
            and not re.search(r"(?is)\bJOIN\b", statement)
        )
    )


def _query_digest(
    native: sqlite3.Connection,
    statement: str,
    parameters: Any,
    *,
    link_activity_observations: bool = False,
    link_tool_counter_observations: bool = False,
    checkpoint: Callable[[], None] | None = None,
) -> str:
    if checkpoint is not None:
        checkpoint()
    cursor = native.execute(statement, parameters)
    try:
        columns = tuple(item[0] for item in cursor.description or ())
        digest = hashlib.sha256(json.dumps(columns).encode())
        count = size = 0
        for row in cursor:
            if checkpoint is not None:
                checkpoint()
            body = [
                ["fresh_lease"]
                if _volatile(column, statement)
                else ["fresh_link_activity"]
                if link_activity_observations and _link_activity(column, value, statement)
                else ["fresh_tool_counter"]
                if link_tool_counter_observations and _link_counter(column, value, statement)
                else _scalar(value)
                for column, value in zip(columns, row, strict=True)
            ]
            encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
            count += 1
            size += len(encoded)
            if count > 100_000 or size > 128 * 1024 * 1024:
                raise ValueError("original_preparation_rows_limit")
            digest.update(b"\0" + encoded)
        digest.update(f"\0rows:{count}".encode())
        if checkpoint is not None:
            checkpoint()
        return digest.hexdigest()
    finally:
        cursor.close()


@dataclass(frozen=True)
class _Query:
    statement: str
    parameters: Any
    digest: str


def _link_counter(column: str, value: Any, statement: str) -> bool:
    return (
        type(value) is int
        and value >= 0
        and (
            column == "dsh_session_links_tool_calls_started"
            or column == "tool_calls_started"
            and bool(
                re.search(r"(?is)\bFROM\s+dsh_session_links\s+WHERE\b", statement)
                and not re.search(r"(?is)\bJOIN\b", statement)
            )
        )
    )


@dataclass(frozen=True)
class _Row:
    model: Any
    identity: tuple[Any, ...]
    columns: tuple[str, ...]
    digest: str
    link_activity_observations: bool = False


def _original_columns(model: Any, link_tool_counter_observations: bool = False) -> tuple[str, ...]:
    # Exactly the existing row-capture exclusions, shared by declaration factories.
    excluded = (
        {"lease_expires_at", "updated_at", "status", "artifact_id", "finished_at", "lease_owner"}
        if model.__tablename__ == "runs"
        else {"last_seq", "last_seen_at", "updated_at"}
        if model.__tablename__ == "dsh_session_links" else set()
    )
    if link_tool_counter_observations and model.__tablename__ == "dsh_session_links":
        excluded.add("tool_calls_started")
    return tuple(column.key for column in model.__table__.columns if column.key not in excluded)


@dataclass(frozen=True)
class DeclaredOriginalWrite:
    """Exact before/after scalar values issued by one complete original read.

    This is not permission, a generic ORM mutator or an allowance to omit rows.
    An insertion declares every mapped column, including its primary key.
    """

    model: Any
    identity: tuple[Any, ...]
    before: tuple[tuple[str, Any], ...] | None
    changes: tuple[tuple[str, Any], ...]


def _declared_pairs(value: tuple[tuple[str, Any], ...]) -> dict[str, Any]:
    if type(value) is not tuple or any(type(pair) is not tuple or len(pair) != 2 for pair in value):
        raise ValueError("original_preparation_declared_write_invalid")
    pairs: dict[str, Any] = {}
    for key, scalar in value:
        if type(key) is not str or key in pairs:
            raise ValueError("original_preparation_declared_write_invalid")
        _scalar(scalar)
        pairs[key] = scalar
    return pairs


def declare_existing_write(
    row: Any, changes: tuple[tuple[str, Any], ...], *,
    link_tool_counter_observations: bool = False,
) -> DeclaredOriginalWrite:
    """Freeze the old captured columns without widening existing volatile masks."""
    model = type(row)
    mapper = inspect(model)
    before = tuple((key, getattr(row, key)) for key in _original_columns(
        model, link_tool_counter_observations,
    ))
    values = _declared_pairs(changes)
    keys = tuple(column.key for column in model.__table__.columns)
    if not values or set(values) - set(keys):
        raise ValueError("original_preparation_declared_write_invalid")
    canonical = tuple((key, values[key]) for key in keys if key in values)
    identity = tuple(getattr(row, column.key) for column in mapper.primary_key)
    return DeclaredOriginalWrite(model, identity, before, canonical)


def declare_insert(row: Any) -> DeclaredOriginalWrite:
    """Freeze one full new row, never server defaults or a partial mutable dict."""
    model = type(row)
    mapper = inspect(model)
    values = tuple((column.key, getattr(row, column.key)) for column in model.__table__.columns)
    _declared_pairs(values)
    return DeclaredOriginalWrite(
        model, tuple(getattr(row, column.key) for column in mapper.primary_key), None, values,
    )


def _declared_signature(writes: tuple[DeclaredOriginalWrite, ...]) -> tuple[Any, ...]:
    if type(writes) is not tuple:
        raise ValueError("original_preparation_declared_write_invalid")
    result = []
    targets = set()
    for write in writes:
        if type(write) is not DeclaredOriginalWrite or type(write.identity) is not tuple:
            raise ValueError("original_preparation_declared_write_invalid")
        mapper = inspect(write.model)
        if len(write.identity) != len(mapper.primary_key) or any(x is None for x in write.identity):
            raise ValueError("original_preparation_declared_write_invalid")
        identity = tuple(_digest(_scalar(x)) for x in write.identity)
        target = write.model, identity
        if target in targets:
            raise ValueError("original_preparation_declared_write_invalid")
        targets.add(target)
        changes = _declared_pairs(write.changes)
        columns = tuple(column.key for column in write.model.__table__.columns)
        if not changes or set(changes) - set(columns):
            raise ValueError("original_preparation_declared_write_invalid")
        if write.before is None:
            if tuple(changes) != columns:
                raise ValueError("original_preparation_declared_insert_incomplete")
        else:
            _declared_pairs(write.before)
        for column, expected in zip(mapper.primary_key, write.identity, strict=True):
            if column.key in changes and _scalar(changes[column.key]) != _scalar(expected):
                raise ValueError("original_preparation_declared_identity_changed")
        result.append((write.model, identity,
                       None if write.before is None else _digest([[k, _scalar(v)] for k, v in write.before]),
                       _digest([[k, _scalar(v)] for k, v in write.changes])))
    return tuple(result)


def _require_declared_before(
    session: Session, writes: tuple[DeclaredOriginalWrite, ...], *,
    link_tool_counter_observations: bool, checkpoint: Callable[[], None] | None,
) -> None:
    _declared_signature(writes)
    for write in writes:
        if checkpoint is not None:
            checkpoint()
        row = session.get(write.model, write.identity, populate_existing=True)
        if write.before is None:
            if row is not None:
                raise ValueError("original_preparation_declared_absence_changed")
            continue
        columns = _original_columns(write.model, link_tool_counter_observations)
        before = _declared_pairs(write.before)
        if row is None or tuple(before) != columns or any(
            _scalar(getattr(row, key)) != _scalar(value) for key, value in before.items()
        ):
            raise ValueError("original_preparation_declared_before_changed")
        changes = _declared_pairs(write.changes)
        if not any(_scalar(getattr(row, key)) != _scalar(value) for key, value in changes.items()):
            raise ValueError("original_preparation_declared_write_no_effect")


def _row_digest(columns: tuple[str, ...], values: Any, link_activity: bool) -> str:
    return _digest(
        [
            ["fresh_link_activity"]
            if link_activity and column == "state" and value in {"admitted", "running", "idle"}
            else _scalar(value)
            for column, value in zip(columns, values, strict=True)
        ]
    )


class OriginalPreparationReadsetChanged(ValueError):
    """Safe diagnostic identity: no query parameters or original content."""

    def __init__(self, statement: str) -> None:
        super().__init__("original_preparation_readset_changed")
        self.query_fingerprint = hashlib.sha256(statement.encode()).hexdigest()


# Issuance only: no verification/permission result is cached. The weak owner
# releases these query/source identities when its one prepared object dies.
_ISSUED_ORIGINALS: dict[int, tuple[Any, ...]] = {}
_DECLARED_WRITER_FENCES: dict[int, tuple[Any, ...]] = {}


@dataclass(frozen=True)
class PreparedOriginals[T]:
    value: T
    queries: tuple[_Query, ...]
    rows: tuple[_Row, ...]
    source: SourceGuard
    link_activity_observations: bool = False
    link_tool_counter_observations: bool = False
    declared_writes: tuple[DeclaredOriginalWrite, ...] = ()
    declared_write_set: bool = False

    def _require_issued(self, session: Session) -> None:
        issued = _ISSUED_ORIGINALS.get(id(self))
        bind = session.get_bind()
        engine = getattr(bind, "engine", bind)
        if (type(self) is not PreparedOriginals or issued is None or issued[0]() is not self
                or engine is not issued[1] or session is issued[2]()
                or self.value is not issued[3] or self.queries is not issued[4]
                or self.rows is not issued[5] or self.source is not issued[6]
                or (self.link_activity_observations, self.link_tool_counter_observations)
                != issued[7] or self.declared_writes is not issued[8]
                or _declared_signature(self.declared_writes) != issued[9]
                or self.declared_write_set is not issued[10]):
            raise ValueError("original_preparation_unissued")

    def _verify_readset(
        self, native: sqlite3.Connection, *, phases: OperationPhases,
        checkpoint: Callable[[], None] | None, readonly: bool,
    ) -> None:
        with _sqlite_checkpoint_scope(native, checkpoint):
            with phases.phase("ro_source_recheck" if readonly else "writer_source"):
                if not self.source.matches():
                    raise ValueError("original_preparation_source_changed")
            if checkpoint is not None:
                checkpoint()
            with phases.phase("ro_readset" if readonly else "writer_readset"):
                for query in self.queries:
                    actual = _query_digest(
                        native, query.statement, query.parameters,
                        link_activity_observations=self.link_activity_observations,
                        link_tool_counter_observations=self.link_tool_counter_observations,
                        checkpoint=checkpoint,
                    )
                    if actual != query.digest:
                        raise OriginalPreparationReadsetChanged(query.statement)

    def verify_read_snapshot(
        self, session: Session, *, diagnostics: OperationPhases | None = None,
        checkpoint: Callable[[], None] | None = None,
    ) -> None:
        """Recheck issued originals in a separate physical read-only snapshot.

        The caller holds one Connection, enables query_only, begins a physical
        transaction, and restores query_only on that SAME Connection after
        rollback. This check grants no permission: the caller must read fresh
        Run/owner/lease/cancel/deadline state after it in this new transaction.
        A CallbackSourceLease, when used, remains on its original owner thread.
        """
        self._require_issued(session)
        connection = session.connection()
        native = connection.connection.driver_connection
        if (connection.dialect.name != "sqlite" or not isinstance(native, sqlite3.Connection)
                or not native.in_transaction or session.get_nested_transaction() is not None
                or session.new or session.dirty or session.deleted
                or native.execute("PRAGMA query_only").fetchone()[0] != 1):
            raise ValueError("original_preparation_read_snapshot_required")
        self._verify_readset(native, phases=diagnostics or OperationPhases(),
                             checkpoint=checkpoint, readonly=True)

    def verify(
        self, session: Session, *, diagnostics: OperationPhases | None = None,
        checkpoint: Callable[[], None] | None = None,
    ) -> None:
        """Re-read range/absence and row results in the current physical writer."""
        phases = diagnostics or OperationPhases()
        native = session.connection().connection.driver_connection
        if not isinstance(native, sqlite3.Connection) or not native.in_transaction:
            raise ValueError("original_preparation_writer_required")
        self._verify_readset(native, phases=phases, checkpoint=checkpoint, readonly=False)
        if self.declared_write_set:
            self._require_issued(session)
            with session.no_autoflush:
                _require_declared_before(
                    session, self.declared_writes,
                    link_tool_counter_observations=self.link_tool_counter_observations,
                    checkpoint=checkpoint,
                )
            if session.new or session.dirty or session.deleted:
                raise ValueError("original_preparation_declared_pending_write")
            connection = session.connection()
            _DECLARED_WRITER_FENCES[id(self)] = (
                weakref.ref(session), session.get_transaction(), connection,
                connection.get_transaction(), native, native.total_changes,
            )

    def verify_after_declared_writes(
        self, session: Session, *, diagnostics: OperationPhases | None = None,
        checkpoint: Callable[[], None] | None = None,
    ) -> None:
        """Recheck exact issued writes in the SAME verified physical writer.

        The full range/absence readset must pass immediately before these own
        writes. BEGIN IMMEDIATE excludes concurrent writers. One real row write
        per declaration and total_changes reject undeclared extra SQL effects.
        This check adds no new volatile columns or execution authority.
        """
        self._require_issued(session)
        fence = _DECLARED_WRITER_FENCES.get(id(self))
        connection = session.connection()
        native = connection.connection.driver_connection
        if (not self.declared_write_set or fence is None or fence[0]() is not session
                or session.get_transaction() is not fence[1] or connection is not fence[2]
                or connection.get_transaction() is not fence[3] or native is not fence[4]
                or not isinstance(native, sqlite3.Connection) or not native.in_transaction
                or session.get_nested_transaction() is not None
                or session.new or session.dirty or session.deleted):
            raise ValueError("original_preparation_declared_writer_changed")
        if native.total_changes - fence[5] != len(self.declared_writes):
            raise ValueError("original_preparation_undeclared_write_effect")
        phases = diagnostics or OperationPhases()
        writes = {(write.model, write.identity): write for write in self.declared_writes}
        with _sqlite_checkpoint_scope(native, checkpoint), session.no_autoflush:
            with phases.phase("writer_after_source"):
                if not self.source.matches():
                    raise ValueError("original_preparation_source_changed")
            with phases.phase("writer_after_rows"):
                for original in self.rows:
                    if checkpoint is not None:
                        checkpoint()
                    write = writes.get((original.model, original.identity))
                    expected = original.digest
                    if write is not None:
                        if write.before is None:
                            raise ValueError("original_preparation_declared_before_changed")
                        values = {**_declared_pairs(write.before), **_declared_pairs(write.changes)}
                        expected = _row_digest(original.columns,
                            tuple(values[key] for key in original.columns),
                            original.link_activity_observations)
                    mapper = inspect(original.model)
                    actual = session.execute(select(*(
                        getattr(original.model, key) for key in original.columns
                    )).where(*(column == value for column, value in zip(
                        mapper.primary_key, original.identity, strict=True,
                    )))).one_or_none()
                    if (actual is None or _row_digest(original.columns, actual,
                            original.link_activity_observations) != expected):
                        raise ValueError("original_preparation_original_changed")
                for write in self.declared_writes:
                    if checkpoint is not None:
                        checkpoint()
                    row = session.get(write.model, write.identity, populate_existing=True)
                    values = {**({} if write.before is None else _declared_pairs(write.before)),
                              **_declared_pairs(write.changes)}
                    if row is None or any(_scalar(getattr(row, key)) != _scalar(value)
                                          for key, value in values.items()):
                        raise ValueError("original_preparation_declared_after_changed")
            if native.total_changes - fence[5] != len(self.declared_writes):
                raise ValueError("original_preparation_undeclared_write_effect")

    def verify_after_writes(
        self,
        session: Session,
        *,
        diagnostics: OperationPhases | None = None,
        checkpoint: Callable[[], None] | None = None,
    ) -> None:
        """Own added publication rows are permitted; original input rows are not."""
        from sqlalchemy import and_, or_, tuple_
        from sqlalchemy.exc import MultipleResultsFound

        phases = diagnostics or OperationPhases()
        native = session.connection().connection.driver_connection
        if checkpoint is not None and not isinstance(native, sqlite3.Connection):
            raise ValueError("original_preparation_writer_required")
        # When no checkpoint is supplied the scope never uses the native handle;
        # with a checkpoint the guard above established the SQLite connection.
        with _sqlite_checkpoint_scope(cast(sqlite3.Connection, native), checkpoint):
            with phases.phase("writer_after_source"):
                if not self.source.matches():
                    raise ValueError("original_preparation_source_changed")
            if checkpoint is not None:
                checkpoint()
            with phases.phase("writer_after_rows"):
                with session.no_autoflush:
                    groups: dict[tuple[Any, tuple[str, ...]], list[_Row]] = {}
                    for row in self.rows:
                        if checkpoint is not None:
                            checkpoint()
                        groups.setdefault((row.model, row.columns), []).append(row)
                    # Conservative parameter budget, additionally respecting a
                    # connection's real lowered SQLite limit. 128 is a bounded
                    # fetch size, not a cap on captured/verified rows.
                    bind_budget = 900
                    if isinstance(native, sqlite3.Connection):
                        bind_budget = min(bind_budget, native.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER))
                    for (model, columns), originals in groups.items():
                        primary_key = tuple(inspect(model).primary_key)
                        batch_size = max(1, min(128, bind_budget // len(primary_key)))
                        for start in range(0, len(originals), batch_size):
                            if checkpoint is not None:
                                checkpoint()
                            batch = originals[start:start + batch_size]
                            identities = [tuple(value for _, value in zip(
                                primary_key, row.identity, strict=True,
                            )) for row in batch]
                            if any(value is None for identity in identities for value in identity):
                                # Equality preserved for nullable legacy PKs:
                                # SQL tuple IN would not match NULL identities.
                                predicate = or_(*(and_(*(column == value for column, value in zip(
                                    primary_key, identity, strict=True,
                                ))) for identity in identities))
                            elif len(primary_key) == 1:
                                predicate = primary_key[0].in_([identity[0] for identity in identities])
                            else:
                                predicate = tuple_(*primary_key).in_(identities)
                            masks_by_identity: dict[tuple[Any, ...], set[bool]] = {}
                            for row in batch:
                                masks_by_identity.setdefault(row.identity, set()).add(row.link_activity_observations)
                            digests_by_identity: dict[tuple[Any, ...], dict[bool, str]] = {}
                            result = session.execute(select(
                                *primary_key, *(getattr(model, key) for key in columns),
                            ).where(predicate).execution_options(yield_per=1))
                            try:
                                for actual in result:
                                    if checkpoint is not None:
                                        checkpoint()
                                    identity = tuple(actual[:len(primary_key)])
                                    if identity in digests_by_identity:
                                        # Retain one_or_none's fail-closed
                                        # behavior for ambiguous legacy PKs.
                                        raise MultipleResultsFound("Multiple original rows for one identity")
                                    masks = masks_by_identity.get(identity)
                                    if masks is None:
                                        raise ValueError("original_preparation_original_changed")
                                    digests: dict[bool, str] = {}
                                    for activity in masks:
                                        if checkpoint is not None:
                                            checkpoint()
                                        digests[activity] = _row_digest(columns, actual[len(primary_key):], activity)
                                    # Retain only hashes, never a batch of large
                                    # payload_json values. At most two masks.
                                    digests_by_identity[identity] = digests
                            finally:
                                result.close()
                            for row in batch:
                                if checkpoint is not None:
                                    checkpoint()
                                digests = digests_by_identity.get(row.identity)
                                if (
                                    digests is None
                                    or digests[row.link_activity_observations] != row.digest
                                ):
                                    raise ValueError("original_preparation_original_changed")


def prepare_originals[T](
    database: Database,
    operation: Callable[[Session], T],
    *,
    link_activity_observations: bool = False,
    link_tool_counter_observations: bool = False,
    diagnostics: OperationPhases | None = None,
    checkpoint: Callable[[], None] | None = None,
    source_lease: CallbackSourceLease | None = None,
    write_set: Callable[[T], tuple[DeclaredOriginalWrite, ...]] | None = None,
) -> PreparedOriginals[T]:
    """Authenticate once without a RESERVED write lock or caller-supplied proof."""
    from ..application.research_graph_binding import (
        _ReadSourceGuard,  # pyright: ignore[reportPrivateUsage]
    )

    phases = diagnostics or OperationPhases()
    if checkpoint is not None:
        checkpoint()
    with phases.phase("source_capture"):
        graph_source = _ReadSourceGuard.capture()
        if graph_source is None:
            raise ValueError("original_preparation_source_unavailable")
        source = _ConsumerSourceGuard.capture(graph_source, graph_source.root)
        if source_lease is not None:
            # Both complete captures and immutable HANDLE hashes precede any
            # writer. The per-call owner holds them through actual COMMIT exit.
            source = source_lease.pin(source)
    if checkpoint is not None:
        checkpoint()
    queries: dict[str, tuple[str, Any]] = {}
    originals: dict[tuple[Any, tuple[Any, ...]], Any] = {}
    with database.session() as session:
        connection = session.connection()
        native = connection.connection.driver_connection
        if (
            connection.dialect.name != "sqlite"
            or not isinstance(native, sqlite3.Connection)
            or native.in_transaction
        ):
            raise ValueError("original_preparation_fresh_sqlite_required")
        connection.exec_driver_sql("BEGIN")
        changes = native.total_changes

        def authorize(
            action: int,
            _first: str | None,
            _second: str | None,
            _database: str | None,
            _origin: str | None,
        ) -> int:
            return (
                sqlite3.SQLITE_OK
                if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
                else sqlite3.SQLITE_DENY
            )

        def capture(
            _connection: Any,
            _cursor: Any,
            statement: str,
            parameters: Any,
            _context: Any,
            executemany: bool,
        ) -> None:
            if executemany or not statement.lstrip().upper().startswith("SELECT "):
                raise ValueError("original_preparation_select_only")
            key = _digest([statement, [_scalar(value) for value in parameters]])
            queries[key] = statement, tuple(parameters)
            if len(queries) > 8192:
                raise ValueError("original_preparation_queries_limit")

        def loaded(_session: Session, row: Any) -> None:
            state = inspect(row)
            if state.identity is not None:
                originals[type(row), state.identity] = row

        native.set_authorizer(authorize)
        event.listen(connection, "before_cursor_execute", capture)
        event.listen(session, "loaded_as_persistent", loaded)
        try:
            with _sqlite_checkpoint_scope(native, checkpoint):
                with phases.phase("original_auth"):
                    with session.no_autoflush, _current_shape_read_scope():
                        value = operation(session)
                        writes = () if write_set is None else write_set(value)
                        _require_declared_before(
                            session, writes,
                            link_tool_counter_observations=link_tool_counter_observations,
                            checkpoint=checkpoint,
                        )
                if session.new or session.dirty or session.deleted:
                    raise ValueError("original_preparation_pending_write")
                event.remove(connection, "before_cursor_execute", capture)
                with phases.phase("ro_readset"):
                    captured = tuple(
                        _Query(
                            statement,
                            parameters,
                            _query_digest(
                                native,
                                statement,
                                parameters,
                                link_activity_observations=link_activity_observations,
                                link_tool_counter_observations=link_tool_counter_observations,
                                checkpoint=checkpoint,
                            ),
                        )
                        for statement, parameters in queries.values()
                    )
                with phases.phase("ro_rows"):
                    rows = []
                    for (model, identity), row in originals.items():
                        if checkpoint is not None:
                            checkpoint()
                        columns = _original_columns(model, link_tool_counter_observations)
                        rows.append(
                            _Row(
                                model,
                                identity,
                                columns,
                                _row_digest(
                                    columns,
                                    [getattr(row, key) for key in columns],
                                    link_activity_observations
                                    and model.__tablename__ == "dsh_session_links",
                                ),
                                link_activity_observations
                                and model.__tablename__ == "dsh_session_links",
                            )
                        )
                with phases.phase("ro_source_recheck"):
                    if (
                        not native.in_transaction
                        or native.total_changes != changes
                        or not source.matches()
                    ):
                        raise ValueError("original_preparation_snapshot_changed")
        finally:
            if event.contains(connection, "before_cursor_execute", capture):
                event.remove(connection, "before_cursor_execute", capture)
            event.remove(session, "loaded_as_persistent", loaded)
            native.set_authorizer(None)
            # Database.session normally commits at exit. A preparation never
            # flushes pending objects or commits the borrowed read snapshot.
            session.rollback()
    prepared = PreparedOriginals(
        value,
        captured,
        tuple(rows),
        source,
        link_activity_observations,
        link_tool_counter_observations,
        writes,
        write_set is not None,
    )
    identity = id(prepared)
    def discard(reference: weakref.ReferenceType) -> None:
        issued = _ISSUED_ORIGINALS.get(identity)
        if issued is not None and issued[0] is reference:
            _ISSUED_ORIGINALS.pop(identity, None)
            _DECLARED_WRITER_FENCES.pop(identity, None)
    _ISSUED_ORIGINALS[identity] = (
        weakref.ref(prepared, discard), database.engine, weakref.ref(session),
        prepared.value, prepared.queries, prepared.rows, prepared.source,
        (prepared.link_activity_observations, prepared.link_tool_counter_observations),
        prepared.declared_writes, _declared_signature(prepared.declared_writes),
        prepared.declared_write_set,
    )
    return prepared
