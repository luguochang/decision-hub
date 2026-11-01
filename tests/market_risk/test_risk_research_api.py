from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from apps.hub_api.risk_research_routes import risk_research_router
from packages.kernel.decision_hub_kernel.application.risk_research_admission import (
    RiskResearchAdmissionService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRecord
from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope
from tests.market_risk.test_market_data import NOW


def test_owner_command_is_durable_idempotent_and_read_never_runs_ai(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'research-api.sqlite'}")
    db.create_all()
    service = RiskResearchAdmissionService(db, enabled=True, clock=lambda: NOW)

    def owner(value):
        if value != "owner":
            raise HTTPException(403, "owner_required")
        return value

    app = FastAPI()
    app.include_router(risk_research_router(service, owner, clock=lambda: NOW))
    path = f"/v1/risk/scopes/{spot_scope('BTC-USDT').scope_id}/research"
    with TestClient(app) as client:
        assert client.get(path).json()["request"] is None
        body = {"request_id": "risk-command1"}
        assert client.post(path, json=body).status_code == 403
        headers = {"X-Owner-Id": "owner", "Idempotency-Key": body["request_id"]}
        original = client.post(path, json=body, headers=headers)
        assert original.status_code == 200
        assert original.json()["status"] == "pending_admission"
        assert client.post(path, json=body, headers=headers).json() == original.json()
        assert client.get(path).json()["request"] == original.json()
        assert (
            client.post(
                path, json=body, headers={**headers, "Idempotency-Key": "wrong"}
            ).status_code
            == 400
        )
        service.enabled = False
        assert client.get(path).json()["enabled"] is False
        assert (
            client.post(
                path, json={"request_id": "new"}, headers={**headers, "Idempotency-Key": "new"}
            ).status_code
            == 409
        )
    with db.session() as session:
        assert session.query(RunRecord).count() == 0
    db.engine.dispose()
