"""Read-only text projection after original native replay; never plan or execution authority."""

from __future__ import annotations

from typing import Any, cast

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionModelAssumptionV1,
)
from packages.kernel.decision_hub_kernel.application.provider_input_receipts import (
    strict_json,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_json,
    observation_sha,
)
from packages.kernel.decision_hub_kernel.ports.research_observation import (
    ObservedExecutionRef,
)

ROLES = (
    ("condition_refs", "entry_condition"),
    ("exit_ref", "exit_policy"),
    ("basis_refs", "basis"),
)


def _primary(synthesis: dict[str, Any]) -> dict[str, Any]:
    return (
        synthesis["primary_v2"]
        if synthesis["schema_version"] == "research-decision-synthesis.v3"
        else synthesis
    )


def _reference(body: dict[str, Any]) -> dict[str, Any]:
    # Same canonical content identity as the original pure preparation producer.
    digest = observation_sha(body)
    return dict(ref="decision_model_assumption_" + digest, hash=digest)


def _referenced(synthesis: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    for candidate in _primary(synthesis)["candidate_set"]["candidates"]:
        for field, role in ROLES:
            value = candidate[field]
            for reference in [value] if field == "exit_ref" and value else value or []:
                pair = (role, reference)
                if pair not in result:
                    result.append(pair)
    return result


def _catalog(catalog: dict[str, Any]) -> dict[str, dict[str, Any] | None]:
    # source_ref names its exact projection; original_ref can name a shared parent.
    originals: dict[str, dict[str, Any] | None] = {
        observation_json(row["source_ref"]): row for row in catalog["originals"]
    }
    aliases: dict[str, dict[str, Any] | None] = {}
    for row in catalog["originals"]:
        reference = row["original_ref"]
        if reference is None:
            continue
        key = observation_json(reference)
        previous = aliases.get(key)
        if key not in aliases:
            aliases[key] = row
        elif previous is None or previous["body_json"] != row["body_json"]:
            aliases[key] = None  # Ambiguous text is unavailable, not a new research gate.
    for key, row in aliases.items():
        originals.setdefault(key, row)
    return originals


def prepare_candidate_definitions(
    synthesis: dict[str, Any],
    assumptions: list[dict[str, Any]],
    catalog: dict[str, Any],
    *,
    synthesis_ref: dict[str, Any],
    prepare_emission_ref: dict[str, Any] | None,
) -> dict[str, Any]:
    """Caller already authenticated/rebuilt prepare and exact reviewed assumptions."""
    if assumptions and prepare_emission_ref is None:
        raise ValueError("perpetual_candidate_definitions_prepare_missing")
    declared = {observation_json(_reference(body)): body for body in assumptions}
    originals = _catalog(catalog)
    names = {strict_json(key)["ref"] for key in declared.keys() | originals.keys()}
    rows: list[dict[str, Any]] = []
    for role, reference in _referenced(synthesis):
        key = observation_json(reference)
        if key in declared:
            body = declared[key]
            if body["role"] != role:
                raise ValueError("perpetual_candidate_definitions_role_mismatch")
            rows.append(
                dict(
                    role=role,
                    definition_ref=reference,
                    kind="declared_assumption",
                    body_json=observation_json(body),
                )
            )
        elif key in originals:
            original = originals[key]
            if original is not None:
                rows.append(
                    dict(
                        role=role,
                        definition_ref=reference,
                        kind="review_original",
                        body_json=original["body_json"],
                    )
                )
        elif reference["ref"] in names:
            raise ValueError("perpetual_candidate_definitions_reference_hash_mismatch")
    return dict(
        synthesis_ref=synthesis_ref, prepare_emission_ref=prepare_emission_ref, definitions=rows
    )


def _checked_rows(
    rows: list[dict[str, Any]],
    synthesis: dict[str, Any],
    catalog: dict[str, Any],
    *,
    prepared: bool,
) -> None:
    expected = {(role, observation_json(ref)) for role, ref in _referenced(synthesis)}
    originals = _catalog(catalog)
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if set(row) != {"role", "definition_ref", "kind", "body_json"}:
            raise ValueError("perpetual_candidate_definitions_row_invalid")
        key = (row["role"], observation_json(row["definition_ref"]))
        if key not in expected or key in seen:
            raise ValueError("perpetual_candidate_definitions_reference_mismatch")
        seen.add(key)
        body = strict_json(row["body_json"])
        if observation_json(body) != row["body_json"]:
            raise ValueError("perpetual_candidate_definitions_body_not_canonical")
        if row["kind"] == "declared_assumption":
            parsed = DecisionModelAssumptionV1.model_validate(body).model_dump(mode="json")
            if (
                not prepared
                or parsed["role"] != row["role"]
                or _reference(parsed) != row["definition_ref"]
            ):
                raise ValueError("perpetual_candidate_definitions_declared_original_mismatch")
        else:
            original = originals.get(key[1])
            if (
                row["kind"] != "review_original"
                or original is None
                or original["body_json"] != row["body_json"]
            ):
                raise ValueError("perpetual_candidate_definitions_review_original_mismatch")


def candidate_definitions_view(
    *,
    owner_id: str,
    run_id: str,
    source: dict[str, Any],
    cached: dict[str, Any],
) -> dict[str, Any]:
    packet = source["candidate"]["perpetual_synthesis"]
    binding, synthesis = packet["binding"], packet["synthesis"]
    context = _primary(synthesis)["candidate_set"]["context"]
    if (
        owner_id != context["owner_id"]
        or run_id != source["run_id"]
        or source["perpetual_binding"] != binding
        or cached.get("synthesis_ref") != binding["synthesis_ref"]
    ):
        raise ValueError("perpetual_candidate_definitions_binding_mismatch")
    if set(cached) != {"synthesis_ref", "prepare_emission_ref", "definitions"}:
        raise ValueError("perpetual_candidate_definitions_cache_invalid")
    prepare = cached["prepare_emission_ref"]
    if prepare is not None:
        parsed = ObservedExecutionRef.model_validate_json(observation_json(prepare))
        provider = ObservedExecutionRef.model_validate_json(
            observation_json(binding["provider_emission_ref"])
        )
        if (
            parsed.schema_version != "research-provider-tool-emission.v1"
            or parsed.role != "model_emit:" + parsed.output_id.removeprefix("model_emit_")
            or not parsed.output_id.startswith("model_emit_")
            or parsed.recorded_at > provider.recorded_at
        ):
            raise ValueError("perpetual_candidate_definitions_prepare_original_mismatch")
    _checked_rows(
        cached["definitions"], synthesis, packet["review_originals"], prepared=prepare is not None
    )
    return dict(
        schema_version="perpetual-candidate-definitions.v1",
        binding=dict(
            owner_id=owner_id,
            run_id=run_id,
            mapped_ref=binding["mapped_ref"],
            provider_emission_ref=binding["provider_emission_ref"],
            synthesis_ref=binding["synthesis_ref"],
            prepare_emission_ref=prepare,
        ),
        definitions=cached["definitions"],
    )


def _reviewed_assumptions(prompt: str) -> list[dict[str, Any]] | None:
    lines = prompt.split("\n")
    marker = "BEGIN_DECLARED_MODEL_ASSUMPTIONS_JSON"
    if marker not in lines:
        return None  # Legacy unphased packets never claimed an authored prepare block.
    if lines.count(marker) != 1:
        raise ValueError("perpetual_candidate_definitions_reviewed_assumptions_ambiguous")
    index = lines.index(marker)
    if index + 3 >= len(lines):
        raise ValueError("perpetual_candidate_definitions_reviewed_assumptions_missing")
    assumptions = strict_json(lines[index + 1])
    if not isinstance(assumptions, list):
        raise ValueError("perpetual_candidate_definitions_reviewed_assumptions_invalid")
    rows = cast(list[Any], assumptions)
    if (
        observation_json(rows) != lines[index + 1]
        or lines[index + 2] != "END_DECLARED_MODEL_ASSUMPTIONS_JSON"
        or lines[index + 3] != "MODEL_ASSUMPTIONS_SHA256=" + observation_sha(rows)
    ):
        raise ValueError("perpetual_candidate_definitions_reviewed_assumptions_mismatch")
    return [DecisionModelAssumptionV1.model_validate(row).model_dump(mode="json") for row in rows]


def _legacy_prepare_ref(
    reader: Any, session: Any, mapped: Any, binding: dict[str, Any]
) -> dict[str, Any] | None:
    """Read only the two selected original rows already checked by the old native replay."""
    read = getattr(reader, "read_provider_emission_in_session", None)
    if read is None:
        return None
    context = mapped.context
    scope = dict(
        run_id=context.run_id,
        generation=mapped.mapping_input_generation,
        root_session_id=context.dsh_session_id,
        execution_session_id=context.dsh_session_id,
    )
    selected = read(session, **scope, call_id=mapped.perpetual_synthesis.selection.call_id)
    if selected is None:
        raise ValueError("perpetual_candidate_definitions_legacy_finalize_missing")
    emission, observed = selected
    if observed.model_dump(mode="json") != binding["provider_emission_ref"]:
        raise ValueError("perpetual_candidate_definitions_legacy_provider_mismatch")
    arguments = strict_json(emission.submit.raw_arguments_json)
    if arguments.get("action") != "finalize":
        return None
    call_id = arguments.get("prepared_call_id")
    if not isinstance(call_id, str) or not call_id:
        raise ValueError("perpetual_candidate_definitions_legacy_prepare_identity_missing")
    selected_prepare = read(session, **scope, call_id=call_id)
    if selected_prepare is None:
        raise ValueError("perpetual_candidate_definitions_legacy_prepare_missing")
    prepared, reference = selected_prepare
    draft = strict_json(prepared.submit.raw_arguments_json)
    if (
        prepared.submit.tool_name != "decision_hub_synthesis_submit"
        or draft.get("action") != "prepare"
        or draft.get("request_id") != context.request_id
        or prepared.candidate_hash is not None
        or emission.candidate_hash is not None
        or prepared.known_at > emission.known_at
    ):
        raise ValueError("perpetual_candidate_definitions_legacy_prepare_changed")
    return reference.model_dump(mode="json")


def legacy_candidate_definitions(
    reader: Any,
    session: Any,
    *,
    source: dict[str, Any],
    cached: dict[str, Any],
) -> dict[str, Any] | None:
    """Only AFTER the original same-session reader completed full native authentication.

    That reader already equated this exact reviewed block to its rebuilt prepare.
    Re-read only the hash-bound mapped row; never rerun or alter historical code.
    """
    packet = source["candidate"]["perpetual_synthesis"]
    binding = packet["binding"]
    catalog_only = prepare_candidate_definitions(
        packet["synthesis"],
        [],
        packet["review_originals"],
        synthesis_ref=binding["synthesis_ref"],
        prepare_emission_ref=None,
    )
    reference = reader.ObservedExecutionRef.model_validate_json(
        observation_json(binding["mapped_ref"])
    )
    mapped = reader.read_mapped_perpetual_observation(session, reference)
    native = mapped.perpetual_synthesis
    if (
        mapped.context.run_id != source["run_id"]
        or native.synthesis.model_dump(mode="json") != packet["synthesis"]
    ):
        raise ValueError("perpetual_candidate_definitions_legacy_source_mismatch")
    prepare = (
        cached["prepare_emission_ref"]
        if cached["status"] == "available"
        else _legacy_prepare_ref(reader, session, mapped, binding)
    )
    if prepare is None:
        return catalog_only
    wrapper = reader.read_frozen_review_envelope_v4(native.review_wrapper_json)
    assumptions = _reviewed_assumptions(wrapper["actual_prompt"])
    if assumptions is None:
        return catalog_only
    return prepare_candidate_definitions(
        packet["synthesis"],
        assumptions,
        packet["review_originals"],
        synthesis_ref=binding["synthesis_ref"],
        prepare_emission_ref=prepare,
    )
