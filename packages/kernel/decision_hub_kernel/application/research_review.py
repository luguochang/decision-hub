from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import ResearchReview, ResearchSessionResult
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRecord,
    RunRoleOutputRecord,
    SnapshotRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import (
    ExecutionBoundary,
    begin_immediate,
    current_result_consumer_boundary,
    prepare_originals,
)

if TYPE_CHECKING:
    from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
        GraphFinalProjectionRef,
    )


@dataclass(frozen=True)
class _PreparedReview:
    review: ResearchReview
    schema: str
    existing: bool
    boundary: ExecutionBoundary


class ResearchReviewService:
    """Persist a review receipt against one immutable result version.

    The review is an application asset, so a missing DSH trace cannot turn a
    completed business result into a failure. It does, however, make the
    review bounded and visible instead of being represented only by a counter.
    """

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))

    def review(
        self,
        run_id: str,
        result: ResearchSessionResult,
        *,
        graph_final_ref: GraphFinalProjectionRef | None = None,
        expected_lease_owner: str | None = None,
    ) -> ResearchReview:
        # Keep the Graph import local: its pure projection hashes use this
        # module, while only this writer is a Graph consumer.
        from .research_graph_binding import graph_requires_native_binding_in_session

        result = ResearchSessionResult.model_validate(result.model_dump(mode="json"))
        with self.database.session() as session:
            native = graph_requires_native_binding_in_session(
                session,
                run_id,
                has_checkpoint_locator=graph_final_ref is not None,
            )
        if not native:
            return self._review_legacy(run_id, result)
        prepared = prepare_originals(self.database, lambda session: self._prepare_review(
            session, run_id, result, graph_final_ref, expected_lease_owner,
        ))
        with self.database.session() as session:
            begin_immediate(session)
            if not expected_lease_owner:
                raise PermissionError("research_graph_publication_owner_required")
            _publication_fence(session, run_id, expected_lease_owner)
            prepared.value.boundary.require_current_link(session, run_id)
            prepared.verify(session)
            if prepared.value.existing:
                prepared.value.boundary.require_current_link(session, run_id)
                _publication_fence(session, run_id, expected_lease_owner)
                return prepared.value.review
            payload = json.dumps(prepared.value.review.model_dump(mode="json"),
                                 ensure_ascii=False, sort_keys=True)
            session.add(RunRoleOutputRecord(
                output_id=f"out_{uuid.uuid4().hex}", run_id=run_id,
                role="independent_reviewer", payload_json=payload,
                payload_hash=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                schema_version=prepared.value.schema, created_at=utcnow(),
            ))
            session.flush()
            prepared.verify_after_writes(session)
            prepared.value.boundary.require_current_link(session, run_id)
            _publication_fence(session, run_id, expected_lease_owner)
            return prepared.value.review

    def _prepare_review(
        self, session: Session, run_id: str, result: ResearchSessionResult,
        graph_final_ref: GraphFinalProjectionRef | None, expected_lease_owner: str | None,
    ) -> _PreparedReview:
        from .research_graph_binding import (
            GraphFinalProjectionRef,
            _permission,  # pyright: ignore[reportPrivateUsage]
            read_graph_final_in_session,
        )
        if graph_final_ref is None:
            raise ValueError("research_graph_publication_reference_required")
        if not isinstance(graph_final_ref, GraphFinalProjectionRef):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("research_graph_publication_reference_invalid")
        graph_final_ref = GraphFinalProjectionRef.model_validate_json(
            graph_final_ref.model_dump_json(),
        )
        if not expected_lease_owner:
            raise PermissionError("research_graph_publication_owner_required")
        _permission(session, run_id, expected_lease_owner)
        original = read_graph_final_in_session(
            session, run_id=run_id, reference=graph_final_ref,
            expected_lease_owner=expected_lease_owner,
            require_current_permission=False,
        )
        boundary = current_result_consumer_boundary(session, run_id, original=original)
        if research_result_content_hash(result) != original.receipt.projected_result_content_hash:
            raise ValueError("research_graph_declared_final_result_mismatch")
        allowed = run_input_evidence_ids_in_session(session, run_id) | {
            item.evidence_id for item in original.result.evidence_candidates
        }
        existing = _review_row_in_session(session, run_id)
        if existing is not None and result.reviews != original.result.reviews and result.reviews:
            verify_graph_review_in_session(
                session, run_id=run_id, original=original.result, declared=result,
            )
            return _PreparedReview(
                _decode_review_row(existing), existing.schema_version, True, boundary,
            )
        _assert_original_review_input(original.result, result, allowed)
        supplied = _validated_native_review(result, allowed_evidence_ids=allowed)
        if existing is not None:
            previous = _decode_review_row(existing)
            _assert_review_producer(
                original.result, previous, existing.schema_version, allowed, run_id,
            )
            if supplied is not None and previous != supplied:
                raise ValueError("research_native_review_conflict")
            if previous.result_hash != research_result_content_hash(result):
                raise ValueError("research_review_result_hash_mismatch")
            return _PreparedReview(previous, existing.schema_version, True, boundary)
        review = supplied or _bounded_review(run_id, result, allowed, self.clock())
        return _PreparedReview(review, "research-review.native-attested.v1" if supplied is not None
                               else "research-review.automated-check.v1", False, boundary)

    def review_in_session(
        self,
        session: Session,
        run_id: str,
        result: ResearchSessionResult,
        *,
        graph_final_ref: GraphFinalProjectionRef | None,
        expected_lease_owner: str | None,
    ) -> ResearchReview:
        """Borrow one physical writer; never begin, commit, or close it here."""
        from .research_graph_binding import (
            GraphFinalProjectionRef,
            verify_declared_graph_result_in_session,
        )

        require_publication_writer(session)
        if graph_final_ref is None:
            raise ValueError("research_graph_publication_reference_required")
        if not isinstance(graph_final_ref, GraphFinalProjectionRef):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("research_graph_publication_reference_invalid")
        if not expected_lease_owner:
            raise PermissionError("research_graph_publication_owner_required")
        original = verify_declared_graph_result_in_session(
            session,
            run_id=run_id,
            reference=graph_final_ref,
            declared_result=result,
            expected_lease_owner=expected_lease_owner,
            allow_reviews_only=True,
        )
        allowed = run_input_evidence_ids_in_session(session, run_id) | {
            item.evidence_id for item in original.result.evidence_candidates
        }
        existing = _review_row_in_session(session, run_id)
        if existing is not None and result.reviews != original.result.reviews and result.reviews:
            verify_graph_review_in_session(
                session,
                run_id=run_id,
                original=original.result,
                declared=result,
            )
            previous = _decode_review_row(existing)
            _publication_fence(session, run_id, expected_lease_owner)
            return previous
        _assert_original_review_input(original.result, result, allowed)
        supplied = _validated_native_review(result, allowed_evidence_ids=allowed)
        if existing is not None:
            previous = _decode_review_row(existing)
            _assert_review_producer(
                original.result, previous, existing.schema_version, allowed, run_id
            )
            if supplied is not None and previous != supplied:
                raise ValueError("research_native_review_conflict")
            if previous.result_hash != research_result_content_hash(result):
                raise ValueError("research_review_result_hash_mismatch")
            _publication_fence(session, run_id, expected_lease_owner)
            return previous
        review = supplied or _bounded_review(run_id, result, allowed, self.clock())
        payload = json.dumps(review.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        session.add(
            RunRoleOutputRecord(
                output_id=f"out_{uuid.uuid4().hex}",
                run_id=run_id,
                role="independent_reviewer",
                payload_json=payload,
                payload_hash=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                schema_version=(
                    "research-review.native-attested.v1"
                    if supplied is not None
                    else "research-review.automated-check.v1"
                ),
                created_at=utcnow(),
            )
        )
        session.flush()
        _publication_fence(session, run_id, expected_lease_owner)
        return review

    def _review_legacy(self, run_id: str, result: ResearchSessionResult) -> ResearchReview:
        # Metadata dispatch is advisory until this physical writer starts.
        # Another connection may have installed native receipts after preflight.
        with self.database.session() as session:
            if session.get_bind().dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            reject_legacy_native_publication_in_session(session, run_id)
            allowed = run_input_evidence_ids_in_session(session, run_id) | {
                item.evidence_id for item in result.evidence_candidates
            }
            supplied = _validated_native_review(result, allowed_evidence_ids=allowed)
            existing = _review_row_in_session(session, run_id)
            if existing is not None:
                # Preserve the legacy payload contract; do not authenticate or
                # upgrade an old review into a native V2 producer receipt.
                previous = ResearchReview.model_validate_json(existing.payload_json)
                if supplied is not None and previous != supplied:
                    raise ValueError("research_native_review_conflict")
                if supplied is None and previous.result_hash != research_result_content_hash(
                    result
                ):
                    raise ValueError("research_review_result_hash_mismatch")
                return previous
            review = supplied or _bounded_review(run_id, result, allowed, self.clock())
            payload = json.dumps(review.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
            session.add(
                RunRoleOutputRecord(
                    output_id=f"out_{uuid.uuid4().hex}",
                    run_id=run_id,
                    role="independent_reviewer",
                    payload_json=payload,
                    payload_hash=hashlib.sha256(payload.encode()).hexdigest(),
                    schema_version=(
                        "research-review.native-attested.v1"
                        if supplied is not None
                        else "research-review.automated-check.v1"
                    ),
                    created_at=utcnow(),
                )
            )
            session.flush()
            return review


def independent_review_receipt_hash(
    *,
    role: Literal["independent_reviewer"],
    target_claim: str,
    evidence_refs: list[str],
    challenge: str,
    resolution: Literal["accepted", "bounded", "requires_new_evidence", "rejected"],
    limitations: list[str],
) -> str:
    """Hash the model-owned fields in an independent DSH review receipt."""

    payload = {
        "schema_version": "independent-review-receipt.v1",
        "role": role,
        "target_claim": target_claim,
        "evidence_refs": evidence_refs,
        "challenge": challenge,
        "resolution": resolution,
        "limitations": limitations,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def research_result_content_hash(result: ResearchSessionResult) -> str:
    """Hash one immutable research result version without its attached reviews."""

    payload = result.model_dump(mode="json", exclude={"reviews"})
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def bind_reviews_to_result_version(result: ResearchSessionResult) -> ResearchSessionResult:
    """Rebind trusted review receipts after deterministic result normalization."""

    if not result.reviews:
        return result
    result_hash = research_result_content_hash(result)
    return result.model_copy(
        update={
            "reviews": [
                review.model_copy(update={"result_hash": result_hash}) for review in result.reviews
            ]
        }
    )


def has_valid_accepted_independent_review(
    result: ResearchSessionResult,
    *,
    additional_allowed_evidence_ids: set[str] | None = None,
    native_provenance_verified: bool = False,
) -> bool:
    """Revalidate an accepted review at the final commit boundary.

    Upstream services are not publication authority. The immutable result
    hash, receipt identity, final thesis, timestamps, empty limitations, and
    accepted Evidence boundary must all still match here.
    """

    accepted_evidence_ids = {
        item.evidence_id for item in result.evidence_candidates if item.quality == "accepted"
    }
    accepted_evidence_ids.update(additional_allowed_evidence_ids or set())
    try:
        review = _validated_native_review(
            result,
            allowed_evidence_ids=accepted_evidence_ids,
        )
    except ValueError:
        return False
    return (
        native_provenance_verified
        and review is not None
        and review.resolution == "accepted"
        and not review.limitations
    )


def _validated_native_review(
    result: ResearchSessionResult,
    *,
    allowed_evidence_ids: set[str] | None = None,
) -> ResearchReview | None:
    if not result.reviews:
        return None
    if len(result.reviews) != 1:
        raise ValueError("research_native_review_count_invalid")
    review = result.reviews[0]
    evidence_ids = (
        {item.evidence_id for item in result.evidence_candidates}
        if allowed_evidence_ids is None
        else allowed_evidence_ids
    )
    if not set(review.evidence_refs) <= evidence_ids:
        raise ValueError("research_native_review_evidence_mismatch")
    if (
        result.causal_case is not None
        and review.target_claim != result.causal_case.thesis
        and review.resolution == "accepted"
    ):
        raise ValueError("research_native_review_target_mismatch")
    receipt_hash = independent_review_receipt_hash(
        role="independent_reviewer",
        target_claim=review.target_claim,
        evidence_refs=review.evidence_refs,
        challenge=review.challenge,
        resolution=review.resolution,
        limitations=review.limitations,
    )
    if review.review_id != f"review_{receipt_hash[:32]}":
        raise ValueError("research_native_review_identity_mismatch")
    if review.result_hash != research_result_content_hash(result):
        raise ValueError("research_native_review_hash_mismatch")
    if not result.started_at <= review.created_at <= result.finished_at:
        raise ValueError("research_native_review_time_mismatch")
    return review


def _run_input_evidence_ids(database: Database, run_id: str) -> set[str]:
    """Return immutable trigger Evidence refs available to this Run.

    Tests and legacy runs may not have a Run/Snapshot row; in that case the
    result-local Evidence boundary remains unchanged.
    """

    run = database.get_run_record(run_id)
    if run is None or run.snapshot_id is None:
        return set()
    try:
        rows = database.get_snapshot_evidence(run.snapshot_id)
    except (KeyError, ValueError):
        return set()
    return {
        str(row["evidence_id"])
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("evidence_id"), str)  # pyright: ignore[reportUnnecessaryIsInstance]
    }


def run_input_evidence_ids_in_session(session: Session, run_id: str) -> set[str]:
    run = session.get(RunRecord, run_id, populate_existing=True)
    snapshot = (
        session.get(SnapshotRecord, run.snapshot_id, populate_existing=True)
        if run is not None and run.snapshot_id
        else None
    )
    if snapshot is None:
        return set()
    try:
        rows = json.loads(snapshot.evidence_json)
    except (ValueError, TypeError):
        return set()
    return {
        str(row["evidence_id"])
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("evidence_id"), str)
    }


def require_publication_writer(session: Session) -> None:
    connection = session.connection()
    driver = connection.connection.driver_connection
    if (
        connection.dialect.name != "sqlite"
        or not isinstance(driver, sqlite3.Connection)
        or not driver.in_transaction
    ):
        raise ValueError("research_publication_writer_transaction_required")


def reject_legacy_native_publication_in_session(session: Session, run_id: str) -> None:
    """Recheck original reserved metadata inside the actual legacy writer."""
    from .research_graph_binding import graph_requires_native_binding_in_session

    if graph_requires_native_binding_in_session(session, run_id):
        raise ValueError("research_graph_publication_reference_required")


def _publication_fence(session: Session, run_id: str, owner: str) -> None:
    changed = session.execute(
        update(RunRecord)
        .where(
            RunRecord.run_id == run_id,
            RunRecord.status == "running",
            RunRecord.artifact_id.is_(None),
            RunRecord.lease_owner == owner,
            RunRecord.lease_expires_at > utcnow(),
        )
        .values(updated_at=RunRecord.updated_at)
        .execution_options(synchronize_session=False)
    )
    if int(getattr(changed, "rowcount", 0)) != 1:
        raise PermissionError("research_graph_writer_permission_lost")


def _review_row_in_session(session: Session, run_id: str) -> RunRoleOutputRecord | None:
    return session.scalar(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role == "independent_reviewer",
        )
        .execution_options(populate_existing=True)
    )


