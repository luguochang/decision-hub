"""Real physical legacy v1 reader/hash preservation; no relabelled old original."""

import hashlib
import importlib
import inspect
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from candidate_perpetual_forecast_support import MAIN, load, publication_case
from test_perpetual_forecast_scoring_store import service_module


def test_four_physical_legacy_software_sources_remain_exact():
    pins = {
        "application/perpetual_forecast_experience.py": (
            "c4dca845491eedcf387a1f76149605096e6569c7bb32896a7f390089b6966544"
        ),
        "ports/perpetual_forecast_experience.py": (
            "54d7240db912f264934c9bfa197161dce782c6aaad03a3615827550efcc3542a"
        ),
        "ports/perpetual_close.py": (
            "ebbaf3a6dd41aa0f95bbe03fb520710d1250d4b84b869eda49b3d6b15001c47a"
        ),
        "application/market_original_parse.py": (
            "d5743e5bb60fe8ffeae4151e555816356ae2e7c255ddc275b5ac348a5e298d8c"
        ),
    }
    for relative, expected in pins.items():
        name = "packages.kernel.decision_hub_kernel." + relative.removesuffix(".py").replace(
            "/", "."
        )
        module = importlib.import_module(name)
        actual = Path(inspect.getfile(module)).resolve()
        assert actual == (MAIN / "packages/kernel/decision_hub_kernel" / relative).resolve()
        assert hashlib.sha256(actual.read_bytes()).hexdigest() == expected


def test_legacy_v1_original_reads_exactly_and_cannot_be_recomputed_as_v2(
    tmp_path: Any, monkeypatch: Any
):
    module = service_module()
    legacy = importlib.import_module(
        "packages.kernel.decision_hub_kernel.application.perpetual_forecast_experience"
    )
    from tests.decision_product.test_perpetual_forecast_experience import future_pages
    from tests.decision_product.test_perpetual_research_binding import OWNER

    case = publication_case(tmp_path, monkeypatch, "BTC-USDT-SWAP", "4h")
    try:
        end = case["derived"].bound.target.window_end
        if isinstance(end, str):
            end = datetime.fromisoformat(end.replace("Z", "+00:00"))
        at = end + timedelta(minutes=1)
        ids = future_pages(case, end, prefix="synthetic-legacy-v1-")
        original = legacy.PerpetualForecastExperienceService(
            case["db"], case["source"], clock=lambda: at
        ).record(run_id=case["run_id"], capture_ids=ids)
        assert original.body["schema_version"] == "perpetual-forecast-experience.v1"
        assert "log_loss" not in original.body
        with pytest.raises(ValueError, match="legacy_observation_preserved"):
            module.PerpetualForecastScoringService(
                case["db"], case["source"], clock=lambda: at
            ).record(run_id=case["run_id"], capture_ids=ids)
        with case["db"].session() as session:
            assert (
                legacy.read_forecast_experience_in_session(
                    session, original.ref, cutoff_at=at
                ).body_json
                == original.body_json
            )
        query = load(
            "packages/kernel/decision_hub_kernel/application/perpetual_forecast_result_view.py"
        )
        body = query.PerpetualForecastResultQuery(case["db"], now=lambda: at).read(
            OWNER, case["run_id"]
        )
        assert body["schema_version"] == "perpetual-forecast-result-view.v1"
        assert body["status"] == "observed" and "log_loss" not in body["result"]
    finally:
        case["db"].engine.dispose()
