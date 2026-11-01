"""Authenticate and derive inherited protection without fabricating an entry order."""

from __future__ import annotations

import json
from decimal import localcontext
from typing import Any

from packages.contracts_py.decision_hub_contracts.generated.action_execution_v1 import (
    ActionExecutionEpisodeV1,
)
from packages.kernel.decision_hub_kernel.application.action_execution_originals import (
    read_activation_in_session,
    read_publication_in_session,
)
from packages.kernel.decision_hub_kernel.application.action_paper_opening import (
    prepare_opening_in_session,
)
from packages.kernel.decision_hub_kernel.decision.action_execution_clock import (
    exit_trigger_deadline,
)
from packages.kernel.decision_hub_kernel.persistence.action_execution_repository import (
    ExecutionRepository,
    account_for,
    canonical,
    episode_ref,
    number,
    seal,
)

from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord, as_utc
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash, spot_scope


def derive_inherited_episode(*, opening, checkpoint, revision, opportunity, risk, activated_at):
    account = account_for(checkpoint)
    if (
        checkpoint.version != 1
        or checkpoint.portfolio_identity.scenario != "existing_long"
        or account.quantity <= 0
        or not revision.published_at <= activated_at < revision.valid_until
    ):
        raise ValueError("execution_inherited_initial_protection_required")
    if not any(
        ref.ref == opening.id and ref.hash == opening.payload_hash
        for ref in checkpoint.opening_original_refs
    ):
        raise ValueError("execution_inherited_opening_checkpoint_mismatch")
    deadline = exit_trigger_deadline(
        revision_valid_until=revision.valid_until,
        opportunity_end=opportunity.time_end,
        entry_at=opening.reference_at,
        max_hold_seconds=min({"30m": 1800, "24h": 86400, "72h": 259200}[revision.horizon], 86400),
    )
    if deadline <= activated_at:
        raise ValueError("execution_inherited_protection_window_exhausted")
    with localcontext() as context:
        context.prec = 50
        stop = opening.reference_price - risk.value
        target = opening.reference_price + 2 * risk.value
        initial_r = account.quantity * risk.value
    if stop <= 0:
        raise ValueError("execution_inherited_nonpositive_stop")
    body: dict[str, Any] = {
        field.alias or key: None for key, field in ActionExecutionEpisodeV1.model_fields.items()
    }
    body.update(
        schema_version="action-execution-episode.v1",
        id="episode_"
        + canonical_hash([checkpoint.portfolio_id, "inherited", revision.revision_id]),
        portfolio_id=checkpoint.portfolio_id,
        version=1,
        opening_kind="inherited",
        opening_checkpoint_ref=checkpoint.id,
        opening_checkpoint_hash=checkpoint.payload_hash,
        opportunity_id=opportunity.id,
        plan_id=revision.plan_id,
        revision_id=revision.revision_id,
        revision_hash=revision.payload_hash,
        entry_at=opening.reference_at,
        reference_price=number(opening.reference_price),
        initial_quantity=number(account.quantity),
        remaining_quantity=number(account.quantity),
        U_basis_ref="execution_u_" + risk.payload_hash,
        U_basis_hash=risk.payload_hash,
        risk_unit_value=number(risk.value),
        initial_R=number(initial_r),
        stop_price=number(stop),
        target_price=number(target),
        original_DSL_revision_ref=revision.revision_id,
        original_DSL_revision_hash=revision.payload_hash,
        exit_trigger_deadline=deadline,
        status="open",
        checkpoint_refs=[{"ref": checkpoint.id, "hash": checkpoint.payload_hash}],
        realized_gross="0",
        realized_cost="0",
        realized_net="0",
        price_coverage="0",
        exclusions=[
            "sampled_quotes_not_continuous",
            "inherited_no_entry_order",
            "protection_from_activation_only",
        ],
        recorded_at=activated_at,
    )
    return seal(ActionExecutionEpisodeV1, body)


def activation_ref(episode_id: str) -> str:
    return "inherited_activation_" + canonical_hash([episode_id])


def activation_payload(*, episode, original, publication, opening):
    return dict(
        schema_version="action-inherited-activation.v1",
        episode_ref=episode_ref(episode),
        episode_hash=episode.payload_hash,
        portfolio_id=episode.portfolio_id,
        revision_id=episode.revision_id,
        revision_hash=episode.revision_hash,
        evaluation_id=original.evaluation.id,
        evaluation_hash=original.evaluation.evaluation_hash,
        input_ref=original.evaluation.input_ref,
        input_hash=original.evaluation.input_hash,
        U_basis_ref=episode.U_basis_ref,
        U_basis_hash=episode.U_basis_hash,
        publication_hash=publication.publication.payload_hash,
        opening_ref=opening.id,
        opening_hash=opening.payload_hash,
        activated_at=episode.recorded_at.isoformat(),
    )


def read_inherited_in_session(
    session, *, episode, store, feature_root, archive, observed_at, checkpoint=None
):
    repository = ExecutionRepository(session)
    row = session.get(RunRoleOutputRecord, activation_ref(episode.id), populate_existing=True)
    if row is None or row.role != "action_inherited_activation":
        raise ValueError("execution_inherited_activation_original_missing")
    payload = json.loads(row.payload_json)
    if canonical(payload) != row.payload_json or canonical_hash(payload) != row.payload_hash:
        raise ValueError("execution_inherited_activation_hash_mismatch")
    first = repository.episode(payload["episode_ref"], payload["episode_hash"])
    if (
        first.id != episode.id
        or first.version != 1
        or first.opening_kind != "inherited"
        or first.recorded_at > observed_at
        or first.recorded_at != as_utc(row.created_at)
    ):
        raise ValueError("execution_inherited_activation_binding_mismatch")
    account = repository.checkpoint(first.opening_checkpoint_ref, first.opening_checkpoint_hash)
    identity = account.portfolio_identity
    protocol = repository.protocol(identity.protocol_ref)
    original = read_activation_in_session(
        session,
        plan_id=first.plan_id,
        revision_id=first.revision_id,
        evaluation_id=payload["evaluation_id"],
        store=store,
        feature_root=feature_root,
        observed_at=first.recorded_at,
        checkpoint=checkpoint,
    )
    publication = read_publication_in_session(
        session,
        revision=original.revision,
        observed_at=first.recorded_at,
        channel=identity.channel,
        owner_id=identity.owner_id,
    )
    opening = prepare_opening_in_session(
        session,
        protocol=protocol,
        scope=spot_scope(identity.scope_id.split(":")[3]),
        archive=archive,
        observed_at=observed_at,
    )
    expected = derive_inherited_episode(
        opening=opening,
        checkpoint=account,
        revision=original.revision,
        opportunity=publication.opportunity,
        risk=original.risk,
        activated_at=first.recorded_at,
    )
    if (
        expected != first
        or payload
        != activation_payload(
            episode=first, original=original, publication=publication, opening=opening
        )
        or row.run_id != publication.decision.run_id
        or publication.opportunity.recipe_hash != identity.method_hash
    ):
        raise ValueError("execution_inherited_activation_replay_mismatch")
    return original, publication
