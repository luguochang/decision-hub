"""New automatic memory/API path and actual upgrade function, private SQLite only."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import socket
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, inspect, select, text
from sqlalchemy.engine import Engine

from apps.hub_api.market_memory_routes import market_memory_router
from apps.hub_worker.market_memory import MarketMemoryTick
from packages.kernel.decision_hub_kernel.application.market_case import MarketCaseService
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalObservationAttemptRecord,
    AnalyticalObservationRecord,
    AnalyticalOutlookRecord,
    Base,
    Database,
    FactVersionRecord,
    OutboxRecord,
    RawCaptureRecord,
    ResearchResultRecord,
    RunRoleOutputRecord,
    StudyRecord,
    as_utc,
)
from packages.kernel.decision_hub_kernel.persistence.market_case_records import MarketCaseRecord
from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from tests.market_risk.test_market_case_components import NOW, seed_case

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def private_only(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "bootstrap"))

    def only_private(_dialect, _record, args, _kwargs):
        database = Path(str(args[0]).removeprefix("file:").split("?")[0]).resolve()
        if not database.is_relative_to(tmp_path.resolve()):
            raise AssertionError("SQL must remain in this private test namespace")

    def no_network(*_args, **_kwargs):
        raise AssertionError("external network is forbidden")

    event.listen(Engine, "do_connect", only_private)
    monkeypatch.setattr(socket, "create_connection", no_network)
    try:
        yield
    finally:
        event.remove(Engine, "do_connect", only_private)


@pytest.fixture
def database(tmp_path):
    value = Database(f"sqlite+pysqlite:///{tmp_path / 'memory.sqlite3'}")
    value.create_all()
    try:
        yield value
    finally:
        value.engine.dispose()


def seed(database, *, identity="one", instrument="BTC-USDT", observed=False):
    seed_case(database, identity=identity)
    with database.session() as session:
        session.get(StudyRecord, "study-" + identity).instrument = instrument
        session.get(AnalyticalOutlookRecord, "out-" + identity).instrument = instrument
        session.get(FactVersionRecord, "fv-" + identity).scope_instrument = instrument
        if not observed:
            session.delete(session.get(AnalyticalObservationRecord, "out-" + identity))


def mature_label(database, archive, identity, evaluated_at):
    """Synthetic archived closed candles, consumed by the real label verifier."""
    with database.session() as session:
        outlook = session.get(AnalyticalOutlookRecord, "out-" + identity)
        window = {}
        for side, boundary, price in (
            ("entry", as_utc(outlook.window_start_at), 100.0),
            ("exit", as_utc(outlook.window_end_at), 102.0),
        ):
            body = json.dumps({"code": "0", "data": [[
                str(int((boundary - timedelta(minutes=1)).timestamp() * 1000)),
                "100", "102", "99", str(price), "1", "1", "1", "1",
            ]]}).encode()
            digest, ref = archive.put(body)
            session.add(RawCaptureRecord(
                capture_id=side + "-" + identity,
                context_id=f"analytical-observation:{outlook.outlook_id}:1",
                url=(f"https://www.okx.com/api/v5/market/history-candles?instId={outlook.instrument}"
                     f"&bar=1m&after={int(boundary.timestamp() * 1000)}"),
                status_code=200, body_hash=digest, body_ref=ref, byte_count=len(body),
                media_type="application/json", adapter_version="synthetic-memory-label-control",
                started_at=evaluated_at, completed_at=evaluated_at, recorded_at=evaluated_at,
            ))
            window[side] = {"instrument": outlook.instrument, "last": price,
                            "observed_at": boundary.isoformat(),
                            "received_at": evaluated_at.isoformat()}
        market_return = (102 / 100 - 1) * 100
        session.add(AnalyticalObservationRecord(
            outlook_id=outlook.outlook_id, status="completed", attempt=1,
            next_attempt_at=evaluated_at, evaluated_at=evaluated_at, benchmark="close_1m",
            quality_status="estimated", market_return_pct=market_return,
            directional_return_pct=market_return, direction_correct=1,
            window_json=json.dumps(window),
        ))
        session.add(AnalyticalObservationAttemptRecord(
            outlook_id=outlook.outlook_id, attempt=1, status="completed",
            started_at=evaluated_at, finished_at=evaluated_at,
        ))


def test_initial_restart_and_actual_later_mature_label_append_without_rewriting(database, tmp_path):
    seed(database)
    now = [NOW]
    archive = RawBodyArchive(tmp_path / "raw-captures")
    tick = MarketMemoryTick(database, archive, clock=lambda: now[0])
    assert tick.process() == 1
    with database.session() as session:
        original = session.scalar(select(MarketCaseRecord))
        original_body, original_hash = original.payload_json, original.payload_hash
        initial = tick.cases._decode(original)
    assert not initial.eligible and initial.label_known_at is None
    assert tick.process() == 0
    assert MarketMemoryTick(database, archive, clock=lambda: now[0]).process() == 0
    now[0] += timedelta(minutes=1)
    mature_label(database, archive, "one", now[0])
    now[0] += timedelta(seconds=1)
    assert tick.process() == 1 and tick.process() == 0
    with database.session() as session:
        rows = session.scalars(select(MarketCaseRecord).order_by(MarketCaseRecord.created_at)).all()
        assert len(rows) == 2
        assert (rows[0].payload_json, rows[0].payload_hash) == (original_body, original_hash)
        mature = tick.cases._decode(rows[1])
    assert mature.assessment["label_status"] == "verified"
    assert mature.label_known_at == now[0] and mature.direction_correct is True
    assert mature.label_capture_ids == ("entry-one", "exit-one")
    assert not mature.eligible  # No qualified Graph/method/input in this diagnostic fixture.
    assert mature.request_id != initial.request_id and mature.available_at > initial.available_at


def test_poison_original_failure_is_preserved_once_and_other_asset_is_not_blocked(database, tmp_path):
    seed(database, identity="poison")
    seed(database, identity="eth", instrument="ETH-USDT")
    with database.session() as session:
        session.add(ResearchResultRecord(
            run_id="run-poison", schema_version="research.v1", payload_json="{}",
            payload_hash="f" * 64, created_at=NOW - timedelta(days=1),
        ))
    tick = MarketMemoryTick(database, RawBodyArchive(tmp_path / "raw-captures"), clock=lambda: NOW)
    assert tick.process(limit=4) == 1
    assert tick.process(limit=4) == 0
    with database.session() as session:
        records = session.scalars(select(MarketCaseRecord)).all()
        failures = session.scalars(select(RunRoleOutputRecord)).all()
        assert len(records) == len(failures) == 1
        assert records[0].instrument == "ETH-USDT"
        body = json.loads(failures[0].payload_json)
        assert body["outlook_id"] == "out-poison"
        assert body["error_code"] == "market_case_result_hash_invalid"
        assert failures[0].payload_hash == hashlib.sha256(failures[0].payload_json.encode()).hexdigest()
        assert session.get(ResearchResultRecord, "run-poison").payload_hash == "f" * 64


def test_retry_and_future_observations_cannot_create_current_mature_versions(database, tmp_path):
    seed(database)
    now = [NOW]
    tick = MarketMemoryTick(database, RawBodyArchive(tmp_path / "raw-captures"), clock=lambda: now[0])
    assert tick.process() == 1
    now[0] += timedelta(minutes=1)
    with database.session() as session:
        session.add(AnalyticalObservationRecord(
            outlook_id="out-one", status="retry", attempt=1, next_attempt_at=now[0],
            evaluated_at=now[0], error_code="market_unavailable",
        ))
    assert tick.process() == 0
    with database.session() as session:
        row = session.get(AnalyticalObservationRecord, "out-one")
        row.status, row.evaluated_at = "completed", now[0] + timedelta(days=1)
    assert tick.process() == 0
    with database.session() as session:
        assert len(session.scalars(select(MarketCaseRecord)).all()) == 1


def test_actual_api_scope_time_pagination_hash_and_error_contracts_are_readonly(database, tmp_path):
    for identity, instrument in (("btc-a", "BTC-USDT"), ("btc-b", "BTC-USDT"), ("eth", "ETH-USDT")):
        seed(database, identity=identity, instrument=instrument)
    service = MarketCaseService(database, RawBodyArchive(tmp_path / "raw-captures"), clock=lambda: NOW)
    stored = {}
    for identity in ("btc-a", "btc-b", "eth"):
        stored[identity] = service.capture(identity, revision_id="rev-" + identity, outlook_id="out-" + identity)
    seed(database, identity="future")
    with database.session() as session:
        session.get(FactVersionRecord, "fv-future").known_at = NOW + timedelta(days=1)
    future = service.capture("future", revision_id="rev-future", outlook_id="out-future")
    app = FastAPI()
    app.include_router(market_memory_router(database, tmp_path, clock=lambda: NOW))
    actions = []

    def observe(_connection, _cursor, statement, *_rest):
        actions.append(statement.lstrip().split()[0].upper())

    event.listen(database.engine, "before_cursor_execute", observe)
    try:
        with TestClient(app) as client:
            btc_path = "/v1/risk/scopes/" + spot_scope("BTC-USDT").scope_id + "/cases"
            eth_path = "/v1/risk/scopes/" + spot_scope("ETH-USDT").scope_id + "/cases"
            first = client.get(btc_path, params={"limit": 1}).json()
            second = client.get(btc_path, params={"limit": 1, "offset": 1}).json()
            assert first["has_more"] is True and second["has_more"] is False
            assert first["items"][0]["case_id"] != second["items"][0]["case_id"]
            assert all(x["instrument"] == "BTC-USDT" for x in first["items"] + second["items"])
            assert len(client.get(eth_path).json()["items"]) == 1
            original = client.get("/v1/risk/cases/" + stored["btc-a"].case_id)
            assert original.status_code == 200 and original.json() == stored["btc-a"].model_dump(mode="json")
            assert client.get("/v1/risk/cases/" + future.case_id).status_code == 409
            assert client.get("/v1/risk/scopes/unknown/cases").status_code == 404
            assert client.get("/v1/risk/cases/not-found").status_code == 404
            assert client.get(btc_path, params={"limit": 0}).status_code == 422
            assert client.get(btc_path, params={"offset": -1}).status_code == 422
            assert actions and set(actions) == {"SELECT"}
    finally:
        event.remove(database.engine, "before_cursor_execute", observe)
    with database.session() as session:
        session.get(MarketCaseRecord, stored["btc-a"].case_id).payload_hash = "0" * 64
    with TestClient(app) as client:
        assert client.get("/v1/risk/cases/" + stored["btc-a"].case_id).status_code == 409
        assert client.get(btc_path).status_code == 409


def test_actual_upgrade_function_0076_to_0080_keeps_all_old_rows_and_all_orm(database, tmp_path):
    # This test creates a separate empty private migration database; it never
    # invokes main(), probe/apply or the named product DB branch.
    tool_path = ROOT / "tools/acceptance/upgrade_market_risk_schema.py"
    spec = importlib.util.spec_from_file_location("private_market_schema_upgrade", tool_path)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    path = tmp_path / "upgrade.sqlite3"
    old = Database(f"sqlite+pysqlite:///{path}")
    config = old._alembic_config()
    config.attributes["configure_logger"] = False
    command.upgrade(config, "0076_legacy_index_reconciliation")
    with old.session() as session:
        session.add(OutboxRecord(
            artifact_id="legacy-original", subject_id="legacy-original", subject_kind="report",
            channel="local", dedupe_key="original-sent", attempts=3, sent_at=NOW,
            dispatch_status="delivered", provider_message_id="original-provider-id",
            claim_generation=2, attempt_history_json='[{"original":true}]',
        ))
    with old.engine.connect() as connection:
        old_outbox = [tuple(row) for row in connection.execute(text("SELECT * FROM outbox"))]
        old_tables = set(inspect(connection).get_table_names())
    old.engine.dispose()
    with tool.read_database(path) as reader:
        original = tool.snapshot(reader)
    inputs = tool.migration_inputs()
    result = tool.upgrade(path, "0080_risk_research_requests", tmp_path, original, inputs,
                          expected_head="0076_legacy_index_reconciliation")
    actual = Database(f"sqlite+pysqlite:///{path}")
    try:
        with actual.engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0080_risk_research_requests"
            assert [tuple(row) for row in connection.execute(text("SELECT * FROM outbox"))] == old_outbox
            inspector = inspect(connection)
            new_tables = set(inspector.get_table_names()) - old_tables
            assert len(new_tables) == 18
            for name, table in Base.metadata.tables.items():
                assert set(column["name"] for column in inspector.get_columns(name)) == set(table.columns.keys())
                assert {index.name for index in table.indexes} <= {index["name"] for index in inspector.get_indexes(name)}
        with tool.read_database(path) as reader:
            assert tool.snapshot(reader, original) == original
            assert tool.checks(reader)["integrity"] == ["ok"]
        assert result["physical_atomic_migration"] is True
        assert result["migration_chain"] == ["0080_risk_research_requests", "0079_market_case_and_components",
                                             "0078_path_forecast_ledger", "0077_run_spot_admissions"]
    finally:
        actual.engine.dispose()
