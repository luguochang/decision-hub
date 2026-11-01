from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select, text, update
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.study_effect_tracking import (
    StudyEffectTrackingCommand,
    StudyEffectTrackingGroup,
    StudyEffectTrackingMissingWindow,
    StudyEffectTrackingPlanRef,
    StudyEffectTrackingReceipt,
    StudyEffectTrackingRequestStatus,
    StudyEffectTrackingStatus,
    StudyEffectTrackingWindowStatus,
)
from packages.kernel.decision_hub_kernel.application.calibration import (
    load_production_in_session,
    bind_registration_sources_in_session,
    prepare_registration_sources_in_session,
    persist_plan_in_session,
    registration_originals_scope,
    register_plan_from_outlook_in_session,
    verify_plan_registrations_in_session,
)
from packages.kernel.decision_hub_kernel.application.calibration_inputs import (
    CalibrationEvaluationPolicy,
    CalibrationPlan,
    canonical_json,
    content_hash,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalOutlookRecord,
    ArtifactRecord,
    Database,
    ProductCommandRecord,
    ReportRevisionRecord,
    RunRecord,
    StrategyCalibrationJobRecord,
    StudyEffectTrackingRequestJobRecord,
    StudyRecord,
    as_utc,
    utcnow,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from packages.kernel.decision_hub_kernel.persistence.original_preparation import (
    begin_immediate,
    prepare_originals,
)
from packages.kernel.decision_hub_kernel.persistence.sqlite_busy import is_sqlite_storage_busy

_KIND = "effect_sampling"
_HORIZONS = ("30m", "24h", "72h")
_SAMPLING_DAYS = 90
_MAX_PRIOR_COMMANDS = 128
_STATUS_LIMIT = 12
_MAX_RECEIPT_BYTES = 64 * 1024
_REQUEST_LEASE = timedelta(minutes=3)
_REQUEST_MAX_ATTEMPTS = 5
_MAX_REQUEST_SOURCE_BYTES = 8 * 1024 * 1024
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _RequestClaim:
    request_id: str
    lease_owner: str
    attempt: int
    identity_json: str


def _accepted_source(originals: dict[str, Any]) -> dict[str, Any]:
    # Pausing/resuming a Study after acceptance does not revoke this command.
    # All report/Run/Artifact/window columns and Study identity remain exact.
    return {**originals, "study": {key: value for key, value in originals["study"].items()
                                    if key not in {"status", "updated_at"}}}


def _request_identity(row: StudyEffectTrackingRequestJobRecord) -> dict[str, Any]:
    return {key: _storage_value(getattr(row, key)) for key in (
        "request_id", "owner_id", "study_id", "revision_id", "source_run_id", "artifact_id",
        "payload_json", "payload_hash", "source_json", "source_hash", "accepted_at",
    )}


def _request_command(row: StudyEffectTrackingRequestJobRecord) -> StudyEffectTrackingCommand:
    for value in (row.request_id, row.owner_id, row.study_id, row.revision_id,
                  row.source_run_id, row.artifact_id):
        _identifier(value)
    try:
        command = StudyEffectTrackingCommand.model_validate_json(row.payload_json)
        source = json.loads(row.source_json)
        if (row.payload_json != canonical_json(command.model_dump(mode="json"))
                or command.request_id != row.request_id or command.revision_id != row.revision_id
                or row.payload_hash != _fingerprint(row.owner_id, row.study_id, command)
                or len(row.source_json.encode("utf-8")) > _MAX_REQUEST_SOURCE_BYTES
                or row.source_json != canonical_json(source)
                or row.source_hash != content_hash(source)
                or set(source) != {"study", "revision", "run", "artifact", "outlooks"}
                or source["study"]["study_id"] != row.study_id
                or source["revision"]["revision_id"] != row.revision_id
                or source["revision"]["source_run_id"] != row.source_run_id
                or source["revision"]["artifact_id"] != row.artifact_id
                or source["run"]["run_id"] != row.source_run_id
                or source["artifact"]["artifact_id"] != row.artifact_id
                or not 1 <= len(source["outlooks"]) <= 3):
            raise ValueError("effect_tracking_request_original_invalid")
        accepted, updated = as_utc(row.accepted_at), as_utc(row.updated_at)
        if (accepted is None or updated is None or updated < accepted
                or type(row.attempts) is not int or not 0 <= row.attempts <= _REQUEST_MAX_ATTEMPTS
                or row.state not in {"accepted", "running", "retrying", "completed", "failed"}
                or (row.state == "accepted" and row.attempts != 0)
                or (row.state in {"running", "retrying"} and row.attempts < 1)
                or (row.state == "running") != (row.lease_owner is not None and row.lease_expires_at is not None)
                or (row.state != "running" and (row.lease_owner is not None or row.lease_expires_at is not None))
                or (row.state in {"accepted", "retrying"}) != (row.next_attempt_at is not None)
                or (row.state == "failed" and not row.error_code)
                or (row.state in {"accepted", "running", "completed"} and row.error_code is not None)):
            raise ValueError("effect_tracking_request_original_invalid")
        return command
    except (ValueError, TypeError, KeyError) as error:
        raise ValueError("effect_tracking_request_original_invalid") from error


def _request_error(error: Exception) -> tuple[str, bool]:
    if is_sqlite_storage_busy(error):
        return "effect_tracking_storage_busy", True
    if isinstance(error, (ValueError, PermissionError, KeyError)):
        code = str(error.args[0]) if error.args else ""
        if re.fullmatch(r"[a-z][a-z0-9_]{0,127}", code):
            return code, code == "original_preparation_readset_changed"
    return "effect_tracking_registration_failed", False


def _clock(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("effect_tracking_clock_timezone_required")
    return value.astimezone(UTC)


def _identifier(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError("effect_tracking_identity_invalid")


def _fingerprint(owner: str, study_id: str, command: StudyEffectTrackingCommand) -> str:
    # The existing ProductDeliveryService command namespace/canonical convention.
    body = json.dumps([owner, study_id, _KIND, command.model_dump(mode="json")],
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _plan_request(owner: str, study_id: str, request_id: str, horizon: str) -> str:
    return "effectplan_" + content_hash([owner, study_id, request_id, horizon])


def _storage_value(value: Any) -> Any:
    # Only authenticated internal SQLite row values use its naive-UTC codec.
    return as_utc(value).isoformat() if isinstance(value, datetime) else value


def _row_body(row: Any) -> dict[str, Any]:
    return {column.key: _storage_value(getattr(row, column.key))
            for column in row.__table__.columns}


def _source_binding(session: Session, study_id: str, revision_id: str) -> tuple[
    ReportRevisionRecord, list[AnalyticalOutlookRecord], dict[str, Any]
]:
    study = session.get(StudyRecord, study_id, populate_existing=True)
    revision = session.get(ReportRevisionRecord, revision_id, populate_existing=True)
    if study is None:
        raise KeyError("study_not_found")
    if revision is None or revision.study_id != study_id:
        raise KeyError("study_report_revision_not_found")
    run = session.get(RunRecord, revision.source_run_id, populate_existing=True)
    artifact = session.get(ArtifactRecord, revision.artifact_id, populate_existing=True)
    if (revision.strategy_version != "research.v1" or run is None or artifact is None
            or run.study_id != study_id or run.strategy_version != revision.strategy_version
            or run.status not in {"completed", "degraded"} or run.finished_at is None
            or run.artifact_id != revision.artifact_id or artifact.run_id != run.run_id
            or artifact.event_id != run.event_id
            or as_utc(artifact.created_at) != as_utc(revision.created_at)):
        raise ValueError("effect_tracking_source_binding_invalid")
    outlooks = list(session.scalars(select(AnalyticalOutlookRecord).where(
        AnalyticalOutlookRecord.run_id == run.run_id,
        AnalyticalOutlookRecord.method_family == "ai_synthesis",
    ).order_by(AnalyticalOutlookRecord.horizon, AnalyticalOutlookRecord.outlook_id)
        .limit(4).execution_options(populate_existing=True)))
    if len(outlooks) > 3 or len({row.horizon for row in outlooks}) != len(outlooks):
        raise ValueError("effect_tracking_ai_windows_ambiguous")
    for row in outlooks:
        if (row.horizon not in _HORIZONS or row.study_id != study_id
                or row.artifact_id != artifact.artifact_id or row.market != study.market
                or row.instrument != study.instrument):
            raise ValueError("effect_tracking_source_binding_invalid")
    originals = {"study": _row_body(study), "revision": _row_body(revision),
                 "run": _row_body(run), "artifact": _row_body(artifact),
                 "outlooks": [_row_body(row) for row in outlooks]}
    return revision, outlooks, originals


def _plan_ref(plan: CalibrationPlan, outlook_id: str) -> StudyEffectTrackingPlanRef:
    return StudyEffectTrackingPlanRef(
        horizon=plan.scope.horizon, outlook_id=outlook_id, job_id=plan.job_id,
        producer_request_id=plan.request_id,
        plan_hash=content_hash(plan.model_dump(mode="json")),
        scope_hash=content_hash(plan.scope.model_dump(mode="json")),
        stable_recipe_hash=plan.scope.stable_recipe_hash,
        strategy_version=plan.scope.strategy_version,
        market=plan.scope.market, instrument=plan.scope.instrument,
    )


def _plan_original(session: Session, receipt: StudyEffectTrackingReceipt,
                   reference: StudyEffectTrackingPlanRef) -> tuple[StrategyCalibrationJobRecord, CalibrationPlan]:
    row = session.get(StrategyCalibrationJobRecord, reference.job_id, populate_existing=True)
    if row is None:
        raise ValueError("effect_tracking_plan_original_invalid")
    try:
        plan = CalibrationPlan.model_validate_json(row.plan_json)
    except ValueError as error:
        raise ValueError("effect_tracking_plan_original_invalid") from error
    if (row.job_id != plan.job_id or row.request_id != plan.request_id
            or row.plan_json != canonical_json(plan.model_dump(mode="json"))
            or row.plan_hash != reference.plan_hash
            or as_utc(row.registered_at) != plan.registered_at
            or plan.registered_at != receipt.recorded_at or plan.start_at != receipt.start_at
            or plan.end_at != receipt.end_at
            or plan.approval_source_ref != "product-command:" + receipt.request_id
            or plan.request_id != _plan_request(receipt.owner_id, receipt.study_id,
                                               receipt.request_id, reference.horizon)
            or plan.policy.purpose != "prospective_research_calibration"
            or _plan_ref(plan, reference.outlook_id) != reference):
        raise ValueError("effect_tracking_plan_original_invalid")
    return row, plan


def _receipt(session: Session, row: ProductCommandRecord, study_id: str,
             owner: str, request_id: str) -> StudyEffectTrackingReceipt:
    if (row.request_id != request_id or row.owner_id != owner
            or row.target_id != study_id or row.kind != _KIND):
        raise ValueError("product_request_reused")
    try:
        if len(row.result_json.encode("utf-8")) > _MAX_RECEIPT_BYTES:
            raise ValueError("effect_tracking_receipt_invalid")
        result = StudyEffectTrackingReceipt.model_validate_json(row.result_json)
        command = StudyEffectTrackingCommand(request_id=request_id, revision_id=result.revision_id)
        if (result.request_id != request_id or result.study_id != study_id
                or result.owner_id != owner or result.recorded_at != as_utc(row.created_at)
                or result.start_at != result.recorded_at
                or result.end_at != result.start_at + timedelta(days=_SAMPLING_DAYS)
                or len({ref.horizon for ref in result.plans}) != len(result.plans)
                or len({ref.outlook_id for ref in result.plans}) != len(result.plans)
                or len({ref.job_id for ref in result.plans}) != len(result.plans)
                or {ref.horizon for ref in result.plans}.intersection(
                    missing.horizon for missing in result.missing_windows)
                or {ref.horizon for ref in result.plans}.union(
                    missing.horizon for missing in result.missing_windows) != set(_HORIZONS)
                or len({missing.horizon for missing in result.missing_windows}) != len(result.missing_windows)):
            raise ValueError("effect_tracking_receipt_invalid")
    except ValueError as error:
        raise ValueError("effect_tracking_receipt_invalid") from error
    if row.payload_hash != _fingerprint(owner, study_id, command):
        raise ValueError("product_request_reused")
    revision, outlooks, _originals = _source_binding(session, study_id, result.revision_id)
    if (revision.source_run_id != result.source_run_id or revision.artifact_id != result.artifact_id
            or {ref.outlook_id for ref in result.plans} != {item.outlook_id for item in outlooks}):
        raise ValueError("effect_tracking_receipt_invalid")
    actual_windows = {item.outlook_id: item.horizon for item in outlooks}
    for reference in result.plans:
        if actual_windows.get(reference.outlook_id) != reference.horizon:
            raise ValueError("effect_tracking_receipt_invalid")
        _plan_original(session, result, reference)
    return result


class StudyEffectTrackingService:
    """One owner command approves exact saved AI windows, without creating research."""

    def __init__(self, database: Database, *, archive: RawBodyArchive,
                 clock: Callable[[], datetime] = utcnow) -> None:
        self.database, self.archive, self.clock = database, archive, clock
        self._request_lock = asyncio.Lock()

    def start(self, study_id: str, owner: str,
              command: StudyEffectTrackingCommand) -> StudyEffectTrackingReceipt:
        _identifier(study_id)
        _identifier(owner)
        digest = _fingerprint(owner, study_id, command)
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            old = session.get(ProductCommandRecord, command.request_id, populate_existing=True)
            if old is not None:
                result = _receipt(session, old, study_id, owner, command.request_id)
                if old.payload_hash != digest or result.revision_id != command.revision_id:
                    raise ValueError("product_request_reused")
                return result
            now = _clock(self.clock())
            end = now + timedelta(days=_SAMPLING_DAYS)
            revision, outlooks, source_before = _source_binding(session, study_id, command.revision_id)
            if not outlooks:
                raise ValueError("effect_tracking_ai_outlooks_unavailable")
            # Source authentication is done while the same physical writer is clean.
            # No first job is added before all three original source reads finish.
            policy = CalibrationEvaluationPolicy(purpose="prospective_research_calibration")
            with registration_originals_scope(session):
                prepared = [(row, register_plan_from_outlook_in_session(
                    session, request_id=_plan_request(owner, study_id, command.request_id, row.horizon),
                    outlook_id=row.outlook_id, policy=policy, start_at=now, end_at=end,
                    approval_source_ref="product-command:" + command.request_id,
                    registered_at=now, archive=self.archive,
                )) for row in outlooks]
            for outlook, plan in prepared:
                if (plan.scope.market != outlook.market or plan.scope.instrument != outlook.instrument
                        or plan.scope.horizon != outlook.horizon or plan.registered_at != now
                        or plan.start_at != now or plan.end_at != end):
                    raise ValueError("effect_tracking_source_binding_invalid")
            # Explicit new requests cannot multiply this Study's same-recipe active plans.
            prior = list(session.scalars(select(ProductCommandRecord).where(
                ProductCommandRecord.owner_id == owner, ProductCommandRecord.target_id == study_id,
                ProductCommandRecord.kind == _KIND,
            ).order_by(ProductCommandRecord.created_at, ProductCommandRecord.request_id)
                .limit(_MAX_PRIOR_COMMANDS + 1).execution_options(populate_existing=True)))
            if len(prior) > _MAX_PRIOR_COMMANDS:
                raise ValueError("effect_tracking_command_read_limit")
            scopes = {content_hash(plan.scope.model_dump(mode="json")) for _row, plan in prepared}
            for stored in prior:
                existing = _receipt(session, stored, study_id, owner, stored.request_id)
                if existing.end_at > now and any(ref.scope_hash in scopes for ref in existing.plans):
                    raise ValueError("effect_tracking_plan_already_registered")
            if _source_binding(session, study_id, command.revision_id)[2] != source_before:
                raise ValueError("effect_tracking_source_changed")
            references = [_plan_ref(plan, row.outlook_id) for row, plan in prepared]
            result = StudyEffectTrackingReceipt(
                schema_version="study-effect-tracking-receipt.v1", request_id=command.request_id,
                owner_id=owner, study_id=study_id, revision_id=revision.revision_id,
                source_run_id=revision.source_run_id, artifact_id=revision.artifact_id,
                recorded_at=now, start_at=now, end_at=end, plans=references,
                missing_windows=[StudyEffectTrackingMissingWindow(
                    horizon=horizon, reason_code="effect_tracking_window_unavailable",
                ) for horizon in _HORIZONS if horizon not in {row.horizon for row in outlooks}],
                probability_qualified=False,
            )
            verify_plan_registrations_in_session(session)
            for _row, plan in prepared:
                persisted = persist_plan_in_session(session, plan=plan)
                if persisted != plan:
                    raise ValueError("effect_tracking_plan_original_invalid")
            session.add(ProductCommandRecord(
                request_id=command.request_id, owner_id=owner, target_id=study_id,
                kind=_KIND, payload_hash=digest, result_json=result.model_dump_json(), created_at=now,
            ))
            session.flush()
            # A flush hook cannot rewrite authenticated inputs or the audit receipt.
            verify_plan_registrations_in_session(session)
            if _source_binding(session, study_id, command.revision_id)[2] != source_before:
                raise ValueError("effect_tracking_source_changed")
            stored = session.get(ProductCommandRecord, command.request_id, populate_existing=True)
            if stored is None or stored.result_json != result.model_dump_json():
                raise ValueError("effect_tracking_receipt_invalid")
            if _receipt(session, stored, study_id, owner, command.request_id) != result:
                raise ValueError("effect_tracking_receipt_invalid")
            return result

    def _request_status(self, session: Session, row: StudyEffectTrackingRequestJobRecord,
                        study_id: str, owner: str, request_id: str) -> StudyEffectTrackingRequestStatus:
        if row.owner_id != owner:
            raise PermissionError("effect_tracking_request_owner_mismatch")
        if row.study_id != study_id or row.request_id != request_id:
            raise ValueError("product_request_reused")
        _request_command(row)
        final = None
        if row.state == "completed":
            command = session.get(ProductCommandRecord, request_id, populate_existing=True)
            if command is None:
                raise ValueError("effect_tracking_request_completion_invalid")
            final = _receipt(session, command, study_id, owner, request_id)
            if (final.revision_id != row.revision_id or final.source_run_id != row.source_run_id
                    or final.artifact_id != row.artifact_id or command.payload_hash != row.payload_hash):
                raise ValueError("effect_tracking_request_completion_invalid")
        return StudyEffectTrackingRequestStatus(
            schema_version="study-effect-tracking-request-status.v1",
            request_id=request_id, owner_id=owner, study_id=study_id, revision_id=row.revision_id,
            source_run_id=row.source_run_id, artifact_id=row.artifact_id, state=row.state,
            accepted_at=as_utc(row.accepted_at), updated_at=as_utc(row.updated_at),
            next_attempt_at=as_utc(row.next_attempt_at), attempts=row.attempts,
            error_code=row.error_code, final_receipt=final, can_retry=False,
        )

    def admit_request(self, study_id: str, owner: str,
                      command: StudyEffectTrackingCommand) -> StudyEffectTrackingRequestStatus:
        """Persist exact intent quickly; do not authenticate Graph or add plans."""
        for value in (study_id, owner, command.request_id, command.revision_id):
            _identifier(value)
        digest = _fingerprint(owner, study_id, command)
        with self.database.session() as session:
            begin_immediate(session)
            request = session.get(StudyEffectTrackingRequestJobRecord, command.request_id,
                                  populate_existing=True)
            if request is not None:
                if (request.payload_hash != digest or request.revision_id != command.revision_id
                        or request.payload_json != canonical_json(command.model_dump(mode="json"))):
                    raise ValueError("product_request_reused")
                return self._request_status(session, request, study_id, owner, command.request_id)
            old = session.get(ProductCommandRecord, command.request_id, populate_existing=True)
            final = _receipt(session, old, study_id, owner, command.request_id) if old is not None else None
            if old is not None and (old.payload_hash != digest or final.revision_id != command.revision_id):
                raise ValueError("product_request_reused")
            revision, outlooks, before = _source_binding(session, study_id, command.revision_id)
            if not outlooks:
                raise ValueError("effect_tracking_ai_outlooks_unavailable")
            anchor = _accepted_source(before)
            body = canonical_json(anchor)
            if len(body.encode("utf-8")) > _MAX_REQUEST_SOURCE_BYTES:
                raise ValueError("effect_tracking_request_source_limit")
            now = _clock(self.clock())
            request = StudyEffectTrackingRequestJobRecord(
                request_id=command.request_id, owner_id=owner, study_id=study_id,
                revision_id=revision.revision_id, source_run_id=revision.source_run_id,
                artifact_id=revision.artifact_id,
                payload_json=canonical_json(command.model_dump(mode="json")), payload_hash=digest,
                source_json=body, source_hash=content_hash(anchor),
                # An already completed old command has its original acceptance
                # evidence. This wrapper neither re-dates nor re-signs its ACK.
                accepted_at=as_utc(old.created_at) if final is not None else now, updated_at=now,
                state="completed" if final is not None else "accepted", attempts=0,
                next_attempt_at=None if final is not None else now,
                lease_owner=None, lease_expires_at=None, error_code=None,
            )
            expected = canonical_json(_row_body(request))
            session.add(request)
            session.flush()
            if _source_binding(session, study_id, command.revision_id)[2] != before:
                raise ValueError("effect_tracking_source_changed")
            stored = session.get(StudyEffectTrackingRequestJobRecord, command.request_id,
                                 populate_existing=True)
            if stored is None or canonical_json(_row_body(stored)) != expected:
                raise ValueError("effect_tracking_request_original_invalid")
            return self._request_status(session, stored, study_id, owner, command.request_id)

    def get_request(self, study_id: str, owner: str,
                    request_id: str) -> StudyEffectTrackingRequestStatus | None:
        for value in (study_id, owner, request_id):
            _identifier(value)
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            with session.no_autoflush:
                row = session.get(StudyEffectTrackingRequestJobRecord, request_id, populate_existing=True)
                return self._request_status(session, row, study_id, owner, request_id) if row else None

    def _claim_request(self) -> _RequestClaim | None:
        # Claims and expired-owner recovery are short, bounded SQL writers.
        # A completed/final-failed request is never selected again.
        for _ in range(3):
            now = _clock(self.clock())
            with self.database.session() as session:
                begin_immediate(session)
                row = session.scalar(select(StudyEffectTrackingRequestJobRecord).where(or_(
                    and_(StudyEffectTrackingRequestJobRecord.state.in_(("accepted", "retrying")),
                         StudyEffectTrackingRequestJobRecord.next_attempt_at <= now),
                    and_(StudyEffectTrackingRequestJobRecord.state == "running",
                         StudyEffectTrackingRequestJobRecord.lease_expires_at <= now),
                )).order_by(StudyEffectTrackingRequestJobRecord.accepted_at,
                           StudyEffectTrackingRequestJobRecord.request_id).limit(1)
                    .execution_options(populate_existing=True))
                if row is None:
                    return None
                _request_command(row)
                identity = canonical_json(_request_identity(row))
                old_state, old_attempt = row.state, row.attempts
                conditions = [StudyEffectTrackingRequestJobRecord.request_id == row.request_id,
                    StudyEffectTrackingRequestJobRecord.state == old_state,
                    StudyEffectTrackingRequestJobRecord.attempts == old_attempt,
                    StudyEffectTrackingRequestJobRecord.payload_hash == row.payload_hash,
                    StudyEffectTrackingRequestJobRecord.source_hash == row.source_hash]
                if old_state == "running":
                    conditions += [StudyEffectTrackingRequestJobRecord.lease_owner == row.lease_owner,
                                   StudyEffectTrackingRequestJobRecord.lease_expires_at <= now]
                else:
                    conditions.append(StudyEffectTrackingRequestJobRecord.next_attempt_at <= now)
                if old_attempt >= _REQUEST_MAX_ATTEMPTS:
                    session.execute(update(StudyEffectTrackingRequestJobRecord).where(*conditions).values(
                        state="failed", updated_at=now, next_attempt_at=None, lease_owner=None,
                        lease_expires_at=None, error_code="effect_tracking_request_attempts_exhausted",
                    ).execution_options(synchronize_session=False))
                    continue
                if old_state == "running":
                    session.execute(update(StudyEffectTrackingRequestJobRecord).where(*conditions).values(
                        state="retrying", updated_at=now,
                        next_attempt_at=now + timedelta(seconds=min(300, 15 * 2 ** (old_attempt - 1))),
                        lease_owner=None, lease_expires_at=None,
                        error_code="effect_tracking_request_lease_expired",
                    ).execution_options(synchronize_session=False))
                    continue
                worker = "effectrequest_" + uuid.uuid4().hex
                changed = session.execute(update(StudyEffectTrackingRequestJobRecord).where(*conditions).values(
                    state="running", attempts=old_attempt + 1, updated_at=now, next_attempt_at=None,
                    lease_owner=worker, lease_expires_at=now + _REQUEST_LEASE, error_code=None,
                ).execution_options(synchronize_session=False)).rowcount
                if changed == 1:
                    return _RequestClaim(row.request_id, worker, old_attempt + 1, identity)
        return None

    def _require_request_claim(self, session: Session, claim: _RequestClaim) -> StudyEffectTrackingRequestJobRecord:
        row = session.get(StudyEffectTrackingRequestJobRecord, claim.request_id, populate_existing=True)
        if row is None:
            raise ValueError("effect_tracking_request_lease_lost")
        _request_command(row)
        if (canonical_json(_request_identity(row)) != claim.identity_json or row.state != "running"
                or row.lease_owner != claim.lease_owner or row.attempts != claim.attempt
                or as_utc(row.lease_expires_at) <= _clock(self.clock())):
            raise ValueError("effect_tracking_request_lease_lost")
        return row

    def _complete_request(self, claim: _RequestClaim) -> bool:
        # No serialized preparation is saved in the job. A restarted owner must
        # authenticate the real originals again in a new SELECT-only snapshot.
        identity = json.loads(claim.identity_json)
        command = StudyEffectTrackingCommand.model_validate_json(identity["payload_json"])
        study_id, owner = identity["study_id"], identity["owner_id"]

        def authenticate(session: Session):
            revision, outlooks, source = _source_binding(session, study_id, command.revision_id)
            if canonical_json(_accepted_source(source)) != identity["source_json"]:
                raise ValueError("effect_tracking_source_changed")
            if not outlooks:
                raise ValueError("effect_tracking_ai_outlooks_unavailable")
            materials = prepare_registration_sources_in_session(
                session, requests=tuple((_plan_request(owner, study_id, command.request_id, row.horizon),
                                         row.outlook_id) for row in outlooks),
                policy=CalibrationEvaluationPolicy(purpose="prospective_research_calibration"),
                approval_source_ref="product-command:" + command.request_id,
                assessed_at=_clock(self.clock()), archive=self.archive,
            )
            if _source_binding(session, study_id, revision.revision_id)[2] != source:
                raise ValueError("effect_tracking_source_changed")
            return materials, canonical_json(source)

        prepared = prepare_originals(self.database, authenticate)
        # The read snapshot has closed. Never upgrade its deferred transaction.
        with self.database.session() as session:
            begin_immediate(session)
            job = self._require_request_claim(session, claim)
            old = session.get(ProductCommandRecord, command.request_id, populate_existing=True)
            if old is not None:
                result = _receipt(session, old, study_id, owner, command.request_id)
                if (old.payload_hash != job.payload_hash or result.revision_id != job.revision_id
                        or result.source_run_id != job.source_run_id or result.artifact_id != job.artifact_id):
                    raise ValueError("product_request_reused")
                return self._finish_request_in_session(session, claim)
            prepared.verify(session)
            materials, source_json = prepared.value
            revision, outlooks, source_before = _source_binding(session, study_id, command.revision_id)
            if (canonical_json(source_before) != source_json
                    or canonical_json(_accepted_source(source_before)) != job.source_json):
                raise ValueError("effect_tracking_source_changed")
            now = _clock(self.clock())
            end = now + timedelta(days=_SAMPLING_DAYS)
            windows = bind_registration_sources_in_session(session, originals=materials,
                registered_at=now, end_at=end, archive=self.archive)
            by_id = {row.outlook_id: row for row in outlooks}
            if {item[0] for item in windows} != set(by_id):
                raise ValueError("effect_tracking_source_binding_invalid")
            for outlook_id, plan in windows:
                row = by_id[outlook_id]
                if (plan.scope.market != row.market or plan.scope.instrument != row.instrument
                        or plan.scope.horizon != row.horizon or plan.registered_at != now
                        or plan.start_at != now or plan.end_at != end
                        or plan.policy != CalibrationEvaluationPolicy(purpose="prospective_research_calibration")
                        or plan.approval_source_ref != "product-command:" + command.request_id
                        or plan.request_id != _plan_request(owner, study_id, command.request_id, row.horizon)):
                    raise ValueError("effect_tracking_source_binding_invalid")
            # Fresh output namespace/prior-active checks belong to this writer.
            prior = list(session.scalars(select(ProductCommandRecord).where(
                ProductCommandRecord.owner_id == owner, ProductCommandRecord.target_id == study_id,
                ProductCommandRecord.kind == _KIND,
            ).order_by(ProductCommandRecord.created_at, ProductCommandRecord.request_id)
                .limit(_MAX_PRIOR_COMMANDS + 1).execution_options(populate_existing=True)))
            if len(prior) > _MAX_PRIOR_COMMANDS:
                raise ValueError("effect_tracking_command_read_limit")
            scopes = {content_hash(plan.scope.model_dump(mode="json")) for _outlook, plan in windows}
            for stored in prior:
                existing = _receipt(session, stored, study_id, owner, stored.request_id)
                if existing.end_at > now and any(ref.scope_hash in scopes for ref in existing.plans):
                    raise ValueError("effect_tracking_plan_already_registered")
            result = StudyEffectTrackingReceipt(
                schema_version="study-effect-tracking-receipt.v1", request_id=command.request_id,
                owner_id=owner, study_id=study_id, revision_id=revision.revision_id,
                source_run_id=revision.source_run_id, artifact_id=revision.artifact_id,
                recorded_at=now, start_at=now, end_at=end,
                plans=[_plan_ref(plan, outlook_id) for outlook_id, plan in windows],
                missing_windows=[StudyEffectTrackingMissingWindow(horizon=horizon,
                    reason_code="effect_tracking_window_unavailable")
                    for horizon in _HORIZONS if horizon not in {row.horizon for row in outlooks}],
                probability_qualified=False,
            )
            verify_plan_registrations_in_session(session)
            for _outlook_id, plan in windows:
                if persist_plan_in_session(session, plan=plan) != plan:
                    raise ValueError("effect_tracking_plan_original_invalid")
            session.add(ProductCommandRecord(request_id=command.request_id, owner_id=owner,
                target_id=study_id, kind=_KIND, payload_hash=job.payload_hash,
                result_json=result.model_dump_json(), created_at=now))
            session.flush()
            verify_plan_registrations_in_session(session)
            prepared.verify_after_writes(session)
            if _source_binding(session, study_id, command.revision_id)[2] != source_before:
                raise ValueError("effect_tracking_source_changed")
            stored = session.get(ProductCommandRecord, command.request_id, populate_existing=True)
            if (stored is None or stored.result_json != result.model_dump_json()
                    or _receipt(session, stored, study_id, owner, command.request_id) != result):
                raise ValueError("effect_tracking_receipt_invalid")
            # Job completion and its final ACK/plans commit together.
            return self._finish_request_in_session(session, claim)

    def _finish_request_in_session(self, session: Session, claim: _RequestClaim) -> bool:
        job = self._require_request_claim(session, claim)
        final = session.get(ProductCommandRecord, claim.request_id, populate_existing=True)
        if final is None or final.payload_hash != job.payload_hash:
            raise ValueError("effect_tracking_request_completion_invalid")
        _receipt(session, final, job.study_id, job.owner_id, job.request_id)
        now = _clock(self.clock())
        changed = session.execute(update(StudyEffectTrackingRequestJobRecord).where(
            StudyEffectTrackingRequestJobRecord.request_id == claim.request_id,
            StudyEffectTrackingRequestJobRecord.state == "running",
            StudyEffectTrackingRequestJobRecord.lease_owner == claim.lease_owner,
            StudyEffectTrackingRequestJobRecord.attempts == claim.attempt,
            StudyEffectTrackingRequestJobRecord.lease_expires_at > now,
            StudyEffectTrackingRequestJobRecord.payload_hash == job.payload_hash,
            StudyEffectTrackingRequestJobRecord.source_hash == job.source_hash,
        ).values(state="completed", updated_at=now, next_attempt_at=None, lease_owner=None,
                 lease_expires_at=None, error_code=None).execution_options(synchronize_session=False)).rowcount
        if changed != 1:
            raise ValueError("effect_tracking_request_lease_lost")
        return True

    def _fail_request(self, claim: _RequestClaim, error: Exception) -> bool:
        code, retryable = _request_error(error)
        if code == "effect_tracking_request_lease_lost":
            return False  # This owner may not touch the replacement/expired claim.
        with self.database.session() as session:
            begin_immediate(session)
            job = self._require_request_claim(session, claim)
            now = _clock(self.clock())
            retry = retryable and claim.attempt < _REQUEST_MAX_ATTEMPTS
            changed = session.execute(update(StudyEffectTrackingRequestJobRecord).where(
                StudyEffectTrackingRequestJobRecord.request_id == claim.request_id,
                StudyEffectTrackingRequestJobRecord.state == "running",
                StudyEffectTrackingRequestJobRecord.lease_owner == claim.lease_owner,
                StudyEffectTrackingRequestJobRecord.attempts == claim.attempt,
                StudyEffectTrackingRequestJobRecord.lease_expires_at > now,
                StudyEffectTrackingRequestJobRecord.payload_hash == job.payload_hash,
                StudyEffectTrackingRequestJobRecord.source_hash == job.source_hash,
            ).values(state="retrying" if retry else "failed", updated_at=now,
                next_attempt_at=now + timedelta(seconds=min(300, 15 * 2 ** (claim.attempt - 1))) if retry else None,
                lease_owner=None, lease_expires_at=None, error_code=code)
                .execution_options(synchronize_session=False)).rowcount
            return changed == 1 and not retry

    def _process_request_batch(self, *, limit: int) -> int:
        processed = 0
        for _ in range(limit):
            claim = self._claim_request()
            if claim is None:
                break
            try:
                processed += int(self._complete_request(claim))
            except Exception as error:
                code, _retry = _request_error(error)
                if code == "effect_tracking_registration_failed":
                    _LOGGER.exception("effect_tracking_request_failed", extra={"request_id": claim.request_id})
                try:
                    processed += int(self._fail_request(claim, error))
                except Exception as failure:
                    if not (is_sqlite_storage_busy(failure)
                            or str(failure) == "effect_tracking_request_lease_lost"):
                        raise
                    # Retain running claim for bounded expired-lease recovery.
        return processed

    async def process_requests(self, *, limit: int = 3) -> int:
        if type(limit) is not int or not 1 <= limit <= 3:
            raise ValueError("effect_tracking_request_batch_invalid")
        async with self._request_lock:
            worker = asyncio.create_task(asyncio.to_thread(self._process_request_batch, limit=limit))
            try:
                return await asyncio.shield(worker)
            except asyncio.CancelledError:
                # The actual leased writer cannot be abandoned by cancelling
                # its Python thread. Join it before releasing this owner lane.
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not worker.cancelled():
                    worker.exception()
                raise

    def get_receipt(self, study_id: str, owner: str,
                    request_id: str) -> StudyEffectTrackingReceipt | None:
        for value in (study_id, owner, request_id):
            _identifier(value)
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            with session.no_autoflush:
                row = session.get(ProductCommandRecord, request_id, populate_existing=True)
                return _receipt(session, row, study_id, owner, request_id) if row else None

    def status(self, study_id: str, owner: str) -> StudyEffectTrackingStatus:
        _identifier(study_id)
        _identifier(owner)
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            with session.no_autoflush:
                if session.get(StudyRecord, study_id) is None:
                    raise KeyError("study_not_found")
                rows = list(session.scalars(select(ProductCommandRecord).where(
                    ProductCommandRecord.owner_id == owner, ProductCommandRecord.target_id == study_id,
                    ProductCommandRecord.kind == _KIND,
                ).order_by(ProductCommandRecord.created_at.desc(), ProductCommandRecord.request_id.desc())
                    .limit(_STATUS_LIMIT + 1).execution_options(populate_existing=True)))
                groups = []
                for row in rows[:_STATUS_LIMIT]:
                    receipt = _receipt(session, row, study_id, owner, row.request_id)
                    windows = [self._window_status(session, receipt, reference) for reference in receipt.plans]
                    groups.append(StudyEffectTrackingGroup(receipt=receipt, windows=windows))
                return StudyEffectTrackingStatus(
                    schema_version="study-effect-tracking-status.v1", study_id=study_id, owner_id=owner,
                    groups=groups, more_available=len(rows) > _STATUS_LIMIT,
                    probability_qualified=False,
                )

    def _window_status(self, session: Session, receipt: StudyEffectTrackingReceipt,
                       reference: StudyEffectTrackingPlanRef) -> StudyEffectTrackingWindowStatus:
        job, plan = _plan_original(session, receipt, reference)
        if job.status not in {"pending", "running", "waiting", "completed", "retry"} or job.attempt < 0:
            raise ValueError("effect_tracking_job_status_invalid")
        reasons = ["calibration_cost_policy_unavailable"] if plan.policy.cost is None else []
        production_state = None
        captured = effective = excluded = None
        captured_at = None
        if job.last_production_id is not None:
            # Exact owned job pointer, never a latest/strategy-wide production query.
            value = load_production_in_session(session, job.last_production_id, archive=self.archive)
            if value.job_id != job.job_id or value.inputs.plan != plan:
                raise ValueError("effect_tracking_production_binding_invalid")
            production_state = value.state
            captured_at = value.captured_at
            reasons = list(dict.fromkeys([*reasons, *value.reasons]))
            captured = len(value.inputs.cohort)
            effective = len(value.inputs.samples)
            excluded = sum(not original.included for original in value.inputs.cohort)
        return StudyEffectTrackingWindowStatus(
            plan=reference, status=job.status, attempt=job.attempt,
            next_attempt_at=as_utc(job.next_attempt_at), error_code=job.error_code,
            production_id=job.last_production_id, production_state=production_state,
            captured_at=captured_at, captured_count=captured, effective_count=effective,
            excluded_count=excluded, reasons=reasons, probability_qualified=False,
        )
