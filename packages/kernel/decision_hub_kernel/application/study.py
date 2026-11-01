from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application.market_scope import (
    DEFAULT_RESEARCH_INSTRUMENT,
    DEFAULT_RESEARCH_MARKET,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord,
    Database,
    EventOccurrenceRecord,
    EventRecord,
    ForecastRecord,
    MacroEventRecord,
    MacroEventVintageRecord,
    ObservationRecord,
    OccurrenceRecord,
    ReportRevisionRecord,
    ResearchEvidenceRecord,
    RunRecord,
    StudyOccurrenceRecord,
    StudyRecord,
    as_utc,
    utcnow,
)


PERPETUAL_DECISION_RESEARCH_PROFILE = "perpetual_decision_v2"


def research_profile_for_study(study: StudyRecord | None) -> str | None:
    """Return a product profile only for the complete persisted Study identity."""
    if study is None:
        return None
    if (
        study.question_key == "perpetual-current-decision.v2"
        and study.identity_basis == "exact_perpetual_decision_v2"
        and study.market == "crypto"
        and study.instrument in {"BTC-USDT-SWAP", "ETH-USDT-SWAP"}
        and study.horizon_scope in {"4h", "24h", "72h"}
    ):
        return PERPETUAL_DECISION_RESEARCH_PROFILE
    return None


def stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()
    return f"{prefix}_{digest[:40]}"


def ensure_occurrence(
    session: Session, event_id: str, now: datetime, *, legacy: bool = False
) -> str:
    existing = session.get(EventOccurrenceRecord, event_id)
    if existing:
        return existing.occurrence_id
    event = session.get(EventRecord, event_id)
    observation = session.scalar(
        select(ObservationRecord)
        .where(ObservationRecord.event_id == event_id)
        .order_by(ObservationRecord.received_at.asc())
        .limit(1)
    )
    macro = session.scalar(select(MacroEventRecord).where(MacroEventRecord.event_id == event_id))
    if macro is None:
        macro = session.scalar(
            select(MacroEventRecord)
            .join(
                MacroEventVintageRecord,
                MacroEventVintageRecord.macro_event_id == MacroEventRecord.macro_event_id,
            )
            .where(MacroEventVintageRecord.event_id == event_id)
            .limit(1)
        )
    source_id = observation.source_id if observation else "unknown"
    if macro:
        identity = f"macro:{macro.source_id}:{macro.external_event_id}"
        # HTML date-derived keys are provisional, not a claimed stable meeting number.
        basis = (
            "calendar_key_provisional" if "calendar:" in macro.external_event_id else "source_uid"
        )
    elif observation and observation.source_url:
        identity, basis = f"url:{source_id}:{observation.source_url}", "source_url"
    else:
        identity, basis = f"event:{event_id}", "legacy_unknown" if legacy else "observation"
    occurrence_id = stable_id("occ", identity)
    session.execute(
        insert(OccurrenceRecord)
        .values(
            occurrence_id=occurrence_id,
            identity_key=hashlib.sha256(identity.encode()).hexdigest(),
            identity_basis=basis,
            family=macro.family if macro else (event.event_type if event else "unknown"),
            source_id=macro.source_id if macro else source_id,
            macro_event_id=macro.macro_event_id if macro else None,
            created_at=now,
        )
        .on_conflict_do_nothing()
    )
    session.execute(
        insert(EventOccurrenceRecord)
        .values(
            event_id=event_id,
            occurrence_id=occurrence_id,
            linked_at=now,
            basis=basis,
        )
        .on_conflict_do_nothing()
    )
    return occurrence_id


def ensure_study(
    session: Session,
    *,
    question_key: str,
    title: str,
    market: str,
    instrument: str,
    horizon_scope: str = "30m,24h,72h",
    identity_basis: str = "explicit",
    status: str = "active",
    now: datetime,
) -> str:
    scope = json.dumps([market, instrument, horizon_scope, question_key], ensure_ascii=False)
    scope_key = hashlib.sha256(scope.encode()).hexdigest()
    study_id = stable_id("study", scope)
    session.execute(
        insert(StudyRecord)
        .values(
            study_id=study_id,
            scope_key=scope_key,
            title=title[:512],
            market=market,
            instrument=instrument,
            horizon_scope=horizon_scope,
            question_key=question_key,
            identity_basis=identity_basis,
            status=status,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["scope_key"])
    )
    return study_id


def study_for_event(session: Session, event_id: str, now: datetime, *, legacy: bool = False) -> str:
    occurrence_id = ensure_occurrence(session, event_id, now, legacy=legacy)
    observation = session.scalar(
        select(ObservationRecord)
        .where(ObservationRecord.event_id == event_id)
        .order_by(ObservationRecord.received_at.asc())
        .limit(1)
    )
    latest_advice = bool(observation and observation.source_id == "decision-desk-latest-advice")
    known_scope = latest_advice or not legacy
    title = (
        "BTC 市场持续观察"
        if latest_advice
        else (
            (
                next(
                    (line.strip() for line in observation.text.splitlines() if line.strip()),
                    "研究主题",
                )
            )
            if observation
            else "历史研究（原始范围未明确）"
        )
    )
    study_id = ensure_study(
        session,
        question_key="btc-current-market" if latest_advice else occurrence_id,
        title=title,
        market=DEFAULT_RESEARCH_MARKET if known_scope else "unknown",
        instrument=DEFAULT_RESEARCH_INSTRUMENT if known_scope else "unknown",
        identity_basis="fixed_product_entry"
        if latest_advice
        else ("legacy_unknown" if legacy else "occurrence"),
        status="active" if known_scope else "paused",
        now=now,
    )
    session.execute(
        insert(StudyOccurrenceRecord)
        .values(
            study_id=study_id,
            occurrence_id=occurrence_id,
            linked_at=now,
        )
        .on_conflict_do_nothing()
    )
    return study_id


