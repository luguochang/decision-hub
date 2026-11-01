"""Consume a known RO preparation rejection; never retry an uncertain write.

The original sealed jobs/subjects remain byte-identical. This finite adapter
records a rejected preparation before any subject save, releases only its exact
committed reservation, and leaves the next fixed offset/budget unchanged.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from time import monotonic

from ..ports.market_path import canonical_hash
from .history_observed_dataset import encoded
from .history_outcome_jobs_v2 import HistoryOutcomeJobsV2, HistoryOutcomeJobUnconfirmed

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
SOURCE_FILE = "packages/kernel/decision_hub_kernel/application/history_outcome_recovery_v2.py"
KNOWN_READ_REJECTIONS = frozenset(
    {
        "history_observed_capture_scope_or_knowledge_invalid",
        "history_observed_candle_identity_or_clock_changed",
        "history_observed_conflicting_revision_clock",
        "history_observed_raw_hash_changed",
        "history_observed_raw_shape_invalid",
        "history_observed_raw_page_limit_invalid",
        "history_observed_raw_row_missing",
        "history_observed_raw_row_value_changed",
        "history_observed_raw_changed_at_exit",
        "history_observed_archive_hash_changed",
        "history_outcome_complete_label_original_or_math_changed",
    }
)


class HistoryOutcomeRecoveryV2:
    def __init__(self, jobs):
        if type(jobs) is not HistoryOutcomeJobsV2:
            raise ValueError("history_outcome_actual_sealed_jobs_required")
        self.jobs, self.service = jobs, jobs.service
        self.identity = jobs, jobs.service, jobs.commands, jobs.owner_claim

    def _sources(self):
        if self.identity != (
            self.jobs,
            self.jobs.service,
            self.jobs.commands,
            self.jobs.owner_claim,
        ):
            raise ValueError("history_outcome_recovery_binding_changed")
        if (
            hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != LOADED_SOURCE_SHA256
            or hashlib.sha256(
                (self.service.originals.source_root / SOURCE_FILE).read_bytes()
            ).hexdigest()
            != LOADED_SOURCE_SHA256
            or any(
                getattr(HistoryOutcomeRecoveryV2, name) is not fn or fn.__code__ is not code
                for name, fn, code in LOADED_METHODS
            )
        ):
            raise ValueError("history_outcome_recovery_loaded_source_changed")
        return (*self.jobs._sources(), (SOURCE_FILE, LOADED_SOURCE_SHA256))

    def process_one(
        self, protocol_id, operation, *, opportunity_id=None, budget_seconds=120, checkpoint=None
    ):
        check = self.service._check(budget_seconds, checkpoint)
        deadline = monotonic() + budget_seconds
        sources = self._sources()
        claim = self.jobs.claim(
            protocol_id,
            operation,
            opportunity_id=opportunity_id,
            budget_seconds=budget_seconds,
            checkpoint=check,
        )
        if claim is None:
            return dict(outcome="no_due_opportunity", committed=False, automatic_retry=False)
        try:
            self.jobs._auth(claim)
            check()
            rejection = None
            # Swallow only the data rejection *inside* the existing physical RO
            # context. Its normal exit must still authenticate the snapshot.
            with self.jobs.commands._read_session() as session:
                native = session.connection().connection.driver_connection
                initial_changes = native.total_changes
                try:
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
                except ValueError as error:
                    code = str(error)
                    if operation != "observe" or code not in KNOWN_READ_REJECTIONS:
                        raise
                    rejection = code
                if (
                    native.total_changes != initial_changes
                    or not native.in_transaction
                    or native.execute("PRAGMA query_only").fetchone() != (1,)
                ):
                    raise ValueError("history_outcome_prepare_changed_database_or_snapshot")
            if rejection is not None:
                # Source/capability/snapshot/commit/budget failures never reach
                # this branch. No subject save has been attempted.
                code = rejection
                check()
                self.jobs._auth(claim)
                if self._sources() != sources:
                    raise ValueError("history_outcome_recovery_source_changed")
                evidence = dict(
                    schema_version="history-outcome-known-ro-rejection.v2",
                    owner_claim=self.service.owner_claim,
                    protocol_id=protocol_id,
                    opportunity_id=claim.opportunity_id,
                    generation=claim.generation,
                    attempt=claim.attempt,
                    stage="readonly_prepare_before_subject_save",
                    observed_at=self.service.now().isoformat(),
                    error_code=code,
                    source_files=sources,
                    subject_save_attempted=False,
                    automatic_retry=False,
                    financial_qualified=False,
                )
                reference, digest = self.service.archive.put(evidence)
                receipt = (
                    self.service.archive.root
                    / "job-rejections"
                    / (
                        canonical_hash(
                            [self.service.owner_claim, claim.opportunity_id, claim.generation]
                        )
                        + ".json"
                    )
                )
                receipt.parent.mkdir(parents=True, exist_ok=True)
                with receipt.open("xb") as handle:
                    handle.write(
                        encoded(
                            dict(
                                rejection_ref=reference,
                                rejection_hash=digest,
                                claim_generation=claim.generation,
                                attempt=claim.attempt,
                                automatic_retry=False,
                            )
                        )
                    )
                    handle.flush()
                    os.fsync(handle.fileno())
                # _finish itself uses the original native CAS and immutable
                # claim receipt; its unknown COMMIT retains the original fence.
                self.jobs._finish(
                    claim,
                    partial=True,
                    reason="history_outcome_known_readonly_prepare_rejected",
                    checkpoint=check,
                )
                return dict(
                    outcome="readonly_prepare_rejected",
                    committed=True,
                    opportunity_id=claim.opportunity_id,
                    generation=claim.generation,
                    attempt=claim.attempt,
                    error_code=code,
                    rejection_ref=reference,
                    rejection_hash=digest,
                    next_action="next_original_fixed_offset",
                    automatic_retry=False,
                    financial_qualified=False,
                )
            if packet is None:
                raise ValueError("history_outcome_reserved_action_not_ready")
            self.jobs._auth(claim)
            check()
            subject = self.service.save(packet, checkpoint=check)[0]
            accepted = operation == "observe" and subject.classification in (
                "verified",
                "late_diagnostic",
            )
            if accepted:
                self.service.confirm(
                    "observation",
                    subject.observation_id,
                    budget_seconds=deadline - monotonic(),
                    checkpoint=check,
                )
            self.jobs._finish(
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
                committed=True,
                opportunity_id=claim.opportunity_id,
                generation=claim.generation,
                attempt=claim.attempt,
                automatic_retry=False,
                financial_qualified=False,
            )
        except BaseException as error:
            raise HistoryOutcomeJobUnconfirmed(
                "history_outcome_action_requires_readonly_reconciliation"
            ) from error

    def tick(self, protocol_id, *, budget_seconds=120, checkpoint=None):
        check = self.service._check(budget_seconds, checkpoint)
        deadline = monotonic() + budget_seconds
        result = self.process_one(
            protocol_id, "reconcile", budget_seconds=deadline - monotonic(), checkpoint=check
        )
        if result["outcome"] != "no_due_opportunity":
            return result
        return self.process_one(
            protocol_id, "observe", budget_seconds=deadline - monotonic(), checkpoint=check
        )


LOADED_METHODS = tuple(
    (name, fn, fn.__code__)
    for name, fn in HistoryOutcomeRecoveryV2.__dict__.items()
    if callable(fn) and hasattr(fn, "__code__")
)
