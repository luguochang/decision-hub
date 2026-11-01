"""Authenticated current-scope observations; never historical terminal proof.

All database helpers borrow the caller's Session. The authenticated collector
supplies an independently observed scope and reserves the write transaction.
No helper calls the Host, resumes a session, changes a Link, or grants a retry.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.persistence.db import (
    AdmissionDecisionRecord,
    DshSessionLinkRecord,
    ReviewScheduleRecord,
    RunRecord,
    RunRoleOutputRecord,
    ServiceRuntimeConfigRecord,
    StudyRecord,
    as_utc,
)

SERVICE_ID = "dsh-host-activity"
MAX_AGE = timedelta(seconds=30)
TERMINAL_RUN_STATES = ("completed", "degraded", "failed", "cancelled")
Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Identifier = Annotated[str, Field(min_length=1, max_length=256)]
RunIdentifier = Annotated[str, Field(min_length=1, max_length=128)]
Count = Annotated[int, Field(ge=0, strict=True)]
StrictBoolean = Annotated[bool, Field(strict=True)]


class ActivityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DshHostActivityScope(ActivityModel):
    host_instance_id: UUID
    process_id: Annotated[int, Field(ge=1, strict=True)]
    started_at: AwareDatetime
    hub_base_url: Annotated[str, Field(min_length=1, max_length=2048)]
    workspace_id: Identifier
    workspace_cwd_hash: Sha


class DshHostActivityScopeView(ActivityModel):
    schema_version: Literal["dsh-host-activity-scope.v1"]
    scope: DshHostActivityScope
    observed_at: AwareDatetime


class DshHostActivityBinding(ActivityModel):
    run_id: RunIdentifier
    study_id: RunIdentifier
    dsh_session_id: Identifier
    generation: Annotated[int, Field(ge=1, le=9007199254740991, strict=True)]
    request_hash: Sha
    link_fingerprint: Sha


class DshHostActivityRequest(ActivityModel):
    schema_version: Literal["dsh-host-activity-request.v1"] = "dsh-host-activity-request.v1"
    records: Annotated[list[DshHostActivityBinding], Field(min_length=1, max_length=64)]

    @model_validator(mode="after")
    def unique_runs(self) -> Self:
        if len({item.run_id for item in self.records}) != len(self.records):
            raise ValueError("dsh_activity_duplicate_run")
        if len({item.dsh_session_id for item in self.records}) != len(self.records):
            raise ValueError("dsh_activity_duplicate_session")
        return self


class DshHostActivityRecord(DshHostActivityBinding):
    state: Literal["current_scope_inactive", "active", "unknown"]
    presence: Literal["present", "absent", "unknown"]
    running: StrictBoolean | None
    queue_count: Count | None
    active_job_count: Count | None
    descendant_ids: list[Identifier]
    fence_id: Identifier | None
    reason_code: Identifier
    terminal_state: Literal["unknown"]


class DshHostActivitySnapshot(ActivityModel):
    schema_version: Literal["dsh-host-activity-snapshot.v1"]
    scope: DshHostActivityScope
    observed_at: AwareDatetime
    expires_at: AwareDatetime
    census_complete: StrictBoolean
    records: Annotated[list[DshHostActivityRecord], Field(min_length=1, max_length=64)]

    @model_validator(mode="after")
    def valid_observation(self) -> Self:
        if not (
            self.scope.started_at <= self.observed_at < self.expires_at
            and self.expires_at - self.observed_at <= MAX_AGE
        ):
            raise ValueError("dsh_activity_time_invalid")
        if len({item.run_id for item in self.records}) != len(self.records):
            raise ValueError("dsh_activity_duplicate_run")
        if len({item.dsh_session_id for item in self.records}) != len(self.records):
            raise ValueError("dsh_activity_duplicate_session")
        for item in self.records:
            if len(set(item.descendant_ids)) != len(item.descendant_ids):
                raise ValueError("dsh_activity_duplicate_descendant")
            if item.state == "current_scope_inactive" and not (
                self.census_complete
                and item.presence != "unknown"
                and item.running is False
                and item.queue_count == 0
                and item.active_job_count == 0
                and item.fence_id is not None
            ):
                raise ValueError("dsh_activity_inactive_unproven")
        return self


class ActivityRegistration(DshHostActivityScope):
    schema_version: Literal["dsh-host-activity-registration.v1"]
    transport_fingerprint: Sha
    observed_at: AwareDatetime
    observation_valid: StrictBoolean
    reason_code: Identifier | None
    source_snapshot_hash: Sha | None

    @property
    def scope(self) -> DshHostActivityScope:
        # RuntimeConfigurationView accepts flat primitive settings only.
        return DshHostActivityScope.model_validate(
            self.model_dump(include=set(DshHostActivityScope.model_fields)),
        )


class ActivityReceipt(ActivityModel):
    schema_version: Literal["dsh-host-activity-receipt.v1"]
    authentication_source: Literal["dsh_callback_secret"]
    run_id: Identifier
    run_fingerprint: Sha
    snapshot_output_id: Identifier
    snapshot_hash: Sha
    scope_config_id: Identifier
    scope_fingerprint: Sha
    recorded_at: AwareDatetime


def _encoded(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _time(value: datetime | None) -> datetime:
    result = as_utc(value)
    if result is None:
        raise ValueError("dsh_activity_time_missing")
    return result


def _scalar(value: object) -> object:
    return _time(value).isoformat() if isinstance(value, datetime) else value


def activity_link_fingerprint(link: DshSessionLinkRecord) -> str:
    """Hash the original persisted Link, including mutable activity fields."""
    return _sha(_encoded({
        column.name: _scalar(getattr(link, column.name)) for column in link.__table__.columns
    }))


def _run_fingerprint(run: RunRecord) -> str:
    return _sha(_encoded({
        name: _scalar(getattr(run, name)) for name in (
            "run_id", "study_id", "status", "finished_at", "lease_owner", "lease_expires_at",
        )
    }))


def activity_binding(run: RunRecord, link: DshSessionLinkRecord) -> DshHostActivityBinding:
    if run.run_id != link.run_id or not run.study_id:
        raise ValueError("dsh_activity_original_binding_invalid")
    return DshHostActivityBinding(
        run_id=run.run_id, study_id=run.study_id, dsh_session_id=link.dsh_session_id,
        generation=link.generation, request_hash=link.request_hash,
        link_fingerprint=activity_link_fingerprint(link),
    )


def _terminal_unleased(run: RunRecord, now: datetime) -> bool:
    finished = as_utc(run.finished_at)
    lease = as_utc(run.lease_expires_at)
    return (
        run.status in TERMINAL_RUN_STATES and finished is not None and finished <= now
        and not (run.lease_owner is not None and (lease is None or lease > now))
        and not (lease is not None and lease > now)
    )


def _current_registration(session: Session, now: datetime) -> ServiceRuntimeConfigRecord | None:
    head = session.scalar(select(ServiceRuntimeConfigRecord).where(
        ServiceRuntimeConfigRecord.service_id == SERVICE_ID,
    ).order_by(
        ServiceRuntimeConfigRecord.recorded_at.desc(), ServiceRuntimeConfigRecord.config_id.desc(),
    ).limit(1).execution_options(populate_existing=True))
    if head is None or not now - MAX_AGE <= _time(head.recorded_at) <= now:
        return None
    registration = ActivityRegistration.model_validate_json(head.settings_json)
    if not registration.observation_valid or not (
        registration.started_at <= registration.observed_at <= _time(head.recorded_at)
    ):
        return None
    return head


def _scope_fingerprint(registration: ActivityRegistration) -> str:
    return _sha(_encoded({
        "scope": registration.scope.model_dump(mode="json"),
        "transport_fingerprint": registration.transport_fingerprint,
    }))


def _append_registration(
    session: Session, registration: ActivityRegistration, now: datetime,
) -> ServiceRuntimeConfigRecord:
    encoded = _encoded(registration.model_dump(mode="json"))
    fingerprint = _sha(encoded)
    instance = str(registration.host_instance_id)
    config_id = "host_activity_config_" + _sha(encoded + "\0" + now.isoformat())[:32]
    old = session.scalar(select(ServiceRuntimeConfigRecord).where(
        ServiceRuntimeConfigRecord.service_id == SERVICE_ID,
    ).order_by(
        ServiceRuntimeConfigRecord.recorded_at.desc(), ServiceRuntimeConfigRecord.config_id.desc(),
    ).limit(1).execution_options(populate_existing=True))
    if old is not None:
        previous = ActivityRegistration.model_validate_json(old.settings_json)
        if now < _time(old.recorded_at) or (
            previous.host_instance_id != registration.host_instance_id
            and registration.started_at <= previous.started_at
        ):
            raise ValueError("dsh_activity_observation_out_of_order")
        if previous.host_instance_id == registration.host_instance_id:
            if _scope_fingerprint(previous) != _scope_fingerprint(registration):
                raise ValueError("dsh_activity_scope_registration_conflict")
            if (
                previous.source_snapshot_hash is not None
                and registration.source_snapshot_hash is not None
                and registration.observed_at < previous.observed_at
            ):
                raise ValueError("dsh_activity_observation_out_of_order")
    config = session.get(ServiceRuntimeConfigRecord, config_id, populate_existing=True)
    if config is None:
        config = ServiceRuntimeConfigRecord(
            config_id=config_id, service_id=SERVICE_ID, instance_id=instance,
            process_id=registration.process_id, started_at=registration.started_at,
            recorded_at=now, fingerprint=fingerprint, settings_json=encoded,
        )
        session.add(config)
    elif (
        config.settings_json != encoded or config.fingerprint != fingerprint
        or config.service_id != SERVICE_ID or config.instance_id != instance
        or config.process_id != registration.process_id
        or _time(config.started_at) != registration.started_at
    ):
        raise ValueError("dsh_activity_scope_registration_conflict")
    return config


def register_verified_activity_scope_in_session(
    session: Session, *, expected_scope: DshHostActivityScope,
    transport_fingerprint: str, recorded_at: datetime,
) -> None:
    """API's independent authenticated GET may invalidate receipts from another boot."""
    now = _time(recorded_at)
    if expected_scope.started_at > now:
        raise ValueError("dsh_activity_scope_time_invalid")
    _append_registration(session, ActivityRegistration(
        **expected_scope.model_dump(), schema_version="dsh-host-activity-registration.v1",
        transport_fingerprint=transport_fingerprint, observed_at=now,
        observation_valid=True, reason_code=None, source_snapshot_hash=None,
    ), now)


