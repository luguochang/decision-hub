"""Recover newly deferred official RSS admissions without replaying source polls.

The 24-hour bound is this service's recovery window, not source freshness or
permission. Originals and their PIT stay immutable; a separate job owns slots.
Calendar, legacy and caller-created recovery contracts are not enrolled.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.models import SourceManifest, TextEnvelope
from packages.kernel.decision_hub_kernel.application.market_scope import require_research_scope
from packages.kernel.decision_hub_kernel.application.research_tracking import (
    research_admission_guard_in_session,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.study import stable_id
from packages.kernel.decision_hub_kernel.persistence.db import (
    AdmissionDecisionRecord,
    Database,
    DiscoveryAdmissionRecoveryJobRecord,
    EventRecord,
    ObservationRecord,
    RunRecord,
    StudyRecord,
    as_utc,
)

TEMPORARY_REASONS = frozenset(
    {
        "study_research_active",
        "research_admission_budget_exhausted",
        "study_host_terminal_unknown",
    }
)
RECOVERY_SOURCES = frozenset({"fed-press", "fed-speeches", "bls-releases", "bea-news"})
RECOVERY_WINDOW = timedelta(hours=24)
RECHECK_INTERVAL = timedelta(seconds=60)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def policy_snapshot(selector: object) -> dict | None:
    # This phase enrolls the existing concrete deterministic policy only.
    from packages.provider_adapters.research.discovery import CryptoMacroDiscoveryPolicy

    if not isinstance(selector, CryptoMacroDiscoveryPolicy):
        return None
    snapshot = {
        "policy_version": selector.policy_version,
        "research_strategy": selector.research_strategy,
        "source_ids": sorted(selector.source_ids),
        "high_impact_terms": list(selector.high_impact_terms),
        "max_daily_research": selector.max_daily_research,
        "max_study_research_per_hour": selector.max_study_research_per_hour,
        "include_baseline": selector.include_baseline,
    }
    families = selector.source_title_prefixes_snapshot()
    # Leave legacy v2/v3 contracts byte exact when no family rules exist.
    if families:
        snapshot["source_title_prefixes"] = families
    return snapshot


class RecoveryContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["discovery-admission-recovery.v1"]
    root_decision_id: str
    job_id: str
    event_id: str
    study_id: str
    study_scope_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str
    strategy_version: Literal["research.v1"]
    observation_id: str
    observation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    envelope_json: str
    source_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_json: str
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    original_reason: str
    created_at: datetime
    expires_at: datetime


def _observation_body(row: ObservationRecord) -> dict:
    fields = (
        "observation_id",
        "event_id",
        "source_id",
        "source_type",
        "language",
        "text",
        "content_hash",
        "source_url",
        "event_hint",
        "revision_of",
    )
    return {
        **{name: getattr(row, name) for name in fields},
        **{
            name: as_utc(getattr(row, name)).isoformat() if getattr(row, name) else None
            for name in ("published_at", "observed_at", "received_at", "scheduled_at")
        },
    }


def _study_scope(row: StudyRecord) -> dict:
    return {
        name: getattr(row, name)
        for name in (
            "study_id",
            "scope_key",
            "market",
            "instrument",
            "horizon_scope",
            "question_key",
            "identity_basis",
        )
    }


def enroll_recovery_in_session(
    session: Session,
    *,
    decision_id: str,
    event_id: str,
    study_id: str,
    idempotency_key: str,
    envelope: TextEnvelope,
    reason: str,
    policy_version: str,
    context: dict | None,
    now: datetime,
) -> dict | None:
    if (
        reason not in TEMPORARY_REASONS
        or context is None
        or envelope.source_id not in RECOVERY_SOURCES
        or envelope.source_type.value != "official_feed"
        or envelope.macro_event is not None
        or envelope.scheduled_at is not None
        or envelope.published_at is None
    ):
        return None
    manifest = SourceManifest.model_validate(context["manifest"])
    policy = context["policy"]
    if (
        manifest.source_id != envelope.source_id
        or not manifest.enabled
        or manifest.source_type != "official_feed"
        or manifest.authority_level != "official"
        or policy["policy_version"] != policy_version
        or policy["research_strategy"] != "research.v1"
        or envelope.source_id not in policy["source_ids"]
        or not envelope.published_at <= envelope.received_at <= now
        or envelope.received_at + RECOVERY_WINDOW <= now
    ):
        return None
    original = session.scalar(
        select(ObservationRecord).where(
            ObservationRecord.event_id == event_id,
            ObservationRecord.content_hash == envelope.content_hash,
            ObservationRecord.source_id == envelope.source_id,
        )
    )
    if original is None or _hash(original.text) != envelope.content_hash:
        return None
    study = session.get(StudyRecord, study_id)
    if study is None or study.status != "active":
        return None
    require_research_scope(study)
    # Same raw text can occur on another poll. Only the first admitted PIT is
    # eligible: don't overwrite it with a replayed observation's later times.
    if any(
        as_utc(getattr(original, field)) != getattr(envelope, field)
        for field in ("observed_at", "published_at", "received_at", "scheduled_at")
    ):
        return None
    job_id = stable_id("discovery_recovery", event_id, study_id, "research.v1", idempotency_key)
    if session.get(DiscoveryAdmissionRecoveryJobRecord, job_id) is not None:
        return None
    contract = RecoveryContract(
        schema_version="discovery-admission-recovery.v1",
        root_decision_id=decision_id,
        job_id=job_id,
        event_id=event_id,
        study_id=study_id,
        idempotency_key=idempotency_key,
        study_scope_sha256=_hash(_json(_study_scope(study))),
        strategy_version="research.v1",
        observation_id=original.observation_id,
        observation_sha256=_hash(_json(_observation_body(original))),
        envelope_json=envelope.model_dump_json(),
        source_manifest_sha256=_hash(_json(manifest.model_dump(mode="json"))),
        policy_json=_json(policy),
        policy_sha256=_hash(_json(policy)),
        original_reason=reason,
        created_at=now,
        expires_at=envelope.received_at + RECOVERY_WINDOW,
    )
    session.execute(
        insert(DiscoveryAdmissionRecoveryJobRecord)
        .values(
            job_id=job_id,
            root_decision_id=decision_id,
            status="pending",
            attempts=0,
            next_check_at=now + RECHECK_INTERVAL,
            expires_at=contract.expires_at,
            terminal_decision_id=None,
            created_at=now,
            updated_at=now,
            contract_sha256=_hash(_json(contract.model_dump(mode="json"))),
        )
        .on_conflict_do_nothing()
    )
    return contract.model_dump(mode="json")


class DiscoveryRecoveryService:
    def __init__(self, database: Database, runs: RunService) -> None:
        self.database, self.runs = database, runs

    def recover_due(
        self, *, registry, selector, validate_envelope, clock: Callable[[], datetime], limit=8
    ) -> int:
        if not 1 <= limit <= 8:
            raise ValueError("discovery_recovery_batch_invalid")
        policy = policy_snapshot(selector)
        if policy is None:
            return 0
        now = clock()
        # No source fetching or model work is performed here. The read closes
        # before each bounded writer, which rechecks every durable input.
        with self.database.session() as session:
            jobs = list(
                session.scalars(
                    select(DiscoveryAdmissionRecoveryJobRecord)
                    .where(
                        DiscoveryAdmissionRecoveryJobRecord.status == "pending",
                        DiscoveryAdmissionRecoveryJobRecord.next_check_at <= now,
                    )
                    .order_by(
                        DiscoveryAdmissionRecoveryJobRecord.next_check_at,
                        DiscoveryAdmissionRecoveryJobRecord.created_at,
                        DiscoveryAdmissionRecoveryJobRecord.job_id,
                    )
                    .limit(limit)
                )
            )
            roots = {
                row.decision_id: row
                for row in session.scalars(
                    select(AdmissionDecisionRecord).where(
                        AdmissionDecisionRecord.decision_id.in_(
                            item.root_decision_id for item in jobs
                        )
                    )
                )
            }
        recovered = 0
        for frozen_job in jobs:
            root_original = roots.get(frozen_job.root_decision_id)
            if root_original is None:
                raise ValueError("discovery_recovery_original_missing")
            original_details_hash = _hash(root_original.details_json)
            try:
                contract = RecoveryContract.model_validate(
                    json.loads(root_original.details_json)["discovery_recovery"]
                )
                envelope = TextEnvelope.model_validate_json(contract.envelope_json)
                saved_policy = json.loads(contract.policy_json)
                contract_hash = _hash(_json(contract.model_dump(mode="json")))
                prepared = (contract, envelope, saved_policy, contract_hash)
            except (ValueError, TypeError, KeyError):
                prepared = None
            with self.database.session() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                # A wait for the SQLite writer cannot carry an old eligibility
                # time across the service's recovery deadline.
                now = clock()
                job = session.get(DiscoveryAdmissionRecoveryJobRecord, frozen_job.job_id)
                if job is None or job.status != "pending" or as_utc(job.next_check_at) > now:
                    continue
                root = session.get(AdmissionDecisionRecord, job.root_decision_id)
                if root is None:
                    raise ValueError("discovery_recovery_original_missing")
                if _hash(root.details_json) != original_details_hash:
                    self._finish(
                        session, job, root, now, "blocked", "discovery_recovery_original_changed"
                    )
                    continue
                if prepared is None:
                    self._finish(
                        session, job, root, now, "blocked", "discovery_recovery_original_invalid"
                    )
                    continue
                contract, envelope, saved_policy, contract_hash = prepared
                try:
                    if (
                        root.decision != "blocked"
                        or root.reason_code not in TEMPORARY_REASONS
                        or root.run_id is not None
                        or root.reason_code != contract.original_reason
                        or root.decision_id != contract.root_decision_id
                        or root.event_id != contract.event_id
                        or root.study_id != contract.study_id
                        or root.source_id != envelope.source_id
                        or root.strategy_version != contract.strategy_version
                        or root.policy_version != saved_policy["policy_version"]
                        or job.job_id != contract.job_id
                        or as_utc(root.decided_at) != contract.created_at
                        or as_utc(job.created_at) != contract.created_at
                        or job.contract_sha256 != contract_hash
                        or as_utc(job.expires_at) != contract.expires_at
                        or _hash(contract.policy_json) != contract.policy_sha256
                        or contract.idempotency_key
                        != f"source:{envelope.source_id}:{envelope.content_hash}:research.v1"
                        or envelope.source_id not in RECOVERY_SOURCES
                        or envelope.macro_event is not None
                        or envelope.scheduled_at is not None
                        or envelope.published_at is None
                        or not envelope.published_at
                        <= envelope.received_at
                        <= contract.created_at
                        <= now
                        or contract.expires_at != envelope.received_at + RECOVERY_WINDOW
                    ):
                        raise ValueError("discovery_recovery_contract_invalid")
                except (ValueError, TypeError, KeyError):
                    self._finish(
                        session, job, root, now, "blocked", "discovery_recovery_original_invalid"
                    )
                    continue
                original = session.get(ObservationRecord, contract.observation_id)
                event = session.get(EventRecord, contract.event_id)
                study = session.get(StudyRecord, contract.study_id)
                if (
                    original is None
                    or event is None
                    or study is None
                    or original.event_id != contract.event_id
                    or _hash(_json(_observation_body(original))) != contract.observation_sha256
                    or _hash(original.text) != envelope.content_hash
                    or original.text != envelope.raw_text
                    or as_utc(event.received_at) != envelope.received_at
                    or as_utc(event.occurred_at) != envelope.observed_at
                    or event.status != "admitted"
                    or _hash(_json(_study_scope(study))) != contract.study_scope_sha256
                ):
                    self._finish(
                        session, job, root, now, "blocked", "discovery_recovery_original_changed"
                    )
                    continue
                try:
                    manifest = registry.get(root.source_id).manifest
                    validate_envelope(manifest, envelope)
                    if (
                        _hash(_json(manifest.model_dump(mode="json")))
                        != contract.source_manifest_sha256
                    ):
                        raise ValueError("discovery_recovery_source_changed")
                    if _json(policy) != contract.policy_json:
                        raise ValueError("discovery_recovery_policy_changed")
                    if not manifest.enabled:
                        raise ValueError("source_disabled")
                    require_research_scope(study)
                except (ValueError, KeyError) as exc:
                    reason = str(exc)
                    if reason not in {
                        "discovery_recovery_source_changed",
                        "discovery_recovery_policy_changed",
                        "source_disabled",
                        "study_scope_not_enabled",
                        "study_scope_horizon_not_enabled",
                    }:
                        reason = "discovery_recovery_source_invalid"
                    self._finish(session, job, root, now, "blocked", reason)
                    continue
                # A paused Study closes this pending recovery; a future owner
                # resume doesn't retroactively authorize this source decision.
                if study.status != "active":
                    self._finish(session, job, root, now, "blocked", "study_paused")
                    continue
                if now >= contract.expires_at:
                    self._finish(session, job, root, now, "ignored", "discovery_recovery_expired")
                    continue
                existing = session.scalar(
                    select(RunRecord).where(
                        RunRecord.idempotency_key == contract.idempotency_key,
                    )
                )
                if existing is not None:
                    if (existing.event_id, existing.study_id, existing.strategy_version) != (
                        contract.event_id,
                        contract.study_id,
                        contract.strategy_version,
                    ):
                        self._finish(
                            session,
                            job,
                            root,
                            now,
                            "blocked",
                            "discovery_recovery_run_scope_mismatch",
                        )
                        continue
                    self._finish(
                        session,
                        job,
                        root,
                        now,
                        "merged",
                        "same_input_already_admitted",
                        existing.run_id,
                    )
                    continue
                guard = research_admission_guard_in_session(
                    session,
                    study_id=contract.study_id,
                    now=now,
                    max_daily_research=saved_policy["max_daily_research"],
                    max_study_research_per_hour=saved_policy["max_study_research_per_hour"],
                )
                if guard.reason_code in TEMPORARY_REASONS:
                    job.next_check_at = min(now + RECHECK_INTERVAL, contract.expires_at)
                    job.attempts += 1
                    job.updated_at = now
                    continue
                if guard.reason_code is not None:
                    self._finish(session, job, root, now, "blocked", guard.reason_code)
                    continue
                if clock() >= contract.expires_at:
                    self._finish(
                        session, job, root, clock(), "ignored", "discovery_recovery_expired"
                    )
                    continue
                run_id, created = self.runs.create(
                    contract.event_id,
                    idempotency_key=contract.idempotency_key,
                    strategy_version=contract.strategy_version,
                    admission_origin="automatic",
                    study_id=contract.study_id,
                    session=session,
                )
                self._finish(
                    session,
                    job,
                    root,
                    now,
                    "admitted" if created else "merged",
                    "discovery_recovery_admitted" if created else "same_input_already_admitted",
                    run_id,
                )
                recovered += int(created)
        return recovered

    @staticmethod
    def _finish(session, job, root, now, decision, reason, run_id=None) -> None:
        decision_id = stable_id("admission_recovery", job.job_id)
        details = {
            "recovery_of": root.decision_id,
            "recovery_job_id": job.job_id,
            "original_reason": root.reason_code,
            "recovery_attempts": job.attempts + 1,
            "recovery_scope": "official_rss_new_contract_only",
        }
        session.execute(
            insert(AdmissionDecisionRecord)
            .values(
                decision_id=decision_id,
                attempt_id=root.attempt_id,
                event_id=root.event_id,
                study_id=root.study_id,
                source_id=root.source_id,
                strategy_version=root.strategy_version,
                policy_version=root.policy_version,
                decision=decision,
                reason_code=reason,
                details_json=json.dumps(details, ensure_ascii=False),
                run_id=run_id,
                decided_at=now,
            )
            .on_conflict_do_nothing()
        )
        job.status = "completed" if run_id else "closed"
        job.terminal_decision_id = decision_id
        job.attempts += 1
        job.updated_at = now
