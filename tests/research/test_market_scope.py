from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from apps.hub_api.main import create_app
from apps.hub_worker.research import DurableResearchWorker, ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import ObservationCreate
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.market_scope import (
    outcome_scope_enabled,
    research_market_catalog,
    research_scope,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.study import ensure_study
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRecord, StudyRecord
from tests.evolution.test_research_worker import FakeResearchRuntime

NOW = datetime(2026, 9, 30, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]


def setup(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'scopes.sqlite3'}")
    db.create_all()
    event, _, _ = AdmissionService(db).admit(
        ObservationCreate(text="Fed statement", source_id="manual")
    )
    studies = {}
    with db.session() as session:
        for market, instrument in [("crypto", "BTC-USDT-SWAP"), ("metal", "XAU-USD")]:
            studies[market] = ensure_study(
                session,
                question_key="same-fed-event",
                title="Fed",
                market=market,
                instrument=instrument,
                now=NOW,
            )
    return db, event, studies


def test_catalog_is_explicit_and_unavailable_markets_cannot_inherit_btc_defaults(tmp_path):
    db, _, _ = setup(tmp_path)
    with TestClient(create_app(db, sources_enabled=False)) as client:
        response = client.get("/v1/product/research-scopes")
        assert response.status_code == 200
        catalog_items = {item["scope_id"]: item for item in response.json()["items"]}
        btc, gold = catalog_items["btc-macro.v1"], catalog_items["gold-macro.v1"]
    assert btc["research_enabled"] and btc["domain_pack_ref"] == "crypto_macro.v1"
    assert not gold["research_enabled"] and not gold["outcome_enabled"]
    assert gold["price_unit"] == "USD/troy_ounce"
    assert "gold_provider_not_verified" in gold["blocking_reasons"]
    for instrument in ["XAUUSD", "GC", "GLD", "PAXG", "BTC-USDT"]:
        assert research_scope("metal", instrument) is None
    assert not outcome_scope_enabled("metal", "XAU-USD")
    assert not outcome_scope_enabled("metal", "BTC-USDT")
    assert outcome_scope_enabled("crypto", "BTC-USDT")
    catalog = research_market_catalog()
    catalog.items[1].research_enabled = True
    assert not research_scope("metal", "XAU-USD").research_enabled


@pytest.mark.parametrize("origin", ["manual", "automatic", "scheduled_recheck"])
def test_gold_run_admission_is_blocked_before_any_btc_execution(tmp_path, origin):
    db, event, studies = setup(tmp_path)
    with pytest.raises(ValueError, match="study_scope_not_enabled"):
        RunService(db).create(
            event,
            "gold",
            strategy_version="research.v1",
            study_id=studies["metal"],
            admission_origin=origin,
        )
    with db.session() as session:
        assert session.query(RunRecord).count() == 0
        assert studies["crypto"] != studies["metal"]


def test_idempotency_and_parent_cannot_cross_market_studies(tmp_path):
    db, event, studies = setup(tmp_path)
    service = RunService(db)
    run, _ = service.create(
        event, "request", strategy_version="research.v1", study_id=studies["crypto"]
    )
    assert service.create(
        event, "request", strategy_version="research.v1", study_id=studies["crypto"]
    ) == (run, False)
    with pytest.raises(ValueError, match="run_idempotency_scope_mismatch"):
        service.create(event, "request", strategy_version="research.v1", study_id=studies["metal"])
    with pytest.raises(ValueError, match="parent_study_mismatch"):
        service.create(event, "child", parent_run_id=run, study_id=studies["metal"])


def test_worker_rechecks_scope_of_existing_queue_and_does_not_freeze_wrong_market(tmp_path):
    db, event, studies = setup(tmp_path)
    run, _ = RunService(db).create(
        event, "btc", strategy_version="research.v1", study_id=studies["crypto"]
    )
    with db.session() as session:
        session.get(RunRecord, run).study_id = studies["metal"]
    factory = ResearchRequestFactory(db, pack_root=ROOT / "packs/crypto_macro")
    with pytest.raises(ValueError, match="study_scope_not_enabled"):
        factory.build(event, run)
    with db.session() as session:
        # The legacy/tampered task is rejected before invoking snapshot or provider work.
        assert session.get(RunRecord, run).snapshot_id is None


def test_unknown_horizon_cannot_be_enabled_by_an_explicit_btc_study(tmp_path):
    db, event, studies = setup(tmp_path)
    with db.session() as session:
        session.get(StudyRecord, studies["crypto"]).horizon_scope = "30m,1y"
    with pytest.raises(ValueError, match="research_scope_horizon_not_enabled"):
        RunService(db).create(
            event, "bad-horizon", strategy_version="research.v1", study_id=studies["crypto"]
        )


async def test_checkpoint_recovery_revalidates_market_before_resuming_model(tmp_path):
    db, event, studies = setup(tmp_path)
    run, _ = RunService(db).create(
        event, "old", strategy_version="research.v1", study_id=studies["crypto"]
    )
    with db.session() as session:
        row = session.get(RunRecord, run)
        row.study_id, row.status = studies["metal"], "running"
        row.lease_owner, row.lease_expires_at = "old-worker", NOW - timedelta(seconds=1)
    worker = DurableResearchWorker(
        db, FakeResearchRuntime(), pack_root=ROOT / "packs/crypto_macro", clock=lambda: NOW
    )
    worker.executor.has_checkpoint = AsyncMock(return_value=True)
    worker.executor.resume = AsyncMock()
    result = await worker.tick()
    assert result.error_code == "study_scope_not_enabled"
    worker.executor.resume.assert_not_called()
    with db.session() as session:
        assert session.get(RunRecord, run).status == "failed"