def _decode_review_row(row: RunRoleOutputRecord) -> ResearchReview:
    # Historical SQLite rows can predate the non-null storage annotation.
    created_at: datetime | None = getattr(row, "created_at", None)
    if (
        row.schema_version
        not in {
            "research-review.native-attested.v1",
            "research-review.automated-check.v1",
        }
        or row.payload_hash != hashlib.sha256(row.payload_json.encode("utf-8")).hexdigest()
        or as_utc(created_at) is None
    ):
        raise ValueError("research_review_receipt_invalid")
    return ResearchReview.model_validate_json(row.payload_json)


def _assert_original_review_input(
    original: ResearchSessionResult,
    declared: ResearchSessionResult,
    allowed: set[str],
) -> None:
    if declared.reviews == original.reviews:
        return
    # The Worker may discard an invalid advisory native receipt. The failure
    # must come from the original Graph body, not a caller's replacement.
    if not declared.reviews and original.reviews:
        try:
            _validated_native_review(original, allowed_evidence_ids=allowed)
        except ValueError:
            return
    raise ValueError("research_graph_review_original_mismatch")


def _assert_review_producer(
    original: ResearchSessionResult,
    review: ResearchReview,
    schema: str,
    allowed: set[str],
    run_id: str,
) -> None:
    native = None
    try:
        native = _validated_native_review(original, allowed_evidence_ids=allowed)
    except ValueError:
        # This is the same original advisory-receipt fallback as the Worker.
        pass
    expected = (
        native
        if schema == "research-review.native-attested.v1"
        else _bounded_review(run_id, original, allowed, review.created_at)
    )
    if expected is None or review != expected:
        raise ValueError("research_graph_review_receipt_origin_invalid")