def bind_run_study(session: Session, run: RunRecord, now: datetime, *, legacy=False) -> str:
    if run.study_id:
        return run.study_id
    parent = session.get(RunRecord, run.parent_run_id) if run.parent_run_id else None
    run.study_id = (
        bind_run_study(session, parent, now, legacy=legacy)
        if parent
        else study_for_event(session, run.event_id, now, legacy=legacy)
    )
    return run.study_id


def record_revision(
    session: Session,
    run: RunRecord,
    artifact: ArtifactRecord,
    *,
    input_fingerprint: str | None,
    now: datetime,
    legacy: bool = False,
) -> str:
    revision_id = stable_id("revision", artifact.artifact_id)
    if session.get(ReportRevisionRecord, revision_id):
        return revision_id
    study_id = bind_run_study(session, run, now, legacy=legacy)
    previous = session.scalar(
        select(ReportRevisionRecord)
        .where(
            ReportRevisionRecord.study_id == study_id,
            ReportRevisionRecord.strategy_version == run.strategy_version,
            ReportRevisionRecord.created_at <= artifact.created_at,
        )
        .order_by(ReportRevisionRecord.created_at.desc(), ReportRevisionRecord.revision_id.desc())
        .limit(1)
    )
    old_artifact = session.get(ArtifactRecord, previous.artifact_id) if previous else None

    def evidence_hashes(run_id):
        return set(
            session.scalars(
                select(ResearchEvidenceRecord.content_hash).where(
                    ResearchEvidenceRecord.run_id == run_id,
                    ResearchEvidenceRecord.quality == "accepted",
                )
            )
        )

    current_hashes = evidence_hashes(run.run_id)
    previous_hashes = evidence_hashes(previous.source_run_id) if previous else set()
    change = {
        "summary_changed": old_artifact.summary != artifact.summary if old_artifact else None,
        "added_evidence_count": len(current_hashes - previous_hashes),
        "removed_evidence_count": len(previous_hashes - current_hashes) if previous else 0,
        "evidence_unit": "distinct_content_hash",
        "previous_summary": old_artifact.summary[:2000] if old_artifact else None,
        "current_summary": artifact.summary[:2000],
        "comparison_status": "compared" if previous else "first_recorded_revision",
    }
    session.add(
        ReportRevisionRecord(
            revision_id=revision_id,
            study_id=study_id,
            source_run_id=run.run_id,
            artifact_id=artifact.artifact_id,
            previous_revision_id=previous.revision_id if previous else None,
            strategy_version=run.strategy_version,
            input_fingerprint=input_fingerprint,
            publication_status=artifact.gate_status,
            change_json=json.dumps(change, ensure_ascii=False),
            identity_basis="legacy_projection" if legacy else "commit_transaction",
            created_at=artifact.created_at,
            recorded_at=now,
        )
    )
    return revision_id


def backfill_studies(database: Database) -> dict[str, int]:
    """Explicit offline projection. Original observations, reports and clocks stay immutable."""
    now = utcnow()
    with database.session() as session:
        for event in session.scalars(select(EventRecord).order_by(EventRecord.received_at)):
            ensure_occurrence(session, event.event_id, now, legacy=True)
        for run in session.scalars(select(RunRecord).order_by(RunRecord.created_at)):
            bind_run_study(session, run, now, legacy=True)
        session.flush()
        for artifact in session.scalars(
            select(ArtifactRecord).order_by(
                ArtifactRecord.created_at,
                ArtifactRecord.artifact_id,
            )
        ):
            run = session.get(RunRecord, artifact.run_id)
            if run is None:
                raise ValueError("artifact_run_missing")
            forecast = session.scalar(
                select(ForecastRecord)
                .where(
                    ForecastRecord.artifact_id == artifact.artifact_id,
                )
                .limit(1)
            )
            record_revision(
                session,
                run,
                artifact,
                input_fingerprint=forecast.input_fingerprint if forecast else None,
                now=now,
                legacy=True,
            )
            session.flush()
        return {
            table.__tablename__: session.query(table).count()
            for table in (
                StudyRecord,
                OccurrenceRecord,
                EventOccurrenceRecord,
                ReportRevisionRecord,
            )
        }


def study_view(row: StudyRecord) -> dict:
    return {
        "study_id": row.study_id,
        "title": row.title,
        "market": row.market,
        "instrument": row.instrument,
        "horizon_scope": row.horizon_scope,
        "identity_basis": row.identity_basis,
        "status": row.status,
        "created_at": as_utc(row.created_at),
        "updated_at": as_utc(row.updated_at),
    }
