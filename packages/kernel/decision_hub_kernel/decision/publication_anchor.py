"""Bind a reviewed relative target to a publication clock, without side effects.

The unique publication service supplies its database clock under the writer and
atomically stores these originals with its first-publication CAS. This function
does not confer review, ownership, source, or financial qualification.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSourceRefV1,
    PendingPublicationTargetV1,
    PublicationAnchorReceiptV1,
    PublicationAnchorRuleV1,
    PublicationBoundTargetV1,
)

from ..ports.market_path import aware_utc, canonical_hash


@dataclass(frozen=True)
class BoundPublicationTarget:
    target: PublicationBoundTargetV1
    target_ref: DecisionSourceRefV1
    receipt: PublicationAnchorReceiptV1
    receipt_ref: DecisionSourceRefV1


def _reference(prefix: str, body) -> DecisionSourceRefV1:
    digest = canonical_hash(body.model_dump(mode="json"))
    return DecisionSourceRefV1(ref=prefix + digest, hash=digest)


def freeze_publication_anchor(
    *,
    candidate_target: PendingPublicationTargetV1 | dict,
    anchor_rule: PublicationAnchorRuleV1 | dict,
    candidate_target_ref: DecisionSourceRefV1 | dict,
    anchor_rule_ref: DecisionSourceRefV1 | dict,
    assigned_at: datetime,
    opportunity_id: str,
    commit_id: str,
) -> BoundPublicationTarget:
    candidate = PendingPublicationTargetV1.model_validate(candidate_target)
    rule = PublicationAnchorRuleV1.model_validate(anchor_rule)
    candidate_ref = DecisionSourceRefV1.model_validate(candidate_target_ref)
    rule_ref = DecisionSourceRefV1.model_validate(anchor_rule_ref)
    if (candidate_ref.hash != canonical_hash(candidate.model_dump(mode="json"))
            or rule_ref.hash != canonical_hash(rule.model_dump(mode="json"))):
        raise ValueError("publication_anchor_source_hash_mismatch")
    matching = (
        "anchor", "horizon", "duration_seconds", "grid_seconds", "clock_rule",
        "price_basis", "threshold_fraction", "method_ref", "clock_policy_ref",
        "valid_until", "invalidation_refs",
    )
    if rule.candidate_target_ref != candidate_ref or any(
        getattr(candidate, key) != getattr(rule, key) for key in matching
    ):
        raise ValueError("publication_anchor_rule_binding_mismatch")
    assigned = aware_utc(assigned_at)
    if assigned >= candidate.valid_until:
        raise ValueError("publication_anchor_candidate_expired")
    start = assigned.replace(second=0, microsecond=0) + timedelta(seconds=60)
    end = start + timedelta(seconds=candidate.duration_seconds)
    semantics = (
        "clock_policy_ref", "instrument_ref", "horizon", "duration_seconds", "price_basis",
        "threshold_fraction", "method_ref", "target_family_ref", "label_rule_ref", "protocol_ref",
        "conditioning_ref",
    )
    candidate_body = candidate.model_dump(mode="json")
    target = PublicationBoundTargetV1.model_validate({
        "schema_version": "publication-bound-target.v1",
        "candidate_target_ref": candidate_ref.model_dump(mode="json"),
        "anchor_rule_ref": rule_ref.model_dump(mode="json"),
        "assigned_at": assigned, "window_start": start, "window_end": end,
        **{key: candidate_body[key] for key in semantics},
    })
    target_ref = _reference("publication_target_", target)
    receipt = PublicationAnchorReceiptV1.model_validate({
        "schema_version": "publication-anchor-receipt.v1",
        "opportunity_id": opportunity_id, "commit_id": commit_id,
        "candidate_target_ref": candidate_ref.model_dump(mode="json"),
        "anchor_rule_ref": rule_ref.model_dump(mode="json"),
        "clock_policy_ref": candidate_body["clock_policy_ref"],
        "assigned_at": assigned, "target_start": start, "target_end": end,
        "duration_seconds": candidate.duration_seconds,
        "final_target_ref": target_ref.model_dump(mode="json"),
    })
    return BoundPublicationTarget(
        target, target_ref, receipt, _reference("publication_anchor_", receipt),
    )
