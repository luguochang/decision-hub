"""Readonly endpoint gates: historical absence, unpublished checks, and damaged originals."""

import hashlib
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.hub_api.research_quality_routes import research_quality_router
from packages.kernel.decision_hub_kernel.application.research_quality_view import (
    ResearchQualityQuery,
    read_published_research_quality_in_session,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRecord,
    RunRoleOutputRecord,
)
from packages.kernel.decision_hub_kernel.persistence.research_quality_records import (
    ResearchQualitySidecarRecord,
)
from packages.kernel.decision_hub_kernel.persistence.run_spot_admission import (
    RunSpotAdmissionRecord,
)

NOW = datetime(2026, 10, 8, tzinfo=UTC)


@pytest.fixture
def database(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'quality-read.sqlite3'}")
    db.create_all()
    with db.session() as session:
        session.add(
            RunRecord(
                run_id="run-old",
                event_id="event-old",
                status="admitted",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            RunRecord(
                run_id="run-new",
                event_id="event-new",
                status="running",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            RunRoleOutputRecord(
                output_id="selection",
                run_id="run-new",
                role="graph_recipe_selection",
                schema_version="research-graph-recipe-selection.v3",
                payload_json="{}",
                payload_hash=hashlib.sha256(b"{}").hexdigest(),
                created_at=NOW,
            )
        )
        # Unpublished and deliberately unusable: the quality reader may not
        # promote this latest sidecar into a publication for an earlier Run.
        session.add(
            ResearchQualitySidecarRecord(
                sidecar_id="unpublished",
                run_id="run-old",
                logical_round=1,
                phase="final",
                source_result_hash="a" * 64,
                projected_result_hash="b" * 64,
                quality_context_hash="c" * 64,
                quality_decision_hash="d" * 64,
                status="sufficient",
                publication_mode="complete",
                payload_json="{}",
                payload_hash=hashlib.sha256(b"{}").hexdigest(),
                created_at=NOW,
            )
        )
    try:
        yield db
    finally:
        db.engine.dispose()


def client(db):
    app = FastAPI()
    app.include_router(research_quality_router(db, clock=lambda: NOW))
    return TestClient(app)


def test_old_run_ignores_newest_unpublished_sidecar_and_new_run_is_pending(database):
    query = ResearchQualityQuery(database)
    old = query.read("run-old", as_of=NOW)
    assert old.status == "unavailable" and old.reason_code == "research_quality_legacy_unavailable"
    assert old.decision is None and old.budget is None
    new = query.read("run-new", as_of=NOW)
    assert (
        new.reason_code == "research_quality_publication_pending" and new.quality_sidecar_id is None
    )
    with client(database) as api:
        assert api.get("/v1/research/runs/run-old/quality").status_code == 200
        assert api.get("/v1/research/runs/missing/quality").status_code == 404


def test_completed_new_run_missing_publication_and_corrupt_old_source_are_errors(database):
    with database.session() as session:
        run = session.get(RunRecord, "run-new")
        assert run is not None
        run.artifact_id = "missing-artifact"
        session.add(
            RunRoleOutputRecord(
                output_id="damaged",
                run_id="run-old",
                role="research_publication_source",
                schema_version="research-publication-source.v2",
                payload_json="{}",
                payload_hash="f" * 64,
                created_at=NOW,
            )
        )
    with client(database) as api:
        assert api.get("/v1/research/runs/run-new/quality").status_code == 409
        assert api.get("/v1/research/runs/run-old/quality").status_code == 409


def test_borrowed_reader_requires_clean_physical_snapshot_and_performs_no_writes(database):
    with database.session() as session:
        with pytest.raises(ValueError, match="clean_read_snapshot"):
            read_published_research_quality_in_session(session, "run-old", as_of=NOW)
        session.connection().exec_driver_sql("BEGIN")
        driver = session.connection().connection.driver_connection
        assert driver is not None
        before = driver.total_changes
        view = read_published_research_quality_in_session(session, "run-old", as_of=NOW)
        assert view.status == "unavailable" and driver.total_changes == before
        session.add(
            RunRecord(
                run_id="dirty", event_id="dirty", status="admitted", created_at=NOW, updated_at=NOW
            )
        )
        with pytest.raises(ValueError, match="clean_read_snapshot"):
            read_published_research_quality_in_session(session, "run-old", as_of=NOW)


def test_missing_selection_cannot_reclassify_spot_run_as_legacy(database):
    with database.session() as session:
        # This deliberately damaged marker is only negative classification,
        # never used as an admission/quality qualification by the reader.
        session.add(
            RunSpotAdmissionRecord(
                run_id="run-old",
                event_id="event-old",
                study_id="study",
                admission_id="approval",
                scope_id="crypto:spot:okx:BTC-USDT:trade:1m",
                proof_hash="a" * 64,
                payload_json="{}",
                payload_hash=hashlib.sha256(b"{}").hexdigest(),
                created_at=NOW,
            )
        )
        session.add(
            RunRoleOutputRecord(
                output_id="relabelled-source",
                run_id="run-old",
                role="research_publication_source",
                schema_version="research-publication-source.v2",
                payload_json='{"schema_version":"research-publication-source.v2",'
                '"run_id":"run-old","event_id":"event-old"}',
                payload_hash="temporary",
                created_at=NOW,
            )
        )
        session.flush()
        row = session.get(RunRoleOutputRecord, "relabelled-source")
        assert row is not None
        row.payload_hash = hashlib.sha256(row.payload_json.encode()).hexdigest()
    with client(database) as api:
        assert api.get("/v1/research/runs/run-old/quality").status_code == 409
