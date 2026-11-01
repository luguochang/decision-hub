from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from apps.hub_api.main import create_app
from packages.contracts_py.decision_hub_contracts.models import ObservationCreate
from packages.kernel.decision_hub_kernel.application.observation_submission import (
    submit_observation,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    EventRecord,
    ObservationRecord,
    ProductCommandRecord,
    RunRecord,
)


def make_app(tmp_path):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'isolated.sqlite3'}")
    return database, create_app(database, source_connectors=[], sources_enabled=False)


def counts(database):
    with database.session() as session:
        return [
            session.query(model).count()
            for model in (EventRecord, ObservationRecord, RunRecord, ProductCommandRecord)
        ]


def test_conflicting_receipt_rolls_back_event_and_run(tmp_path):
    database, app = make_app(tmp_path)
    service = app.state.analyzer
    payload = ObservationCreate(text="Receipt recovery acceptance text")
    original, execute = submit_observation(service, payload, "pc-receipt-1")
    assert execute
    assert counts(database) == [1, 1, 1, 1]
    response = TestClient(app).post(
        "/v1/observations",
        headers={"Idempotency-Key": "pc-receipt-1"},
        json={"text": "Changed intent must not leave a new event"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "observation_idempotency_conflict"
    assert counts(database) == [1, 1, 1, 1]
    assert submit_observation(service, payload, "pc-receipt-1") == (original, False)


def test_content_deduplicated_request_has_its_own_durable_receipt(tmp_path):
    database, app = make_app(tmp_path)
    service = app.state.analyzer
    payload = ObservationCreate(text="One input, independent receipt identities")
    first, _ = submit_observation(service, payload, "original-intent")
    alias, execute = submit_observation(service, payload, "second-intent")
    assert alias == first and not execute
    assert counts(database) == [1, 1, 1, 2]
    # Retry is now bound even when the original Run belongs to another key.
    assert submit_observation(service, payload, "second-intent") == (first, False)


def test_concurrent_retries_dispatch_only_once(tmp_path):
    database, app = make_app(tmp_path)
    payload = ObservationCreate(text="Concurrent identical text")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: submit_observation(app.state.analyzer, payload, "concurrent-key"),
                range(4),
            )
        )
    assert len({row[0]["run_id"] for row in results}) == 1
    assert sum(row[1] for row in results) == 1
    assert counts(database) == [1, 1, 1, 1]


def test_legacy_key_adopts_receipt_but_never_returns_a_research_run(tmp_path):
    database, app = make_app(tmp_path)
    service = app.state.analyzer
    payload = ObservationCreate(text="Legacy input also has an AI Run")
    event_id, _, _ = service.admission.admit(payload)
    legacy, _ = service.runs.create(event_id, "pre-receipts-key")
    research, _ = service.runs.create(event_id, "research-key", strategy_version="research.v1")
    receipt, execute = submit_observation(service, payload, "pre-receipts-key")
    assert receipt["run_id"] == legacy != research
    assert not execute
    alias, execute = submit_observation(service, payload, "alias-key")
    assert alias == receipt and not execute
    assert counts(database) == [1, 1, 2, 2]


def test_existing_key_collision_cannot_admit_new_input(tmp_path):
    database, app = make_app(tmp_path)
    service = app.state.analyzer
    payload = ObservationCreate(text="Already persisted before receipt deployment")
    event_id, _, _ = service.admission.admit(payload)
    service.runs.create(event_id, "old-key")
    response = TestClient(app).post(
        "/v1/observations", headers={"Idempotency-Key": "old-key"}, json={"text": "other"}
    )
    assert response.status_code == 409
    assert counts(database) == [1, 1, 1, 0]