def invalidate_activity_scope_in_session(
    session: Session, *, recorded_at: datetime, reason_code: str,
) -> None:
    """A failed official-channel probe removes current authority without altering old proof."""
    now = _time(recorded_at)
    head = session.scalar(select(ServiceRuntimeConfigRecord).where(
        ServiceRuntimeConfigRecord.service_id == SERVICE_ID,
    ).order_by(
        ServiceRuntimeConfigRecord.recorded_at.desc(), ServiceRuntimeConfigRecord.config_id.desc(),
    ).limit(1).execution_options(populate_existing=True))
    if head is None:
        return
    old = ActivityRegistration.model_validate_json(head.settings_json)
    body = old.model_dump()
    body.update({
        "observed_at": now, "observation_valid": False, "reason_code": reason_code,
        "source_snapshot_hash": None,
    })
    _append_registration(session, ActivityRegistration.model_validate(body), now)


def freeze_activity_snapshot_in_session(
    session: Session, snapshot: DshHostActivitySnapshot, *,
    expected_scope: DshHostActivityScope, transport_fingerprint: str,
    recorded_at: datetime, raw_snapshot_json: str | None = None,
) -> int:
    """Authenticated API only; expected_scope is independently read from its configured Host."""
    now = _time(recorded_at)
    if not snapshot.observed_at <= now < snapshot.expires_at:
        raise ValueError("dsh_activity_observation_expired")
    raw = raw_snapshot_json or _encoded(snapshot.model_dump(mode="json"))
    if DshHostActivitySnapshot.model_validate_json(raw) != snapshot:
        raise ValueError("dsh_activity_original_snapshot_mismatch")
    runs: list[RunRecord] = []
    for item in snapshot.records:
        run = session.get(RunRecord, item.run_id, populate_existing=True)
        link = session.get(DshSessionLinkRecord, item.run_id, populate_existing=True)
        if (
            run is None or link is None or not _terminal_unleased(run, now)
            or activity_binding(run, link) != DshHostActivityBinding.model_validate(
                item.model_dump(include=set(DshHostActivityBinding.model_fields)),
            )
        ):
            raise ValueError("dsh_activity_original_binding_invalid")
        runs.append(run)
    digest = _sha(raw)
    if snapshot.scope != expected_scope:
        raise ValueError("dsh_activity_scope_mismatch")
    registration = ActivityRegistration(
        **expected_scope.model_dump(), schema_version="dsh-host-activity-registration.v1",
        transport_fingerprint=transport_fingerprint, observed_at=snapshot.observed_at,
        observation_valid=snapshot.census_complete,
        reason_code=None if snapshot.census_complete else "host_activity_census_incomplete",
        source_snapshot_hash=digest,
    )
    config = _append_registration(session, registration, now)
    snapshot_id = "host_activity_snapshot_" + digest
    original = session.get(RunRoleOutputRecord, snapshot_id, populate_existing=True)
    if original is None:
        session.add(RunRoleOutputRecord(
            output_id=snapshot_id, run_id=snapshot.records[0].run_id,
            role="dsh_activity_snapshot:" + digest[:32], payload_json=raw,
            payload_hash=digest, schema_version=snapshot.schema_version, created_at=now,
        ))
    elif original.payload_json != raw or original.payload_hash != digest:
        raise ValueError("dsh_activity_original_snapshot_conflict")
    created = 0
    for run in runs:
        output_id = "host_activity_" + _sha(run.run_id + "\0" + digest)
        existing = session.get(RunRoleOutputRecord, output_id, populate_existing=True)
        if existing is not None:
            original_receipt = ActivityReceipt.model_validate_json(existing.payload_json)
            if not (
                existing.payload_hash == _sha(existing.payload_json)
                and existing.payload_json == _encoded(original_receipt.model_dump(mode="json"))
                and existing.schema_version == original_receipt.schema_version
                and existing.role == "dsh_activity:" + digest[:32]
                and existing.run_id == run.run_id
                and original_receipt.run_id == run.run_id
                and original_receipt.run_fingerprint == _run_fingerprint(run)
                and original_receipt.snapshot_output_id == snapshot_id
                and original_receipt.snapshot_hash == digest
                and original_receipt.scope_fingerprint == _scope_fingerprint(registration)
                and _time(existing.created_at) == original_receipt.recorded_at <= now
            ):
                raise ValueError("dsh_activity_original_receipt_conflict")
            continue
        receipt = ActivityReceipt(
            schema_version="dsh-host-activity-receipt.v1",
            authentication_source="dsh_callback_secret", run_id=run.run_id,
            run_fingerprint=_run_fingerprint(run), snapshot_output_id=snapshot_id,
            snapshot_hash=digest, scope_config_id=config.config_id,
            scope_fingerprint=_scope_fingerprint(registration), recorded_at=now,
        )
        payload = _encoded(receipt.model_dump(mode="json"))
        session.add(RunRoleOutputRecord(
            output_id=output_id, run_id=run.run_id, role="dsh_activity:" + digest[:32],
            payload_json=payload, payload_hash=_sha(payload),
            schema_version=receipt.schema_version, created_at=now,
        ))
        created += 1
    return created


