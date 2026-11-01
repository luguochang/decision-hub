from __future__ import annotations

import json
from datetime import datetime
from typing import cast

from sqlalchemy import select, text
from sqlalchemy.dialects.sqlite import insert

from packages.contracts_py.decision_hub_contracts.models import TextEnvelope
from packages.kernel.decision_hub_kernel.application.discovery_recovery import (
    enroll_recovery_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_tracking import (
    research_admission_guard_in_session,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.study import stable_id, study_for_event
from packages.kernel.decision_hub_kernel.persistence.db import (
    AdmissionDecisionRecord,
    Database,
    EventOccurrenceRecord,
    EventWatchRecord,
    RunRecord,
    StudyRecord,
    as_utc,
)


class DiscoveryAdmissionService:
    def __init__(self, database: Database, runs: RunService):
        self.database, self.runs = database, runs

    def decide(
        self,
        *,
        attempt_id: str,
        event_id: str,
        envelope: TextEnvelope,
        strategy: str,
        selected: bool,
        reason: str,
        policy_version: str,
        details: dict[str, object],
        now: datetime,
        max_daily_research: int = 12,
        max_study_research_per_hour: int = 2,
        recovery_context: dict | None = None,
    ) -> tuple[str | None, bool, str]:
        """Budget check, durable decision and Run admission share one write transaction."""
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            study_id = study_for_event(session, event_id, now)
            # study_for_event has created/bound this row in the same writer.
            study = cast(StudyRecord, session.get(StudyRecord, study_id))
            decision, run_id, created = "ignored", None, False
            decision_event_id = event_id
            key = f"source:{envelope.source_id}:{envelope.content_hash}" + (
                f":{strategy}" if strategy != "baseline.v1" else ""
            )
            if envelope.macro_event:
                if envelope.macro_event.status == "cancelled":
                    selected, reason = False, "official_event_cancelled"
                elif (
                    envelope.macro_event.time_precision != "exact"
                    and envelope.macro_event.status == "scheduled"
                ):
                    selected, reason = False, "calendar_date_without_release_time"
                elif envelope.macro_event.status == "scheduled":
                    # The admitted calendar event has an occurrence binding.
                    occurrence = cast(
                        EventOccurrenceRecord, session.get(EventOccurrenceRecord, event_id)
                    )
                    # Text-only corrections at the same instant reuse the existing
                    # schedule. A changed instant owns a new auditable event/watch.
                    previous_watch = session.scalar(
                        select(EventWatchRecord)
                        .join(
                            EventOccurrenceRecord,
                            EventOccurrenceRecord.event_id == EventWatchRecord.event_id,
                        )
                        .where(
                            EventOccurrenceRecord.occurrence_id == occurrence.occurrence_id,
                            EventWatchRecord.scheduled_at == envelope.scheduled_at,
                            EventWatchRecord.status != "cancelled",
                        )
                        .order_by(EventWatchRecord.created_at)
                        .limit(1)
                    )
                    if previous_watch:
                        event_id = previous_watch.event_id
                    key = stable_id(
                        "source_calendar",
                        envelope.source_id,
                        occurrence.occurrence_id,
                        str(as_utc(envelope.scheduled_at)),
                        strategy,
                    )
            existing = session.scalar(select(RunRecord).where(RunRecord.idempotency_key == key))
            if (
                existing
                and existing.status == "cancelled"
                and existing.error_code == "calendar_schedule_superseded"
                and envelope.macro_event
                and envelope.macro_event.status == "scheduled"
            ):
                key = stable_id("calendar_reinstated", key, envelope.content_hash)
                existing = session.scalar(select(RunRecord).where(RunRecord.idempotency_key == key))
            if selected and existing:
                decision, reason, run_id = "merged", "same_input_already_admitted", existing.run_id
                event_id = existing.event_id
            elif selected and study.status != "active":
                decision, reason, selected = "blocked", "study_paused", False
            elif selected and strategy == "research.v1":
                guard = research_admission_guard_in_session(
                    session,
                    study_id=study_id,
                    now=now,
                    max_daily_research=max_daily_research,
                    max_study_research_per_hour=max_study_research_per_hour,
                )
                details = {**details, **guard.details}
                if guard.reason_code is not None:
                    decision, reason, selected = "blocked", guard.reason_code, False
            if selected and run_id is None:
                run_id, created = self.runs.create(
                    event_id,
                    idempotency_key=key,
                    strategy_version=strategy,
                    admission_origin="automatic",
                    available_at=envelope.scheduled_at
                    if not envelope.macro_event or envelope.macro_event.status == "scheduled"
                    else None,
                    study_id=study_id,
                    session=session,
                )
                decision = "admitted" if created else "merged"
            decision_id = stable_id("admission", attempt_id, decision_event_id, strategy)
            if strategy == "research.v1" and decision == "blocked":
                recovery = enroll_recovery_in_session(
                    session,
                    decision_id=decision_id,
                    event_id=decision_event_id,
                    study_id=study_id,
                    idempotency_key=key,
                    envelope=envelope,
                    reason=reason,
                    policy_version=policy_version,
                    context=recovery_context,
                    now=now,
                )
                if recovery is not None:
                    details = {**details, "discovery_recovery": recovery}
            session.execute(
                insert(AdmissionDecisionRecord)
                .values(
                    decision_id=decision_id,
                    attempt_id=attempt_id,
                    event_id=decision_event_id,
                    study_id=study_id,
                    source_id=envelope.source_id,
                    strategy_version=strategy,
                    policy_version=policy_version,
                    decision=decision,
                    reason_code=reason,
                    details_json=json.dumps(details, ensure_ascii=False),
                    run_id=run_id,
                    decided_at=now,
                )
                .on_conflict_do_nothing()
            )
            return run_id, created, event_id
