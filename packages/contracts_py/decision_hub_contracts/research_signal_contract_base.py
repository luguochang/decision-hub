"""Canonical research-signal shape and field relations, without trust authority."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import AnyUrl, BaseModel, ConfigDict, field_validator, model_validator
from referencing import Registry, Resource

from .decision_product_contract_base import _wire


def _signal_wire(value: object) -> object:
    if isinstance(value, AnyUrl):
        return str(value)
    if isinstance(value, BaseModel):
        return _signal_wire(value.model_dump(mode="python"))
    if isinstance(value, dict):
        return {key: _signal_wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_signal_wire(item) for item in value]
    return _wire(value)


@lru_cache(maxsize=1)
def _validators() -> dict[str, Draft202012Validator]:
    directory = Path(__file__).resolve().parents[3] / "contracts/schemas"
    resources = []
    documents = {}
    for filename in (
        "research_signal.schema.yaml",
        "decision_product_v1.schema.yaml",
        "agentic_research.schema.yaml",
    ):
        path = directory / filename
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        documents[filename] = document
        resource = Resource.from_contents(document)
        resources.extend(((path.as_uri(), resource), (document["$id"], resource)))
    registry = Registry().with_resources(resources)
    source = directory / "research_signal.schema.yaml"
    document = documents[source.name]
    return {
        definition["title"]: Draft202012Validator(
            {"$id": source.as_uri(), "$defs": document["$defs"], "$ref": f"#/$defs/{key}"},
            registry=registry,
            format_checker=FormatChecker(),
        )
        for key, definition in document["$defs"].items()
    }


def _canonical_name(cls: type) -> str | None:
    return next((base.__name__ for base in cls.__mro__ if base.__name__ in _validators()), None)


class ResearchSignalContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def canonical_shape(cls, value: object) -> object:
        wire = _signal_wire(value)
        name = _canonical_name(cls)
        if name is not None:
            error = next(_validators()[name].iter_errors(wire), None)
            if error is not None:
                path = ".".join(str(item) for item in error.absolute_path)
                raise ValueError(f"research_signal_shape_invalid:{error.validator}:{path}")
        elif cls.__name__.startswith("Decision"):
            # Codegen inlines external classes. Reuse the real W1 validator so
            # its temporal and purpose rules remain effective inside candidates.
            from .generated import decision_product_v1

            original = getattr(decision_product_v1, cls.__name__, None)
            if isinstance(original, type) and issubclass(original, BaseModel):
                original.model_validate(value)
        return value

    @field_validator("*", mode="after")
    @classmethod
    def normalize_own_times(cls, value: object) -> object:
        # Do not normalize nested legacy candidate timestamps: their existing
        # serialization is part of frozen hashes. Ports reuse its actual class.
        if _canonical_name(cls) is not None and isinstance(value, datetime):
            return value.astimezone(UTC)
        return value

    @model_validator(mode="after")
    def field_relations(self):
        name = _canonical_name(type(self))
        if name == "ResearchPublicationTimeV1":
            if self.precision == "interval" and self.window_start >= self.window_end:
                raise ValueError("research_publication_interval_invalid")
        elif name == "ResearchClaimSignalV1":
            if self.supersedes_version_id == self.version_id:
                raise ValueError("research_claim_self_supersedes")
        elif name == "ResearchMechanismLagV1":
            if self.minimum_seconds > self.maximum_seconds:
                raise ValueError("research_mechanism_lag_invalid")
        elif name == "ResearchMechanismReviewV1":
            node_ids = [node.id for node in self.nodes]
            edge_ids = [edge.id for edge in self.edges]
            if len(set(node_ids)) != len(node_ids) or len(set(edge_ids)) != len(edge_ids):
                raise ValueError("research_mechanism_duplicate_identity")
            for edge in self.edges:
                if (
                    edge.from_node not in node_ids
                    or edge.to_node not in node_ids
                    or edge.from_node == edge.to_node
                ):
                    raise ValueError("research_mechanism_endpoint_invalid")
            if self.priced_in_status != "unknown" and not self.priced_in_refs:
                raise ValueError("research_mechanism_priced_in_evidence_required")
        elif name == "ResearchActionCandidateV1":
            if not self.as_of < self.next_review_at <= self.valid_until:
                raise ValueError("research_action_window_invalid")
            conditions = [*self.entry, *self.exit, *self.invalidation]
            if len({condition.id for condition in conditions}) != len(conditions):
                raise ValueError("research_action_duplicate_condition")
            for items, purposes in (
                (self.entry, {"entry"}),
                (self.exit, {"exit", "risk_upgrade"}),
                (self.invalidation, {"invalidation"}),
            ):
                if any(
                    condition.purpose not in purposes
                    or not self.as_of < condition.valid_until <= self.valid_until
                    for condition in items
                ):
                    raise ValueError("research_action_condition_binding_invalid")
        elif name == "ResearchDecisionSynthesisV1":
            if self.request_id != self.research_candidate_v1.request_id:
                raise ValueError("research_synthesis_request_mismatch")
            reviews = {review.review_id: review for review in self.mechanism_reviews}
            if len(reviews) != len(self.mechanism_reviews):
                raise ValueError("research_synthesis_duplicate_review")
            branches = [(x.scope_id, x.horizon, x.position_context) for x in self.action_intents]
            if len(set(branches)) != len(branches):
                raise ValueError("research_synthesis_duplicate_action_branch")
            for action in self.action_intents:
                review = reviews.get(action.mechanism_review_ref)
                if review is None or (
                    review.scope_id != action.scope_id
                    or review.horizon != action.horizon
                    or review.cutoff != action.as_of
                ):
                    raise ValueError("research_synthesis_mechanism_binding_invalid")
                if not set(action.claim_refs).issubset(self.claim_signal_refs):
                    raise ValueError("research_synthesis_claim_binding_invalid")
        elif name == "ResearchNativeSelectionV1":
            if (self.presence_generation is None) != (self.presence_ref is None):
                raise ValueError("research_native_presence_pair_invalid")
        elif name == "ResearchActionSynthesisSidecarV1":
            if self.producer_cutoff > self.cutoff:
                raise ValueError("research_sidecar_producer_after_cutoff")
            if self.request_id != self.synthesis.request_id:
                raise ValueError("research_sidecar_request_mismatch")
            if self.selection_witness.presence_generation not in (None, self.generation):
                raise ValueError("research_sidecar_presence_generation_mismatch")
            if any(
                item.run_id != self.run_id or item.cutoff != self.producer_cutoff
                for item in self.synthesis.mechanism_reviews
            ) or any(item.as_of != self.producer_cutoff for item in self.synthesis.action_intents):
                raise ValueError("research_sidecar_context_mismatch")
            if not any(
                item.scope_id == self.scope_id and item.horizon == self.horizon
                for item in self.synthesis.action_intents
            ):
                raise ValueError("research_sidecar_target_intent_required")
        elif name == "DecisionUiReviewBranchV1":
            if (
                self.applied != (self.disposition == "updated")
                or self.current_head.plan_id != self.plan_id
                or (self.candidate is None) != (self.mechanism is None)
                or (
                    self.candidate is not None
                    and (
                        self.candidate.position_context != self.position_context
                        or self.candidate.mechanism_review_ref != self.mechanism.review_id
                    )
                )
            ):
                raise ValueError("decision_ui_review_branch_mismatch")
        elif name == "DecisionUiReviewResultV1":
            if (
                {item.position_context for item in self.branches} != {"flat", "long", "unknown"}
                or len({item.plan_id for item in self.branches}) != 3
                or (self.status == "published") != any(item.applied for item in self.branches)
            ):
                raise ValueError("decision_ui_review_result_mismatch")
        elif name == "DecisionUiReviewViewV1" and self.review is not None:
            if self.review.completed_at > self.as_of:
                raise ValueError("decision_ui_review_future_result")
            for branch in self.review.branches:
                if branch.candidate is not None and (
                    (branch.candidate.scope_id, branch.candidate.horizon)
                    != (self.scope_id, self.horizon)
                    or (branch.mechanism.scope_id, branch.mechanism.horizon)
                    != (self.scope_id, self.horizon)
                    or branch.mechanism.run_id != self.review.run_id
                    or branch.candidate.as_of > self.review.completed_at
                    or branch.mechanism.cutoff > self.review.completed_at
                ):
                    raise ValueError("decision_ui_review_scope_or_time_mismatch")
        return self
