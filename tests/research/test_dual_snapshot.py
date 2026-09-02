from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import AnyUrl

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    EvidenceRequirement,
    ObservationCreate,
)
from packages.contracts_py.decision_hub_contracts.models import SourceType, TextEnvelope
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    ResearchEvidenceService,
    research_evidence_content_hash,
    research_evidence_instance_id,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.snapshot import SnapshotService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRecord,
    SnapshotRecord,
)

TRIGGER_AT = datetime(2026, 8, 29, 3, 0, tzinfo=UTC)
DECISION_AT = TRIGGER_AT + timedelta(minutes=3)


def _database(tmp_path: Path) -> tuple[Database, str]:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'research.sqlite3'}")
    database.create_all()
    text = "A central-bank official discussed inflation risks."
    envelope = TextEnvelope(
        source_id="manual-transcript",
        source_type=SourceType.transcript,
        observed_at=TRIGGER_AT - timedelta(seconds=1),
        published_at=TRIGGER_AT - timedelta(minutes=1),
        received_at=TRIGGER_AT,
        raw_text=text,
        language="en",
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
    )
    trigger_snapshot_id, _ = SnapshotService(database).freeze("event-1", envelope)
    with database.session() as session:
        session.add(
            RunRecord(
                run_id="run-1",
                event_id="event-1",
                snapshot_id=trigger_snapshot_id,
                status="running",
                strategy_version="candidate.v1",
                runtime_version="dsh-sdk-0.1.1rc1",
                created_at=TRIGGER_AT,
                updated_at=TRIGGER_AT,
            )
        )
    return database, trigger_snapshot_id


def _requirement(*, freshness_seconds: int = 300) -> EvidenceRequirement:
    return EvidenceRequirement(
        requirement_id="derivatives_crowding",
        description="Funding and open interest confirmation.",
        importance="hard",
        source_priority=["exchange"],
        authority_floor="exchange",
        preferred_capabilities=["market.crypto_derivatives"],
        freshness_seconds=freshness_seconds,
        minimum_independent_sources=1,
        allowed_fallbacks=[],
        confidence_cap=0.5,
    )


def _candidate(
    *,
    evidence_id: str | None = None,
    published_at: datetime = DECISION_AT - timedelta(seconds=10),
    observed_at: datetime = DECISION_AT - timedelta(seconds=5),
    received_at: datetime = DECISION_AT - timedelta(seconds=4),
    excerpt: str = '{"fundingRate":"0.0001","oi":"1000"}',
    research_session_id: str = "research-session-1",
) -> EvidenceCandidate:
    url = "https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP"
    content_hash = research_evidence_content_hash(
        requirement_id="derivatives_crowding",
        kind="market",
        authority="exchange",
        source_id="okx-public",
        source_url=url,
        published_at=published_at,
        excerpt=excerpt,
        structured_payload_ref=None,
    )
    return EvidenceCandidate(
        evidence_id=evidence_id
        or research_evidence_instance_id(
            content_hash=content_hash,
            research_session_id=research_session_id,
        ),
        requirement_id="derivatives_crowding",
        kind="market",
        authority="exchange",
        source_id="okx-public",
        source_url=AnyUrl(url),
        published_at=published_at,
        observed_at=observed_at,
        received_at=received_at,
        content_hash=content_hash,
        excerpt=excerpt,
        structured_payload_ref=None,
        tool_call_id="tool-call-1",
        research_session_id=research_session_id,
        round=1,
        quality="candidate",
        freshness_status="unknown",
        conflict_group=None,
    )


def test_trigger_snapshot_identity_is_scoped_to_each_run(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'trigger-identity.sqlite3'}")
    database.create_all()
    event_id, _, admitted = AdmissionService(database, clock=lambda: TRIGGER_AT).admit(
        ObservationCreate(
            text="One event may be analyzed by baseline and research runtimes.",
            source_id="trigger-identity",
            source_type=SourceType.transcript,
            observed_at=TRIGGER_AT - timedelta(seconds=1),
            published_at=TRIGGER_AT - timedelta(seconds=2),
            language="en",
        )
    )
    assert admitted
    runs = RunService(database, clock=lambda: TRIGGER_AT)
    baseline_id, _ = runs.create(event_id, strategy_version="baseline.v1")
    research_id, _ = runs.create(event_id, strategy_version="research.v1")

    baseline_snapshot, _ = SnapshotService(database).freeze_for_run(
        baseline_id, event_id
    )
    research_snapshot, _ = SnapshotService(database).freeze_for_run(
        research_id, event_id
    )

    assert baseline_snapshot != research_snapshot
    with database.session() as session:
        baseline = session.get(SnapshotRecord, baseline_snapshot)
        research = session.get(SnapshotRecord, research_snapshot)
        assert baseline is not None and baseline.run_id == baseline_id
        assert research is not None and research.run_id == research_id
        assert baseline.snapshot_hash == research.snapshot_hash


