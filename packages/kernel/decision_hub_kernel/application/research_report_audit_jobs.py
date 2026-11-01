"""One explicit API-owned full reader, with durable retrievable evidence.

No publication claim, model, permit or replay scope is manufactured here.
Every native call enters the unchanged public reader on its own thread.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import os
import re
import time
from datetime import timedelta
from threading import RLock
from uuid import uuid4

from sqlalchemy import select
from ..persistence.db import RunRecord, RunRoleOutputRecord, as_utc, utcnow
from ..persistence.research_publication_bundle_records import (
    PublicationBundleJobRecord as Publication, PublicationBundleRecord as Saved,
    PublicationBundleConfirmationRecord as PublicationAck,
)
from ..persistence.research_report_audit_records import ReportAuditJobRecord as Job, ReportAuditConfirmationRecord as Ack
from ..ports.research_report_audit import (
    AUDIT_TOTAL_SECONDS, RESULT_BOUND, ReportAuditContextV1, ReportAuditHeadV1,
    ReportAuditReceiptV1, ReportAuditRequestV1, ReportAuditStateV1,
    ReportAuditResultV1, ReportAuditResultReplyV1,
)
from .research_publication_execution import PublicationExecutionCoordinator
from .research_quality_view import ResearchQualityViewV1

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

class ReportAuditCommitUnknown(RuntimeError):
    pass

class ReportAuditManager:
    HEAD_BOUND = 2 * 1024 * 1024
    RECEIPT_BOUND = 64 * 1024

    def __init__(self, database, lifecycle, *, clock=utcnow):
        self.database, self.lifecycle, self.clock = database, lifecycle, clock
        self.epoch = digest(f"report-audit:{os.getpid()}:{uuid4().hex}")
        self.coordinator = PublicationExecutionCoordinator()
        self.loop, self.task, self.lock = None, None, RLock()
        self.uncertain = set()

    @staticmethod
    def _row(session, model, clause, params):
        table = model.__table__
        result = session.connection().exec_driver_sql(f"SELECT * FROM {table.name} WHERE {clause}", params)
        if set(result.keys()) != set(table.columns.keys()):
            raise ValueError("report_audit_native_schema_changed")
        rows = result.fetchmany(2)
        if len(rows) != 1:
            raise ValueError("report_audit_exact_native_row_required")
        value = dict(zip(result.keys(), rows[0]))
        if any(type(cell) not in {str, int, float, type(None)} for cell in value.values()):
            raise ValueError("report_audit_native_cell_changed")
        return value

    def _identity(self, session, run_id):
        if self.lifecycle.archive.fenced(run_id):
            raise ValueError("report_audit_publication_fenced")
        native = {name: self._row(session, model, "run_id=?", (run_id,)) for name, model in
                  (("run", RunRecord), ("job", Publication), ("saved", Saved), ("ack", PublicationAck))}
        for name, role in (("publication", "research_publication_source"), ("selection", "graph_recipe_selection")):
            native[name] = self._row(session, RunRoleOutputRecord, "run_id=? AND role=?", (run_id, role))
        job, saved, ack = native["job"], native["saved"], native["ack"]
        if (job["state"] != "confirmed" or job["owner"] is not None or job["lease_expires_at"] is not None
                or job["reason_code"] is not None or job["confirmed_at"] is None
                or any(row["request_id"] != job["request_id"] or row["generation"] != job["generation"]
                       or row["bundle_hash"] != job["bundle_hash"] for row in (saved, ack))
                or ack["confirmed_at"] != job["confirmed_at"]):
            raise ValueError("report_audit_confirmed_identity_required")
        for name in ("publication", "selection"):
            row = native[name]
            if digest(row["payload_json"]) != row["payload_hash"]:
                raise ValueError("report_audit_native_payload_changed")
        raw = canonical(native)
        if len(raw.encode("utf-8")) > self.HEAD_BOUND:
            raise ValueError("report_audit_head_bound_exceeded")
        head = ReportAuditHeadV1(run_id=run_id, generation=job["generation"], state_version=job["state_version"],
            identity_sha256=digest(raw), bundle_sha256=saved["bundle_hash"], material_sha256=saved["material_hash"],
            report_sha256=saved["report_hash"])
        return head, raw

    def snapshot(self, run_id):
        # The ordinary fast reader checks present serving permission, original
        # bundle bytes and report. Native rows freeze the complete Saved/ACK.
        bundle, serving = self.lifecycle.read_confirmed_report_snapshot(run_id)
        if bundle is None or serving is None:
            raise ValueError("report_audit_confirmed_snapshot_required")
        with self.lifecycle._ro() as session:
            entry = self._identity(session, run_id)
        with self.lifecycle._ro() as session:
            exit = self._identity(session, run_id)
        final_bundle, final_serving = self.lifecycle.read_confirmed_report_snapshot(run_id)
        if (entry != exit or bundle != final_bundle or serving != final_serving
                or serving["state_version"] != exit[0].state_version
                or serving["bundle_sha256"] != exit[0].bundle_sha256):
            raise ValueError("report_audit_serving_head_changed")
        return exit

    def _write(self, audit_id, action):
        session = self.database.factory()
        committing = False
        try:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            value = action(session)
            session.flush()
            committing = True
            session.commit()
            return value
        except BaseException as error:
            session.rollback()
            if committing:
                with self.lock:
                    self.uncertain.add(audit_id)
                raise ReportAuditCommitUnknown("report_audit_commit_unknown") from error
            raise
        finally:
            session.close()

    def _validated(self, row, run_id, key, owner):
        if (row is None or row.run_id != run_id or row.request_key != key or row.owner_id != owner
                or len(row.receipt_json.encode("utf-8")) > self.RECEIPT_BOUND
                or digest(row.receipt_json) != row.receipt_hash or digest(row.identity_json) != row.identity_hash):
            raise ValueError("report_audit_journal_changed")
        receipt = ReportAuditReceiptV1.model_validate_json(row.receipt_json)
        if (canonical(receipt.model_dump(mode="json")) != row.receipt_json
                or receipt.audit_id != row.audit_id or receipt.epoch != row.epoch
                or receipt.request.request_key != key or receipt.request.owner_id != owner
                or receipt.request.expected_head.run_id != run_id
                or receipt.request.expected_head.identity_sha256 != row.identity_hash
                or receipt.accepted_at != as_utc(row.created_at)
                or receipt.request_sha256 != digest(canonical(receipt.request.model_dump(mode="json")))
                or row.audit_id != digest(canonical([run_id, owner, key]))):
            raise ValueError("report_audit_receipt_changed")
        return receipt

    def _state(self, session, row, receipt):
        ack = session.get(Ack, row.audit_id, populate_existing=True)
        if (row.state == "completed") != (ack is not None):
            raise ValueError("report_audit_confirmation_changed")
        if ack is not None:
            if (ack.identity_hash != row.identity_hash or ack.receipt_hash != row.receipt_hash
                    or ack.result_hash != row.result_hash or ack.result_bytes != row.result_bytes
                    or as_utc(ack.confirmed_at) != as_utc(row.finished_at)):
                raise ValueError("report_audit_confirmation_changed")
            self._result(row, receipt)
        return ReportAuditStateV1(receipt=receipt, state=row.state, version=row.version,
            started_at=as_utc(row.started_at), deadline_at=as_utc(row.deadline_at), finished_at=as_utc(row.finished_at),
            native_exit_observed_at=as_utc(row.native_exit_observed_at), native_exited=row.native_exited == 1,
            reason_code=row.reason_code, result_sha256=row.result_hash, result_bytes=row.result_bytes,
            audit_confirmed=row.state == "completed")

    def _result(self, row, receipt):
        if type(row.result_json) is not str:
            raise ValueError("report_audit_result_missing")
        raw = row.result_json.encode("utf-8")
        if not 0 < len(raw) <= RESULT_BOUND or len(raw) != row.result_bytes or digest(row.result_json) != row.result_hash:
            raise ValueError("report_audit_result_bytes_changed")
        result = ReportAuditResultV1.model_validate_json(raw)
        view = ResearchQualityViewV1.model_validate_json(result.quality_json)
        if (canonical(result.model_dump(mode="json")) != row.result_json
                or result.audit_id != row.audit_id or result.head != receipt.request.expected_head
                or view.run_id != row.run_id or view.status != "available" or view.decision is None
                or view.as_of != result.checked_at or view.decision.full_report_hash != result.head.report_sha256):
            raise ValueError("report_audit_result_identity_changed")
        return result

    def context(self, run_id):
        head, _ = self.snapshot(run_id)
        with self.lifecycle._ro() as session:
            active = list(session.scalars(select(Job.audit_id).where(Job.active_slot == "native").limit(2)))
        if len(active) > 1:
            raise ValueError("report_audit_slot_changed")
        with self.lock:
            local_busy = self.task is not None and not self.task.done()
        return ReportAuditContextV1(head=head, active_audit_id=active[0] if active else None,
            request_allowed=not active and not local_busy and self.loop is not None)

    def status(self, run_id, key, *, owner_id):
        audit_id = digest(canonical([run_id, owner_id, key]))
        with self.lock:
            if audit_id in self.uncertain:
                raise ReportAuditCommitUnknown("report_audit_commit_unknown")
        head, raw = self.snapshot(run_id)
        def read(session):
            row = session.get(Job, audit_id, populate_existing=True)
            if row is None:
                raise KeyError(key)
            receipt = self._validated(row, run_id, key, owner_id)
            if receipt.request.expected_head != head or row.identity_json != raw:
                raise ValueError("report_audit_serving_head_changed")
            return self._state(session, row, receipt)
        with self.lifecycle._ro() as session:
            entry = read(session)
        with self.lifecycle._ro() as session:
            exit = read(session)
        if entry != exit or self.snapshot(run_id) != (head, raw):
            raise ValueError("report_audit_status_changed")
        return exit

    def result(self, run_id, key, *, owner_id):
        status = self.status(run_id, key, owner_id=owner_id)
        if not status.audit_confirmed:
            raise ValueError("report_audit_result_unconfirmed")
        with self.lifecycle._ro() as session:
            row = session.get(Job, status.receipt.audit_id, populate_existing=True)
            result = self._result(row, status.receipt)
        if self.status(run_id, key, owner_id=owner_id) != status:
            raise ValueError("report_audit_result_changed")
        return ReportAuditResultReplyV1(status=status, result=result)

    def accept(self, run_id, command, *, owner_id):
        if type(command) is not ReportAuditRequestV1 or command.owner_id != owner_id or command.expected_head.run_id != run_id:
            raise PermissionError("report_audit_request_identity_changed")
        audit_id = digest(canonical([run_id, owner_id, command.request_key]))
        try:
            existing = self.status(run_id, command.request_key, owner_id=owner_id)
        except KeyError:
            existing = None
        if existing is not None:
            if existing.receipt.request != command:
                raise ValueError("report_audit_key_reused")
            # Explicit replay of the original POST can start an independently
            # verified accepted-but-unstarted row in this same normal epoch.
            # GET never starts work; a lost/unknown COMMIT epoch never resumes.
            if (existing.state == "accepted" and existing.started_at is None
                    and existing.receipt.epoch == self.epoch and self.loop is not None):
                self.loop.call_soon_threadsafe(self._launch, audit_id)
            return existing.receipt
        with self.lock:
            if self.task is not None and not self.task.done():
                raise ValueError("report_audit_native_slot_busy")
        if self.loop is None:
            raise ValueError("report_audit_manager_not_started")
        head, identity = self.snapshot(run_id)
        if head != command.expected_head:
            raise ValueError("report_audit_expected_head_changed")
        now = self.clock()
        receipt = ReportAuditReceiptV1(audit_id=audit_id, request=command,
            request_sha256=digest(canonical(command.model_dump(mode="json"))), accepted_at=now, epoch=self.epoch)
        receipt_json = canonical(receipt.model_dump(mode="json"))
        def write(session):
            if self._identity(session, run_id) != (head, identity):
                raise ValueError("report_audit_expected_head_changed")
            row = session.get(Job, audit_id, populate_existing=True)
            if row is not None:
                prior = self._validated(row, run_id, command.request_key, owner_id)
                if prior.request != command:
                    raise ValueError("report_audit_key_reused")
                return False
            if session.scalar(select(Job.audit_id).where(Job.active_slot == "native").limit(1)) is not None:
                raise ValueError("report_audit_native_slot_busy")
            session.add(Job(audit_id=audit_id, run_id=run_id, request_key=command.request_key, owner_id=owner_id,
                identity_json=identity, identity_hash=head.identity_sha256, receipt_json=receipt_json,
                receipt_hash=digest(receipt_json), epoch=self.epoch, active_slot="native", state="accepted", version=1,
                created_at=now, updated_at=now, native_exited=0))
            return True
        created = self._write(audit_id, write)
        found = self.status(run_id, command.request_key, owner_id=owner_id)
        if found.receipt.request != command or self.snapshot(run_id) != (head, identity):
            raise ValueError("report_audit_accept_readback_changed")
        if created:
            self.loop.call_soon_threadsafe(self._launch, audit_id)
        return found.receipt

    async def start(self):
        self.loop = asyncio.get_running_loop()
        # A lost epoch is never reclaimed. No OS-exit assertion is invented.
        def obsolete(session):
            for row in session.scalars(select(Job).where(Job.active_slot == "native", Job.epoch != self.epoch)):
                row.state, row.reason_code = "unknown", "report_audit_epoch_lost"
                row.version += 1
                row.updated_at = self.clock()
        self._write(self.epoch, obsolete)

    def _launch(self, audit_id):
        if self.task is not None and not self.task.done():
            return
        self.task = asyncio.create_task(self._run(audit_id))
        self.task.add_done_callback(lambda task: None if task.cancelled() else task.exception())

    @staticmethod
    def _failure_code(error):
        # Preserve original reader's bounded machine code, never SQL, paths,
        # arguments or body. Unknown exceptions retain their safe class code.
        if (type(error) in {ValueError, PermissionError, TimeoutError}
                and len(error.args) == 1 and type(error.args[0]) is str
                and re.fullmatch(r"[a-z0-9_]{1,160}", error.args[0])):
            return error.args[0]
        return "report_audit_" + (type(error).__name__.lower() if type(error) in {
            KeyError, OSError, TypeError, RuntimeError, asyncio.CancelledError,
        } else "original_verification_failed")

    def _transition(self, audit_id, states, action):
        def write(session):
            row = session.get(Job, audit_id, populate_existing=True)
            if row is None or row.epoch != self.epoch or row.active_slot != "native" or row.state not in states:
                raise ValueError("report_audit_once_consumption_changed")
            action(session, row)
            row.version += 1
            row.updated_at = self.clock()
        self._write(audit_id, write)

    def _native(self, audit_id):
        now = self.clock()
        def begin(session, row):
            receipt = self._validated(row, row.run_id, row.request_key, row.owner_id)
            if self._identity(session, row.run_id)[1] != row.identity_json:
                raise ValueError("report_audit_expected_head_changed")
            if row.started_at is not None:
                raise ValueError("report_audit_once_consumption_changed")
            row.state, row.started_at = "running", now
            row.deadline_at = now + timedelta(seconds=AUDIT_TOTAL_SECONDS)
        self._transition(audit_id, {"accepted"}, begin)
        with self.lifecycle._ro() as session:
            row = session.get(Job, audit_id, populate_existing=True)
            receipt = self._validated(row, row.run_id, row.request_key, row.owner_id)
            identity = row.identity_json
        head, _ = self.snapshot(receipt.request.expected_head.run_id)
        if head != receipt.request.expected_head:
            raise ValueError("report_audit_expected_head_changed")
        bundle, serving = self.lifecycle.read_with_head(head.run_id)
        if bundle is None or serving["state"] != "confirmed" or serving["state_version"] != head.state_version:
            raise ValueError("report_audit_confirmed_identity_required")
        view = self.lifecycle.audit_original(head.run_id, as_of=self.clock())
        if view.status != "available" or view.decision is None:
            raise ValueError("report_audit_original_unavailable")
        if bundle.quality(read_at=self.clock()).model_dump(mode="json", exclude={"as_of"}) != view.model_dump(mode="json", exclude={"as_of"}):
            raise ValueError("research_quality_full_audit_changed")
        final_bundle, final_serving = self.lifecycle.read_with_head(head.run_id)
        if final_bundle != bundle or final_serving != serving or self.snapshot(head.run_id) != (head, identity):
            raise ValueError("report_audit_serving_head_changed")
        result = ReportAuditResultV1(audit_id=audit_id, head=head, checked_at=view.as_of, quality_json=view.model_dump_json())
        raw = canonical(result.model_dump(mode="json"))
        if not 0 < len(raw.encode("utf-8")) <= RESULT_BOUND:
            raise ValueError("report_audit_result_bound_exceeded")
        return raw

    async def _run(self, audit_id):
        deadline_monotonic = time.monotonic() + AUDIT_TOTAL_SECONDS
        native = asyncio.create_task(self.coordinator.run_once(lambda: self._native(audit_id)))
        timed_out = False
        try:
            done, _ = await asyncio.wait({native}, timeout=AUDIT_TOTAL_SECONDS)
            if not done:
                timed_out = True
                def deadline(session, row):
                    row.state, row.reason_code = "failed", "report_audit_total_budget_exceeded"
                    row.finished_at = self.clock()
                self._transition(audit_id, {"accepted", "running"}, deadline)
            raw = await asyncio.shield(native)
            if timed_out:
                raise TimeoutError("report_audit_total_budget_exceeded")
            await asyncio.to_thread(self._finish, audit_id, raw, deadline_monotonic)
        except BaseException as error:
            # A logical timeout holds the slot until BOTH native finally and
            # concurrent Future.done are really observed by the coordinator.
            while not native.done():
                try:
                    await asyncio.shield(native)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            future = self.coordinator.native_future
            exited = self.coordinator.native_exit.is_set() and future is not None and future.done()
            try:
                if audit_id not in self.uncertain:
                    def failed(session, row):
                        row.state = "failed" if exited else "unknown"
                        row.reason_code = "report_audit_total_budget_exceeded" if timed_out else self._failure_code(error)
                        row.finished_at = row.finished_at or self.clock()
                        if exited:
                            row.native_exited, row.native_exit_observed_at, row.active_slot = 1, self.clock(), None
                    self._transition(audit_id, {"accepted", "running", "prepared", "failed"}, failed)
            except BaseException:
                logging.getLogger(__name__).warning("report_audit_terminal_write_unconfirmed audit_sha256=%s", audit_id)
            logging.getLogger(__name__).warning("report_audit_failed audit_sha256=%s exception_class=%s native_exited=%s",
                audit_id, type(error).__name__, exited)

    def _finish(self, audit_id, raw, deadline_monotonic):
        future = self.coordinator.native_future
        if future is None or not future.done() or not self.coordinator.native_exit.is_set():
            raise ValueError("report_audit_native_exit_not_observed")
        now = self.clock()
        def prepared(session, row):
            if (as_utc(row.deadline_at) is None or now > as_utc(row.deadline_at)
                    or time.monotonic() > deadline_monotonic):
                raise TimeoutError("report_audit_total_budget_exceeded")
            row.state, row.result_json, row.result_hash, row.result_bytes = "prepared", raw, digest(raw), len(raw.encode("utf-8"))
            row.native_exited, row.native_exit_observed_at = 1, now
        self._transition(audit_id, {"running"}, prepared)
        with self.lifecycle._ro() as session:
            row = session.get(Job, audit_id, populate_existing=True)
            receipt = self._validated(row, row.run_id, row.request_key, row.owner_id)
            result = self._result(row, receipt)
            identity = row.identity_json
        if self.snapshot(result.head.run_id) != (result.head, identity):
            raise ValueError("report_audit_serving_head_changed")
        def confirmed(session, row):
            if self._identity(session, row.run_id) != (result.head, identity) or row.result_json != raw:
                raise ValueError("report_audit_result_changed")
            if self.clock() > as_utc(row.deadline_at) or time.monotonic() > deadline_monotonic:
                raise TimeoutError("report_audit_total_budget_exceeded")
            if session.get(Ack, audit_id) is not None:
                raise ValueError("report_audit_confirmation_forbidden")
            row.state, row.finished_at, row.active_slot = "completed", self.clock(), None
            session.add(Ack(audit_id=audit_id, identity_hash=row.identity_hash, receipt_hash=row.receipt_hash,
                result_hash=row.result_hash, result_bytes=row.result_bytes, confirmed_at=row.finished_at))
        self._transition(audit_id, {"prepared"}, confirmed)
        # Independent receipt/result/native/head readback, no repeated audit.
        self.result(result.head.run_id, receipt.request.request_key, owner_id=receipt.request.owner_id)

    async def close(self):
        self.loop = None
        if self.task is not None:
            await asyncio.shield(self.task)
        self.coordinator.close()
