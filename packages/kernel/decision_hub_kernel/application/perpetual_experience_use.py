"""Original-backed model-input/causal-reference observations, not skill authority."""

import hashlib
from datetime import datetime
from typing import Any

from packages.contracts_py.decision_hub_contracts.generated.provider_input import (
    ResearchProviderInputRef,
)

from ..persistence.db import DshSessionPromptRecord, RunRecord, RunRoleOutputRecord, as_utc
from ..persistence.original_preparation import begin_immediate, prepare_originals
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.perpetual_materials_view import expand_materials_view
from ..ports.perpetual_preparation import derived_ref, prepare_perpetual_synthesis
from ..ports.research_action_synthesis import (
    normalize_action_synthesis,
    primary_perpetual_synthesis,
)
from ..ports.research_observation import ObservedExecutionRef
from .perpetual_forecast_experience import read_forecast_experience_in_session
from .perpetual_research_binding import (
    TASK_BINDING_ROLE,
    _read,
    canonical,
    read_perpetual_admission_identity_in_session,
)
from .perpetual_review_materials import validate_analysis_originals, validate_catalog
from .provider_input_receipts import (
    read_provider_emission_in_session,
    read_provider_input_in_session,
    strict_input_json,
    strict_json,
)
from .research_execution_binding import read_prepared_execution_in_session
from .research_task import read_task_request

SCHEMA = "perpetual-experience-provider-use.v1"
ROLE = "perpetual_experience_provider_use_v1"


def _prompt_pointer(body, prompt):
    matches = []
    for index, message in enumerate(body["messages"]):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if content == prompt:
            matches.append(f"/messages/{index}/content")
        elif isinstance(content, list):
            for block_index, block in enumerate(content):
                if (
                    isinstance(block, dict)
                    and block.get("type") == "text"
                    and block.get("text") == prompt
                ):
                    matches.append(f"/messages/{index}/content/{block_index}/text")
    return matches[0] if len(matches) == 1 else None


def _task_sections_present(prompt, request):
    from packages.runtime_adapters.dsh_runtime.profile import _prompt_request_sections

    control, untrusted = _prompt_request_sections(request)
    lines = prompt.splitlines()
    label = "Canonical control request (untrusted text is replaced by typed references):"
    if lines.count(label) != 1 or lines.count("BEGIN_UNTRUSTED_RESEARCH_DATA") != 1:
        return False
    index, data_index = lines.index(label), lines.index("BEGIN_UNTRUSTED_RESEARCH_DATA")
    return (
        index + 1 < len(lines)
        and lines[index + 1] == control
        and data_index + 2 < len(lines)
        and lines[data_index + 1] == untrusted
        and lines[data_index + 2] == "END_UNTRUSTED_RESEARCH_DATA"
    )


def _frozen_materials(session, request, prompt, original_task_hash):
    # Historical input use is authenticated against its original server prompt,
    # not today's account/settings head. It confers no publication permission.
    lines = prompt.splitlines()
    marker = "BEGIN_FROZEN_PERPETUAL_MATERIALS_JSON"
    if lines.count(marker) != 1:
        raise ValueError("perpetual_experience_materials_original_missing")
    index = lines.index(marker)
    if index + 2 >= len(lines) or lines[index + 2] != "END_FROZEN_PERPETUAL_MATERIALS_JSON":
        raise ValueError("perpetual_experience_materials_original_invalid")
    materials = strict_json(lines[index + 1])
    admission, admission_ref = read_perpetual_admission_identity_in_session(session, request.run_id)
    binding, binding_ref = _read(session, request.run_id, TASK_BINDING_ROLE)
    if (
        materials["admission_ref"] != admission_ref
        or materials["task_binding_ref"] != binding_ref
        or materials["context"] != admission["context"]
        or binding["admission_ref"] != admission_ref
        or binding["original_task_hash"] != original_task_hash
    ):
        raise ValueError("perpetual_experience_materials_task_mismatch")
    materials = expand_materials_view(materials, run_id=request.run_id)
    validate_catalog(materials["review_originals"])
    return materials


