"""One publication: prepare, independent confirm, and bounded frozen serving.

An uninstalled business candidate. No endpoint or worker imports this module.
The internal job owner is separate from a terminal Run lease and from product
read access. Full original verification/replay is a prerequisite supplied by
the producer, never a boolean request argument or an end-user token.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import traceback
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from ..persistence.db import Database, RunRecord, RunRoleOutputRecord, as_utc, utcnow
from ..persistence.frozen_publication_material import verify_material_file
from ..persistence.research_publication_bundle_records import (
    PublicationBundleConfirmationRecord as Ack,
    PublicationBundleJobRecord as Job,
    PublicationBundleRecord as Saved,
)
from ..ports.research_publication_bundle import FrozenPublicationBundleV1
from ..ports.research_observation import ObservedExecutionRef
from .research_quality_view import ResearchQualityViewV1
from .commit import PUBLICATION_SOURCE_ROLE
from .research_publication_frozen_reader import FrozenPublicationPreparer, PreparedFrozenPublication


def encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


PUBLICATION_PHASES49 = frozenset({
    "produce", "preparation_verify", "material_import", "bundle_put",
    "prepared_cas", "prepared_readback", "confirmation_verify",
    "confirmed_cas", "confirmed_readback", "final_verify", "consume",
})
DIAGNOSTIC_BOUND49 = 2 * 1024 * 1024
DIAGNOSTIC_COUNT49 = 16


class BundleCommitUnconfirmed(RuntimeError):
    def __init__(self, phase: str):
        self.phase = phase
        super().__init__("publication_bundle_" + phase + "_commit_unconfirmed")


class BundleWriterBeginBusy(RuntimeError):
    """Exact native SQLITE_BUSY before the first writer BEGIN acquired any lock.

    Never denotes business SQL or uncertain COMMIT; retry stays with the
    existing bounded worker cadence and durable head, not this call.
    """
    def __init__(self, phase: str):
        self.phase = phase
        super().__init__("publication_bundle_writer_begin_busy")


class BundleArchive:
    """Exclusive content files and durable pre-COMMIT fences, outside writers."""
    def __init__(self, root: Path, *, max_bundle_bytes: int, max_material_bytes: int):
        if (type(max_bundle_bytes) is not int or max_bundle_bytes < 1
                or type(max_material_bytes) is not int or max_material_bytes < 1):
            raise ValueError("publication_bundle_explicit_bound_required")
        self.root, self.max_bundle_bytes = root.absolute(), max_bundle_bytes
        self.max_material_bytes = max_material_bytes
        self._check_root()
        self.root.mkdir(parents=True, exist_ok=True)

    def _check_root(self) -> None:
        if ".." in self.root.parts:
            raise ValueError("publication_bundle_archive_reference_invalid")
        current = Path(self.root.anchor)
        for part in self.root.parts[1:]:
            current /= part
            try:
                status = current.lstat()
            except FileNotFoundError:
                continue
            if current.is_symlink() or getattr(status, "st_file_attributes", 0) & 0x400:
                raise ValueError("publication_bundle_archive_link_forbidden")

    def _path(self, relative: str) -> Path:
        self._check_root()
        if not re.fullmatch(r"(?:bundles|fences|materials|diagnostics)/[a-f0-9]{64}\.json", relative):
            raise ValueError("publication_bundle_archive_reference_invalid")
        path = self.root
        for part in relative.split("/"):
            path = path / part
            try:
                status = path.lstat()
            except FileNotFoundError:
                continue
            else:
                if path.is_symlink() or getattr(status, "st_file_attributes", 0) & 0x400:
                    raise ValueError("publication_bundle_archive_link_forbidden")
                if path.is_file() and getattr(status, "st_nlink", 1) != 1:
                    raise ValueError("publication_bundle_archive_link_forbidden")
        return path

    def verify_material(self, bundle: FrozenPublicationBundleV1) -> None:
        # External native material is a mandatory bundle constituent. This
        # executes at both independent serving snapshots, not only at save.
        path = self._path(bundle.material_ref)
        verify_material_file(path, expected_sha256=bundle.material_sha256,
            expected_bytes=bundle.material_bytes, max_bytes=self.max_material_bytes)
        self._path(bundle.material_ref)  # Recheck ancestor path after IO.

    def import_material(self, bundle: FrozenPublicationBundleV1, source: Path) -> None:
        path = self._path(bundle.material_ref)
        if path.exists():
            self.verify_material(bundle)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path(bundle.material_ref)
        stage = path.parent / (".partial-" + uuid4().hex)
        try:
            with stage.open("xb") as stream:
                verify_material_file(source, expected_sha256=bundle.material_sha256,
                    expected_bytes=bundle.material_bytes, max_bytes=self.max_material_bytes, sink=stream)
                stream.flush()
                os.fsync(stream.fileno())
            self._path(bundle.material_ref)
            try:
                os.link(stage, path)  # Atomic no-overwrite; never replace another file.
            except FileExistsError:
                pass
        finally:
            stage.unlink(missing_ok=True)
        self.verify_material(bundle)

    def exclusive(self, relative: str, raw: bytes) -> None:
        if len(raw) > self.max_bundle_bytes:
            raise ValueError("publication_bundle_archive_bound_exceeded")
        path = self._path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

    def put(self, bundle: FrozenPublicationBundleV1) -> tuple[str, str, int]:
        raw = encoded(bundle.model_dump(mode="json"))
        if len(raw) > self.max_bundle_bytes:
            raise ValueError("publication_bundle_archive_bound_exceeded")
        digest, reference = sha(raw), "bundles/" + sha(raw) + ".json"
        path = self._path(reference)
        if path.exists():
            if path.read_bytes() != raw:
                raise ValueError("publication_bundle_archive_hash_collision")
        else:
            self.exclusive(reference, raw)
        return reference, digest, len(raw)

    def load(self, reference: str, digest: str, size: int) -> FrozenPublicationBundleV1:
        path = self._path(reference)
        if type(size) is not int or size < 1 or size > self.max_bundle_bytes:
            raise ValueError("publication_bundle_archive_bound_exceeded")
        if path.stat().st_size != size:
            raise ValueError("publication_bundle_saved_bytes_changed")
        with path.open("rb") as stream:
            raw = stream.read(size + 1)
        if len(raw) != size or sha(raw) != digest:
            raise ValueError("publication_bundle_saved_bytes_changed")
        value = FrozenPublicationBundleV1.model_validate_json(raw)
        if encoded(value.model_dump(mode="json")) != raw:
            raise ValueError("publication_bundle_saved_canonical_changed")
        return value

    def fence_ref(self, run_id: str) -> str:
        return "fences/" + sha(run_id.encode()) + ".json"

    def fenced(self, run_id: str) -> bool:
        return self._path(self.fence_ref(run_id)).exists()

    def release(self, run_id: str, body: bytes) -> None:
        path = self._path(self.fence_ref(run_id))
        if path.read_bytes() != body:
            raise ValueError("publication_bundle_commit_fence_changed")
        path.unlink()

    def diagnostic_ref(self, claim: BundleClaim) -> str:
        identity = {"schema_version":"publication-failure-identity.v1",
            "run_id":claim.run_id,"request_id":claim.request_id,"generation":claim.generation}
        return "diagnostics/" + sha(encoded(identity)) + ".json"

    def put_diagnostic(self, claim: BundleClaim, body: dict) -> tuple[str, str]:
        raw = encoded(body)
        if len(raw) > DIAGNOSTIC_BOUND49:
            raise ValueError("publication_failure_diagnostic_bound_exceeded")
        reference = self.diagnostic_ref(claim)
        lock_ref = "diagnostics/" + "0" * 64 + ".json"
        lock_body = encoded({"schema_version":"publication-diagnostic-budget-lock.v1","owner":uuid4().hex})
        self.exclusive(lock_ref,lock_body)
        try:
            return self._put_diagnostic_locked(reference,raw,lock_ref)
        finally:
            lock = self._path(lock_ref)
            if lock.read_bytes()!=lock_body:
                raise ValueError("publication_failure_diagnostic_budget_lock_changed")
            lock.unlink()

    def _put_diagnostic_locked(self, reference: str, raw: bytes, lock_ref: str):
        directory = self._path(reference).parent
        count = total = 0
        if directory.exists():
            for path in directory.iterdir():
                if path.name=="0"*64+".json":
                    continue
                count += 1
                if count >= DIAGNOSTIC_COUNT49:
                    raise ValueError("publication_failure_diagnostic_inventory_bound_exceeded")
                candidate = self._path("diagnostics/" + path.name)
                size = candidate.stat().st_size
                if not candidate.is_file() or not 0 < size <= DIAGNOSTIC_BOUND49:
                    raise ValueError("publication_failure_diagnostic_inventory_invalid")
                total += size
                if total + len(raw) > DIAGNOSTIC_COUNT49 * DIAGNOSTIC_BOUND49:
                    raise ValueError("publication_failure_diagnostic_inventory_bound_exceeded")
        self.exclusive(reference, raw)  # First failure only; no overwrite or pruning.
        path = self._path(reference)
        if path.stat().st_size != len(raw) or path.read_bytes() != raw:
            raise ValueError("publication_failure_diagnostic_readback_changed")
        return reference, sha(raw)

    def load_diagnostic(self, claim: BundleClaim) -> tuple[dict, str] | None:
        path = self._path(self.diagnostic_ref(claim))
        if not path.exists():
            return None
        if not 0 < path.stat().st_size <= DIAGNOSTIC_BOUND49:
            raise ValueError("publication_failure_diagnostic_bound_exceeded")
        with path.open("rb") as stream:
            raw = stream.read(DIAGNOSTIC_BOUND49 + 1)
        value = json.loads(raw)
        if type(value) is not dict or len(raw) > DIAGNOSTIC_BOUND49 or encoded(value) != raw:
            raise ValueError("publication_failure_diagnostic_canonical_changed")
        recheck = self._path(self.diagnostic_ref(claim))
        if not 0 < recheck.stat().st_size <= DIAGNOSTIC_BOUND49:
            raise ValueError("publication_failure_diagnostic_bound_exceeded")
        with recheck.open("rb") as stream:
            second = stream.read(DIAGNOSTIC_BOUND49 + 1)
        if second != raw:
            raise ValueError("publication_failure_diagnostic_changed")
        return value, sha(raw)

    def original_archive_empty(self) -> bool:
        """Prove global zero entries; never infer ownership or prune orphans."""
        for kind in ("bundles", "materials"):
            directory = self.root / kind
            self._path(kind + "/" + "0" * 64 + ".json")
            if directory.exists():
                if not directory.is_dir():
                    raise ValueError("publication_failure_archive_kind_changed")
                if next(directory.iterdir(), None) is not None:
                    return False
        return True


@dataclass(frozen=True)
class BundleClaim:
    run_id: str
    request_id: str
    owner: str
    generation: int


class PublicationBundleLifecycle:
    def __init__(self, database: Database, archive: BundleArchive, *,
                 preparer: FrozenPublicationPreparer,
                 clock: Callable[[], datetime] = utcnow):
        self.database, self.archive, self.clock, self.preparer = database, archive, clock, preparer
        self._diagnostic_scope49 = ContextVar("publication_diagnostic_scope49", default=None)
        path = Path(database.engine.url.database or "").resolve(strict=True)
        if database.engine.dialect.name != "sqlite" or not path.is_file():
            raise ValueError("publication_bundle_physical_sqlite_required")
        # Never instantiate Database for readonly access: its startup PRAGMAs
        # and automatic exit COMMIT are inappropriate for original reads.
        self.read_engine = create_engine("sqlite+pysqlite://", poolclass=NullPool,
            creator=lambda: sqlite3.connect(path.as_uri() + "?mode=ro", uri=True))

    def close(self) -> None:
        self.read_engine.dispose()

    @contextmanager
    def diagnostic_scope(self, claim: BundleClaim):
        token = self._diagnostic_scope49.set({"claim":claim,"phase":"produce"})
        try:
            yield
        finally:
            self._diagnostic_scope49.reset(token)

    def _phase49(self, phase: str) -> None:
        if phase not in PUBLICATION_PHASES49:
            raise ValueError("publication_failure_phase_invalid")
        scope = self._diagnostic_scope49.get()
        if scope is not None:
            scope["phase"] = phase

    def record_failure_diagnostic(self, claim: BundleClaim, error: Exception):
        scope = self._diagnostic_scope49.get()
        if scope is None or scope["claim"] != claim or scope["phase"] not in PUBLICATION_PHASES49:
            raise ValueError("publication_failure_diagnostic_scope_required")
        with self._ro() as session:
            job = session.get(Job,claim.run_id,populate_existing=True)
            if job is None:
                raise ValueError("publication_failure_diagnostic_job_missing")
            head = {"run_id":job.run_id,"request_id":job.request_id,"generation":job.generation,
                "owner":job.owner,"state":job.state,"state_version":job.state_version,
                "bundle_hash":job.bundle_hash,"selection_native_hash":job.selection_native_hash,
                "publication_hash":job.publication_hash}
        body = {"schema_version":"publication-worker-private-failure.v1",
            "claim":{"run_id":claim.run_id,"request_id":claim.request_id,
                "generation":claim.generation,"owner":claim.owner},
            "phase":scope["phase"],"head":head,"occurred_at":self.clock().isoformat(),
            "exception_type":type(error).__module__+"."+type(error).__qualname__,
            "exception_message":str(error),"traceback":"".join(traceback.format_exception(error)),
            "fenced_at_error":self.archive.fenced(claim.run_id)}
        return self.archive.put_diagnostic(claim,body)

    @contextmanager
    def _archive_admission49(self, claim: BundleClaim):
        """One per-Run fence blocks claim/retry while archive bytes are written.

        A stale producer must revalidate its normal owner/generation/lease
        after taking this fence, before touching archive files. No COMMIT
        occurs under this admission; crashes retain its fence for RO review.
        """
        body=encoded({"schema_version":"publication-bundle-commit-fence.v1",
            "run_id":claim.run_id,"request_id":claim.request_id,"generation":claim.generation,
            "phase":"archive_preparation","bundle_hash":"0"*64})
        try:
            self.archive.exclusive(self.archive.fence_ref(claim.run_id),body)
        except FileExistsError as error:
            raise ValueError("publication_bundle_fence_requires_readonly_reconciliation") from error
        try:
            with self._ro() as session:
                self._owned(session,claim,"queued")
            yield
            with self._ro() as session:
                self._owned(session,claim,"queued")
        finally:
            self.archive.release(claim.run_id,body)

    @contextmanager
    def _ro(self) -> Generator[Session, None, None]:
        with Session(self.read_engine) as session:
            session.connection().exec_driver_sql("PRAGMA query_only=ON")
            session.connection().exec_driver_sql("BEGIN")
            native = session.connection().connection.driver_connection
            assert isinstance(native, sqlite3.Connection)
            changes = native.total_changes
            try:
                yield session
                if native.total_changes != changes or not native.in_transaction:
                    raise ValueError("publication_bundle_independent_read_changed")
            finally:
                session.rollback()

    def _publication(self, session: Session, job: Job,
                     bundle: FrozenPublicationBundleV1 | None = None) -> None:
        run = session.get(RunRecord, job.run_id, populate_existing=True)
        rows = list(session.scalars(select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == job.run_id, RunRoleOutputRecord.role == PUBLICATION_SOURCE_ROLE
        ).execution_options(populate_existing=True)))
        if (run is None or run.status not in {"completed", "degraded"}
                or run.event_id != job.event_id or run.artifact_id != job.artifact_id
                or run.finished_at is None or len(rows) != 1):
            raise ValueError("publication_bundle_current_publication_changed")
        source = rows[0]
        if (source.output_id != job.publication_id or source.payload_hash != job.publication_hash
                or source.schema_version != job.publication_schema
                or as_utc(source.created_at) != as_utc(job.publication_created_at)
                or sha(source.payload_json.encode()) != job.publication_hash):
            raise ValueError("publication_bundle_current_publication_changed")
        body = json.loads(source.payload_json)
        if (type(body) is not dict or body.get("schema_version") != job.publication_schema
                or body.get("run_id") != job.run_id or body.get("event_id") != job.event_id
                or body.get("artifact_id") != job.artifact_id):
            raise ValueError("publication_bundle_current_publication_changed")
        if bundle is not None:
            quality = ResearchQualityViewV1.model_validate_json(bundle.quality_json)
            binding = body.get("quality_binding")
            final = body.get("graph_final_ref")
            if not isinstance(binding, dict) or not isinstance(final, dict):
                raise ValueError("publication_bundle_current_quality_binding_changed")
            original_final = ObservedExecutionRef.model_validate_json(encoded(final.get("row")))
            if (quality.decision is None or bundle.report_sha256 != binding.get("source_result_hash")
                    or quality.decision.decision_hash != binding.get("quality_decision_hash")
                    or quality.quality_sidecar_id != binding.get("quality_sidecar_id")
                    or quality.graph_final_ref != original_final
                    or quality.gate_status != body.get("gate", {}).get("status")):
                raise ValueError("publication_bundle_current_quality_binding_changed")

    def _owned(self, session: Session, claim: BundleClaim, state: str) -> Job:
        job = session.get(Job, claim.run_id, populate_existing=True)
        if (job is None or job.request_id != claim.request_id or job.owner != claim.owner
                or job.generation != claim.generation or job.state != state
                or as_utc(job.lease_expires_at) is None
                or as_utc(job.lease_expires_at) <= self.clock()):
            raise PermissionError("publication_bundle_internal_claim_lost")
        self._publication(session, job)
        return job

    def enqueue(self, run_id: str, *, owner: str, request_id: str, lease_seconds: int) -> BundleClaim:
        """Internal worker/backfill command; this is not a user read ACL."""
        if not owner or not request_id or type(lease_seconds) is not int or lease_seconds < 1:
            raise ValueError("publication_bundle_internal_claim_required")
        if self.archive.fenced(run_id):
            raise ValueError("publication_bundle_fence_requires_readonly_reconciliation")
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            if session.get(Job, run_id) is not None:
                raise ValueError("publication_bundle_existing_job_requires_explicit_recovery")
            run = session.get(RunRecord, run_id)
            sources = list(session.scalars(select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.run_id == run_id, RunRoleOutputRecord.role == PUBLICATION_SOURCE_ROLE)))
            if run is None or len(sources) != 1:
                raise ValueError("publication_bundle_publication_missing")
            source, now = sources[0], self.clock()
            job = Job(run_id=run_id, request_id=request_id, event_id=run.event_id,
                artifact_id=run.artifact_id, publication_id=source.output_id,
                publication_hash=source.payload_hash, publication_schema=source.schema_version,
                publication_created_at=source.created_at, generation=0, owner=owner,
                lease_expires_at=now + timedelta(seconds=lease_seconds), state="queued", updated_at=now)
            self._publication(session, job)
            session.add(job)
        return BundleClaim(run_id, request_id, owner, 0)

    def _attempt(self, claim: BundleClaim, phase: str, bundle_hash: str,
                 body: dict, write: Callable[[Session], None]) -> bytes:
        fence = encoded({"schema_version": "publication-bundle-commit-fence.v1",
            "run_id": claim.run_id, "request_id": claim.request_id,
            "generation": claim.generation, "phase": phase, "bundle_hash": bundle_hash, **body})
        try:
            self.archive.exclusive(self.archive.fence_ref(claim.run_id), fence)
        except FileExistsError as error:
            raise ValueError("publication_bundle_fence_requires_readonly_reconciliation") from error
        committing = False
        try:
            with self.database.factory() as session:
                writer_connection = session.connection()
                try:
                    writer_connection.exec_driver_sql("BEGIN IMMEDIATE")
                except OperationalError as error:
                    native = error.orig
                    if (error.statement == "BEGIN IMMEDIATE"
                            and isinstance(native, sqlite3.OperationalError)
                            and type(getattr(native, "sqlite_errorcode", None)) is int
                            and native.sqlite_errorcode == sqlite3.SQLITE_BUSY):
                        raise BundleWriterBeginBusy(phase) from error
                    raise
                write(session)
                session.flush()
                committing = True
                session.commit()
        except BaseException as error:
            if committing:
                raise BundleCommitUnconfirmed(phase) from error
            self.archive.release(claim.run_id, fence)
            raise
        return fence

    def save_verified(self, claim: BundleClaim,
                      prepared: PreparedFrozenPublication) -> FrozenPublicationBundleV1:
        """Called only by the producer holding its one-call frozen-read proof.

        The issuer's same-process, same-object proof is mandatory. User JSON
        and two self-equal outputs do not manufacture a preparation proof.
        """
        def verify_preparation():
            self.preparer.verify(prepared)

        self._phase49("preparation_verify")
        verify_preparation()
        bundle = prepared.value
        self._phase49("material_import")
        with self._archive_admission49(claim):
            self.archive.import_material(bundle, self.preparer.material_source(prepared))
            self._phase49("bundle_put")
            reference, digest, size = self.archive.put(bundle)
            verify_preparation()

        def prepare(session):
            job = self._owned(session, claim, "queued")
            if (bundle.run_id != job.run_id or bundle.event_id != job.event_id
                    or bundle.artifact_id != job.artifact_id
                    or bundle.publication_ref.output_id != job.publication_id
                    or bundle.publication_ref.payload_hash != job.publication_hash
                    or bundle.publication_ref.schema_version != job.publication_schema
                    or bundle.original_published_at != as_utc(job.publication_created_at)):
                raise ValueError("publication_bundle_prepare_identity_changed")
            session.add(Saved(bundle_hash=digest, run_id=claim.run_id, request_id=claim.request_id,
                generation=claim.generation, bundle_ref=reference, bundle_bytes=size,
                material_ref=bundle.material_ref, material_hash=bundle.material_sha256,
                material_bytes=bundle.material_bytes,
                report_hash=bundle.report_sha256, report_bytes=bundle.report_bytes,
                reader_hash=bundle.trusted_reader_source_sha256, prepared_at=self.clock()))
            job.state, job.bundle_hash, job.updated_at = "prepared", digest, self.clock()
            self._publication(session, job, bundle)

        self._phase49("prepared_cas")
        first_fence = self._attempt(claim, "prepared", digest, {}, prepare)
        # File/hash/structure readback is outside the writer and on a new
        # physical query-only connection. It authorizes only the next CAS.
        self._phase49("prepared_readback")
        with self._ro() as session:
            self._owned(session, claim, "prepared")
            saved = session.get(Saved, digest)
            if saved is None or self._load_saved(saved) != bundle:
                raise ValueError("publication_bundle_prepared_readback_changed")
        self.archive.release(claim.run_id, first_fence)
        return self._confirm_verified(claim, prepared)

    def confirm_saved_verified(self, claim: BundleClaim,
                               prepared: PreparedFrozenPublication) -> FrozenPublicationBundleV1:
        """Normal worker continuation of an exact already-Saved preparation.

        The normal issuer must freshly consume the original frozen material.
        This entry neither inserts Saved nor imports or rewrites archive bytes.
        """
        self.preparer.verify(prepared)
        bundle = prepared.value
        native_reader = getattr(self, "_prepared_confirmation_native74", None)
        if not callable(native_reader):
            raise ValueError("publication_confirmation_normal_durable_worker_required")
        if self.archive.fenced(claim.run_id):
            raise ValueError("publication_bundle_fence_requires_readonly_reconciliation")
        with self._ro() as session:
            job = self._owned(session, claim, "prepared")
            saved = session.get(Saved, job.bundle_hash, populate_existing=True)
            if (saved is None or saved.run_id != claim.run_id
                    or saved.request_id != claim.request_id or saved.generation != claim.generation
                    or saved.bundle_hash != sha(encoded(bundle.model_dump(mode="json")))
                    or session.get(Ack, claim.run_id, populate_existing=True) is not None
                    or self._load_saved(saved) != bundle):
                raise ValueError("publication_confirmation_saved_identity_changed")
            native = native_reader(session, claim.run_id)

        def verify_confirmation_head(session):
            if native_reader(session, claim.run_id) != native:
                raise ValueError("publication_confirmation_native_head_changed")

        return self._confirm_verified(claim, prepared,
                                      verify_confirmation_head=verify_confirmation_head)

    def _confirm_verified(self, claim: BundleClaim, prepared: PreparedFrozenPublication,
                          *, verify_confirmation_head=None) -> FrozenPublicationBundleV1:
        """The original confirmation tail shared by new save and Saved continuation."""
        def verify_preparation():
            self.preparer.verify(prepared)

        bundle = prepared.value
        digest = sha(encoded(bundle.model_dump(mode="json")))
        # A fully read-back known prepared COMMIT is no longer unknown.
        # Cancellation may now acquire its own durable fence and win the
        # following confirmation CAS; it never overwrites an uncertain fence.
        self._phase49("confirmation_verify")
        verify_preparation()
        confirmed_at = self.clock()

        def confirm(session):
            job = self._owned(session, claim, "prepared")
            if verify_confirmation_head is not None:
                verify_confirmation_head(session)
            if job.bundle_hash != digest or session.get(Ack, claim.run_id) is not None:
                raise ValueError("publication_bundle_confirmation_identity_changed")
            session.add(Ack(run_id=claim.run_id, request_id=claim.request_id,
                generation=claim.generation, bundle_hash=digest, confirmed_at=confirmed_at))
            job.state, job.confirmed_at, job.updated_at = "confirmed", confirmed_at, self.clock()
            job.owner, job.lease_expires_at = None, None
            self._publication(session, job, bundle)

        self._phase49("confirmed_cas")
        second_fence = self._attempt(claim, "confirmed", digest,
                                     {"confirmed_at": confirmed_at.isoformat()}, confirm)
        self._phase49("confirmed_readback")
        with self._ro() as session:
            loaded, _ = self._confirmed(session, claim.run_id, allow_fence=True)
            if loaded != bundle:
                raise ValueError("publication_bundle_confirmation_readback_changed")
        self._phase49("final_verify")
        verify_preparation()
        self.archive.release(claim.run_id, second_fence)
        self._phase49("consume")
        self.preparer.consume(prepared)
        return bundle

    def _load_saved(self, saved: Saved) -> FrozenPublicationBundleV1:
        bundle = self.archive.load(saved.bundle_ref, saved.bundle_hash, saved.bundle_bytes)
        if (bundle.run_id != saved.run_id or bundle.material_ref != saved.material_ref
                or bundle.material_sha256 != saved.material_hash
                or bundle.material_bytes != saved.material_bytes
                or bundle.report_sha256 != saved.report_hash or bundle.report_bytes != saved.report_bytes
                or bundle.trusted_reader_source_sha256 != saved.reader_hash):
            raise ValueError("publication_bundle_saved_index_changed")
        self.archive.verify_material(bundle)
        return bundle

    def _confirmed(self, session: Session, run_id: str, *, allow_fence=False):
        job = session.get(Job, run_id, populate_existing=True)
        if job is None:
            return None, None  # The sole explicit no-bundle compatibility branch.
        if (job.state != "confirmed" or not job.bundle_hash
                or (not allow_fence and self.archive.fenced(run_id))):
            raise ValueError("publication_bundle_not_independently_confirmed")
        saved, ack = session.get(Saved, job.bundle_hash), session.get(Ack, run_id)
        if (saved is None or ack is None or saved.request_id != job.request_id
                or saved.generation != job.generation or ack.request_id != job.request_id
                or ack.generation != job.generation or ack.bundle_hash != job.bundle_hash
                or as_utc(ack.confirmed_at) != as_utc(job.confirmed_at)):
            raise ValueError("publication_bundle_confirmation_changed")
        bundle = self._load_saved(saved)
        self._publication(session, job, bundle)
        if (bundle.artifact_id != job.artifact_id or bundle.event_id != job.event_id
                or bundle.publication_ref.output_id != job.publication_id
                or bundle.publication_ref.payload_hash != job.publication_hash):
            raise ValueError("publication_bundle_confirmation_identity_changed")
        identity = (job.request_id, job.generation, job.bundle_hash, as_utc(ack.confirmed_at))
        return bundle, identity

    def _confirmed_report_snapshot(self, session: Session, run_id: str):
        """Distinct read scope: confirmed report bytes, no fresh material audit.

        Saved/ACK originate only from save_verified: full producer proof and
        material verification at save, prepared readback, and ACK readback.
        This scope preserves that confirmation plus current publication and
        revocation checks. It never substitutes for _confirmed or read().
        """
        job = session.get(Job, run_id, populate_existing=True)
        if job is None:
            return None, None
        if (job.state != "confirmed" or not job.bundle_hash
                or self.archive.fenced(run_id)):
            raise ValueError("report_snapshot_not_independently_confirmed")
        saved = session.get(Saved, job.bundle_hash, populate_existing=True)
        ack = session.get(Ack, run_id, populate_existing=True)
        if (saved is None or ack is None or saved.run_id != run_id
                or saved.request_id != job.request_id or saved.generation != job.generation
                or ack.request_id != job.request_id or ack.generation != job.generation
                or ack.bundle_hash != job.bundle_hash
                or as_utc(ack.confirmed_at) != as_utc(job.confirmed_at)):
            raise ValueError("report_snapshot_confirmation_changed")
        # The bounded bundle is independently hashed and structurally checked.
        # Full external material is deliberately outside THIS declared scope.
        bundle = self.archive.load(saved.bundle_ref, saved.bundle_hash, saved.bundle_bytes)
        if (bundle.run_id != saved.run_id or bundle.material_ref != saved.material_ref
                or bundle.material_sha256 != saved.material_hash
                or bundle.material_bytes != saved.material_bytes
                or bundle.report_sha256 != saved.report_hash or bundle.report_bytes != saved.report_bytes
                or bundle.trusted_reader_source_sha256 != saved.reader_hash):
            raise ValueError("report_snapshot_saved_index_changed")
        self._publication(session, job, bundle)
        if (bundle.artifact_id != job.artifact_id or bundle.event_id != job.event_id
                or bundle.publication_ref.output_id != job.publication_id
                or bundle.publication_ref.payload_hash != job.publication_hash):
            raise ValueError("report_snapshot_publication_identity_changed")
        identity = (job.request_id, job.generation, job.bundle_hash,
                    as_utc(ack.confirmed_at), job.state_version, job.state,
                    job.selection_native_hash)
        return bundle, identity

    def read(self, run_id: str) -> FrozenPublicationBundleV1 | None:
        with self._ro() as session:
            bundle, identity = self._confirmed(session, run_id)
        # New physical RO sees revocation/cancellation committed after the
        # entry snapshot. Its successful read is the serving decision point.
        with self._ro() as session:
            exit_bundle, exit_identity = self._confirmed(session, run_id)
        if exit_identity != identity or exit_bundle != bundle:
            raise ValueError("publication_bundle_serving_head_changed")
        return bundle

    def cancel_preparation(self, claim: BundleClaim) -> None:
        """Stop an internal freeze job; never cancel an already-published Run."""
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            job = session.get(Job, claim.run_id, populate_existing=True)
            if (job is None or job.request_id != claim.request_id or job.generation != claim.generation
                    or job.owner != claim.owner or job.state not in {"queued", "prepared"}):
                raise PermissionError("publication_bundle_cancel_claim_lost")
            changed = session.execute(update(Job).where(Job.run_id == claim.run_id,
                Job.generation == claim.generation, Job.state.in_(["queued", "prepared"]),
                Job.owner == claim.owner).values(state="cancelled", generation=claim.generation + 1,
                owner=None, lease_expires_at=None, updated_at=self.clock()))
            if changed.rowcount != 1:
                raise PermissionError("publication_bundle_cancel_CAS_lost")

    def reconciliation_state(self, run_id: str) -> str:
        """Readonly diagnosis only. No COMMIT retry, ACK or fence deletion."""
        path = self.archive._path(self.archive.fence_ref(run_id))
        if not path.exists():
            return "no_unknown_commit_fence"
        body = json.loads(path.read_bytes())
        if body.get("run_id") != run_id:
            raise ValueError("publication_bundle_commit_fence_changed")
        with self._ro() as session:
            job = session.get(Job, run_id)
            if job is None or job.request_id != body["request_id"]:
                return "unknown_commit_absent_or_different_requires_operator_review"
            if job.state in {"cancelled", "revoked"}:
                return "unknown_commit_blocked_by_current_head"
            if body.get("phase") == "archive_preparation":
                return "archive_preparation_incomplete_requires_operator_review"
            saved = session.get(Saved, body["bundle_hash"])
            if saved is None:
                return "unknown_commit_not_saved_requires_operator_review"
            self._publication(session, job)
            self._load_saved(saved)
            if body["phase"] == "prepared" and job.state == "prepared":
                return "saved_prepared_requires_explicit_confirmation_recovery"
            if body["phase"] == "confirmed" and job.state == "confirmed":
                self._confirmed(session, run_id, allow_fence=True)
                ack = session.get(Ack, run_id)
                if as_utc(ack.confirmed_at) != datetime.fromisoformat(body["confirmed_at"]):
                    raise ValueError("publication_bundle_commit_fence_changed")
                return "saved_confirmed_requires_explicit_fence_recovery"
            return "unknown_commit_state_requires_operator_review"
