from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from packages.contracts_py.decision_hub_contracts import FactEnvelope
from packages.kernel.decision_hub_kernel.application.fact_store import ResearchFactStore
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    ResearchEvidenceRecord,
    RunRecord,
)

NOW = datetime(2026, 9, 4, 8, 0, tzinfo=UTC)


def _database(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'facts.sqlite3'}")
    database.create_all()
    with database.session() as session:
        session.add(
            RunRecord(
                run_id="run-fact",
                event_id="event-fact",
                status="running",
                strategy_version="research.v1",
                runtime_version="dsh.v1",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            ResearchEvidenceRecord(
                evidence_id="evidence-fact",
                run_id="run-fact",
                capability_id="market.crypto_derivatives",
                requirement_id="crypto_spot_confirmation",
                kind="market",
                authority="exchange",
                source_id="coinex-public",
                source_url="https://api.coinex.com/v2/spot/ticker",
                published_at=None,
                observed_at=NOW - timedelta(seconds=2),
                received_at=NOW - timedelta(seconds=1),
                content_hash="0" * 64,
                excerpt="typed spot fact",
                structured_payload_ref=None,
                tool_call_id="call-1",
                research_session_id="session-fact",
                round=1,
                quality="accepted",
                freshness_status="fresh",
                conflict_group=None,
                accepted_at=NOW,
            )
        )
    return database


def _fact(**updates: object) -> FactEnvelope:
    values: dict[str, Any] = {
        "schema_version": "fact-envelope.v1",
        "fact_id": "fact-spot-price",
        "evidence_id": "evidence-fact",
        "requirement_id": "crypto_spot_confirmation",
        "metric_family": "crypto.spot",
        "field": "price",
        "instrument": "BTCUSDT",
        "venue": "coinex",
        "value": "79043",
        "unit": "usdt",
        "window_start_at": None,
        "window_end_at": None,
        "event_offset": None,
        "observed_at": NOW - timedelta(seconds=2),
        "received_at": NOW - timedelta(seconds=1),
        "published_at": None,
        "source_id": "coinex-public",
        "independence_group": "coinex",
        "quality": "candidate",
        "delay_class": "realtime",
        "payload_schema_ref": "coinex.public-market.v2",
        "payload_hash": "1" * 64,
        "attributes": {"provider_field": "spot_price"},
    }
    values.update(updates)
    return FactEnvelope.model_validate(values)


def test_fact_store_is_idempotent_and_inherits_accepted_evidence_quality(
    tmp_path: Path,
) -> None:
    store = ResearchFactStore(_database(tmp_path))

    first = store.accept_facts(
        run_id="run-fact",
        capability_id="market.crypto_derivatives",
        facts=[_fact()],
        cutoff_at=NOW,
    )
    second = store.accept_facts(
        run_id="run-fact",
        capability_id="market.crypto_derivatives",
        facts=[_fact()],
        cutoff_at=NOW,
    )

    assert first == second
    assert first[0].quality == "accepted"
    assert store.list_run_facts("run-fact") == first


def test_fact_store_reuses_semantic_fact_across_later_provider_receipt(
    tmp_path: Path,
) -> None:
    store = ResearchFactStore(_database(tmp_path))
    first = store.accept_facts(
        run_id="run-fact",
        capability_id="market.crypto_derivatives",
        facts=[_fact()],
        cutoff_at=NOW,
    )

    repeated = store.accept_facts(
        run_id="run-fact",
        capability_id="market.crypto_derivatives",
        facts=[_fact(observed_at=NOW, received_at=NOW)],
        cutoff_at=NOW,
    )

    assert repeated == first
    assert store.list_run_facts("run-fact") == first


def test_fact_store_still_rejects_semantic_change_under_existing_fact_id(
    tmp_path: Path,
) -> None:
    store = ResearchFactStore(_database(tmp_path))
    store.accept_facts(
        run_id="run-fact",
        capability_id="market.crypto_derivatives",
        facts=[_fact()],
        cutoff_at=NOW,
    )

    with pytest.raises(ValueError, match="research_fact_identity_conflict"):
        store.accept_facts(
            run_id="run-fact",
            capability_id="market.crypto_derivatives",
            facts=[_fact(value="79044")],
            cutoff_at=NOW,
        )


def test_fact_store_rejects_wrong_evidence_lineage_and_future_receipt(tmp_path: Path) -> None:
    store = ResearchFactStore(_database(tmp_path))
    with pytest.raises(ValueError, match="research_fact_lineage_mismatch"):
        store.accept_facts(
            run_id="run-fact",
            capability_id="market.crypto_derivatives",
            facts=[_fact(source_id="another-provider")],
            cutoff_at=NOW,
        )
    with pytest.raises(ValueError, match="research_fact_pit_violation"):
        store.accept_facts(
            run_id="run-fact",
            capability_id="market.crypto_derivatives",
            facts=[_fact(received_at=NOW + timedelta(seconds=1))],
            cutoff_at=NOW,
        )