def _read_selection(session, request):
    # A newest receipt is authoritative, including when invalid; no downgrade.
    row = session.get(
        RunRoleOutputRecord,
        "perp_experience_selection_v3_" + canonical_hash(request.run_id),
        populate_existing=True,
    )
    version = "v3"
    if row is None:
        version = "v2"
        row = session.get(
            RunRoleOutputRecord,
            "perp_experience_selection_v2_" + canonical_hash(request.run_id),
            populate_existing=True,
        )
    if row is None:
        version = "v1"
        row = session.get(
            RunRoleOutputRecord,
            "perp_experience_selection_" + canonical_hash(request.run_id),
            populate_existing=True,
        )
    if row is None:
        return None
    body = strict_json(row.payload_json)
    effective = {item.evidence_id: item.content_hash for item in request.input_evidence}
    if (
        row.run_id != request.run_id
        or row.role != "perpetual_experience_task_selection_" + version
        or row.schema_version != "perpetual-experience-task-selection." + version
        or body["schema_version"] != row.schema_version
        or row.payload_json != canonical(body)
        or row.payload_hash != canonical_hash(body)
        or body["task_hash"] != canonical_hash(request.model_dump(mode="json"))
        or body["request_id"] != request.request_id
        or body["run_id"] != request.run_id
        or any(effective.get(ref["ref"]) != ref["hash"] for ref in body["selected"])
    ):
        raise ValueError("perpetual_experience_task_selection_invalid")
    if version in ("v2", "v3"):
        admission = read_perpetual_admission_identity_in_session(session, request.run_id)[0]
        candidates = body["candidates"]
        if (
            body.get("strategy") != "perpetual-history-inputs." + version
            or body.get("scope") != admission["context"]
            or body.get("cutoff_at") != admission["admitted_at"]
            or len({item["ref"] for item in candidates}) != len(candidates)
            or any(
                item.get("kind")
                not in (
                    {"paper_episode_net_cashflow", "forecast_terminal_direction",
                     "forecast_terminal_direction_v2"}
                    if version == "v3"
                    else {"paper_episode_net_cashflow", "forecast_terminal_direction"}
                )
                for item in candidates
            )
            or any(item not in candidates for item in body["selected"])
        ):
            raise ValueError("perpetual_experience_task_selection_v2_invalid")
    return body