def host_activity_inactive_in_session(
    session: Session, *, run: RunRecord, link: DshSessionLinkRecord, now: datetime,
) -> bool:
    """Only clear the current managed-scope mutex, never certify old completion."""
    current = _time(now)
    try:
        original_run = session.get(RunRecord, run.run_id, populate_existing=True)
        original_link = session.get(DshSessionLinkRecord, link.run_id, populate_existing=True)
        if original_run is None or original_link is None or run.run_id != link.run_id:
            return False
        run, link = original_run, original_link
        if not _terminal_unleased(run, current):
            return False
        head = _current_registration(session, current)
        if head is None:
            return False
        registration = ActivityRegistration.model_validate_json(head.settings_json)
        if (
            head.fingerprint != _sha(head.settings_json)
            or head.settings_json != _encoded(registration.model_dump(mode="json"))
            or head.instance_id != str(registration.scope.host_instance_id)
            or head.process_id != registration.scope.process_id
            or _time(head.started_at) != registration.scope.started_at
        ):
            return False
        output = session.scalar(select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == run.run_id,
            RunRoleOutputRecord.role.like("dsh_activity:%"),
        ).order_by(RunRoleOutputRecord.created_at.desc(), RunRoleOutputRecord.output_id.desc())
            .limit(1).execution_options(populate_existing=True))
        if output is None or output.payload_hash != _sha(output.payload_json):
            return False
        receipt = ActivityReceipt.model_validate_json(output.payload_json)
        if (
            output.schema_version != receipt.schema_version or receipt.run_id != run.run_id
            or receipt.run_fingerprint != _run_fingerprint(run)
            or receipt.scope_fingerprint != _scope_fingerprint(registration)
            or output.payload_json != _encoded(receipt.model_dump(mode="json"))
            or _time(output.created_at) != receipt.recorded_at
        ):
            return False
        original_config = session.get(
            ServiceRuntimeConfigRecord, receipt.scope_config_id, populate_existing=True,
        )
        if original_config is None or not (
            original_config.service_id == SERVICE_ID
            and original_config.fingerprint == _sha(original_config.settings_json)
            and _time(original_config.recorded_at) == receipt.recorded_at
        ):
            return False
        original_registration = ActivityRegistration.model_validate_json(
            original_config.settings_json,
        )
        if not (
            original_registration.observation_valid
            and _scope_fingerprint(original_registration) == receipt.scope_fingerprint
            and original_registration.source_snapshot_hash == receipt.snapshot_hash
        ):
            return False
        original = session.get(
            RunRoleOutputRecord, receipt.snapshot_output_id, populate_existing=True,
        )
        if original is None or original.payload_hash != receipt.snapshot_hash:
            return False
        snapshot = DshHostActivitySnapshot.model_validate_json(original.payload_json)
        if (
            original.schema_version != snapshot.schema_version
            or original.run_id != snapshot.records[0].run_id
            or original.role != "dsh_activity_snapshot:" + receipt.snapshot_hash[:32]
            or _sha(original.payload_json) != receipt.snapshot_hash
            or original.output_id != "host_activity_snapshot_" + receipt.snapshot_hash
            or output.output_id != "host_activity_" + _sha(
                run.run_id + "\0" + receipt.snapshot_hash,
            )
            or output.role != "dsh_activity:" + receipt.snapshot_hash[:32]
            or snapshot.scope != registration.scope
            or not snapshot.observed_at <= _time(original.created_at) <= receipt.recorded_at
            or not snapshot.observed_at <= receipt.recorded_at <= current < snapshot.expires_at
        ):
            return False
        matches = [item for item in snapshot.records if item.run_id == run.run_id]
        if len(matches) != 1:
            return False
        item = matches[0]
        return (
            item.state == "current_scope_inactive" and snapshot.census_complete
            and DshHostActivityBinding.model_validate(
                item.model_dump(include=set(DshHostActivityBinding.model_fields)),
            ) == activity_binding(run, link)
        )
    except (ValueError, TypeError, KeyError):
        return False


