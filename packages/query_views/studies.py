"""Read models for durable study identity and discovery explanations."""

from __future__ import annotations

import json
import re
from typing import Literal

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.study import (
    AdmissionDecisionView,
    AdmissionListView,
    ReportRevisionView,
    StudyActivityView,
    StudyEntryProjectionView,
    StudyListView,
    StudyReportComparisonView,
    StudyTaskView,
    StudyTimelineView,
    StudyView,
)
from packages.kernel.decision_hub_kernel.application.study import study_view
from packages.kernel.decision_hub_kernel.persistence.db import (
    AdmissionDecisionRecord,
    AnalyticalOutlookRecord,
    ArtifactRecord,
    Database,
    ObservationRecord,
    ReportRevisionRecord,
    ReviewScheduleRecord,
    RunRecord,
    StudyRecord,
    as_utc,
)


def _manual_input_display_title(text: str | None) -> str | None:
    """Presentation only, based on an actual manual Run's original input.

    A stored Study instrument is intentionally not consulted: historical
    automatic event registration used the BTC default before relevance admission.
    """
    if not isinstance(text, str) or len(text) > 8192:
        return None
    scope = re.search(
        r"(?:核查|研究|复核|更新|观察)\s*(?:最新|当前|今日)?\s*BTC(?![A-Za-z0-9])",
        text, re.IGNORECASE,
    )
    if scope is None:
        # An affirmative sentence explicitly fixing the sole instrument is also
        # usable; the actual manual Run/input provenance remains authoritative.
        scope = re.search(
            r"(?:^|[。！？!?；;\n])\s*"
            r"(?:独立公开宏观研究(?:\s+[A-Za-z0-9][A-Za-z0-9_-]{0,31})?\s*[（(]"
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
            r"[）)]\s*[:：]\s*)?"
            r"以\s*BTC(?:-USDT(?:-SWAP)?)?(?![A-Za-z0-9_-])"
            r"\s*为\s*唯一(?:研究)?标的(?=$|[\s，,。；;！？!?：:])",
            text, re.IGNORECASE,
        )
    if scope is None or re.search(r"(?:不要|不应|无需|排除|不)\s*$", text[:scope.start()]):
        return None
    if re.search(r"黄金|\b(?:XAU|ETH|Ethereum|gold)\b", text, re.IGNORECASE):
        return None
    perpetual = "永续" in text or re.search(r"\b(?:perpetual|swap)\b", text, re.IGNORECASE)
    macro = "宏观" in text or re.search(r"\bmacro(?:economic)?\b", text, re.IGNORECASE)
    market = "BTC 永续" if perpetual else "BTC 市场"
    return f"{market}与宏观市场研究" if macro else f"{market}研究"


def _entry_queries(study_ids: list[str] | None = None):
    """Shared entry facts for whole-list filtering and page-bounded projection."""
    runs = (
        select(RunRecord.study_id, func.count().label("research_runs"))
        .where(RunRecord.strategy_version == "research.v1")
    )
    admissions = (
        select(
            AdmissionDecisionRecord.study_id,
            AdmissionDecisionRecord.decision,
            AdmissionDecisionRecord.reason_code,
            AdmissionDecisionRecord.run_id,
            AdmissionDecisionRecord.decided_at,
            func.row_number().over(
                partition_by=AdmissionDecisionRecord.study_id,
                order_by=(
                    AdmissionDecisionRecord.decided_at.desc(),
                    AdmissionDecisionRecord.decision_id.desc(),
                ),
            ).label("position"),
        )
        .where(AdmissionDecisionRecord.strategy_version == "research.v1")
    )
    if study_ids is not None:
        runs = runs.where(RunRecord.study_id.in_(study_ids))
        admissions = admissions.where(AdmissionDecisionRecord.study_id.in_(study_ids))
    return runs.group_by(RunRecord.study_id).subquery(), admissions.subquery()


