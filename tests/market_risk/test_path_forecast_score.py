"""Hand-counted synthetic estimator scores, not authenticated market performance."""

from datetime import timedelta
from decimal import Decimal

import pytest

from packages.kernel.decision_hub_kernel.application.path_forecast_history import seal_payload
from packages.kernel.decision_hub_kernel.decision.path_forecast import fit_path_forecast
from packages.kernel.decision_hub_kernel.decision.path_forecast_score import score_path_forecasts
from packages.kernel.decision_hub_kernel.ports.path_forecast import PathObservationReceipt
from tests.market_risk.test_path_forecast import BTC, CUTOFF, ETH, predict, window


def label(scope=BTC):
    original = window(1, scope=scope, cutoff=CUTOFF)
    return seal_payload(
        PathObservationReceipt(
            scope=scope,
            input_cutoff_at=CUTOFF,
            path=original.path,
            label_known_at=original.label_known_at,
            originals=(),
            payload_hash="0" * 64,
        )
    )


def forecasts(scope=BTC):
    model = fit_path_forecast(
        scope,
        tuple(window(i, scope=scope) for i in range(60)),
        training_cutoff_at=CUTOFF,
    )
    # Existing helper uses BTC features, so use explicit feature for ETH.
    if scope == BTC:
        return predict(model), predict(model, version="COND_VOL_V1", rv="1")
    from packages.kernel.decision_hub_kernel.decision.path_forecast import predict_path_forecast
    from tests.market_risk.test_path_forecast import feature

    return tuple(
        predict_path_forecast(
            model,
            cutoff_at=CUTOFF,
            published_at=CUTOFF,
            features=feature(CUTOFF, rv="1", scope=scope),
            model_version=method,
        )
        for method in ("BASE_EMPIRICAL_V1", "COND_VOL_V1")
    )


def test_hand_brier_same_inputs_full_missing_denominator_and_no_ci():
    base, candidate = forecasts()
    # Both methods must receive the same current feature; RV=1 still selects low.
    model = fit_path_forecast(BTC, tuple(window(i) for i in range(60)), training_cutoff_at=CUTOFF)
    base = predict(model, rv="1")
    result = score_path_forecasts(
        (base, candidate),
        (label(),),
        expected_slots=((BTC.scope_id, CUTOFF), (BTC.scope_id, CUTOFF + timedelta(days=1))),
        as_of=CUTOFF + timedelta(days=2),
    )
    main = next(target for target in result["targets"] if target["primary"])
    assert Decimal(main["baseline_brier"]) == Decimal(".25")
    assert Decimal(main["candidate_brier"]) == (Decimal(".5") / 31) ** 2
    assert main["paired_scored"] == 1
    assert Decimal(main["paired_delta"]) > 0
    assert result["expected_prediction_count"] == 4
    assert result["unpublished_prediction_count"] == 2
    assert result["unavailable_label_count"] == 1
    assert result["ci"] is None and not result["promotion_allowed"]
    assert result["alarm_metrics"]["status"] == "unknown"
    assert result["conclusion"] == "insufficient_evidence"


def test_two_assets_same_utc_day_are_one_block_and_distinct_inputs_do_not_pair():
    btc = forecasts()
    eth = forecasts(ETH)
    result = score_path_forecasts(
        (*btc, *eth),
        (label(), label(ETH)),
        expected_slots=((BTC.scope_id, CUTOFF), (ETH.scope_id, CUTOFF)),
        as_of=CUTOFF + timedelta(days=2),
    )
    # BTC helper supplied rv=2 vs rv=1: its losses are public but pair is excluded.
    assert any("pair_input_identity_mismatch" in item["reasons"] for item in result["exclusions"])
    assert result["independent_paired_day_blocks"] == 1
    assert result["independent_event_blocks"] is None


def test_late_and_tampered_forecast_cannot_score_or_change_fixed_window():
    model = fit_path_forecast(BTC, tuple(window(i) for i in range(60)), training_cutoff_at=CUTOFF)
    late = predict(model, published=CUTOFF + timedelta(minutes=1, microseconds=1))
    result = score_path_forecasts(
        (late,),
        (label(),),
        expected_slots=((BTC.scope_id, CUTOFF),),
        as_of=CUTOFF + timedelta(days=2),
    )
    assert result["late_prediction_count"] == 1
    assert all(target["baseline_scored"] == 0 for target in result["targets"])
    with pytest.raises(ValueError, match="payload_changed"):
        score_path_forecasts(
            (late.model_copy(update={"parameter_hash": "0" * 64}),),
            (),
            expected_slots=((BTC.scope_id, CUTOFF),),
            as_of=CUTOFF + timedelta(days=2),
        )
