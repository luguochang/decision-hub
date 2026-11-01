"""Server-captured, immutable report/label cases with conservative PIT retrieval."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    ResearchInputEvidence,
    ResearchSessionResult,
)
from packages.evals.assessment_basis import (
    build_assessment_basis_in_session,
    verify_assessment_basis_in_session,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive

from ..persistence.db import (
    AnalyticalObservationRecord,
    AnalyticalOutlookRecord,
    ArtifactRecord,
    Database,
    FactVersionRecord,
    FeedbackRecord,
    ReportRevisionRecord,
    ResearchResultRecord,
    RunRecord,
    StudyRecord,
    as_utc,
    utcnow,
)
from ..persistence.market_case_records import MarketCaseRecord, MarketCaseRelationRecord
from ..ports.market_case import (
    CaseContextReference,
    CaseOriginal,
    CaseRelation,
    CaseRetrieval,
    CaseScope,
    MarketCase,
    RetrievedCase,
)
from ..ports.market_path import aware_utc, canonical_hash


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _row(row: object) -> dict:
    values = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        # Only ORM SQLite UTC storage is decoded here. External times remain strict.
        values[column.name] = as_utc(value).isoformat() if isinstance(value, datetime) else value
    return values


@contextmanager
def _read_boundary(session: Session) -> Iterator[None]:
    connection = session.connection()
    native = connection.connection.driver_connection
    if not isinstance(native, sqlite3.Connection) or not native.in_transaction:
        raise ValueError("market_case_physical_snapshot_required")
    if session.new or session.dirty or session.deleted:
        raise ValueError("market_case_clean_reader_required")
    tx, nested, count = (
        connection.get_transaction(),
        connection.get_nested_transaction(),
        native.total_changes,
    )

    def only_select(_conn, _cursor, statement, _parameters, _context, executemany):
        if executemany or not statement.lstrip().upper().startswith("SELECT "):
            raise ValueError("market_case_select_only")

    event.listen(connection, "before_cursor_execute", only_select)
    try:
        with session.no_autoflush:
            yield
    finally:
        event.remove(connection, "before_cursor_execute", only_select)
        if (
            not native.in_transaction
            or native.total_changes != count
            or connection.get_transaction() is not tx
            or connection.get_nested_transaction() is not nested
            or session.new
            or session.dirty
            or session.deleted
        ):
            raise ValueError("market_case_snapshot_changed")


def case_context_reference(case: MarketCase) -> CaseContextReference:
    """Root may inject this protected context; it is never a current-price Fact."""
    lines = [f"历史案例 {case.case_id}；原窗口 {case.scope.horizon}；原方向 {case.action}"]
    if case.mechanism:
        lines.append(case.mechanism)
    lines.extend(f"反方：{text}" for text in case.counter_evidence)
    lines.extend(f"未解决：{text}" for text in case.unresolved_questions)
    lines.append("历史结果不授予当前方向、校准概率或交易权限。")
    return CaseContextReference(
        ref=f"market_case:{case.case_id}",
        payload_hash=case.payload_hash,
        available_at=case.available_at,
        revision_id=case.revision_id,
        run_id=case.run_id,
        excerpt="\n".join(lines)[:4000],
    )


class MarketCaseService:
    def __init__(self, database: Database, archive: RawBodyArchive, *, clock=utcnow):
        self.database, self.archive, self.clock = database, archive, clock

    def capture(
        self,
        request_id: str,
        *,
        revision_id: str,
        outlook_id: str,
        feedback_id: str | None = None,
    ) -> MarketCase:
        """Trusted internal entry. User input cannot set eligibility, scope, or known time."""
        now = aware_utc(self.clock())
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            old = session.scalar(
                select(MarketCaseRecord).where(MarketCaseRecord.request_id == request_id)
            )
            if old is not None:
                value = self._decode(old)
                if (
                    value.revision_id,
                    value.outlook_id,
                    (value.feedback or {}).get("feedback_id"),
                ) != (
                    revision_id,
                    outlook_id,
                    feedback_id,
                ):
                    raise ValueError("market_case_request_reused")
                return value
            with _read_boundary(session):
                value = self._capture(
                    session, request_id, revision_id, outlook_id, feedback_id, now
                )
            if len(value.model_dump_json().encode()) > 2 * 1024 * 1024:
                raise ValueError("market_case_payload_limit")
            record = MarketCaseRecord(
                case_id=value.case_id,
                request_id=request_id,
                outlook_id=outlook_id,
                market=value.scope.market,
                instrument=value.scope.instrument,
                horizon=value.scope.horizon,
                recipe_hash=value.scope.stable_recipe_hash,
                created_at=now,
                available_at=value.available_at,
                payload_json=value.model_dump_json(),
                payload_hash=value.payload_hash,
            )
            session.add(record)
            session.flush()
            # Captured source and label queries are independently re-read after the writer flush.
            with session.no_autoflush:
                verify_assessment_basis_in_session(
                    session, value.assessment_basis, archive=self.archive
                )
                if (
                    self._decode(
                        session.get(MarketCaseRecord, value.case_id, populate_existing=True)
                    )
                    != value
                ):
                    raise ValueError("market_case_writer_changed")
            return value

    def _capture(self, session, request_id, revision_id, outlook_id, feedback_id, now):
        revision = session.get(ReportRevisionRecord, revision_id, populate_existing=True)
        outlook = session.get(AnalyticalOutlookRecord, outlook_id, populate_existing=True)
        if revision is None or outlook is None:
            raise ValueError("market_case_report_not_found")
        study = session.get(StudyRecord, revision.study_id, populate_existing=True)
        run = session.get(RunRecord, revision.source_run_id, populate_existing=True)
        artifact = session.get(ArtifactRecord, revision.artifact_id, populate_existing=True)
        if (
            study is None
            or run is None
            or artifact is None
            or run.status not in {"completed", "degraded", "failed"}
            or run.finished_at is None
            or run.study_id != revision.study_id
            or run.artifact_id != revision.artifact_id
            or artifact.run_id != run.run_id
            or outlook.run_id != run.run_id
            or outlook.artifact_id != artifact.artifact_id
            or outlook.study_id != study.study_id
            or (outlook.market, outlook.instrument) != (study.market, study.instrument)
        ):
            raise ValueError("market_case_report_binding_invalid")
        assessment, basis = build_assessment_basis_in_session(
            session,
            outlook_id,
            assessed_at=now,
            archive=self.archive,
        )
        reasons = []
        if assessment.source_status != "verified":
            reasons.append(assessment.source_reason or "source_unverified")
        if assessment.method_status != "verified" or assessment.stable_recipe_hash is None:
            reasons.append("method_unverified")
        if assessment.label_status != "verified":
            reasons.append(assessment.label_reason or "mature_label_unverified")
        if not assessment.directional_usable:
            reasons.append("prospective_direction_unqualified")
        snapshot = {
            "revision": _row(revision),
            "outlook": _row(outlook),
            "artifact": _row(artifact),
        }
        # This actual capture is a conservative upper bound on first server knowledge,
        # never the report's old cutoff or an evaluated_at backfill.
        known_times = [
            now,
            as_utc(revision.recorded_at),
            as_utc(outlook.recorded_at),
            as_utc(artifact.created_at),
        ]
        originals = []
        for namespace, original_id, row in (
            ("report_revision", revision_id, revision),
            ("analytical_outlook", outlook_id, outlook),
            ("artifact", artifact.artifact_id, artifact),
        ):
            originals.append(
                CaseOriginal(
                    namespace=namespace,
                    original_id=original_id,
                    payload_hash=canonical_hash(_row(row)),
                    first_known_at=now,
                )
            )
        fact_refs = json.loads(outlook.fact_version_refs_json)
        if not isinstance(fact_refs, list):
            raise ValueError("market_case_fact_refs_invalid")
        for ref in fact_refs:
            fact = session.get(FactVersionRecord, ref, populate_existing=True)
            if fact is None:
                reasons.append("fact_original_missing")
                continue
            known = as_utc(fact.known_at)
            known_times.append(known)
            if known is None or known > now:
                reasons.append("fact_known_after_capture")
            snapshot.setdefault("facts", {})[ref] = _row(fact)
            originals.append(
                CaseOriginal(
                    namespace="fact_version",
                    original_id=ref,
                    payload_hash=canonical_hash(_row(fact)),
                    first_known_at=known,
                )
            )
        saved = session.get(ResearchResultRecord, run.run_id, populate_existing=True)
        causal = None
        if saved is not None:
            if hashlib.sha256(saved.payload_json.encode()).hexdigest() != saved.payload_hash:
                raise ValueError("market_case_result_hash_invalid")
            result = ResearchSessionResult.model_validate_json(saved.payload_json)
            causal = result.causal_case
            snapshot["result"] = _row(saved)
            known_times.append(as_utc(saved.created_at))
            originals.append(
                CaseOriginal(
                    namespace="research_result",
                    original_id=run.run_id,
                    payload_hash=saved.payload_hash,
                    first_known_at=now,
                )
            )
        else:
            reasons.append("research_result_missing")
        observation = session.get(AnalyticalObservationRecord, outlook_id, populate_existing=True)
        label_known = now if assessment.label_status == "verified" else None
        if label_known is not None and (
            observation is None
            or observation.status != "completed"
            or as_utc(observation.evaluated_at) is None
            or as_utc(observation.evaluated_at) > now
        ):
            reasons.append("label_known_time_invalid")
        feedback = None
        if feedback_id:
            feedback_row = session.get(FeedbackRecord, feedback_id, populate_existing=True)
            if (
                feedback_row is None
                or feedback_row.target_type != "report_revision"
                or feedback_row.target_id != revision_id
            ):
                raise ValueError("market_case_feedback_binding_invalid")
            feedback = _row(feedback_row)
            known_times.append(as_utc(feedback_row.created_at))
            originals.append(
                CaseOriginal(
                    namespace="product_feedback",
                    original_id=feedback_id,
                    payload_hash=canonical_hash(feedback),
                    first_known_at=now,
                )
            )
        if any(value is None or value > now for value in known_times):
            reasons.append("original_known_after_capture")
        reasons = tuple(dict.fromkeys(reasons))
        eligible = not reasons
        classification = (
            "verified_market_case"
            if eligible
            else "product_feedback"
            if feedback
            else "model_error"
            if run.error_code == "research_plan_invalid"
            else "operational_failure"
            if run.error_code
            else "research_diagnostic"
        )
        return MarketCase(
            case_id="mcase_" + canonical_hash({"request_id": request_id})[:40],
            request_id=request_id,
            revision_id=revision_id,
            study_id=study.study_id,
            run_id=run.run_id,
            artifact_id=artifact.artifact_id,
            outlook_id=outlook_id,
            scope=CaseScope(
                market=outlook.market,
                instrument=outlook.instrument,
                horizon=outlook.horizon,
                method_family=outlook.method_family,
                stable_recipe_hash=assessment.stable_recipe_hash,
            ),
            original_cutoff_at=as_utc(outlook.data_as_of),
            window_start_at=as_utc(outlook.window_start_at),
            window_end_at=as_utc(outlook.window_end_at),
            created_at=now,
            original_known_at=max(value for value in known_times if value is not None),
            label_known_at=label_known,
            available_at=max([now, *[v for v in known_times if v is not None]]),
            classification=classification,
            failure_category=run.error_code,
            eligible=eligible,
            exclusion_reasons=reasons,
            action=outlook.action,
            subjective_probability=outlook.subjective_probability,
            mechanism=causal.thesis if causal else None,
            precursor=tuple(link.statement for link in causal.main_chain) if causal else (),
            counter_evidence=tuple(link.statement for link in causal.opposite_chain)
            if causal
            else (),
            unresolved_questions=tuple(causal.unresolved_questions) if causal else (),
            confirmation=tuple(link.confirmation for link in causal.main_chain) if causal else (),
            invalidation=tuple(link.invalidation for link in causal.main_chain) if causal else (),
            direction_correct=assessment.verified_direction_correct,
            directional_return_pct=assessment.verified_directional_return_pct,
            label_capture_ids=tuple(assessment.label_capture_ids),
            originals=tuple(originals),
            assessment=assessment.model_dump(mode="json"),
            assessment_basis=basis,
            source_snapshot=snapshot,
            feedback=feedback,
        )

    @staticmethod
    def _decode(row: MarketCaseRecord | None) -> MarketCase:
        if row is None:
            raise ValueError("market_case_not_found")
        value = MarketCase.model_validate_json(row.payload_json)
        if (
            value.payload_hash != row.payload_hash
            or value.case_id != row.case_id
            or value.request_id != row.request_id
            or value.outlook_id != row.outlook_id
            or (
                value.scope.market,
                value.scope.instrument,
                value.scope.horizon,
                value.scope.stable_recipe_hash,
            )
            != (row.market, row.instrument, row.horizon, row.recipe_hash)
            or value.available_at != as_utc(row.available_at)
            or value.created_at != as_utc(row.created_at)
        ):
            raise ValueError("market_case_original_invalid")
        return value

    def read(self, case_id: str) -> MarketCase:
        with self.database.session() as session:
            return self._decode(session.get(MarketCaseRecord, case_id))

    def list_in_session(
        self, session: Session, *, cutoff_at: datetime, limit: int = 50
    ) -> tuple[MarketCase, ...]:
        """Read diagnostic/feedback cases too; this does not make them eligible context."""
        cutoff = aware_utc(cutoff_at)
        if not 1 <= limit <= 100:
            raise ValueError("market_case_read_budget_invalid")
        with _read_boundary(session):
            rows = session.scalars(
                select(MarketCaseRecord)
                .where(
                    MarketCaseRecord.available_at <= cutoff,
                )
                .order_by(MarketCaseRecord.available_at.desc(), MarketCaseRecord.case_id)
                .limit(limit)
            ).all()
            return tuple(self._decode(row) for row in rows)

    def relate(
        self, request_id: str, *, left_case_id: str, right_case_id: str, kind: str, reason: str
    ) -> CaseRelation:
        now = aware_utc(self.clock())
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            left = self._decode(session.get(MarketCaseRecord, left_case_id))
            right = self._decode(session.get(MarketCaseRecord, right_case_id))
            if (
                left_case_id == right_case_id
                or (left.scope.market, left.scope.instrument, left.scope.horizon)
                != (right.scope.market, right.scope.instrument, right.scope.horizon)
                or max(left.available_at, right.available_at) > now
            ):
                raise ValueError("market_case_relation_scope_invalid")
            old = session.scalar(
                select(MarketCaseRelationRecord).where(
                    MarketCaseRelationRecord.request_id == request_id
                )
            )
            if old:
                value = self._relation(old)
                if (value.left_case_id, value.right_case_id, value.kind, value.reason) != (
                    left_case_id,
                    right_case_id,
                    kind,
                    reason,
                ):
                    raise ValueError("market_case_relation_request_reused")
                return value
            value = CaseRelation(
                relation_id="mrel_" + canonical_hash({"request_id": request_id})[:40],
                request_id=request_id,
                left_case_id=left_case_id,
                right_case_id=right_case_id,
                left_payload_hash=left.payload_hash,
                right_payload_hash=right.payload_hash,
                kind=kind,
                reason=reason,
                created_at=now,
            )
            session.add(
                MarketCaseRelationRecord(
                    **{
                        key: getattr(value, key)
                        for key in (
                            "relation_id",
                            "request_id",
                            "left_case_id",
                            "right_case_id",
                            "kind",
                            "created_at",
                        )
                    },
                    payload_json=value.model_dump_json(),
                    payload_hash=canonical_hash(value.model_dump(mode="json")),
                )
            )
            return value

    @staticmethod
    def _relation(row) -> CaseRelation:
        value = CaseRelation.model_validate_json(row.payload_json)
        if (
            canonical_hash(value.model_dump(mode="json")) != row.payload_hash
            or value.relation_id != row.relation_id
            or value.request_id != row.request_id
            or value.left_case_id != row.left_case_id
            or value.right_case_id != row.right_case_id
            or value.kind != row.kind
            or value.created_at != as_utc(row.created_at)
        ):
            raise ValueError("market_case_relation_original_invalid")
        return value

    def relations_in_session(
        self, session: Session, *, case_id: str, cutoff_at: datetime
    ) -> tuple[CaseRelation, ...]:
        cutoff = aware_utc(cutoff_at)
        with _read_boundary(session):
            case = self._decode(session.get(MarketCaseRecord, case_id))
            if case.available_at > cutoff:
                return ()
            return self._relations(session, case_id, cutoff)

    def _relations(self, session, case_id, cutoff):
        rows = session.scalars(
            select(MarketCaseRelationRecord)
            .where(
                (
                    (MarketCaseRelationRecord.left_case_id == case_id)
                    | (MarketCaseRelationRecord.right_case_id == case_id)
                ),
                MarketCaseRelationRecord.created_at <= cutoff,
            )
            .order_by(MarketCaseRelationRecord.relation_id)
            .limit(101)
        ).all()
        if len(rows) > 100:
            raise ValueError("market_case_relation_budget_exceeded")
        values = tuple(self._relation(row) for row in rows)
        for value in values:
            left = self._decode(session.get(MarketCaseRecord, value.left_case_id))
            right = self._decode(session.get(MarketCaseRecord, value.right_case_id))
            if (
                left.payload_hash != value.left_payload_hash
                or right.payload_hash != value.right_payload_hash
                or max(left.available_at, right.available_at) > value.created_at
                or value.created_at > cutoff
            ):
                raise ValueError("market_case_relation_binding_invalid")
        return values

    def retrieve_in_session(
        self,
        session: Session,
        *,
        scope: CaseScope,
        cutoff_at: datetime,
        query_terms: tuple[str, ...] = (),
        max_items: int = 5,
        memory_enabled: bool = True,
    ) -> CaseRetrieval:
        cutoff = aware_utc(cutoff_at)
        if not 1 <= max_items <= 20 or len(query_terms) > 20:
            raise ValueError("market_case_retrieval_budget_invalid")
        if not memory_enabled:
            return CaseRetrieval(
                scope=scope,
                cutoff_at=cutoff,
                items=(),
                excluded=(),
                memory_enabled=False,
                truncated=False,
                total_scanned=0,
            )
        selected, excluded = [], []
        with _read_boundary(session):
            rows = session.scalars(
                select(MarketCaseRecord)
                .where(
                    MarketCaseRecord.market == scope.market,
                    MarketCaseRecord.instrument == scope.instrument,
                    MarketCaseRecord.horizon == scope.horizon,
                    MarketCaseRecord.available_at <= cutoff,
                )
                .order_by(MarketCaseRecord.available_at.desc(), MarketCaseRecord.case_id)
                .limit(101)
            ).all()
            for row in rows[:100]:
                case = self._decode(row)
                if case.scope != scope:
                    excluded.append((case.case_id, "method_scope_mismatch"))
                    continue
                if not case.eligible:
                    excluded.append((case.case_id, "original_case_ineligible"))
                    continue
                if case.available_at > cutoff:
                    raise ValueError("market_case_pit_index_invalid")
                try:
                    verify_assessment_basis_in_session(
                        session, case.assessment_basis, archive=self.archive
                    )
                    # The stored qualification is a projection, never an authority flag.
                    fresh_assessment, _basis = build_assessment_basis_in_session(
                        session,
                        case.outlook_id,
                        assessed_at=case.created_at,
                        archive=self.archive,
                    )
                    if (
                        fresh_assessment.model_dump(mode="json") != case.assessment
                        or not fresh_assessment.directional_usable
                        or fresh_assessment.label_status != "verified"
                    ):
                        raise ValueError("market_case_qualification_changed")
                except ValueError as error:
                    excluded.append((case.case_id, str(error)[:128]))
                    continue
                try:
                    relations = self._relations(session, case.case_id, cutoff)
                except ValueError as error:
                    if str(error) != "market_case_relation_budget_exceeded":
                        raise
                    excluded.append((case.case_id, "relation_budget_exceeded"))
                    continue
                text = _json(case.model_dump(mode="json")).casefold()
                matches = tuple(term for term in query_terms if term.casefold() in text)
                missing = tuple(term for term in query_terms if term.casefold() not in text)
                differences = [f"missing_query_term:{term}" for term in missing]
                differences.extend(f"{relation.kind}:{relation.reason}" for relation in relations)
                if not relations:
                    differences.append("normal_control_not_bound")
                selected.append((len(matches), case, relations, matches, tuple(differences)))
        selected.sort(
            key=lambda item: (-item[0], -item[1].available_at.timestamp(), item[1].case_id)
        )
        return CaseRetrieval(
            scope=scope,
            cutoff_at=cutoff,
            items=tuple(
                RetrievedCase(
                    case=item[1],
                    payload_hash=item[1].payload_hash,
                    similarities=("exact_scope", *item[3]),
                    differences=item[4],
                    relations=item[2],
                )
                for item in selected[:max_items]
            ),
            excluded=tuple(excluded),
            memory_enabled=True,
            truncated=len(rows) > 100 or len(selected) > max_items,
            total_scanned=min(len(rows), 100),
        )

    def context_in_session(
        self,
        session: Session,
        *,
        scope: CaseScope,
        cutoff_at: datetime,
        query_terms: tuple[str, ...] = (),
        max_items: int = 5,
        memory_enabled: bool = True,
    ) -> tuple[tuple[ResearchInputEvidence, ...], tuple[CaseContextReference, ...], CaseRetrieval]:
        """Factory port: selected history is protected context, never market Fact admission.

        The caller freezes both DTO and immutable case ref/hash, and puts the exact
        evidence IDs into QualityProjectionContextV1.additional_required_context_refs.
        No case is synthesized or upgraded here; memory_off does no case reads.
        """
        retrieval = self.retrieve_in_session(
            session,
            scope=scope,
            cutoff_at=cutoff_at,
            query_terms=query_terms,
            max_items=max_items,
            memory_enabled=memory_enabled,
        )
        references = tuple(case_context_reference(item.case) for item in retrieval.items)
        inputs = tuple(
            ResearchInputEvidence(
                evidence_id="market_case_context_" + reference.payload_hash,
                kind="document",
                authority="unverified",
                source_id="market_case_ledger.v1",
                source_url=None,
                published_at=None,
                observed_at=reference.available_at,
                received_at=reference.available_at,
                content_hash=reference.payload_hash,
                excerpt=reference.excerpt,
            )
            for reference in references
        )
        return inputs, references, retrieval
