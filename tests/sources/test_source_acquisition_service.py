from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.hub_api.main import create_app
from packages.contracts_py.decision_hub_contracts import EvidenceCandidate, EvidenceRequirement
from packages.contracts_py.decision_hub_contracts.models import SourceAcquisition
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
    research_evidence_content_hash,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.source_acquisition import (
    SourceAcquisitionService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, SourceAcquisitionRecord
from packages.kernel.decision_hub_kernel.ports.sources import SourceManifest
from packages.source_adapters.registry import SourceRegistry


class _FixtureSource:
    manifest = SourceManifest(
        source_id="fixture-browser",
        source_type="official_feed",
        version="fixture.v1",
        authority_level="official",
        allowed_domains=("example.test",),
    )


def _receipt(*, receipt_id: str = "receipt-1") -> SourceAcquisition:
    return SourceAcquisition.model_validate(
        {
            "schema_version": "acquisition-receipt.v1",
            "receipt_id": receipt_id,
            "source_id": "fixture-browser",
            "url": "https://example.test/calendar",
            "access_mode": "playwright",
            "status": "succeeded",
            "observed_at": datetime(2026, 9, 22, tzinfo=UTC).isoformat(),
            "status_code": 200,
            "artifacts": [
                {"kind": "dom", "path": "receipt/dom.html", "content_hash": "a" * 64},
                {"kind": "trace", "path": "receipt/trace.zip", "content_hash": "b" * 64},
            ],
            "error_code": None,
        }
    )


def test_acquisition_receipt_is_durable_and_idempotent(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'acquisition.sqlite3'}")
    database.create_all()
    registry = SourceRegistry()
    registry.register(_FixtureSource())
    service = SourceAcquisitionService(database, registry)

    first = service.ingest(_receipt())
    second = service.ingest(_receipt())

    assert first.created is True
    assert second.created is False
    assert second.receipt_hash == first.receipt_hash
    assert len(service.list()) == 1
    with database.session() as session:
        assert session.query(SourceAcquisitionRecord).count() == 1


def test_failed_receipt_has_no_fabricated_observation_and_is_durable(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'acquisition-failed.sqlite3'}")
    database.create_all()
    registry = SourceRegistry()
    registry.register(_FixtureSource())
    service = SourceAcquisitionService(database, registry)
    started = datetime(2026, 9, 22, tzinfo=UTC)
    completed = started + timedelta(seconds=2)
    failure = _receipt(receipt_id="receipt-failed").model_copy(
        update={
            "status": "failed",
            "artifacts": [],
            "status_code": None,
            "error_code": "crawler_failed:TimeoutError",
            "request_started_at": started,
            "request_completed_at": completed,
            "observed_at": None,
            "received_at": started,  # untrusted client field must be replaced by Hub.
        }
    )

    first = service.ingest(failure)
    second = service.ingest(failure.model_copy(update={"received_at": completed}))

    assert first.created is True
    assert second.created is False
    assert second.receipt_hash == first.receipt_hash
    assert first.receipt.observed_at is None
    assert first.receipt.request_started_at == started
    assert first.receipt.request_completed_at == completed
    assert first.receipt.received_at is not None
    assert first.receipt.received_at > completed
    assert service.list()[0].receipt_id == "receipt-failed"
    with database.session() as session:
        row = session.get(SourceAcquisitionRecord, "receipt-failed")
        assert row is not None
        assert row.observed_at is None
        assert row.received_at is not None


def test_acquisition_timeline_rejects_invented_or_reversed_observation(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'acquisition-time-invalid.sqlite3'}")
    database.create_all()
    registry = SourceRegistry()
    registry.register(_FixtureSource())
    service = SourceAcquisitionService(database, registry)
    start = datetime(2026, 9, 22, tzinfo=UTC)
    with pytest.raises(ValueError, match="acquisition_request_time_invalid"):
        service.ingest(
            _receipt().model_copy(
                update={
                    "request_started_at": start + timedelta(seconds=2),
                    "request_completed_at": start,
                }
            )
        )
    with pytest.raises(ValueError, match="acquisition_observation_time_invalid"):
        service.ingest(
            _receipt().model_copy(
                update={
                    "request_started_at": start + timedelta(seconds=1),
                    "request_completed_at": start + timedelta(seconds=3),
                    "observed_at": start,
                }
            )
        )
    with pytest.raises(ValueError, match="acquisition_succeeded_without_observation"):
        service.ingest(_receipt().model_copy(update={"observed_at": None}))


def test_acquisition_receipt_rejects_conflict_and_untrusted_url(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'acquisition-invalid.sqlite3'}")
    database.create_all()
    registry = SourceRegistry()
    registry.register(_FixtureSource())
    service = SourceAcquisitionService(database, registry)
    service.ingest(_receipt())

    with pytest.raises(ValueError, match="acquisition_receipt_conflict"):
        service.ingest(_receipt().model_copy(update={"status_code": 201}))
    with pytest.raises(ValueError, match="acquisition_url_outside_allowed_domains"):
        service.ingest(
            _receipt(receipt_id="receipt-2").model_copy(update={"url": "https://evil.test/page"})
        )


def test_acquisition_receipt_rejects_artifact_path_traversal(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'acquisition-path.sqlite3'}")
    database.create_all()
    registry = SourceRegistry()
    registry.register(_FixtureSource())
    service = SourceAcquisitionService(database, registry)
    receipt = _receipt().model_copy(
        update={
            "receipt_id": "receipt-3",
            "artifacts": [
                {
                    "kind": "dom",
                    "path": "../secrets.html",
                    "content_hash": "c" * 64,
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="acquisition_artifact_path_invalid"):
        service.ingest(receipt)


def test_acquisition_receipt_api_exposes_durable_readback(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'acquisition-api.sqlite3'}")
    app = create_app(database=database, source_connectors=[_FixtureSource()])
    with TestClient(app) as client:
        response = client.post("/v1/source-acquisitions", json=_receipt().model_dump(mode="json"))
        assert response.status_code == 202
        assert response.json()["created"] is True
        listed = client.get("/v1/source-acquisitions").json()
        assert len(listed) == 1
        assert listed[0]["receipt_id"] == "receipt-1"


def test_evidence_admission_requires_a_successful_same_source_receipt(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'acquisition-evidence.sqlite3'}")
    database.create_all()
    registry = SourceRegistry()
    registry.register(_FixtureSource())
    receipt = SourceAcquisitionService(database, registry).ingest(_receipt()).receipt
    run_id, _ = RunService(
        database,
        clock=lambda: datetime(2026, 9, 22, tzinfo=UTC),
    ).create("event-acquisition-evidence")
    observed = datetime(2026, 9, 22, tzinfo=UTC)
    excerpt = "The source page was captured with a durable browser receipt."
    content_hash = research_evidence_content_hash(
        requirement_id="browser_fact",
        kind="official",
        authority="official",
        source_id="fixture-browser",
        source_url="https://example.test/calendar",
        published_at=None,
        excerpt=excerpt,
        structured_payload_ref=None,
    )
    candidate = EvidenceCandidate(
        evidence_id="ev-browser-receipt",
        requirement_id="browser_fact",
        kind="official",
        authority="official",
        source_id="fixture-browser",
        source_url="https://example.test/calendar",
        published_at=None,
        observed_at=observed,
        received_at=observed,
        content_hash=content_hash,
        excerpt=excerpt,
        content_ref=None,
        acquisition_receipt_id=receipt.receipt_id,
        source_content_hash=None,
        parser_ref=None,
        structured_payload_ref=None,
        tool_call_id=None,
        research_session_id="session-browser-receipt",
        round=1,
        quality="candidate",
        freshness_status="unknown",
        conflict_group=None,
    )
    requirement = EvidenceRequirement(
        requirement_id="browser_fact",
        description="A browser sourced official fact.",
        importance="hard",
        source_priority=["official"],
        authority_floor="official",
        preferred_capabilities=["web.fetch"],
        freshness_seconds=86_400,
        minimum_independent_sources=1,
        allowed_fallbacks=[],
        confidence_cap=0.5,
    )
    admitted = ResearchEvidenceService(database).accept_candidates(
        run_id=run_id,
        capability_id="web.fetch",
        candidates=[candidate],
        requirements={"browser_fact": requirement},
        cutoff_at=observed,
    )
    assert admitted[0].acquisition_receipt_id == receipt.receipt_id

    missing_receipt = candidate.model_copy(
        update={"evidence_id": "ev-missing-receipt", "acquisition_receipt_id": "missing"}
    )
    with pytest.raises(ValueError, match="acquisition_receipt_not_found"):
        ResearchEvidenceService(database).accept_candidates(
            run_id=run_id,
            capability_id="web.fetch",
            candidates=[missing_receipt],
            requirements={"browser_fact": requirement},
            cutoff_at=observed,
        )
