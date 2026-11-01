"""Read one original native V2 packet against its server-frozen financial scope.

This SELECT-only boundary does not publish or authorize orders. Archived native
mapping replay proves the actual independent child; provider originals prove
the submitted model block; admission originals supply financial scope/policy.
No model-authored flag or matching hash alone provides those authorities.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..decision.perpetual_conditions import ConditionDeclarationResult, decode_perpetual_condition
from ..ports.event_scenario_manifest import event_scenario_validation_scope
from ..ports.market_path import aware_utc
from ..ports.research_action_synthesis import (
    normalize_action_synthesis,
    parse_action_review_request,
    primary_perpetual_authorization,
    primary_perpetual_review_request,
    primary_perpetual_synthesis,
    validate_action_review_binding_v2,
)
from ..ports.research_observation import (
    ObservedExecutionRef,
    ResearchPerpetualSynthesisObservation,
)
from .perpetual_review_envelope import read_frozen_review_envelope_v4
from .provider_input_receipts import read_provider_emission_in_session, strict_json
from .research_publication_binding import (
    observation_json,
    observation_sha,
    read_mapped_perpetual_observation,
)


@dataclass(frozen=True)
class TrustedPerpetualResearch:
    """Immutable values from one authenticated read, not reusable permission."""

    run_id: str
    owner_id: str
    opportunity_id: str
    mapped_ref: ObservedExecutionRef
    transport_ref: ObservedExecutionRef
    provider_emission_ref: ObservedExecutionRef
    admission_ref_json: str
    task_binding_ref_json: str
    synthesis_json: str
    review_request_json: str
    authorization_json: str
    materials_json: str
    native_selection_json: str
    producer_generation: int
    known_at: datetime


@dataclass(frozen=True)
class PerpetualReviewOriginals:
    """Exact strings extracted after native replay, never independent permission."""

    synthesis_json: str
    review_request_json: str
    authorization_json: str
    review_originals_json: str


@dataclass(frozen=True)
class PreparedPerpetualConditions:
    """Read-only declarations and their existing reviewed prepare provenance.

    Declared is a typed policy, not active monitoring or trading permission.
    Watch registration must separately authenticate its source plan and inputs.
    """

    run_id: str
    candidate_target_ref_json: str
    synthesis_ref_json: str
    prepare_emission_ref: ObservedExecutionRef | None
    declarations: tuple[ConditionDeclarationResult, ...]
    reason_code: str | None = None
    mapped_ref: ObservedExecutionRef | None = None
    provider_emission_ref: ObservedExecutionRef | None = None


def _prepared_condition_declarations(
    synthesis: dict,
    assumptions: list[dict],
    *,
    run_id: str,
    synthesis_ref: dict,
    prepare_emission_ref: ObservedExecutionRef | None,
    actual_review_prompt: str | None,
) -> PreparedPerpetualConditions:
    """Decode after original authentication; independently pin exact prompt body."""
    from ..ports.perpetual_preparation import derived_ref

    if synthesis_ref.get("hash") != derived_ref("research_decision_synthesis", synthesis)["hash"]:
        raise ValueError("perpetual_condition_synthesis_ref_mismatch")
    synthesis = primary_perpetual_synthesis(synthesis)
    result = PreparedPerpetualConditions(
        run_id,
        observation_json(synthesis["candidate_set"]["candidate_target_ref"]),
        observation_json(synthesis_ref),
        prepare_emission_ref,
        (),
    )
    if prepare_emission_ref is None:
        return replace(result, reason_code="prepared_condition_declarations_unavailable")
    lines = actual_review_prompt.split("\n") if isinstance(actual_review_prompt, str) else []
    marker = "BEGIN_DECLARED_MODEL_ASSUMPTIONS_JSON"
    if lines.count(marker) != 1:
        raise ValueError("perpetual_condition_reviewed_assumptions_mismatch")
    position = lines.index(marker)
    if (
        position + 3 >= len(lines)
        or lines[position + 1] != observation_json(assumptions)
        or lines[position + 2] != "END_DECLARED_MODEL_ASSUMPTIONS_JSON"
        or lines[position + 3] != "MODEL_ASSUMPTIONS_SHA256=" + observation_sha(assumptions)
    ):
        raise ValueError("perpetual_condition_reviewed_assumptions_mismatch")
    return replace(
        result,
        declarations=tuple(
            decode_perpetual_condition(assumption, synthesis=synthesis)
            for assumption in assumptions
            if assumption["role"] == "entry_condition"
        ),
    )


@dataclass(frozen=True)
class _PerpetualPreparedSources:
    originals: PerpetualReviewOriginals
    emission_ref: ObservedExecutionRef
    probability_definitions: dict
    conditions: PreparedPerpetualConditions
    candidate_definitions: dict[str, Any]


_PROBABILITY_DEFINITION_ROLES = (
    ("event_definition", "event_definition_ref"),
    ("occurrence_target", "occurrence_target_ref"),
    ("outcome_target", "outcome_target_ref"),
)


def _prepared_probability_definitions(
    synthesis: dict,
    assumptions: list[dict],
    *,
    synthesis_ref: dict,
    prepare_emission_ref: ObservedExecutionRef | None,
) -> dict:
    """Resolve only exact local assumptions; external/legacy targets stay unavailable."""
    if prepare_emission_ref is None:
        return dict(status="unavailable", synthesis_ref=synthesis_ref)
    from ..ports.perpetual_preparation import derived_ref

    by_ref: dict[str, tuple[dict, dict]] = {}
    for definition in assumptions:
        reference = derived_ref("decision_model_assumption", definition)
        key = observation_json(reference)
        existing = by_ref.get(key)
        if existing is not None and existing[0] != definition:
            raise ValueError("perpetual_probability_definition_hash_collision")
        by_ref[key] = (definition, reference)
    event_probability = primary_perpetual_synthesis(synthesis)["event_probability"]
    if (
        event_probability["occurrence_distribution"]["target_ref"]
        != event_probability["occurrence_target_ref"]
        or event_probability["outcome_given_occurrence"]["target_ref"]
        != event_probability["outcome_target_ref"]
    ):
        raise ValueError("perpetual_probability_definition_target_mismatch")
    definitions = []
    for role, field in _PROBABILITY_DEFINITION_ROLES:
        expected = event_probability[field]
        matched = by_ref.get(observation_json(expected))
        if matched is None:
            return dict(status="unavailable", synthesis_ref=synthesis_ref)
        definition, reference = matched
        if definition["role"] != role or reference != expected:
            raise ValueError("perpetual_probability_definition_role_mismatch")
        definitions.append(dict(role=role, definition_ref=reference, definition=definition))
    return dict(
        status="available",
        synthesis_ref=synthesis_ref,
        prepare_emission_ref=prepare_emission_ref.model_dump(mode="json"),
        definitions=definitions,
    )


def _review_originals(native: ResearchPerpetualSynthesisObservation) -> PerpetualReviewOriginals:
    # The caller authenticates this wrapper through read_mapped_... replay.
    # Hash RAW included bodies before typed comparisons: lawful .000Z and Z
    # lexical forms represent one instant but have different original hashes.
    if native.authorization is None:
        raise ValueError("perpetual_independent_review_missing")
    if not isinstance(native.review_wrapper_json, str):
        raise ValueError("perpetual_native_review_original_missing")
    wrapper = read_frozen_review_envelope_v4(native.review_wrapper_json)
    if (
        not isinstance(wrapper, dict)
        or wrapper.get("schema_version") != "decision-hub-frozen-review-result.v4"
    ):
        raise ValueError("perpetual_native_review_original_invalid")
    for field in ("candidate", "review_request"):
        text = wrapper.get(field + "_json")
        if not isinstance(text, str) or hashlib.sha256(text.encode()).hexdigest() != wrapper.get(
            field + "_sha256"
        ):
            raise ValueError("perpetual_native_review_original_digest_mismatch")
    raw_candidate = strict_json(wrapper["candidate_json"])
    raw_packet = strict_json(wrapper["review_request_json"])
    output = wrapper.get("original_output")
    blocks = output.get("output") if isinstance(output, dict) else None
    if not isinstance(blocks, list):
        raise ValueError("perpetual_native_review_original_authorization_missing")
    authorizations = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if not isinstance(text, str):
            continue
        try:
            body = strict_json(text)
        except ValueError:
            # Match native text-block selection; the caller authenticates the
            # complete original output before this exact-one extraction.
            continue
        for item in body if isinstance(body, list) else [body]:
            if isinstance(item, dict) and item.get("schema_version") in {
                "action-review-authorization.v2",
                "action-review-authorization.v3",
            }:
                # The complete raw array remains authenticated in original_output.
                authorizations.append(
                    (observation_json(item) if isinstance(body, list) else text, item)
                )
    if len(authorizations) != 1:
        raise ValueError("perpetual_native_review_original_authorization_missing")
    authorization_text, raw_authorization = authorizations[0]
    authorization = validate_action_review_binding_v2(raw_packet, raw_authorization)
    from .perpetual_review_materials import (
        review_originals_from_prompt,
        validate_analysis_originals,
    )

    catalog = review_originals_from_prompt(wrapper["actual_prompt"])
    validate_analysis_originals(native.synthesis, catalog)
    if (
        catalog["run_id"] != wrapper["binding"]["runId"]
        or catalog["request_hash"] != wrapper["binding"]["requestHash"]
        or aware_utc(datetime.fromisoformat(catalog["as_of"].replace("Z", "+00:00")))
        > authorization.reviewed_at
    ):
        raise ValueError("perpetual_review_original_scope_mismatch")
    if (
        not isinstance(raw_packet, dict)
        or raw_packet.get("synthesis") != raw_candidate
        or observation_sha(raw_candidate) != native.raw_candidate_hash
        or normalize_action_synthesis(raw_candidate) != native.synthesis
        or parse_action_review_request(raw_packet) != native.review_request
        or authorization != native.authorization
    ):
        raise ValueError("perpetual_native_review_original_packet_mismatch")
    return PerpetualReviewOriginals(
        wrapper["candidate_json"],
        wrapper["review_request_json"],
        authorization_text,
        observation_json(catalog),
    )


def validate_perpetual_provider_packet(
    native: ResearchPerpetualSynthesisObservation,
    arguments: object,
) -> PerpetualReviewOriginals:
    """Pure original join; native replay and provider receipt must precede it."""
    originals = _review_originals(native)
    if (
        not isinstance(arguments, dict)
        or set(arguments) != {"candidate", "review_request"}
        or observation_sha(arguments["candidate"]) != native.raw_candidate_hash
        or arguments["candidate"] != strict_json(originals.synthesis_json)
        or arguments["review_request"] != strict_json(originals.review_request_json)
    ):
        raise ValueError("perpetual_provider_native_raw_packet_mismatch")
    return originals


def validate_perpetual_probability_method(synthesis) -> None:
    """The current server method has no fitted/calibrated model activation."""
    synthesis = primary_perpetual_synthesis(synthesis)
    for estimate in (
        synthesis.event_probability.occurrence_distribution,
        synthesis.event_probability.outcome_given_occurrence,
        synthesis.market_distribution.direction_distribution,
    ):
        if not (
            estimate.kind == "subjective"
            and estimate.qualification == "research_trial"
            or estimate.kind == "unavailable"
            and estimate.qualification in {"research_trial", "blocked"}
        ):
            raise ValueError("perpetual_probability_model_original_unavailable")


def validate_perpetual_packet_scope(
    admission: dict,
    native: ResearchPerpetualSynthesisObservation,
    *,
    request_id: str,
    known_at: datetime,
) -> None:
    """Pure relationship checks; caller must authenticate admission/native first."""
    outer_synthesis, outer_packet, outer_authorization = (
        native.synthesis,
        native.review_request,
        native.authorization,
    )
    if outer_authorization is None:
        raise ValueError("perpetual_independent_review_missing")
    _review_originals(native)
    synthesis, packet, authorization = (
        primary_perpetual_synthesis(outer_synthesis),
        primary_perpetual_review_request(outer_packet),
        primary_perpetual_authorization(outer_authorization),
    )
    if authorization.verdict != "approved":
        raise ValueError("perpetual_independent_review_not_approved")
    value = synthesis.model_dump(mode="json")
    if (
        packet.synthesis != synthesis
        or value["request_id"] != request_id
        or value["opportunity_id"] != admission["opportunity_id"]
        or value["candidate_set"]["context"] != admission["context"]
        or value["candidate_target"] != admission["candidate_target"]
        or value["anchor_rule"] != admission["anchor_rule"]
        or value["candidate_set"]["candidate_target_ref"] != admission["candidate_target_ref"]
        or value["candidate_set"]["anchor_rule_ref"] != admission["anchor_rule_ref"]
        or value["method_ref"] != admission["method_ref"]
        or value["method_code_hash"] != admission["method_code_hash"]
    ):
        raise ValueError("perpetual_native_server_scope_mismatch")
    policy = admission["allowed_domain_policy"]
    domain = packet.allowed_domain.model_dump(mode="json")
    if any(
        domain[key] != policy[key]
        for key in (
            "method_ref",
            "method_code_hash",
            "variables",
            "invalidation_refs",
            "valid_until",
        )
    ) or (
        policy["mutation_scope"] != "none_original_frozen_inputs_only"
        or policy["deterministic_recomparison_enabled"] is not False
    ):
        raise ValueError("perpetual_native_server_domain_mismatch")
    # The empty variable list permits only the original frozen inputs, never
    # extrapolation to a different price, account or setting under this review.
    if policy["variables"]:
        raise ValueError("perpetual_native_domain_evaluator_unavailable")
    # This admitted method authenticates subjective research, not a fitted or
    # calibrated probability model. A model-written kind/ref cannot promote it.
    # Values remain unrestricted beyond the canonical probability partition.
    validate_perpetual_probability_method(synthesis)
    now = aware_utc(known_at)
    admitted = aware_utc(datetime.fromisoformat(admission["admitted_at"].replace("Z", "+00:00")))
    expires = aware_utc(datetime.fromisoformat(admission["valid_until"].replace("Z", "+00:00")))
    if not (
        synthesis.known_at_max <= synthesis.as_of
        and admitted
        <= synthesis.as_of
        <= packet.requested_at
        <= authorization.reviewed_at
        <= now
        < expires
        and now < synthesis.candidate_set.valid_until
        and now < authorization.valid_until
    ):
        raise ValueError("perpetual_native_server_clock_mismatch")


@event_scenario_validation_scope()
def _read_perpetual_provider_originals_and_definitions_in_session(
    session, mapped, saved, *, known_at, purpose="active_research"
):
    """Authenticate actual Provider phases without granting financial approval."""
    native, context = mapped.perpetual_synthesis, mapped.context
    admission = saved["admission"]
    original = read_provider_emission_in_session(
        session,
        run_id=context.run_id,
        generation=mapped.mapping_input_generation,
        root_session_id=context.dsh_session_id,
        execution_session_id=context.dsh_session_id,
        call_id=native.selection.call_id,
    )
    if original is None:
        raise ValueError("perpetual_provider_synthesis_original_missing")
    emission, emission_ref = original
    candidate = native.synthesis.model_dump(mode="json")
    arguments = strict_json(emission.submit.raw_arguments_json)
    phased = isinstance(arguments, dict) and arguments.get("action") == "finalize"
    originals = (
        _review_originals(native)
        if phased
        else validate_perpetual_provider_packet(native, arguments)
    )
    reviewed_request = strict_json(originals.review_request_json)
    probability_definitions = _prepared_probability_definitions(
        strict_json(originals.synthesis_json),
        [],
        synthesis_ref=reviewed_request["synthesis_ref"],
        prepare_emission_ref=None,
    )
    conditions = _prepared_condition_declarations(
        strict_json(originals.synthesis_json),
        [],
        run_id=context.run_id,
        synthesis_ref=reviewed_request["synthesis_ref"],
        prepare_emission_ref=None,
        actual_review_prompt=None,
    )
    from .perpetual_research_binding import (
        read_authenticated_perpetual_execution_in_session,
    )
    from .perpetual_review_materials import read_review_originals_in_session

    request, execution_scope, prepared = read_authenticated_perpetual_execution_in_session(
        session,
        context.run_id,
        mapped.mapping_input_generation,
        purpose=purpose,
    )
    if (
        prepared.execution.dsh_session_id != context.dsh_session_id
        or execution_scope["binding_ref"] != saved["binding_ref"]
    ):
        raise ValueError("perpetual_provider_execution_identity_mismatch")
    saved = execution_scope
    catalog = strict_json(originals.review_originals_json)
    read_review_originals_in_session(
        session,
        request,
        saved["materials"],
        as_of=datetime.fromisoformat(catalog["as_of"].replace("Z", "+00:00")),
        selected=catalog,
    )
    from .perpetual_candidate_definitions import prepare_candidate_definitions

    candidate_definitions = prepare_candidate_definitions(
        strict_json(originals.synthesis_json), [], catalog,
        synthesis_ref=reviewed_request["synthesis_ref"], prepare_emission_ref=None,
    )

    if phased:
        from packages.runtime_adapters.dsh_runtime.perpetual_attestation import (
            preparation_for_capture,
        )
        from packages.runtime_adapters.dsh_runtime.web_runtime import _sdk_run

        from ..ports.perpetual_preparation import prepare_perpetual_synthesis
        from .research_publication_binding import read_transport_observation

        transport = read_transport_observation(session, mapped.mapping_input_ref)
        proof = preparation_for_capture(
            _sdk_run(transport.reply).notifications,
            expected_session_id=context.dsh_session_id,
            call_id=native.selection.call_id,
            catalog=catalog,
            request=request,
        )
        if proof is None or proof["finalize_arguments"] != arguments:
            raise ValueError("perpetual_provider_finalize_original_changed")
        prepared = read_provider_emission_in_session(
            session,
            run_id=context.run_id,
            generation=mapped.mapping_input_generation,
            root_session_id=context.dsh_session_id,
            execution_session_id=context.dsh_session_id,
            call_id=proof["preparation"]["prepared_call_id"],
        )
        if prepared is None:
            raise ValueError("perpetual_provider_prepare_original_missing")
        prepare_emission, prepare_ref = prepared
        actual_draft_args = strict_json(prepare_emission.submit.raw_arguments_json)
        if (
            actual_draft_args != proof["prepare_arguments"]
            or prepare_emission.candidate_hash is not None
            or emission.candidate_hash is not None
            or prepare_emission.submit.tool_name != "decision_hub_synthesis_submit"
            or prepare_emission.known_at > emission.known_at
        ):
            raise ValueError("perpetual_provider_prepare_original_changed")
        # Server scope comes from the real immutable admission, not the Tool's
        # normalized output. Catalog source identities were authenticated above.
        binding = {
            key: admission[key]
            for key in (
                "opportunity_id",
                "context",
                "candidate_target_ref",
                "anchor_rule_ref",
                "method_ref",
                "method_code_hash",
            )
        }
        # Every negative ancestor is authenticated independently. A valid final
        # packet cannot conceal a changed raw draft or fabricated earlier review.
        for attempt in proof["attempts"][:-1]:
            ancestor = read_provider_emission_in_session(
                session,
                run_id=context.run_id,
                generation=mapped.mapping_input_generation,
                root_session_id=context.dsh_session_id,
                execution_session_id=context.dsh_session_id,
                call_id=attempt["call_id"],
            )
            if ancestor is None:
                raise ValueError("perpetual_provider_ancestor_original_missing")
            earlier, _earlier_ref = ancestor
            if (
                earlier.candidate_hash is not None
                or earlier.submit.tool_name != "decision_hub_synthesis_submit"
                or strict_json(earlier.submit.raw_arguments_json) != attempt["prepare_arguments"]
                or earlier.known_at > prepare_emission.known_at
            ):
                raise ValueError("perpetual_provider_ancestor_original_changed")
            frozen_catalog = attempt["catalog"]
            read_review_originals_in_session(
                session,
                request,
                saved["materials"],
                as_of=datetime.fromisoformat(frozen_catalog["as_of"].replace("Z", "+00:00")),
                selected=frozen_catalog,
            )
            rebuilt = prepare_perpetual_synthesis(
                attempt["prepare_arguments"]["draft"],
                request_id=context.request_id,
                binding=binding,
                review_originals=frozen_catalog,
                requested_at=attempt["preparation"]["requested_at"],
            )
            if (
                rebuilt["candidate"] != attempt["candidate"]
                or rebuilt["review_request"] != attempt["review_request"]
                or any(
                    rebuilt[key] != attempt["preparation"][key]
                    for key in (
                        "assumptions",
                        "source_draft_hash",
                        "normalized_synthesis_hash",
                        "normalized_review_request_hash",
                    )
                )
            ):
                raise ValueError("perpetual_provider_ancestor_replay_mismatch")
        normalized = prepare_perpetual_synthesis(
            actual_draft_args["draft"],
            request_id=context.request_id,
            binding=binding,
            review_originals=catalog,
            requested_at=proof["preparation"]["requested_at"],
        )
        if (
            normalized["candidate"] != strict_json(originals.synthesis_json)
            or normalized["review_request"] != strict_json(originals.review_request_json)
            or any(
                normalized[key] != proof["preparation"][key]
                for key in (
                    "assumptions",
                    "source_draft_hash",
                    "normalized_synthesis_hash",
                    "normalized_review_request_hash",
                )
            )
        ):
            raise ValueError("perpetual_provider_draft_replay_mismatch")
        probability_definitions = _prepared_probability_definitions(
            normalized["candidate"],
            normalized["assumptions"],
            synthesis_ref=normalized["review_request"]["synthesis_ref"],
            prepare_emission_ref=prepare_ref,
        )
        wrapper = read_frozen_review_envelope_v4(native.review_wrapper_json)
        conditions = _prepared_condition_declarations(
            normalized["candidate"],
            normalized["assumptions"],
            run_id=context.run_id,
            synthesis_ref=normalized["review_request"]["synthesis_ref"],
            prepare_emission_ref=prepare_ref,
            actual_review_prompt=wrapper["actual_prompt"],
        )
        candidate_definitions = prepare_candidate_definitions(
            normalized["candidate"], normalized["assumptions"], catalog,
            synthesis_ref=normalized["review_request"]["synthesis_ref"],
            prepare_emission_ref=prepare_ref.model_dump(mode="json"),
        )

    if (
        emission.submit.tool_name != "decision_hub_synthesis_submit"
        or not phased
        and emission.candidate_hash != observation_sha(candidate)
        or emission.known_at > mapped.recorded_at
        or mapped.recorded_at > aware_utc(known_at)
    ):
        raise ValueError("perpetual_provider_native_candidate_mismatch")
    return _PerpetualPreparedSources(
        originals, emission_ref, probability_definitions, conditions, candidate_definitions,
    )


def read_perpetual_provider_originals_in_session(
    session, mapped, saved, *, known_at, purpose="active_research"
):
    """Preserve the established provider-original reader API."""
    sources = _read_perpetual_provider_originals_and_definitions_in_session(
        session, mapped, saved, known_at=known_at, purpose=purpose
    )
    return sources.originals, sources.emission_ref


def _report_definition_caches(sources: Any) -> dict[str, Any]:
    """Older registered namespaces return the original four-field source object."""
    return dict(probability_definitions=sources.probability_definitions,
                candidate_definitions=getattr(sources, "candidate_definitions", None))


def read_perpetual_report_definitions_in_session(
    session: Session,
    *,
    mapped_ref: ObservedExecutionRef | dict[str, Any],
    provider_emission_ref: ObservedExecutionRef | dict[str, Any],
    known_at: datetime,
) -> dict[str, Any]:
    """Replay the exact mapped execution and return its prepared definitions."""
    mapped_reference = (
        mapped_ref
        if isinstance(mapped_ref, ObservedExecutionRef)
        else ObservedExecutionRef.model_validate_json(observation_json(mapped_ref))
    )
    expected_provider = (
        provider_emission_ref
        if isinstance(provider_emission_ref, ObservedExecutionRef)
        else ObservedExecutionRef.model_validate_json(observation_json(provider_emission_ref))
    )
    mapped = read_mapped_perpetual_observation(session, mapped_reference)
    context = mapped.context
    from .perpetual_research_binding import (
        read_authenticated_perpetual_execution_in_session,
    )

    _request, saved_scope, prepared = read_authenticated_perpetual_execution_in_session(
        session,
        context.run_id,
        mapped.mapping_input_generation,
        purpose="frozen_read",
    )
    if prepared.execution.dsh_session_id != context.dsh_session_id:
        raise ValueError("perpetual_probability_definitions_execution_mismatch")
    sources = _read_perpetual_provider_originals_and_definitions_in_session(
        session, mapped, saved_scope, known_at=known_at, purpose="frozen_read"
    )
    if sources.emission_ref != expected_provider:
        raise ValueError("perpetual_probability_definitions_provider_mismatch")
    return _report_definition_caches(sources)


def read_perpetual_probability_definitions_in_session(
    session: Session,
    *,
    mapped_ref: ObservedExecutionRef | dict,
    provider_emission_ref: ObservedExecutionRef | dict,
    known_at: datetime,
) -> dict:
    """Preserve the existing probability-only reader and its original authentication."""
    definitions = read_perpetual_report_definitions_in_session(
        session, mapped_ref=mapped_ref, provider_emission_ref=provider_emission_ref,
        known_at=known_at,
    )
    return definitions["probability_definitions"]


def read_perpetual_conditions_in_session(
    session: Session,
    *,
    source_run_id: str,
    candidate_target_ref: dict,
    mapped_ref: ObservedExecutionRef | dict,
    provider_emission_ref: ObservedExecutionRef | dict,
    known_at: datetime,
) -> PreparedPerpetualConditions:
    """Read exact approved sameRun policies; never trust a report's copied text."""
    reference = (
        mapped_ref
        if isinstance(mapped_ref, ObservedExecutionRef)
        else ObservedExecutionRef.model_validate(mapped_ref)
    )
    expected_provider = (
        provider_emission_ref
        if isinstance(provider_emission_ref, ObservedExecutionRef)
        else ObservedExecutionRef.model_validate(provider_emission_ref)
    )
    mapped = read_mapped_perpetual_observation(session, reference)
    context, native = mapped.context, mapped.perpetual_synthesis
    if context.run_id != source_run_id:
        raise ValueError("perpetual_condition_source_run_mismatch")
    if (
        primary_perpetual_synthesis(native.synthesis).candidate_set.candidate_target_ref.model_dump(
            mode="json"
        )
        != candidate_target_ref
    ):
        raise ValueError("perpetual_condition_source_target_mismatch")
    from .perpetual_research_binding import read_authenticated_perpetual_execution_in_session

    _request, scope, prepared = read_authenticated_perpetual_execution_in_session(
        session,
        context.run_id,
        mapped.mapping_input_generation,
        purpose="frozen_read",
    )
    if prepared.execution.dsh_session_id != context.dsh_session_id:
        raise ValueError("perpetual_condition_execution_mismatch")
    if native.authorization is None:
        raise ValueError("perpetual_independent_review_missing")
    # Authenticate original publication eligibility at its actual reviewed time;
    # reading an archived policy later does not renew its validity or protection.
    validate_perpetual_packet_scope(
        scope["admission"],
        native,
        request_id=context.request_id,
        known_at=native.authorization.reviewed_at,
    )
    sources = _read_perpetual_provider_originals_and_definitions_in_session(
        session,
        mapped,
        scope,
        known_at=known_at,
        purpose="frozen_read",
    )
    if sources.emission_ref != expected_provider:
        raise ValueError("perpetual_condition_provider_mismatch")
    return replace(
        sources.conditions, mapped_ref=reference, provider_emission_ref=sources.emission_ref
    )


