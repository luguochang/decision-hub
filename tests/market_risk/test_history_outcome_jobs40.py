"""Real private scheduling/storage; the cold model boundary is synthetic only."""

import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select

from packages.kernel.decision_hub_kernel.application.history_outcome_jobs_v2 import (
    OFFSETS,
    HistoryOutcomeJobsV2,
    HistoryOutcomeJobUnconfirmed,
)
from packages.kernel.decision_hub_kernel.persistence.db import as_utc
from packages.kernel.decision_hub_kernel.persistence.history_outcome_v2_records import (
    HistoryOutcomeObservationV2Record as Observation,
)
from packages.kernel.decision_hub_kernel.persistence.history_outcome_v2_records import (
    HistoryOutcomeOpportunityV2Record as Opportunity,
)
from tests.market_risk.test_history_outcome_service40 import fixture40, insert_exact_1440


def first_opportunity(env):
    with env.service.commands._read_session() as session:
        return session.scalar(
            select(Opportunity).order_by(Opportunity.hour_at, Opportunity.scope_id)
        )


def publication(env, request="jobs-private"):
    protocol = env.service.register(request)
    jobs = HistoryOutcomeJobsV2(env.service)
    env.clock[0] = protocol.first_hour + timedelta(minutes=6)
    row = first_opportunity(env)
    result = jobs.process_one(protocol.protocol_id, "reconcile", opportunity_id=row.opportunity_id)
    assert result["outcome"] == "publication_saved"
    return protocol, jobs, row.opportunity_id


def test_reserved_partial_once_and_frozen_offsets_no_counter_reset(tmp_path, monkeypatch):
    env = fixture40(tmp_path, monkeypatch)
    protocol, jobs, identifier = publication(env)
    with env.service.commands._read_session() as session:
        row = session.get(Opportunity, identifier)
        assert row.attempts == 0 and row.generation == 1 and row.lease_owner is None
        end = as_utc(row.label_cutoff_at) + timedelta(days=1, minutes=1)
        deadline = as_utc(row.label_deadline_at)
    env.clock[0] = end + timedelta(seconds=OFFSETS[0])
    result = jobs.process_one(protocol.protocol_id, "observe", opportunity_id=identifier)
    assert result["outcome"] == "partial_saved" and result["attempt"] == 1
    with env.service.commands._read_session() as session:
        row = session.get(Opportunity, identifier)
        subjects = session.scalars(
            select(Observation).where(Observation.kind == "observation")
        ).all()
        assert len(subjects) == 1 and subjects[0].attempt == 1
        assert row.attempts == 1 and row.generation == 2 and row.lease_owner is None
        assert as_utc(row.next_due_at) == end + timedelta(seconds=OFFSETS[1])
        assert as_utc(row.label_deadline_at) == deadline and row.first_acceptance_id is None
    assert not jobs._fence(identifier).exists()
    receipts = tuple((env.service.archive.root / "job-receipts").glob("*.json"))
    assert len(receipts) == 2 and all(
        json.loads(path.read_bytes())["source_files"] for path in receipts
    )
    result = HistoryOutcomeJobsV2(env.service).process_one(
        protocol.protocol_id, "observe", opportunity_id=identifier
    )
    assert result["outcome"] == "no_due_opportunity"
    # Reuse the shared private 1440-minute raw builder once. No model/fit is run.
    env.clock[0] = end + timedelta(seconds=OFFSETS[1])
    with env.service.commands._read_session() as session:
        opportunity = session.get(Opportunity, identifier)
        cutoff = as_utc(opportunity.label_cutoff_at)
    insert_exact_1440(env, opportunity, cutoff)
    result = HistoryOutcomeJobsV2(env.service).process_one(
        protocol.protocol_id, "observe", opportunity_id=identifier
    )
    assert result["outcome"] == "accepted" and result["attempt"] == 2
    with env.service.commands._read_session() as session:
        row = session.get(Opportunity, identifier)
        subject, _, _ = env.service._read(session, "observation", row.first_accepted_observation_id)
        acceptance, _, _ = env.service._acceptance(session, row.first_acceptance_id)
        assert subject.accepted_at is None and subject.observed_count == 1440
        assert acceptance.independently_verified_at == env.clock[0]
        assert row.attempts == 2 and row.generation == 3 and row.state == "completed"
        assert row.lease_owner is None and not jobs._fence(identifier).exists()


