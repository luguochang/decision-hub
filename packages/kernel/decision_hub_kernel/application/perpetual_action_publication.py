"""Internal V2 branch of the unique ActionPublicationService.

No external endpoint accepts a plan, mapped observation or a verified flag.
Committed Run/report/Graph identities select the native packet; the existing
Action repository, command ledger and outbox share one physical transaction.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSourceRefV1,
)

from ..persistence.action_repository import ActionRepository, make_command_receipt
from ..persistence.db import DshSessionLinkRecord, RunRecord, RunRoleOutputRecord, as_utc
from ..persistence.original_preparation import begin_immediate, prepare_originals
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.sources import make_notification_envelope
from .commit import PUBLICATION_SOURCE_ROLE
from .outbox import OutboxService
from .perpetual_research_reader import read_trusted_perpetual_research_in_session
from .provider_input_receipts import strict_json
from .research_graph_binding import (
    GraphFinalProjectionRef,
    read_graph_final_in_session,
)
from .research_publication_binding import observation_json

_OWNER = "system:action-publication:v1"


@dataclass(frozen=True)
class _Read:
    trusted: object
    opportunity: object
    graph_recorded_at: object


def _read_committed(
    session: Session,
    *,
    run_id: str,
    event_id: str,
    artifact_id: str,
    reference: GraphFinalProjectionRef,
    opportunity_id: str,
    at,
) -> _Read:
    run = session.get(RunRecord, run_id, populate_existing=True)
    source = session.scalars(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role == PUBLICATION_SOURCE_ROLE,
        )
        .execution_options(populate_existing=True)
    ).one_or_none()
    if (
        run is None
        or run.status not in {"completed", "degraded"}
        or run.event_id != event_id
        or run.artifact_id != artifact_id
        or source is None
        or as_utc(run.finished_at) is None
    ):
        raise ValueError("perpetual_publication_committed_run_required")
    body = strict_json(source.payload_json)
    if (
        source.payload_hash != hashlib.sha256(source.payload_json.encode()).hexdigest()
        or source.output_id != "publication_" + source.payload_hash[:40]
        or observation_json(body) != source.payload_json
        or body["schema_version"] != source.schema_version
        or body["run_id"] != run_id
        or body["event_id"] != event_id
        or body["artifact_id"] != artifact_id
        or body["graph_final_ref"] != reference.model_dump(mode="json")
        or body["recorded_at"] != as_utc(run.finished_at).isoformat()
    ):
        raise ValueError("perpetual_publication_committed_source_mismatch")
    final = read_graph_final_in_session(
        session,
        run_id=run_id,
        reference=reference,
        expected_lease_owner=None,
        require_current_permission=False,
    )
    if final.receipt.schema_version != "research-perpetual-graph-final.v1":
        raise ValueError("perpetual_publication_graph_version_mismatch")
    link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
    context = final.receipt.native_context
    if (
        link is None
        or link.generation != context.generation
        or link.dsh_session_id != context.dsh_session_id
        or link.error_code is not None
        or as_utc(run.finished_at) > aware_utc(at)
    ):
        raise ValueError("perpetual_publication_generation_or_clock_mismatch")
    trusted = read_trusted_perpetual_research_in_session(
        session,
        mapped_ref=final.receipt.selected_synthesis_observation_ref,
        known_at=at,
    )
    opportunity = ActionRepository(session).get_opportunity(opportunity_id)
    if (
        trusted.run_id != run_id
        or trusted.opportunity_id != opportunity_id
        or opportunity is None
        or opportunity.schema_version != "decision-opportunity.v2"
        or opportunity.event_ref != event_id
        or opportunity.status != "open"
    ):
        raise ValueError("perpetual_publication_opportunity_mismatch")
    return _Read(trusted, opportunity, final.receipt.recorded_at)


def _store_original(session: Session, run_id: str, original, at) -> None:
    """One immutable Run-local locator for a content reference; no new writer."""
    reference = original.ref.model_dump(mode="json")
    payload = original.body_json
    if hashlib.sha256(payload.encode()).hexdigest() != reference["hash"]:
        raise ValueError("perpetual_publication_original_hash_mismatch")
    role = "perpetual_publication_original:" + canonical_hash(reference)
    output_id = "perp_pub_" + canonical_hash([run_id, role])
    old = session.get(RunRoleOutputRecord, output_id, populate_existing=True)
    if old is not None:
        if (old.run_id, old.role, old.payload_hash, old.payload_json) != (
            run_id,
            role,
            reference["hash"],
            payload,
        ):
            raise ValueError("perpetual_publication_original_conflict")
        return
    session.add(
        RunRoleOutputRecord(
            output_id=output_id,
            run_id=run_id,
            role=role,
            payload_json=payload,
            payload_hash=reference["hash"],
            schema_version=original.schema_version,
            created_at=at,
        )
    )


def publish_perpetual(
    service,
    *,
    run_id: str,
    event_id: str,
    artifact_id: str,
    reference: GraphFinalProjectionRef,
    opportunity_id: str,
    request_id: str,
    payload_hash: str,
):
    """Called only by ActionPublicationService after frozen Task V2 selection."""
    from ..decision.perpetual_publication import (
        PerpetualPublicationOriginal,
        derive_perpetual_publication,
    )
    from ..decision.publication_anchor import freeze_publication_anchor
    from .action_publication import _replay
    from .event_scenario_publication import (
        primary_publication_trusted,
        store_event_scenario_publication_in_session,
    )
    from .perpetual_cost_assumptions import read_perpetual_cost_assumptions

    read_at = aware_utc(service.clock())
    prepared = prepare_originals(
        service.database,
        lambda session: _read_committed(
            session,
            run_id=run_id,
            event_id=event_id,
            artifact_id=artifact_id,
            reference=reference,
            opportunity_id=opportunity_id,
            at=read_at,
        ),
    )
    with service.database.session() as session:
        begin_immediate(session)
        repository = ActionRepository(session)
        if receipt := _replay(repository, request_id, payload_hash):
            return receipt
        prepared.verify(session)
        original = prepared.value
        at = aware_utc(service.clock())
        if not read_at <= at < original.opportunity.time_end:
            raise ValueError("perpetual_publication_clock_expired")
        primary_trusted = primary_publication_trusted(original.trusted)
        synthesis = strict_json(primary_trusted.synthesis_json)
        bound = freeze_publication_anchor(
            candidate_target=synthesis["candidate_target"],
            anchor_rule=synthesis["anchor_rule"],
            candidate_target_ref=synthesis["candidate_set"]["candidate_target_ref"],
            anchor_rule_ref=synthesis["candidate_set"]["anchor_rule_ref"],
            assigned_at=at,
            opportunity_id=opportunity_id,
            commit_id=request_id,
        )
        materials = strict_json(original.trusted.materials_json)
        costs = read_perpetual_cost_assumptions(materials["cost_assumptions_ref"])
        derived = derive_perpetual_publication(
            trusted=primary_trusted,
            bound=bound,
            cost_original=costs,
        )
        # Archive the exact admitted method, never today's recomputed code view.
        # PC/history readers can resolve this binding after implementation moves.
        _store_original(
            session,
            run_id,
            PerpetualPublicationOriginal(
                role="comparison_method",
                schema_version=materials["comparison_method"]["schema_version"],
                reference=DecisionSourceRefV1.model_validate(materials["method_ref"]),
                body_json=observation_json(materials["comparison_method"]),
            ),
            at,
        )
        for item in derived.originals:
            _store_original(session, run_id, item, at)
        repository.append_snapshot(derived.snapshot)
        branches = []
        outbox = OutboxService(service.database)
        for branch in derived.branches:
            if branch.revision is None or branch.head is None:
                continue
            revision = branch.revision
            repository.create_plan(revision, head=branch.head)
            envelope = make_notification_envelope(
                subject_kind="action_revision",
                subject_id=revision.revision_id,
                channel=service.channel,
                dedupe_key=f"action-revision:{revision.revision_id}:published:{service.channel}",
                subject=revision.context.instrument_id.removeprefix("okx:")
                + " "
                + revision.horizon
                + " 事前研究建议",
                body=revision.reason,
                created_at=at,
            )
            outbox_id = outbox.enqueue_subject(envelope, session=session)
            branches.append(
                dict(
                    position_context=revision.position_context,
                    plan_id=revision.plan_id,
                    revision_id=revision.revision_id,
                    revision_hash=revision.payload_hash,
                    outbox_id=outbox_id,
                )
            )
        if not branches:
            raise ValueError("perpetual_publication_no_reviewed_action_branch")
        event_publication_ref = store_event_scenario_publication_in_session(
            session,
            trusted=original.trusted,
            publishing_request_id=request_id,
            publishing_payload_hash=payload_hash,
            plan_ids=[row["plan_id"] for row in branches],
            decision_id=derived.snapshot.id,
            snapshot_hash=derived.snapshot.snapshot_hash,
            at=at,
        )
        event_link = (
            {}
            if event_publication_ref is None
            else dict(
                event_scenario_publication_ref=event_publication_ref,
                event_scenario_plan_ref=strict_json(original.trusted.synthesis_json)[
                    "event_plan_ref"
                ],
            )
        )
        repository.set_opportunity_status(
            opportunity_id, expected_status="open", status="decided", reason=None
        )
        receipt = repository.append_command_receipt(
            make_command_receipt(
                owner_id=_OWNER,
                request_id=request_id,
                plan_id=branches[0]["plan_id"],
                operation="publish",
                payload_hash=payload_hash,
                accepted_at=at,
                response=dict(
                    status="published",
                    opportunity_id=opportunity_id,
                    decision_id=derived.snapshot.id,
                    snapshot_hash=derived.snapshot.snapshot_hash,
                    branches=branches,
                    qualification="research_trial",
                    execution="paper_only",
                    uncovered_branches=list(derived.uncovered_branches),
                    gaps=list(derived.gaps),
                    server_time=at.isoformat(),
                    **event_link,
                ),
            )
        )
        return receipt
