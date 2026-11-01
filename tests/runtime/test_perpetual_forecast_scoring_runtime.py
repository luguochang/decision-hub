"""Original normal tick with authored venue bytes; no live HTTP or model."""

import ast
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from candidate_perpetual_forecast_support import ROOT, load, publication_case
from test_perpetual_forecast_scoring_store import service_module


def runtime_module() -> Any:
    service_module()
    load("packages/kernel/decision_hub_kernel/application/perpetual_experience_use.py")
    capture = (
        ROOT / "packages/kernel/decision_hub_kernel/application/perpetual_forecast_truth_capture.py"
    )
    if capture.exists():
        load("packages/kernel/decision_hub_kernel/application/perpetual_forecast_truth_capture.py")
    return load("packages/kernel/decision_hub_kernel/application/perpetual_forecast_runtime.py")


def test_explicit_scoring_version_preserves_legacy_default_and_normal_selects_v2():
    module = runtime_module()
    database = object()
    source = SimpleNamespace(database=database)
    assert module.PerpetualForecastRuntime(database, source).scoring_version == "v1"
    assert (
        module.PerpetualForecastRuntime(database, source, scoring_version="v2").scoring_version
        == "v2"
    )
    with pytest.raises(ValueError, match="scoring_version"):
        module.PerpetualForecastRuntime(database, source, scoring_version="v9")
    tree = ast.parse((ROOT / "apps/hub_worker/composition.py").read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "PerpetualForecastRuntime"
    ]
    assert len(calls) == 1
    version = next(item.value for item in calls[0].keywords if item.arg == "scoring_version")
    assert isinstance(version, ast.Constant) and version.value == "v2"


@pytest.mark.asyncio
async def test_normal_due_seventy_two_hour_publication_pages_and_persists_once(
    tmp_path: Any, monkeypatch: Any
):
    module = runtime_module()
    from apps.hub_worker.composition import build_research_admission_tick
    from packages.kernel.decision_hub_kernel.application.risk_research_admission import (
        RiskResearchAdmissionService,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord, utcnow
    from packages.kernel.decision_hub_kernel.ports.perpetual_close import millis

    case = publication_case(tmp_path, monkeypatch, "ETH-USDT-SWAP", "72h")
    try:
        end = case["derived"].bound.target.window_end
        if isinstance(end, str):
            end = datetime.fromisoformat(end.replace("Z", "+00:00"))
        start = end - timedelta(hours=72)
        holder, calls = {"at": end - timedelta(seconds=1)}, []

        def venue(request: Any):
            assert request.url.params["instId"] == "ETH-USDT-SWAP"
            cursor = int(request.url.params["after"])
            calls.append(cursor)
            rows = []
            for index in range(300):
                opened = cursor - (index + 1) * 60000
                if opened < millis(start) - 60000:
                    break
                price = "110" if opened + 60000 == millis(end) else "100"
                rows.append([str(opened), price, price, price, price, "7", "0.07", "7", "1"])
            return httpx.Response(200, json={"code": "0", "data": rows})

        runtime = module.PerpetualForecastRuntime(
            case["db"],
            case["source"],
            scoring_version="v2",
            clock=lambda: holder["at"],
            http_transport=httpx.MockTransport(venue),
        )
        tick = build_research_admission_tick(
            perpetual_forecasts=runtime,
            periodic_admission=RiskResearchAdmissionService(case["db"], enabled=False),
            previous_research_tick=None,
        )
        assert await tick() == 0 and calls == []
        holder["at"] = utcnow() + timedelta(minutes=1)
        claim = runtime._claim(case["run_id"])
        assert claim["schema_version"] == "perpetual-forecast-due-observation.v2"
        load("packages/kernel/decision_hub_kernel/application/perpetual_forecast_scoring_view.py")
        query = load(
            "packages/kernel/decision_hub_kernel/application/perpetual_forecast_result_view.py"
        )
        from tests.decision_product.test_perpetual_research_binding import OWNER

        pending = query.PerpetualForecastResultQuery(case["db"], now=lambda: holder["at"]).read(
            OWNER, case["run_id"]
        )
        assert pending["schema_version"] == "perpetual-forecast-result-view.v2"
        assert pending["status"] == "pending" and pending["result"] is None and calls == []
        holder["at"] += timedelta(seconds=301)
        assert await tick() == 1 and len(calls) == 15
        assert calls == [millis(end) - page * 300 * 60000 for page in range(15)]
        assert await tick() == 0 and len(calls) == 15
        with case["db"].session() as session:
            original = (
                session.query(RunRoleOutputRecord)
                .filter_by(role="perpetual_forecast_experience_v2")
                .one()
            )
            body = json.loads(original.payload_json)
            assert len(body["closes"]) == 4321 and body["horizon"] == "72h"
            due = [
                json.loads(row.payload_json)
                for row in session.query(RunRoleOutputRecord).filter_by(
                    schema_version="perpetual-forecast-due-observation.v2"
                )
            ]
            assert due[-1]["status"] == "recorded" and due[-1]["label_created"] is True
            assert len(due[-1]["capture_ids"]) == 15
            assert (
                not session.query(RunRoleOutputRecord)
                .filter_by(role="perpetual_forecast_experience_v1")
                .count()
            )
    finally:
        case["db"].engine.dispose()
