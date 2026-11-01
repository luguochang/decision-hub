"""Bounded, durable original-input assessment; never submits research or rewrites it."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from time import monotonic
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import CursorResult

from packages.evals.analytical_assessment import AnalyticalAssessment
from packages.evals.assessment_basis import (
    LOADED_VERIFIER_VERSION,
    SCHEMA,
    build_assessment_basis,
    loaded_assessment_manifest_hash,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalAssessmentJobRecord as Job,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalOutlookRecord as Outlook,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, as_utc, utcnow
from packages.provider_adapters.research.raw_capture import RawBodyArchive


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class _AssessmentStopped(RuntimeError):
    pass


def _assessment_checkpoint(stopped: threading.Event, deadline: float | None) -> None:
    if stopped.is_set() or (deadline is not None and monotonic() >= deadline):
        raise _AssessmentStopped("assessment_deadline_or_cancelled")


class AnalyticalAssessmentService:
    """One bounded off-loop batch. Claims and projections survive process restarts."""

    def __init__(
        self, database: Database, *, archive: RawBodyArchive | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.database = database
        self.archive = archive
        self.clock = clock
        self._batch_lock = asyncio.Lock()

    def discover(self, now: datetime, manifest: str, *, limit: int = 128) -> int:
        """First forward originals are queued without deriving a success from old labels."""
        with self.database.session() as session:
            missing = session.execute(select(Outlook.outlook_id).outerjoin(
                Job, Job.outlook_id == Outlook.outlook_id,
            ).where(
                Outlook.cohort == "prospective", Outlook.recorded_at <= now,
                Job.outlook_id.is_(None),
            ).order_by(
                case((Outlook.method_family == "ai_synthesis", 0), else_=1),
                Outlook.recorded_at, Outlook.outlook_id,
            ).limit(limit)).scalars().all()
            if missing:
                session.execute(insert(Job).values([{
                    "outlook_id": identity, "status": "pending", "attempt": 0,
                    "next_attempt_at": now, "verifier_version": manifest,
                } for identity in missing]).on_conflict_do_nothing(index_elements=["outlook_id"]))
            session.execute(update(Job).where(
                Job.status == "running", Job.lease_expires_at <= now,
            ).values(status="retry", lease_owner=None, lease_expires_at=None,
                     next_attempt_at=now, error_code="assessment_lease_expired"))
            # A pre-release worker can have written a new disk hash with old
            # loaded code. Missing/new ABI identity must schedule a real build,
            # even when that old completed row has a future next_attempt_at.
            def basis_field(path: str):
                return case(
                    (func.json_valid(Job.basis_json), func.json_extract(Job.basis_json, path)),
                    else_=None,
                )

            stale_basis = (
                basis_field("$.schema_version").is_distinct_from(SCHEMA)
                | basis_field("$.loaded_verifier.schema_version").is_distinct_from(
                    LOADED_VERIFIER_VERSION
                )
                | basis_field("$.loaded_verifier.manifest_hash").is_distinct_from(manifest)
            )
            elapsed_boundary = (
                func.julianday(basis_field("$.next_time_boundary_at")) <= func.julianday(now)
            )
            session.execute(update(Job).where(
                Job.status == "completed",
                (Job.verifier_version != manifest) | (Job.next_attempt_at <= now)
                | stale_basis | elapsed_boundary,
            ).values(status="pending", next_attempt_at=now, verifier_version=manifest))
            return len(missing)

    def claim(self, now: datetime, manifest: str) -> tuple[str, str, int] | None:
        with self.database.session() as session:
            row = session.execute(select(Job.outlook_id, Job.attempt).join(
                Outlook, Outlook.outlook_id == Job.outlook_id,
            ).where(
                Job.status.in_(("pending", "retry")), Job.next_attempt_at <= now,
            ).order_by(Job.next_attempt_at, Outlook.recorded_at, Job.outlook_id).limit(1)).first()
            if row is None:
                return None
            identity, old_attempt = row
            owner, attempt = "assessment_" + uuid4().hex, old_attempt + 1
            changed = cast(CursorResult[Any], session.execute(update(Job).where(
                Job.outlook_id == identity, Job.attempt == old_attempt,
                Job.status.in_(("pending", "retry")), Job.next_attempt_at <= now,
            ).values(
                status="running", attempt=attempt, lease_owner=owner,
                lease_expires_at=now + timedelta(minutes=2), verifier_version=manifest,
                error_code=None,
            ))).rowcount
            return (identity, owner, attempt) if changed == 1 else None

    def finish(
        self, identity: str, owner: str, attempt: int, *,
        assessment: AnalyticalAssessment | None = None, basis: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> bool:
        loaded_manifest = (
            loaded_assessment_manifest_hash()
            if basis is not None and assessment is not None
            else None
        )
        return self._finish_verified(
            identity, owner, attempt, assessment=assessment, basis=basis,
            error=error, loaded_manifest=loaded_manifest,
        )

    def _finish_verified(
        self, identity: str, owner: str, attempt: int, *,
        assessment: AnalyticalAssessment | None = None, basis: dict[str, Any] | None = None,
        error: str | None = None, loaded_manifest: str | None = None,
    ) -> bool:
        now = self.clock()
        with self.database.session() as session:
            row = session.get(Outlook, identity)
            if row is None:
                return False
            if assessment is not None and basis is not None:
                if (assessment.outlook_id != identity or assessment.run_id != row.run_id
                    or assessment.study_id != row.study_id or basis["outlook_id"] != identity
                    or as_utc(assessment.assessed_at) > now):
                    raise ValueError("assessment_basis_identity_mismatch")
                assessment_json = _json(assessment.model_dump(mode="json"))
                basis_json = _json(basis)
                if basis["assessment_hash"] != _hash(assessment_json):
                    raise ValueError("assessment_basis_result_hash_mismatch")
                # Fresh source fence in the final claim writer. This never
                # upgrades an old derived row; only a genuine new build has it.
                if (
                    loaded_manifest is None
                    or
                    basis.get("schema_version") != SCHEMA
                    or basis.get("loaded_verifier") != {
                        "schema_version": LOADED_VERIFIER_VERSION,
                        "manifest_hash": loaded_manifest,
                    }
                    or basis["manifest_hash"] != loaded_manifest
                ):
                    raise ValueError("assessment_loaded_verifier_identity_mismatch")
                # Time changes require another genuine verification. No elapsed
                # window or newly completed observation inherits an old verdict.
                due = now + timedelta(hours=6)
                if assessment.label_status == "not_due":
                    due = min(due, as_utc(row.window_end_at))
                elif assessment.label_status in {"pending", "retry"}:
                    due = now + timedelta(minutes=1)
                boundary = basis.get("next_time_boundary_at")
                if boundary is not None:
                    boundary_at = datetime.fromisoformat(boundary)
                    if boundary_at.tzinfo is None or boundary_at.utcoffset() is None:
                        raise ValueError("assessment_basis_clock_timezone_required")
                    if boundary_at > now:
                        due = min(due, as_utc(boundary_at))
                values = dict(
                    status="completed", evaluated_at=assessment.assessed_at, next_attempt_at=due,
                    verifier_version=basis["manifest_hash"], basis_json=basis_json,
                    basis_hash=_hash(basis_json), assessment_json=assessment_json,
                    assessment_hash=_hash(assessment_json), error_code=None,
                )
            else:
                values = dict(
                    status="retry", next_attempt_at=now + timedelta(
                        seconds=min(3600, 30 * 2 ** min(attempt - 1, 7)),
                    ), error_code=error or "assessment_failed",
                )
            conditions = [
                Job.outlook_id == identity, Job.status == "running",
                Job.attempt == attempt, Job.lease_owner == owner, Job.lease_expires_at > now,
            ]
            if basis is not None and assessment is not None:
                conditions.append(Job.verifier_version == basis["manifest_hash"])
            changed = cast(CursorResult[Any], session.execute(update(Job).where(
                *conditions,
            ).values(**values, lease_owner=None, lease_expires_at=None))).rowcount
            return changed == 1

    def _retry_claim(
        self, identity: str, owner: str, attempt: int, *, error: str,
    ) -> bool:
        """Release only this worker's exact claim after its read thread has stopped."""
        now = self.clock()
        with self.database.session() as session:
            changed = cast(CursorResult[Any], session.execute(update(Job).where(
                Job.outlook_id == identity,
                Job.status == "running",
                Job.attempt == attempt,
                Job.lease_owner == owner,
            ).values(
                status="retry",
                next_attempt_at=now + timedelta(
                    seconds=min(3600, 30 * 2 ** min(attempt - 1, 7)),
                ),
                lease_owner=None,
                lease_expires_at=None,
                error_code=error,
            ).execution_options(synchronize_session=False))).rowcount
            return changed == 1

    def _process_batch(self, *, limit: int, checkpoint: Callable[[], None]) -> int:
        source_memo: dict[Any, Any] = {}
        cutoff = self.clock()
        manifest = loaded_assessment_manifest_hash(
            memo=source_memo, checkpoint=checkpoint,
        )
        checkpoint()
        self.discover(cutoff, manifest)
        completed = 0
        for _ in range(limit):
            checkpoint()
            claim = self.claim(self.clock(), manifest)
            if claim is None:
                break
            identity, owner, attempt = claim
            try:
                assessment, basis = build_assessment_basis(
                    self.database, identity, assessed_at=self.clock(), archive=self.archive,
                    source_memo=source_memo, checkpoint=checkpoint,
                )
                if basis["manifest_hash"] != manifest:
                    raise ValueError("assessment_verifier_changed_during_execution")
                # One uncached scan is the final source fence for this writer.
                # The operation memo can never satisfy this check.
                fresh_manifest = loaded_assessment_manifest_hash(checkpoint=checkpoint)
                if fresh_manifest != manifest:
                    raise ValueError("assessment_verifier_changed_during_execution")
                completed += int(self._finish_verified(
                    identity, owner, attempt, assessment=assessment, basis=basis,
                    loaded_manifest=fresh_manifest,
                ))
            except _AssessmentStopped:
                self._retry_claim(
                    identity, owner, attempt, error="assessment_deadline_elapsed",
                )
                raise
            except Exception as exc:
                # Technical failure has a durable retry. It never becomes a
                # fabricated invalid-source assessment or a completed job.
                self.finish(identity, owner, attempt, error="assessment_" + type(exc).__name__)
        return completed

    async def process_due(
        self, *, limit: int = 2, deadline_at: float | None = None,
    ) -> int:
        if not 1 <= limit <= 8:
            raise ValueError("assessment_batch_limit_invalid")
        if deadline_at is not None and (
            type(deadline_at) not in (int, float) or not math.isfinite(deadline_at)
        ):
            raise ValueError("assessment_deadline_invalid")
        deadline = (
            monotonic() + (deadline_at - asyncio.get_running_loop().time())
            if deadline_at is not None else None
        )
        stopped = threading.Event()

        def checkpoint() -> None:
            _assessment_checkpoint(stopped, deadline)

        async with self._batch_lock:
            # Cancellation cannot release the lock while its thread is still
            # producing a result; the fenced claim remains authoritative.
            task = asyncio.create_task(asyncio.to_thread(
                self._process_batch, limit=limit, checkpoint=checkpoint,
            ))
            try:
                return await asyncio.shield(task)
            except _AssessmentStopped:
                raise TimeoutError("assessment_deadline_elapsed") from None
            except asyncio.CancelledError:
                stopped.set()
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not task.cancelled():
                    task.exception()
                raise
