from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapper, Session

from packages.contracts_py.decision_hub_contracts import ObservationCreate
from packages.kernel.decision_hub_kernel.persistence.db import (
    Base,
    Database,
    EventRecord,
    ObservationRecord,
    ProductCommandRecord,
    RunRecord,
)


@dataclass
class Intake:
    app: FastAPI
    database: Database
    client: TestClient


@pytest.fixture
def intake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Intake]:
    # Main's module-level app must also stay in this disposable directory.
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "import-data"))
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    monkeypatch.setenv("DECISION_HUB_DOCUMENT_CORRECTION_LLM_ENABLED", "0")
    from apps.hub_api.main import create_app
    from packages.runtime_adapters.langgraph_agent.runtime import LangGraphAgentRuntime

    calls = []

    async def forbidden_execution(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))
        raise AssertionError("Research intake must not call a runtime/Provider")

    monkeypatch.setattr(LangGraphAgentRuntime, "execute", forbidden_execution)
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'intake.sqlite3'}")
    app = create_app(database, source_connectors=[], sources_enabled=False)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield Intake(app=app, database=database, client=client)
        assert calls == []
    finally:
        client.close()
        database.engine.dispose()


def payload(**changes: object) -> dict[str, Any]:
    return {
        "text": "当前 BTC 价格和成交量：原关注点。",
        "source_id": "decision-desk-latest-advice",
        "source_type": "manual",
        "language": "zh",
        "event_hint": "crypto_market_snapshot",
        **changes,
    }


def rows(database: Database) -> dict[str, str]:
    names = (
        "events", "observations", "runs", "product_commands", "occurrences",
        "event_occurrences", "studies", "study_occurrences",
    )
    with database.session() as session:
        return {
            name: repr(sorted(
                (dict(row) for row in session.execute(
                    select(Base.metadata.tables[name])
                ).mappings()),
                key=repr,
            ))
            for name in names
        }


def post(intake: Intake, body: dict[str, Any], key: str = "research-submit:original"):
    return intake.client.post(
        "/v1/research/observations", headers={"Idempotency-Key": key}, json=body
    )


@pytest.mark.parametrize("changes", [
    {"text": "当前 BTC 价格和成交量：另一个关注点。"},
    {"language": "en"},
    {"source_type": "web"},
], ids=["changed-text-no-orphan", "changed-language", "changed-source-type"])
def test_changed_original_intent_is_409_without_new_rows(
    intake: Intake, changes: dict[str, Any],
) -> None:
    first = post(intake, payload())
    assert first.status_code == 202, first.text
    before = rows(intake.database)
    changed = post(intake, payload(**changes))
    assert changed.status_code == 409, {
        "status": changed.status_code, "body": changed.text,
        "rows_unchanged": rows(intake.database) == before,
    }
    assert changed.json()["detail"] == "research_submission_intent_conflict"
    assert rows(intake.database) == before


@pytest.mark.parametrize("changes", [
    {"published_at": "2026-09-29T09:00:00Z"},
    {"observed_at": "2026-09-29T09:01:00Z"},
    {"source_url": "https://example.test/original"},
    {"source_id": "other-manual-entry"},
    {"event_hint": "policy_rate_decision"},
    {"text": "给我最新操作建议"},
])
def test_other_original_fields_are_bound(intake: Intake, changes: dict[str, Any]) -> None:
    assert post(intake, payload()).status_code == 202
    before = rows(intake.database)
    response = post(intake, payload(**changes))
    assert response.status_code == 409
    assert response.json()["detail"] == "research_submission_intent_conflict"
    assert rows(intake.database) == before


def test_concurrent_same_key_returns_one_atomic_admission(intake: Intake) -> None:
    barrier = Barrier(4)

    def submit_once(_: int) -> dict[str, Any]:
        client = TestClient(intake.app, raise_server_exceptions=False)
        try:
            barrier.wait(timeout=5)
            response = client.post(
                "/v1/research/observations",
                headers={"Idempotency-Key": "research-submit:concurrent"}, json=payload(),
            )
            assert response.status_code == 202, response.text
            return response.json()
        finally:
            client.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(submit_once, range(4)))
    assert all(reply == replies[0] for reply in replies)
    with intake.database.session() as session:
        assert [session.query(model).count() for model in (
            EventRecord, ObservationRecord, RunRecord, ProductCommandRecord,
        )] == [1, 1, 1, 1]
        command = session.query(ProductCommandRecord).one()
        saved = json.loads(command.result_json)
        assert saved["original_input"] == ObservationCreate(**payload()).model_dump(mode="json")
        assert saved["result"] == replies[0]
        assert saved["normalized_input"]["text"] != payload()["text"]


