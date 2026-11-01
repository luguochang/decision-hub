"""Explicit development-only terminal publication scope over exact old sources."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, SessionTransaction

from ..persistence.db import Database, RunRecord, RunRoleOutputRecord
from .development_history_snapshot import (
    DevelopmentHistorySnapshot,
    DevelopmentRuntimePaths,
    capture_current_development_snapshot,
    native_history_path,
    read_development_history_snapshot,
)
from .research_graph_history_versions import (
    RetainedPublicationRead,
    original_publication_namespace,
    original_publication_read_scope,
    original_publication_result,
)
from .research_graph_recipe import GraphRecipeSelectionV2


def _canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _database_binding(database: Database, expected: Path) -> None:
    value = database.engine.url.database
    if (database.engine.dialect.name != "sqlite" or value is None
            or Path(value).absolute() != expected):
        raise ValueError("development_history_database_mismatch")
    for path in (*reversed(expected.parents), expected):
        try:
            observed = native_history_path(path).lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(observed.st_mode) or getattr(observed, "st_file_attributes", 0) & 0x400
                or stat.S_ISREG(observed.st_mode) and observed.st_nlink > 1):
            raise ValueError("development_history_database_mismatch")


@dataclass(frozen=True)
class _Scope:
    reader: DevelopmentHistoryReader
    session: Session
    run_id: str
    reference_json: str
    connection: Connection
    driver: sqlite3.Connection
    transaction: SessionTransaction | None
    nested: SessionTransaction | None
    changes: int
    snapshot: DevelopmentHistorySnapshot
    implementation: tuple[object, ...]

    def unchanged(self) -> bool:
        return (
            self.session.get_transaction() is self.transaction
            and self.session.get_nested_transaction() is self.nested
            and self.session.connection() is self.connection
            and self.connection.connection.driver_connection is self.driver
            and self.driver.in_transaction
            and self.driver.total_changes == self.changes
            and not self.session.new and not self.session.dirty and not self.session.deleted
            and self.reader.snapshot == self.snapshot
            and self.session.get_bind() is self.reader.database.engine
            and _implementation() == self.implementation
        )


_ACTIVE: ContextVar[_Scope | None] = ContextVar(
    "development_history_publication_scope", default=None,
)


class DevelopmentHistoryReader:
    def __init__(self, database: Database, snapshot: DevelopmentHistorySnapshot) -> None:
        self.database, self.snapshot = database, snapshot
        self.database_path = snapshot.development_root / "data/db/decision_hub.sqlite3"
        self._require_database()

    def _require_database(self) -> None:
        _database_binding(self.database, self.database_path)

    @contextmanager
    def publication_scope(
        self, session: Session, run_id: str, reference_json: str,
    ) -> Generator[None]:
        self._require_database()
        if session.new or session.dirty or session.deleted:
            raise ValueError("development_history_write_forbidden")
        if session.get_bind() is not self.database.engine:
            raise ValueError("development_history_database_mismatch")
        from .research_graph_binding import GraphFinalProjectionRef

        reference = GraphFinalProjectionRef.model_validate_json(reference_json)
        if reference.row.schema_version != "research-perpetual-graph-final.v1":
            raise ValueError("development_history_scope_mismatch")
        connection = session.connection()
        driver = connection.connection.driver_connection
        if not isinstance(driver, sqlite3.Connection) or not driver.in_transaction:
            raise ValueError("development_history_scope_mismatch")
        databases = driver.execute("PRAGMA database_list").fetchall()
        main = [row[2] for row in databases if row[1] == "main"]
        if len(main) != 1 or Path(main[0]).absolute() != self.database_path:
            raise ValueError("development_history_database_mismatch")
        run = session.get(RunRecord, run_id, populate_existing=True)
        if (run is None or run.status not in {"completed", "degraded"}
                or not run.artifact_id or run.finished_at is None):
            raise PermissionError("development_history_publication_not_terminal")
        prior = int(driver.execute("PRAGMA query_only").fetchone()[0])
        scope = _Scope(self, session, run_id, reference_json, connection, driver,
                       session.get_transaction(), session.get_nested_transaction(),
                       driver.total_changes,
                       self.snapshot, _implementation())
        token = _ACTIVE.set(scope)
        try:
            driver.execute("PRAGMA query_only=ON")
            with original_publication_read_scope(session):
                yield
                if not scope.unchanged():
                    raise ValueError("development_history_read_boundary_changed")
        finally:
            _ACTIVE.reset(token)
            try:
                if not scope.unchanged():
                    raise ValueError("development_history_read_boundary_changed")
                driver.execute("PRAGMA query_only=" + str(prior))
                if int(driver.execute("PRAGMA query_only").fetchone()[0]) != prior:
                    raise ValueError("development_history_read_cleanup_failed")
            except Exception as error:
                connection.invalidate()
                raise ValueError("development_history_read_cleanup_failed") from error

    def replay(
        self, scope: _Scope, *, declared_json: str,
    ) -> RetainedPublicationRead:
        session, run_id = scope.session, scope.run_id
        if not scope.unchanged():
            raise ValueError("development_history_read_boundary_changed")
        rows = list(session.scalars(select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role == "graph_recipe_selection",
        ).execution_options(populate_existing=True)))
        if len(rows) != 1 or rows[0].schema_version != "research-graph-recipe-selection.v2":
            raise ValueError("development_history_snapshot_unavailable")
        row = rows[0]
        row_before = (row.output_id, row.payload_hash, row.payload_json, row.created_at)
        selection = GraphRecipeSelectionV2.model_validate_json(row.payload_json)
        if selection.run_id != run_id or _sha(row.payload_json) != row.payload_hash:
            raise ValueError("development_history_snapshot_invalid")
        manifest_json = _canonical(selection.manifest.model_dump(mode="json"))
        snapshot, payloads = read_development_history_snapshot(
            self.snapshot, manifest_json, selection.dependency_hash,
        )
        prefix = "packages.kernel.decision_hub_kernel.application."
        for name in (
            "commit", "research_graph_binding", "research_graph_recipe",
            "perpetual_research_report",
        ):
            if "packages/kernel/decision_hub_kernel/application/" + name + ".py" not in payloads:
                raise ValueError("development_history_snapshot_invalid")
        with original_publication_namespace(
            "development_" + uuid4().hex, payloads, native_history_path(snapshot.root),
        ) as load:
            recipe: Any = load(prefix + "research_graph_recipe")
            original = recipe.read_graph_recipe_selection_in_session(session, run_id)
            if (original is None or original.receipt.dependency_hash != selection.dependency_hash
                    or _canonical(original.receipt.manifest.model_dump(mode="json"))
                    != manifest_json):
                raise ValueError("development_history_snapshot_invalid")
            output = _consume_original_publication(load, prefix, scope, declared_json)
        final_snapshot, final_payloads = read_development_history_snapshot(
            self.snapshot, manifest_json, selection.dependency_hash,
        )
        session.refresh(row)
        if (snapshot != final_snapshot or payloads != final_payloads or not scope.unchanged()
                or row_before != (
                    row.output_id, row.payload_hash, row.payload_json, row.created_at,
                )):
            raise ValueError("development_history_read_boundary_changed")
        return output


def _consume_original_publication(
    load: Callable[[str], ModuleType], prefix: str, scope: _Scope, declared_json: str,
) -> RetainedPublicationRead:
    session, run_id = scope.session, scope.run_id
    consumer: Any = load(prefix + "commit")
    binding: Any = load(prefix + "research_graph_binding")
    table = consumer.RunRecord.__table__
    before = session.execute(
        select(table).where(table.c.run_id == run_id),
    ).mappings().one_or_none()
    if before is None:
        raise ValueError("development_history_scope_mismatch")
    original_run = dict(before)
    if (original_run["status"] not in {"completed", "degraded"}
            or not original_run["artifact_id"] or original_run["finished_at"] is None):
        raise PermissionError("development_history_publication_not_terminal")
    private_run = session.get(consumer.RunRecord, run_id, populate_existing=True)
    artifact_id, gate = consumer._read_publication_source_in_session(
        session, run=private_run,
        reference=binding.GraphFinalProjectionRef.model_validate_json(scope.reference_json),
        declared=consumer.ResearchSessionResult.model_validate_json(declared_json),
        expected_lease_owner=None, candidate=None, gate=None,
    )
    if not isinstance(artifact_id, str) or artifact_id != original_run["artifact_id"]:
        raise ValueError("development_history_read_boundary_changed")
    gate_json = _canonical({"status": gate.status.value, "decisions": gate.decisions})
    after = session.execute(select(table).where(table.c.run_id == run_id)).mappings().one()
    if dict(after) != original_run:
        raise ValueError("development_history_read_boundary_changed")
    return original_publication_result(artifact_id, gate_json)


def prepare_development_history_reader(
    repository_root: Path, development: DevelopmentRuntimePaths, database: Database,
) -> DevelopmentHistoryReader:
    _database_binding(database, development.root / "data/db/decision_hub.sqlite3")
    snapshot = capture_current_development_snapshot(repository_root, development)
    return DevelopmentHistoryReader(database, snapshot)


def _implementation() -> tuple[object, ...]:
    functions = (
        read_development_history_snapshot, original_publication_namespace,
        original_publication_read_scope, original_publication_result,
        DevelopmentHistoryReader.replay, DevelopmentHistoryReader.publication_scope,
        _Scope.unchanged, _consume_original_publication, native_history_path,
    )
    return tuple((function, getattr(function, "__code__", None),
                  getattr(function, "__wrapped__", None),
                  getattr(getattr(function, "__wrapped__", None), "__code__", None))
                 for function in functions)


def replay_development_publication_in_session(
    session: Session, *, run_id: str, reference_json: str, declared_json: str,
    expected_lease_owner: str | None, candidate_json: str | None = None,
    gate_json: str | None = None,
) -> RetainedPublicationRead | None:
    scope = _ACTIVE.get()
    if scope is None:
        return None
    if (scope.session is not session or scope.run_id != run_id
            or scope.reference_json != reference_json or expected_lease_owner is not None
            or candidate_json is not None or gate_json is not None):
        raise ValueError("development_history_scope_mismatch")
    return scope.reader.replay(scope, declared_json=declared_json)
