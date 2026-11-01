"""Atomic Action publication from exact committed research identities.

The public service accepts no synthesis, plan, quality verdict or verified flag.
Native authentication happens in a clean read snapshot, then its SQL readset and
source are rechecked in one short writer before publishing all three branches.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    ResearchInputEvidence,
    ResearchSessionResult,
)

from ..decision.action_derivation import (
    ActionBasisEvidence,
    derive_action_publication,
    derive_action_revision,
)
from ..persistence.action_repository import (
    ActionRepository,
    StoredCommandReceipt,
    make_command_receipt,
)
from ..persistence.db import (
    Database,
    ResearchEvidenceRecord,
    RunRecord,
    RunRoleOutputRecord,
    SourceAcquisitionRecord,
    as_utc,
    utcnow,
)
from ..persistence.original_preparation import begin_immediate, prepare_originals
from ..ports.action_plan import Opportunity
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.research_signal import ResearchActionSynthesisSidecar, ResearchDecisionSynthesis
from ..ports.sources import make_notification_envelope
from .action_research_reader import (
    TrustedActionResearchInputs,
    read_trusted_research_in_session,
    reject_final_without_action_synthesis_in_session,
)
from .action_review import ActionReviewIntent, review_for_run_in_session
from .action_revision import REVIEW_SCHEMA, plan_revision_changes
from .outbox import OutboxService
from .research_evidence import (
    ResearchCapabilityError,
    _evidence_model,  # pyright: ignore[reportPrivateUsage]
    _validate_candidate_pit,  # pyright: ignore[reportPrivateUsage]
    research_evidence_content_hash,
)
from .research_graph_binding import GraphFinalProjectionRef
from .research_publication_binding import observation_json
from .source_acquisition import _record_to_receipt  # pyright: ignore[reportPrivateUsage]

if TYPE_CHECKING:
    from packages.provider_adapters.research.document_bundle_store import DocumentBundleStore


_OWNER = "system:action-publication:v1"
_SCHEMA = "research-action-synthesis-sidecar.v1"


@dataclass(frozen=True)
class _Publication:
    sidecar: ResearchActionSynthesisSidecar
    sidecar_id: str
    sidecar_role: str
    opportunity: Opportunity
    evidence: tuple[ActionBasisEvidence, ...]
    source_gaps: tuple[tuple[str, str], ...]
    document_originals: tuple[tuple[str, str], ...]
    review: ActionReviewIntent | None
    skill_originals: tuple[tuple[str, str, str], ...] = ()


class _DocumentReads:
    """Capture every immutable document byte read, including locator assets."""

    def __init__(self, store: DocumentBundleStore) -> None:
        self.store = store
        self.hashes: dict[str, str] = {}

    def read(self, reference: str) -> bytes:
        payload = self.store.read(reference)
        digest = hashlib.sha256(payload).hexdigest()
        if reference in self.hashes and self.hashes[reference] != digest:
            raise ValueError("action_document_original_changed")
        self.hashes[reference] = digest
        return payload

    def path_for(self, reference: str):
        return self.store.path_for(reference)


def _identity(value: str) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or value.strip() != value:  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ValueError("action_publication_identity_invalid")


def _replay(
    repository: ActionRepository,
    request_id: str,
    payload_hash: str,
) -> StoredCommandReceipt | None:
    receipt = repository.get_command(_OWNER, request_id)
    if receipt is not None and (
        receipt.operation != "publish" or receipt.payload_hash != payload_hash
    ):
        raise ValueError("action_publication_key_conflict")
    return receipt


def _sidecar(
    trusted: TrustedActionResearchInputs,
    opportunity: Opportunity,
    skill_read=None,
) -> ResearchActionSynthesisSidecar:
    native = trusted.native_action
    if trusted.action_status != "native_candidate" or native is None:
        raise ValueError("action_native_typed_synthesis_unavailable")
    if native.review_gate_status != "matched" or native.review_effect_ref is None:
        raise ValueError("action_native_independent_review_required")
    if native.execution_mode != "live" and opportunity.cohort == "prospective":
        raise ValueError("action_prospective_live_source_required")
    if trusted.scope_id != opportunity.scope_id or trusted.selected_observation_ref is None:
        raise ValueError("action_publication_scope_mismatch")
    synthesis = ResearchDecisionSynthesis.model_validate_json(native.synthesis_json)
    if synthesis.skill_consumption_refs and skill_read is None:
        # W5 must authenticate real consumption, not just a model's version ID.
        raise ValueError("action_skill_consumption_original_required")
    body = {
        "schema_version": _SCHEMA,
        "run_id": trusted.run_id,
        "request_id": trusted.request_id,
        "generation": native.producer_generation,
        "request_hash": native.task_request_hash,
        "context_hash": native.raw_context_hash,
        "scope_id": opportunity.scope_id,
        "horizon": opportunity.horizon,
        "cutoff": trusted.decision_cutoff_at,
        "producer_cutoff": native.producer_cutoff_at,
        "selected_native_observation_ref": trusted.selected_observation_ref,
        "selection_witness": json.loads(native.selection_witness_json),
        "raw_candidate_hash": native.raw_candidate_hash,
        "projected_research_candidate_hash": native.projected_research_candidate_hash,
        "synthesis": synthesis,
        "payload_hash": "0" * 64,
    }
    parsed = ResearchActionSynthesisSidecar.model_validate(body)
    wire = parsed.model_dump(mode="json")
    wire["payload_hash"] = canonical_hash({k: v for k, v in wire.items() if k != "payload_hash"})
    return ResearchActionSynthesisSidecar.model_validate(wire)


def _acquisition(session: Session, candidate: EvidenceCandidate, cutoff: datetime) -> None:
    if candidate.acquisition_receipt_id is None:
        return
    row = session.get(
        SourceAcquisitionRecord,
        candidate.acquisition_receipt_id,
        populate_existing=True,
    )
    if row is None:
        raise ValueError("action_acquisition_original_missing")
    receipt = _record_to_receipt(row)
    body = receipt.model_dump(mode="json")
    for field in ("request_started_at", "request_completed_at", "received_at"):
        if body.get(field) is None:
            body.pop(field, None)
    body.pop("received_at", None)
    if canonical_hash(body) != row.receipt_hash:
        raise ValueError("action_acquisition_hash_mismatch")
    if (
        receipt.status != "succeeded"
        or receipt.source_id != candidate.source_id
        or str(receipt.url) != str(candidate.source_url)
        or not receipt.artifacts
    ):
        raise ValueError("action_acquisition_source_mismatch")
    recorded = as_utc(row.created_at)
    if (
        receipt.observed_at is None
        or receipt.received_at is None
        or not receipt.observed_at <= receipt.received_at <= candidate.received_at <= cutoff
        or receipt.observed_at > candidate.observed_at
        or not receipt.received_at <= recorded <= cutoff
        or (receipt.request_started_at is None) != (receipt.request_completed_at is None)
        or (
            receipt.request_started_at is not None
            and receipt.request_started_at > receipt.observed_at
        )
        or (
            receipt.request_completed_at is not None
            and not receipt.observed_at <= receipt.request_completed_at <= receipt.received_at
        )
    ):
        raise ValueError("action_acquisition_time_invalid")


def _used_evidence_refs(sidecar: ResearchActionSynthesisSidecar) -> set[str]:
    intents = [
        item
        for item in sidecar.synthesis.action_intents
        if (item.scope_id, item.horizon) == (sidecar.scope_id, sidecar.horizon)
    ]
    refs = {ref for item in intents for ref in item.evidence_refs}
    review_ids = {item.mechanism_review_ref for item in intents}
    for review in sidecar.synthesis.mechanism_reviews:
        if review.review_id not in review_ids:
            continue
        refs.update(ref for node in review.nodes for ref in node.refs)
        refs.update(
            ref for edge in review.edges for ref in (*edge.support_refs, *edge.counter_refs)
        )
        refs.update([*review.alternative_refs, *review.priced_in_refs])
    refs.difference_update(sidecar.synthesis.claim_signal_refs)
    refs.difference_update(ref for item in intents for ref in item.claim_refs)
    return refs


def _evidence(
    session: Session,
    trusted: TrustedActionResearchInputs,
    sidecar: ResearchActionSynthesisSidecar,
) -> tuple[list[ActionBasisEvidence], list[tuple[str, str]]]:
    if trusted.selected_result_json is None:
        raise ValueError("action_selected_result_required")
    result = ResearchSessionResult.model_validate_json(trusted.selected_result_json)
    output = []
    gaps = []
    native = trusted.native_action
    if native is None:
        raise ValueError("action_native_typed_synthesis_unavailable")
    inputs = [
        ResearchInputEvidence.model_validate(value)
        for value in json.loads(
            native.producer_input_evidence_json,
        )
    ]
    inputs_by_id = {item.evidence_id: item for item in inputs}
    originals = {item.evidence_id: item for item in result.evidence_candidates}
    if len(inputs_by_id) != len(inputs) or len(originals) != len(result.evidence_candidates):
        raise ValueError("action_evidence_duplicate_conflict")
    input_fields = set(ResearchInputEvidence.model_fields) - {"excerpt"}
    for evidence_id in sorted(_used_evidence_refs(sidecar)):
        original = originals.get(evidence_id)
        prior = inputs_by_id.get(evidence_id)
        if original is None and prior is None:
            raise ValueError("action_evidence_reference_unattested")
        if (
            original is not None
            and prior is not None
            and original.model_dump(
                include=input_fields,
            )
            != prior.model_dump(include=input_fields)
        ):
            raise ValueError("action_evidence_duplicate_conflict")
        row = session.get(ResearchEvidenceRecord, evidence_id, populate_existing=True)
        if row is None or row.run_id != trusted.run_id:
            if original is not None:
                raise ValueError("action_evidence_original_missing")
            # A real frozen Input can be a trigger, compiled spot context or
            # historic library prompt. Its identity is authenticated, but the
            # dedicated source lineage is not an admitted current-run Fact.
            # Omit its qualification; only branches actually using it block.
            gaps.append((evidence_id, "action_input_evidence_kind_unsupported"))
            continue
        actual = _evidence_model(row)
        # Admission normalizes these two classifications; all source fields must
        # remain the same exact candidate that the native synthesis consumed.
        if original is not None and actual.model_dump(
            exclude={"quality", "freshness_status"}
        ) != original.model_dump(exclude={"quality", "freshness_status"}):
            raise ValueError("action_evidence_original_mismatch")
        if prior is not None and actual.model_dump(include=input_fields) != prior.model_dump(
            include=input_fields,
        ):
            raise ValueError("action_input_evidence_original_mismatch")
        digest = research_evidence_content_hash(
            requirement_id=actual.requirement_id,
            kind=actual.kind,
            authority=actual.authority,
            source_id=actual.source_id,
            source_url=str(actual.source_url) if actual.source_url else None,
            published_at=actual.published_at,
            excerpt=actual.excerpt,
            structured_payload_ref=actual.structured_payload_ref,
        )
        if digest != actual.content_hash:
            raise ValueError("action_evidence_content_hash_mismatch")
        try:
            _validate_candidate_pit(actual, sidecar.producer_cutoff)
        except ResearchCapabilityError as error:
            raise ValueError(error.error_code) from error
        _acquisition(session, actual, sidecar.producer_cutoff)
        # Separate articles from one publisher are not independent. A same-body
        # hash is checked separately by the pure branch admission calculation.
        domain = urlsplit(str(actual.source_url)).hostname if actual.source_url else None
        independence = (domain or actual.source_id).lower().removeprefix("www.")
        output.append(
            ActionBasisEvidence(
                ref=actual.evidence_id,
                payload_hash=actual.content_hash,
                kind="evidence",
                known_at=actual.received_at,
                independence_key=independence,
                eligible=actual.quality == "accepted"
                and actual.freshness_status == "fresh"
                and actual.authority not in {"search_derived", "unverified"},
                original_content_hash=actual.source_content_hash,
                assertion_hash=canonical_hash(
                    {
                        "excerpt": actual.excerpt,
                        "structured_payload_ref": actual.structured_payload_ref,
                    }
                ),
            )
        )
    return output, gaps


def _claims(
    session: Session,
    *,
    trusted: TrustedActionResearchInputs,
    sidecar: ResearchActionSynthesisSidecar,
    documents: _DocumentReads | None,
) -> list[ActionBasisEvidence]:
    refs = set(sidecar.synthesis.claim_signal_refs)
    refs.update(ref for intent in sidecar.synthesis.action_intents for ref in intent.claim_refs)
    if not refs or documents is None:
        # Missing authenticated metadata stays missing, so the pure builder
        # blocks each branch which depends on it; never silently promotes it.
        return []
    from .research_signal import resolve_claim_signal_in_session

    output = []
    for claim_id in sorted(refs):
        frozen = resolve_claim_signal_in_session(
            session,
            run_id=trusted.run_id,
            claim_id=claim_id,
            cutoff=sidecar.producer_cutoff,
            store=cast("DocumentBundleStore", documents),
        )
        if frozen is None:
            continue
        signal = frozen.signal
        host = urlsplit(str(signal.source.canonical_url)).hostname
        output.append(
            ActionBasisEvidence(
                ref=claim_id,
                payload_hash=frozen.payload_hash,
                kind="claim",
                known_at=signal.source.first_seen_at,
                independence_key=signal.source.syndication_cluster
                or (host or signal.source.source_id).lower().removeprefix("www."),
                # This is a verifiable opinion, never an independent Fact.
                eligible=True,
                original_content_hash=signal.content_hash,
            )
        )
    return output


def _store_sidecar(session: Session, prepared: _Publication, published_at: datetime) -> None:
    sidecar = prepared.sidecar
    encoded = observation_json(sidecar.model_dump(mode="json"))
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    existing = session.scalar(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.run_id == sidecar.run_id,
            RunRoleOutputRecord.role == prepared.sidecar_role,
        )
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        if (
            existing.output_id != prepared.sidecar_id
            or existing.schema_version != _SCHEMA
            or existing.payload_json != encoded
            or existing.payload_hash != digest
        ):
            raise ValueError("action_sidecar_immutable_conflict")
        return
    session.add(
        RunRoleOutputRecord(
            output_id=prepared.sidecar_id,
            run_id=sidecar.run_id,
            role=prepared.sidecar_role,
            payload_json=encoded,
            payload_hash=digest,
            schema_version=_SCHEMA,
            created_at=published_at,
        )
    )
    session.flush()


class ActionPublicationService:
    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = utcnow,
        document_store: DocumentBundleStore | None = None,
        channel: str = "local",
        skill_reader=None,
    ) -> None:
        self.database = database
        self.clock = clock
        self.document_store = document_store
        self.channel = channel
        self.skill_reader = skill_reader

    def publish(
        self,
        *,
        run_id: str,
        event_id: str,
        artifact_id: str,
        reference: GraphFinalProjectionRef,
        opportunity_id: str,
    ) -> StoredCommandReceipt:
        for identity in (run_id, event_id, artifact_id, opportunity_id):
            _identity(identity)
        if not isinstance(reference, GraphFinalProjectionRef):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("action_publication_reference_invalid")
        reference = GraphFinalProjectionRef.model_validate_json(reference.model_dump_json())
        request_id = "publish_" + canonical_hash([run_id, opportunity_id])
        payload_hash = canonical_hash(
            {
                "run_id": run_id,
                "event_id": event_id,
                "artifact_id": artifact_id,
                "reference": reference.model_dump(mode="json"),
                "opportunity_id": opportunity_id,
            }
        )
        # A timed-out caller may replay after expiry or a later control command.
        with self.database.session() as session:
            if receipt := _replay(ActionRepository(session), request_id, payload_hash):
                return receipt
            # Only the immutable server Task chooses this branch. Callers keep
            # the same committed Run/report/Graph API and cannot submit plans.
            from .research_task import read_task_request

            run = session.get(RunRecord, run_id, populate_existing=True)
            task = read_task_request(session, run) if run is not None else None
            from ..ports.research_synthesis_protocol import PERPETUAL_OUTPUT_SCHEMA_REFS

            perpetual = task is not None and task[0].output_schema_ref in PERPETUAL_OUTPUT_SCHEMA_REFS
        if perpetual:
            from .perpetual_action_publication import publish_perpetual

            return publish_perpetual(
                self, run_id=run_id, event_id=event_id, artifact_id=artifact_id,
                reference=reference, opportunity_id=opportunity_id,
                request_id=request_id, payload_hash=payload_hash,
            )
        documents = _DocumentReads(self.document_store) if self.document_store else None

        def read(session: Session) -> _Publication:
            opportunity = ActionRepository(session).get_opportunity(opportunity_id)
            if opportunity is None:
                raise ValueError("action_opportunity_not_found")
            if opportunity.event_ref is not None and opportunity.event_ref != event_id:
                raise ValueError("action_opportunity_event_mismatch")
            review = review_for_run_in_session(session, run_id)
            if review is not None and (
                review.opportunity.id != opportunity_id or review.event_id != event_id
            ):
                raise ValueError("action_review_publication_opportunity_mismatch")
            reject_final_without_action_synthesis_in_session(
                session, run_id=run_id, event_id=event_id,
                artifact_id=artifact_id, reference=reference,
            )
            trusted = read_trusted_research_in_session(
                session,
                run_id=run_id,
                event_id=event_id,
                artifact_id=artifact_id,
                reference=reference,
            )
            skill_read = self.skill_reader.read_in_session(session, trusted=trusted) if self.skill_reader else None
            sidecar = _sidecar(trusted, opportunity, skill_read)
            sidecar_id = (
                "action_synthesis_"
                + canonical_hash(
                    [
                        run_id,
                        sidecar.scope_id,
                        sidecar.horizon,
                    ]
                )[:40]
            )
            evidence, source_gaps = _evidence(session, trusted, sidecar)
            evidence.extend(
                _claims(
                    session,
                    trusted=trusted,
                    sidecar=sidecar,
                    documents=documents,
                )
            )
            if skill_read is not None:
                evidence.extend(skill_read.evidence)
            return _Publication(
                sidecar,
                sidecar_id,
                "research_action_synthesis_" + sidecar.horizon,
                opportunity,
                tuple(evidence),
                tuple(source_gaps),
                tuple(sorted(documents.hashes.items())) if documents else (),
                review,
                skill_originals=skill_read.originals if skill_read else (),
            )

        prepared = prepare_originals(self.database, read)
        original = prepared.value
        with self.database.session() as session:
            begin_immediate(session)
            repository = ActionRepository(session)
            if receipt := _replay(repository, request_id, payload_hash):
                return receipt
            prepared.verify(session)
            if original.skill_originals:
                if self.skill_reader is None:
                    raise ValueError("action_skill_reader_unavailable")
                self.skill_reader.verify_originals(original.skill_originals)
            if self.document_store is not None:
                for ref, digest in original.document_originals:
                    if hashlib.sha256(self.document_store.read(ref)).hexdigest() != digest:
                        raise ValueError("action_document_original_changed")
            # Publication time is taken after original verification under the
            # writer lock. Queue/read time cannot be an earlier availability.
            published_at = aware_utc(self.clock())
            if published_at >= original.opportunity.time_end or any(
                published_at >= candidate.valid_until
                for candidate in original.sidecar.synthesis.action_intents
                if (candidate.scope_id, candidate.horizon)
                == (
                    original.opportunity.scope_id,
                    original.opportunity.horizon,
                )
            ):
                raise ValueError("action_publication_expired_during_preparation")
            derive = (
                derive_action_revision if original.review is not None else derive_action_publication
            )
            derived = derive(
                sidecar=original.sidecar,
                sidecar_ref=original.sidecar_id,
                opportunity=original.opportunity,
                published_at=published_at,
                evidence=original.evidence,
            )
            revision_changes = (
                plan_revision_changes(
                    session,
                    derived=derived,
                    intent=original.review,
                    sidecar=original.sidecar,
                    published_at=published_at,
                )
                if original.review is not None
                else None
            )
            _store_sidecar(session, original, published_at)
            has_updates = revision_changes is None or any(
                item.disposition == "updated" for item in revision_changes
            )
            if has_updates:
                repository.append_snapshot(derived.snapshot)
            outbox = OutboxService(self.database)
            plans = []
            for index, branch in enumerate(derived.branches):
                change = revision_changes[index] if revision_changes is not None else None
                revision = change.revision if change is not None else branch.revision
                if change is None:
                    if repository.get_head(revision.plan_id) is not None:
                        raise ValueError("action_publication_existing_plan_without_receipt")
                    repository.create_plan(revision, head=branch.head)
                elif change.disposition == "updated":
                    repository.append_revision(
                        revision,
                        expected_revision_id=change.previous_head.current_revision_id,
                        expected_state_version=change.previous_head.state_version,
                        next_head=change.head,
                    )
                if change is not None and repository.get_head(revision.plan_id) != change.head:
                    raise ValueError("action_review_written_head_mismatch")
                message = make_notification_envelope(
                    subject_kind="action_revision",
                    subject_id=revision.revision_id,
                    channel=self.channel,
                    dedupe_key=f"action-review:{request_id}:{revision.position_context}:{self.channel}"
                    if change is not None
                    else f"action-revision:{revision.revision_id}:published:{self.channel}",
                    subject=f"{revision.scope_id.split(':')[3]} {revision.horizon} 行动方案更新："
                    + {
                        "flat": "空仓",
                        "long": "已有多仓",
                        "unknown": "待确认仓位",
                    }[revision.position_context],
                    body=("新研究已保存，旧方案继续生效；保护更新待认证。" + branch.revision.reason)
                    if change is not None and change.disposition == "retained"
                    else revision.reason,
                    created_at=published_at,
                )
                outbox_id = outbox.enqueue_subject(message, session=session)
                plans.append(
                    {
                        "position_context": revision.position_context,
                        "plan_id": revision.plan_id,
                        "revision_id": revision.revision_id,
                        "revision_hash": revision.payload_hash,
                        "outbox_id": outbox_id,
                        **(
                            {
                                "disposition": change.disposition,
                                "reason": change.reason,
                                "next_review_at": change.next_review_at.isoformat()
                                if change.next_review_at
                                else None,
                            }
                            if change is not None
                            else {}
                        ),
                    }
                )
            receipt = repository.append_command_receipt(
                make_command_receipt(
                    owner_id=_OWNER,
                    request_id=request_id,
                    plan_id=plans[0]["plan_id"],
                    operation="publish",
                    payload_hash=payload_hash,
                    accepted_at=published_at,
                    response={
                        "status": "published" if has_updates else "review_only",
                        **(
                            {
                                "review_schema": REVIEW_SCHEMA,
                                "run_id": run_id,
                                "review_trigger_key": original.review.trigger_key,
                                "review_intent_hash": original.review.payload_hash,
                            }
                            if original.review is not None
                            else {}
                        ),
                        "opportunity_id": opportunity_id,
                        "decision_id": derived.snapshot.id if has_updates else None,
                        "snapshot_hash": derived.snapshot.snapshot_hash if has_updates else None,
                        "sidecar_ref": original.sidecar_id,
                        "sidecar_hash": original.sidecar.payload_hash,
                        "branches": plans,
                        "source_gaps": [
                            {"ref": ref, "reason": reason} for ref, reason in original.source_gaps
                        ],
                        "server_time": published_at.isoformat(),
                    },
                )
            )
            # Verify all inputs before our one intentional input-row transition.
            prepared.verify_after_writes(session)
            if original.review is None:
                repository.set_opportunity_status(
                    opportunity_id,
                    expected_status="open",
                    status="decided",
                )
            return receipt