def _blocked_risk_research_studies(session: Session, now: datetime) -> set[str]:
    # Local imports avoid the admission -> tracking -> activity import cycle.
    from ..persistence.risk_research_records import (
        RiskResearchRequestRecord,
    )
    from .risk_admission_wait import recognized_condition_waits
    from .risk_research_admission import (
        _recognized_request_triggers,  # pyright: ignore[reportPrivateUsage]
    )
    from .study import stable_id

    requests = session.scalars(select(RiskResearchRequestRecord).where(
        or_(
            (
                (RiskResearchRequestRecord.status == "pending_admission")
                & RiskResearchRequestRecord.error_code.in_((
                    "study_host_terminal_unknown", "study_host_activity_refresh_required",
                ))
                & RiskResearchRequestRecord.attempts.in_((0, 1, 2))
                & RiskResearchRequestRecord.lease_owner.is_(None)
                & RiskResearchRequestRecord.lease_expires_at.is_(None)
                & (RiskResearchRequestRecord.available_at >= now - timedelta(seconds=60))
            ),
            (
                (RiskResearchRequestRecord.status == "claiming")
                & RiskResearchRequestRecord.attempts.in_((1, 2, 3))
                & RiskResearchRequestRecord.lease_owner.is_not(None)
                & (RiskResearchRequestRecord.lease_expires_at > now)
                & (RiskResearchRequestRecord.available_at <= RiskResearchRequestRecord.updated_at)
            ),
        ),
        RiskResearchRequestRecord.run_id.is_(None),
        RiskResearchRequestRecord.event_id.is_(None),
        RiskResearchRequestRecord.instrument.in_(("BTC-USDT", "ETH-USDT")),
        RiskResearchRequestRecord.lease_generation >= RiskResearchRequestRecord.attempts,
        RiskResearchRequestRecord.created_at <= RiskResearchRequestRecord.updated_at,
        RiskResearchRequestRecord.updated_at <= now,
    ).execution_options(populate_existing=True)).yield_per(64)
    needed: set[str] = set()
    for request in requests:
        try:
            waits = recognized_condition_waits(session, request, now=now)
            updated = _time(request.updated_at)
            if request.status == "pending_admission":
                latest = waits[-1] if waits else None
                if latest is not None and latest["lease_generation"] == request.lease_generation:
                    if (request.error_code != latest["reason_code"]
                            or request.attempts != latest["attempts_after"]
                            or updated != datetime.fromisoformat(latest["recorded_at"])
                            or _time(request.available_at)
                            != datetime.fromisoformat(latest["available_at"])):
                        continue
                elif (request.attempts not in (1, 2)
                        or _time(request.available_at) != updated + timedelta(
                            seconds=60 * 2 ** (request.attempts - 1))):
                    # Preserve existing non-credit 60/120-second histories.
                    continue
            elif (
                # Collection demand is not lease ownership or Host authority.
                # The realtime owner and general research collector are separate
                # processes. Only the admission service's exact _owns check may
                # consume a lease; this recognizes its persisted acquisition shape.
                re.fullmatch(r"risk-research:[0-9a-f]{32}", request.lease_owner or "") is None
                or _time(request.lease_expires_at) != updated + timedelta(seconds=60)
                or not (
                    _time(request.available_at) <= updated <= now < _time(request.lease_expires_at)
                )
            ):
                continue
            _recognized_request_triggers(session, request, now=now)
            scope = json.dumps([
                "crypto", request.instrument, "30m,24h,72h", "spot-market-risk.v1",
            ], ensure_ascii=False)
            study = session.get(StudyRecord, stable_id("study", scope), populate_existing=True)
            if study is not None and (
                study.scope_key == _sha(scope) and study.status == "active"
                and study.market == "crypto" and study.instrument == request.instrument
                and study.horizon_scope == "30m,24h,72h"
                and study.question_key == "spot-market-risk.v1"
                and study.identity_basis == "exact_spot_monitor"
            ):
                needed.add(study.study_id)
        except (ValueError, TypeError, KeyError):
            # Corrupt or unauthenticated intent cannot create collection demand.
            continue
    return needed