@event_scenario_validation_scope()
def read_trusted_perpetual_research_in_session(
    session: Session,
    *,
    mapped_ref: ObservedExecutionRef,
    known_at: datetime,
    purpose="active_research",
) -> TrustedPerpetualResearch:
    """Authenticate saved native/Task/provider/admission in the caller snapshot."""
    from .perpetual_research_binding import read_perpetual_research_binding_in_session

    mapped = read_mapped_perpetual_observation(session, mapped_ref)
    context, native = mapped.context, mapped.perpetual_synthesis
    saved = read_perpetual_research_binding_in_session(session, context.run_id, purpose=purpose)
    admission, task = saved["admission"], saved["task_binding"]
    if (
        task["original_task_hash"] != context.original_task_request_hash
        or task["request_id"] != context.request_id
        or task["run_id"] != context.run_id
        or task["event_id"] != context.event_id
        or admission["run_id"] != context.run_id
        or admission["event_id"] != context.event_id
        or task["admission_ref"] != saved["admission_ref"]
    ):
        raise ValueError("perpetual_native_original_task_mismatch")
    validate_perpetual_packet_scope(
        admission, native, request_id=context.request_id, known_at=known_at
    )
    originals, emission_ref = read_perpetual_provider_originals_in_session(
        session, mapped, saved, known_at=known_at, purpose=purpose
    )
    catalog = strict_json(originals.review_originals_json)
    return TrustedPerpetualResearch(
        run_id=context.run_id,
        owner_id=admission["owner_id"],
        opportunity_id=admission["opportunity_id"],
        mapped_ref=mapped_ref,
        transport_ref=mapped.mapping_input_ref,
        provider_emission_ref=emission_ref,
        admission_ref_json=observation_json(saved["admission_ref"]),
        task_binding_ref_json=observation_json(saved["binding_ref"]),
        synthesis_json=originals.synthesis_json,
        review_request_json=originals.review_request_json,
        authorization_json=originals.authorization_json,
        materials_json=observation_json(dict(saved["materials"], review_originals=catalog)),
        native_selection_json=observation_json(native.selection.model_dump(mode="json")),
        producer_generation=mapped.mapping_input_generation,
        known_at=aware_utc(known_at),
    )