def derive_experience_use_in_session(session, reference, *, cutoff_at):
    reference = ObservedExecutionRef.model_validate(reference)
    row = session.get(RunRoleOutputRecord, reference.output_id, populate_existing=True)
    if row is None or row.payload_hash != reference.payload_hash:
        raise ValueError("perpetual_experience_emission_missing")
    preliminary = strict_json(row.payload_json)["submit"]
    original = read_provider_emission_in_session(
        session,
        **{
            key: preliminary[key]
            for key in (
                "run_id",
                "generation",
                "root_session_id",
                "execution_session_id",
                "call_id",
            )
        },
    )
    if original is None or original[1] != reference:
        raise ValueError("perpetual_experience_emission_reference_invalid")
    emission, emission_ref = original
    submit = emission.submit
    if (
        emission.known_at > aware_utc(cutoff_at)
        or submit.tool_name != "decision_hub_synthesis_submit"
    ):
        raise ValueError("perpetual_experience_emission_scope_invalid")
    task = read_task_request(session, session.get(RunRecord, submit.run_id))
    if task is None:
        raise ValueError("perpetual_experience_task_missing")
    original_task = task[0]
    selection = _read_selection(session, original_task)
    if selection is None or not selection["selected"]:
        return None
    input_original, input_ref = read_provider_input_in_session(
        session, ResearchProviderInputRef.model_validate(submit.input_ref.model_dump(mode="json"))
    )
    execution = read_prepared_execution_in_session(session, submit.run_id, submit.generation)
    request = execution.execution.request
    if (
        submit.execution_session_id != submit.root_session_id
        or input_original.submit.run_id != request.run_id
        or input_original.known_at > emission.known_at
        or input_original.native_context.original_task_request_hash != selection["task_hash"]
        or input_original.native_context.execution_request_hash
        != canonical_hash(request.model_dump(mode="json"))
        or execution.execution.task_request_hash != selection["task_hash"]
    ):
        raise ValueError("perpetual_experience_provider_input_identity_invalid")
    prompt = session.get(
        DshSessionPromptRecord, (request.run_id, submit.generation), populate_existing=True
    )
    if (
        prompt is None
        or hashlib.sha256(prompt.prompt.encode()).hexdigest() != input_original.prompt_hash
    ):
        raise ValueError("perpetual_experience_prompt_original_invalid")
    pointer = _prompt_pointer(strict_input_json(input_original.submit.input_json), prompt.prompt)
    if not _task_sections_present(prompt.prompt, request):
        pointer = None
    arguments = strict_json(submit.raw_arguments_json)
    if arguments.get("action") == "finalize":
        # The authoring prepare emission is processed independently. A finalize
        # acknowledgment does not prove an originating model read experience.
        return None
    materials = _frozen_materials(session, request, prompt.prompt, selection["task_hash"])
    catalog = materials["review_originals"]
    full_wrapper = None
    full_event_plan_draft_ref = None
    event_plan_ref = None
    causal_pointer_prefix = ""
    if arguments.get("action") == "prepare":
        declared_draft = arguments["draft"]
        primary_draft = declared_draft
        if declared_draft.get("schema_version") == "perpetual-synthesis-draft.v2":
            # This is the actual Provider emission callback. The later event
            # source-call catalog has not been built yet, so consume only the
            # exact authored primary recipe under the already frozen catalog.
            # Event financial preparation/review remains a separate boundary.
            from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
                PerpetualSynthesisDraftV2,
            )

            PerpetualSynthesisDraftV2.model_validate(declared_draft)
            primary_draft = declared_draft["primary_draft"]
            full_wrapper = declared_draft
            full_event_plan_draft_ref = derived_ref(
                "declared_event_scenario_action_plan_draft", declared_draft["event_plan_draft"]
            )
            causal_pointer_prefix = "/primary_draft"
        normalized = prepare_perpetual_synthesis(
            primary_draft,
            request_id=request.request_id,
            binding={
                key: materials[key]
                for key in (
                    "opportunity_id",
                    "context",
                    "candidate_target_ref",
                    "anchor_rule_ref",
                    "method_ref",
                    "method_code_hash",
                )
            },
            review_originals=catalog,
            requested_at=aware_utc(emission.known_at).isoformat().replace("+00:00", "Z"),
        )
        synthesis = normalized["candidate"]
    elif set(arguments) == {"candidate", "review_request"}:
        raw_candidate = arguments["candidate"]
        parsed = normalize_action_synthesis(raw_candidate)
        if raw_candidate.get("schema_version") == "research-decision-synthesis.v3":
            full_wrapper = raw_candidate
            synthesis = primary_perpetual_synthesis(raw_candidate)
            full_event_plan_draft_ref = raw_candidate["event_plan_draft_ref"]
            event_plan_ref = raw_candidate["event_plan_ref"]
            causal_pointer_prefix = "/primary_v2"
        else:
            synthesis = parsed.model_dump(mode="json")
        if synthesis["schema_version"] != "research-decision-synthesis.v2":
            return None
    else:
        return None
    if (
        synthesis["request_id"] != request.request_id
        or synthesis["candidate_set"]["context"] != materials["context"]
    ):
        raise ValueError("perpetual_experience_research_scope_invalid")
    validate_analysis_originals(synthesis, catalog)
    if request.context_manifest is None:
        raise ValueError("perpetual_experience_context_manifest_missing")
    uses = []
    for selected in selection["selected"]:
        original_evidence = next(
            item for item in original_task.input_evidence if item.evidence_id == selected["ref"]
        )
        selected_ref = {key: selected[key] for key in ("ref", "hash")}
        v2 = selection["schema_version"] in (
            "perpetual-experience-task-selection.v2", "perpetual-experience-task-selection.v3"
        )
        kind = selected.get("kind", "forecast_terminal_direction")
        if v2 and kind == "paper_episode_net_cashflow":
            from .perpetual_paper_experience import read_paper_experience_in_session

            if original_evidence.source_id != "perpetual_paper_episode_experience.v1":
                raise ValueError("perpetual_experience_kind_source_mismatch")
            experience = read_paper_experience_in_session(
                session, selected_ref, cutoff_at=request.context_manifest.created_at
            )
            identity = read_perpetual_admission_identity_in_session(session, request.run_id)
            if identity is None:
                raise ValueError("perpetual_experience_admission_missing")
            admission = identity[0]
            b, context = experience.body, admission["context"]
            if (
                b["owner_id"],
                b["account_kind"],
                b["account_id"],
                b["instrument_id"],
                b["profile_ref"],
                b["reference_horizon"],
            ) != (
                admission["owner_id"],
                context["account_kind"],
                context["account_id"],
                context["instrument_id"],
                context["profile_ref"],
                context["horizon"],
            ):
                raise ValueError("perpetual_experience_use_scope_mismatch")
        elif kind == "forecast_terminal_direction_v2":
            from .perpetual_forecast_scoring import read_scoring_experience_in_session
            if (not selection["schema_version"].endswith(".v3")
                    or original_evidence.source_id != "perpetual_forecast_experience.v2"):
                raise ValueError("perpetual_experience_kind_source_mismatch")
            experience = read_scoring_experience_in_session(session, selected_ref,
                cutoff_at=request.context_manifest.created_at)
            identity = read_perpetual_admission_identity_in_session(session, request.run_id)
            if identity is None:
                raise ValueError("perpetual_experience_admission_missing")
            admission = identity[0]
            if (
                experience.body["owner_id"], experience.body["instrument_id"],
                experience.body["horizon"],
            ) != (
                admission["owner_id"], admission["context"]["instrument_id"],
                admission["context"]["horizon"],
            ):
                raise ValueError("perpetual_experience_use_scope_mismatch")
        else:
            if (
                original_evidence.source_id != "perpetual_forecast_experience.v1"
                or kind != "forecast_terminal_direction"
            ):
                raise ValueError("perpetual_experience_kind_source_mismatch")
            experience = read_forecast_experience_in_session(
                session, selected_ref, cutoff_at=request.context_manifest.created_at
            )
        evidence = next(
            (item for item in request.input_evidence if item.evidence_id == selected["ref"]), None
        )
        if evidence is None or evidence.model_dump(mode="json") != original_evidence.model_dump(
            mode="json"
        ):
            uses.append(
                {
                    "experience_ref": selected_ref,
                    **({"experience_kind": kind} if v2 else {}),
                    "status": "not_delivered",
                    "consumed_as_historical_data": False,
                    "causal_links": [],
                }
            )
            continue
        entries = [
            entry
            for entry in catalog["originals"]
            if entry["source_kind"] == "frozen_input"
            and entry["role"] == evidence.evidence_id
            and entry["original_ref"] == selected_ref
            and strict_json(entry["body_json"]) == evidence.model_dump(mode="json")
        ]
        if len(entries) != 1:
            raise ValueError("perpetual_experience_catalog_original_missing")
        entry = entries[0]
        links = []
        for chain in ("main_chain", "opposite_chain"):
            for index, link in enumerate(synthesis["analysis"][chain]):
                if any(ref in (entry["source_ref"], selected_ref) for ref in link["evidence_refs"]):
                    links.append(
                        {
                            "pointer": f"{causal_pointer_prefix}/analysis/{chain}/{index}",
                            "link_id": link["link_id"],
                            "link_hash": canonical_hash(link),
                        }
                    )
        uses.append(
            {
                "experience_ref": selected_ref,
                **({"experience_kind": kind} if v2 else {}),
                "catalog_ref": entry["source_ref"],
                "experience_known_at": experience.body["recorded_at"],
                "excerpt_sha256": hashlib.sha256(evidence.excerpt.encode()).hexdigest(),
                "status": "referenced" if pointer is not None and links else "not_referenced",
                "consumed_as_historical_data": pointer is not None and bool(links),
                "causal_links": links,
            }
        )
    return dict(
        schema_version=(
            "perpetual-experience-provider-use.v3"
            if selection["schema_version"].endswith(".v3")
            else "perpetual-experience-provider-use.v2"
            if selection["schema_version"].endswith(".v2")
            else SCHEMA
        ),
        status="observed",
        run_id=request.run_id,
        generation=submit.generation,
        request_hash=submit.request_hash,
        task_hash=selection["task_hash"],
        provider_emission_ref=emission_ref.model_dump(mode="json"),
        provider_input_ref=input_ref.model_dump(mode="json"),
        prompt_hash=input_original.prompt_hash,
        prompt_pointer=pointer,
        prompt_body_hash=hashlib.sha256(prompt.prompt.encode()).hexdigest() if pointer else None,
        raw_arguments_hash=submit.raw_arguments_hash,
        synthesis_hash=canonical_hash(full_wrapper if full_wrapper is not None else synthesis),
        **(dict(full_wrapper_hash=canonical_hash(full_wrapper),
                primary_synthesis_hash=canonical_hash(synthesis),
                full_event_plan_draft_ref=full_event_plan_draft_ref,
                event_plan_ref=event_plan_ref,
                causal_source_schema_version=full_wrapper["schema_version"],
                causal_pointer_root="authored_draft" if arguments.get("action") == "prepare"
                else "submitted_synthesis") if full_wrapper is not None else {}),
        uses=uses,
        skill_consumed=False,
        financial_qualified=False,
        independently_reviewed=False,
        final_report_retention_proven=False,
        recorded_at=aware_utc(cutoff_at).isoformat(),
    )