def _active_spot_monitor_studies_in_session(session: Session, now: datetime) -> set[str]:
    # Collection demand only. The original Host/API and admission guards still
    # independently authenticate activity, native binding, scope and freshness.
    from ..persistence.risk_records import RiskMonitorPlanRecord
    from ..ports.market_path import spot_scope
    from ..ports.risk_monitor import RiskConditionV1
    from .study import stable_id

    instruments = ("BTC-USDT", "ETH-USDT")
    scopes = {spot_scope(instrument).scope_id: instrument for instrument in instruments}
    plans = session.scalars(select(RiskMonitorPlanRecord).where(
        RiskMonitorPlanRecord.scope_id.in_(scopes),
        RiskMonitorPlanRecord.state.in_(("active", "degraded")),
        or_(RiskMonitorPlanRecord.expires_at.is_(None), RiskMonitorPlanRecord.expires_at > now),
    ).execution_options(populate_existing=True)).yield_per(64)
    needed: set[str] = set()
    for number, plan in enumerate(plans, start=1):
        if number > 64:
            raise ValueError("dsh_activity_monitor_demand_bound")
        try:
            instrument = scopes.get(plan.scope_id)
            if instrument is None or len(plan.rule_json.encode("utf-8")) > 16384:
                continue
            rule = RiskConditionV1.model_validate_json(plan.rule_json)
            encoded = _encoded(rule.model_dump(mode="json"))
            if (
                plan.state not in {"active", "degraded"}
                or rule.scope_id != plan.scope_id or rule.condition_id != plan.rule_version
                or plan.rule_json != encoded or plan.rule_hash != _sha(encoded)
                or plan.plan_id != "rplan_" + _sha(_encoded([rule.scope_id, rule.condition_id]))[:48]
                or not _time(plan.created_at) <= _time(plan.updated_at) <= now
                or (plan.expires_at is not None and _time(plan.expires_at) <= now)
                or not now - timedelta(seconds=180) <= _time(plan.last_evaluated_at) <= now
                or not now - timedelta(seconds=180) <= _time(plan.last_data_as_of) <= now
            ):
                continue
            scope = json.dumps([
                "crypto", instrument, "30m,24h,72h", "spot-market-risk.v1",
            ], ensure_ascii=False)
            study = session.get(StudyRecord, stable_id("study", scope), populate_existing=True)
            if study is not None and (
                study.scope_key == _sha(scope) and study.status == "active"
                and study.market == "crypto" and study.instrument == instrument
                and study.horizon_scope == "30m,24h,72h"
                and study.question_key == "spot-market-risk.v1"
                and study.identity_basis == "exact_spot_monitor"
            ):
                needed.add(study.study_id)
        except (ValueError, TypeError, KeyError, AttributeError):
            # Unsupported or malformed monitoring metadata cannot request proof.
            continue
    return needed