def verify_graph_review_in_session(
    session: Session,
    *,
    run_id: str,
    original: ResearchSessionResult,
    declared: ResearchSessionResult,
) -> tuple[str, str, str, str]:
    """Authenticate the actual review row; review-only Graph equality is insufficient."""
    row = _review_row_in_session(session, run_id)
    if row is None or declared.reviews is None or len(declared.reviews) != 1:
        raise ValueError("research_graph_publication_review_required")
    review = _decode_review_row(row)
    allowed = run_input_evidence_ids_in_session(session, run_id) | {
        item.evidence_id for item in original.evidence_candidates
    }
    _assert_review_producer(original, review, row.schema_version, allowed, run_id)
    if declared.reviews != [review]:
        raise ValueError("research_graph_review_receipt_mismatch")
    recorded = as_utc(row.created_at)
    assert recorded is not None
    return row.output_id, row.schema_version, row.payload_hash, recorded.isoformat()


def _bounded_review(
    run_id: str,
    result: ResearchSessionResult,
    evidence_ids: set[str],
    created_at: datetime,
) -> ResearchReview:
    # Same deterministic bounded check as the legacy producer. It cannot mint
    # an accepted independent review from coverage or prepared policy metadata.
    result_hash = research_result_content_hash(result)
    if result.causal_case is None:
        refs = sorted(evidence_ids)[:1]
        target = "No directional causal claim was published."
        challenge = (
            "Re-read the fixed result and confirm that no unsupported directional claim is present."
        )
        resolution = "bounded"
        limitations = ["No causal case was available for an independent claim challenge."]
    else:
        refs = sorted(set(result.causal_case.evidence_refs) & evidence_ids)
        missing = sorted(set(result.causal_case.evidence_refs) - evidence_ids)
        target = result.causal_case.thesis
        challenge = (
            "Re-read the fixed result version and every cited Evidence ref; challenge whether "
            "the main chain is supported by the retained facts and whether the opposite chain "
            "is addressed."
        )
        if missing:
            resolution = "requires_new_evidence"
            limitations = [
                f"Claim cites Evidence absent from the fixed result: {', '.join(missing)}."
            ]
        elif result.final_coverage.status == "sufficient":
            resolution = "bounded"
            limitations = [
                "Automated evidence check only; no attested independent reviewer receipt "
                "was available for this immutable result version."
            ]
        else:
            resolution = "bounded"
            limitations = [
                "Hard evidence gaps remain; review cannot promote a directional result.",
                *[gap.requirement_id for gap in result.final_coverage.gaps],
            ]
    return ResearchReview(
        review_id=f"review:{run_id}:{result_hash[:16]}",
        role="independent_reviewer",
        target_claim=target,
        evidence_refs=refs or [f"run:{run_id}"],
        result_hash=result_hash,
        challenge=challenge,
        resolution=resolution,
        limitations=list(dict.fromkeys(limitations)),
        created_at=created_at,
    )