def test_evidence_is_normalized_persisted_and_idempotent(tmp_path: Path) -> None:
    database, _ = _database(tmp_path)
    service = ResearchEvidenceService(database)
    candidate = _candidate()

    first = service.accept_candidates(
        run_id="run-1",
        capability_id="market.crypto_derivatives",
        candidates=[candidate],
        requirements={candidate.requirement_id: _requirement()},
        cutoff_at=DECISION_AT,
    )
    second = service.accept_candidates(
        run_id="run-1",
        capability_id="market.crypto_derivatives",
        candidates=[candidate],
        requirements={candidate.requirement_id: _requirement()},
        cutoff_at=DECISION_AT,
    )

    assert first[0].quality == "accepted"
    assert first[0].freshness_status == "fresh"
    assert second == first
    assert service.list_run_evidence("run-1") == first


def test_stale_evidence_is_preserved_but_not_marked_accepted(tmp_path: Path) -> None:
    database, _ = _database(tmp_path)
    service = ResearchEvidenceService(database)
    candidate = _candidate(
        published_at=TRIGGER_AT - timedelta(days=1),
        observed_at=DECISION_AT - timedelta(seconds=5),
        received_at=DECISION_AT - timedelta(seconds=4),
    )

    result = service.accept_candidates(
        run_id="run-1",
        capability_id="market.crypto_derivatives",
        candidates=[candidate],
        requirements={candidate.requirement_id: _requirement()},
        cutoff_at=DECISION_AT,
    )

    assert result[0].quality == "stale"
    assert result[0].freshness_status == "stale"


def test_future_evidence_is_rejected_before_persistence(tmp_path: Path) -> None:
    database, _ = _database(tmp_path)
    service = ResearchEvidenceService(database)
    candidate = _candidate(received_at=DECISION_AT + timedelta(seconds=1))

    with pytest.raises(ResearchCapabilityError) as raised:
        service.accept_candidates(
            run_id="run-1",
            capability_id="market.crypto_derivatives",
            candidates=[candidate],
            requirements={candidate.requirement_id: _requirement()},
            cutoff_at=DECISION_AT,
        )

    assert raised.value.error_code == "research_pit_violation"
    assert service.list_run_evidence("run-1") == []


def test_same_evidence_identity_cannot_be_rewritten(tmp_path: Path) -> None:
    database, _ = _database(tmp_path)
    service = ResearchEvidenceService(database)
    original = _candidate(evidence_id="evidence-stable-id")
    changed = _candidate(evidence_id="evidence-stable-id", excerpt="changed payload")
    requirements = {original.requirement_id: _requirement()}
    service.accept_candidates(
        run_id="run-1",
        capability_id="market.crypto_derivatives",
        candidates=[original],
        requirements=requirements,
        cutoff_at=DECISION_AT,
    )

    with pytest.raises(ValueError, match="research_evidence_identity_conflict"):
        service.accept_candidates(
            run_id="run-1",
            capability_id="market.crypto_derivatives",
            candidates=[changed],
            requirements=requirements,
            cutoff_at=DECISION_AT,
        )


