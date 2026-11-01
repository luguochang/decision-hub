"""Separate descriptive research cohorts from published forecasts and trading P&L."""

from __future__ import annotations

import json
import math
import re
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import and_, case, event, func, or_
from sqlalchemy.orm import Session, SessionTransaction, defer, load_only

from packages.contracts_py.decision_hub_contracts.binding_contract_base import (
    shape_validation_scope,
)
from packages.evals.analytical_assessment import AnalyticalAssessment, assess_outlook_in_session
from packages.evals.assessment_basis import AssessmentJob as AssessmentJobView
from packages.evals.assessment_basis import read_valid_assessment
from packages.evals.prospective_observations import first_record_outlooks
from packages.evals.recipe_observations import (
    RecipeObservationCandidate,
    select_recipe_observations,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalAssessmentJobRecord as AssessmentJob,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalObservationAttemptRecord as Attempt,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalObservationRecord as Observation,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalOutlookRecord as Outlook,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    ExecutionPermissionRecord,
    PublicationDecisionRecord,
    RawCaptureRecord,
    as_utc,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive

type _MethodScope = tuple[str, str, str, str, str]
type _SelectionStatus = Literal["unchecked", "selected", "repeat", "unkeyed"]
_RECIPE_HASH = re.compile(r"[a-f0-9]{64}\Z")


def _recipe_hash(assessment: AnalyticalAssessment | None) -> str | None:
    if (assessment is not None and assessment.source_status == "verified"
        and assessment.method_status == "verified"
        and assessment.stable_recipe_hash is not None
        and _RECIPE_HASH.fullmatch(assessment.stable_recipe_hash)):
        return assessment.stable_recipe_hash
    return None


def _recipe_filters(filters: dict[str, Any]) -> tuple[dict[str, Any], str | None, str | None]:
    saved = dict(filters)
    recipe = saved.pop("stable_recipe_hash", None)
    status = saved.pop("recipe_status", None)
    if recipe is not None and (not isinstance(recipe, str) or not _RECIPE_HASH.fullmatch(recipe)):
        raise ValueError("analytical_recipe_hash_invalid")
    if status is not None and status != "unchecked":
        raise ValueError("analytical_recipe_status_invalid")
    if recipe is not None and status is not None:
        raise ValueError("analytical_recipe_filters_conflict")
    return saved, recipe, status


def _method_scope(row: Outlook) -> _MethodScope | None:
    if not row.study_id:
        return None
    return row.market, row.instrument, row.method_family, row.horizon, row.study_id


def _record_order(row: Any) -> tuple[datetime, str]:
    return cast(datetime, as_utc(row.recorded_at)), row.outlook_id


def outlook_view(row: Outlook, observation: Observation) -> dict[str, Any]:
    value: dict[str, Any] = {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name != "raw_payload_json"
    }
    for field in ("evidence_refs", "fact_version_refs", "exclusion_reasons"):
        value[field] = json.loads(value.pop(field + "_json"))
    for field in ("data_as_of", "window_start_at", "window_end_at", "recorded_at"):
        value[field] = as_utc(value[field])
    value["observation"] = {
        key: getattr(observation, key)
        for key in (
            "status",
            "attempt",
            "next_attempt_at",
            "error_code",
            "market_return_pct",
            "directional_return_pct",
            "direction_correct",
            "quality_status",
            "benchmark",
            "evaluated_at",
        )
    }
    for field in ("next_attempt_at", "evaluated_at"):
        value["observation"][field] = as_utc(value["observation"][field])
    correct = value["observation"]["direction_correct"]
    value["observation"]["direction_correct"] = None if correct is None else bool(correct)
    return value


def filter_query(query: Any, filters: dict[str, Any]) -> Any:
    for key, value in filters.items():
        if key in {"stable_recipe_hash", "recipe_status"}:
            continue
        if value is not None:
            if key == "status":
                query = query.filter(Observation.status == value)
            elif key == "quality_status":
                query = query.filter(
                    Observation.quality_status.is_(None)
                    if value == "unlabelled"
                    else Observation.quality_status == value
                )
            elif key == "since":
                query = query.filter(Outlook.window_start_at >= value)
            elif key == "until":
                query = query.filter(Outlook.window_start_at < value)
            else:
                query = query.filter(getattr(Outlook, key) == value)
    return query


class AnalyticalOutlookQuery:
    def __init__(
        self, database: Database, *, archive: RawBodyArchive | None = None,
        clock: Callable[[], object] | None = None,
        cohort_budget_seconds: float = 2.0, monotonic: Callable[[], float] = time.monotonic,
        background_assessments: bool = False,
    ) -> None:
        if not math.isfinite(cohort_budget_seconds) or cohort_budget_seconds < 0:
            raise ValueError("analytical_cohort_budget_invalid")
        self.database = database
        self.archive = archive
        self.clock = clock or (lambda: datetime.now(UTC))
        self._cohort_budget_seconds = cohort_budget_seconds
        self._monotonic = monotonic
        self._background_assessments = background_assessments

    def _read_snapshot(self, session: Session) -> None:
        if self._background_assessments:
            connection = session.connection()
            if connection.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN")
                connection.info["assessment_replay_authorizer_owned"] = True
                info = connection.info

                def release_marker(_session: Session, transaction: SessionTransaction) -> None:
                    if transaction.parent is None:
                        info.pop("assessment_replay_authorizer_owned", None)

                event.listen(session, "after_transaction_end", release_marker)

    def _recipe_selection(
        self, session: Session, now: datetime, *, scopes: set[_MethodScope],
        memo: dict[Any, Any], current: AnalyticalAssessment | None = None,
    ) -> tuple[dict[str, _SelectionStatus], dict[str, AnalyticalAssessment]]:
        if not scopes:
            return {}, {}
        columns = (Outlook.market, Outlook.instrument, Outlook.method_family,
                   Outlook.horizon, Outlook.study_id)
        originals = session.query(Outlook).options(load_only(
            Outlook.outlook_id, Outlook.study_id, Outlook.market, Outlook.instrument,
            Outlook.method_family, Outlook.horizon, Outlook.recorded_at,
        )).filter(
            Outlook.cohort == "prospective", Outlook.recorded_at <= now,
            or_(*(and_(*(column == value for column, value in zip(
                columns, scope, strict=True,
            ))) for scope in scopes)),
        ).order_by(Outlook.recorded_at, Outlook.outlook_id).all()
        jobs = {job.outlook_id: job for job in session.query(AssessmentJob).filter(
            AssessmentJob.outlook_id.in_([row.outlook_id for row in originals]),
        )}
        assessments: dict[str, AnalyticalAssessment] = {}
        candidates = []
        for row in originals:
            job = jobs.get(row.outlook_id)
            assessment = (current if current is not None and current.outlook_id == row.outlook_id
                          else read_valid_assessment(
                              session, cast(AssessmentJobView, job), now=now,
                              archive=self.archive, memo=memo,
                          ) if job is not None else None)
            if assessment is not None:
                assessments[row.outlook_id] = assessment
            candidates.append(RecipeObservationCandidate(
                outlook_id=row.outlook_id, recorded_at=as_utc(row.recorded_at),
                market=row.market, instrument=row.instrument, method_family=row.method_family,
                horizon=row.horizon, study_id=row.study_id,
                stable_recipe_hash=_recipe_hash(assessment),
                source_verified=_recipe_hash(assessment) is not None,
            ))
        return dict(select_recipe_observations(candidates, now=now).statuses), assessments

    def _recipe_records_in_session(
        self, session: Session, now: datetime, *, filters: dict[str, Any],
        recipe: str | None, recipe_status: str | None,
    ) -> tuple[
        list[tuple[Outlook, Observation]], dict[str, _SelectionStatus],
        dict[str, AnalyticalAssessment],
    ]:
        rows = list(filter_query(
            session.query(Outlook, Observation).options(defer(Outlook.raw_payload_json)).join(
                Observation, Observation.outlook_id == Outlook.outlook_id,
            ), filters,
        ).all())
        scopes = {scope for row, _ in rows if (scope := _method_scope(row)) is not None}
        memo: dict[Any, Any] = {}
        statuses, assessments = self._recipe_selection(session, now, scopes=scopes, memo=memo)
        # Missing Study identities stay unkeyed. Their genuine source can still
        # be read independently, without granting them a first-sample position.
        for row, _ in rows:
            if row.study_id or row.cohort != "prospective" or as_utc(row.recorded_at) > now:
                continue
            statuses[row.outlook_id] = "unkeyed"
            job = session.get(AssessmentJob, row.outlook_id)
            assessment = read_valid_assessment(
                session, cast(AssessmentJobView, job), now=now,
                archive=self.archive, memo=memo,
            ) if job is not None else None
            if assessment is not None:
                assessments[row.outlook_id] = assessment
        if recipe is not None:
            rows = [(row, observation) for row, observation in rows
                    if _recipe_hash(assessments.get(row.outlook_id)) == recipe]
        elif recipe_status == "unchecked":
            rows = [(row, observation) for row, observation in rows
                    if _recipe_hash(assessments.get(row.outlook_id)) is None]
        return rows, statuses, assessments

    @shape_validation_scope()
    def assessment(self, outlook_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            self._read_snapshot(session)
            return self._assessment_in_session(session, outlook_id).model_dump(mode="json")

    def _now(self) -> datetime:
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("analytical_assessment_clock_timezone_required")
        return as_utc(now)

    def _selection_in_session(
        self, session: Session, now: datetime,
    ) -> tuple[frozenset[str], frozenset[str]]:
        originals = session.query(Outlook).options(
            load_only(Outlook.outlook_id, Outlook.study_id, Outlook.market, Outlook.instrument,
                      Outlook.method_family, Outlook.method_version, Outlook.horizon,
                      Outlook.cohort, Outlook.recorded_at),
        ).filter(Outlook.cohort == "prospective", Outlook.recorded_at <= now).all()
        first_ids = frozenset(o.outlook_id for o in first_record_outlooks(originals, now=now))
        versions: dict[_MethodScope, set[str]] = {}
        for row in originals:
            scope = _method_scope(row)
            if scope is not None:
                versions.setdefault(scope, set()).add(row.method_version)
        ambiguous_scopes = {scope for scope, saved in versions.items() if len(saved) > 1}
        ambiguous_ids = frozenset(
            row.outlook_id for row in originals if _method_scope(row) in ambiguous_scopes
        )
        return first_ids, ambiguous_ids

    @staticmethod
    def _selection_assessment(
        assessment: AnalyticalAssessment, *, selection_status: _SelectionStatus,
        method_ambiguous: bool,
    ) -> AnalyticalAssessment:
        limitations = list(assessment.limitations)
        if method_ambiguous:
            selection_status = "unchecked"
            if "method_identity_ambiguous" not in limitations:
                limitations.append("method_identity_ambiguous")
        return assessment.model_copy(update={
            "selection_status": selection_status,
            "directional_usable": assessment.directional_usable and selection_status == "selected",
            "limitations": limitations,
        })

    def _assessment_in_session(
        self, session: Session, outlook_id: str,
    ) -> AnalyticalAssessment:
        now = self._now()
        row = session.get(Outlook, outlook_id)
        if row is None:
            raise KeyError(outlook_id)
        memo: dict[Any, Any] = {}
        job = session.get(AssessmentJob, outlook_id) if self._background_assessments else None
        assessment = (read_valid_assessment(
            session, cast(AssessmentJobView, job), now=now,
            archive=self.archive, memo=memo,
        ) if job is not None else None)
        if assessment is None and self._background_assessments:
            reason = ("assessment_recheck_pending" if job is not None and job.status == "completed"
                      else "assessment_background_pending")
            # The original record remains readable; only the background worker
            # may rebuild a missing, expired or rejected authenticated basis.
            # A deferred check is neither a verified sample nor missing originals.
            return AnalyticalAssessment(
                assessed_at=now, outlook_id=row.outlook_id, run_id=row.run_id,
                study_id=row.study_id, source_status="unverifiable", source_reason=reason,
                method_status="unverifiable", label_status="unverifiable", label_reason=reason,
                selection_status="unchecked", directional_usable=False,
            )
        if assessment is None:
            assessment = assess_outlook_in_session(
                session, outlook_id, assessed_at=now, archive=self.archive,
            )
        if self._background_assessments:
            scope = _method_scope(row)
            statuses, _ = self._recipe_selection(
                session, now, scopes={scope} if scope is not None else set(), memo=memo,
                current=assessment,
            )
            return self._selection_assessment(
                assessment, selection_status=statuses.get(outlook_id, "unkeyed"),
                method_ambiguous=statuses.get(outlook_id) == "unchecked",
            )
        first_ids, ambiguous_ids = self._selection_in_session(session, now)
        selected: _SelectionStatus = (
            "unkeyed" if not row.study_id or row.cohort != "prospective"
            or as_utc(row.recorded_at) > now else
            "selected" if outlook_id in first_ids else "repeat"
        )
        return self._selection_assessment(
            assessment, selection_status=selected, method_ambiguous=outlook_id in ambiguous_ids,
        )

    def list(self, *, limit: int = 30, offset: int = 0, **filters: Any) -> dict[str, Any]:
        filters, recipe, recipe_status = _recipe_filters(filters)
        if (recipe is not None or recipe_status is not None) and not self._background_assessments:
            raise ValueError("analytical_recipe_filter_requires_background")
        with self.database.session() as session:
            if recipe is not None or recipe_status is not None:
                self._read_snapshot(session)
                originals, _, _ = self._recipe_records_in_session(
                    session, self._now(), filters=filters, recipe=recipe,
                    recipe_status=recipe_status,
                )
                originals.sort(key=lambda pair: (
                    -as_utc(pair[0].window_start_at).timestamp(), pair[0].outlook_id,
                ))
                total = len(originals)
                offset = min(offset, ((total - 1) // limit) * limit) if total else 0
                return dict(items=[outlook_view(row, observation)
                                   for row, observation in originals[offset:offset + limit]],
                            total=total, limit=limit, offset=offset)
            query = filter_query(
                session.query(Outlook, Observation).join(
                    Observation, Observation.outlook_id == Outlook.outlook_id
                ),
                filters,
            )
            total = query.count()
            offset = min(offset, ((total - 1) // limit) * limit) if total else 0
            rows = (
                query.order_by(Outlook.window_start_at.desc(), Outlook.outlook_id)
                .offset(offset)
                .limit(limit)
            )
            return dict(
                items=[outlook_view(o, r) for o, r in rows], total=total, limit=limit, offset=offset
            )

    @shape_validation_scope()
    def get(self, outlook_id: str, *, limit: int = 30, offset: int = 0) -> dict[str, Any]:
        with self.database.session() as session:
            self._read_snapshot(session)
            row = session.get(Outlook, outlook_id)
            if row is None:
                raise KeyError(outlook_id)
            observation = session.get(Observation, outlook_id)
            if observation is None:
                raise KeyError(outlook_id)
            query = session.query(Attempt).filter_by(outlook_id=outlook_id)
            total = query.count()
            offset = min(offset, ((total - 1) // limit) * limit) if total else 0
            attempts = query.order_by(Attempt.attempt.desc()).offset(offset).limit(limit).all()
            contexts = {
                f"analytical-observation:{outlook_id}:{a.attempt}": a.attempt for a in attempts
            }
            captures = {a.attempt: [] for a in attempts}
            for capture in session.query(RawCaptureRecord).filter(
                RawCaptureRecord.context_id.in_(contexts)
            ):
                captures[contexts[capture.context_id]].append(capture.capture_id)
            return dict(
                outlook=outlook_view(row, observation),
                assessment=self._assessment_in_session(session, outlook_id).model_dump(mode="json"),
                raw_payload=json.loads(row.raw_payload_json),
                window=json.loads(observation.window_json) if observation.window_json else None,
                attempts=[
                    dict(
                        attempt=a.attempt,
                        status=a.status,
                        started_at=as_utc(a.started_at),
                        finished_at=as_utc(a.finished_at),
                        error_code=a.error_code,
                        raw_capture_ids=captures[a.attempt],
                    )
                    for a in attempts
                ],
                attempts_total=total,
                attempts_limit=limit,
                attempts_offset=offset,
            )

    def publication(self, artifact_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            publication = session.get(PublicationDecisionRecord, artifact_id)
            execution = session.get(ExecutionPermissionRecord, artifact_id)
            if publication is None or execution is None:
                raise KeyError(artifact_id)
            return dict(
                artifact_id=artifact_id,
                run_id=publication.run_id,
                status=publication.status,
                result_hash=publication.result_hash,
                decisions=json.loads(publication.decisions_json),
                published_horizons=json.loads(publication.published_horizons_json),
                review_refs=json.loads(publication.review_refs_json),
                decided_at=as_utc(publication.decided_at),
                execution_allowed=execution.allowed,
                execution_policy=execution.policy_version,
                execution_reasons=json.loads(execution.reasons_json),
            )

    def _recipe_cohorts(
        self, *, limit: int, offset: int, filters: dict[str, Any],
        recipe: str | None, recipe_status: str | None,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            self._read_snapshot(session)
            now = self._now()
            originals, statuses, cached = self._recipe_records_in_session(
                session, now, filters=filters, recipe=recipe, recipe_status=recipe_status,
            )
            grouped: dict[tuple[str | None, ...], list[tuple[Outlook, Observation]]] = {}
            for outlook, observation in originals:
                identity = _recipe_hash(cached.get(outlook.outlook_id))
                key = (
                    outlook.market, outlook.instrument, outlook.method_family,
                    "verified" if identity is not None else "unchecked",
                    identity if identity is not None else outlook.method_version,
                    outlook.horizon, outlook.action, outlook.cohort,
                    outlook.research_quality, observation.quality_status,
                )
                grouped.setdefault(key, []).append((outlook, observation))
            # Identity grouping precedes pagination. Neither a saved alias nor
            # the first page can split one recipe or combine two real recipes.
            ordered = sorted(grouped, key=lambda key: (
                key[3] != "verified", tuple(value or "" for value in key),
            ))
            total = len(ordered)
            offset = min(offset, ((total - 1) // limit) * limit) if total else 0
            items: list[dict[str, Any]] = []
            for key in ordered[offset:offset + limit]:
                pairs = grouped[key]
                row, observation = pairs[0]
                identity = _recipe_hash(cached.get(row.outlook_id))
                aliases = sorted({outlook.method_version for outlook, _ in pairs})
                checked = [cached[outlook.outlook_id] for outlook, _ in pairs
                           if outlook.outlook_id in cached]
                selected = [cached[outlook.outlook_id] for outlook, _ in pairs
                            if statuses.get(outlook.outlook_id) == "selected"
                            and outlook.outlook_id in cached]
                usable = [assessment for assessment in selected
                          if assessment.directional_usable and _recipe_hash(assessment) == identity
                          and assessment.label_status == "verified"
                          and assessment.verified_direction_correct is not None
                          and assessment.verified_directional_return_pct is not None]
                pending = len(pairs) - len(checked)
                unchecked = sum(statuses.get(outlook.outlook_id) == "unchecked"
                                for outlook, _ in pairs)
                unkeyed = sum(not outlook.study_id and outlook.cohort == "prospective"
                              and as_utc(outlook.recorded_at) <= now for outlook, _ in pairs)
                reasons: Counter[str] = Counter()
                for assessment in checked:
                    if assessment.source_status != "verified":
                        reasons[assessment.source_reason or "source_unverifiable"] += 1
                    if assessment.label_reason or assessment.label_status != "verified":
                        reasons[assessment.label_reason or assessment.label_status] += 1
                    if _recipe_hash(assessment) is None:
                        reasons["stable_recipe_unverifiable"] += 1
                if pending:
                    reasons["assessment_pending"] = pending
                if unchecked:
                    reasons["method_identity_ambiguous"] = unchecked
                if unkeyed:
                    reasons["study_identity_missing"] = unkeyed
                scored = [observed for outlook, observed in pairs
                          if outlook.cohort == "prospective" and outlook.eligibility == "eligible"
                          and outlook.action in {"long", "short"}
                          and observed.status == "completed"
                          and observed.direction_correct in {0, 1}]
                raw_returns = [observed.directional_return_pct for observed in scored
                               if observed.directional_return_pct is not None]
                returns = [assessment.verified_directional_return_pct for assessment in usable
                           if assessment.verified_directional_return_pct is not None]
                unverified = sum(
                    (assessment := cached.get(outlook.outlook_id)) is None
                    or assessment.source_status != "verified"
                    or assessment.label_status != "verified"
                    or _recipe_hash(assessment) is None
                    or statuses.get(outlook.outlook_id) in {"unchecked", "unkeyed"}
                    or "frozen_input_unverified" in assessment.limitations
                    for outlook, _ in pairs
                )
                value: dict[str, Any] = dict(
                    market=row.market, instrument=row.instrument, method_family=row.method_family,
                    method_version=aliases[0] if len(aliases) == 1 else None,
                    stable_recipe_hash=identity, recipe_status=key[3],
                    saved_method_versions=aliases,
                    horizon=row.horizon, action=row.action, cohort=row.cohort,
                    research_quality=row.research_quality,
                    quality_status=observation.quality_status,
                    total=len(pairs),
                    completed=sum(observed.status == "completed" for _, observed in pairs),
                    pending=sum(observed.status in {"pending", "running"} for _, observed in pairs),
                    retry=sum(observed.status == "retry" for _, observed in pairs),
                    excluded=sum(observed.status == "excluded" for _, observed in pairs),
                    record_directional_count=len(scored),
                    record_correct_count=sum(observed.direction_correct == 1
                                             for observed in scored),
                    record_average_directional_return_pct=(
                        sum(raw_returns) / len(raw_returns) if raw_returns else None
                    ),
                    distinct_studies=len({outlook.study_id for outlook, _ in pairs
                                          if outlook.study_id is not None}),
                    fixed_sample_count=len(selected),
                    repeat_count=sum(statuses.get(outlook.outlook_id) == "repeat"
                                     for outlook, _ in pairs),
                    source_verified_count=sum(a.source_status == "verified" for a in checked),
                    label_verified_count=sum(a.label_status == "verified" for a in checked),
                    unverified_count=unverified,
                    method_recipe_hashes=[identity] if identity else [],
                    assessment_checked_count=len(checked), assessment_pending_count=pending,
                    assessment_reasons=dict(reasons),
                    sample_selection_version="first-record-study-authenticated-recipe-horizon.v3",
                    directional_count=len(usable),
                    correct_count=sum(a.verified_direction_correct is True for a in usable),
                    average_directional_return_pct=sum(returns) / len(returns) if returns else None,
                    minimum_rate_samples=30, calibration_status="not_calibration",
                )
                if identity is not None:
                    value["assessment_version"] = "authenticated-recipe-originals.v2"
                value["directional_hit_rate"] = (
                    value["correct_count"] / len(usable) if len(usable) >= 30 else None
                )
                items.append(value)
            return dict(items=items, total=total, limit=limit, offset=offset)

    @shape_validation_scope()
    def cohorts(self, *, limit: int = 30, offset: int = 0, **filters: Any) -> dict[str, Any]:
        deadline = self._monotonic() + self._cohort_budget_seconds
        filters, recipe, recipe_status = _recipe_filters(filters)
        if self._background_assessments:
            return self._recipe_cohorts(
                limit=limit, offset=offset, filters=filters, recipe=recipe,
                recipe_status=recipe_status,
            )
        if recipe is not None or recipe_status is not None:
            raise ValueError("analytical_recipe_filter_requires_background")
        budget_exhausted = False
        keys = (
            "market",
            "instrument",
            "method_family",
            "method_version",
            "horizon",
            "action",
            "cohort",
            "research_quality",
        )
        groups = [getattr(Outlook, key) for key in keys] + [Observation.quality_status]
        scored = and_(
            Outlook.cohort == "prospective",
            Outlook.eligibility == "eligible",
            Outlook.action.in_(["long", "short"]),
            Observation.status == "completed",
            Observation.direction_correct.in_([0, 1]),
        )

        def count_status(values: list[str]):
            return func.sum(case((Observation.status.in_(values), 1), else_=0))

        with self.database.session() as session:
            self._read_snapshot(session)
            query = filter_query(
                session.query(
                    *groups,
                    func.count(Outlook.outlook_id),
                    count_status(["completed"]),
                    count_status(["pending", "running"]),
                    count_status(["retry"]),
                    count_status(["excluded"]),
                    func.sum(case((scored, 1), else_=0)),
                    func.sum(case((scored, Observation.direction_correct), else_=0)),
                    func.count(func.distinct(Outlook.study_id)),
                    func.avg(case((scored, Observation.directional_return_pct), else_=None)),
                ).join(Observation, Observation.outlook_id == Outlook.outlook_id),
                filters,
            ).group_by(*groups)
            total = query.count()
            offset = min(offset, ((total - 1) // limit) * limit) if total else 0
            rows = query.order_by(*groups).offset(offset).limit(limit).all()
            # Choose each Study's first recorded judgment before any display
            # filters. A later successful update cannot replace its failure
            # or abstention, and pagination cannot change the selected sample.
            now = self._now()
            first_ids, ambiguous_ids = (self._selection_in_session(session, now)
                                        if not self._background_assessments else (set(), set()))
            page_groups = {tuple(row[:len(groups)]) for row in rows}
            matching = filter_query(
                session.query(
                    Outlook.outlook_id, Outlook.study_id, *groups, Outlook.recorded_at
                ).join(
                    Observation, Observation.outlook_id == Outlook.outlook_id
                ), filters,
            ).filter(or_(*(and_(*(column == value for column, value in zip(
                groups, key, strict=True
            ))) for key in page_groups))).all() if page_groups else []
            by_group = {key: [] for key in page_groups}
            for outlook in matching:
                key = tuple(outlook[2:-1])
                if key in by_group:
                    by_group[key].append(outlook)
            recipe_statuses, cached_assessments = ({}, {})
            if self._background_assessments:
                scopes = {(o.market, o.instrument, o.method_family, o.horizon, o.study_id)
                          for o in matching if o.study_id is not None}
                recipe_statuses, cached_assessments = self._recipe_selection(
                    session, now, scopes=scopes, memo={},
                )
                first_ids = {identity for identity, status in recipe_statuses.items()
                             if status == "selected"}
                ambiguous_ids = {identity for identity, status in recipe_statuses.items()
                                 if status == "unchecked"}
            items = []
            for row in rows:
                value: dict[str, Any] = dict(
                    zip(
                        (
                            *keys,
                            "quality_status",
                            "total",
                            "completed",
                            "pending",
                            "retry",
                            "excluded",
                            "directional_count",
                            "correct_count",
                            "distinct_studies",
                            "average_directional_return_pct",
                        ),
                        row,
                        strict=True,
                    )
                )
                value["record_directional_count"] = value["directional_count"]
                value["record_correct_count"] = value["correct_count"] or 0
                value["record_average_directional_return_pct"] = value[
                    "average_directional_return_pct"
                ]
                group_rows = by_group[tuple(row[:len(groups)])]
                selected = sorted(
                    (o for o in group_rows if o.outlook_id in first_ids),
                    key=_record_order,
                )
                assessments: list[AnalyticalAssessment] = []
                for outlook in selected:
                    if self._background_assessments:
                        cached = cached_assessments.get(outlook.outlook_id)
                        if cached is not None:
                            assessments.append(self._selection_assessment(
                                cached, selection_status="selected", method_ambiguous=False,
                            ))
                        continue
                    if budget_exhausted or self._monotonic() >= deadline:
                        budget_exhausted = True
                        break
                    assessments.append(self._selection_assessment(
                        assess_outlook_in_session(
                            session, outlook.outlook_id, assessed_at=now, archive=self.archive,
                        ), selection_status="selected",
                        method_ambiguous=outlook.outlook_id in ambiguous_ids,
                    ))
                pending = len(selected) - len(assessments)
                recipes = sorted({a.stable_recipe_hash for a in assessments
                                  if a.stable_recipe_hash is not None})
                reasons = Counter()
                for assessment in assessments:
                    if assessment.source_status != "verified":
                        reasons[assessment.source_reason or "source_unverifiable"] += 1
                    if assessment.label_reason or assessment.label_status != "verified":
                        reasons[assessment.label_reason or assessment.label_status] += 1
                    if assessment.stable_recipe_hash is None:
                        reasons["stable_recipe_unverifiable"] += 1
                ambiguous = sum(o.outlook_id in ambiguous_ids for o in selected)
                if ambiguous:
                    reasons["method_identity_ambiguous"] = ambiguous
                if pending:
                    reasons["assessment_pending"] = pending
                usable = [a for a in assessments if a.directional_usable
                          and a.stable_recipe_hash is not None
                          and a.verified_direction_correct is not None
                          and a.verified_directional_return_pct is not None]
                if len(recipes) > 1:
                    reasons["mixed_stable_recipes"] += 1
                    usable = []
                if pending:
                    # Unchecked originals may carry a different recipe. Keep
                    # observed verification counts without publishing a rate.
                    usable = []
                directional_returns = [a.verified_directional_return_pct for a in usable
                                       if a.verified_directional_return_pct is not None]
                value.update(
                    fixed_sample_count=len(selected),
                    repeat_count=sum(o.study_id is not None and o.outlook_id not in first_ids
                                     and o.cohort == "prospective" and as_utc(o.recorded_at) <= now
                                     for o in group_rows),
                    source_verified_count=sum(a.source_status == "verified" for a in assessments),
                    label_verified_count=sum(a.label_status == "verified" for a in assessments),
                    unverified_count=pending + sum(a.source_status != "verified"
                                                  or a.label_status != "verified"
                                                  or "frozen_input_unverified" in a.limitations
                                                  or "method_identity_ambiguous" in a.limitations
                                                  for a in assessments),
                    method_recipe_hashes=recipes,
                    assessment_reasons=dict(reasons),
                    directional_count=len(usable),
                    correct_count=sum(a.verified_direction_correct is True for a in usable),
                    average_directional_return_pct=(
                        sum(directional_returns) / len(directional_returns)
                        if directional_returns else None
                    ),
                )
                if not pending:
                    value["assessment_version"] = "fixed-study-originals.v1"
                if self._background_assessments:
                    checked = sum(o.outlook_id in cached_assessments for o in group_rows)
                    unchecked = sum(o.outlook_id in ambiguous_ids for o in group_rows)
                    value.update(
                        assessment_checked_count=checked,
                        assessment_pending_count=len(group_rows) - checked,
                        sample_selection_version=(
                            "first-record-study-authenticated-recipe-horizon.v3"
                        ),
                        repeat_count=sum(recipe_statuses.get(o.outlook_id) == "repeat"
                                         for o in group_rows),
                    )
                    if unchecked:
                        value["assessment_reasons"]["method_identity_ambiguous"] = unchecked
                        value["unverified_count"] += unchecked
                    if checked < len(group_rows):
                        value["assessment_reasons"]["assessment_pending"] = (
                            len(group_rows) - checked
                        )
                    if unchecked or checked < len(group_rows):
                        value.pop("assessment_version", None)
                        value.update(directional_count=0, correct_count=0,
                                     average_directional_return_pct=None)
                    else:
                        value["assessment_version"] = "authenticated-recipe-originals.v2"
                value["minimum_rate_samples"] = 30
                value["directional_hit_rate"] = (
                    value["correct_count"] / value["directional_count"]
                    if value["directional_count"] >= 30
                    else None
                )
                value["calibration_status"] = "not_calibration"
                items.append(value)
            return dict(items=items, total=total, limit=limit, offset=offset)
