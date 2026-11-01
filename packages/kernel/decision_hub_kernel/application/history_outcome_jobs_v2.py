"""One bounded outcome action, preserved counters and durable unknown fences.

This module downloads nothing and never retries a commit or reserved attempt.
The four observation opportunities are fixed offsets from the frozen window end.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic

from sqlalchemy import select, update

from ..persistence.db import as_utc
from ..persistence.history_outcome_v2_records import (
    HistoryOutcomeOpportunityV2Record as Opportunity,
)
from ..ports.market_path import canonical_hash
from .history_forecast_transfer39 import native_equal39
from .history_observed_dataset import encoded
from .history_outcome_v2 import HistoryOutcomeV2Service, _native

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
OFFSETS = (120, 900, 3600, 21600)


class HistoryOutcomeJobUnconfirmed(ValueError):
    automatic_retry = False

    def __init__(self, code, *, committed=None):
        super().__init__(code)
        self.committed = committed


@dataclass(frozen=True, slots=True)
class OutcomeJobClaimV2:
    protocol_id: str
    opportunity_id: str
    operation: str
    generation: int
    attempt: int
    lease_owner: str
    lease_expires_at: datetime
    source_files: tuple[tuple[str, str], ...]
    fence_path: Path
    fence_body: bytes
    thread_id: int
    authentication: bytes


class HistoryOutcomeJobsV2:
    def __init__(self, service):
        if type(service) is not HistoryOutcomeV2Service:
            raise ValueError("history_outcome_jobs_actual_service_required")
        self.service, self.commands = service, service.commands
        self.owner_claim = service.owner_claim
        self.identity = (
            service,
            self.commands,
            self.owner_claim,
            service.archive,
            service.archive.root,
            service.originals.source_root,
        )
        self.key = secrets.token_bytes(32)
        self.issued = {}

    def _sources(self, expected=None):
        if self.identity != (
            self.service,
            self.service.commands,
            self.service.owner_claim,
            self.service.archive,
            self.service.archive.root,
            self.service.originals.source_root,
        ):
            raise ValueError("history_outcome_jobs_binding_changed")
        if any(
            getattr(HistoryOutcomeJobsV2, name) is not fn or fn.__code__ is not code
            for name, fn, code in LOADED_METHODS
        ):
            raise ValueError("history_outcome_jobs_loaded_callable_changed")
        path = "packages/kernel/decision_hub_kernel/application/history_outcome_jobs_v2.py"
        digest = hashlib.sha256(
            (self.service.originals.source_root / path).read_bytes()
        ).hexdigest()
        if digest != LOADED_SOURCE_SHA256:
            raise ValueError("history_outcome_jobs_loaded_source_changed")
        sources = tuple(dict((*self.service._sources(), (path, digest))).items())
        if expected is not None and expected != sources:
            raise ValueError("history_outcome_jobs_source_changed")
        return sources

    def _mac(self, claim):
        return hmac.digest(
            self.key,
            encoded(
                [
                    claim.protocol_id,
                    claim.opportunity_id,
                    claim.operation,
                    claim.generation,
                    claim.attempt,
                    claim.lease_owner,
                    claim.lease_expires_at.isoformat(),
                    claim.source_files,
                    str(claim.fence_path),
                    claim.fence_body.hex(),
                    claim.thread_id,
                ]
            ),
            "sha256",
        )

    def _auth(self, claim):
        if (
            type(claim) is not OutcomeJobClaimV2
            or self.issued.get(id(claim)) is not claim
            or (
                claim.thread_id != threading.get_ident()
                or not hmac.compare_digest(claim.authentication, self._mac(claim))
                or claim.fence_path.read_bytes() != claim.fence_body
            )
        ):
            raise ValueError("history_outcome_jobs_issued_claim_required")
        self._sources(claim.source_files)

    def _fence(self, opportunity_id):
        return (
            self.service.archive.root
            / "job-fences"
            / (canonical_hash([self.owner_claim, opportunity_id]) + ".json")
        )

    def _matches(self, row, claim):
        return (
            row is not None
            and row.owner_claim == self.owner_claim
            and row.protocol_id == claim.protocol_id
            and (
                row.generation == claim.generation
                and row.attempts == claim.attempt
                and row.lease_owner == claim.lease_owner
                and as_utc(row.lease_expires_at) == claim.lease_expires_at
            )
        )

    def claim(
        self, protocol_id, operation, *, opportunity_id=None, budget_seconds=120, checkpoint=None
    ):
        if (
            operation not in ("reconcile", "observe")
            or type(budget_seconds) not in (int, float)
            or not 0 < budget_seconds <= 120
        ):
            raise ValueError("history_outcome_jobs_finite_operation_required")
        check = self.service._check(budget_seconds, checkpoint)
        check()
        sources, clock = self._sources(), self.service.now()
        with self.commands._read_session() as session:
            protocol, _, registration = self.service._read(session, "protocol", protocol_id)
            if registration.confirmation_id is None:
                raise ValueError("history_outcome_jobs_confirmed_protocol_required")
            self.service._acceptance(session, registration.confirmation_id, "publication")
            statement = select(Opportunity).where(
                Opportunity.owner_claim == self.owner_claim, Opportunity.protocol_id == protocol_id
            )
            if opportunity_id is not None:
                statement = statement.where(Opportunity.opportunity_id == opportunity_id)
            else:
                statement = statement.where(
                    Opportunity.state.in_(("pending", "ready")), Opportunity.next_due_at <= clock
                )
                statement = statement.where(
                    Opportunity.first_publication_id.is_(None)
                    if operation == "reconcile"
                    else Opportunity.first_publication_id.is_not(None)
                )
            row = session.scalar(
                statement.order_by(Opportunity.hour_at, Opportunity.scope_id).limit(1)
            )
            if row is None:
                return None
            self.service._read(session, "opportunity", row.opportunity_id)
            if (
                row.lease_owner is not None
                or row.state in ("running", "unconfirmed")
                or self._fence(row.opportunity_id).exists()
            ):
                raise HistoryOutcomeJobUnconfirmed(
                    "history_outcome_jobs_prior_attempt_requires_reconciliation"
                )
            if row.state not in ("pending", "ready") or as_utc(row.next_due_at) > clock:
                return None
            if operation == "reconcile":
                if row.first_publication_id is not None:
                    return None
                attempt = row.attempts
            else:
                if (
                    row.first_publication_id is None
                    or row.first_acceptance_id is not None
                    or row.attempts >= 4
                ):
                    return None
                if row.label_cutoff_at is None or row.label_deadline_at is None:
                    raise ValueError("history_outcome_jobs_frozen_label_binding_required")
                end = as_utc(row.label_cutoff_at) + timedelta(days=1, minutes=1)
                due = end + timedelta(seconds=OFFSETS[row.attempts])
                if clock < due:
                    return None
                if clock > as_utc(row.label_deadline_at):
                    raise ValueError("history_outcome_jobs_label_deadline_elapsed")
                # A restart observes only the newest due fixed slot. Earlier
                # missed slots are consumed, never backfilled as fresh attempts.
                attempt = max(
                    index + 1
                    for index, offset in enumerate(OFFSETS)
                    if end + timedelta(seconds=offset) <= clock
                )
                if attempt <= row.attempts:
                    return None
            generation = row.generation + 1
            lease = "outcome_job_" + secrets.token_hex(16)
            expires = clock + timedelta(seconds=budget_seconds)
            if operation == "observe":
                expires = min(expires, as_utc(row.label_deadline_at))
            before = row.generation, row.attempts, row.state, row.next_due_at
            original_native = _native(row)
            chosen = row.opportunity_id
        check()
        self._sources(sources)
        path = self._fence(chosen)
        body = encoded(
            dict(
                schema_version="history-outcome-job-fence.v2",
                owner_claim=self.owner_claim,
                protocol_id=protocol_id,
                opportunity_id=chosen,
                operation=operation,
                generation=generation,
                attempt=attempt,
                lease_owner=lease,
                lease_expires_at=expires.isoformat(),
                source_files=sources,
                skipped_attempts=list(range(before[1] + 1, attempt))
                if operation == "observe"
                else [],
                opportunity_original_native_hash=canonical_hash(
                    [
                        [key, value.isoformat() if isinstance(value, datetime) else value]
                        for key, value in original_native
                    ]
                ),
            )
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            raise HistoryOutcomeJobUnconfirmed(
                "history_outcome_jobs_prior_fence_requires_reconciliation"
            ) from None
        receipt = (
            path.parent.parent
            / "job-receipts"
            / (canonical_hash([self.owner_claim, chosen, generation]) + ".json")
        )
        receipt.parent.mkdir(parents=True, exist_ok=True)
        try:
            with receipt.open("xb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException as error:
            raise HistoryOutcomeJobUnconfirmed(
                "history_outcome_jobs_claim_receipt_unconfirmed"
            ) from error
        claim = OutcomeJobClaimV2(
            protocol_id,
            chosen,
            operation,
            generation,
            attempt,
            lease,
            expires,
            sources,
            path,
            body,
            threading.get_ident(),
            b"",
        )
        claim = OutcomeJobClaimV2(*claim.__getstate__()[:-1], self._mac(claim))
        committed = False
        try:
            with self.commands._write_session() as session:
                check()
                self._sources(sources)
                current = session.get(Opportunity, chosen, populate_existing=True)
                if current is None or not native_equal39(_native(current), original_native):
                    raise ValueError("history_outcome_jobs_original_metadata_changed")
                result = session.execute(
                    update(Opportunity)
                    .where(
                        Opportunity.opportunity_id == chosen,
                        Opportunity.owner_claim == self.owner_claim,
                        Opportunity.protocol_id == protocol_id,
                        Opportunity.generation == before[0],
                        Opportunity.attempts == before[1],
                        Opportunity.state == before[2],
                        Opportunity.next_due_at == before[3],
                        Opportunity.lease_owner.is_(None),
                        Opportunity.lease_expires_at.is_(None),
                    )
                    .values(
                        generation=generation,
                        attempts=attempt,
                        lease_owner=lease,
                        lease_expires_at=expires,
                        state="running",
                        error_code=None,
                    )
                )
                if result.rowcount != 1:
                    raise ValueError("history_outcome_jobs_claim_cas_conflict")
                check()
                self._sources(sources)
            committed = True
            with self.commands._read_session() as session:
                if not self._matches(session.get(Opportunity, chosen), claim):
                    raise ValueError("history_outcome_jobs_claim_readback_changed")
            check()
            self._sources(sources)
        except BaseException as error:
            raise HistoryOutcomeJobUnconfirmed(
                "history_outcome_jobs_claim_unconfirmed", committed=True if committed else None
            ) from error
        self.issued[id(claim)] = claim
        return claim

    def _finish(
        self, claim, *, partial=False, already_completed=False, reason=None, checkpoint=None
    ):
        self._auth(claim)
        if checkpoint is not None:
            checkpoint()
        if already_completed:
            with self.commands._read_session() as session:
                row = session.get(Opportunity, claim.opportunity_id)
                if (
                    row is None
                    or row.generation != claim.generation
                    or row.attempts != claim.attempt
                    or (
                        row.state != "completed"
                        or row.first_acceptance_id is None
                        or row.lease_owner is not None
                        or row.owner_claim != self.owner_claim
                        or row.protocol_id != claim.protocol_id
                    )
                ):
                    raise ValueError("history_outcome_jobs_completed_readback_changed")
        else:
            with self.commands._write_session() as session:
                row = session.get(Opportunity, claim.opportunity_id, populate_existing=True)
                if not self._matches(row, claim):
                    raise ValueError("history_outcome_jobs_finish_claim_changed")
                if partial and claim.attempt < 4:
                    row.next_due_at = as_utc(row.label_cutoff_at) + timedelta(
                        days=1, minutes=1, seconds=OFFSETS[claim.attempt]
                    )
                    row.state = "ready"
                elif partial:
                    row.state = "blocked"
                elif claim.operation == "reconcile":
                    if row.first_publication_id is None:
                        raise ValueError("history_outcome_jobs_publication_not_saved")
                    row.state = "ready"
                row.lease_owner, row.lease_expires_at, row.error_code = None, None, reason
                expected = row.state, as_utc(row.next_due_at), row.error_code
                self._sources(claim.source_files)
                if checkpoint is not None:
                    checkpoint()
            with self.commands._read_session() as session:
                row = session.get(Opportunity, claim.opportunity_id)
                if (
                    row is None
                    or row.generation != claim.generation
                    or row.attempts != claim.attempt
                    or (
                        row.lease_owner is not None
                        or row.lease_expires_at is not None
                        or (row.state, as_utc(row.next_due_at), row.error_code) != expected
                    )
                ):
                    raise ValueError("history_outcome_jobs_finish_readback_changed")
        self._auth(claim)
        if checkpoint is not None:
            checkpoint()
        claim.fence_path.unlink()
        self.issued.pop(id(claim), None)

    def process_one(
        self, protocol_id, operation, *, opportunity_id=None, budget_seconds=120, checkpoint=None
    ):
        check = self.service._check(budget_seconds, checkpoint)
        deadline = monotonic() + budget_seconds
        claim = self.claim(
            protocol_id,
            operation,
            opportunity_id=opportunity_id,
            budget_seconds=budget_seconds,
            checkpoint=check,
        )
        if claim is None:
            return dict(outcome="no_due_opportunity", committed=False, automatic_retry=False)
        try:
            self._auth(claim)
            check()
            with self.commands._read_session() as session:
                if operation == "reconcile":
                    packet = self.service.prepare_publication(
                        session,
                        claim.opportunity_id,
                        budget_seconds=deadline - monotonic(),
                        checkpoint=check,
                    )
                else:
                    packet = self.service.prepare_observation(
                        session,
                        claim.opportunity_id,
                        attempt=claim.attempt,
                        budget_seconds=deadline - monotonic(),
                        checkpoint=check,
                    )
            if packet is None:
                raise ValueError("history_outcome_jobs_reserved_action_not_ready")
            self._auth(claim)
            check()
            subject = self.service.save(packet, checkpoint=check)[0]
            accepted = False
            if operation == "observe" and subject.classification in ("verified", "late_diagnostic"):
                self.service.confirm(
                    "observation",
                    subject.observation_id,
                    budget_seconds=deadline - monotonic(),
                    checkpoint=check,
                )
                accepted = True
            self._finish(
                claim,
                partial=operation == "observe" and not accepted,
                already_completed=accepted,
                checkpoint=check,
            )
            return dict(
                outcome="accepted"
                if accepted
                else "publication_saved"
                if operation == "reconcile"
                else "partial_saved",
                opportunity_id=claim.opportunity_id,
                generation=claim.generation,
                attempt=claim.attempt,
                committed=True,
                automatic_retry=False,
                financial_qualified=False,
            )
        except BaseException as error:
            # The reservation and any subject may already be durable. Keep both
            # fences; no automatic attempt or COMMIT replay follows this path.
            raise HistoryOutcomeJobUnconfirmed("history_outcome_jobs_action_unconfirmed") from error

    def tick(self, protocol_id, *, budget_seconds=120, checkpoint=None):
        check = self.service._check(budget_seconds, checkpoint)
        deadline = monotonic() + budget_seconds
        result = self.process_one(
            protocol_id, "reconcile", budget_seconds=deadline - monotonic(), checkpoint=check
        )
        if result["outcome"] != "no_due_opportunity":
            return result
        check()
        return self.process_one(
            protocol_id, "observe", budget_seconds=deadline - monotonic(), checkpoint=check
        )


LOADED_METHODS = tuple(
    (name, value, value.__code__)
    for name, value in vars(HistoryOutcomeJobsV2).items()
    if callable(value) and hasattr(value, "__code__")
)