def test_retry_uses_first_receipt_without_today_normalizer_or_run_status(
    intake: Intake, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = post(intake, payload()).json()
    with intake.database.session() as session:
        run = session.get(RunRecord, first["run_id"])
        assert run is not None
        run.status = "cancelled"
    before = rows(intake.database)
    from packages.kernel.decision_hub_kernel.application import research_submission

    def forbidden_normalizer(_payload: ObservationCreate) -> ObservationCreate:
        raise AssertionError("Receipt replay must never normalize the input again")

    monkeypatch.setattr(
        research_submission, "normalize_latest_advice_request", forbidden_normalizer
    )
    response = post(intake, payload())
    assert response.status_code == 202
    assert response.json() == first
    assert response.json()["status"] == "queued"
    assert rows(intake.database) == before


@pytest.mark.parametrize("damage", ["invalid-json", "missing-field", "original", "normalized"])
def test_corrupt_internal_receipt_is_rejected_without_replacement(
    intake: Intake, damage: str,
) -> None:
    assert post(intake, payload()).status_code == 202
    with intake.database.session() as session:
        command = session.query(ProductCommandRecord).one()
        saved = json.loads(command.result_json)
        if damage == "invalid-json":
            command.result_json = "{"
        else:
            if damage == "missing-field":
                del saved["normalized_input"]
            else:
                saved[f"{damage}_input"]["text"] += " altered"
            command.result_json = json.dumps(saved)
    before = rows(intake.database)
    response = post(intake, payload())
    assert response.status_code == 409
    assert response.json()["detail"] == "research_submission_receipt_corrupt"
    assert rows(intake.database) == before


@pytest.mark.parametrize("field", ["event_id", "idempotency_key", "strategy_version"])
def test_receipt_checks_original_run_identity(intake: Intake, field: str) -> None:
    first = post(intake, payload()).json()
    second = post(intake, payload(text="不同 BTC 关注点"), "research-submit:other").json()
    with intake.database.session() as session:
        run = session.get(RunRecord, first["run_id"])
        assert run is not None
        setattr(run, field, {
            "event_id": second["event_id"], "idempotency_key": "altered-run-key",
            "strategy_version": "baseline.v1",
        }[field])
    before = rows(intake.database)
    response = post(intake, payload())
    assert response.status_code == 409
    assert response.json()["detail"] == "research_submission_receipt_corrupt"
    assert rows(intake.database) == before


def test_resigned_wrong_child_receipt_cannot_pass_binding(intake: Intake) -> None:
    assert post(intake, payload()).status_code == 202
    second = post(intake, payload(text="不同 BTC 关注点"), "research-submit:other").json()
    with intake.database.session() as session:
        command = session.get(ProductCommandRecord, "research-submit:original")
        assert command is not None
        saved = json.loads(command.result_json)
        saved["result"] = second
        command.target_id = second["run_id"]
        del saved["receipt_hash"]
        canonical = json.dumps(saved, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        saved["receipt_hash"] = hashlib.sha256(canonical.encode()).hexdigest()
        command.result_json = json.dumps(saved)
    before = rows(intake.database)
    response = post(intake, payload())
    assert response.status_code == 409
    assert response.json()["detail"] == "research_submission_receipt_corrupt"
    assert rows(intake.database) == before


def test_legacy_key_without_receipt_is_not_adopted_and_remains_readable(intake: Intake) -> None:
    from packages.kernel.decision_hub_kernel.application.research_intake import (
        normalize_latest_advice_request,
    )

    _event, run_id, created = intake.app.state.analyzer.queue_research(
        normalize_latest_advice_request(ObservationCreate(**payload())), "research-submit:original"
    )
    assert created
    before = rows(intake.database)
    for body in (payload(), payload(language="en")):
        response = post(intake, body)
        assert response.status_code == 409
        assert response.json()["detail"] == "research_submission_legacy_binding_unavailable"
        assert rows(intake.database) == before
    assert intake.client.get(f"/v1/runs/{run_id}").status_code == 200
    assert intake.client.get(f"/v1/research/runs/{run_id}").status_code == 200
    assert rows(intake.database) == before


def test_existing_other_entry_command_cannot_be_reused(intake: Intake) -> None:
    assert post(intake, payload()).status_code == 202
    with intake.database.session() as session:
        command = session.query(ProductCommandRecord).one()
        command.kind = "legacy_observation"
        command.owner_id = "legacy-observation-api"
    before = rows(intake.database)
    response = post(intake, payload())
    assert response.status_code == 409
    assert response.json()["detail"] == "research_submission_intent_conflict"
    assert rows(intake.database) == before


def test_disabled_study_scope_rolls_back_entire_admission(
    intake: Intake, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from packages.kernel.decision_hub_kernel.application import run as run_module
    from packages.kernel.decision_hub_kernel.persistence.db import StudyRecord

    original_study_for_event = run_module.study_for_event

    def disabled_scope(session: Session, event_id: str, now: datetime) -> str:
        study_id = original_study_for_event(session, event_id, now)
        study = session.get(StudyRecord, study_id)
        assert study is not None
        study.market, study.instrument = "metal", "XAU-USD"
        return study_id

    monkeypatch.setattr(run_module, "study_for_event", disabled_scope)
    before = rows(intake.database)
    response = post(intake, payload())
    assert response.status_code == 400
    assert response.json()["detail"] == "study_scope_not_enabled"
    assert rows(intake.database) == before


def test_receipt_insert_failure_rolls_back_event_occurrence_study_and_run(intake: Intake) -> None:
    def fail_receipt_insert(
        mapper: Mapper[ProductCommandRecord], connection: Connection, target: ProductCommandRecord,
    ) -> None:
        raise RuntimeError("isolated_receipt_insert_failure")

    before = rows(intake.database)
    event.listen(ProductCommandRecord, "before_insert", fail_receipt_insert)
    try:
        response = post(intake, payload())
    finally:
        event.remove(ProductCommandRecord, "before_insert", fail_receipt_insert)
    assert response.status_code == 500
    assert rows(intake.database) == before


@pytest.mark.parametrize("key", ["", " ", " padded ", "x" * 129])
def test_invalid_key_is_400_without_admission(intake: Intake, key: str) -> None:
    before = rows(intake.database)
    assert post(intake, payload(), key).status_code == 400
    assert rows(intake.database) == before


def test_different_raw_templates_conflict_even_when_normalized_text_is_identical(
    intake: Intake,
) -> None:
    assert post(intake, payload(text="获取最新操作建议")).status_code == 202
    before = rows(intake.database)
    response = post(intake, payload(text="给我最新操作建议"))
    assert response.status_code == 409
    assert response.json()["detail"] == "research_submission_intent_conflict"
    assert rows(intake.database) == before


@pytest.mark.parametrize("field,value", [
    ("text", "altered durable observation"), ("source_id", "altered-source"),
    ("source_type", "web"), ("language", "en"),
    ("published_at", datetime(2026, 1, 1, tzinfo=UTC)),
    ("observed_at", datetime(2026, 1, 1, tzinfo=UTC)),
    ("received_at", datetime(2026, 1, 1, tzinfo=UTC)),
    ("source_url", "https://example.test/altered"),
    ("event_hint", "policy_rate_decision"),
    ("scheduled_at", datetime(2026, 1, 1, tzinfo=UTC)),
    ("revision_of", "obs-altered"), ("content_hash", "a" * 64),
])
def test_receipt_checks_actual_admitted_observation_fields(
    intake: Intake, field: str, value: object,
) -> None:
    assert post(intake, payload()).status_code == 202
    with intake.database.session() as session:
        observation = session.query(ObservationRecord).one()
        setattr(observation, field, value)
    before = rows(intake.database)
    response = post(intake, payload())
    assert response.status_code == 409
    assert response.json()["detail"] == "research_submission_receipt_corrupt"
    assert rows(intake.database) == before


@pytest.mark.parametrize("field", ["event_type", "occurred_at", "received_at"])
def test_receipt_checks_actual_admitted_event_fields(intake: Intake, field: str) -> None:
    assert post(intake, payload()).status_code == 202
    with intake.database.session() as session:
        original = session.query(EventRecord).one()
        setattr(original, field, "altered_event" if field == "event_type"
                else getattr(original, field) + timedelta(seconds=5))
    before = rows(intake.database)
    response = post(intake, payload())
    assert response.status_code == 409
    assert response.json()["detail"] == "research_submission_receipt_corrupt"
    assert rows(intake.database) == before


def test_content_dedup_freezes_real_admission_without_fabricating_new_metadata(
    intake: Intake,
) -> None:
    first_body = payload(text="FOMC original statement", source_id="original-official-source",
                         event_hint="policy_rate_decision")
    first = post(intake, first_body).json()
    second_body = {**first_body, "source_id": "second-content-source", "language": "en"}
    second_response = post(intake, second_body, "research-submit:content-alias")
    assert second_response.status_code == 202
    second = second_response.json()
    assert second["event_id"] == first["event_id"]
    assert second["run_id"] != first["run_id"]
    with intake.database.session() as session:
        assert session.query(ObservationRecord).count() == 1
        command = session.get(ProductCommandRecord, "research-submit:content-alias")
        assert command is not None
        saved = json.loads(command.result_json)
        assert saved["normalized_input"]["source_id"] == "second-content-source"
        assert saved["normalized_input"]["language"] == "en"
        assert saved["admitted_observation"]["source_id"] == "original-official-source"
        assert saved["admitted_observation"]["language"] == "zh"
    before = rows(intake.database)
    replay = post(intake, second_body, "research-submit:content-alias")
    assert replay.status_code == 202 and replay.json() == second
    assert rows(intake.database) == before