def _read_v3_prior(
    session: Any, row: Any, reference: ObservedExecutionRef, *, cutoff_at: datetime,
) -> dict[str, Any]:
    """Re-read the first stored fact at its own time, including a writer race."""
    try:
        body = strict_json(row.payload_json)
        digest = canonical_hash(reference.model_dump(mode="json"))
        at = aware_utc(datetime.fromisoformat(body["recorded_at"].replace("Z", "+00:00")))
        if (
            row.output_id != "perp_experience_use_v3_" + digest
            or row.role != "perpetual_experience_provider_use_v3:" + digest
            or row.schema_version != "perpetual-experience-provider-use.v3"
            or body["schema_version"] != row.schema_version
            or row.run_id != body["run_id"]
            or as_utc(row.created_at) != at or at > aware_utc(cutoff_at)
            or canonical(body) != row.payload_json or canonical_hash(body) != row.payload_hash
            or body["provider_emission_ref"] != reference.model_dump(mode="json")
        ):
            raise ValueError("v3_prior_identity_or_clock_changed")
        rebuilt = derive_experience_use_in_session(session, reference, cutoff_at=at)
        if rebuilt is None or canonical(rebuilt) != row.payload_json:
            raise ValueError("v3_prior_original_facts_changed")
        return body
    except (ValueError, TypeError, KeyError, OSError) as error:
        raise ValueError("perpetual_experience_use_original_invalid") from error


