"""P6 projections use the real publication reader, not a replacement query."""
from datetime import timedelta

import pytest

from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_advice_query import current, persisted_publication
from tests.decision_product.test_perpetual_research_binding import AT
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case


def test_legacy_publication_has_explicit_unknown_basis(binding_case):
    persisted_publication(binding_case)
    advice = current(binding_case, now=AT + timedelta(seconds=40))
    assert advice["forecast_basis"] is None


def test_numeric_relations_preserve_exact_probability_text():
    from packages.kernel.decision_hub_kernel.application.perpetual_forecast_basis import (
        compare_probabilities,
    )

    base = [{"outcome_id": key, "value": value} for key, value in
            (("up", "0.34"), ("neutral", "0.33"), ("down", "0.33"))]
    assert compare_probabilities(base, base) == ("equal", "0")
    near = [{**row} for row in base]
    near[0]["value"], near[1]["value"] = "0.341", "0.329"
    assert compare_probabilities(base, near) == ("different", "0.1")
    far = [{**row} for row in base]
    far[0]["value"], far[1]["value"] = "0.54", "0.13"
    assert compare_probabilities(base, far) == ("different", "20")
    assert compare_probabilities(base, None) == ("unknown", None)


def test_frozen_history_reaches_real_query_and_tampering_is_rejected(
    binding_case, monkeypatch, tmp_path,
):
    import json

    from packages.kernel.decision_hub_kernel.application.perpetual_base_rate_source import (
        read_direction_base_rate_in_session,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
        PerpetualResearchBindingService,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
    from tests.decision_product.test_perpetual_base_rate_source import hourly_pages

    case = binding_case
    ids = hourly_pages((case["db"], case["archive"], case["source"], None))
    prepare = PerpetualResearchBindingService.prepare_admission_body_in_session

    def with_frozen_history(self, session, **kwargs):
        # Only fixture admission assembly is extended. All production read/auth paths run.
        admission = prepare(self, session, **kwargs)
        admission["direction_base_rate"] = read_direction_base_rate_in_session(
            session, case["archive"], inst_id="BTC-USDT-SWAP", capture_ids=ids,
            as_of=AT, threshold_fraction=admission["candidate_target"]["threshold_fraction"],
            horizon_seconds=14400,
        )
        return admission

    monkeypatch.setattr(
        PerpetualResearchBindingService, "prepare_admission_body_in_session", with_frozen_history
    )
    persisted_publication(case)
    advice = current(case, now=AT + timedelta(seconds=40))
    basis = advice["forecast_basis"]
    assert basis["run_id"] == case["run_id"]
    assert basis["horizon"] == "4h"
    assert basis["baseline_probabilities"] == basis["research_materials"][
        "direction_base_rate"]["window_90d"]["probabilities"]
    assert basis["model_probabilities"] == advice["market_distribution"][
        "direction_distribution"]["probabilities"]
    assert basis["relation"] in {"equal", "different", "unknown"}
    out = tmp_path
    out.joinpath("authenticated-projection.json").write_text(
        json.dumps(advice, ensure_ascii=False), encoding="utf-8"
    )
    out.joinpath("projection-bytes.json").write_text(json.dumps({
        "materials_utf8_bytes": len(json.dumps(basis["research_materials"]).encode()),
        "projection_utf8_bytes": len(json.dumps(basis).encode()),
    }), encoding="utf-8")
    with case["db"].session() as session:
        original = session.query(RunRoleOutputRecord).filter_by(
            run_id=case["run_id"], payload_hash=basis["research_materials_ref"]["hash"]
        ).one()
        changed = json.loads(original.payload_json)
        changed["direction_base_rate"]["horizon_seconds"] = 86400
        original.payload_json = json.dumps(changed)
    with pytest.raises(ValueError, match="perpetual_advice_original_unavailable"):
        current(case, now=AT + timedelta(seconds=40))


@pytest.mark.parametrize("values", [
    ("0.34", "0.33", "0.34"), ("-0.01", "0.5", "0.51"),
    ("NaN", "0.5", "0.5"), ("1.01", "0", "-0.01"),
])
def test_invalid_probability_vectors_cannot_be_shown(values):
    from packages.kernel.decision_hub_kernel.application.perpetual_forecast_basis import (
        compare_probabilities,
    )
    rows = [{"outcome_id": key, "value": value}
            for key, value in zip(("up", "neutral", "down"), values, strict=True)]
    with pytest.raises(ValueError, match="perpetual_forecast_basis_probabilities_invalid"):
        compare_probabilities(rows, None)
