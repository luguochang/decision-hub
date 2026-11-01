"""Read durable tracking authority and the shared non-manual research budget.

All functions borrow the caller's Session. Call admission guards while holding
the SQLite write reservation; they never create consent or repair old records.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, cast

from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import ResearchSessionResult
from packages.kernel.decision_hub_kernel.application.dsh_activity import (
    host_activity_inactive_in_session,
)
from packages.kernel.decision_hub_kernel.application.market_scope import research_scope_enabled
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchCommandService,
)
from packages.kernel.decision_hub_kernel.application.research_run_scope import (
    require_run_research_scope_in_session,
)
from packages.kernel.decision_hub_kernel.ports.research_publication_bundle import (
    FrozenPublicationBundleV1,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord,
    DshSessionLinkRecord,
    ProductCommandRecord,
    ReportRevisionRecord,
    ResearchCommandRecord,
    ResearchResultRecord,
    RunRecord,
    RunRoleOutputRecord,
    StudyRecord,
    as_utc,
)

ConfirmedReportSnapshotIdentity = tuple[str, int, str, datetime | None, int, str, str | None]
ConfirmedReportReader = Callable[
    [Session, str], tuple[FrozenPublicationBundleV1 | None, ConfirmedReportSnapshotIdentity | None]
]


NON_MANUAL_RESEARCH_ORIGINS = ("automatic", "scheduled_recheck")
RESEARCH_RESERVATION_POLICY = "research-admission-reservation.v1"
GLOBAL_DAILY_RESEARCH_LIMIT = 12
ORDINARY_AUTOMATIC_DAILY_LIMIT = 6
TERMINAL_RESEARCH_STATUSES = ("completed", "degraded")


@dataclass(frozen=True)
class TrackingBasis:
    run: RunRecord
    result: ResearchSessionResult
    intent_at: datetime
    command_request_id: str | None = None


@dataclass(frozen=True)
class ResearchAdmissionGuard:
    reason_code: str | None
    details: dict[str, int | str]


def _manual_update_intent_in_session(
    session: Session, run: RunRecord
) -> tuple[datetime, str] | None:
    if run.admission_origin != "manual" or run.parent_run_id is None or not run.study_id:
        return None
    source = session.get(RunRecord, run.parent_run_id)
    if source is None or source.study_id != run.study_id or source.event_id != run.event_id:
        return None
    records = session.query(ResearchCommandRecord).filter_by(target_run_id=run.run_id).all()
    for record in records:
        if record.status != "accepted" or record.command not in {"recheck", "retry"}:
            continue
        original = session.get(ProductCommandRecord, record.request_id)
        if original is None or original.kind != ResearchCommandService.KIND:
            continue
        if original.owner_id == ResearchCommandService.LEGACY_INTERNAL_OWNER:
            continue
        try:
            # Reuse the original receipt/hash verifier; do not duplicate its proof.
            receipt = ResearchCommandService._receipt_view(  # pyright: ignore[reportPrivateUsage]
                original
            )
        except (ValueError, TypeError, KeyError):
            continue
        accepted_at = as_utc(record.created_at)
        if (
            not 1 <= len(receipt.owner_id) <= 128
            or receipt.result.status != "accepted"
            or receipt.result.target_run_id != run.run_id
            or receipt.source_run_id != source.run_id
            or record.run_id != source.run_id
            or receipt.command.command != record.command
            or receipt.command.reason != record.reason
            or receipt.result.created_at != accepted_at
            or accepted_at != as_utc(run.created_at)
            or run.idempotency_key != f"research-command:{record.request_id}"
        ):
            continue
        # Preserve the physical NULL boundary despite ORM non-null annotations.
        if cast(datetime | None, accepted_at) is not None:
            return accepted_at, record.request_id
    return None


def _saved_result_in_session(session: Session, run: RunRecord) -> ResearchSessionResult | None:
    record = session.get(ResearchResultRecord, run.run_id)
    if record is None:
        return None
    try:
        if hashlib.sha256(record.payload_json.encode()).hexdigest() != record.payload_hash:
            return None
        result = ResearchSessionResult.model_validate_json(record.payload_json)
    except ValueError:
        return None
    if (
        record.schema_version != result.schema_version
        or result.status not in TERMINAL_RESEARCH_STATUSES
    ):
        return None
    return result


def _artifact_matches_result_in_session(
    session: Session, run: RunRecord, artifact: ArtifactRecord, result: ResearchSessionResult,
    *, confirmed_report_reader: ConfirmedReportReader | None = None,
) -> bool:
    # Reuse the exact Commit projection. Reading either historical completeness
    # declaration here never regrades the report or grants trading permission.
    from .commit import (
        PUBLICATION_SOURCE_ROLE,
        PUBLICATION_SOURCE_SCHEMA,
        PUBLICATION_SOURCE_SCHEMA_V2,
        PUBLICATION_SOURCE_SCHEMA_V3,
        PUBLICATION_SOURCE_SCHEMA_V4,
        _artifact_body,  # pyright: ignore[reportPrivateUsage]
        _canonical_hash,  # pyright: ignore[reportPrivateUsage]
        _project_research_candidate,  # pyright: ignore[reportPrivateUsage]
    )

    fields = (
        "facts",
        "inferences",
        "counter_thesis",
        "uncertainty",
        "transmission_chain",
        "citations",
    )
    source = (
        session.query(RunRoleOutputRecord)
        .filter_by(run_id=run.run_id, role=PUBLICATION_SOURCE_ROLE)
        .first()
    )
    declarations: tuple[bool, ...] = (False, True)
    candidate: dict[str, object] | None = None
    try:
        original_payload = json.loads(artifact.payload_json)
        if source is not None:
            body = json.loads(source.payload_json)
            digest = hashlib.sha256(source.payload_json.encode()).hexdigest()
            candidate = body.get("candidate") if isinstance(body, dict) else None
            saved = session.get(ResearchResultRecord, run.run_id)
            if (
                not isinstance(body, dict)
                or source.schema_version not in {
                    PUBLICATION_SOURCE_SCHEMA, PUBLICATION_SOURCE_SCHEMA_V2,
                    PUBLICATION_SOURCE_SCHEMA_V3, PUBLICATION_SOURCE_SCHEMA_V4,
                }
                or body.get("schema_version") != source.schema_version
                or source.payload_hash != digest
                or source.output_id != f"publication_{digest[:40]}"
                or not isinstance(candidate, dict)
                or type(candidate.get("research_complete")) is not bool
                or body.get("run_id") != run.run_id
                or body.get("artifact_id") != artifact.artifact_id
                or saved is None
                or body.get("result_payload_hash") != saved.payload_hash
                or body.get("artifact_hash") != _canonical_hash(_artifact_body(artifact))
            ):
                return False
            if source.schema_version != PUBLICATION_SOURCE_SCHEMA:
                # This is the existing confirmed-report read scope, not a fresh
                # material/policy replay or permission to publish or trade.
                if confirmed_report_reader is None:
                    return False
                base_schema = (
                    body.get("base_schema_version")
                    if source.schema_version == PUBLICATION_SOURCE_SCHEMA_V4
                    else source.schema_version
                )
                if base_schema not in {
                    PUBLICATION_SOURCE_SCHEMA, PUBLICATION_SOURCE_SCHEMA_V2,
                    PUBLICATION_SOURCE_SCHEMA_V3,
                }:
                    return False
                bundle, identity = confirmed_report_reader(session, run.run_id)
                if (not isinstance(bundle, FrozenPublicationBundleV1)
                        or not isinstance(identity, tuple) or len(identity) != 7):
                    return False
                request_id, generation, bundle_hash, confirmed_at, state_version, state, selection_hash = identity
                published_at = as_utc(cast(datetime | None, source.created_at))
                if (
                    not isinstance(request_id, str) or not request_id
                    or type(generation) is not int or generation < 1
                    or bundle_hash != _canonical_hash(bundle.model_dump(mode="json"))
                    or type(state_version) is not int or state_version < 1
                    or state != "confirmed"
                    or not isinstance(selection_hash, str) or len(selection_hash) != 64
                    or any(character not in "0123456789abcdef" for character in selection_hash)
                    or not isinstance(confirmed_at, datetime)
                    or as_utc(confirmed_at) is None
                    or published_at is None
                    or cast(datetime, as_utc(confirmed_at)) < published_at
                    or bundle.run_id != run.run_id
                    or bundle.event_id != run.event_id
                    or bundle.artifact_id != artifact.artifact_id
                    or bundle.original_published_at != published_at
                    or bundle.publication_ref.output_id != source.output_id
                    or bundle.publication_ref.role != source.role
                    or bundle.publication_ref.schema_version != source.schema_version
                    or bundle.publication_ref.payload_hash != source.payload_hash
                    or bundle.publication_ref.recorded_at != published_at
                    or body.get("event_id") != run.event_id
                ):
                    return False
            declarations = (cast(bool, candidate["research_complete"]),)
        for declaration in declarations:
            projected = _project_research_candidate(result, declaration)
            if (
                artifact.headline == projected["headline"]
                and artifact.summary == projected["summary"]
                and original_payload == {field: projected[field] for field in fields}
                and (source is None or candidate == projected)
            ):
                return True
    except (ValueError, TypeError, OSError, KeyError):
        return False
    return False


def tracking_basis_in_session(
    session: Session, *, study_id: str, now: datetime,
    confirmed_report_reader: ConfirmedReportReader | None = None,
) -> TrackingBasis | None:
    """Select the newest authorized *intent*, never the latest auto child report.

    Roots retain their existing saved-result compatibility. Newly eligible
    manual children require the original owner receipt and Commit revision.
    """
    study = session.get(StudyRecord, study_id)
    if study is None or study.status != "active":
        return None
    runs = (
        session.query(RunRecord)
        .filter(
            RunRecord.study_id == study_id,
            RunRecord.strategy_version == "research.v1",
            RunRecord.status.in_(TERMINAL_RESEARCH_STATUSES),
            RunRecord.admission_origin.in_(("manual", "automatic", "legacy")),
        )
        .order_by(RunRecord.created_at.desc(), RunRecord.run_id.desc())
        .all()
    )
    for run in runs:
        if not research_scope_enabled(study):
            try:
                require_run_research_scope_in_session(session, run)
            except ValueError:
                return None
        # ORM annotations do not validate physical historical rows on read.
        finished = as_utc(cast(datetime | None, run.finished_at or run.updated_at))
        if finished is None or finished > now:
            continue
        authority_request_id = None
        if run.parent_run_id is None:
            intent_at = as_utc(cast(datetime | None, run.created_at))
        else:
            intent = _manual_update_intent_in_session(session, run)
            if intent is None:
                continue
            intent_at, authority_request_id = intent
            artifact = session.get(ArtifactRecord, run.artifact_id) if run.artifact_id else None
            revision = (
                session.query(ReportRevisionRecord).filter_by(source_run_id=run.run_id).first()
            )
            if (
                artifact is None
                or artifact.run_id != run.run_id
                or artifact.event_id != run.event_id
                or artifact.gate_status not in {"publish", "degraded", "research_only"}
                or revision is None
                or revision.study_id != study_id
                or revision.artifact_id != artifact.artifact_id
                or revision.strategy_version != "research.v1"
                or revision.identity_basis != "commit_transaction"
                or revision.publication_status != artifact.gate_status
                or as_utc(revision.created_at) != as_utc(artifact.created_at)
            ):
                continue
        if intent_at is None or intent_at > now:
            continue
        result = _saved_result_in_session(session, run)
        if result is None or result.finished_at > now:
            continue
        if authority_request_id is not None and result.finished_at < intent_at:
            continue
        artifact = session.get(ArtifactRecord, run.artifact_id) if run.artifact_id else None
        if artifact is not None and artifact.gate_status == "reject":
            continue
        if authority_request_id is not None and (
            artifact is None
            or not _artifact_matches_result_in_session(
                session, run, artifact, result, confirmed_report_reader=confirmed_report_reader,
            )
        ):
            continue
        # Accepted command time is required to equal the child's creation time,
        # so this order is the intent order. Do not deserialize older reports.
        return TrackingBasis(run, result, intent_at, authority_request_id)
    return None


def host_work_unsettled_in_session(session: Session, run_id: str) -> bool:
    link = session.get(DshSessionLinkRecord, run_id)
    return link is not None and (
        link.state not in {"completed", "failed", "cancelled"} or link.terminal_at is None
    )


def research_admission_guard_in_session(
    session: Session,
    *,
    study_id: str,
    now: datetime,
    max_daily_research: int = 12,
    max_study_research_per_hour: int = 2,
    admission_origin: str = "automatic",
) -> ResearchAdmissionGuard:
    """Keep concurrency checks for all origins; quota counts non-manual work."""
    if admission_origin not in (*NON_MANUAL_RESEARCH_ORIGINS, "manual"):
        raise ValueError("run_admission_origin_invalid")
    admitted = session.query(RunRecord).filter(
        RunRecord.strategy_version == "research.v1",
        RunRecord.admission_origin.in_(NON_MANUAL_RESEARCH_ORIGINS),
    )
    daily_limit = min(max_daily_research, GLOBAL_DAILY_RESEARCH_LIMIT)
    ordinary_limit = min(daily_limit, ORDINARY_AUTOMATIC_DAILY_LIMIT)
    daily = admitted.filter(RunRecord.created_at >= now - timedelta(days=1))
    daily_count = daily.count()
    ordinary = daily.filter(RunRecord.admission_origin == "automatic")
    ordinary_count = ordinary.count()
    study_hour = admitted.filter(
        RunRecord.study_id == study_id,
        RunRecord.created_at >= now - timedelta(hours=1),
    )
    study_count = study_hour.count()
    details: dict[str, int | str] = {
        "policy_version": RESEARCH_RESERVATION_POLICY,
        "daily_count": daily_count,
        "daily_limit": daily_limit,
        "ordinary_daily_count": ordinary_count,
        "ordinary_daily_limit": ordinary_limit,
        "scheduled_recheck_daily_count": daily_count - ordinary_count,
        "reserved_review_slots": max(daily_limit - ordinary_limit, 0),
        "study_hour_count": study_count,
        "study_hour_limit": max_study_research_per_hour,
    }
    if (
        session.query(RunRecord)
        .filter(
            RunRecord.study_id == study_id,
            RunRecord.strategy_version == "research.v1",
            RunRecord.status.in_(("admitted", "running")),
        )
        .first()
        is not None
    ):
        return ResearchAdmissionGuard("study_research_active", details)
    unsettled = (
        session.query(DshSessionLinkRecord)
        .join(RunRecord, RunRecord.run_id == DshSessionLinkRecord.run_id)
        .filter(
            RunRecord.study_id == study_id,
            RunRecord.strategy_version == "research.v1",
        )
        .all()
    )
    for link in unsettled:
        if link.state in {"completed", "failed", "cancelled"} and link.terminal_at is not None:
            continue
        original_run = session.get(RunRecord, link.run_id, populate_existing=True)
        if original_run is None or not host_activity_inactive_in_session(
            session, run=original_run, link=link, now=now,
        ):
            return ResearchAdmissionGuard("study_host_terminal_unknown", details)
    if admission_origin != "manual":
        limits = [("global_daily", daily, daily_count, daily_limit, timedelta(days=1))]
        if admission_origin == "automatic":
            limits.append(
                ("ordinary_daily", ordinary, ordinary_count, ordinary_limit, timedelta(days=1))
            )
        limits.append(
            ("study_hour", study_hour, study_count, max_study_research_per_hour, timedelta(hours=1))
        )
        blockers, checks = [], []
        for name, query, count, limit, window in limits:
            if count < limit:
                continue
            blockers.append(name)
            if limit <= 0:
                continue  # A disabled policy has no promised expiry-based recovery.
            release = (
                query.with_entities(RunRecord.created_at)
                .order_by(RunRecord.created_at.asc(), RunRecord.run_id.asc())
                .offset(count - limit)
                .first()
            )
            if release is None or (created := as_utc(release[0])) is None:
                raise ValueError("research_admission_capacity_readset_changed")
            # The existing >= boundary still includes a row at its exact expiry.
            checks.append(created + window + timedelta(microseconds=1))
        if blockers:
            details["budget_blockers"] = ",".join(blockers)
            if checks:
                details["next_capacity_check_at"] = max(checks).isoformat()
            return ResearchAdmissionGuard("research_admission_budget_exhausted", details)
    return ResearchAdmissionGuard(None, details)
