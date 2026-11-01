"""Finite real-manifest fixtures; no fabricated publication or accepted result."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application import (
    development_history_reader as readers,
)
from packages.kernel.decision_hub_kernel.application import (
    development_history_snapshot as snapshots,
)
from packages.kernel.decision_hub_kernel.application.development_history_snapshot import (
    native_history_path,
)
from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
    GraphFinalProjectionRef,
)
from packages.kernel.decision_hub_kernel.application.research_graph_recipe import (
    GraphRecipeManifestV2,
    graph_recipe_manifest_v2,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_json,
    observation_sha,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRecord,
    RunRoleOutputRecord,
)
from packages.kernel.decision_hub_kernel.ports.research_observation import (
    ObservedExecutionRef,
)
from packages.runtime_adapters.development_runtime import (
    DevelopmentRuntime,
    development_runtime_from_environment,
)

REPOSITORY = Path(__file__).resolve().parents[2]
AT = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
RUN_ID = "run_development_history_scope_only"
REFERENCE = GraphFinalProjectionRef(
    row=ObservedExecutionRef(
        output_id="development_history_scope_only_final",
        role="research_graph_final_projection",
        schema_version="research-perpetual-graph-final.v1",
        payload_hash="a" * 64,
        recorded_at=AT,
    )
)
REFERENCE_JSON = REFERENCE.model_dump_json()


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def development_environment(repository: Path, root: Path) -> dict[str, str]:
    values = {
        "DEVELOPMENT_MODE": "1",
        "DEVELOPMENT_ROOT": str(root),
        "DATA_DIR": str(root / "data"),
        "DSH_HOME": str(root / "dsh-home"),
        "CHECKPOINT_PATH": str(root / "data/checkpoints/decision_graph.sqlite3"),
        "PUBLICATION_DATA_DIR": str(
            repository.parent / ".decision-hub-development" / root.name / "publication"
        ),
        "DOCUMENT_BUNDLE_ROOT": str(root / "data/document-bundles"),
        "SOURCE_ACQUISITION_ROOT": str(root / "data/source-acquisition"),
        "SPOT_RESEARCH_ENABLED": "0",
        "ACTION_PRODUCT_ENABLED": "0",
        "ACTION_REVIEW_ENABLED": "0",
        "ACTION_EXECUTION_ENABLED": "0",
        "RESEARCH_LEARNING_ENABLED": "0",
        "DURABLE_SKILL_CALLBACKS_ENABLED": "0",
        "RESEARCH_SKILLS_ENABLED": "0",
    }
    return {"DECISION_HUB_" + key: value for key, value in values.items()}


@dataclass(frozen=True)
class HistoryCase:
    repository: Path
    development: DevelopmentRuntime
    database: Database
    snapshot: snapshots.DevelopmentHistorySnapshot
    manifest: GraphRecipeManifestV2

    @property
    def database_path(self) -> Path:
        return self.development.data_dir / "db/decision_hub.sqlite3"

    def reader(self) -> readers.DevelopmentHistoryReader:
        return readers.prepare_development_history_reader(
            self.repository, self.development, self.database,
        )

    @contextmanager
    def session(self, *, begin: bool = True) -> Iterator[Session]:
        with Session(self.database.engine, autoflush=False) as session:
            connection = session.connection()
            native = connection.connection.driver_connection
            assert isinstance(native, sqlite3.Connection)
            fixture_prior = int(native.execute("PRAGMA query_only").fetchone()[0])
            if begin:
                connection.exec_driver_sql("BEGIN")
            try:
                yield session
            finally:
                # Assertions observe the consumer's restoration first. Then
                # restore this fixture's own entry state before pool reuse.
                if not connection.closed and not connection.invalidated:
                    try:
                        native.execute("PRAGMA query_only=" + str(fixture_prior))
                    except sqlite3.Error:
                        connection.invalidate()


@pytest.fixture(scope="module")
def history_case(tmp_path_factory: pytest.TempPathFactory) -> Generator[HistoryCase]:
    """Clone only today's actual declared bytes, then exercise real DEV capture."""
    manifest = graph_recipe_manifest_v2()
    repository = tmp_path_factory.mktemp("development-history") / "source"
    repository.mkdir()
    for item in (*manifest.source_files, *manifest.schema_files):
        raw = (REPOSITORY / item.path).read_bytes()
        assert sha(raw) == item.payload_hash, "source changed after the common freeze"
        target = repository / item.path
        native_history_path(target).parent.mkdir(parents=True, exist_ok=True)
        native_history_path(target).write_bytes(raw)
    root = repository / "data/development/long-path-control"
    environment = development_environment(repository, root)
    with pytest.MonkeyPatch.context() as patch:
        for name in tuple(os.environ):
            if name.startswith("DECISION_HUB_") and any(part in name for part in (
                "HISTORY_", "HARNESS_", "RETAINED_RECIPE_", "SETTINGS_PRIVATE_",
            )):
                patch.delenv(name, raising=False)
        for name, value in environment.items():
            patch.setenv(name, value)
        development = development_runtime_from_environment(
            repository, environment=environment,
        )
        assert development is not None
        database = Database()
        RunRecord.__table__.create(database.engine)
        RunRoleOutputRecord.__table__.create(database.engine)
        with database.session() as session:
            session.add(RunRecord(
                run_id=RUN_ID,
                event_id="event_history_scope_only",
                status="completed",
                created_at=AT,
                updated_at=AT,
                finished_at=AT,
                artifact_id="artifact_history_scope_only",
            ))
        try:
            snapshot = snapshots.capture_current_development_snapshot(repository, development)
            yield HistoryCase(repository, development, database, snapshot, manifest)
        finally:
            database.engine.dispose()


