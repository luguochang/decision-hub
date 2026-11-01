"""Durable publication jobs and serving head; uninstalled production candidate.

Publication enqueue shares the original publication transaction. Preparation
and file validation never happen in a writer. Internal worker ownership is
separate from Run leases and from the existing product read policy.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import nullcontext
import logging
import os
import time
import json
import re
from typing import Callable
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from ..persistence.db import RunRecord, RunRoleOutputRecord, as_utc
from ..persistence.research_publication_bundle_records import (
    PublicationBundleJobRecord as Job, PublicationBundleStateEventRecord as Event,
    PublicationBundleRecord as Saved, PublicationBundleConfirmationRecord as Ack,
)
from .research_publication_bundle_lifecycle import (
    PublicationBundleLifecycle, BundleClaim, BundleCommitUnconfirmed, BundleWriterBeginBusy, encoded, sha,
)
from .research_publication_execution import PublicationExecutionStopped, execution_stage
from ..ports.research_saved_confirmation import SAVED_CONFIRMATION_TERMINAL_REASONS

PUBLICATION_SOURCE_ROLE = "research_publication_source"
READER_VERSION = "frozen-publication-reader.v1"
DIAGNOSTIC_GAP_RECOVERY_MODE = "diagnostic_gap_all_owned_archives.v1"
PREPARATION_QUEUE_RECOVERY_MODE83 = "scheduled_preparation_queue_only_snapshot_ownership.v1"
CURRENT_READER_ABI = "publication-source-snapshot-read.v2"
CANCEL_REASONS = frozenset({"publication_bundle_preparation_cancelled",
    "publication_execution_compute_budget_exceeded", "publication_execution_total_budget_exceeded",
    "publication_execution_stop_requested"})
NATIVE_FIELDS = ("output_id", "run_id", "role", "schema_version", "payload_json", "payload_hash", "created_at")
EVENT_PHASES49 = frozenset({"claimed", "prepared", "confirmed", "enqueued", "cancelled",
    "unconfirmed", "revoked", "retry_queued", "lease_renewed",
    "confirmation_claimed", "confirmation_lease_renewed"})


class _ReportRecoveryObservations74:
    """Call-local metadata only; observer failure never replaces business work."""

    _PHASES = frozenset({"entry", "request", "head", "owner", "abi", "history",
        "ownership", "authority", "writer", "readback", "full_fresh_exit"})
    _CODES = frozenset({
        "publication_retry_explicit_expected_identity_required",
        "publication_recovery_current_reader_required",
        "publication_bundle_fence_requires_readonly_reconciliation",
        "publication_recovery_exact_known_cancel_required",
        "publication_recovery_normal_owner_required", "publication_recovery_owner_scope_changed",
        "publication_retry_generation_CAS_lost", "publication_retry_independent_readback_changed",
        "publication_retry_saved_or_confirmed_requires_review", "publication_retry_event_history_changed",
        "publication_retry_archive_ownership_changed", "publication_recovery_receipt_missing",
        "publication_retry_archive_ownership_bound_exceeded", "publication_retry_archive_ownership_unknown",
        "report_snapshot_not_independently_confirmed", "report_snapshot_confirmation_changed",
        "report_snapshot_saved_index_changed", "report_snapshot_publication_identity_changed",
        "publication_recovery_receipt_invalid", "publication_recovery_request_reused",
        "publication_retry_normal_source_changed", "publication_source_snapshot_boundary_changed",
        "publication_source_snapshot_implementation_changed", "research_history_version_unavailable",
        "research_history_v3_manifest_changed", "research_history_v3_loaded_source_changed",
    })

    def __init__(self, run_id, expected_request, expected_generation, expected_version,
                 *, scope, request_id=None):
        self._state = None
        try:
            if scope not in {"route", "retry_known_cancelled"}:
                return
            self._state = {"schema_version": "report-recovery-observation.v1", "scope": scope,
                "run_sha256": sha(run_id.encode()),
                "expected_request_sha256": sha(expected_request.encode()),
                "request_sha256": sha(request_id.encode()) if type(request_id) is str else None,
                "expected_generation": expected_generation if type(expected_generation) is int else None,
                "expected_state_version": expected_version if type(expected_version) is int else None}
            self._started = self._phase_started = time.perf_counter()
            self._phase = "request" if scope == "route" else "entry"
            self._times, self._seen = {}, set()
            self._emit("started")
        except BaseException:
            self._state = None

    def _elapsed(self, started):
        duration = (time.perf_counter() - started) * 1000
        if not -float("inf") < duration < float("inf"):
            raise ValueError("recovery_observation_clock_unavailable")
        return round(max(0.0, duration), 3)

    def _emit(self, event, *, error=None, transport_error=False):
        try:
            if self._state is None:
                return
            error_class = _publication_failure_class(error) if error is not None else None
            if type(error) is BundleWriterBeginBusy:
                error_class, code = "BundleWriterBeginBusy", "bundle_writer_begin_busy"
            elif type(error) is BundleCommitUnconfirmed:
                error_class, code = "BundleCommitUnconfirmed", "bundle_commit_unconfirmed"
            else:
                code = (error.args[0] if type(error) in {ValueError, PermissionError}
                    and len(error.args) == 1 and type(error.args[0]) is str
                    and error.args[0] in self._CODES else "unknown") if error is not None else None
            value = {**self._state, "event": event, "phase": self._phase,
                "observed_at_utc": datetime.now(timezone.utc).isoformat(), "process_id": os.getpid(),
                "elapsed_ms": self._elapsed(self._started),
                "phase_elapsed_ms": self._elapsed(self._phase_started),
                "exception_class": error_class, "safe_code": code,
                "transport_exception_class": "HTTPException" if transport_error else None}
            if event == "terminal":
                value.update(outcome="error" if error is not None or transport_error else "returned",
                    stage_elapsed_ms={**self._times, self._phase: value["phase_elapsed_ms"]})
            logging.getLogger(__name__).warning("report_recovery_metadata %s",
                json.dumps(value, sort_keys=True, separators=(",", ":")))
        except BaseException:
            pass

    def stage(self, phase):
        try:
            if self._state is None or phase not in self._PHASES or phase in self._seen:
                return
            self._times[self._phase] = self._elapsed(self._phase_started)
            self._phase, self._phase_started = phase, time.perf_counter()
            self._seen.add(phase)
            self._emit("stage_entry")
        except BaseException:
            pass

    def terminal(self, error=None, *, transport_error=False):
        self._emit("terminal", error=error, transport_error=transport_error)


def _failure_metadata49(value: dict) -> None:
    if (type(value) is not dict or set(value) != {"status", "sha256"}
            or type(value["status"]) is not str or value["status"] not in {"saved", "unavailable"}
            or (value["status"] == "saved" and (type(value["sha256"]) is not str
                or not re.fullmatch(r"[a-f0-9]{64}", value["sha256"])))
            or value["status"] == "unavailable" and value["sha256"] is not None):
        raise ValueError("publication_retry_diagnostic_metadata_invalid")


def exact_selection(session: Session, run_id: str) -> str:
    rows = session.connection().exec_driver_sql(
        "SELECT output_id,run_id,role,schema_version,payload_json,payload_hash,created_at "
        "FROM run_role_outputs WHERE run_id=? AND role=?", (run_id, "graph_recipe_selection")
    ).fetchall()
    if len(rows) != 1 or len(rows[0]) != 7 or any(type(v) is not str for v in rows[0]):
        raise ValueError("publication_job_selection_native_identity_invalid")
    value = dict(zip(NATIVE_FIELDS, rows[0]))
    body = json.loads(value["payload_json"])
    if (not value["output_id"] or value["role"] != "graph_recipe_selection"
            or value["run_id"] != run_id or value["schema_version"] not in {
                "research-graph-recipe-selection.v2","research-graph-recipe-selection.v3"}
            or type(body) is not dict or body.get("schema_version") != value["schema_version"]
            or encoded(body).decode() != value["payload_json"]
            or sha(value["payload_json"].encode()) != value["payload_hash"]):
        raise ValueError("publication_job_selection_native_identity_invalid")
    return encoded(value).decode()


def append_event(session: Session, job: Job, *, occurred_at: datetime, transition_phase: str | None = None,
                 failure_diagnostic: dict | None = None) -> None:
    payload = {"schema_version":"publication-bundle-state-event.v1", "run_id":job.run_id,
        "request_id":job.request_id, "version":job.state_version, "generation":job.generation,
        "state":job.state, "reason_code":job.reason_code, "bundle_hash":job.bundle_hash,
        "occurred_at":occurred_at.isoformat()}
    if transition_phase is not None:
        if type(transition_phase) is not str or transition_phase not in EVENT_PHASES49:
            raise ValueError("publication_job_transition_phase_unknown")
        payload.update(schema_version="publication-bundle-state-event.v2",transition_phase=transition_phase)
        if failure_diagnostic is not None:
            if transition_phase != "unconfirmed":
                raise ValueError("publication_retry_diagnostic_metadata_invalid")
            _failure_metadata49(failure_diagnostic)
            payload["failure_diagnostic"]=failure_diagnostic
    raw = encoded(payload)
    session.add(Event(event_id=sha(raw),run_id=job.run_id,request_id=job.request_id,
        version=job.state_version,generation=job.generation,state=job.state,
        reason_code=job.reason_code,bundle_hash=job.bundle_hash,occurred_at=occurred_at,payload_json=raw.decode()))


def enqueue_in_publication_transaction(session: Session, run_id: str, *, now: datetime) -> str | None:
    """Normal registered publisher only. No COMMIT, file IO, model or token."""
    if session.connection().dialect.name != "sqlite":
        raise ValueError("publication_job_physical_sqlite_required")
    session.flush()  # Original publication fields must be visible natively.
    run = session.get(RunRecord,run_id,populate_existing=True)
    rows = list(session.scalars(select(RunRoleOutputRecord).where(
        RunRoleOutputRecord.run_id == run_id, RunRoleOutputRecord.role == PUBLICATION_SOURCE_ROLE)))
    if (run is None or run.status not in {"completed","degraded"} or not run.artifact_id
            or run.finished_at is None or len(rows)!=1):
        raise ValueError("publication_job_terminal_publication_required")
    source = rows[0]
    if sha(source.payload_json.encode()) != source.payload_hash:
        raise ValueError("publication_job_publication_original_changed")
    # This consumer's registered quality reader accepts only publication V4.
    # V5 has its own signed report reader; no legacy bundle task is its authority.
    from .commit import PUBLICATION_SOURCE_SCHEMA_V4
    if source.schema_version != PUBLICATION_SOURCE_SCHEMA_V4:
        return None
    selection = exact_selection(session,run_id)
    request = "freeze:" + sha(encoded([run_id,source.output_id,source.payload_hash,sha(selection.encode()),READER_VERSION]))
    existing = session.get(Job,run_id,populate_existing=True)
    if existing is not None:
        if (existing.request_id!=request or existing.publication_hash!=source.payload_hash
                or existing.selection_native_json!=selection or existing.reader_version!=READER_VERSION):
            raise ValueError("publication_job_existing_identity_changed")
        return request
    job=Job(run_id=run_id,request_id=request,event_id=run.event_id,artifact_id=run.artifact_id,
        publication_id=source.output_id,publication_hash=source.payload_hash,
        publication_schema=source.schema_version,publication_created_at=source.created_at,
        generation=0,owner=None,lease_expires_at=None,state="queued",updated_at=now,
        state_version=1,selection_native_json=selection,selection_native_hash=sha(selection.encode()),
        reader_version=READER_VERSION,available_at=now,attempts=0,reason_code=None,created_at=now)
    session.add(job)
    append_event(session,job,occurred_at=now)
    return request


class DurablePublicationLifecycle(PublicationBundleLifecycle):
    def audit_original(self, run_id: str, *, as_of: datetime):
        """Existing normal full reader, one file-mode RO/query-only snapshot.

        Caller independently enforces serving head before/after. This method
        writes no audit job, confirmation, material or publication state.
        """
        from .research_quality_view import read_published_research_quality_in_session
        with self._ro() as session:
            native=session.connection().connection.driver_connection
            before=native.total_changes
            result=read_published_research_quality_in_session(session,run_id,as_of=as_of)
            if native.total_changes!=before:
                raise ValueError("publication_full_audit_native_changes_forbidden")
            return result

    def _publication(self, session, job, bundle=None):
        super()._publication(session,job,bundle)
        if (job.reader_version != READER_VERSION or not job.selection_native_json
                or sha(job.selection_native_json.encode()) != job.selection_native_hash
                or exact_selection(session,job.run_id) != job.selection_native_json):
            raise ValueError("publication_job_selected_coverage_changed")

    def _attempt(self, claim, phase, bundle_hash, body, write):
        def audited_write(session):
            write(session)
            session.flush()
            job=session.get(Job,claim.run_id,populate_existing=True)
            if job is None:
                raise ValueError("publication_job_attempt_head_missing")
            job.state_version += 1
            append_event(session,job,occurred_at=self.clock(),transition_phase=phase,
                failure_diagnostic=body.get("failure_diagnostic") if phase=="unconfirmed" else None)
        return super()._attempt(claim,phase,bundle_hash,body,audited_write)

    def claim_one(self, *, owner: str, lease_seconds: int = 180) -> BundleClaim | None:
        if (not re.fullmatch(r"publication-bundle:[0-9]+:[a-f0-9]{32}",owner)
                or type(lease_seconds) is not int or not 90<=lease_seconds<=180):
            raise ValueError("publication_job_internal_worker_identity_required")
        now=self.clock()
        # Claim candidates in independent RO; no archive IO in the writer.
        with self._ro() as session:
            chosen=None
            cursor=getattr(self,"_claim_scan_cursor",None)
            # This cursor is scheduling state, never permission. Every chosen
            # head still undergoes fresh publication validation and writer CAS.
            # Bounded pages advance across polls instead of starving behind
            # a first page containing only unknown-COMMIT fences.
            for _ in range(16):
                query=select(Job).where(Job.state=="queued",Job.available_at<=now,
                    or_(Job.owner.is_(None),Job.lease_expires_at<=now))
                if cursor is not None:
                    query=query.where(or_(Job.created_at>cursor[0],
                        and_(Job.created_at==cursor[0],Job.run_id>cursor[1])))
                candidates=list(session.scalars(query.order_by(Job.created_at,Job.run_id).limit(8)))
                if not candidates:
                    self._claim_scan_cursor=None
                    if cursor is not None:
                        cursor=None
                        continue
                    break
                cursor=(candidates[-1].created_at,candidates[-1].run_id)
                self._claim_scan_cursor=cursor
                chosen=next((j for j in candidates if not self.archive.fenced(j.run_id)),None)
                if chosen is not None:
                    break
            if chosen is None:
                return None
            target=(chosen.run_id,chosen.request_id,chosen.generation,chosen.state_version)
        claim=BundleClaim(target[0],target[1],owner,target[2]+1)
        def write(session):
            job=session.get(Job,target[0],populate_existing=True)
            if (job is None or job.request_id!=target[1] or job.generation!=target[2]
                    or job.state_version!=target[3] or job.state!="queued" or as_utc(job.available_at)>now
                    or job.owner is not None and (as_utc(job.lease_expires_at) is None
                        or as_utc(job.lease_expires_at)>now)):
                raise PermissionError("publication_job_claim_CAS_lost")
            self._publication(session,job)
            changed=session.execute(update(Job).where(Job.run_id==job.run_id,
                Job.generation==target[2],Job.state=="queued",Job.state_version==job.state_version,
                or_(Job.owner.is_(None),Job.lease_expires_at<=now)).execution_options(synchronize_session=False).values(
                    owner=owner,generation=target[2]+1,lease_expires_at=now+timedelta(seconds=lease_seconds),
                    updated_at=now,attempts=job.attempts+1))
            if changed.rowcount!=1:
                raise PermissionError("publication_job_claim_CAS_lost")
            session.expire(job); session.refresh(job)
        try:
            fence=self._attempt(claim,"claimed","0"*64,{"previous_generation":target[2]},write)
        except PermissionError:
            return None
        except ValueError as error:
            if str(error)=="publication_bundle_fence_requires_readonly_reconciliation":
                return None
            raise
        with self._ro() as session:
            self._owned(session,claim,"queued")
        self.archive.release(claim.run_id,fence)
        return claim

    def _renew_claim_head(self, session, claim):
        job=self._owned(session,claim,"queued")
        if job.bundle_hash is not None or job.confirmed_at is not None:
            raise PermissionError("publication_renew_preparation_only_required")
        for table in ("research_publication_bundles","research_publication_bundle_confirmations"):
            if session.connection().exec_driver_sql("SELECT * FROM "+table+" WHERE run_id=?",(claim.run_id,)).fetchall():
                raise PermissionError("publication_renew_saved_or_confirmed_requires_review")
        return job

    def renew_claim(self, claim: BundleClaim, *, lease_seconds: int = 180) -> dict:
        """Finite prepare-only renewal; never resurrect an expired claim.

        The coordinator owns the total execution budget. This method preserves
        the original 180-second rolling bound and original unknown-COMMIT fence.
        Saved/prepared work does not receive a longer lease here.
        """
        if (type(lease_seconds) is not int or not 90<=lease_seconds<=180):
            raise ValueError("publication_renew_lease_bound_required")
        if self.archive.fenced(claim.run_id):
            raise ValueError("publication_bundle_fence_requires_readonly_reconciliation")
        source_fields=("publication_id","publication_hash","publication_schema","publication_created_at",
            "selection_native_json","selection_native_hash","reader_version","event_id","artifact_id")
        with self._ro() as reader:
            job=self._renew_claim_head(reader,claim)
            version,old_lease=job.state_version,job.lease_expires_at
            source={name:getattr(job,name) for name in source_fields}
        until=None
        def write(session):
            nonlocal until
            job=self._renew_claim_head(session,claim)
            now=self.clock()
            if (job.state_version!=version or job.lease_expires_at!=old_lease
                    or any(getattr(job,name)!=value for name,value in source.items())
                    or as_utc(old_lease)<=now):
                raise PermissionError("publication_renew_exact_head_CAS_lost")
            until=now+timedelta(seconds=lease_seconds)
            if until<=as_utc(old_lease):
                raise PermissionError("publication_renew_clock_did_not_advance")
            guards=[Job.run_id==claim.run_id,Job.request_id==claim.request_id,Job.owner==claim.owner,
                Job.generation==claim.generation,Job.state=="queued",Job.state_version==version,
                Job.lease_expires_at==old_lease,Job.lease_expires_at>now,
                Job.bundle_hash.is_(None),Job.confirmed_at.is_(None)]
            guards.extend(getattr(Job,name)==value for name,value in source.items())
            changed=session.execute(update(Job).where(*guards).execution_options(synchronize_session=False)
                .values(lease_expires_at=until,updated_at=now))
            if changed.rowcount!=1:
                raise PermissionError("publication_renew_exact_head_CAS_lost")
            session.expire(job);session.refresh(job)
        fence=self._attempt(claim,"lease_renewed","0"*64,{},write)
        with self._ro() as reader:
            job=self._renew_claim_head(reader,claim)
            if (job.state_version!=version+1 or as_utc(job.lease_expires_at)!=until
                    or any(getattr(job,name)!=value for name,value in source.items())):
                raise ValueError("publication_renew_independent_readback_changed")
        self.archive.release(claim.run_id,fence)
        return {"run_id":claim.run_id,"request_id":claim.request_id,"generation":claim.generation,
            "state_version":version+1,"state":"queued","lease_expires_at":until.isoformat()}

    def enqueue(self, *args, **kwargs):
        raise ValueError("publication_bundle_use_atomic_publication_or_explicit_backfill")

    def _confirmation_rows74(self, session, record, statement, parameters, *, count=1):
        """Full native shape and cells; an exact absent ACK remains absent."""
        result=session.connection().exec_driver_sql(statement,parameters)
        columns,rows=list(result.keys()),result.fetchall()
        declared={column.name:column for column in record.__table__.columns}
        if (len(columns)!=len(declared) or len(set(columns))!=len(columns)
                or set(columns)!=set(declared) or len(rows)!=count):
            raise ValueError("publication_confirmation_native_shape_changed")
        cells=[]
        for values in rows:
            if len(values)!=len(columns):raise ValueError("publication_confirmation_native_shape_changed")
            row=[]
            for name,value in zip(columns,values):
                column=declared[name];kind=column.type.python_type
                kind=str if kind is datetime else kind
                if (value is None and not column.nullable
                        or value is not None and type(value) is not kind):
                    raise ValueError("publication_confirmation_native_shape_changed")
                row.append(("null",None) if value is None else ("real",value.hex()) if type(value) is float
                    else ("blob",value.hex()) if type(value) is bytes else
                    ("integer",value) if type(value) is int else ("text",value))
            cells.append(row)
        return {"table":record.__tablename__,"columns":columns,"rows":cells}

    def _prepared_confirmation_lineage74(self,session,run_id):
        """Authenticate normal plan ancestry and its original owner creation."""
        from ..persistence.db import ProductCommandRecord,ReviewScheduleRecord,ResearchResultRecord,RunEventRecord
        from .research_report_recovery_commands import ReportRecoveryCommandService
        proof=[];seen=set();current=session.get(RunRecord,run_id,populate_existing=True)
        for _ in range(64):
            if current is None or current.run_id in seen or current.strategy_version!="research.v1":
                raise ValueError("publication_confirmation_lineage_changed")
            seen.add(current.run_id)
            proof.append(self._confirmation_rows74(session,RunRecord,"SELECT * FROM runs WHERE run_id=?",(current.run_id,)))
            if current.admission_origin=="manual":
                key=current.idempotency_key
                if current.parent_run_id is not None:
                    if type(key) is not str or not key.startswith("research-command:"):
                        raise ValueError("publication_confirmation_lineage_changed")
                    key=key[len("research-command:"):]
                original=session.get(ProductCommandRecord,key,populate_existing=True)
                if original is None:
                    # Existing risk admission creates an internal Run and saved
                    # owner-command trigger, not a user submission receipt.
                    # This proves computation lineage only; serving ACL stays
                    # with the normal API and the normal issuer's fresh guards.
                    if current.parent_run_id is not None or not key.startswith("risk-research:"):
                        raise ValueError("publication_confirmation_original_creation_missing")
                    from ..persistence.risk_research_records import (
                        RiskResearchRequestRecord,RiskResearchTriggerRecord,RiskResearchPlanRecord)
                    from .risk_research_admission import _recognized_request_triggers,_trigger_admission_origin
                    risk_key=key[len("risk-research:"):]
                    risk=session.get(RiskResearchRequestRecord,risk_key,populate_existing=True)
                    if (risk is None or risk.status!="queued" or risk.run_id!=current.run_id
                            or risk.event_id!=current.event_id or risk.error_code is not None
                            or risk.lease_owner is not None or risk.lease_expires_at is not None):
                        raise ValueError("publication_confirmation_internal_creation_changed")
                    trigger_count=session.connection().exec_driver_sql(
                        "SELECT count(*) FROM risk_research_triggers WHERE request_id=?",(risk_key,)).scalar_one()
                    if type(trigger_count) is not int or not 1<=trigger_count<=64:
                        raise ValueError("publication_confirmation_internal_creation_bound_exceeded")
                    triggers=_recognized_request_triggers(session,risk,now=self.clock())
                    if (len(triggers)!=trigger_count
                            or _trigger_admission_origin(risk,triggers,now=self.clock())!=current.admission_origin):
                        raise ValueError("publication_confirmation_internal_creation_changed")
                    proof.append(self._confirmation_rows74(session,RiskResearchRequestRecord,
                        "SELECT * FROM risk_research_requests WHERE request_id=?",(risk_key,)))
                    proof.append(self._confirmation_rows74(session,RiskResearchTriggerRecord,
                        "SELECT * FROM risk_research_triggers WHERE request_id=? ORDER BY recorded_at,trigger_key",
                        (risk_key,),count=trigger_count))
                    for trigger in triggers:
                        if trigger.kind=="daily":
                            proof.append(self._confirmation_rows74(session,RiskResearchPlanRecord,
                                "SELECT * FROM risk_research_plans WHERE plan_id=?",(trigger.source_ref,)))
                    proof.append(self._confirmation_rows74(session,ProductCommandRecord,
                        "SELECT * FROM product_commands WHERE request_id=? OR (target_id=? AND kind!=?)",
                        (key,current.run_id,"saved_confirmation"),count=0))
                    proof.append({"normal_internal_creation":"risk-research",
                        "user_creation_receipt":"absent","serving_authority_granted":False})
                    break
                identity=ReportRecoveryCommandService._owner_identity(session,current.run_id,original.owner_id)
                proof.append(self._confirmation_rows74(session,ProductCommandRecord,
                    "SELECT * FROM product_commands WHERE request_id=?",(original.request_id,)))
                proof.append({"normal_creation_identity":identity})
                break
            if (current.admission_origin!="scheduled_recheck" or not current.parent_run_id
                    or type(current.idempotency_key) is not str
                    or not current.idempotency_key.startswith("research-review-plan:")):
                raise ValueError("publication_confirmation_lineage_changed")
            plan_id=current.idempotency_key[len("research-review-plan:"):]
            plan=session.get(ReviewScheduleRecord,plan_id,populate_existing=True)
            parent=session.get(RunRecord,current.parent_run_id,populate_existing=True)
            if (plan is None or parent is None or plan.child_run_id!=current.run_id
                    or plan.parent_run_id!=parent.run_id or plan.status not in {"admitted","completed"}
                    or plan.failure_code is not None or parent.event_id!=current.event_id
                    or parent.study_id!=current.study_id or parent.status not in {"completed","degraded"}
                    or parent.artifact_id is None or parent.finished_at is None):
                raise ValueError("publication_confirmation_lineage_changed")
            sources=list(session.scalars(select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.run_id==parent.run_id,RunRoleOutputRecord.role==PUBLICATION_SOURCE_ROLE)
                .execution_options(populate_existing=True)))
            result=session.get(ResearchResultRecord,parent.run_id,populate_existing=True)
            if len(sources)!=1 or result is None:raise ValueError("publication_confirmation_parent_publication_missing")
            source=sources[0];body=json.loads(source.payload_json)
            if (sha(source.payload_json.encode())!=source.payload_hash or type(body) is not dict
                    or body.get("run_id")!=parent.run_id or body.get("event_id")!=parent.event_id
                    or body.get("artifact_id")!=parent.artifact_id or type(body.get("quality_binding")) is not dict
                    or body.get("result_payload_hash")!=result.payload_hash
                    or body.get("result_schema_version")!=result.schema_version
                    or as_utc(result.created_at) is None
                    or body.get("result_created_at")!=as_utc(result.created_at).isoformat()
                    or sha(result.payload_json.encode())!=result.payload_hash):
                raise ValueError("publication_confirmation_parent_publication_changed")
            expected={"review_plan_id":plan_id,"child_run_id":current.run_id,"horizon":plan.horizon,
                "trigger_type":plan.trigger_type,"trigger_at":as_utc(plan.trigger_at).isoformat(),
                "idempotency_key":current.idempotency_key}
            events=list(session.scalars(select(RunEventRecord).where(RunEventRecord.run_id==parent.run_id,
                RunEventRecord.event_type=="research.scheduled_recheck.admitted").limit(65)
                .execution_options(populate_existing=True)))
            matches=[event for event in events if json.loads(event.payload_json)==expected]
            if len(events)>64 or len(matches)!=1:raise ValueError("publication_confirmation_plan_admission_changed")
            for record,statement,parameters in (
                (ReviewScheduleRecord,"SELECT * FROM review_schedules WHERE review_plan_id=?",(plan_id,)),
                (RunRoleOutputRecord,"SELECT * FROM run_role_outputs WHERE run_id=? AND role=?",(parent.run_id,PUBLICATION_SOURCE_ROLE)),
                (ResearchResultRecord,"SELECT * FROM research_results WHERE run_id=?",(parent.run_id,)),
                (RunEventRecord,"SELECT * FROM run_events WHERE id=?",(matches[0].id,)),
            ):proof.append(self._confirmation_rows74(session,record,statement,parameters))
            current=parent
        else:raise ValueError("publication_confirmation_lineage_bound_exceeded")
        if len(encoded(proof))>32*1024*1024:raise ValueError("publication_confirmation_lineage_bound_exceeded")
        return proof

    def saved_confirmation_owner_proof(self,session,run_id,owner_id):
        """Original creation ownership only; no report or source-read grant."""
        if type(owner_id) is not str or not owner_id or owner_id!=owner_id.strip() or len(owner_id)>128:
            raise PermissionError("publication_saved_confirmation_original_owner_required")
        lineage=self._prepared_confirmation_lineage74(session,run_id)
        current=session.get(RunRecord,run_id,populate_existing=True)
        for _ in range(64):
            if current is None:raise PermissionError("publication_saved_confirmation_original_owner_required")
            if current.admission_origin=="manual":break
            current=session.get(RunRecord,current.parent_run_id,populate_existing=True)
        else:raise PermissionError("publication_saved_confirmation_original_owner_required")
        if current.idempotency_key.startswith("risk-research:") and current.parent_run_id is None:
            from ..persistence.risk_research_records import RiskResearchRequestRecord,RiskResearchTriggerRecord
            from .risk_research_admission import _recognized_request_triggers,_trigger_admission_origin
            key=current.idempotency_key[len("risk-research:"):]
            risk=session.get(RiskResearchRequestRecord,key,populate_existing=True)
            if risk is None:raise PermissionError("publication_saved_confirmation_original_owner_required")
            triggers=_recognized_request_triggers(session,risk,now=self.clock())
            owners=[]
            for trigger in triggers:
                if trigger.kind!="manual":continue
                source=json.loads(trigger.payload_json).get("source")
                if type(source) is not dict or set(source)!={"owner_id"} or source["owner_id"]!=owner_id:
                    raise PermissionError("publication_saved_confirmation_original_owner_required")
                owners.append(trigger)
            if len(owners)!=1 or _trigger_admission_origin(risk,triggers,now=self.clock())!="manual":
                raise PermissionError("publication_saved_confirmation_original_owner_required")
            owner_source=self._confirmation_rows74(session,RiskResearchTriggerRecord,
                "SELECT * FROM risk_research_triggers WHERE request_id=? AND trigger_key=?",(key,owners[0].trigger_key))
        else:
            from .research_report_recovery_commands import ReportRecoveryCommandService
            owner_source=ReportRecoveryCommandService._owner_identity(session,current.run_id,owner_id)
        return {"owner_id":owner_id,"root_run_id":current.run_id,"lineage":lineage,"owner_source":owner_source}

    def _saved_confirmation_history76(self,session,job):
        history=self._retry_gap_history_native49(session,job.run_id,job.state_version)
        bodies=[json.loads(row[history["columns"].index("payload_json")]) for row in history["rows"]]
        base={"schema_version","run_id","request_id","version","generation","state","reason_code","bundle_hash","occurred_at"}
        for body in bodies:
            schema=body.get("schema_version")
            fields=base if schema=="publication-bundle-state-event.v1" else base|{"transition_phase"}
            if schema=="publication-bundle-state-event.v2" and "failure_diagnostic" in body:
                if body.get("transition_phase")!="unconfirmed":raise ValueError("publication_confirmation_history_changed")
                fields=fields|{"failure_diagnostic"};_failure_metadata49(body["failure_diagnostic"])
            if schema=="publication-bundle-state-event.v3":
                fields=base|{"transition_phase","authorization_request_id","claim_owner"}
                if (body.get("transition_phase") not in {"confirmation_authorized","confirmation_authorization_consumed"}
                        or type(body.get("authorization_request_id")) is not str
                        or re.fullmatch(r"saved-confirmation:[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}",body["authorization_request_id"]) is None
                        or body["transition_phase"]=="confirmation_authorized" and (body.get("claim_owner") is not None or body["state"]!="unconfirmed")
                        or body["transition_phase"]=="confirmation_authorization_consumed" and (type(body.get("claim_owner")) is not str
                            or re.fullmatch(r"publication-bundle:[0-9]+:[a-f0-9]{32}",body["claim_owner"]) is None or body["state"]!="prepared")):
                    raise ValueError("publication_saved_confirmation_authorization_history_changed")
            if (schema not in {"publication-bundle-state-event.v1","publication-bundle-state-event.v2","publication-bundle-state-event.v3"}
                    or set(body)!=fields or body["request_id"]!=job.request_id
                    or type(body["generation"]) is not int or not 0<=body["generation"]<=job.generation
                    or schema=="publication-bundle-state-event.v2" and body["transition_phase"] not in EVENT_PHASES49):
                raise ValueError("publication_saved_confirmation_authorization_history_changed")
        return history,bodies

    def _saved_confirmation_authorizations76(self,session,job,history=None):
        from ..persistence.db import ProductCommandRecord
        from .research_saved_confirmation_commands import ResearchSavedConfirmationCommandService as Commands
        if history is None:history,bodies=self._saved_confirmation_history76(session,job)
        else:bodies=[json.loads(row[history["columns"].index("payload_json")]) for row in history["rows"]]
        records=list(session.scalars(select(ProductCommandRecord).where(ProductCommandRecord.target_id==job.run_id,
            ProductCommandRecord.kind==Commands.KIND).order_by(ProductCommandRecord.request_id).limit(65)
            .execution_options(populate_existing=True)))
        if len(records)>64:raise ValueError("publication_saved_confirmation_authorization_bound_exceeded")
        result=[];keys=set();owners=set()
        for record in records:
            receipt=Commands.validated_receipt(record,run_id=job.run_id)
            command=receipt.command;reply=receipt.result
            saved=session.get(Saved,job.bundle_hash,populate_existing=True)
            if (command.expected_request_id!=job.request_id or command.expected_generation!=job.generation
                    or saved is None or any(getattr(saved,name)!=getattr(command,"expected_"+name)
                        for name in ("bundle_hash","bundle_bytes","material_hash","material_bytes","report_hash","report_bytes","reader_hash"))
                    or sha(encoded(self.saved_confirmation_owner_proof(session,job.run_id,receipt.owner_id)))!=receipt.owner_native_sha256):
                raise ValueError("publication_saved_confirmation_authorization_changed")
            grants=[body for body in bodies if body.get("authorization_request_id")==record.request_id
                and body.get("transition_phase")=="confirmation_authorized"]
            consumed=[body for body in bodies if body.get("authorization_request_id")==record.request_id
                and body.get("transition_phase")=="confirmation_authorization_consumed"]
            previous=bodies[command.expected_state_version-1] if command.expected_state_version<=len(bodies) else None
            if (len(grants)!=1 or len(consumed)>1 or grants[0]["version"]!=reply.authorized_state_version
                    or previous is None or previous["state"]!="unconfirmed"
                    or previous.get("transition_phase")!="unconfirmed" or previous["reason_code"] not in SAVED_CONFIRMATION_TERMINAL_REASONS
                    or previous["generation"]!=job.generation or previous["bundle_hash"]!=command.expected_bundle_hash
                    or grants[0]["generation"]!=job.generation or grants[0]["bundle_hash"]!=command.expected_bundle_hash
                    or grants[0]["reason_code"]!=previous["reason_code"]
                    or consumed and (consumed[0]["version"]!=reply.authorized_state_version+1
                        or consumed[0]["generation"]!=job.generation or consumed[0]["bundle_hash"]!=command.expected_bundle_hash
                        or consumed[0]["reason_code"] is not None or consumed[0]["claim_owner"] in owners)):
                raise ValueError("publication_saved_confirmation_authorization_changed")
            keys.add(record.request_id)
            if consumed:owners.add(consumed[0]["claim_owner"])
            native=self._confirmation_rows74(session,ProductCommandRecord,
                "SELECT * FROM product_commands WHERE request_id=?",(record.request_id,))
            result.append({"receipt":receipt.model_dump(mode="json"),"native":native,"grant":grants[0],
                "consumed":None if not consumed else consumed[0]})
        if any(body.get("schema_version")=="publication-bundle-state-event.v3"
               and body.get("authorization_request_id") not in keys for body in bodies):
            raise ValueError("publication_saved_confirmation_authorization_changed")
        if job.state=="prepared" and result:
            last=max(result,key=lambda item:item["grant"]["version"])
            if last["consumed"] is None or last["consumed"]["claim_owner"]!=job.owner:
                raise ValueError("publication_saved_confirmation_authorization_changed")
        return result

    def _saved_confirmation_ancestor_authorizations76(self,session,run_id):
        """Excluded creation-kind records remain fully visible to native checks."""
        from ..persistence.db import ProductCommandRecord
        current=session.get(RunRecord,run_id,populate_existing=True);result=[];seen=set()
        for _ in range(64):
            if current is None or current.run_id in seen:raise ValueError("publication_confirmation_lineage_changed")
            seen.add(current.run_id)
            if current.run_id!=run_id:
                records=list(session.scalars(select(ProductCommandRecord).where(ProductCommandRecord.target_id==current.run_id,
                    ProductCommandRecord.kind=="saved_confirmation").order_by(ProductCommandRecord.request_id).limit(65)
                    .execution_options(populate_existing=True)))
                if len(records)>64:raise ValueError("publication_saved_confirmation_authorization_bound_exceeded")
                raw=self._confirmation_rows74(session,ProductCommandRecord,
                    "SELECT * FROM product_commands WHERE target_id=? AND kind=? ORDER BY request_id",
                    (current.run_id,"saved_confirmation"),count=len(records))
                authorizations=[]
                if records:
                    parent_job=session.get(Job,current.run_id,populate_existing=True)
                    if parent_job is None:raise ValueError("publication_saved_confirmation_authorization_changed")
                    self._publication(session,parent_job)
                    authorizations=self._saved_confirmation_authorizations76(session,parent_job)
                result.append({"run_id":current.run_id,"native":raw,"authorizations":authorizations})
            if current.parent_run_id is None:break
            current=session.get(RunRecord,current.parent_run_id,populate_existing=True)
        else:raise ValueError("publication_confirmation_lineage_bound_exceeded")
        return result

    def _saved_confirmation_terminal76(self,session,run_id,*,command=None):
        job=session.get(Job,run_id,populate_existing=True)
        if job is None:raise KeyError(run_id)
        if (job.state!="unconfirmed" or job.reason_code not in SAVED_CONFIRMATION_TERMINAL_REASONS
                or job.owner is not None or job.lease_expires_at is not None or job.confirmed_at is not None or not job.bundle_hash):
            raise ValueError("publication_saved_confirmation_exact_terminal_required")
        self._publication(session,job)
        saved=session.get(Saved,job.bundle_hash,populate_existing=True)
        if (saved is None or saved.run_id!=run_id or saved.request_id!=job.request_id or saved.generation!=job.generation
                or saved.bundle_ref!="bundles/"+saved.bundle_hash+".json" or session.get(Ack,run_id,populate_existing=True) is not None):
            raise ValueError("publication_saved_confirmation_saved_identity_changed")
        if command is not None and (job.request_id!=command.expected_request_id or job.generation!=command.expected_generation
                or job.state_version!=command.expected_state_version or any(getattr(saved,name)!=getattr(command,"expected_"+name)
                    for name in ("bundle_hash","bundle_bytes","material_hash","material_bytes","report_hash","report_bytes","reader_hash"))):
            raise ValueError("publication_saved_confirmation_exact_terminal_required")
        history,bodies=self._saved_confirmation_history76(session,job)
        if (bodies[-1]["state"]!=job.state or bodies[-1]["generation"]!=job.generation
                or bodies[-1]["bundle_hash"]!=saved.bundle_hash or bodies[-1]["reason_code"]!=job.reason_code):
            raise ValueError("publication_saved_confirmation_terminal_history_changed")
        if bodies[-1].get("transition_phase") not in {"unconfirmed","confirmation_authorized"}:
            raise ValueError("publication_saved_confirmation_terminal_history_changed")
        proof=[self._confirmation_rows74(session,record,statement,(run_id,),count=count) for record,statement,count in (
            (Job,"SELECT * FROM research_publication_bundle_jobs WHERE run_id=?",1),
            (Saved,"SELECT * FROM research_publication_bundles WHERE run_id=?",1),
            (Ack,"SELECT * FROM research_publication_bundle_confirmations WHERE run_id=?",0),
            (RunRecord,"SELECT * FROM runs WHERE run_id=?",1))]
        for role in (PUBLICATION_SOURCE_ROLE,"graph_recipe_selection"):
            proof.append(self._confirmation_rows74(session,RunRoleOutputRecord,
                "SELECT * FROM run_role_outputs WHERE run_id=? AND role=?",(run_id,role)))
        authorizations=self._saved_confirmation_authorizations76(session,job,history)
        value={"native":proof,"history":history,"lineage":self._prepared_confirmation_lineage74(session,run_id),
            "authorizations":authorizations,"ancestor_authorizations":self._saved_confirmation_ancestor_authorizations76(session,run_id)}
        if len(encoded(value))>32*1024*1024:raise ValueError("publication_confirmation_native_bound_exceeded")
        return job,saved,value

    def saved_confirmation_status(self,run_id,owner_id):
        with self._ro() as reader:
            self.saved_confirmation_owner_proof(reader,run_id,owner_id)
            job=reader.get(Job,run_id,populate_existing=True)
            saved=None if job is None or not job.bundle_hash else reader.get(Saved,job.bundle_hash,populate_existing=True)
            if saved is None:raise KeyError(run_id)
            self._publication(reader,job)
            ack=reader.get(Ack,run_id,populate_existing=True)
            fenced=self.archive.fenced(run_id);pending=False;eligible=False
            if job.state=="unconfirmed" and job.reason_code in SAVED_CONFIRMATION_TERMINAL_REASONS:
                _job,_saved,native=self._saved_confirmation_terminal76(reader,run_id)
                pending=any(item["consumed"] is None for item in native["authorizations"])
                eligible=not pending and not fenced
            return {"schema_version":"research-saved-confirmation-status.v1","run_id":run_id,"request_id":job.request_id,
                "generation":job.generation,"state_version":job.state_version,"state":job.state,"reason_code":job.reason_code,
                "eligible":eligible,"bundle_hash":saved.bundle_hash,"bundle_bytes":saved.bundle_bytes,
                "material_hash":saved.material_hash,"material_bytes":saved.material_bytes,"report_hash":saved.report_hash,
                "report_bytes":saved.report_bytes,"reader_hash":saved.reader_hash,"saved_count":1,"ack_count":0 if ack is None else 1,
                "fenced":fenced,"owner_lease_present":job.owner is not None or job.lease_expires_at is not None,
                "authorization_pending":pending}

    def _saved_confirmation_attempt76(self,claim,phase,digest,authorization_request_id,claim_owner,write):
        """The original fenced writer with a closed, owner-correlated V3 event."""
        if phase not in {"confirmation_authorized","confirmation_authorization_consumed"}:
            raise ValueError("publication_saved_confirmation_phase_invalid")
        def audited(writer):
            write(writer);writer.flush()
            job=writer.get(Job,claim.run_id,populate_existing=True);job.state_version+=1
            body={"schema_version":"publication-bundle-state-event.v3","run_id":job.run_id,"request_id":job.request_id,
                "version":job.state_version,"generation":job.generation,"state":job.state,"reason_code":job.reason_code,
                "bundle_hash":job.bundle_hash,"occurred_at":self.clock().isoformat(),"transition_phase":phase,
                "authorization_request_id":authorization_request_id,"claim_owner":claim_owner}
            raw=encoded(body)
            writer.add(Event(event_id=sha(raw),run_id=job.run_id,request_id=job.request_id,version=job.state_version,
                generation=job.generation,state=job.state,reason_code=job.reason_code,bundle_hash=job.bundle_hash,
                occurred_at=datetime.fromisoformat(body["occurred_at"]),payload_json=raw.decode()))
        return PublicationBundleLifecycle._attempt(self,claim,phase,digest,
            {"authorization_request_id":authorization_request_id,"claim_owner":claim_owner},audited)

    def authorize_saved_confirmation(self,run_id,command,owner_id,*,journal_writer):
        if self.archive.fenced(run_id):raise ValueError("publication_bundle_fence_requires_readonly_reconciliation")
        if owner_id!=command.owner_id:raise PermissionError("publication_saved_confirmation_original_owner_required")
        with self._ro() as reader:
            owner=self.saved_confirmation_owner_proof(reader,run_id,owner_id)
            job,saved,native=self._saved_confirmation_terminal76(reader,run_id,command=command)
            if any(item["consumed"] is None for item in native["authorizations"]):
                raise ValueError("publication_saved_confirmation_exact_terminal_required")
            bundle=self._load_saved(saved);self._publication(reader,job,bundle)
            saved_native=native["native"][1];digest=saved.bundle_hash;reason=job.reason_code
        reply={"schema_version":"research-saved-confirmation-reply.v1","run_id":run_id,"request_id":command.request_id,
            "expected_request_id":command.expected_request_id,"generation":command.expected_generation,
            "previous_state_version":command.expected_state_version,"authorized_state_version":command.expected_state_version+1,
            "state":"unconfirmed","authorization_status":"accepted","report_confirmed":False,"automatic_retry":False}
        claim=BundleClaim(run_id,command.expected_request_id,"internal-saved-confirmation-authorization",command.expected_generation)
        def write(writer):
            job,_saved,fresh=self._saved_confirmation_terminal76(writer,run_id,command=command)
            if fresh!=native or self.saved_confirmation_owner_proof(writer,run_id,owner_id)!=owner:
                raise PermissionError("publication_saved_confirmation_authorization_CAS_lost")
            changed=writer.execute(update(Job).where(Job.run_id==run_id,Job.request_id==command.expected_request_id,
                Job.generation==command.expected_generation,Job.state_version==command.expected_state_version,
                Job.state=="unconfirmed",Job.reason_code==reason,Job.owner.is_(None),
                Job.lease_expires_at.is_(None),Job.bundle_hash==digest,Job.confirmed_at.is_(None))
                .execution_options(synchronize_session=False).values(updated_at=self.clock()))
            if changed.rowcount!=1:raise PermissionError("publication_saved_confirmation_authorization_CAS_lost")
            writer.expire(job);writer.refresh(job);journal_writer(writer,reply,write=True)
        fence=self._saved_confirmation_attempt76(claim,"confirmation_authorized",digest,command.request_id,None,write)
        with self._ro() as reader:
            job,saved,readback=self._saved_confirmation_terminal76(reader,run_id)
            if (job.state_version!=command.expected_state_version+1 or readback["native"][1:]!=native["native"][1:]
                    or readback["lineage"]!=native["lineage"]
                    or readback["ancestor_authorizations"]!=native["ancestor_authorizations"]
                    or readback["history"]["rows"][:-1]!=native["history"]["rows"]
                    or [item for item in readback["authorizations"] if item["receipt"]["request_id"]!=command.request_id]!=native["authorizations"]
                    or self.saved_confirmation_owner_proof(reader,run_id,owner_id)!=owner or self._load_saved(saved)!=bundle):
                raise ValueError("publication_saved_confirmation_authorization_readback_changed")
            journal_writer(reader,reply,write=False)
        self.archive.release(run_id,fence)
        return reply

    def claim_authorized_saved_confirmation(self,*,owner,lease_seconds=180):
        if re.fullmatch(r"publication-bundle:[0-9]+:[a-f0-9]{32}",owner) is None or type(lease_seconds) is not int or not 90<=lease_seconds<=180:
            raise ValueError("publication_job_internal_worker_identity_required")
        with self._ro() as reader:
            candidates=list(reader.scalars(select(Job).join(Event,and_(Event.run_id==Job.run_id,Event.version==Job.state_version))
                .where(Event.payload_json.contains('"transition_phase":"confirmation_authorized"'),
                Job.state=="unconfirmed",Job.owner.is_(None),Job.lease_expires_at.is_(None),
                Job.reason_code.in_(SAVED_CONFIRMATION_TERMINAL_REASONS),Job.bundle_hash.is_not(None),Job.confirmed_at.is_(None))
                .order_by(Job.updated_at,Job.run_id).limit(8)))
            chosen=None
            for candidate in candidates:
                if self.archive.fenced(candidate.run_id):continue
                job,saved,native=self._saved_confirmation_terminal76(reader,candidate.run_id)
                pending=[item for item in native["authorizations"] if item["consumed"] is None]
                if not pending:continue
                if len(pending)!=1 or pending[0]["grant"]["version"]!=job.state_version:
                    raise ValueError("publication_saved_confirmation_authorization_changed")
                if any(item["consumed"] is not None and item["consumed"]["claim_owner"]==owner for item in native["authorizations"]):
                    raise PermissionError("publication_saved_confirmation_fresh_owner_required")
                chosen=(job.run_id,job.request_id,job.generation,job.state_version,job.attempts,saved.bundle_hash)
                reason=job.reason_code
                grant=pending[0];bundle=self._load_saved(saved);self._publication(reader,job,bundle);break
            if chosen is None:return None
            now=self.clock();until=now+timedelta(seconds=lease_seconds)
        claim=BundleClaim(chosen[0],chosen[1],owner,chosen[2])
        def write(writer):
            job,_saved,fresh=self._saved_confirmation_terminal76(writer,chosen[0])
            if fresh!=native:raise PermissionError("publication_saved_confirmation_claim_CAS_lost")
            changed=writer.execute(update(Job).where(Job.run_id==chosen[0],Job.request_id==chosen[1],Job.generation==chosen[2],
                Job.state_version==chosen[3],Job.state=="unconfirmed",Job.reason_code==reason,
                Job.owner.is_(None),Job.lease_expires_at.is_(None),Job.bundle_hash==chosen[5],Job.confirmed_at.is_(None))
                .execution_options(synchronize_session=False).values(state="prepared",reason_code=None,owner=owner,
                    lease_expires_at=until,attempts=chosen[4]+1,updated_at=now))
            if changed.rowcount!=1:raise PermissionError("publication_saved_confirmation_claim_CAS_lost")
            writer.expire(job);writer.refresh(job)
        try:
            fence=self._saved_confirmation_attempt76(claim,"confirmation_authorization_consumed",chosen[5],
                grant["receipt"]["request_id"],owner,write)
        except PermissionError as error:
            if error.args==("publication_saved_confirmation_claim_CAS_lost",):return None
            raise
        except ValueError as error:
            if error.args in {("publication_bundle_fence_requires_readonly_reconciliation",),
                              ("publication_saved_confirmation_exact_terminal_required",)}:return None
            raise
        with self._ro() as reader:
            job,saved=self._prepared_confirmation_head74(reader,claim.run_id,claim=claim)
            fresh=self._prepared_confirmation_native74(reader,claim.run_id)
            restored=[]
            for item in fresh["authorizations"]:
                if item["receipt"]["request_id"]==grant["receipt"]["request_id"]:
                    if (item["consumed"] is None or item["consumed"]["claim_owner"]!=owner
                            or item["consumed"]["version"]!=chosen[3]+1):
                        raise ValueError("publication_saved_confirmation_claim_readback_changed")
                    restored.append({**item,"consumed":None})
                else:restored.append(item)
            if (job.state_version!=chosen[3]+1 or job.attempts!=chosen[4]+1 or as_utc(job.lease_expires_at)!=until
                    or fresh["native"][1:]!=native["native"][1:] or fresh["lineage"]!=native["lineage"]
                    or fresh["ancestor_authorizations"]!=native["ancestor_authorizations"]
                    or fresh["history"]["rows"][:-1]!=native["history"]["rows"]
                    or restored!=native["authorizations"] or self._load_saved(saved)!=bundle):
                raise ValueError("publication_saved_confirmation_claim_readback_changed")
        self.archive.release(claim.run_id,fence)
        return claim

    def _prepared_confirmation_head74(self,session,run_id,*,claim=None,expired=False):
        job=session.get(Job,run_id,populate_existing=True)
        if (job is None or job.state!="prepared" or not job.bundle_hash or job.confirmed_at is not None
                or job.reason_code is not None or not job.owner or as_utc(job.lease_expires_at) is None
                or expired and as_utc(job.lease_expires_at)>self.clock()):
            raise PermissionError("publication_confirmation_exact_prepared_required")
        if claim is not None:job=self._owned(session,claim,"prepared")
        else:self._publication(session,job)
        saved=session.get(Saved,job.bundle_hash,populate_existing=True)
        if (saved is None or saved.run_id!=run_id or saved.request_id!=job.request_id
                or saved.generation!=job.generation or saved.bundle_hash!=job.bundle_hash
                or saved.bundle_ref!="bundles/"+saved.bundle_hash+".json"
                or session.get(Ack,run_id,populate_existing=True) is not None):
            raise ValueError("publication_confirmation_saved_identity_changed")
        return job,saved

    def _prepared_confirmation_native74(self,session,run_id):
        job,saved=self._prepared_confirmation_head74(session,run_id)
        proof=[]
        for record,statement,parameters,count in (
            (Job,"SELECT * FROM research_publication_bundle_jobs WHERE run_id=?",(run_id,),1),
            (Saved,"SELECT * FROM research_publication_bundles WHERE run_id=?",(run_id,),1),
            (Ack,"SELECT * FROM research_publication_bundle_confirmations WHERE run_id=?",(run_id,),0),
            (RunRecord,"SELECT * FROM runs WHERE run_id=?",(run_id,),1),
            (RunRoleOutputRecord,"SELECT * FROM run_role_outputs WHERE run_id=? AND role=?",(run_id,PUBLICATION_SOURCE_ROLE),1),
            (RunRoleOutputRecord,"SELECT * FROM run_role_outputs WHERE run_id=? AND role=?",(run_id,"graph_recipe_selection"),1),
        ):proof.append(self._confirmation_rows74(session,record,statement,parameters,count=count))
        history=self._retry_gap_history_native49(session,run_id,job.state_version)
        bodies=[json.loads(row[history["columns"].index("payload_json")]) for row in history["rows"]]
        if any(body.get("schema_version")=="publication-bundle-state-event.v3" for body in bodies):
            self._saved_confirmation_history76(session,job)
        for body in bodies:
            fields={"schema_version","run_id","request_id","version","generation","state",
                "reason_code","bundle_hash","occurred_at"}
            schema=body.get("schema_version")
            if schema=="publication-bundle-state-event.v2":
                fields.add("transition_phase")
                if "failure_diagnostic" in body:
                    if body.get("transition_phase")!="unconfirmed":
                        raise ValueError("publication_confirmation_history_changed")
                    fields.add("failure_diagnostic");_failure_metadata49(body["failure_diagnostic"])
            if schema=="publication-bundle-state-event.v3":
                fields.update({"transition_phase","authorization_request_id","claim_owner"})
            if (schema not in {"publication-bundle-state-event.v1","publication-bundle-state-event.v2","publication-bundle-state-event.v3"}
                    or set(body)!=fields or body["request_id"]!=job.request_id
                    or type(body["generation"]) is not int or not 0<=body["generation"]<=job.generation
                    or schema=="publication-bundle-state-event.v2" and body["transition_phase"] not in EVENT_PHASES49):
                raise ValueError("publication_confirmation_history_changed")
        if (bodies[-1]["state"]!="prepared" or bodies[-1]["generation"]!=job.generation
                or bodies[-1]["bundle_hash"]!=saved.bundle_hash):
            raise ValueError("publication_confirmation_history_changed")
        value={"native":proof,"history":history,"lineage":self._prepared_confirmation_lineage74(session,run_id),
            "authorizations":self._saved_confirmation_authorizations76(session,job,history),
            "ancestor_authorizations":self._saved_confirmation_ancestor_authorizations76(session,run_id)}
        if len(encoded(value))>32*1024*1024:raise ValueError("publication_confirmation_native_bound_exceeded")
        return value

    def prepared_confirmation_input(self,session,claim):
        if self.archive.fenced(claim.run_id):raise ValueError("publication_bundle_fence_requires_readonly_reconciliation")
        job,saved=self._prepared_confirmation_head74(session,claim.run_id,claim=claim)
        self._prepared_confirmation_native74(session,claim.run_id)
        bundle=self._load_saved(saved);self._publication(session,job,bundle)
        return bundle,self.archive._path(saved.material_ref)

    def claim_prepared_confirmation(self,*,owner: str,lease_seconds: int=180):
        """One automatic continuation of an expired exact original generation."""
        if (not re.fullmatch(r"publication-bundle:[0-9]+:[a-f0-9]{32}",owner)
                or type(lease_seconds) is not int or not 90<=lease_seconds<=180):
            raise ValueError("publication_job_internal_worker_identity_required")
        now=self.clock()
        with self._ro() as reader:
            jobs=list(reader.scalars(select(Job).where(Job.state=="prepared",Job.lease_expires_at<=now,
                Job.owner.is_not(None),Job.bundle_hash.is_not(None),Job.confirmed_at.is_(None),Job.reason_code.is_(None))
                .order_by(Job.created_at,Job.run_id).limit(8)))
            chosen=next((job for job in jobs if not self.archive.fenced(job.run_id)),None)
            if chosen is None:return None
            # Complete metadata is sufficient only to deny this exact expired
            # head. It cannot authorize confirmation when verification fails.
            raw=[self._confirmation_rows74(reader,record,statement,parameters,count=count)
                for record,statement,parameters,count in (
                    (Job,"SELECT * FROM research_publication_bundle_jobs WHERE run_id=?",(chosen.run_id,),1),
                    (Saved,"SELECT * FROM research_publication_bundles WHERE run_id=?",(chosen.run_id,),1),
                    (Ack,"SELECT * FROM research_publication_bundle_confirmations WHERE run_id=?",(chosen.run_id,),0))]
            old=(chosen.run_id,chosen.request_id,chosen.generation,chosen.state_version,chosen.owner,chosen.lease_expires_at)
            digest=chosen.bundle_hash
            old_attempts=chosen.attempts
            new_lease=now+timedelta(seconds=lease_seconds)
            saved=reader.get(Saved,digest,populate_existing=True)
            rejection=None
            try:
                job,saved=self._prepared_confirmation_head74(reader,chosen.run_id,expired=True)
                native=self._prepared_confirmation_native74(reader,job.run_id)
                bundle=self._load_saved(saved);self._publication(reader,job,bundle)
            except (ValueError,PermissionError,OSError):
                rejection="publication_confirmation_precondition_failed"
                native=None;bundle=None
            if rejection is not None:
                interrupted=False
            else:
                history=native["history"]
                interrupted=any(json.loads(row[history["columns"].index("payload_json")]).get("transition_phase")
                    in {"confirmation_claimed","confirmation_authorization_consumed"}
                    and row[history["columns"].index("generation")]==chosen.generation
                    for row in history["rows"])
            terminal_reason=rejection or ("publication_confirmation_interrupted" if interrupted else None)
        claim=BundleClaim(old[0],old[1],owner,old[2])
        def write(writer):
            current=writer.get(Job,old[0],populate_existing=True)
            fresh=[self._confirmation_rows74(writer,record,statement,parameters,count=count)
                for record,statement,parameters,count in (
                    (Job,"SELECT * FROM research_publication_bundle_jobs WHERE run_id=?",(old[0],),1),
                    (Saved,"SELECT * FROM research_publication_bundles WHERE run_id=?",(old[0],),1),
                    (Ack,"SELECT * FROM research_publication_bundle_confirmations WHERE run_id=?",(old[0],),0))]
            if fresh!=raw:
                raise PermissionError("publication_confirmation_claim_CAS_lost")
            if terminal_reason is None:
                self._prepared_confirmation_head74(writer,old[0],expired=True)
                if self._prepared_confirmation_native74(writer,old[0])!=native:
                    raise PermissionError("publication_confirmation_claim_CAS_lost")
            changed=writer.execute(update(Job).where(Job.run_id==old[0],Job.request_id==old[1],Job.generation==old[2],
                Job.state_version==old[3],Job.state=="prepared",Job.owner==old[4],Job.lease_expires_at==old[5],
                Job.lease_expires_at<=now,Job.bundle_hash==current.bundle_hash,Job.confirmed_at.is_(None),Job.reason_code.is_(None))
                .execution_options(synchronize_session=False).values(**({"state":"unconfirmed",
                    "reason_code":terminal_reason,"owner":None,"lease_expires_at":None}
                    if terminal_reason is not None else {"owner":owner,"lease_expires_at":new_lease,
                        "attempts":old_attempts+1}),updated_at=now))
            if changed.rowcount!=1:raise PermissionError("publication_confirmation_claim_CAS_lost")
            writer.expire(current);writer.refresh(current)
        phase="unconfirmed" if terminal_reason is not None else "confirmation_claimed"
        try:
            fence=self._attempt(claim,phase,digest,
                {"reason_code":terminal_reason} if terminal_reason is not None else {},write)
        except PermissionError as error:
            if error.args==("publication_confirmation_claim_CAS_lost",):return None
            raise
        except ValueError as error:
            if error.args==("publication_bundle_fence_requires_readonly_reconciliation",):return None
            raise
        with self._ro() as reader:
            current=reader.get(Job,old[0],populate_existing=True)
            if (current is None or current.request_id!=old[1] or current.generation!=old[2]
                    or current.state_version!=old[3]+1 or current.bundle_hash!=digest
                    or reader.get(Ack,old[0],populate_existing=True) is not None
                    or current.state!=("unconfirmed" if terminal_reason is not None else "prepared")
                    or current.owner!=(None if terminal_reason is not None else owner)
                    or as_utc(current.lease_expires_at)!=(None if terminal_reason is not None else new_lease)
                    or current.attempts!=(old_attempts if terminal_reason is not None else old_attempts+1)
                    or self._confirmation_rows74(reader,Saved,
                        "SELECT * FROM research_publication_bundles WHERE run_id=?",(old[0],))!=raw[1]):
                raise ValueError("publication_confirmation_claim_readback_changed")
            if terminal_reason is None:
                if self._load_saved(reader.get(Saved,digest,populate_existing=True))!=bundle:
                    raise ValueError("publication_confirmation_claim_readback_changed")
                self._publication(reader,current,bundle)
        self.archive.release(claim.run_id,fence)
        return None if terminal_reason is not None else claim

    def renew_prepared_confirmation(self,claim,*,lease_seconds: int=180):
        if type(lease_seconds) is not int or not 90<=lease_seconds<=180:raise ValueError("publication_renew_lease_bound_required")
        if self.archive.fenced(claim.run_id):raise ValueError("publication_bundle_fence_requires_readonly_reconciliation")
        with self._ro() as reader:
            job,saved=self._prepared_confirmation_head74(reader,claim.run_id,claim=claim)
            version,old_lease,digest=job.state_version,job.lease_expires_at,saved.bundle_hash
            native=self._prepared_confirmation_native74(reader,claim.run_id);history=native["history"]
            if not any(json.loads(row[history["columns"].index("payload_json")]).get("transition_phase")
                in {"confirmation_claimed","confirmation_authorization_consumed"}
                and row[history["columns"].index("generation")]==claim.generation
                    for row in history["rows"]):raise PermissionError("publication_confirmation_claim_required")
        until=None
        def write(writer):
            nonlocal until
            job,_saved=self._prepared_confirmation_head74(writer,claim.run_id,claim=claim)
            if self._prepared_confirmation_native74(writer,claim.run_id)!=native:
                raise PermissionError("publication_renew_exact_head_CAS_lost")
            now=self.clock();until=now+timedelta(seconds=lease_seconds)
            if until<=as_utc(old_lease):raise PermissionError("publication_renew_clock_did_not_advance")
            changed=writer.execute(update(Job).where(Job.run_id==claim.run_id,Job.request_id==claim.request_id,
                Job.owner==claim.owner,Job.generation==claim.generation,Job.state=="prepared",Job.state_version==version,
                Job.lease_expires_at==old_lease,Job.lease_expires_at>now,Job.bundle_hash==job.bundle_hash,
                Job.confirmed_at.is_(None),Job.reason_code.is_(None)).execution_options(synchronize_session=False)
                .values(lease_expires_at=until,updated_at=now))
            if changed.rowcount!=1:raise PermissionError("publication_renew_exact_head_CAS_lost")
            writer.expire(job);writer.refresh(job)
        fence=self._attempt(claim,"confirmation_lease_renewed",digest,{},write)
        with self._ro() as reader:
            current,_=self._prepared_confirmation_head74(reader,claim.run_id,claim=claim)
            readback=self._prepared_confirmation_native74(reader,claim.run_id)
            if current.state_version!=version+1 or as_utc(current.lease_expires_at)!=until:
                raise ValueError("publication_renew_independent_readback_changed")
            if (readback["native"][1:]!=native["native"][1:] or readback["lineage"]!=native["lineage"]
                    or readback["authorizations"]!=native["authorizations"]
                    or readback["ancestor_authorizations"]!=native["ancestor_authorizations"]):
                raise ValueError("publication_renew_independent_readback_changed")
        self.archive.release(claim.run_id,fence)
        return True

    def fail_prepared_confirmation(self,claim,*,reason_code="publication_confirmation_failed",failure_diagnostic=None):
        """Same-gen durable denial; never queued cancellation or Saved deletion."""
        if not re.fullmatch(r"[a-z0-9_]{1,160}",reason_code):reason_code="publication_confirmation_failed"
        if self.archive.fenced(claim.run_id):raise ValueError("publication_bundle_fence_requires_readonly_reconciliation")
        with self._ro() as reader:
            current=reader.get(Job,claim.run_id,populate_existing=True)
            if (current is None or current.request_id!=claim.request_id or current.generation!=claim.generation
                    or current.owner!=claim.owner or current.state!="prepared"):
                raise PermissionError("publication_bundle_internal_claim_lost")
            original_saved=self._confirmation_rows74(reader,Saved,
                "SELECT * FROM research_publication_bundles WHERE run_id=?",(claim.run_id,))
            self._confirmation_rows74(reader,Ack,
                "SELECT * FROM research_publication_bundle_confirmations WHERE run_id=?",(claim.run_id,),count=0)
            digest=current.bundle_hash
        def write(writer):
            job=writer.get(Job,claim.run_id,populate_existing=True)
            if (job is None or job.request_id!=claim.request_id or job.generation!=claim.generation
                    or job.owner!=claim.owner or job.state!="prepared"
                    or job.bundle_hash!=digest
                    or writer.get(Ack,claim.run_id,populate_existing=True) is not None
                    or self._confirmation_rows74(writer,Saved,
                        "SELECT * FROM research_publication_bundles WHERE run_id=?",(claim.run_id,))!=original_saved):
                raise PermissionError("publication_bundle_internal_claim_lost")
            changed=writer.execute(update(Job).where(Job.run_id==claim.run_id,Job.request_id==claim.request_id,
                Job.generation==claim.generation,Job.owner==claim.owner,Job.state=="prepared",
                Job.state_version==job.state_version,Job.bundle_hash==job.bundle_hash,Job.confirmed_at.is_(None))
                .execution_options(synchronize_session=False).values(state="unconfirmed",reason_code=reason_code,
                    owner=None,lease_expires_at=None,updated_at=self.clock()))
            if changed.rowcount!=1:raise PermissionError("publication_bundle_internal_claim_lost")
            writer.expire(job);writer.refresh(job)
        body={"reason_code":reason_code}
        if failure_diagnostic is not None:body["failure_diagnostic"]=failure_diagnostic
        fence=self._attempt(claim,"unconfirmed",digest,body,write)
        with self._ro() as reader:
            job=reader.get(Job,claim.run_id,populate_existing=True)
            if (job is None or job.state!="unconfirmed" or job.generation!=claim.generation
                    or job.request_id!=claim.request_id or job.reason_code!=reason_code
                    or job.bundle_hash!=digest
                    or reader.get(Ack,claim.run_id,populate_existing=True) is not None
                    or self._confirmation_rows74(reader,Saved,
                        "SELECT * FROM research_publication_bundles WHERE run_id=?",(claim.run_id,))!=original_saved):
                raise ValueError("publication_confirmation_failure_readback_changed")
        self.archive.release(claim.run_id,fence)

    def _preparation_authorization_head79(self,session,run_id,*,expected_request,
                                          expected_generation,expected_version):
        """Closed scheduled no-Saved head; diagnostics never grant a permit."""
        job=self._retry_head49(session,run_id,expected_request=expected_request,
            expected_generation=expected_generation,expected_version=expected_version)
        run=session.get(RunRecord,run_id,populate_existing=True)
        if (job.reason_code!="frozen_io_undeclared_authorization_resource" or run is None
                or run.admission_origin!="scheduled_recheck" or not run.parent_run_id
                or type(run.idempotency_key) is not str
                or not run.idempotency_key.startswith("research-review-plan:")):
            raise ValueError("publication_preparation_recovery_exact_head_required")
        plan_id=run.idempotency_key[len("research-review-plan:"):]
        if not 1<=len(plan_id)<=128 or plan_id!=plan_id.strip():
            raise ValueError("publication_preparation_recovery_plan_changed")
        history=self._retry_gap_history_native49(session,run_id,expected_version)
        last=dict(zip(history["columns"],history["rows"][-1]))
        body=json.loads(last["payload_json"])
        if (last["generation"]!=job.generation or last["request_id"]!=job.request_id
                or last["state"]!="unconfirmed" or last["reason_code"]!=job.reason_code
                or last["bundle_hash"] is not None or body.get("schema_version")!="publication-bundle-state-event.v2"
                or body.get("transition_phase")!="unconfirmed"
                or body.get("failure_diagnostic")!={"status":"unavailable","sha256":None}):
            raise ValueError("publication_preparation_recovery_terminal_history_changed")
        native=[self._confirmation_rows74(session,record,statement,parameters,count=count)
            for record,statement,parameters,count in (
                (Job,"SELECT * FROM research_publication_bundle_jobs WHERE run_id=?",(run_id,),1),
                (RunRecord,"SELECT * FROM runs WHERE run_id=?",(run_id,),1),
                (RunRoleOutputRecord,"SELECT * FROM run_role_outputs WHERE run_id=? AND role=?",(run_id,PUBLICATION_SOURCE_ROLE),1),
                (RunRoleOutputRecord,"SELECT * FROM run_role_outputs WHERE run_id=? AND role=?",(run_id,"graph_recipe_selection"),1),
                (Saved,"SELECT * FROM research_publication_bundles WHERE run_id=?",(run_id,),0),
                (Ack,"SELECT * FROM research_publication_bundle_confirmations WHERE run_id=?",(run_id,),0),
            )]
        return job,plan_id,sha(encoded([native,history]))

    def _retry_head49(self, session, run_id, *, expected_request, expected_generation, expected_version):
        job=session.get(Job,run_id,populate_existing=True)
        if (job is None or job.request_id!=expected_request or job.generation!=expected_generation
                or job.state_version!=expected_version or job.state!="unconfirmed"
                or job.owner is not None or job.lease_expires_at is not None
                or job.bundle_hash is not None or job.confirmed_at is not None):
            raise ValueError("publication_retry_exact_unconfirmed_head_required")
        self._publication(session,job)  # Includes complete native seven-field selection.
        native=session.connection()
        for table in ("research_publication_bundles","research_publication_bundle_confirmations"):
            if native.exec_driver_sql("SELECT * FROM "+table+" WHERE run_id=?",(run_id,)).fetchall():
                raise ValueError("publication_retry_saved_or_confirmed_requires_review")
        return job

    def _retry_archive_inventory51(self):
        """Complete bounded lexical inventory; metadata never substitutes for hashes."""
        entries = []
        for kind in ("bundles", "materials"):
            directory = self.archive._path(kind + "/" + "0" * 64 + ".json").parent
            if not directory.exists():
                continue
            if not directory.is_dir():
                raise ValueError("publication_retry_archive_ownership_unknown")
            for path in directory.iterdir():
                if len(entries) >= 4000 or not re.fullmatch(r"[a-f0-9]{64}\.json", path.name):
                    raise ValueError("publication_retry_archive_ownership_unknown")
                reference = kind + "/" + path.name
                checked = self.archive._path(reference)
                native = checked.stat()
                bound = self.archive.max_bundle_bytes if kind == "bundles" else self.archive.max_material_bytes
                if not checked.is_file() or not 0 < native.st_size <= bound:
                    raise ValueError("publication_retry_archive_ownership_unknown")
                entries.append((reference, native.st_dev, native.st_ino, native.st_size, native.st_mtime_ns))
        return sorted(entries)

    def _retry_owner_native51(self, session, run_id, bundle_hash):
        """Full original SQLite cells, including REAL hex; no ORM serialization proof."""
        queries = (
            (Job, "SELECT * FROM research_publication_bundle_jobs WHERE run_id=?", (run_id,)),
            (Saved, "SELECT * FROM research_publication_bundles WHERE bundle_hash=?", (bundle_hash,)),
            (Ack, "SELECT * FROM research_publication_bundle_confirmations WHERE run_id=?", (run_id,)),
            (RunRecord, "SELECT * FROM runs WHERE run_id=?", (run_id,)),
            (RunRoleOutputRecord, "SELECT * FROM run_role_outputs WHERE run_id=? AND role=?", (run_id, PUBLICATION_SOURCE_ROLE)),
            (RunRoleOutputRecord, "SELECT * FROM run_role_outputs WHERE run_id=? AND role=?", (run_id, "graph_recipe_selection")),
        )
        proof = []
        for record, statement, parameters in queries:
            result = session.connection().exec_driver_sql(statement, parameters)
            columns, rows = list(result.keys()), result.fetchall()
            expected_columns = {column.name: column for column in record.__table__.columns}
            # Migration-added columns retain their physical SQLite order.
            # Keep that exact order in the proof; require every declared field once.
            if (len(columns) != len(expected_columns)
                    or any(type(name) is not str for name in columns)
                    or len(set(columns)) != len(columns)
                    or set(columns) != set(expected_columns)
                    or len(rows) != 1 or len(rows[0]) != len(columns)):
                raise ValueError("publication_retry_archive_ownership_unknown")
            cells = []
            for name, value in zip(columns, rows[0]):
                column = expected_columns[name]
                expected = column.type.python_type
                expected = str if expected is datetime else expected
                if (value is None and not column.nullable
                        or value is not None and type(value) is not expected):
                    raise ValueError("publication_retry_archive_ownership_unknown")
                if value is None:
                    cells.append({"type":"null", "value":None})
                elif type(value) is str:
                    cells.append({"type":"text", "value":value})
                elif type(value) is int:
                    cells.append({"type":"integer", "value":value})
                elif type(value) is float:
                    cells.append({"type":"real", "value":value.hex()})
                elif type(value) is bytes:
                    cells.append({"type":"blob", "value":value.hex()})
                else:
                    raise ValueError("publication_retry_archive_ownership_unknown")
            proof.append({"table":record.__tablename__, "columns":columns, "cells":cells})
        return proof

    def _retry_owned_archive_proof51(self, session, run_id):
        """Legacy recovery only when every original belongs to another confirmed Run.

        Existing 16MiB bundle/192MiB material limits remain authoritative.
        This finite ownership census additionally limits manifest/native proof
        bytes to 32MiB and full material verification work to one configured
        material bound. It never guesses an orphan or in-flight partial owner.
        """
        inventory = self._retry_archive_inventory51()
        owners, material_refs = [], set()
        manifest_bytes = material_work = native_bytes = 0
        for reference, _device, _inode, size, _mtime in inventory:
            if not reference.startswith("bundles/"):
                continue
            manifest_bytes += size
            if manifest_bytes > 32 * 1024 * 1024:
                raise ValueError("publication_retry_archive_ownership_bound_exceeded")
            digest = reference.split("/")[1][:-5]
            saved = session.get(Saved, digest, populate_existing=True)
            if (saved is None or saved.run_id == run_id or saved.bundle_ref != reference
                    or saved.bundle_bytes != size or saved.bundle_hash != digest):
                raise ValueError("publication_retry_archive_ownership_unknown")
            native = self._retry_owner_native51(session, saved.run_id, digest)
            material_work += saved.material_bytes
            if material_work > self.archive.max_material_bytes:
                raise ValueError("publication_retry_archive_ownership_bound_exceeded")
            # The original reader checks the complete manifest, material hash,
            # native selection, current publication, Saved and independent ACK.
            bundle, identity = self._confirmed(session, saved.run_id)
            if (bundle is None or bundle.run_id != saved.run_id
                    or type(identity) is not tuple or len(identity) != 7 or identity[2] != digest
                    or bundle.material_ref != saved.material_ref
                    or sha(encoded(bundle.model_dump(mode="json"))) != digest
                    or len(encoded(bundle.model_dump(mode="json"))) != size):
                raise ValueError("publication_retry_archive_ownership_unknown")
            material_refs.add(bundle.material_ref)
            native_bytes += len(encoded(native))
            if native_bytes > 32 * 1024 * 1024:
                raise ValueError("publication_retry_archive_ownership_bound_exceeded")
            owners.append({"run_id":saved.run_id, "bundle_hash":digest, "native":native})
        if {entry[0] for entry in inventory if entry[0].startswith("materials/")} != material_refs:
            raise ValueError("publication_retry_archive_ownership_unknown")
        if inventory != self._retry_archive_inventory51():
            raise ValueError("publication_retry_archive_ownership_changed")
        return {"inventory":inventory, "owners":owners}

    def _known_cancel_archive_ownership69(self, session, run_id):
        """Queue-only ownership; confirmed snapshots, not other material byte audits.

        Target source/history/issuer checks remain with retry_known_cancelled.
        Unrelated confirmed material keeps its original per-file bound and
        ownership; this scope neither reads nor grants serving of those bytes.
        """
        for table in ("research_publication_bundles", "research_publication_bundle_confirmations"):
            if session.connection().exec_driver_sql(
                    "SELECT 1 FROM "+table+" WHERE run_id=? LIMIT 1", (run_id,)).fetchone():
                raise ValueError("publication_retry_saved_or_confirmed_requires_review")
        inventory = self._retry_archive_inventory51()
        by_reference = {entry[0]: entry for entry in inventory}
        owners, material_refs = [], set()
        manifest_bytes = native_bytes = 0
        for reference, _device, _inode, size, _mtime in inventory:
            if not reference.startswith("bundles/"):
                continue
            manifest_bytes += size
            if manifest_bytes > 32 * 1024 * 1024:
                raise ValueError("publication_retry_archive_ownership_bound_exceeded")
            digest = reference.split("/")[1][:-5]
            saved = session.get(Saved, digest, populate_existing=True)
            if (saved is None or saved.run_id == run_id or saved.bundle_ref != reference
                    or saved.bundle_bytes != size or saved.bundle_hash != digest):
                raise ValueError("publication_retry_archive_ownership_unknown")
            native = self._retry_owner_native51(session, saved.run_id, digest)
            native_bytes += len(encoded(native))
            if native_bytes > 32 * 1024 * 1024:
                raise ValueError("publication_retry_archive_ownership_bound_exceeded")
            bundle, identity = PublicationBundleLifecycle._confirmed_report_snapshot(self, session, saved.run_id)
            material = by_reference.get(saved.material_ref)
            if (bundle is None or bundle.run_id != saved.run_id
                    or type(identity) is not tuple or len(identity) != 7 or identity[2] != digest
                    or bundle.material_ref != saved.material_ref
                    or bundle.material_sha256 != saved.material_hash
                    or bundle.material_ref != "materials/" + bundle.material_sha256 + ".json"
                    or bundle.material_bytes != saved.material_bytes
                    or type(saved.material_bytes) is not int
                    or not 0 < saved.material_bytes <= self.archive.max_material_bytes
                    or material is None or material[3] != saved.material_bytes
                    or sha(encoded(bundle.model_dump(mode="json"))) != digest
                    or len(encoded(bundle.model_dump(mode="json"))) != size):
                raise ValueError("publication_retry_archive_ownership_unknown")
            material_refs.add(bundle.material_ref)
            owners.append({"run_id": saved.run_id, "bundle_hash": digest,
                           "native": native, "snapshot_identity": identity})
        if {entry[0] for entry in inventory if entry[0].startswith("materials/")} != material_refs:
            raise ValueError("publication_retry_archive_ownership_unknown")
        if inventory != self._retry_archive_inventory51():
            raise ValueError("publication_retry_archive_ownership_changed")
        return {"scope": "known-cancel-confirmed-snapshot-archive-ownership.v1",
                "full_external_material_audit": False,
                "inventory": inventory, "owners": owners,
                "material_references": sorted(material_refs)}

    def _retry_owner_native_unchanged51(self, session, proof):
        # Only exact native reads in the writer. No file scan, file hash,
        # confirmation issuance or cross-call cached permission happens here.
        for owner in proof["owners"]:
            if self._retry_owner_native51(session, owner["run_id"], owner["bundle_hash"]) != owner["native"]:
                raise ValueError("publication_retry_archive_ownership_changed")

    def _retry_gap_history_native49(self, session, run_id, expected_version):
        """Gap-mode only: complete native history, never an event-ID cache."""
        result=session.connection().exec_driver_sql(
            "SELECT * FROM research_publication_bundle_state_events WHERE run_id=? "
            "AND version<=? ORDER BY version LIMIT 4001", (run_id,expected_version))
        columns, rows=list(result.keys()),result.fetchall()
        declared={column.name:column for column in Event.__table__.columns}
        if (len(columns)!=len(declared) or len(set(columns))!=len(columns)
                or set(columns)!=set(declared) or not 0<len(rows)<=4000 or len(rows)!=expected_version):
            raise ValueError("publication_retry_event_history_changed")
        proof=[]
        for position,values in enumerate(rows,1):
            if len(values)!=len(columns):raise ValueError("publication_retry_event_history_changed")
            row=dict(zip(columns,values))
            for name,value in row.items():
                column=declared[name]; kind=column.type.python_type
                kind=str if kind is datetime else kind
                if (value is None and not column.nullable or value is not None and type(value) is not kind):
                    raise ValueError("publication_retry_event_history_changed")
            body=json.loads(row["payload_json"])
            if (type(body) is not dict or encoded(body).decode()!=row["payload_json"]
                    or sha(row["payload_json"].encode())!=row["event_id"]
                    or row["run_id"]!=run_id or row["version"]!=position
                    or any(body.get(name)!=row[name] for name in (
                        "run_id","request_id","version","generation","state","reason_code","bundle_hash"))):
                raise ValueError("publication_retry_event_history_changed")
            if (type(body.get("occurred_at")) is not str
                    or as_utc(datetime.fromisoformat(body["occurred_at"]))!=as_utc(datetime.fromisoformat(row["occurred_at"]))):
                raise ValueError("publication_retry_event_history_changed")
            proof.append(list(values))
        value={"columns":columns,"rows":proof}
        if len(encoded(value))>32*1024*1024:
            raise ValueError("publication_retry_event_history_bound_exceeded")
        return value

    def _retry_archive_proof49(self, session, job, *, recovery_mode=None):
        if recovery_mode is not None and (type(recovery_mode) is not str or recovery_mode != DIAGNOSTIC_GAP_RECOVERY_MODE):
            raise ValueError("publication_retry_recovery_mode_invalid")
        if self.archive.fenced(job.run_id):
            raise ValueError("publication_bundle_fence_requires_readonly_reconciliation")
        rows=list(session.scalars(select(Event).where(Event.run_id==job.run_id).order_by(Event.version).limit(4001)))
        if not rows or len(rows)>4000:
            raise ValueError("publication_retry_event_history_unknown")
        generations=set(); history=[]; failures={}; gaps=[]; diagnostic_refs=[]
        for position,row in enumerate(rows,1):
            body=json.loads(row.payload_json)
            expected_fields={"schema_version","run_id","request_id","version","generation",
                "state","reason_code","bundle_hash","occurred_at"}
            if type(body) is dict and body.get("schema_version")=="publication-bundle-state-event.v2":
                expected_fields.add("transition_phase")
                if "failure_diagnostic" in body:expected_fields.add("failure_diagnostic")
            if (type(body) is not dict or set(body)!=expected_fields
                    or sha(row.payload_json.encode())!=row.event_id or encoded(body).decode()!=row.payload_json
                    or body.get("schema_version") not in {"publication-bundle-state-event.v1","publication-bundle-state-event.v2"}
                    or body.get("run_id")!=job.run_id or body.get("request_id")!=job.request_id
                    or type(body.get("generation")) is not int or type(body.get("version")) is not int
                    or body.get("generation")!=row.generation or body.get("version")!=row.version
                    or body.get("state")!=row.state or row.request_id!=job.request_id
                    or body.get("reason_code")!=row.reason_code or body.get("bundle_hash")!=row.bundle_hash
                    or type(row.version) is not int or row.version!=position
                    or type(row.generation) is not int or not 0<=row.generation<=job.generation):
                raise ValueError("publication_retry_event_history_changed")
            phase=body.get("transition_phase")
            if body["schema_version"]=="publication-bundle-state-event.v2" and (type(phase) is not str
                    or phase not in EVENT_PHASES49):
                raise ValueError("publication_job_transition_phase_unknown")
            diagnostic=body.get("failure_diagnostic")
            if diagnostic is not None:
                _failure_metadata49(diagnostic)
                if phase!="unconfirmed" or row.state!="unconfirmed":
                    raise ValueError("publication_retry_diagnostic_metadata_invalid")
                if diagnostic["status"]=="unavailable":
                    if recovery_mode is None:
                        raise ValueError("publication_retry_diagnostic_unavailable")
                    if (row.generation in failures
                            or any(gap["generation"] == row.generation for gap in gaps)):
                        raise ValueError("publication_retry_event_history_changed")
                    gaps.append({"generation":row.generation,"event_id":row.event_id,
                                 "status":"unavailable"})
                else:
                    loaded=self.archive.load_diagnostic(BundleClaim(job.run_id,job.request_id,"diagnostic-read",row.generation))
                    if loaded is None or loaded[1]!=diagnostic["sha256"]:
                        raise ValueError("publication_retry_diagnostic_changed")
                    if (row.generation in failures
                            or any(gap["generation"] == row.generation for gap in gaps)):
                        raise ValueError("publication_retry_event_history_changed")
                    failures[row.generation]=(row,loaded)
                    diagnostic_refs.append({"generation":row.generation,"event_id":row.event_id,
                                            "sha256":loaded[1]})
            if (row.generation>0 and row.state=="queued" and phase!="retry_queued"):
                generations.add(row.generation)
            history.append(row.event_id)
        last=rows[-1]
        if last.version!=job.state_version or last.generation!=job.generation or last.state!="unconfirmed":
            raise ValueError("publication_retry_event_head_changed")
        if recovery_mode == DIAGNOSTIC_GAP_RECOVERY_MODE:
            if not gaps:
                raise ValueError("publication_retry_diagnostic_gap_required")
            native_history=self._retry_gap_history_native49(session,job.run_id,job.state_version)
            native_payloads=[values[native_history["columns"].index("payload_json")] for values in native_history["rows"]]
            if native_payloads!=[row.payload_json for row in rows]:
                raise ValueError("publication_retry_event_history_changed")
            # Missing diagnostics never establish a failure phase. Only the
            # complete existing all-owned archive proof permits this new mode.
            ownership = self._retry_owned_archive_proof51(session, job.run_id)
            return {"kind":"diagnostic_gap_all_owned_archives", "history":history,
                    "diagnostics":[], "diagnostic_gaps":gaps,
                    "diagnostic_refs":diagnostic_refs, "archive_ownership":ownership,
                    "native_history":native_history}
        proofs=[];complete=True
        for generation in sorted(generations):
            failure=failures.get(generation)
            if failure is None:
                complete=False;break
            _,(body,digest)=failure;head=body.get("head",{});identity=body.get("claim",{})
            failed=[row for row in rows if row.generation==generation and row.state=="unconfirmed"]
            if (type(body) is not dict or type(head) is not dict or type(identity) is not dict
                    or body.get("schema_version")!="publication-worker-private-failure.v1"
                    or body.get("phase") not in {"produce","preparation_verify"}
                    or body.get("fenced_at_error") is not False
                    or identity.get("run_id")!=job.run_id or identity.get("request_id")!=job.request_id
                    or type(identity.get("generation")) is not int or identity.get("generation")!=generation
                    or type(identity.get("owner")) is not str or not identity.get("owner")
                    or head.get("owner")!=identity.get("owner") or head.get("run_id")!=job.run_id
                    or head.get("request_id")!=job.request_id or type(head.get("generation")) is not int
                    or head.get("generation")!=generation or type(head.get("state_version")) is not int
                    or head.get("state")!="queued" or head.get("bundle_hash") is not None
                    or head.get("publication_hash")!=job.publication_hash
                    or head.get("selection_native_hash")!=job.selection_native_hash
                    or len(failed)!=1 or head.get("state_version")!=failed[0].version-1):
                complete=False;break
            proofs.append(digest)
        if complete and generations and job.generation in generations:
            return {"kind":"trusted_all_generations_before_archive","history":history,"diagnostics":proofs}
        if self.archive.original_archive_empty():
            return {"kind":"legacy_global_archive_empty","history":history,"diagnostics":[]}
        ownership = self._retry_owned_archive_proof51(session, job.run_id)
        return {"kind":"legacy_other_confirmed_run_archives", "history":history,
            "diagnostics":[], "archive_ownership":ownership}

    def recovery_status(self, run_id: str) -> dict:
        """Pure RO explanation, no claim, fence removal, archive scan or retry."""
        with self._ro() as session:
            job=session.get(Job,run_id,populate_existing=True)
            if job is None:raise KeyError(run_id)
            result={"run_id":run_id,"request_id":job.request_id,"generation":job.generation,
                "state_version":job.state_version,"eligible":False,"reason_code":None}
            try:
                if self.archive.fenced(run_id):
                    raise ValueError("publication_bundle_fence_requires_readonly_reconciliation")
                job=self._retry_head49(session,run_id,expected_request=job.request_id,
                    expected_generation=job.generation,expected_version=job.state_version)
                result["archive_proof"]=self._retry_archive_proof49(session,job)["kind"]
                result["eligible"]=True
            except ValueError as error:
                result["reason_code"]=str(error)
            return result

    def _preparation_queue_archive_proof83(self, session, job, *, permit):
        # Only the strict owned caller inside the normal opaque Source scope
        # can select this queue purpose; this helper issues no permission.
        self.preparer.verify_retry_native49(session,run_id=job.run_id,permit=permit)
        self._preparation_authorization_head79(session,job.run_id,expected_request=job.request_id,
            expected_generation=job.generation,expected_version=job.state_version)
        recovery_mode=DIAGNOSTIC_GAP_RECOVERY_MODE
        if recovery_mode is not None and (type(recovery_mode) is not str or recovery_mode != DIAGNOSTIC_GAP_RECOVERY_MODE):
            raise ValueError("publication_retry_recovery_mode_invalid")
        if self.archive.fenced(job.run_id):
            raise ValueError("publication_bundle_fence_requires_readonly_reconciliation")
        rows=list(session.scalars(select(Event).where(Event.run_id==job.run_id).order_by(Event.version).limit(4001)))
        if not rows or len(rows)>4000:
            raise ValueError("publication_retry_event_history_unknown")
        generations=set(); history=[]; failures={}; gaps=[]; diagnostic_refs=[]
        for position,row in enumerate(rows,1):
            body=json.loads(row.payload_json)
            expected_fields={"schema_version","run_id","request_id","version","generation",
                "state","reason_code","bundle_hash","occurred_at"}
            if type(body) is dict and body.get("schema_version")=="publication-bundle-state-event.v2":
                expected_fields.add("transition_phase")
                if "failure_diagnostic" in body:expected_fields.add("failure_diagnostic")
            if (type(body) is not dict or set(body)!=expected_fields
                    or sha(row.payload_json.encode())!=row.event_id or encoded(body).decode()!=row.payload_json
                    or body.get("schema_version") not in {"publication-bundle-state-event.v1","publication-bundle-state-event.v2"}
                    or body.get("run_id")!=job.run_id or body.get("request_id")!=job.request_id
                    or type(body.get("generation")) is not int or type(body.get("version")) is not int
                    or body.get("generation")!=row.generation or body.get("version")!=row.version
                    or body.get("state")!=row.state or row.request_id!=job.request_id
                    or body.get("reason_code")!=row.reason_code or body.get("bundle_hash")!=row.bundle_hash
                    or type(row.version) is not int or row.version!=position
                    or type(row.generation) is not int or not 0<=row.generation<=job.generation):
                raise ValueError("publication_retry_event_history_changed")
            phase=body.get("transition_phase")
            if body["schema_version"]=="publication-bundle-state-event.v2" and (type(phase) is not str
                    or phase not in EVENT_PHASES49):
                raise ValueError("publication_job_transition_phase_unknown")
            diagnostic=body.get("failure_diagnostic")
            if diagnostic is not None:
                _failure_metadata49(diagnostic)
                if phase!="unconfirmed" or row.state!="unconfirmed":
                    raise ValueError("publication_retry_diagnostic_metadata_invalid")
                if diagnostic["status"]=="unavailable":
                    if recovery_mode is None:
                        raise ValueError("publication_retry_diagnostic_unavailable")
                    if (row.generation in failures
                            or any(gap["generation"] == row.generation for gap in gaps)):
                        raise ValueError("publication_retry_event_history_changed")
                    gaps.append({"generation":row.generation,"event_id":row.event_id,
                                 "status":"unavailable"})
                else:
                    loaded=self.archive.load_diagnostic(BundleClaim(job.run_id,job.request_id,"diagnostic-read",row.generation))
                    if loaded is None or loaded[1]!=diagnostic["sha256"]:
                        raise ValueError("publication_retry_diagnostic_changed")
                    if (row.generation in failures
                            or any(gap["generation"] == row.generation for gap in gaps)):
                        raise ValueError("publication_retry_event_history_changed")
                    failures[row.generation]=(row,loaded)
                    diagnostic_refs.append({"generation":row.generation,"event_id":row.event_id,
                                            "sha256":loaded[1]})
            if (row.generation>0 and row.state=="queued" and phase!="retry_queued"):
                generations.add(row.generation)
            history.append(row.event_id)
        last=rows[-1]
        if last.version!=job.state_version or last.generation!=job.generation or last.state!="unconfirmed":
            raise ValueError("publication_retry_event_head_changed")
        if recovery_mode == DIAGNOSTIC_GAP_RECOVERY_MODE:
            if not gaps:
                raise ValueError("publication_retry_diagnostic_gap_required")
            native_history=self._retry_gap_history_native49(session,job.run_id,job.state_version)
            native_payloads=[values[native_history["columns"].index("payload_json")] for values in native_history["rows"]]
            if native_payloads!=[row.payload_json for row in rows]:
                raise ValueError("publication_retry_event_history_changed")
            # Every archive owner and confirmed report snapshot is verified.
            # Other material remains outside this queue-only read purpose.
            ownership = self._preparation_queue_archive_ownership83(session, job.run_id)
            return {"kind":"diagnostic_gap_all_owned_archives", "history":history,
                    "diagnostics":[], "diagnostic_gaps":gaps,
                    "diagnostic_refs":diagnostic_refs, "archive_ownership":ownership,
                    "native_history":native_history}
    def _preparation_queue_archive_ownership83(self, session, run_id):
        ownership=self._known_cancel_archive_ownership69(session,run_id)
        owners=[{**owner,"snapshot_identity":(*owner["snapshot_identity"][:3],
            owner["snapshot_identity"][3].isoformat(),*owner["snapshot_identity"][4:])}
            for owner in ownership["owners"]]
        return {**ownership,"owners":owners,"scope":PREPARATION_QUEUE_RECOVERY_MODE83}

    def retry_unconfirmed(self, run_id: str, *, expected_request: str,
                          expected_generation: int, expected_version: int,
                          recovery_mode=None, expected_reason=None,
                          verify_owner_native=None, command_journal=None) -> dict:
        """Explicit internal managed retry; never called by a GET or user ACL.

        No original, model, Saved or ACK is produced here. All generations of
        existing saved rows, unknown fences and cancellation remain barriers.
        """
        if (type(expected_request) is not str or not 1<=len(expected_request)<=128
                or type(expected_generation) is not int or expected_generation<0
                or type(expected_version) is not int or expected_version<1):
            raise ValueError("publication_retry_explicit_expected_identity_required")
        if recovery_mode is not None and (type(recovery_mode) is not str or recovery_mode != DIAGNOSTIC_GAP_RECOVERY_MODE):
            raise ValueError("publication_retry_recovery_mode_invalid")
        owned = any(value is not None for value in (expected_reason,verify_owner_native,command_journal))
        if owned and (expected_reason!="frozen_io_undeclared_authorization_resource"
                or recovery_mode!=DIAGNOSTIC_GAP_RECOVERY_MODE or not callable(verify_owner_native)
                or not callable(command_journal)):
            raise ValueError("publication_preparation_recovery_contract_required")
        qualifier=getattr(self.preparer,"reader_abi_qualification",None) if owned else None
        if owned and not callable(qualifier):
            raise ValueError("publication_recovery_current_reader_required")
        authority=getattr(self.preparer,"retry_authority49",None)
        if not callable(authority):
            raise ValueError("publication_retry_normal_authority_required")
        if self.archive.fenced(run_id):
            raise ValueError("publication_bundle_fence_requires_readonly_reconciliation")
        with self._ro() as session:
            job=self._retry_head49(session,run_id,expected_request=expected_request,
                expected_generation=expected_generation,expected_version=expected_version)
            if owned:
                owned_head=self._preparation_authorization_head79(session,run_id,expected_request=expected_request,
                    expected_generation=expected_generation,expected_version=expected_version)
                owner_identity=verify_owner_native(session,run_id)
                if type(owner_identity) is not dict or set(owner_identity)!={"owner_id","root_run_id","lineage","owner_source"}:
                    raise PermissionError("publication_recovery_normal_owner_required")
                abi=qualifier(session,run_id=run_id)
                if type(abi) is not str or abi!=CURRENT_READER_ABI:
                    raise ValueError("publication_recovery_current_reader_required")
            proof=None if owned else self._retry_archive_proof49(session,job,recovery_mode=recovery_mode)
            scope=authority(session,run_id=run_id)
            with scope as permit:
                self.preparer.verify_retry_native49(session,run_id=run_id,permit=permit)
                self.preparer.verify_retry_sources49(permit)
                if owned:
                    proof=self._preparation_queue_archive_proof83(session,job,permit=permit)
                    reply={"schema_version":"research-preparation-recovery-reply.v2","run_id":run_id,
                        "request_id":expected_request,"generation":expected_generation+1,
                        "state_version":expected_version+1,"state":"queued","report_confirmed":False,
                        "source_abi":CURRENT_READER_ABI,"automatic_retry":False,
                        "recovery_mode":PREPARATION_QUEUE_RECOVERY_MODE83,
                        "event_history_sha256":sha(encoded(proof["native_history"])),
                        "archive_ownership_sha256":sha(encoded(proof["archive_ownership"]))}
                # The opaque normal source scope remains live until independent
                # readback; a changed source leaves this new generation fenced.
                claim=BundleClaim(run_id,expected_request,"internal-explicit-retry",expected_generation+1)
                queued_native=None
                def write(writer):
                    nonlocal queued_native
                    self._retry_head49(writer,run_id,expected_request=expected_request,
                        expected_generation=expected_generation,expected_version=expected_version)
                    if owned:
                        if self._preparation_authorization_head79(writer,run_id,expected_request=expected_request,
                                expected_generation=expected_generation,expected_version=expected_version)[1:]!=owned_head[1:]:
                            raise ValueError("publication_preparation_recovery_native_changed")
                        if verify_owner_native(writer,run_id)!=owner_identity:
                            raise PermissionError("publication_recovery_owner_scope_changed")
                        if self._retry_gap_history_native49(writer,run_id,expected_version)!=proof["native_history"]:
                            raise ValueError("publication_retry_event_history_changed")
                    self.preparer.verify_retry_native49(writer,run_id=run_id,permit=permit)
                    if proof["kind"] in {"legacy_other_confirmed_run_archives",
                                         "diagnostic_gap_all_owned_archives"}:
                        self._retry_owner_native_unchanged51(writer, proof["archive_ownership"])
                    changed=writer.execute(update(Job).where(Job.run_id==run_id,Job.request_id==expected_request,
                        Job.generation==expected_generation,Job.state_version==expected_version,
                        Job.state=="unconfirmed",Job.owner.is_(None),Job.lease_expires_at.is_(None),
                        Job.bundle_hash.is_(None),Job.confirmed_at.is_(None),
                        *([Job.reason_code==expected_reason,Job.publication_id==job.publication_id,
                            Job.publication_hash==job.publication_hash,Job.publication_schema==job.publication_schema,
                            Job.publication_created_at==job.publication_created_at,
                            Job.selection_native_json==job.selection_native_json,Job.selection_native_hash==job.selection_native_hash,
                            Job.reader_version==job.reader_version,Job.event_id==job.event_id,Job.artifact_id==job.artifact_id]
                          if owned else [])).execution_options(synchronize_session=False).values(
                            generation=expected_generation+1,state="queued",reason_code=None,
                            available_at=self.clock(),updated_at=self.clock()))
                    if changed.rowcount!=1:raise PermissionError("publication_retry_generation_CAS_lost")
                    if owned:
                        command_journal(writer,reply,write=True)
                        queued_native=self._confirmation_rows74(writer,Job,
                            "SELECT * FROM research_publication_bundle_jobs WHERE run_id=?",(run_id,))
                        index=queued_native["columns"].index("state_version")
                        queued_native["rows"][0][index]=("integer",expected_version+1)
                fence=self._attempt(claim,"retry_queued","0"*64,{},write)
                with self._ro() as reader:
                    current=reader.get(Job,run_id,populate_existing=True)
                    if (current is None or current.request_id!=expected_request or current.state!="queued"
                            or current.generation!=expected_generation+1 or current.state_version!=expected_version+1
                            or current.owner is not None or current.bundle_hash is not None):
                        raise ValueError("publication_retry_independent_readback_changed")
                    if owned and self._confirmation_rows74(reader,Job,
                            "SELECT * FROM research_publication_bundle_jobs WHERE run_id=?",(run_id,))!=queued_native:
                        raise ValueError("publication_retry_independent_readback_changed")
                    self._publication(reader,current)
                    for table in ("research_publication_bundles","research_publication_bundle_confirmations"):
                        if reader.connection().exec_driver_sql("SELECT * FROM "+table+" WHERE run_id=?",(run_id,)).fetchall():
                            raise ValueError("publication_retry_saved_or_confirmed_requires_review")
                    self.preparer.verify_retry_native49(reader,run_id=run_id,permit=permit)
                    if owned:
                        if verify_owner_native(reader,run_id)!=owner_identity:
                            raise PermissionError("publication_recovery_owner_scope_changed")
                        command_journal(reader,reply,write=False)
                        exit_abi=qualifier(reader,run_id=run_id)
                        if type(exit_abi) is not str or exit_abi!=CURRENT_READER_ABI:
                            raise ValueError("publication_recovery_current_reader_required")
                self.preparer.verify_retry_sources49(permit)
                # Compare the same history/diagnostic hashes before releasing
                # the fence. The newly appended retry event is not a producer.
                with self._ro() as reader:
                    history=list(reader.scalars(select(Event.event_id).where(Event.run_id==run_id,
                        Event.version<=expected_version).order_by(Event.version)))
                    if history!=proof["history"]:raise ValueError("publication_retry_event_history_changed")
                    if owned:
                        if verify_owner_native(reader,run_id)!=owner_identity:
                            raise PermissionError("publication_recovery_owner_scope_changed")
                        command_journal(reader,reply,write=False)
                    if proof["kind"]=="diagnostic_gap_all_owned_archives":
                        native_history=self._retry_gap_history_native49(reader,run_id,expected_version)
                        if native_history!=proof["native_history"]:
                            raise ValueError("publication_retry_event_history_changed")
                if proof["kind"]=="legacy_global_archive_empty" and not self.archive.original_archive_empty():
                    raise ValueError("publication_retry_archive_ownership_unknown")
                if proof["kind"] in {"legacy_other_confirmed_run_archives",
                                     "diagnostic_gap_all_owned_archives"}:
                    with self._ro() as reader:
                        current_ownership = (self._preparation_queue_archive_ownership83(reader,run_id)
                            if owned else self._retry_owned_archive_proof51(reader, run_id))
                    if current_ownership != proof["archive_ownership"]:
                        raise ValueError("publication_retry_archive_ownership_changed")
                if proof["kind"] == "diagnostic_gap_all_owned_archives":
                    for item in proof["diagnostic_refs"]:
                        loaded=self.archive.load_diagnostic(BundleClaim(
                            run_id,expected_request,"diagnostic-read",item["generation"]))
                        if loaded is None or loaded[1]!=item["sha256"]:
                            raise ValueError("publication_retry_diagnostic_changed")
                if proof["kind"]=="trusted_all_generations_before_archive":
                    current_hashes=[]
                    for generation in range(1,expected_generation+1):
                        loaded=self.archive.load_diagnostic(BundleClaim(run_id,expected_request,"diagnostic-read",generation))
                        if loaded is not None:current_hashes.append(loaded[1])
                    if current_hashes!=proof["diagnostics"]:
                        raise ValueError("publication_retry_diagnostic_changed")
                if owned:
                    with self._ro() as reader:
                        if verify_owner_native(reader,run_id)!=owner_identity:
                            raise PermissionError("publication_recovery_owner_scope_changed")
                        command_journal(reader,reply,write=False)
                    self.preparer.verify_retry_sources49(permit)
                self.archive.release(run_id,fence)
        if owned:return reply
        result={"run_id":run_id,"request_id":expected_request,"generation":expected_generation+1,
            "state_version":expected_version+1,"state":"queued"}
        if recovery_mode == DIAGNOSTIC_GAP_RECOVERY_MODE:
            result.update(recovery_mode=DIAGNOSTIC_GAP_RECOVERY_MODE,
                diagnostic_gap_generations=[item["generation"] for item in proof["diagnostic_gaps"]],
                diagnostic_gap_event_ids=[item["event_id"] for item in proof["diagnostic_gaps"]],
                event_history_sha256=sha(encoded(proof["native_history"])),
                archive_ownership_sha256=sha(encoded(proof["archive_ownership"])))
        return result

    def enqueue_existing(self,run_id: str) -> str:
        """Explicit internal backfill. A GET must never call this method."""
        if self.archive.fenced(run_id):
            raise ValueError("publication_bundle_fence_requires_readonly_reconciliation")
        with self._ro() as session:
            prior=session.get(Job,run_id,populate_existing=True)
            if prior is not None:
                self._publication(session,prior)
                return prior.request_id
        claim=BundleClaim(run_id,"backfill:"+sha(run_id.encode()),"internal-backfill",0)
        result=[]
        def write(session):
            result.append(enqueue_in_publication_transaction(session,run_id,now=self.clock()))
        fence=self._attempt(claim,"enqueued","0"*64,{},write)
        with self._ro() as session:
            job=session.get(Job,run_id,populate_existing=True)
            if job is None or job.request_id!=result[0] or job.state!="queued":
                raise ValueError("publication_job_enqueue_readback_changed")
            self._publication(session,job)
        self.archive.release(run_id,fence)
        return result[0]

    def cancel_preparation(self,claim: BundleClaim, *, reason_code="publication_bundle_preparation_cancelled") -> None:
        """Cancel this internal job generation, preserving the published Run."""
        if type(reason_code) is not str or reason_code not in CANCEL_REASONS:
            raise ValueError("publication_bundle_cancel_reason_invalid")
        def write(session):
            job=session.get(Job,claim.run_id,populate_existing=True)
            if (job is None or job.request_id!=claim.request_id or job.owner!=claim.owner
                    or job.generation!=claim.generation or job.state not in {"queued","prepared"}):
                raise PermissionError("publication_bundle_cancel_claim_lost")
            self._publication(session,job)
            changed=session.execute(update(Job).where(Job.run_id==claim.run_id,
                Job.request_id==claim.request_id,Job.generation==claim.generation,
                Job.owner==claim.owner,Job.state.in_(["queued","prepared"]),
                Job.state_version==job.state_version).values(state="cancelled",
                    generation=claim.generation+1,owner=None,lease_expires_at=None,
                    reason_code=reason_code,updated_at=self.clock()))
            if changed.rowcount!=1:
                raise PermissionError("publication_bundle_cancel_CAS_lost")
            session.expire(job); session.refresh(job)
        fence=self._attempt(claim,"cancelled","0"*64,{},write)
        with self._ro() as session:
            job=session.get(Job,claim.run_id,populate_existing=True)
            if job is None or job.state!="cancelled" or job.generation!=claim.generation+1:
                raise ValueError("publication_bundle_cancel_readback_changed")
        self.archive.release(claim.run_id,fence)

    def _known_cancel_head(self, session, run_id, *, expected_request,
                           expected_generation, expected_version):
        job=session.get(Job,run_id,populate_existing=True)
        if (job is None or job.request_id!=expected_request or job.generation!=expected_generation
                or job.state_version!=expected_version or job.state!="cancelled"
                or job.owner is not None or job.lease_expires_at is not None
                or job.bundle_hash is not None or job.confirmed_at is not None
                or type(job.reason_code) is not str or job.reason_code not in CANCEL_REASONS):
            raise ValueError("publication_recovery_exact_known_cancel_required")
        self._publication(session,job)
        for table in ("research_publication_bundles","research_publication_bundle_confirmations"):
            if session.connection().exec_driver_sql("SELECT * FROM "+table+" WHERE run_id=?",(run_id,)).fetchall():
                raise ValueError("publication_retry_saved_or_confirmed_requires_review")
        return job

    def _known_cancel_history(self, session, job):
        native=self._retry_gap_history_native49(session,job.run_id,job.state_version)
        payload_index=native["columns"].index("payload_json")
        diagnostics=[]; bodies=[]; diagnostic_generations=set()
        for values in native["rows"]:
            body=json.loads(values[payload_index])
            fields={"schema_version","run_id","request_id","version","generation",
                "state","reason_code","bundle_hash","occurred_at"}
            if body.get("schema_version")=="publication-bundle-state-event.v2":
                fields.add("transition_phase")
                if "failure_diagnostic" in body:fields.add("failure_diagnostic")
            if (set(body)!=fields or body.get("schema_version") not in {
                    "publication-bundle-state-event.v1","publication-bundle-state-event.v2"}
                    or body.get("request_id")!=job.request_id
                    or type(body.get("generation")) is not int or not 0<=body["generation"]<=job.generation
                    or body.get("schema_version")=="publication-bundle-state-event.v2" and (
                        type(body.get("transition_phase")) is not str
                        or body["transition_phase"] not in EVENT_PHASES49)):
                raise ValueError("publication_retry_event_history_changed")
            phase=body.get("transition_phase")
            states={"claimed":"queued","lease_renewed":"queued","enqueued":"queued",
                "retry_queued":"queued","prepared":"prepared","confirmed":"confirmed",
                "cancelled":"cancelled","unconfirmed":"unconfirmed","revoked":"revoked"}
            if body["schema_version"]=="publication-bundle-state-event.v1":
                if bodies or body["state"]!="queued" or body["generation"]!=0:
                    raise ValueError("publication_recovery_known_history_required")
            elif not bodies:
                if (phase,body["state"],body["generation"]) not in {
                        ("enqueued","queued",0),("claimed","queued",1)}:
                    raise ValueError("publication_recovery_known_history_required")
            else:
                previous=bodies[-1]
                advance=phase in {"claimed","retry_queued","cancelled","revoked"}
                allowed={"claimed":{"queued"},"lease_renewed":{"queued"},
                    "retry_queued":{"unconfirmed","cancelled"},"prepared":{"queued"},
                    "confirmed":{"prepared"},"cancelled":{"queued","prepared"},
                    "unconfirmed":{"queued","prepared"},"revoked":{"confirmed"}}
                if (phase not in allowed or body["state"]!=states[phase]
                        or previous["state"] not in allowed[phase]
                        or body["generation"]!=previous["generation"]+int(advance)):
                    raise ValueError("publication_recovery_known_history_required")
            diagnostic=body.get("failure_diagnostic")
            if "failure_diagnostic" in body:
                _failure_metadata49(diagnostic)
                if body.get("transition_phase")!="unconfirmed" or body["state"]!="unconfirmed":
                    raise ValueError("publication_retry_diagnostic_metadata_invalid")
                if body["generation"] in diagnostic_generations:
                    raise ValueError("publication_retry_event_history_changed")
                diagnostic_generations.add(body["generation"])
                if diagnostic["status"]=="saved":
                    loaded=self.archive.load_diagnostic(BundleClaim(
                        job.run_id,job.request_id,"diagnostic-read",body["generation"]))
                    if loaded is None or loaded[1]!=diagnostic["sha256"]:
                        raise ValueError("publication_retry_diagnostic_changed")
                    diagnostics.append({"generation":body["generation"],"sha256":loaded[1]})
            bodies.append(body)
        last=bodies[-1]
        if (len(bodies)<2 or last["schema_version"]!="publication-bundle-state-event.v2"
                or last.get("transition_phase")!="cancelled" or last["state"]!="cancelled"
                or last["generation"]!=job.generation or last["version"]!=job.state_version
                or last["reason_code"]!=job.reason_code or last["bundle_hash"] is not None
                or bodies[-2]["state"]!="queued" or bodies[-2]["bundle_hash"] is not None
                or bodies[-2]["generation"]+1!=job.generation):
            raise ValueError("publication_recovery_cancel_before_archive_required")
        return {"native":native,"diagnostics":diagnostics}

    def _owned_preparation_gap_head70(self, session, run_id, *, expected_request,
                                       expected_generation, expected_version):
        job=self._retry_head49(session,run_id,expected_request=expected_request,
            expected_generation=expected_generation,expected_version=expected_version)
        if (type(job.reason_code) is not str
                or job.reason_code!="frozen_io_root_data_fallback"):
            raise ValueError("publication_recovery_exact_owned_preparation_gap_required")
        return job

    def _owned_preparation_gap_history70(self, session, job):
        """Queue-only current diagnostic gap; no claimed failure phase or read grant."""
        native=self._retry_gap_history_native49(session,job.run_id,job.state_version)
        index=native["columns"].index("payload_json")
        bodies=[];diagnostics=[];diagnostic_generations=set();gaps=[]
        unknown_commit_codes={"publication_bundle_"+phase+"_commit_unconfirmed"
            for phase in EVENT_PHASES49} | {
                "publication_bundle_commit_unconfirmed","publication_renew_commit_unconfirmed",
                "publication_bundle_fence_requires_readonly_reconciliation",
                "publication_retry_commit_unknown"}
        for values in native["rows"]:
            body=json.loads(values[index])
            fields={"schema_version","run_id","request_id","version","generation",
                "state","reason_code","bundle_hash","occurred_at"}
            if body.get("schema_version")=="publication-bundle-state-event.v2":
                fields.add("transition_phase")
                if "failure_diagnostic" in body:fields.add("failure_diagnostic")
            if (set(body)!=fields or body.get("schema_version") not in {
                    "publication-bundle-state-event.v1","publication-bundle-state-event.v2"}
                    or body.get("request_id")!=job.request_id
                    or type(body.get("generation")) is not int or not 0<=body["generation"]<=job.generation
                    or body.get("bundle_hash") is not None
                    or body.get("state") not in {"queued","unconfirmed","cancelled"}
                    or body.get("reason_code") in unknown_commit_codes
                    or body.get("schema_version")=="publication-bundle-state-event.v2" and (
                        type(body.get("transition_phase")) is not str
                        or body["transition_phase"] not in EVENT_PHASES49)):
                raise ValueError("publication_recovery_owned_gap_history_required")
            phase=body.get("transition_phase")
            states={"claimed":"queued","lease_renewed":"queued","enqueued":"queued",
                "retry_queued":"queued","cancelled":"cancelled","unconfirmed":"unconfirmed"}
            if body["schema_version"]=="publication-bundle-state-event.v1":
                if bodies or body["state"]!="queued" or body["generation"]!=0:
                    raise ValueError("publication_recovery_owned_gap_history_required")
            elif not bodies:
                if (phase,body["state"],body["generation"]) not in {
                        ("enqueued","queued",0),("claimed","queued",1)}:
                    raise ValueError("publication_recovery_owned_gap_history_required")
            else:
                previous=bodies[-1]
                advance=phase in {"claimed","retry_queued","cancelled"}
                allowed={"claimed":{"queued"},"lease_renewed":{"queued"},
                    "retry_queued":{"unconfirmed","cancelled"},
                    "cancelled":{"queued"},"unconfirmed":{"queued"}}
                if (phase not in allowed or body["state"]!=states[phase]
                        or previous["state"] not in allowed[phase]
                        or body["generation"]!=previous["generation"]+int(advance)):
                    raise ValueError("publication_recovery_owned_gap_history_required")
            diagnostic=body.get("failure_diagnostic")
            if "failure_diagnostic" in body:
                _failure_metadata49(diagnostic)
                if (phase!="unconfirmed" or body["state"]!="unconfirmed"
                        or body["generation"] in diagnostic_generations):
                    raise ValueError("publication_retry_diagnostic_metadata_invalid")
                diagnostic_generations.add(body["generation"])
                if diagnostic["status"]=="saved":
                    loaded=self.archive.load_diagnostic(BundleClaim(
                        job.run_id,job.request_id,"diagnostic-read",body["generation"]))
                    if loaded is None or loaded[1]!=diagnostic["sha256"]:
                        raise ValueError("publication_retry_diagnostic_changed")
                    failure=loaded[0]
                    if type(failure) is not dict:
                        raise ValueError("publication_recovery_saved_diagnostic_before_archive_required")
                    head=failure.get("head");claim=failure.get("claim")
                    if type(head) is not dict or type(claim) is not dict:
                        raise ValueError("publication_recovery_saved_diagnostic_before_archive_required")
                    error_type=failure.get("exception_type")
                    message=failure.get("exception_message")
                    if (type(message) is not str or type(error_type) is not str
                            or error_type not in {"builtins.ValueError","builtins.TypeError",
                                "builtins.PermissionError","builtins.TimeoutError","builtins.RuntimeError",
                                "builtins.OSError","builtins.MemoryError","builtins.Exception"}):
                        raise ValueError("publication_recovery_saved_diagnostic_before_archive_required")
                    lines=message.splitlines()
                    reason=(lines[0] if error_type=="builtins.ValueError" and lines
                        and re.fullmatch(r"[a-z0-9_]{1,160}",lines[0]) else "publication_bundle_preparation_failed")
                    if (failure.get("schema_version")!="publication-worker-private-failure.v1"
                            or failure.get("phase") not in {"produce","preparation_verify"}
                            or failure.get("fenced_at_error") is not False
                            or claim.get("run_id")!=job.run_id or claim.get("request_id")!=job.request_id
                            or type(claim.get("generation")) is not int or claim["generation"]!=body["generation"]
                            or type(claim.get("owner")) is not str or not claim["owner"]
                            or head.get("owner")!=claim["owner"] or head.get("run_id")!=job.run_id
                            or head.get("request_id")!=job.request_id
                            or type(head.get("generation")) is not int or head["generation"]!=body["generation"]
                            or type(head.get("state_version")) is not int or head["state_version"]!=body["version"]-1
                            or head.get("state")!="queued" or head.get("bundle_hash") is not None
                            or head.get("publication_hash")!=job.publication_hash
                            or head.get("selection_native_hash")!=job.selection_native_hash
                            or reason!=body["reason_code"]):
                        raise ValueError("publication_recovery_saved_diagnostic_before_archive_required")
                    diagnostics.append({"generation":body["generation"],"sha256":loaded[1]})
                else:gaps.append({"generation":body["generation"],"status":"unavailable"})
            elif phase=="unconfirmed":
                gaps.append({"generation":body["generation"],"status":"missing"})
            bodies.append(body)
        last=bodies[-1]
        if (len(bodies)<2 or last["schema_version"]!="publication-bundle-state-event.v2"
                or last.get("transition_phase")!="unconfirmed" or last["state"]!="unconfirmed"
                or last["generation"]!=job.generation or last["version"]!=job.state_version
                or last["reason_code"]!="frozen_io_root_data_fallback"
                or last["reason_code"]!=job.reason_code
                or last.get("failure_diagnostic")!={"status":"unavailable","sha256":None}
                or bodies[-2]["state"]!="queued" or bodies[-2]["generation"]!=job.generation):
            raise ValueError("publication_recovery_exact_owned_preparation_gap_required")
        return {"kind":"owned_queue_only_diagnostic_gap70","authoritative_failure_phase":"UNKNOWN",
            "current_diagnostic_status":"unavailable","native":native,
            "diagnostics":diagnostics,"diagnostic_gaps":gaps}

    def known_cancel_recovery_status(self, run_id: str) -> dict:
        """Metadata prechecks only; never scan archives, enqueue, or issue a permit."""
        def snapshot(session, read_abi=None):
            job=session.get(Job,run_id,populate_existing=True)
            if job is None:raise KeyError(run_id)
            self._publication(session,job)
            value={"schema_version":"research-report-recovery-status.v1","run_id":run_id,
                "request_id":job.request_id,"generation":job.generation,"state_version":job.state_version,
                "state":job.state,"reason_code":job.reason_code,"source_abi":None,
                "action":"not_applicable","proof_scope":"metadata_prechecks_only"}
            if self.archive.fenced(run_id):
                value.update(action="review_required",reason_code="publication_bundle_commit_unconfirmed")
            elif job.state=="cancelled":
                try:
                    self._known_cancel_head(session,run_id,expected_request=job.request_id,
                        expected_generation=job.generation,expected_version=job.state_version)
                    qualifier=getattr(self.preparer,"reader_abi_qualification",None)
                    abi=(read_abi(session,run_id=run_id) if read_abi is not None
                        else qualifier(session,run_id=run_id) if callable(qualifier) else None)
                    if type(abi) is str and abi==CURRENT_READER_ABI:
                        value.update(source_abi=abi,action="owner_request_required")
                    else:value["action"]="new_research_required"
                except (ValueError,PermissionError,TypeError,OSError):
                    value["action"]="review_required"
            elif job.state=="unconfirmed" and job.reason_code=="frozen_io_root_data_fallback":
                try:
                    self._owned_preparation_gap_head70(session,run_id,expected_request=job.request_id,
                        expected_generation=job.generation,expected_version=job.state_version)
                    self._owned_preparation_gap_history70(session,job)
                    qualifier=getattr(self.preparer,"reader_abi_qualification",None)
                    abi=(read_abi(session,run_id=run_id) if read_abi is not None
                        else qualifier(session,run_id=run_id) if callable(qualifier) else None)
                    if type(abi) is str and abi==CURRENT_READER_ABI:
                        value.update(source_abi=abi,action="owner_request_required")
                    else:value["action"]="review_required"
                except (ValueError,PermissionError,TypeError,OSError):
                    value["action"]="review_required"
            return value
        consumer=getattr(self.preparer,"consume_reader_abi_qualification",None)
        if not callable(consumer):
            # Preserve the original two complete qualifications for preparers
            # without the new server-only read lifecycle.
            with self._ro() as reader:entry=snapshot(reader)
            with self._ro() as reader:exit=snapshot(reader)
            if entry!=exit:raise ValueError("publication_bundle_serving_head_changed")
            return exit
        with self._ro() as reader:
            needed=False
            def defer_abi(session,*,run_id):
                nonlocal needed
                needed=True
                return None
            entry=snapshot(reader,read_abi=defer_abi)
            if not needed:
                with self._ro() as second:exit=snapshot(second)
                if entry!=exit:raise ValueError("publication_bundle_serving_head_changed")
                return exit
            def consume(abi,finish):
                qualified=dict(entry)
                if type(abi) is str and abi==CURRENT_READER_ABI:
                    qualified.update(source_abi=abi,action="owner_request_required")
                # Second Session gets only the ABI value, never a source permit.
                with self._ro() as second:
                    exit=snapshot(second,read_abi=lambda session,*,run_id:abi)
                    if qualified!=exit:raise ValueError("publication_bundle_serving_head_changed")
                    finish(second)
                return exit
            return consumer(reader,run_id=run_id,consume=consume)

    def retry_known_cancelled(self, run_id: str, *, expected_request: str,
                             expected_generation: int, expected_version: int,
                             verify_owner_native: Callable, command_journal: Callable | None = None) -> dict:
        """Restart same published Run preparation; no report/save/ACK permission.

        Current qualified ABI, actual owner/source issuer and two complete
        all-owned archive proofs are mandatory. Unconfirmed retry is unchanged.
        """
        observation = _ReportRecoveryObservations74(run_id, expected_request,
            expected_generation, expected_version, scope="retry_known_cancelled")
        observation_error = None
        try:
            if (type(expected_request) is not str or not expected_request
                    or type(expected_generation) is not int or expected_generation<1
                    or type(expected_version) is not int or expected_version<1
                    or not callable(verify_owner_native)
                    or command_journal is not None and not callable(command_journal)):
                raise ValueError("publication_retry_explicit_expected_identity_required")
            reply={"schema_version":"research-report-recovery-reply.v1","run_id":run_id,
                "request_id":expected_request,"generation":expected_generation+1,
                "state_version":expected_version+1,"state":"queued","report_confirmed":False,
                "source_abi":CURRENT_READER_ABI,"automatic_retry":False}
            authority=getattr(self.preparer,"retry_authority49",None)
            qualifier=getattr(self.preparer,"reader_abi_qualification",None)
            if not callable(authority) or not callable(qualifier):
                raise ValueError("publication_recovery_current_reader_required")
            if self.archive.fenced(run_id):
                raise ValueError("publication_bundle_fence_requires_readonly_reconciliation")
            observation.stage("head")
            with self._ro() as reader:
                job=self._known_cancel_head(reader,run_id,expected_request=expected_request,
                    expected_generation=expected_generation,expected_version=expected_version)
                observation.stage("owner")
                owner_identity=verify_owner_native(reader,run_id)
                if (type(owner_identity) is not tuple or len(owner_identity)!=3
                        or any(type(item) is not str or not item for item in owner_identity)):
                    raise PermissionError("publication_recovery_normal_owner_required")
                observation.stage("abi")
                abi=qualifier(reader,run_id=run_id)
                if type(abi) is not str or abi!=CURRENT_READER_ABI:
                    raise ValueError("publication_recovery_current_reader_required")
                observation.stage("history")
                history=self._known_cancel_history(reader,job)
                old_head=SimpleNamespace(run_id=run_id,request_id=expected_request,generation=expected_generation,
                    state_version=expected_version,reason_code=job.reason_code)
                observation.stage("ownership")
                ownership=self._known_cancel_archive_ownership69(reader,run_id)
                observation.stage("authority")
                with authority(reader,run_id=run_id) as permit:
                    self.preparer.verify_retry_native49(reader,run_id=run_id,permit=permit)
                    self.preparer.verify_retry_sources49(permit)
                    claim=BundleClaim(run_id,expected_request,"internal-known-cancel-retry",expected_generation+1)
                    def write(writer):
                        current=self._known_cancel_head(writer,run_id,expected_request=expected_request,
                            expected_generation=expected_generation,expected_version=expected_version)
                        if verify_owner_native(writer,run_id)!=owner_identity:
                            raise PermissionError("publication_recovery_owner_scope_changed")
                        self.preparer.verify_retry_native49(writer,run_id=run_id,permit=permit)
                        self._retry_owner_native_unchanged51(writer,ownership)
                        changed=writer.execute(update(Job).where(Job.run_id==run_id,Job.request_id==expected_request,
                            Job.generation==expected_generation,Job.state_version==expected_version,
                            Job.state=="cancelled",Job.owner.is_(None),Job.lease_expires_at.is_(None),
                            Job.bundle_hash.is_(None),Job.confirmed_at.is_(None),Job.reason_code==current.reason_code,
                            Job.publication_id==current.publication_id,Job.publication_hash==current.publication_hash,
                            Job.publication_schema==current.publication_schema,
                            Job.publication_created_at==current.publication_created_at,
                            Job.selection_native_json==current.selection_native_json,
                            Job.selection_native_hash==current.selection_native_hash,Job.reader_version==current.reader_version,
                            Job.event_id==current.event_id,Job.artifact_id==current.artifact_id
                        ).execution_options(synchronize_session=False).values(
                            generation=expected_generation+1,state="queued",reason_code=None,
                            available_at=self.clock(),updated_at=self.clock()))
                        if changed.rowcount!=1:raise PermissionError("publication_retry_generation_CAS_lost")
                        if command_journal is not None:
                            command_journal(writer,reply,write=True)
                    observation.stage("writer")
                    fence=self._attempt(claim,"retry_queued","0"*64,{},write)
                    observation.stage("readback")
                    with self._ro() as reader:
                        current=reader.get(Job,run_id,populate_existing=True)
                        if (current is None or current.request_id!=expected_request or current.state!="queued"
                                or current.generation!=expected_generation+1 or current.state_version!=expected_version+1
                                or current.owner is not None or current.lease_expires_at is not None
                                or current.bundle_hash is not None or current.confirmed_at is not None):
                            raise ValueError("publication_retry_independent_readback_changed")
                        self._publication(reader,current)
                        for table in ("research_publication_bundles","research_publication_bundle_confirmations"):
                            if reader.connection().exec_driver_sql("SELECT * FROM "+table+" WHERE run_id=?",(run_id,)).fetchall():
                                raise ValueError("publication_retry_saved_or_confirmed_requires_review")
                        if verify_owner_native(reader,run_id)!=owner_identity:
                            raise PermissionError("publication_recovery_owner_scope_changed")
                        if command_journal is not None:
                            command_journal(reader,reply,write=False)
                        self.preparer.verify_retry_native49(reader,run_id=run_id,permit=permit)
                        exit_abi=qualifier(reader,run_id=run_id)
                        if type(exit_abi) is not str or exit_abi!=CURRENT_READER_ABI:
                            raise ValueError("publication_recovery_current_reader_required")
                        if self._known_cancel_history(reader,old_head)!=history:
                            raise ValueError("publication_retry_event_history_changed")
                        if self._known_cancel_archive_ownership69(reader,run_id)!=ownership:
                            raise ValueError("publication_retry_archive_ownership_changed")
                    observation.stage("full_fresh_exit")
                    self.preparer.verify_retry_sources49(permit)
                    self.archive.release(run_id,fence)
            return reply
        except BaseException as error:
            observation_error = error
            raise
        finally:
            observation.terminal(observation_error)

    def retry_owned_preparation_gap70(self, run_id: str, *, expected_request: str,
                             expected_generation: int, expected_version: int,
                             verify_owner_native: Callable, command_journal: Callable | None = None) -> dict:
        """Owner queue-only diagnostic gap; original failure phase stays UNKNOWN.

        Full current source/history/absence and archive ownership are mandatory.
        No report/material serving or replay of an unknown commit is permitted.
        """
        if (type(expected_request) is not str or not expected_request
                or type(expected_generation) is not int or expected_generation<1
                or type(expected_version) is not int or expected_version<1
                or not callable(verify_owner_native)
                or command_journal is not None and not callable(command_journal)):
            raise ValueError("publication_retry_explicit_expected_identity_required")
        reply={"schema_version":"research-report-recovery-reply.v1","run_id":run_id,
            "request_id":expected_request,"generation":expected_generation+1,
            "state_version":expected_version+1,"state":"queued","report_confirmed":False,
            "source_abi":CURRENT_READER_ABI,"automatic_retry":False}
        authority=getattr(self.preparer,"retry_authority49",None)
        qualifier=getattr(self.preparer,"reader_abi_qualification",None)
        if not callable(authority) or not callable(qualifier):
            raise ValueError("publication_recovery_current_reader_required")
        if self.archive.fenced(run_id):
            raise ValueError("publication_bundle_fence_requires_readonly_reconciliation")
        with self._ro() as reader:
            job=self._owned_preparation_gap_head70(reader,run_id,expected_request=expected_request,
                expected_generation=expected_generation,expected_version=expected_version)
            owner_identity=verify_owner_native(reader,run_id)
            if (type(owner_identity) is not tuple or len(owner_identity)!=3
                    or any(type(item) is not str or not item for item in owner_identity)):
                raise PermissionError("publication_recovery_normal_owner_required")
            abi=qualifier(reader,run_id=run_id)
            if type(abi) is not str or abi!=CURRENT_READER_ABI:
                raise ValueError("publication_recovery_current_reader_required")
            history=self._owned_preparation_gap_history70(reader,job)
            old_head=SimpleNamespace(run_id=run_id,request_id=expected_request,generation=expected_generation,
                state_version=expected_version,reason_code=job.reason_code,
                publication_hash=job.publication_hash,selection_native_hash=job.selection_native_hash)
            ownership=self._known_cancel_archive_ownership69(reader,run_id)
            with authority(reader,run_id=run_id) as permit:
                self.preparer.verify_retry_native49(reader,run_id=run_id,permit=permit)
                self.preparer.verify_retry_sources49(permit)
                claim=BundleClaim(run_id,expected_request,"internal-owned-preparation-gap-retry",expected_generation+1)
                def write(writer):
                    current=self._owned_preparation_gap_head70(writer,run_id,expected_request=expected_request,
                        expected_generation=expected_generation,expected_version=expected_version)
                    if verify_owner_native(writer,run_id)!=owner_identity:
                        raise PermissionError("publication_recovery_owner_scope_changed")
                    self.preparer.verify_retry_native49(writer,run_id=run_id,permit=permit)
                    self._retry_owner_native_unchanged51(writer,ownership)
                    changed=writer.execute(update(Job).where(Job.run_id==run_id,Job.request_id==expected_request,
                        Job.generation==expected_generation,Job.state_version==expected_version,
                        Job.state=="unconfirmed",Job.owner.is_(None),Job.lease_expires_at.is_(None),
                        Job.bundle_hash.is_(None),Job.confirmed_at.is_(None),Job.reason_code==current.reason_code,
                        Job.publication_id==current.publication_id,Job.publication_hash==current.publication_hash,
                        Job.publication_schema==current.publication_schema,
                        Job.publication_created_at==current.publication_created_at,
                        Job.selection_native_json==current.selection_native_json,
                        Job.selection_native_hash==current.selection_native_hash,Job.reader_version==current.reader_version,
                        Job.event_id==current.event_id,Job.artifact_id==current.artifact_id
                    ).execution_options(synchronize_session=False).values(
                        generation=expected_generation+1,state="queued",reason_code=None,
                        available_at=self.clock(),updated_at=self.clock()))
                    if changed.rowcount!=1:raise PermissionError("publication_retry_generation_CAS_lost")
                    if command_journal is not None:
                        command_journal(writer,reply,write=True)
                fence=self._attempt(claim,"retry_queued","0"*64,{},write)
                with self._ro() as reader:
                    current=reader.get(Job,run_id,populate_existing=True)
                    if (current is None or current.request_id!=expected_request or current.state!="queued"
                            or current.generation!=expected_generation+1 or current.state_version!=expected_version+1
                            or current.owner is not None or current.lease_expires_at is not None
                            or current.bundle_hash is not None or current.confirmed_at is not None):
                        raise ValueError("publication_retry_independent_readback_changed")
                    self._publication(reader,current)
                    for table in ("research_publication_bundles","research_publication_bundle_confirmations"):
                        if reader.connection().exec_driver_sql("SELECT * FROM "+table+" WHERE run_id=?",(run_id,)).fetchall():
                            raise ValueError("publication_retry_saved_or_confirmed_requires_review")
                    if verify_owner_native(reader,run_id)!=owner_identity:
                        raise PermissionError("publication_recovery_owner_scope_changed")
                    if command_journal is not None:
                        command_journal(reader,reply,write=False)
                    self.preparer.verify_retry_native49(reader,run_id=run_id,permit=permit)
                    exit_abi=qualifier(reader,run_id=run_id)
                    if type(exit_abi) is not str or exit_abi!=CURRENT_READER_ABI:
                        raise ValueError("publication_recovery_current_reader_required")
                    if self._owned_preparation_gap_history70(reader,old_head)!=history:
                        raise ValueError("publication_retry_event_history_changed")
                    if self._known_cancel_archive_ownership69(reader,run_id)!=ownership:
                        raise ValueError("publication_retry_archive_ownership_changed")
                self.preparer.verify_retry_sources49(permit)
                self.archive.release(run_id,fence)
        return reply

    def fail_claim(self, claim: BundleClaim, *, reason: str, failure_diagnostic: dict | None = None) -> None:
        """Preserve published Run; only this owned job changes state."""
        if not re.fullmatch(r"[a-z0-9_]{1,160}",reason):
            reason="publication_bundle_preparation_failed"
        def write(session):
            current=session.get(Job,claim.run_id,populate_existing=True)
            if current is None or current.state not in {"queued","prepared"}:
                raise PermissionError("publication_bundle_internal_claim_lost")
            job=self._owned(session,claim,current.state)
            job.state="unconfirmed"; job.reason_code=reason
            job.owner=job.lease_expires_at=None; job.updated_at=self.clock()
        fence=self._attempt(claim,"unconfirmed","0"*64,
            {"reason_code":reason,"failure_diagnostic":failure_diagnostic},write)
        with self._ro() as session:
            job=session.get(Job,claim.run_id,populate_existing=True)
            if job is None or job.state!="unconfirmed" or job.reason_code!=reason:
                raise ValueError("publication_job_failure_readback_changed")
        self.archive.release(claim.run_id,fence)

    def revoke(self, run_id: str, *, reason: str, expected_version: int) -> None:
        """Internal managed command only; no public writer route."""
        if not re.fullmatch(r"[a-z0-9_]{1,160}",reason):
            raise ValueError("publication_job_revocation_reason_required")
        with self._ro() as session:
            job=session.get(Job,run_id,populate_existing=True)
            if job is None or job.state!="confirmed" or job.state_version!=expected_version:
                raise ValueError("publication_job_revocation_head_changed")
            claim=BundleClaim(job.run_id,job.request_id,"internal-revocation",job.generation)
            digest=job.bundle_hash
        def write(session):
            job=session.get(Job,run_id,populate_existing=True)
            changed=session.execute(update(Job).where(Job.run_id==run_id,Job.state=="confirmed",
                Job.state_version==expected_version,Job.generation==claim.generation,
                Job.bundle_hash==digest).values(state="revoked",generation=claim.generation+1,
                    reason_code=reason,owner=None,lease_expires_at=None,updated_at=self.clock()))
            if changed.rowcount!=1:
                raise ValueError("publication_job_revocation_CAS_lost")
            session.expire(job); session.refresh(job)
        fence=self._attempt(claim,"revoked",digest,{"reason_code":reason},write)
        with self._ro() as session:
            job=session.get(Job,run_id,populate_existing=True)
            if job.state!="revoked" or job.generation!=claim.generation+1:
                raise ValueError("publication_job_revocation_readback_changed")
        self.archive.release(run_id,fence)

    def _confirmed(self,session,run_id,*,allow_fence=False):
        bundle,identity=super()._confirmed(session,run_id,allow_fence=allow_fence)
        if identity is None:
            return bundle,identity
        job=session.get(Job,run_id,populate_existing=True)
        return bundle,(*identity,job.state_version,job.state,job.selection_native_hash)

    def status(self,run_id: str) -> dict:
        def snapshot(session):
            job=session.get(Job,run_id,populate_existing=True)
            if job is None:
                if session.get(RunRecord,run_id) is None:
                    raise KeyError(run_id)
                fenced=self.archive.fenced(run_id)
                return {"run_id":run_id,"state":"unconfirmed" if fenced else "legacy_full_original","state_version":0,
                    "confirmed_at":None,"reason_code":"publication_bundle_commit_unconfirmed" if fenced else None}
            self._publication(session,job)
            fenced=self.archive.fenced(run_id)
            return {"run_id":run_id,"state":"unconfirmed" if fenced else job.state,
                "state_version":job.state_version,"confirmed_at":as_utc(job.confirmed_at),
                "reason_code":"publication_bundle_commit_unconfirmed" if fenced else job.reason_code}
        with self._ro() as session:
            entry=snapshot(session)
        with self._ro() as session:
            exit=snapshot(session)
        if entry!=exit:
            raise ValueError("publication_bundle_serving_head_changed")
        return exit

    def read_with_head(self,run_id: str):
        """Data and current version share the independent serving decision."""
        def snapshot(session):
            job=session.get(Job,run_id,populate_existing=True)
            if job is None and session.get(RunRecord,run_id) is None:
                raise KeyError(run_id)
            fenced=self.archive.fenced(run_id)
            head={"run_id":run_id,"state":"unconfirmed" if fenced else "legacy_full_original","state_version":0,
                "confirmed_at":None,"reason_code":"publication_bundle_commit_unconfirmed" if fenced else None} if job is None else {
                "run_id":run_id,"state":job.state,"state_version":job.state_version,
                "confirmed_at":as_utc(job.confirmed_at),"reason_code":job.reason_code}
            if job is None:
                return None,None,head
            self._publication(session,job)
            if fenced:
                head["state"]="unconfirmed"
                head["reason_code"]="publication_bundle_commit_unconfirmed"
            if head["state"]!="confirmed":
                return None,(job.request_id,job.generation,job.state_version,job.state),head
            bundle,identity=self._confirmed(session,run_id)
            return bundle,identity,head
        with self._ro() as session:
            entry=snapshot(session)
        with self._ro() as session:
            exit=snapshot(session)
        if entry!=exit:
            raise ValueError("publication_bundle_serving_head_changed")
        return exit[0],exit[2]

    def read_confirmed_report_snapshot(self, run_id: str):
        """Bounded report snapshot with entry/exit current permission checks.

        Existing read_with_head continues to verify full external material.
        No job means no snapshot; it never creates work or invokes legacy IO.
        """
        def snapshot(session):
            job = session.get(Job, run_id, populate_existing=True)
            if job is None:
                if session.get(RunRecord, run_id, populate_existing=True) is None:
                    raise KeyError(run_id)
                if self.archive.fenced(run_id):
                    raise ValueError("report_snapshot_commit_unconfirmed")
                return None, None, None
            bundle, identity = self._confirmed_report_snapshot(session, run_id)
            head = {"run_id": run_id, "state": "confirmed_report_snapshot",
                    "state_version": job.state_version, "bundle_sha256": job.bundle_hash,
                    "confirmed_at": as_utc(job.confirmed_at),
                    "material_audit": "not_checked_this_read"}
            return bundle, identity, head

        with self._ro() as session:
            entry = snapshot(session)
        with self._ro() as session:
            exit = snapshot(session)
        if entry != exit:
            raise ValueError("report_snapshot_serving_head_changed")
        return exit[0], exit[2]


_RENEW_BUILTIN_REJECTIONS = {
    PermissionError: frozenset({
        "publication_bundle_internal_claim_lost", "publication_renew_preparation_only_required",
        "publication_renew_saved_or_confirmed_requires_review",
        "publication_renew_exact_head_CAS_lost", "publication_renew_clock_did_not_advance",
    }),
    ValueError: frozenset({
        "publication_bundle_fence_requires_readonly_reconciliation",
        "publication_renew_lease_bound_required", "publication_renew_independent_readback_changed",
        "publication_bundle_independent_read_changed", "publication_bundle_current_publication_changed",
        "publication_job_selected_coverage_changed", "publication_job_selection_native_identity_invalid",
        "publication_job_attempt_head_missing",
    }),
}
_DIAGNOSTIC_REJECTIONS = frozenset({
    "publication_failure_diagnostic_inventory_bound_exceeded",
    "publication_failure_diagnostic_inventory_invalid",
    "publication_failure_diagnostic_bound_exceeded",
    "publication_failure_diagnostic_budget_lock_changed",
    "publication_failure_diagnostic_readback_changed",
    "publication_failure_diagnostic_scope_required", "publication_failure_diagnostic_job_missing",
})


def _publication_renew_rejection_code(error: BaseException) -> str:
    # Exact server-owned types/codes only; no message, SQL or custom type text.
    if type(error) is BundleWriterBeginBusy and error.phase in {"lease_renewed","confirmation_lease_renewed"}:
        return "publication_renew_writer_begin_busy"
    if type(error) is BundleCommitUnconfirmed and error.phase in {"lease_renewed","confirmation_lease_renewed"}:
        return "publication_renew_commit_unconfirmed"
    allowed = _RENEW_BUILTIN_REJECTIONS.get(type(error), ())
    if type(error) is PermissionError and error.args in {
            ("publication_confirmation_claim_required",),("publication_confirmation_exact_prepared_required",),
            ("publication_confirmation_claim_CAS_lost",)}:
        return "publication_renew_preparation_only_required"
    return error.args[0] if len(error.args) == 1 and type(error.args[0]) is str and error.args[0] in allowed else "unknown"


def _publication_diagnostic_rejection_code(error: BaseException) -> str:
    return (error.args[0] if type(error) is ValueError and len(error.args) == 1
        and type(error.args[0]) is str and error.args[0] in _DIAGNOSTIC_REJECTIONS else "unknown")


def _publication_failure_class(error: BaseException) -> str:
    # Exact builtin classes only: custom class names are untrusted text.
    return {
        PublicationExecutionStopped: "PublicationExecutionStopped",
        MemoryError: "MemoryError", ValueError: "ValueError", TypeError: "TypeError",
        PermissionError: "PermissionError", TimeoutError: "TimeoutError",
        RuntimeError: "RuntimeError", OSError: "OSError", Exception: "Exception",
    }.get(type(error), "unknown")


class PublicationBundleWorker:
    """One existing managed worker task; no new process and no GET creation."""
    def __init__(self,lifecycle: DurablePublicationLifecycle, producer: Callable, *, saved_producer=None):
        self.lifecycle,self.producer=lifecycle,producer
        self.saved_producer=saved_producer
        self.owner="publication-bundle:"+str(__import__("os").getpid())+":"+uuid4().hex

    def process_one(self) -> bool:
        saved_continuation=False
        try:
            claim=self.lifecycle.claim_one(owner=self.owner)
            if claim is None and self.saved_producer is not None:
                claim=self.lifecycle.claim_prepared_confirmation(owner=self.owner)
                saved_continuation=claim is not None
                if claim is None:
                    # A fresh UUID per explicit consumption prevents same-gen owner ABA.
                    owner="publication-bundle:"+str(__import__("os").getpid())+":"+uuid4().hex
                    while owner==self.owner:
                        owner="publication-bundle:"+str(__import__("os").getpid())+":"+uuid4().hex
                    claim=self.lifecycle.claim_authorized_saved_confirmation(owner=owner)
                    saved_continuation=claim is not None
        except (BundleCommitUnconfirmed, BundleWriterBeginBusy, PermissionError):
            return False
        if claim is None:
            return False
        prepared=None
        with self.lifecycle.diagnostic_scope(claim):
            try:
                execution=getattr(self,"execution",None)
                renew=(lambda:self.lifecycle.renew_prepared_confirmation(claim)) if saved_continuation else (
                    lambda:self.lifecycle.renew_claim(claim))
                stop=(lambda:self.lifecycle.fail_prepared_confirmation(claim,
                    reason_code="publication_execution_stop_requested")) if saved_continuation else (
                    lambda:self.lifecycle.cancel_preparation(claim))
                stop_reason=(lambda reason_code:self.lifecycle.fail_prepared_confirmation(claim,
                    reason_code=reason_code)) if saved_continuation else (
                    lambda reason_code:self.lifecycle.cancel_preparation(claim,reason_code=reason_code))
                scope=(execution.attempt_scope(claim,renew=renew,
                       cancel_preparation=stop,
                       cancel_preparation_reason=stop_reason,
                       renew_rejection_code=_publication_renew_rejection_code)
                       if execution is not None else nullcontext(None))
                with scope as attempt:
                    prepared=(self.saved_producer(claim) if saved_continuation else self.producer(claim))
                    with execution_stage("save"):
                        if attempt is not None:
                            attempt.prepare_save()
                        if saved_continuation:
                            self.lifecycle.confirm_saved_verified(claim,prepared)
                        else:
                            self.lifecycle.save_verified(claim,prepared)
                return True
            except Exception as error:
                diagnostic={"status":"unavailable","sha256":None}
                try:
                    _reference,digest=self.lifecycle.record_failure_diagnostic(claim,error)
                    diagnostic={"status":"saved","sha256":digest}
                except Exception as diagnostic_error:
                    logging.getLogger(__name__).warning(
                        "publication_failure_diagnostic_unavailable type=%s producer_type=%s reason_code=%s claim=%s",
                        _publication_failure_class(diagnostic_error), _publication_failure_class(error),
                        _publication_diagnostic_rejection_code(diagnostic_error),
                        sha(encoded([claim.run_id,claim.request_id,claim.generation])))
                abandon=getattr(self.lifecycle.preparer,"abandon",None)
                if prepared is not None and callable(abandon):
                    try:abandon(prepared)
                    except Exception as cleanup_error:
                        logging.getLogger(__name__).warning("publication_failure_cleanup_unavailable type=%s claim=%s",
                            type(cleanup_error).__name__,sha(encoded([claim.run_id,claim.request_id,claim.generation])))
                if self.lifecycle.archive.fenced(claim.run_id):
                    return False  # Unknown COMMIT stays fenced; no recovery write.
                try:
                    lines=str(error).splitlines()
                    reason=(lines[0] if isinstance(error,ValueError) and lines else "publication_bundle_preparation_failed")
                    if saved_continuation:
                        self.lifecycle.fail_prepared_confirmation(claim,reason_code=reason,
                            failure_diagnostic=diagnostic)
                    else:
                        self.lifecycle.fail_claim(claim,reason=reason,failure_diagnostic=diagnostic)
                except (PermissionError,BundleCommitUnconfirmed,BundleWriterBeginBusy):
                    pass
                return False