def _prepared_v3_prior(
    database: Any, reference: ObservedExecutionRef, *, clock: Any,
) -> dict[str, Any]:
    address = "perp_experience_use_v3_" + canonical_hash(reference.model_dump(mode="json"))
    cutoff = aware_utc(clock())
    def read(session: Any) -> dict[str, Any]:
        row = session.get(RunRoleOutputRecord, address, populate_existing=True)
        if row is None:
            raise ValueError("perpetual_experience_use_original_invalid")
        return _read_v3_prior(session, row, reference, cutoff_at=cutoff)
    return prepare_originals(
        database, read, link_activity_observations=True, link_tool_counter_observations=True,
    ).value


def record_experience_use(database, reference, *, clock):
    ref = ObservedExecutionRef.model_validate(reference)
    digest = canonical_hash(ref.model_dump(mode="json"))
    prior_v3 = False
    with database.session() as session:
        for version, prefix in (
            ("v3", "perp_experience_use_v3_"),
            ("v2", "perp_experience_use_v2_"),
            ("v1", "perp_experience_use_"),
        ):
            prior = session.get(RunRoleOutputRecord, prefix + digest, populate_existing=True)
            if prior is not None:
                if version == "v3":
                    prior_v3 = True
                    break
                body = strict_json(prior.payload_json)
                if (
                    prior.role != "perpetual_experience_provider_use_" + version + ":" + digest
                    or prior.schema_version != "perpetual-experience-provider-use." + version
                    or body["schema_version"] != prior.schema_version
                    or canonical(body) != prior.payload_json
                    or prior.payload_hash != canonical_hash(body)
                    or body["provider_emission_ref"] != ref.model_dump(mode="json")
                ):
                    raise ValueError("perpetual_experience_use_original_invalid")
                return body
    if prior_v3:
        return _prepared_v3_prior(database, ref, clock=clock)
    prepared = prepare_originals(
        database,
        lambda session: derive_experience_use_in_session(session, ref, cutoff_at=clock()),
        link_activity_observations=True,
        link_tool_counter_observations=True,
    )
    body = prepared.value
    if body is None:
        return None
    version = body["schema_version"].rsplit(".", 1)[-1]
    prefix = "perp_experience_use_" if version == "v1" else "perp_experience_use_" + version + "_"
    output_id = prefix + digest
    concurrent_v3 = False
    with database.session() as session:
        begin_immediate(session)
        prepared.verify(session)
        old = session.get(RunRoleOutputRecord, output_id, populate_existing=True)
        if old is None:
            session.add(
                RunRoleOutputRecord(
                    output_id=output_id,
                    run_id=body["run_id"],
                    role="perpetual_experience_provider_use_" + version + ":" + digest,
                    schema_version=body["schema_version"],
                    payload_json=canonical(body),
                    payload_hash=canonical_hash(body),
                    created_at=datetime.fromisoformat(body["recorded_at"]),
                )
            )
            session.flush()
        elif version == "v3":
            concurrent_v3 = True
        prepared.verify_after_writes(session)
    if concurrent_v3:
        return _prepared_v3_prior(database, ref, clock=clock)
    return body