def test_unknown_claim_commit_is_not_replayed_after_restart_or_expiry(tmp_path, monkeypatch):
    env = fixture40(tmp_path, monkeypatch)
    protocol, jobs, identifier = publication(env, "jobs-unknown")
    with env.service.commands._read_session() as session:
        row = session.get(Opportunity, identifier)
        env.clock[0] = as_utc(row.next_due_at)
    original = env.service.commands._write_session
    calls = []

    @contextmanager
    def ack_lost():
        calls.append(1)
        with original() as session:
            yield session
        raise OSError("synthetic actual post-COMMIT ACK loss")

    monkeypatch.setattr(env.service.commands, "_write_session", ack_lost)
    with pytest.raises(HistoryOutcomeJobUnconfirmed, match="claim_unconfirmed"):
        jobs.process_one(protocol.protocol_id, "observe", opportunity_id=identifier)
    assert calls == [1] and jobs._fence(identifier).exists()
    env.clock[0] += timedelta(hours=2)
    monkeypatch.setattr(env.service.commands, "_write_session", original)
    fresh = HistoryOutcomeJobsV2(env.service)
    with pytest.raises(HistoryOutcomeJobUnconfirmed, match="requires_reconciliation"):
        fresh.process_one(protocol.protocol_id, "observe", opportunity_id=identifier)
    with env.service.commands._read_session() as session:
        row = session.get(Opportunity, identifier)
        assert row.attempts == 1 and row.generation == 2 and row.lease_owner is not None
        assert not session.scalars(
            select(Observation).where(Observation.kind == "observation")
        ).all()


def test_cap_deadline_and_claim_issuer_preserve_attempts(tmp_path, monkeypatch):
    env = fixture40(tmp_path, monkeypatch)
    protocol, jobs, identifier = publication(env, "jobs-cap")
    with env.service.commands._read_session() as session:
        row = session.get(Opportunity, identifier)
        end = as_utc(row.label_cutoff_at) + timedelta(days=1, minutes=1)
    for attempt, offset in enumerate(OFFSETS, 1):
        env.clock[0] = end + timedelta(seconds=offset)
        result = jobs.process_one(protocol.protocol_id, "observe", opportunity_id=identifier)
        assert result["attempt"] == attempt
    env.clock[0] = end + timedelta(days=1, seconds=1)
    assert (
        jobs.process_one(protocol.protocol_id, "observe", opportunity_id=identifier)["outcome"]
        == "no_due_opportunity"
    )
    with env.service.commands._read_session() as session:
        row = session.get(Opportunity, identifier)
        assert row.attempts == 4 and row.generation == 5 and row.state == "blocked"
        assert row.lease_owner is None
    # Exact claim issuance cannot be borrowed by a caller or different job.
    other = first_opportunity(env)
    assert other.opportunity_id == identifier
    env.clock[0] = protocol.first_hour + timedelta(minutes=6)
    with env.service.commands._read_session() as session:
        target = session.scalars(
            select(Opportunity)
            .where(Opportunity.first_publication_id.is_(None))
            .order_by(Opportunity.hour_at, Opportunity.scope_id)
        ).first()
    claim = jobs.claim(protocol.protocol_id, "reconcile", opportunity_id=target.opportunity_id)
    assert claim is not None
    with pytest.raises(ValueError, match="issued_claim"):
        jobs._auth(replace(claim, opportunity_id=identifier))
