"""One isolated owner follow-up and the authorization boundaries it depends on."""

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from apps.hub_api.risk_research_routes import risk_research_router
from packages.kernel.decision_hub_kernel.application.risk_research_admission import (
    RiskResearchAdmissionService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRecord
from packages.kernel.decision_hub_kernel.persistence.risk_research_records import (
    RiskResearchRequestRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope


def test_owner_command_retains_failed_manual_run_and_separate_new_auto_with_no_get_writes(tmp_path):
    db = Database("sqlite:///" + (tmp_path / "engineering-followup.sqlite3").as_posix())
    db.create_all()
    now = [datetime(2026, 10, 15, 14, 20, tzinfo=UTC)]
    service = RiskResearchAdmissionService(db, enabled=True, clock=lambda: now[0])
    a = service.stage(
        "BTC-USDT",
        request_id="engineering-owner-command",
        source_ref="owner-command:engineering-owner-command",
        source={"owner_id": "alice"},
    )
    scope = spot_scope("BTC-USDT").scope_id
    run_id = "engineering-followup-run"
    with db.session() as session:
        row = session.get(RiskResearchRequestRecord, a["request_id"])
        row.status, row.run_id = "queued", run_id
        session.add(
            RunRecord(
                run_id=run_id,
                event_id="engineering-only",
                status="running",
                created_at=now[0],
                updated_at=now[0],
            )
        )
    now[0] += timedelta(minutes=5)
    b = service.stage(
        "BTC-USDT",
        request_id="engineering-alert",
        kind="risk_alert",
        source_ref="engineering-risk-alert",
        source={},
    )
    now[0] += timedelta(minutes=2)
    with db.session() as session:
        run = session.get(RunRecord, run_id)
        run.status, run.error_code = "failed", "research_method_binding_failed"
        run.updated_at = run.finished_at = now[0]

    def owner(value):
        if not value:
            raise HTTPException(403, "owner_identity_required")
        return value

    app = FastAPI()
    app.include_router(risk_research_router(service, owner, clock=lambda: now[0]))
    url = f"/v1/risk/scopes/{scope}/research/commands/engineering-owner-command"
    with TestClient(app) as client:
        latest = client.get(f"/v1/risk/scopes/{scope}/research").json()
        assert latest["request"]["request_id"] == b["request_id"]
        response = client.get(url, headers={"X-Owner-Id": "alice"})
        assert response.status_code == 200
        result = response.json()
        assert result["command_id"] == "engineering-owner-command"
        assert result["request"]["request_id"] == a["request_id"]
        assert result["request"]["run_id"] == run_id
        assert result["request"]["run_status"] == result["request"]["status"] == "failed"
        assert result["next_automatic_request"]["request_id"] == b["request_id"]
        assert result["next_automatic_request"]["run_id"] is None
        assert client.get(url, headers={"X-Owner-Id": "alice"}).json() == result
        assert client.get(url, headers={"X-Owner-Id": "bob"}).status_code == 404
        assert (
            client.get(
                url.replace("engineering-owner-command", "engineering-unknown-command"),
                headers={"X-Owner-Id": "alice"},
            ).status_code
            == 404
        )
        assert (
            client.get(
                url.replace(scope, spot_scope("ETH-USDT").scope_id), headers={"X-Owner-Id": "alice"}
            ).status_code
            == 404
        )
        assert (
            client.get(
                url.replace(scope, "unknown-scope"), headers={"X-Owner-Id": "alice"}
            ).status_code
            == 404
        )
        assert client.get(url).status_code == 403
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(RunRecord)) == 1
        assert session.scalar(select(func.count()).select_from(RiskResearchRequestRecord)) == 2
    db.engine.dispose()