def _entry_state(runs, admissions):
    # A later ignored event cannot erase previously registered research.
    return case(
        (func.coalesce(runs.c.research_runs, 0) > 0, "has_research"),
        (and_(admissions.c.decision.in_(("ignored", "blocked")),
              admissions.c.run_id.is_(None)), "collected_not_researched"),
        else_="unknown",
    )


def _entry_projections(
    session: Session, study_ids: list[str]
) -> dict[str, StudyEntryProjectionView]:
    """One page-bounded statement; no per-card detail or authoritative bundle reads."""
    if not study_ids:
        return {}
    runs, admissions = _entry_queries(study_ids)
    manual_inputs = (
        select(
            RunRecord.study_id,
            RunRecord.run_id,
            ObservationRecord.observation_id,
            func.substr(ObservationRecord.text, 1, 8193).label("text"),
            func.row_number().over(
                partition_by=RunRecord.study_id,
                order_by=(
                    RunRecord.created_at.asc(), RunRecord.run_id.asc(),
                    ObservationRecord.received_at.asc(), ObservationRecord.observation_id.asc(),
                ),
            ).label("position"),
        )
        .join(ObservationRecord, ObservationRecord.event_id == RunRecord.event_id)
        .where(
            RunRecord.study_id.in_(study_ids),
            RunRecord.strategy_version == "research.v1",
            RunRecord.admission_origin == "manual",
        )
        .subquery()
    )
    rows = session.execute(
        select(
            StudyRecord.study_id,
            func.coalesce(runs.c.research_runs, 0).label("research_runs"),
            _entry_state(runs, admissions).label("state"),
            admissions.c.decision,
            admissions.c.reason_code,
            admissions.c.run_id.label("admission_run_id"),
            manual_inputs.c.text,
            manual_inputs.c.run_id.label("title_run_id"),
            manual_inputs.c.observation_id,
        )
        .outerjoin(runs, runs.c.study_id == StudyRecord.study_id)
        .outerjoin(admissions, and_(
            admissions.c.study_id == StudyRecord.study_id, admissions.c.position == 1,
        ))
        .outerjoin(manual_inputs, and_(
            manual_inputs.c.study_id == StudyRecord.study_id, manual_inputs.c.position == 1,
        ))
        .where(StudyRecord.study_id.in_(study_ids))
    ).mappings()
    projections = {}
    for row in rows:
        count = row["research_runs"]
        title = _manual_input_display_title(row["text"])
        projections[row["study_id"]] = StudyEntryProjectionView(
            state=row["state"],
            research_runs=count,
            latest_admission_decision=row["decision"],
            latest_admission_reason=row["reason_code"],
            display_title=title,
            display_title_source_run_id=row["title_run_id"] if title else None,
            display_title_source_observation_id=row["observation_id"] if title else None,
        )
    return projections


class StudyReportComparisonBindingError(ValueError):
    """The original report pointer or ordinary Run/artifact binding is invalid."""


