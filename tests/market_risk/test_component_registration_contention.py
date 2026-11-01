"""File SQLite registration remains idempotent while another writer is active."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import event, select

from packages.kernel.decision_hub_kernel.application.component_registry import ComponentRegistry
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.persistence.market_case_records import (
    MarketComponentVersionRecord,
)
from packages.kernel.decision_hub_kernel.ports.component_registry import ComponentManifest

NOW = datetime(2026, 10, 18, tzinfo=UTC)


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    path = tmp_path / "components.sqlite3"
    db = Database(f"sqlite+pysqlite:///{path}")
    MarketComponentVersionRecord.__table__.create(db.engine)
    yield db, path
    db.engine.dispose()


def manifest(*, code_hash="a" * 64):
    return ComponentManifest(
        version_id="installed-feature-one",
        kind="feature",
        component_ref="test-feature",
        scope_id="crypto:spot:okx:BTC-USDT:trade:1m",
        input_contract="input.v1",
        output_contract="output.v1",
        code_hash=code_hash,
        resource_hash="b" * 64,
        parameters={"factor": 2},
    )


def persisted(db):
    with db.session() as session:
        rows = session.scalars(select(MarketComponentVersionRecord)).all()
        return [ComponentManifest.model_validate_json(row.payload_json) for row in rows]


def test_registered_component_is_readable_while_another_connection_holds_writer(database):
    db, path = database
    registry = ComponentRegistry(db, clock=lambda: NOW)
    installed = manifest()
    registry.register(installed)

    with sqlite3.connect(path) as writer:
        writer.execute("BEGIN IMMEDIATE")
        try:
            assert registry.register(installed) == installed
            assert writer.in_transaction
            assert persisted(db) == [installed]
        finally:
            writer.rollback()


def test_existing_version_content_conflict_is_rejected_while_writer_is_held(database):
    db, path = database
    registry = ComponentRegistry(db, clock=lambda: NOW)
    installed = manifest()
    registry.register(installed)

    with sqlite3.connect(path) as writer:
        writer.execute("BEGIN IMMEDIATE")
        try:
            with pytest.raises(ValueError, match="^component_version_reused$"):
                registry.register(manifest(code_hash="c" * 64))
            assert persisted(db) == [installed]
        finally:
            writer.rollback()


def test_missing_component_is_persisted(database):
    db, _path = database
    installed = manifest()

    assert ComponentRegistry(db, clock=lambda: NOW).register(installed) == installed
    assert persisted(db) == [installed]


@pytest.mark.parametrize("same_content", [True, False], ids=["same", "conflict"])
def test_missing_component_rechecks_concurrent_registration_under_writer(database, same_content):
    db, path = database
    requested = manifest()
    winner = requested if same_content else manifest(code_hash="c" * 64)

    def concurrent_registration(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.strip().upper() != "BEGIN IMMEDIATE":
            return
        # Commit a real competing registration after the unlocked lookup and
        # immediately before this call acquires its own SQLite writer.
        with sqlite3.connect(Path(path)) as connection:
            connection.execute(
                "INSERT INTO market_component_versions "
                "(version_id, kind, component_ref, scope_id, created_at, payload_json, payload_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    winner.version_id, winner.kind, winner.component_ref, winner.scope_id,
                    NOW.isoformat(), winner.model_dump_json(), winner.payload_hash,
                ),
            )

    event.listen(db.engine, "before_cursor_execute", concurrent_registration)
    try:
        registry = ComponentRegistry(db, clock=lambda: NOW)
        if same_content:
            assert registry.register(requested) == requested
        else:
            with pytest.raises(ValueError, match="^component_version_reused$"):
                registry.register(requested)
    finally:
        event.remove(db.engine, "before_cursor_execute", concurrent_registration)

    assert persisted(db) == [winner]
