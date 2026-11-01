"""Private SQL version and same-writer seams; no real market qualification."""

import asyncio
from datetime import timedelta

import pytest

from packages.kernel.decision_hub_kernel.application.path_shadow_jobs import PathShadowJobService
from packages.kernel.decision_hub_kernel.application.risk_path_ledger import (
    REPORT,
    RiskPathLedger,
    encoded,
    writer,
)
from packages.kernel.decision_hub_kernel.decision.path_forecast import LEGACY_PARAMETERS
from packages.kernel.decision_hub_kernel.persistence.path_forecast_records import (
    RiskPredictionRecord,
    RiskShadowTrialRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from tests.market_risk.test_path_shadow_ledger import BTC, ETH, FIRST
from tests.market_risk.test_path_shadow_ledger import runtime as shadow_runtime


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    yield from shadow_runtime.__wrapped__(tmp_path, monkeypatch)


def test_legacy_unfit_protocol_keeps_v1_model_and_registration_original(runtime):
    db, _, _, now, ledger, jobs = runtime
    # A bounded exact V1 fixture, not a rewritten historical production row.
    protocol = {
        "schema_version": "risk-path-shadow-trial.v1",
        "parameters": LEGACY_PARAMETERS.model_dump(mode="json"),
        "scopes": [BTC.model_dump(mode="json"), ETH.model_dump(mode="json")],
        "first_cutoff_at": FIRST.isoformat(),
        "target_version": "long-short-mae-2pct-5pct.v1",
        "feature_policy": LEGACY_PARAMETERS.feature_policy,
        "source_policy": "authenticated-market-path-store.v1",
        "alarm_policy": "not_registered_no_user_dispatch",
        "promotion_allowed": False,
    }
    digest = canonical_hash(protocol)
    trial_id = "controlled-old-v1"
    with db.session() as session:
        writer(session)
        session.add(
            RiskShadowTrialRecord(
                trial_id=trial_id,
                request_id="legacy-key",
                registration_hash=digest,
                protocol_json=encoded(protocol),
                protocol_hash=digest,
                registered_at=now[0],
                first_cutoff_at=FIRST,
                last_cutoff_at=FIRST + timedelta(days=27),
                last_window_end_at=FIRST + timedelta(days=28, minutes=1),
                label_deadline_at=FIRST + timedelta(days=29, minutes=1),
                state="registered",
            )
        )
        ledger.add_job(session, trial_id, "fit", BTC.scope_id, FIRST, now[0], FIRST)
    assert ledger.register(request_id="legacy-key", first_cutoff_at=FIRST) == trial_id
    assert jobs.process_due(limit=1) == 1
    model = ledger.fitted_model(trial_id, BTC.scope_id)
    assert model.parameters == LEGACY_PARAMETERS
    assert "ci_seed" not in model.parameters.model_dump(mode="json")
    with db.session() as session:
        row = session.get(RiskShadowTrialRecord, trial_id)
        assert row.protocol_hash == digest and row.protocol_json == encoded(protocol)
    assert REPORT == "path-shadow-evaluation.v1"


def test_new_pair_callback_uses_saved_originals_same_writer_and_rolls_back(runtime):
    db, data, _, now, original_ledger, jobs = runtime
    trial = original_ledger.register(request_id="v2", first_cutoff_at=FIRST)
    assert original_ledger.trial(trial)["protocol"]["parameters"]["protocol_version"] == (
        "path-shadow-24h.v2"
    )
    assert jobs.process_due(limit=2) == 2
    now[0] = FIRST - timedelta(seconds=1)
    assert asyncio.run(data.refresh_due()) == 2
    now[0] = FIRST + timedelta(seconds=10)
    called = []

    def reject_after_originals(session, *, request_id, prediction_refs, known_at):
        assert session.connection().connection.driver_connection.in_transaction
        assert request_id.startswith("forecast-alarm:pathjob_") and known_at == now[0]
        assert len(prediction_refs) == 2
        for ref in prediction_refs:
            row = session.get(RiskPredictionRecord, ref["prediction_id"])
            assert row is not None and row.payload_hash == ref["payload_hash"]
        called.append(prediction_refs)
        raise ValueError("controlled_alarm_writer_failure")

    ledger = RiskPathLedger(
        db, data.store, clock=lambda: now[0], forecast_alarm_publisher=reject_after_originals
    )
    worker = PathShadowJobService(ledger, owner_id="v2-writer")
    claim = worker.claim()
    assert claim.kind == "forecast"
    with pytest.raises(ValueError, match="controlled_alarm_writer_failure"):
        worker.execute(claim)
    assert len(called) == 1
    with db.session() as session:
        assert session.query(RiskPredictionRecord).count() == 0
    # The genuine same frozen model can subsequently publish without an alarm hook.
    assert jobs.process_due(limit=1) == 1  # Other scope remains eligible.
    assert len(original_ledger.predictions(ETH.scope_id, trial_id=trial)) == 2 or (
        len(original_ledger.predictions(BTC.scope_id, trial_id=trial)) == 2
    )
