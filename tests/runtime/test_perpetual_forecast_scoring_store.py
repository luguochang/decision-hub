"""Original publication + private SQL truth/score persistence; synthetic samples only."""

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any

import pytest
from candidate_perpetual_forecast_support import load, parsed_pages, publication_case


def service_module() -> Any:
    load("packages/kernel/decision_hub_kernel/ports/perpetual_forecast_scoring.py")
    load("packages/kernel/decision_hub_kernel/application/perpetual_forecast_truth.py")
    return load("packages/kernel/decision_hub_kernel/application/perpetual_forecast_scoring.py")


@pytest.mark.parametrize("instrument", ["BTC-USDT-SWAP", "ETH-USDT-SWAP"])
@pytest.mark.parametrize("horizon", ["4h", "24h", "72h"])
def test_original_frozen_publication_to_v2_score_readback_is_idempotent(
    tmp_path: Any,
    monkeypatch: Any,
    instrument: Any,
    horizon: Any,
):
    assert_score_wire(tmp_path, monkeypatch, instrument, horizon, "finite")


@pytest.mark.parametrize("mode", ["infinite", "unavailable"])
def test_original_unknown_and_zero_probability_facts_are_projected_without_clipping(
    tmp_path: Any, monkeypatch: Any, mode: Any
):
    assert_score_wire(tmp_path, monkeypatch, "ETH-USDT-SWAP", "24h", mode)


def assert_score_wire(tmp_path: Any, monkeypatch: Any, instrument: Any, horizon: Any, mode: Any):
    module = service_module()
    case = publication_case(tmp_path, monkeypatch, instrument, horizon, probability_mode=mode)
    try:
        from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord

        target = case["derived"].bound.target
        end = target.window_end
        if isinstance(end, str):
            end = datetime.fromisoformat(end.replace("Z", "+00:00"))
        entries = []
        _, _, at, _ = parsed_pages(horizon, instrument, end=end, entries=entries)
        for capture, raw in entries:
            assert case["archive"].put(raw) == (capture.body_hash, capture.body_ref)
            with case["db"].session() as session:
                session.add(RawCaptureRecord(**asdict(capture), media_type="application/json"))
        service = module.PerpetualForecastScoringService(
            case["db"], case["source"], clock=lambda: at
        )
        ids = tuple(capture.capture_id for capture, _ in entries)
        original = service.record(run_id=case["run_id"], capture_ids=ids)
        assert original.body["schema_version"] == "perpetual-forecast-experience.v2"
        assert original.body["horizon"] == horizon
        assert original.body["instrument_id"] == "okx:" + instrument
        assert (
            original.body["financial_qualified"] is False
            and original.body["trade_outcome"] is False
        )
        assert len(original.body["reliability_observations"]) == (0 if mode == "unavailable" else 3)
        assert original.body["log_loss"]["status"] == mode
        assert len(original.body["closes"]) == {"4h": 241, "24h": 1441, "72h": 4321}[horizon]
        assert service.record(run_id=case["run_id"], capture_ids=ids) == original
        with case["db"].session() as session:
            assert service.read_in_session(session, original.ref, cutoff_at=at) == original
        if instrument == "ETH-USDT-SWAP" and horizon == "24h" and mode == "finite":
            assert_original_learning_scope(case, original, at)
            with pytest.raises(ValueError, match="capture_conflict"):
                service.record(run_id=case["run_id"], capture_ids=("different-capture",))
            assert_resealed_score_rejected(case, original, service, at)
        (tmp_path / "scoring-original.v2.json").write_text(original.body_json, encoding="utf-8")
        load("packages/kernel/decision_hub_kernel/application/perpetual_forecast_scoring_view.py")
        query_module = load(
            "packages/kernel/decision_hub_kernel/application/perpetual_forecast_result_view.py"
        )
        from tests.decision_product.test_perpetual_research_binding import OWNER

        result_view = query_module.PerpetualForecastResultQuery(case["db"], now=lambda: at).read(
            OWNER, case["run_id"]
        )
        assert result_view["schema_version"] == "perpetual-forecast-result-view.v2"
        assert result_view["status"] == "observed"
        assert result_view["result"]["log_loss"] == original.body["log_loss"]
        from packages.kernel.decision_hub_kernel.application.perpetual_advice_query import (
            PerpetualAdviceQuery,
        )
        from tests.decision_product.test_perpetual_advice_query import params

        advice = PerpetualAdviceQuery(case["db"], now=lambda: at).current(
            OWNER, **{**params(case), "horizon": horizon}
        )
        (tmp_path / "forecast-wire.v2.json").write_text(
            json.dumps({"advice": advice, "result": result_view}), encoding="utf-8"
        )
    finally:
        case["db"].engine.dispose()


def assert_resealed_score_rejected(case: Any, original: Any, service: Any, at: Any):
    from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
    from packages.kernel.decision_hub_kernel.ports.perpetual_forecast_experience import (
        experience_original,
    )

    forged = experience_original(
        {**original.body, "log_loss": {"status": "finite", "value": "0", "reason": None}}
    )
    with case["db"].session() as session:
        row = session.get(RunRoleOutputRecord, original.ref.ref)
        row.output_id, row.payload_json, row.payload_hash = (
            forged.ref.ref,
            forged.body_json,
            forged.ref.hash,
        )
    try:
        with case["db"].session() as session:
            with pytest.raises(ValueError, match="original_changed"):
                service.read_in_session(session, forged.ref, cutoff_at=at)
    finally:
        with case["db"].session() as session:
            row = session.get(RunRoleOutputRecord, forged.ref.ref)
            row.output_id, row.payload_json, row.payload_hash = (
                original.ref.ref,
                original.body_json,
                original.ref.hash,
            )


def assert_original_learning_scope(case: Any, original: Any, at: Any):
    from datetime import timedelta

    inputs = load(
        "packages/kernel/decision_hub_kernel/application/perpetual_forecast_scoring_inputs.py"
    )
    arguments = dict(
        owner_id=original.body["owner_id"],
        instrument_id=original.body["instrument_id"],
        horizon=original.body["horizon"],
        cutoff_at=at,
    )
    with case["db"].session() as session:
        selected = inputs.scoring_context_in_session(session, **arguments)
        assert len(selected) == 1 and selected[0].evidence_id == original.ref.ref
        assert selected[0].source_id == "perpetual_forecast_experience.v2"
        assert selected[0].content_hash == original.ref.hash and '"log_loss"' in selected[0].excerpt
        for override in (
            {"owner_id": "other-owner"},
            {"instrument_id": "okx:BTC-USDT-SWAP"},
            {"horizon": "4h"},
            {"cutoff_at": at - timedelta(seconds=1)},
        ):
            assert inputs.scoring_context_in_session(session, **{**arguments, **override}) == ()