def activity_targets_in_session(
    session: Session, *, now: datetime, limit: int = 64,
    study_ids: Sequence[str] | None = None,
) -> DshHostActivityRequest | None:
    """Observe due/blocked studies and authenticated active spot-monitor demand."""
    current = _time(now)
    needed: set[str] = set(study_ids or ())
    review_studies = session.scalars(select(RunRecord.study_id).join(
        ReviewScheduleRecord, ReviewScheduleRecord.parent_run_id == RunRecord.run_id,
    ).where(
        ReviewScheduleRecord.status == "planned", ReviewScheduleRecord.child_run_id.is_(None),
        ReviewScheduleRecord.trigger_at <= current, RunRecord.study_id.is_not(None),
    ))
    source_studies = session.scalars(select(AdmissionDecisionRecord.study_id).where(
        AdmissionDecisionRecord.strategy_version == "research.v1",
        AdmissionDecisionRecord.decision == "blocked",
        AdmissionDecisionRecord.reason_code == "study_host_terminal_unknown",
        AdmissionDecisionRecord.decided_at >= current - timedelta(seconds=60),
        AdmissionDecisionRecord.decided_at <= current,
        AdmissionDecisionRecord.study_id.is_not(None),
    ))
    for study in (*review_studies, *source_studies):
        if study is not None:
            needed.add(study)
    needed.update(_blocked_risk_research_studies(session, current))
    needed.update(_active_spot_monitor_studies_in_session(session, current))
    if not needed:
        return None
    candidates = session.execute(select(RunRecord, DshSessionLinkRecord).join(
        DshSessionLinkRecord, DshSessionLinkRecord.run_id == RunRecord.run_id,
    ).join(StudyRecord, StudyRecord.study_id == RunRecord.study_id).where(
        RunRecord.study_id.in_(needed), StudyRecord.status == "active",
        RunRecord.strategy_version == "research.v1", RunRecord.status.in_(TERMINAL_RUN_STATES),
        or_(
            DshSessionLinkRecord.state.not_in(("completed", "failed", "cancelled")),
            DshSessionLinkRecord.terminal_at.is_(None),
        ),
    ).order_by(RunRecord.updated_at.desc(), RunRecord.run_id)).yield_per(64)
    bindings: list[DshHostActivityBinding] = []
    refresh: list[DshHostActivityBinding] = []
    bound = max(1, min(limit, 64))
    for run, link in candidates:
        if _terminal_unleased(run, current):
            try:
                binding = activity_binding(run, link)
            except ValueError:
                continue
            if host_activity_inactive_in_session(session, run=run, link=link, now=current):
                if len(refresh) < bound:
                    refresh.append(binding)
            else:
                bindings.append(binding)
            if len(bindings) >= bound:
                break
    bindings = bindings or refresh
    return DshHostActivityRequest(records=bindings) if bindings else None
