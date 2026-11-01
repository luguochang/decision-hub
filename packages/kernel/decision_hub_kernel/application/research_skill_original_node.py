"""Current-generation local read execution over policy-frozen original data."""

from datetime import datetime

from ..ports.market_path import aware_utc, canonical_hash
from . import research_skill_compiler as compiler
from .research_execution_binding import read_prepared_execution_in_session
from .research_skill_original_read import PROTOCOL, read_spot_original_in_session
from .research_skill_package import decoded
from .research_skill_repair_policy import (
    LATE_MODE,
    prompt_policy_reference,
    read_repair_policy_in_session,
)


def derive_original_node(nodes, session, intent):
    selection, _, identity, plan = nodes._base(session, intent)
    loaded = nodes.use.read_loaded_in_session(
        session, run_id=intent["run_id"], event_ref=intent["loaded_event_ref"]
    )
    if loaded["use_id"] != selection["use_id"] or loaded["generation"] != intent["generation"]:
        raise ValueError("skill_original_loaded_selection_mismatch")
    prepared = read_prepared_execution_in_session(session, intent["run_id"], intent["generation"])
    policy_ref = prompt_policy_reference(prepared.prompt.prompt)
    if policy_ref is None:
        raise ValueError("skill_original_policy_required")
    policy = read_repair_policy_in_session(
        session,
        run_id=intent["run_id"],
        generation=intent["generation"],
        reference=policy_ref,
        request=prepared.execution.request,
    )
    if policy["policy"] != LATE_MODE:
        raise ValueError("skill_original_policy_mode_invalid")
    original_selection = nodes.use.selections.read_in_session(session, intent["run_id"])
    source = read_spot_original_in_session(
        nodes.use,
        nodes.market,
        session,
        selection=original_selection,
        source_generation=policy["source_generation"],
        cutoff_at=datetime.fromisoformat(policy["acquisition_close_at"]),
        prepared_reader=nodes.gateway_prepared_reader,
        expected_manifest=policy["original_source"],
    )
    pair = nodes.use.emission_reader(
        session,
        run_id=intent["run_id"],
        generation=intent["generation"],
        root_session_id=identity["dsh_session_id"],
        execution_session_id=intent["execution_session_id"],
        call_id=intent["call_id"],
    )
    if pair is None:
        raise ValueError("skill_original_native_read_missing")
    emission, emission_ref = pair
    expected = dict(
        operation="execute_step",
        skill_version_id=selection["skill_version_id"],
        node_id="spot_evidence",
        input={"source": "frozen_original"},
    )
    read_at = aware_utc(datetime.fromisoformat(intent["read_at"]))
    cutoff = datetime.fromisoformat(policy["acquisition_close_at"])
    deadline = min(
        datetime.fromisoformat(policy[key])
        for key in ("original_deadline_at", "binding_deadline_at")
    )
    if (
        emission.submit.tool_name != "decision_hub_skill"
        or decoded(emission.submit.raw_arguments_json) != expected
        or emission.submit.request_hash != identity["request_hash"]
        or emission.submit.generation != intent["generation"]
        or emission.submit.execution_session_id != intent["execution_session_id"]
        or not datetime.fromisoformat(loaded["first_known_at"]) < aware_utc(emission.known_at)
        or not cutoff <= aware_utc(emission.known_at) <= read_at < deadline
    ):
        raise ValueError("skill_original_native_read_identity_or_time_invalid")
    bound = compiler.bind_node_inputs(
        plan, "spot_evidence", decoded(source.workflow_input_json), {}
    )
    if decoded(bound.input_json) != decoded(source.input_json):
        raise ValueError("skill_original_current_node_query_changed")
    proof = dict(
        kind="original_read",
        reader_protocol=PROTOCOL,
        repair_policy_ref=policy_ref,
        native_read_emission_ref=emission_ref.model_dump(mode="json"),
        source_manifest_hash=canonical_hash(policy["original_source"]),
        source_generation=policy["source_generation"],
        source_call_id=policy["original_source"]["call_id"],
    )
    details = nodes._details(
        selection,
        plan,
        "spot_evidence",
        intent["loaded_event_ref"],
        [],
        decoded(source.input_json),
        decoded(source.output_json),
        proof,
        read_at,
        datetime.fromisoformat(policy["original_source"]["source_known_through"]),
    )
    enriched = {
        **intent,
        "original_deadline_at": deadline.isoformat(),
        "workflow_input": decoded(source.workflow_input_json),
        "original_market_refs": [list(pair) for pair in source.market_refs],
        "attempts": policy["original_source"]["attempts"],
    }
    if "original_deadline_at" in intent and intent["original_deadline_at"] != deadline.isoformat():
        raise ValueError("skill_original_read_deadline_changed")
    return selection, details, enriched, source.market_refs
