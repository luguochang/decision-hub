"""Saved report links do not authenticate quality or block immediate risk reads."""

from datetime import timedelta

import pytest

from packages.kernel.decision_hub_kernel.application import research_quality_view
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalOutlookRecord,
    ArtifactRecord,
    Database,
    RunRecord,
)
from packages.query_views import market_risk
from tests.market_risk.test_market_data import NOW, SCOPE


@pytest.fixture
def query(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'projection.sqlite'}")
    db.create_all()
    with db.session() as session:
        for run_id, instrument, horizon, age in (
            ("btc24", "BTC-USDT", "24h", 5),
            ("btc30", "BTC-USDT", "30m", 1),
            ("eth24", "ETH-USDT", "24h", 1),
            ("future", "BTC-USDT", "24h", -10),
        ):
            stamp = NOW - timedelta(minutes=age)
            session.add(RunRecord(
                run_id=run_id, event_id=run_id, artifact_id=run_id,
                status="research_only", created_at=stamp, updated_at=stamp,
            ))
            session.add(ArtifactRecord(
                artifact_id=run_id, run_id=run_id, event_id=run_id,
                gate_status="publish" if run_id == "btc24" else "research_only",
                headline="controlled",
                summary="protected_context_incomplete;challenge_unresolved:internal",
                payload_json='{"counter_thesis":"旧反方"}', created_at=stamp,
            ))
            session.add(AnalyticalOutlookRecord(
                outlook_id=run_id, run_id=run_id, artifact_id=run_id, market="crypto",
                instrument=instrument, method_family="ai_synthesis", method_version="test",
                horizon=horizon, action="neutral", probability_status="uncalibrated",
                window_start_at=stamp, window_end_at=stamp + timedelta(hours=24),
                result_hash="a" * 64, evidence_refs_json="[]", fact_version_refs_json="[]",
                raw_payload_json="{}", trigger="test", invalidation="test",
                cohort="prospective", eligibility="unverified", exclusion_reasons_json="[]",
                research_quality="unknown", recorded_at=stamp,
            ))
    try:
        yield market_risk.MarketRiskQuery(db, tmp_path, clock=lambda: NOW)
    finally:
        db.engine.dispose()


def test_saved_exact_asset_24h_link_does_not_read_or_grant_quality(query, monkeypatch):
    def forbidden_quality_read(*args, **kwargs):
        raise AssertionError("Immediate risk reads must not authenticate the full report")

    monkeypatch.setattr(
        research_quality_view, "read_published_research_quality_in_session", forbidden_quality_read,
    )
    monkeypatch.setattr(
        market_risk, "read_published_research_quality_in_session", forbidden_quality_read,
        raising=False,
    )
    selected = ((SCOPE, "btc24"), (query.scope("crypto:spot:okx:ETH-USDT:trade:1m"), "eth24"))
    for scope, run_id in selected:
        view, counter = query._research(scope, NOW)
        assert view.run_id == view.artifact_id == run_id
        assert view.status == "partial" and counter is None
        assert "protected_context_incomplete" not in view.summary
        assert "challenge_unresolved:internal" not in view.summary
        with query.database.session() as session:
            original = session.get(ArtifactRecord, run_id)
            assert original.summary == "protected_context_incomplete;challenge_unresolved:internal"
            assert original.payload_json == '{"counter_thesis":"旧反方"}'

    with query.database.session() as session:
        session.query(AnalyticalOutlookRecord).filter_by(
            instrument="BTC-USDT", horizon="24h",
        ).delete()
    missing, counter = query._research(SCOPE, NOW)
    assert missing.status == "not_available"
    assert missing.run_id is missing.artifact_id is missing.summary is counter is None
