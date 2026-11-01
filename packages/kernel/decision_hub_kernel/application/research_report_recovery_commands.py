
"""Owner-bound same-Run preparation recovery; never starts research or grants a report."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import hashlib
import json

from ..persistence.db import Database, ProductCommandRecord, RunRecord, as_utc, utcnow
from ..persistence.research_publication_bundle_records import PublicationBundleJobRecord
from ..ports.research_report_recovery import (
    ReportRecoveryCommandV1, ReportRecoveryReceiptV1, ReportRecoveryReplyV1,
)
from .research_observability import ResearchCommandService
from .research_submission import owner_root_creation_receipt_in_session


class ReportRecoveryCommandService:
    KIND = "report_recovery"

    def __init__(self, database: Database, lifecycle, *, clock: Callable[[], datetime] = utcnow):
        self.database, self.lifecycle, self.clock = database, lifecycle, clock

    @staticmethod
    def _owner_identity(session, run_id: str, owner_id: str) -> tuple[str, str, str]:
        if (type(owner_id) is not str or not 1 <= len(owner_id) <= 128
                or owner_id == ResearchCommandService.LEGACY_INTERNAL_OWNER):
            raise PermissionError("publication_recovery_normal_owner_required")
        run = session.get(RunRecord, run_id, populate_existing=True)
        if (run is None or run.strategy_version != "research.v1"
                or run.admission_origin != "manual"):
            raise PermissionError("publication_recovery_original_creation_required")
        if run.parent_run_id is None:
            original = owner_root_creation_receipt_in_session(session, run_id, owner_id)
            if original is None:
                raise PermissionError("publication_recovery_original_creation_required")
            record = session.get(ProductCommandRecord, original.request_id, populate_existing=True)
            if record is None or record.owner_id != owner_id:
                raise PermissionError("publication_recovery_original_creation_required")
            return owner_id, record.request_id, record.payload_hash
        prefix = "research-command:"
        if type(run.idempotency_key) is not str or not run.idempotency_key.startswith(prefix):
            raise PermissionError("publication_recovery_original_creation_required")
        request_id = run.idempotency_key[len(prefix):]
        original = session.get(ProductCommandRecord, request_id, populate_existing=True)
        if (original is None or original.owner_id != owner_id
                or original.kind != ResearchCommandService.KIND):
            raise PermissionError("publication_recovery_original_creation_required")
        receipt = ResearchCommandService._receipt_view(original)
        parent = session.get(RunRecord, run.parent_run_id, populate_existing=True)
        if (parent is None or parent.strategy_version != "research.v1"
                or parent.event_id != run.event_id
                or receipt.owner_id != owner_id or receipt.source_run_id != run.parent_run_id
                or receipt.command.command not in {"retry", "recheck"}
                or receipt.result.status != "accepted" or receipt.result.target_run_id != run_id
                or receipt.request_id != request_id
                or receipt.result.created_at != as_utc(run.created_at)):
            raise PermissionError("publication_recovery_original_creation_required")
        return owner_id, original.request_id, original.payload_hash

    @classmethod
    def _fingerprint(cls, owner_id, run_id, command, owner_identity):
        value = [cls.KIND, owner_id, run_id, command.model_dump(mode="json"), owner_identity]
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def _record_receipt(cls, record, *, owner_id, run_id, owner_identity):
        if (record.owner_id != owner_id or record.target_id != run_id or record.kind != cls.KIND):
            raise ValueError("publication_recovery_request_reused")
        receipt = ReportRecoveryReceiptV1.model_validate_json(record.result_json)
        digest = cls._fingerprint(owner_id, run_id, receipt.command, owner_identity)
        result = receipt.result
        if (receipt.request_id != record.request_id or receipt.owner_id != owner_id
                or receipt.run_id != run_id or receipt.command.request_id != record.request_id
                or receipt.payload_hash != record.payload_hash or record.payload_hash != digest
                or receipt.recorded_at != as_utc(record.created_at) or receipt.status != "accepted"
                or result.run_id != run_id or result.request_id != receipt.command.expected_request_id
                or result.generation != receipt.command.expected_generation + 1
                or result.state_version != receipt.command.expected_state_version + 1):
            raise ValueError("publication_recovery_receipt_invalid")
        return receipt

    def _receipt_snapshot(self, session, run_id, request_id, owner_id):
        if self.lifecycle.archive.fenced(run_id):
            raise ValueError("publication_bundle_fence_requires_readonly_reconciliation")
        owner_identity = self._owner_identity(session, run_id, owner_id)
        job = session.get(PublicationBundleJobRecord, run_id, populate_existing=True)
        if job is None:
            raise KeyError(run_id)
        self.lifecycle._publication(session, job)
        record = session.get(ProductCommandRecord, request_id, populate_existing=True)
        receipt = (None if record is None else self._record_receipt(
            record, owner_id=owner_id, run_id=run_id, owner_identity=owner_identity))
        if receipt is not None and (job.request_id != receipt.result.request_id
                or job.generation < receipt.result.generation
                or job.state_version < receipt.result.state_version):
            raise ValueError("publication_recovery_receipt_head_changed")
        if self.lifecycle.archive.fenced(run_id):
            raise ValueError("publication_bundle_fence_requires_readonly_reconciliation")
        return receipt, owner_identity

    def receipt(self, run_id: str, request_id: str, *, owner_id: str,
                missing_ok: bool = False) -> ReportRecoveryReceiptV1 | None:
        with self.lifecycle._ro() as session:
            entry = self._receipt_snapshot(session, run_id, request_id, owner_id)
        with self.lifecycle._ro() as session:
            exit = self._receipt_snapshot(session, run_id, request_id, owner_id)
        if entry != exit:
            raise ValueError("publication_recovery_receipt_changed")
        if exit[0] is None and not missing_ok:
            raise KeyError(request_id)
        return exit[0]

    def apply(self, run_id: str, command: ReportRecoveryCommandV1, *,
              owner_id: str) -> ReportRecoveryReceiptV1:
        prior = self.receipt(run_id, command.request_id, owner_id=owner_id, missing_ok=True)
        if prior is not None:
            if prior.command != command:
                raise ValueError("publication_recovery_request_reused")
            return prior
        with self.lifecycle._ro() as session:
            owner_identity = self._owner_identity(session, run_id, owner_id)
            head = session.get(PublicationBundleJobRecord, run_id, populate_existing=True)
            owned_gap = (head is not None and head.request_id == command.expected_request_id
                and head.generation == command.expected_generation
                and head.state_version == command.expected_state_version
                and head.state == "unconfirmed" and head.reason_code == "frozen_io_root_data_fallback")
        digest = self._fingerprint(owner_id, run_id, command, owner_identity)

        def verify_owner_native(session, actual_run_id):
            if actual_run_id != run_id:
                raise PermissionError("publication_recovery_owner_scope_changed")
            actual = self._owner_identity(session, run_id, owner_id)
            if actual != owner_identity:
                raise PermissionError("publication_recovery_owner_scope_changed")
            return actual

        def command_journal(session, reply, *, write):
            verify_owner_native(session, run_id)
            result = ReportRecoveryReplyV1.model_validate(reply)
            record = session.get(ProductCommandRecord, command.request_id, populate_existing=True)
            if write:
                if record is not None:
                    raise ValueError("publication_recovery_request_reused")
                now = self.clock()
                receipt = ReportRecoveryReceiptV1(
                    schema_version="research-report-recovery-receipt.v1",
                    request_id=command.request_id, owner_id=owner_id, run_id=run_id,
                    command=command, result=result, status="accepted",
                    payload_hash=digest, recorded_at=now)
                session.add(ProductCommandRecord(
                    request_id=command.request_id, owner_id=owner_id, target_id=run_id,
                    kind=self.KIND, payload_hash=digest, result_json=receipt.model_dump_json(),
                    created_at=now))
            else:
                if record is None:
                    raise ValueError("publication_recovery_receipt_missing")
                receipt = self._record_receipt(record, owner_id=owner_id,
                    run_id=run_id, owner_identity=owner_identity)
                if receipt.command != command or receipt.result != result:
                    raise ValueError("publication_recovery_receipt_invalid")

        try:
            retry = (self.lifecycle.retry_owned_preparation_gap70 if owned_gap
                else self.lifecycle.retry_known_cancelled)
            retry(run_id,
                expected_request=command.expected_request_id,
                expected_generation=command.expected_generation,
                expected_version=command.expected_state_version,
                verify_owner_native=verify_owner_native, command_journal=command_journal)
        except (PermissionError, ValueError) as error:
            # A concurrent *known* CAS/head loss permits only a fresh receipt read.
            # Unknown SQL/COMMIT is not caught and never repeats preparation.
            if not (type(error) is PermissionError and error.args == ("publication_retry_generation_CAS_lost",)
                    or type(error) is ValueError and error.args == ("publication_recovery_exact_known_cancel_required",)):
                raise
            prior = self.receipt(run_id, command.request_id, owner_id=owner_id, missing_ok=True)
            if prior is None:
                raise
            if prior.command != command:
                raise ValueError("publication_recovery_request_reused") from error
            return prior
        receipt = self.receipt(run_id, command.request_id, owner_id=owner_id)
        if receipt is None or receipt.command != command:
            raise ValueError("publication_recovery_receipt_invalid")
        return receipt


class PreparationAuthorizationRecoveryCommandService:
    """Scheduled no-Saved preparation recovery; distinct permanent plan receipt."""
    KIND = "publication_preparation_recovery"
    RECEIPT_BOUND = 64 * 1024

    def __init__(self, database, lifecycle, *, clock=utcnow):
        self.database, self.lifecycle, self.clock = database, lifecycle, clock

    @staticmethod
    def _canonical(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    @classmethod
    def _sha(cls, value):
        return hashlib.sha256(cls._canonical(value).encode("utf-8")).hexdigest()

    def _owner(self, session, run_id, owner_id):
        proof=self.lifecycle.saved_confirmation_owner_proof(session,run_id,owner_id)
        if type(proof) is not dict or set(proof)!={"owner_id","root_run_id","lineage","owner_source"}:
            raise PermissionError("publication_recovery_normal_owner_required")
        if proof["owner_id"]!=owner_id:
            raise PermissionError("publication_recovery_owner_scope_changed")
        return proof

    def _plan(self, session, run_id):
        run=session.get(RunRecord,run_id,populate_existing=True)
        if (run is None or run.admission_origin!="scheduled_recheck" or not run.parent_run_id
                or type(run.idempotency_key) is not str or not run.idempotency_key.startswith("research-review-plan:")):
            raise PermissionError("publication_preparation_recovery_plan_required")
        plan_id=run.idempotency_key[len("research-review-plan:"):]
        if not 1<=len(plan_id)<=128 or plan_id!=plan_id.strip():
            raise ValueError("publication_preparation_recovery_plan_changed")
        return plan_id

    def _metadata(self, session, run_id, owner_id, *, read_abi=None):
        from ..ports.research_report_recovery import PreparationRecoveryStatusV2
        if self.lifecycle.archive.fenced(run_id):raise ValueError("publication_bundle_fence_requires_readonly_reconciliation")
        plan_id=self._plan(session,run_id)
        owner_hash=self._sha(self._owner(session,run_id,owner_id))
        job=session.get(PublicationBundleJobRecord,run_id,populate_existing=True)
        if job is None:raise KeyError(run_id)
        self.lifecycle._publication(session,job)
        native_hash,abi,eligible=None,None,False
        if job.state=="unconfirmed" and job.reason_code=="frozen_io_undeclared_authorization_resource":
            _,actual_plan,native_hash=self.lifecycle._preparation_authorization_head79(session,run_id,
                expected_request=job.request_id,expected_generation=job.generation,expected_version=job.state_version)
            if actual_plan!=plan_id:raise ValueError("publication_preparation_recovery_plan_changed")
            qualifier=getattr(self.lifecycle.preparer,"reader_abi_qualification",None)
            if not callable(qualifier):raise ValueError("publication_recovery_current_reader_required")
            abi=(qualifier(session,run_id=run_id) if read_abi is None
                else read_abi(session,run_id=run_id))
            if abi is not None or read_abi is None:
                if type(abi) is not str or abi!="publication-source-snapshot-read.v2":
                    raise ValueError("publication_recovery_current_reader_required")
                eligible=True
        if self.lifecycle.archive.fenced(run_id):
            raise ValueError("publication_bundle_fence_requires_readonly_reconciliation")
        return PreparationRecoveryStatusV2(run_id=run_id,request_id=job.request_id,plan_id=plan_id,
            generation=job.generation,state_version=job.state_version,state=job.state,reason_code=job.reason_code,
            eligible=eligible,native_sha256=native_hash,owner_native_sha256=owner_hash,source_abi=abi)

    def status(self,run_id,*,owner_id):
        consumer=getattr(self.lifecycle.preparer,"consume_reader_abi_qualification",None)
        if not callable(consumer):
            with self.lifecycle._ro() as reader:entry=self._metadata(reader,run_id,owner_id)
            with self.lifecycle._ro() as reader:exit=self._metadata(reader,run_id,owner_id)
            if entry!=exit:raise ValueError("publication_preparation_recovery_metadata_changed")
            return exit
        with self.lifecycle._ro() as reader:
            needed=False
            def defer_abi(session,*,run_id):
                nonlocal needed
                needed=True
                return None
            entry=self._metadata(reader,run_id,owner_id,read_abi=defer_abi)
            if not needed:
                with self.lifecycle._ro() as second:
                    exit=self._metadata(second,run_id,owner_id,read_abi=defer_abi)
                if entry!=exit:raise ValueError("publication_preparation_recovery_metadata_changed")
                return exit
            def consume(abi,finish):
                if type(abi) is not str or abi!="publication-source-snapshot-read.v2":
                    raise ValueError("publication_recovery_current_reader_required")
                qualified=type(entry).model_validate({**entry.model_dump(),"eligible":True,"source_abi":abi})
                # Only the actual ABI scalar crosses to the independent reader.
                with self.lifecycle._ro() as second:
                    exit=self._metadata(second,run_id,owner_id,
                        read_abi=lambda session,*,run_id:abi)
                    if qualified!=exit:raise ValueError("publication_preparation_recovery_metadata_changed")
                    finish(second)
                return exit
            return consumer(reader,run_id=run_id,consume=consume)

    @classmethod
    def _fingerprint(cls,run_id,command):
        return cls._sha([cls.KIND,run_id,command.model_dump(mode="json")])

    def _record(self,record,*,run_id,owner_id,plan_id):
        from ..ports.research_report_recovery import PreparationRecoveryReceiptV2
        if (record.kind!=self.KIND or record.target_id!=plan_id or record.owner_id!=owner_id
                or type(record.result_json) is not str
                or not 0<len(record.result_json.encode("utf-8"))<=self.RECEIPT_BOUND):
            raise ValueError("publication_preparation_recovery_request_reused")
        receipt=PreparationRecoveryReceiptV2.model_validate_json(record.result_json)
        if (receipt.request_id!=record.request_id or receipt.run_id!=run_id or receipt.owner_id!=owner_id
                or receipt.plan_id!=plan_id or receipt.recorded_at!=as_utc(record.created_at)
                or receipt.payload_hash!=record.payload_hash
                or record.payload_hash!=self._fingerprint(run_id,receipt.command)
                or self._canonical(receipt.model_dump(mode="json"))!=record.result_json):
            raise ValueError("publication_preparation_recovery_receipt_changed")
        return receipt

    def _receipt_snapshot(self,session,run_id,request_id,owner_id):
        if self.lifecycle.archive.fenced(run_id):raise ValueError("publication_bundle_fence_requires_readonly_reconciliation")
        if self.lifecycle.archive.fenced(run_id):raise ValueError("publication_bundle_fence_requires_readonly_reconciliation")
        plan_id=self._plan(session,run_id)
        owner_hash=self._sha(self._owner(session,run_id,owner_id))
        job=session.get(PublicationBundleJobRecord,run_id,populate_existing=True)
        if job is None:raise KeyError(run_id)
        self.lifecycle._publication(session,job)
        record=session.get(ProductCommandRecord,request_id,populate_existing=True)
        receipt=None if record is None else self._record(record,run_id=run_id,owner_id=owner_id,plan_id=plan_id)
        acceptance_native=None
        if receipt is not None:
            command,result=receipt.command,receipt.result
            if (command.expected_owner_native_sha256!=owner_hash or job.request_id!=command.expected_request_id
                    or job.generation<result.generation or job.state_version<result.state_version):
                raise ValueError("publication_preparation_recovery_receipt_head_changed")
            history=self.lifecycle._retry_gap_history_native49(session,run_id,command.expected_state_version)
            if self._sha(history)!=result.event_history_sha256:
                raise ValueError("publication_retry_event_history_changed")
            last=dict(zip(history["columns"],history["rows"][-1]))
            if (last["reason_code"]!=command.expected_reason or last["state"]!="unconfirmed"
                    or last["generation"]!=command.expected_generation or last["request_id"]!=command.expected_request_id):
                raise ValueError("publication_preparation_recovery_terminal_history_changed")
            from sqlalchemy import select
            from ..persistence.research_publication_bundle_records import PublicationBundleStateEventRecord
            event=session.scalar(select(PublicationBundleStateEventRecord).where(
                PublicationBundleStateEventRecord.run_id==run_id,PublicationBundleStateEventRecord.version==result.state_version))
            if event is None:raise ValueError("publication_preparation_recovery_receipt_event_missing")
            acceptance_native=self.lifecycle._confirmation_rows74(session,PublicationBundleStateEventRecord,
                "SELECT * FROM research_publication_bundle_state_events WHERE run_id=? AND version=?",(run_id,result.state_version))
            native=dict(zip(acceptance_native["columns"],(cell[1] for cell in acceptance_native["rows"][0])))
            body=json.loads(event.payload_json)
            fields={"schema_version","run_id","request_id","version","generation","state","reason_code","bundle_hash","occurred_at","transition_phase"}
            if (type(body) is not dict or set(body)!=fields
                    or hashlib.sha256(event.payload_json.encode()).hexdigest()!=event.event_id
                    or self._canonical(body)!=event.payload_json or body.get("schema_version")!="publication-bundle-state-event.v2"
                    or body.get("transition_phase")!="retry_queued" or body.get("state")!="queued"
                    or body.get("generation")!=result.generation or body.get("version")!=result.state_version
                    or body.get("request_id")!=result.request_id or body.get("run_id")!=run_id
                    or type(body.get("version")) is not int or type(body.get("generation")) is not int
                    or any(body.get(name)!=native[name] for name in (
                        "run_id","request_id","version","generation","state","reason_code","bundle_hash"))
                    or type(body.get("occurred_at")) is not str
                    or as_utc(datetime.fromisoformat(body["occurred_at"]))!=as_utc(datetime.fromisoformat(native["occurred_at"]))
                    or body.get("reason_code") is not None or body.get("bundle_hash") is not None):
                raise ValueError("publication_preparation_recovery_receipt_event_changed")
        if self.lifecycle.archive.fenced(run_id):raise ValueError("publication_bundle_fence_requires_readonly_reconciliation")
        return receipt,owner_hash,plan_id,acceptance_native

    def receipt(self,run_id,request_id,*,owner_id,missing_ok=False):
        with self.lifecycle._ro() as reader:entry=self._receipt_snapshot(reader,run_id,request_id,owner_id)
        with self.lifecycle._ro() as reader:exit=self._receipt_snapshot(reader,run_id,request_id,owner_id)
        if entry!=exit:raise ValueError("publication_preparation_recovery_receipt_changed")
        if exit[0] is None and not missing_ok:raise KeyError(request_id)
        return exit[0]

    def apply(self,run_id,command,*,owner_id):
        from ..ports.research_report_recovery import PreparationRecoveryCommandV2,PreparationRecoveryReceiptV2,PreparationRecoveryReplyV2
        from .research_publication_jobs48 import DIAGNOSTIC_GAP_RECOVERY_MODE
        if type(command) is not PreparationRecoveryCommandV2 or command.owner_id!=owner_id:
            raise PermissionError("publication_recovery_normal_owner_required")
        prior=self.receipt(run_id,command.request_id,owner_id=owner_id,missing_ok=True)
        if prior is not None:
            if prior.command!=command:raise ValueError("publication_preparation_recovery_request_reused")
            return prior
        try:
            metadata=self.status(run_id,owner_id=owner_id)
            if (not metadata.eligible or metadata.request_id!=command.expected_request_id
                    or metadata.plan_id!=command.expected_plan_id or metadata.generation!=command.expected_generation
                    or metadata.state_version!=command.expected_state_version or metadata.reason_code!=command.expected_reason
                    or metadata.native_sha256!=command.expected_native_sha256
                    or metadata.owner_native_sha256!=command.expected_owner_native_sha256):
                raise ValueError("publication_preparation_recovery_expected_head_changed")
        except ValueError as error:
            if error.args not in {("publication_preparation_recovery_expected_head_changed",),
                    ("publication_retry_exact_unconfirmed_head_required",),
                    ("publication_preparation_recovery_metadata_changed",)}:raise
            prior=self.receipt(run_id,command.request_id,owner_id=owner_id,missing_ok=True)
            if prior is None or prior.command!=command:raise
            return prior
        with self.lifecycle._ro() as reader:owner_proof=self._owner(reader,run_id,owner_id)
        fingerprint=self._fingerprint(run_id,command)
        def owner_native(session,actual_run):
            if actual_run!=run_id or self._plan(session,run_id)!=command.expected_plan_id:
                raise PermissionError("publication_recovery_owner_scope_changed")
            head=session.get(PublicationBundleJobRecord,run_id,populate_existing=True)
            if (head is not None and head.state=="unconfirmed" and head.generation==command.expected_generation
                    and head.state_version==command.expected_state_version):
                _,_,native_hash=self.lifecycle._preparation_authorization_head79(session,run_id,
                    expected_request=command.expected_request_id,expected_generation=command.expected_generation,
                    expected_version=command.expected_state_version)
                if native_hash!=command.expected_native_sha256:
                    raise ValueError("publication_preparation_recovery_native_changed")
            actual=self._owner(session,run_id,owner_id)
            if actual!=owner_proof or self._sha(actual)!=command.expected_owner_native_sha256:
                raise PermissionError("publication_recovery_owner_scope_changed")
            return actual
        def journal(session,raw_reply,*,write):
            owner_native(session,run_id)
            reply=PreparationRecoveryReplyV2.model_validate(raw_reply)
            record=session.get(ProductCommandRecord,command.request_id,populate_existing=True)
            if write:
                if record is not None:raise ValueError("publication_preparation_recovery_request_reused")
                now=self.clock()
                receipt=PreparationRecoveryReceiptV2(request_id=command.request_id,owner_id=owner_id,run_id=run_id,
                    plan_id=command.expected_plan_id,command=command,result=reply,status="accepted",payload_hash=fingerprint,recorded_at=now)
                raw=self._canonical(receipt.model_dump(mode="json"))
                if len(raw.encode("utf-8"))>self.RECEIPT_BOUND:raise ValueError("publication_preparation_recovery_receipt_bound_exceeded")
                session.add(ProductCommandRecord(request_id=command.request_id,owner_id=owner_id,
                    target_id=command.expected_plan_id,kind=self.KIND,payload_hash=fingerprint,result_json=raw,created_at=now))
            else:
                if record is None:raise ValueError("publication_preparation_recovery_receipt_missing")
                receipt=self._record(record,run_id=run_id,owner_id=owner_id,plan_id=command.expected_plan_id)
                if receipt.command!=command or receipt.result!=reply:
                    raise ValueError("publication_preparation_recovery_receipt_changed")
        try:
            self.lifecycle.retry_unconfirmed(run_id,expected_request=command.expected_request_id,
                expected_generation=command.expected_generation,expected_version=command.expected_state_version,
                recovery_mode=DIAGNOSTIC_GAP_RECOVERY_MODE,expected_reason=command.expected_reason,
                verify_owner_native=owner_native,command_journal=journal)
        except (PermissionError,ValueError) as error:
            if not (type(error) is PermissionError and error.args==("publication_retry_generation_CAS_lost",)
                    or type(error) is ValueError and error.args in {
                        ("publication_retry_exact_unconfirmed_head_required",),
                        ("publication_preparation_recovery_expected_head_changed",)}):raise
            prior=self.receipt(run_id,command.request_id,owner_id=owner_id,missing_ok=True)
            if prior is None or prior.command!=command:raise
            return prior
        receipt=self.receipt(run_id,command.request_id,owner_id=owner_id)
        if receipt.command!=command:raise ValueError("publication_preparation_recovery_receipt_changed")
        return receipt