def test_same_content_can_be_persisted_by_independent_research_runs(tmp_path: Path) -> None:
    database, trigger_snapshot_id = _database(tmp_path)
    with database.session() as session:
        session.add(
            RunRecord(
                run_id="run-2",
                event_id="event-1",
                snapshot_id=trigger_snapshot_id,
                status="running",
                strategy_version="candidate.v1",
                runtime_version="dsh-sdk-0.1.1rc1",
                created_at=TRIGGER_AT,
                updated_at=TRIGGER_AT,
            )
        )
    service = ResearchEvidenceService(database)
    first = _candidate(research_session_id="research-session-1")
    second = _candidate(research_session_id="research-session-2")
    requirements = {first.requirement_id: _requirement()}

    service.accept_candidates(
        run_id="run-1",
        capability_id="market.crypto_derivatives",
        candidates=[first],
        requirements=requirements,
        cutoff_at=DECISION_AT,
    )
    service.accept_candidates(
        run_id="run-2",
        capability_id="market.crypto_derivatives",
        candidates=[second],
        requirements=requirements,
        cutoff_at=DECISION_AT,
    )

    assert first.content_hash == second.content_hash
    assert first.evidence_id != second.evidence_id
    accepted_update = {"quality": "accepted", "freshness_status": "fresh"}
    assert service.list_run_evidence("run-1") == [
        first.model_copy(update=accepted_update)
    ]
    assert service.list_run_evidence("run-2") == [
        second.model_copy(update=accepted_update)
    ]


def test_decision_snapshot_is_distinct_linked_and_immutable(tmp_path: Path) -> None:
    database, trigger_snapshot_id = _database(tmp_path)
    service = ResearchEvidenceService(database)
    candidate = _candidate()
    accepted = service.accept_candidates(
        run_id="run-1",
        capability_id="market.crypto_derivatives",
        candidates=[candidate],
        requirements={candidate.requirement_id: _requirement()},
        cutoff_at=DECISION_AT,
    )

    decision = service.freeze_decision_snapshot(
        run_id="run-1",
        evidence_refs=[accepted[0].evidence_id],
        cutoff_at=DECISION_AT,
    )
    replayed = service.freeze_decision_snapshot(
        run_id="run-1",
        evidence_refs=[accepted[0].evidence_id],
        cutoff_at=DECISION_AT,
    )

    assert decision == replayed
    assert decision.snapshot_type == "decision"
    assert decision.snapshot_id != trigger_snapshot_id
    assert decision.parent_snapshot_id == trigger_snapshot_id
    assert decision.generation == 2
    with database.session() as session:
        run = session.get(RunRecord, "run-1")
        trigger = session.get(SnapshotRecord, trigger_snapshot_id)
        assert run is not None and run.decision_snapshot_id == decision.snapshot_id
        assert trigger is not None and trigger.snapshot_type == "trigger"
        assert trigger.generation == 1
        trigger_evidence = json.loads(trigger.evidence_json)
        decision_snapshot = session.get(SnapshotRecord, decision.snapshot_id)
        assert decision_snapshot is not None
        decision_evidence = json.loads(decision_snapshot.evidence_json)

    trigger_ref = trigger_evidence[0]["evidence_id"]
    assert decision.evidence_refs == [trigger_ref, accepted[0].evidence_id]
    assert {item["evidence_id"] for item in decision_evidence} == {
        trigger_ref,
        accepted[0].evidence_id,
    }
    assert next(item for item in decision_evidence if item["evidence_id"] == trigger_ref)[
        "text"
    ] == "A central-bank official discussed inflation risks."

    with pytest.raises(ValueError, match="decision_snapshot_already_frozen"):
        service.freeze_decision_snapshot(
            run_id="run-1",
            evidence_refs=[accepted[0].evidence_id, "different-evidence"],
            cutoff_at=DECISION_AT,
        )


def test_decision_snapshot_rejects_evidence_from_another_run(tmp_path: Path) -> None:
    database, trigger_snapshot_id = _database(tmp_path)
    with database.session() as session:
        session.add(
            RunRecord(
                run_id="run-2",
                event_id="event-1",
                snapshot_id=trigger_snapshot_id,
                status="running",
                strategy_version="candidate.v1",
                runtime_version="dsh-sdk-0.1.1rc1",
                created_at=TRIGGER_AT,
                updated_at=TRIGGER_AT,
            )
        )
    service = ResearchEvidenceService(database)
    candidate = _candidate()
    accepted = service.accept_candidates(
        run_id="run-2",
        capability_id="market.crypto_derivatives",
        candidates=[candidate],
        requirements={candidate.requirement_id: _requirement()},
        cutoff_at=DECISION_AT,
    )

    with pytest.raises(ValueError, match="decision_snapshot_evidence_run_mismatch"):
        service.freeze_decision_snapshot(
            run_id="run-1",
            evidence_refs=[accepted[0].evidence_id],
            cutoff_at=DECISION_AT,
        )