@contextmanager
def changed_file(path: Path, replacement: bytes | None) -> Iterator[None]:
    """Damage only one temporary snapshot/clone and restore its exact bytes."""
    before = native_history_path(path).read_bytes()
    if replacement is None:
        native_history_path(path).unlink()
    else:
        native_history_path(path).write_bytes(replacement)
    try:
        yield
    finally:
        native_history_path(path).write_bytes(before)


@contextmanager
def changed_run(case: HistoryCase, **values: str | datetime | None) -> Iterator[None]:
    with case.database.session() as session:
        row = session.get(RunRecord, RUN_ID)
        assert row is not None
        before = {key: getattr(row, key) for key in values}
        for key, value in values.items():
            setattr(row, key, value)
    try:
        yield
    finally:
        with case.database.session() as session:
            row = session.get(RunRecord, RUN_ID)
            assert row is not None
            for key, value in before.items():
                setattr(row, key, value)


def original_manifest(case: HistoryCase) -> GraphRecipeManifestV2:
    parsed = GraphRecipeManifestV2.model_validate_json(case.snapshot.manifest_json)
    assert observation_json(parsed.model_dump(mode="json")) == case.snapshot.manifest_json
    assert observation_sha(parsed.model_dump(mode="json")) == case.snapshot.dependency_hash
    return parsed


def private_module_names() -> set[str]:
    import sys

    return {name for name in sys.modules if name.startswith("_decision_hub_graph_history_")}


def scope_only_reference(*, payload_hash: str = "a" * 64) -> str:
    """A real reference model for scope binding, never a graph receipt proof."""
    plain = json.loads(REFERENCE_JSON)
    plain["row"]["payload_hash"] = payload_hash
    return GraphFinalProjectionRef.model_validate_json(observation_json(plain)).model_dump_json()


@contextmanager
def other_database() -> Iterator[Session]:
    """Different physical SQLite DB; never substitute for the bound DEV file."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with Session(engine, autoflush=False) as session:
            yield session
    finally:
        engine.dispose()