class StudyQueryService:
    def __init__(self, database: Database):
        self.database = database

    def list(
        self, *, market=None, q=None, view: Literal["research", "sources", "all"] = "all",
        limit=30, offset=0,
    ) -> StudyListView:
        if view not in {"research", "sources", "all"}:
            raise ValueError("study_list_view_invalid")
        with self.database.session() as session:
            latest = (
                session.query(
                    ReportRevisionRecord.study_id,
                    func.max(ReportRevisionRecord.created_at).label("latest_report_at"),
                )
                .group_by(ReportRevisionRecord.study_id)
                .subquery()
            )
            query = session.query(StudyRecord, latest.c.latest_report_at).outerjoin(
                latest, latest.c.study_id == StudyRecord.study_id
            )
            if view != "all":
                runs, admissions = _entry_queries()
                query = query.outerjoin(runs, runs.c.study_id == StudyRecord.study_id).outerjoin(
                    admissions, and_(admissions.c.study_id == StudyRecord.study_id,
                                     admissions.c.position == 1),
                )
                entry = "has_research" if view == "research" else "collected_not_researched"
                query = query.filter(_entry_state(runs, admissions) == entry)
            if market:
                query = query.filter(StudyRecord.market == market)
            if q:
                escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                query = query.filter(StudyRecord.title.ilike(f"%{escaped}%", escape="\\"))
            total = query.count()
            offset = min(offset, ((total - 1) // limit) * limit) if total else 0
            if view == "research":
                ordering = (latest.c.latest_report_at.is_(None).asc(),
                            latest.c.latest_report_at.desc(), StudyRecord.updated_at.desc(),
                            StudyRecord.study_id)
            elif view == "sources":
                ordering = (admissions.c.decided_at.desc(), StudyRecord.created_at.desc(),
                            StudyRecord.study_id)
            else:
                ordering = (func.coalesce(latest.c.latest_report_at, StudyRecord.updated_at).desc(),
                            StudyRecord.study_id)
            rows = (
                query.order_by(*ordering)
                .offset(offset)
                .limit(limit)
                .all()
            )
            entries = _entry_projections(session, [row.study_id for row, _ in rows])
            return StudyListView(
                items=[
                    StudyView(
                        **study_view(row), latest_report_at=as_utc(report_at),
                        entry_projection=entries[row.study_id],
                    )
                    for row, report_at in rows
                ],
                total=total,
                limit=limit,
                offset=offset,
            )

    def for_run(self, run_id: str) -> StudyView | None:
        with self.database.session() as session:
            run = session.get(RunRecord, run_id)
            if run is None:
                raise KeyError(run_id)
            study = session.get(StudyRecord, run.study_id) if run.study_id else None
            return (
                StudyView(
                    **study_view(study),
                    entry_projection=_entry_projections(session, [study.study_id])[study.study_id],
                )
                if study else None
            )

    def timeline(self, study_id: str, *, limit=30, offset=0) -> StudyTimelineView:
        with self.database.session() as session:
            study = session.get(StudyRecord, study_id)
            if study is None:
                raise KeyError(study_id)
            query = session.query(ReportRevisionRecord).filter_by(study_id=study_id)
            total = query.count()
            offset = min(offset, ((total - 1) // limit) * limit) if total else 0
            rows = (
                query.order_by(
                    ReportRevisionRecord.created_at.desc(), ReportRevisionRecord.revision_id.desc()
                )
                .offset(offset)
                .limit(limit)
                .all()
            )
            parents = {
                r.revision_id: r
                for r in session.query(ReportRevisionRecord).filter(
                    ReportRevisionRecord.revision_id.in_(
                        [r.previous_revision_id for r in rows if r.previous_revision_id]
                    )
                )
            }
            run_ids = {r.source_run_id for r in rows} | {r.source_run_id for r in parents.values()}
            outlooks = {}
            for outlook in session.query(AnalyticalOutlookRecord).filter(
                AnalyticalOutlookRecord.run_id.in_(run_ids)
            ):
                outlooks.setdefault(outlook.run_id, []).append(outlook)
            return StudyTimelineView(
                study=StudyView(
                    **study_view(study),
                    entry_projection=_entry_projections(session, [study_id])[study_id],
                ),
                activity=self._activity(session, study_id),
                total=total,
                limit=limit,
                offset=offset,
                revisions=[
                    ReportRevisionView(
                        revision_id=r.revision_id,
                        study_id=r.study_id,
                        source_run_id=r.source_run_id,
                        artifact_id=r.artifact_id,
                        previous_revision_id=r.previous_revision_id,
                        strategy_version=r.strategy_version,
                        input_fingerprint=r.input_fingerprint,
                        publication_status=r.publication_status,
                        change=json.loads(r.change_json),
                        direction_changes=direction_changes(
                            outlooks.get(r.source_run_id, []),
                            outlooks.get(parents[r.previous_revision_id].source_run_id, [])
                            if r.previous_revision_id in parents
                            else [],
                            has_previous=r.previous_revision_id is not None,
                        ),
                        identity_basis=r.identity_basis,
                        created_at=as_utc(r.created_at),
                        recorded_at=as_utc(r.recorded_at),
                    )
                    for r in rows
                ],
            )

    def comparison(self, study_id: str, revision_id: str) -> StudyReportComparisonView:
        """Read two original ordinary report revisions; never select a newer replacement."""
        with self.database.session() as session:
            study = session.get(StudyRecord, study_id)
            current = session.get(ReportRevisionRecord, revision_id)
            if study is None or current is None or current.study_id != study_id:
                raise KeyError(revision_id)

            def verify(row: ReportRevisionRecord) -> None:
                run = session.get(RunRecord, row.source_run_id)
                artifact = session.get(ArtifactRecord, row.artifact_id)
                if (
                    row.study_id != study_id
                    or row.strategy_version != "research.v1"
                    or run is None or artifact is None
                    or run.study_id != study_id
                    or run.strategy_version != row.strategy_version
                    or run.artifact_id != row.artifact_id
                    or artifact.run_id != row.source_run_id
                    or artifact.event_id != run.event_id
                    or as_utc(artifact.created_at) != as_utc(row.created_at)
                ):
                    raise StudyReportComparisonBindingError()

            def parent(row: ReportRevisionRecord) -> ReportRevisionRecord | None:
                if row.previous_revision_id is None:
                    return None
                previous = session.get(ReportRevisionRecord, row.previous_revision_id)
                if (
                    previous is None or previous.revision_id == row.revision_id
                    or previous.previous_revision_id == row.revision_id
                    or previous.previous_revision_id == previous.revision_id
                    or previous.study_id != study_id
                    or previous.strategy_version != row.strategy_version
                    or as_utc(previous.created_at) > as_utc(row.created_at)
                ):
                    raise StudyReportComparisonBindingError()
                verify(previous)
                return previous

            verify(current)
            previous = parent(current)
            run_ids = {row.source_run_id for row in (current, previous) if row is not None}
            outlooks = {}
            for outlook in session.query(AnalyticalOutlookRecord).filter(
                AnalyticalOutlookRecord.run_id.in_(run_ids)
            ):
                outlooks.setdefault(outlook.run_id, []).append(outlook)

            def view(row: ReportRevisionRecord, prior: ReportRevisionRecord | None) -> ReportRevisionView:
                try:
                    return ReportRevisionView(
                        revision_id=row.revision_id, study_id=row.study_id,
                        source_run_id=row.source_run_id, artifact_id=row.artifact_id,
                        previous_revision_id=row.previous_revision_id,
                        strategy_version=row.strategy_version, input_fingerprint=row.input_fingerprint,
                        publication_status=row.publication_status, change=json.loads(row.change_json),
                        direction_changes=direction_changes(
                            outlooks.get(row.source_run_id, []),
                            outlooks.get(prior.source_run_id, []) if prior is not None else [],
                            has_previous=row.previous_revision_id is not None,
                        ),
                        identity_basis=row.identity_basis,
                        created_at=as_utc(row.created_at), recorded_at=as_utc(row.recorded_at),
                    )
                except (TypeError, ValueError) as error:
                    raise StudyReportComparisonBindingError() from error

            return StudyReportComparisonView(
                schema_version="study-report-comparison-view.v1", study_id=study_id,
                current=view(current, previous),
                previous=view(previous, None) if previous is not None else None,
            )

    def _activity(self, session, study_id: str) -> StudyActivityView:
        runs = session.query(RunRecord).filter_by(study_id=study_id, strategy_version="research.v1")
        recent = runs.order_by(RunRecord.created_at.desc(), RunRecord.run_id.desc()).limit(6).all()
        artifacts = {
            a.artifact_id: a.run_id
            for a in session.query(ArtifactRecord).filter(
                ArtifactRecord.artifact_id.in_([r.artifact_id for r in recent if r.artifact_id])
            )
        }
        plans = (
            session.query(ReviewScheduleRecord)
            .join(RunRecord, ReviewScheduleRecord.parent_run_id == RunRecord.run_id)
            .filter(
                RunRecord.study_id == study_id,
                RunRecord.strategy_version == "research.v1",
                ReviewScheduleRecord.status == "planned",
            )
        )
        next_plan = plans.order_by(ReviewScheduleRecord.trigger_at).first()
        admission = (
            session.query(AdmissionDecisionRecord)
            .filter_by(study_id=study_id, strategy_version="research.v1")
            .order_by(
                AdmissionDecisionRecord.decided_at.desc(),
                AdmissionDecisionRecord.decision_id.desc(),
            )
            .first()
        )
        return StudyActivityView(
            research_runs=runs.count(),
            active_runs=runs.filter(RunRecord.status.in_(["admitted", "running"])).count(),
            recent_runs=[
                StudyTaskView(
                    run_id=r.run_id,
                    status=r.status,
                    admission_origin=r.admission_origin,
                    created_at=as_utc(r.created_at),
                    updated_at=as_utc(r.updated_at),
                    error_code=r.error_code,
                    report_available=bool(
                        r.artifact_id and artifacts.get(r.artifact_id) == r.run_id
                    ),
                )
                for r in recent
            ],
            pending_review_plans=plans.count(),
            next_review_at=as_utc(next_plan.trigger_at) if next_plan else None,
            latest_admission=AdmissionDecisionView(
                decision_id=admission.decision_id,
                attempt_id=admission.attempt_id,
                event_id=admission.event_id,
                study_id=admission.study_id,
                source_id=admission.source_id,
                strategy_version=admission.strategy_version,
                policy_version=admission.policy_version,
                decision=admission.decision,
                reason_code=admission.reason_code,
                details=json.loads(admission.details_json),
                run_id=admission.run_id,
                decided_at=as_utc(admission.decided_at),
            )
            if admission
            else None,
        )

    def decisions(
        self, *, source_id=None, study_id=None, decision=None, limit=30, offset=0
    ) -> AdmissionListView:
        with self.database.session() as session:
            query = session.query(AdmissionDecisionRecord)
            if source_id:
                query = query.filter_by(source_id=source_id)
            if study_id:
                query = query.filter_by(study_id=study_id)
            if decision:
                query = query.filter_by(decision=decision)
            total = query.count()
            offset = min(offset, ((total - 1) // limit) * limit) if total else 0
            rows = (
                query.order_by(
                    AdmissionDecisionRecord.decided_at.desc(), AdmissionDecisionRecord.decision_id
                )
                .offset(offset)
                .limit(limit)
            )
            items = [
                AdmissionDecisionView(
                    decision_id=r.decision_id,
                    attempt_id=r.attempt_id,
                    event_id=r.event_id,
                    study_id=r.study_id,
                    source_id=r.source_id,
                    strategy_version=r.strategy_version,
                    policy_version=r.policy_version,
                    decision=r.decision,
                    reason_code=r.reason_code,
                    details=json.loads(r.details_json),
                    run_id=r.run_id,
                    decided_at=as_utc(r.decided_at),
                )
                for r in rows
            ]
            return AdmissionListView(items=items, total=total, limit=limit, offset=offset)


def direction_changes(current, previous, *, has_previous):
    """Compare only matched methods and scopes; missing versions are not unchanged."""

    def key(row):
        return row.method_family, row.instrument, row.horizon

    before, after = {key(row): row for row in previous}, {key(row): row for row in current}
    changes = []
    for identity in sorted(set(before) | set(after)):
        old, new = before.get(identity), after.get(identity)
        row = new or old
        status = (
            "missing_current"
            if new is None
            else "first_recorded"
            if not has_previous
            else "missing_previous"
            if old is None
            else "method_changed"
            if old.method_version != new.method_version
            else "compared"
        )
        changes.append(
            dict(
                method_family=row.method_family,
                method_version=row.method_version,
                instrument=row.instrument,
                horizon=row.horizon,
                previous_action=old.action if old else None,
                current_action=new.action if new else None,
                comparison_status=status,
                changed=old.action != new.action if status == "compared" else None,
            )
        )
    return changes
