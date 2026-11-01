"""Bind a first real Gateway attempt to a frozen skill node, never invoke another loop."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from packages.contracts_py.decision_hub_contracts import ResearchCapabilityResult
from packages.kernel.decision_hub_kernel.application.fact_store import _fact_model
from packages.kernel.decision_hub_kernel.application.provider_input_receipts import (
    row_reference,
    strict_json,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import _evidence_model
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    read_bundle_identity_in_session,
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_policy_checker import (
    _trajectory_anchor,
    _trajectory_call,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    ResearchEvidenceRecord,
    ResearchFactRecord,
    ResearchToolCallReservationRecord,
    RunRoleOutputRecord,
    as_utc,
)
from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc

from . import research_skill_compiler as compiler
from .research_skill_occurrence import read_occurrence
from .research_skill_package import decoded, encoded, sha

MATCH_FIELDS = (
    "requirement_id",
    "target_url",
    "symbols",
    "fields",
    "allowed_domains",
    "max_results",
)


@dataclass(frozen=True)
class GatewayNodeOriginal:
    node_id: str
    input_json: bytes
    output_json: bytes
    proof_json: bytes
    attempts_json: bytes
    completed_at: datetime
    inputs_known_through: datetime


class SkillGatewayAttemptFailure(ValueError):
    def __init__(self, code, attempts):
        super().__init__(code)
        self.attempts_json = encoded(attempts)


def _prepared_gateway(session, run_id, generation):
    prepared = read_prepared_execution_in_session(session, run_id, generation)
    bundle, identity = read_bundle_identity_in_session(session, run_id)
    anchor, _ = _trajectory_anchor(session, bundle, identity, prepared)
    return prepared, anchor


def requested_intent_mismatches(arguments, expected, capability_id):
    """Names only; no relaxation of effect-bearing matching or retry census."""
    fields = ["capability_id"] if arguments.get("capability_id") != capability_id else []
    fields.extend(field for field in MATCH_FIELDS if arguments.get(field) != expected[field])
    fields.extend(
        key
        for key in (
            "event_id",
            "event_at",
            "window_start_at",
            "window_end_at",
            "requested_event_offsets",
            "evidence_ref",
            "content_ref",
        )
        if arguments.get(key) not in (None, [])
    )
    return fields


def read_gateway_node_in_session(
    use,
    session,
    *,
    run_id,
    generation,
    execution_session_id,
    loaded_event_ref,
    node_id,
    workflow_input,
    requested_call_id,
    occurrence_proof,
    completed_outputs=None,
    parent_known_at=(),
    prepared_reader=_prepared_gateway,
):
    """First seed supports spot_market_facts; all originals are independently re-read.

    completed_outputs/parent_known_at are supplied by the node event reader, never API JSON.
    First matching failed/refused attempt is not skipped for a favorable later result.
    """
    loaded = use.read_loaded_in_session(session, run_id=run_id, event_ref=loaded_event_ref)
    selection, _, identity = use._selection(session, run_id, generation, execution_session_id)
    if loaded["use_id"] != selection["use_id"]:
        raise ValueError("skill_gateway_loaded_selection_mismatch")
    plan_bytes = use.packages.archive.read(selection["compiled_plan_ref"])
    if sha(plan_bytes) != selection["compiled_plan_hash"]:
        raise ValueError("skill_gateway_compiled_plan_changed")
    compiled = compiler.CompiledWorkflow(plan_bytes.decode(), selection["compiled_plan_hash"])
    plan = compiled.read()
    node = next((row for row in plan["workflow"]["nodes"] if row["id"] == node_id), None)
    if (
        node is None
        or node["op"] != "evidence.acquire"
        or node["capability_id"] != "spot_market_facts"
    ):
        raise ValueError("skill_gateway_node_not_installed")
    bound = compiler.bind_node_inputs(compiled, node_id, workflow_input, completed_outputs or {})
    expected = decoded(bound.input_json)
    use.packages.contracts.check("gateway_intent_v1", expected)
    use.packages.contracts.check("tool_occurrence_proof_v1", occurrence_proof)
    if occurrence_proof["call_id"] != requested_call_id:
        raise ValueError("skill_gateway_call_identity_mismatch")
    occurrence = read_occurrence(
        session,
        occurrence_proof,
        run_id=run_id,
        generation=generation,
        root_session_id=identity["dsh_session_id"],
        execution_session_id=execution_session_id,
        request_hash=identity["request_hash"],
        tool_name="decision_hub_research",
        provider_reader=use.provider_reader,
        emission_reader=use.emission_reader,
    )
    loaded_at = datetime.fromisoformat(loaded["first_known_at"])
    call_at = aware_utc(occurrence.emission.known_at)
    if call_at <= loaded_at or any(aware_utc(stamp) >= call_at for stamp in parent_known_at):
        raise ValueError("skill_gateway_before_loaded_or_dependencies")
    mismatches = requested_intent_mismatches(occurrence.arguments, expected, node["capability_id"])
    if mismatches:
        raise SkillGatewayAttemptFailure(
            "skill_gateway_arguments_mismatch:" + ",".join(mismatches), []
        )
    # Existing schema contains no free-form retry budget. This node selects the
    # first actual matching attempt and retains failed/refused attempts as failure.
    rows = session.scalars(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.schema_version == "research-provider-tool-emission.v1",
        )
        .order_by(RunRoleOutputRecord.created_at, RunRoleOutputRecord.output_id)
        .limit(513)
        .execution_options(populate_existing=True)
    ).all()
    if len(rows) > 512:
        raise ValueError("skill_gateway_emission_census_limit")
    positions = {}
    for mi, message in enumerate(occurrence.transcript["messages"]):
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            continue
        for bi, block in enumerate(message["content"]):
            if isinstance(block, dict) and block.get("type") == "tool-call":
                positions.setdefault(block.get("id"), []).append((mi, bi))
    attempts = []
    for row in rows:
        raw = strict_json(row.payload_json)
        meta = raw.get("submit")
        if (
            not isinstance(meta, dict)
            or meta.get("tool_name") != "decision_hub_research"
            or meta.get("generation") != generation
            or meta.get("execution_session_id") != execution_session_id
            or meta.get("root_session_id") != identity["dsh_session_id"]
        ):
            continue
        pair = use.emission_reader(
            session,
            run_id=run_id,
            generation=generation,
            root_session_id=identity["dsh_session_id"],
            execution_session_id=execution_session_id,
            call_id=meta["call_id"],
        )
        if pair is None:
            raise ValueError("skill_gateway_emission_original_missing")
        emitted, emitted_ref = pair
        known = aware_utc(emitted.known_at)
        if known <= loaded_at or known > aware_utc(occurrence.provider_input.known_at):
            continue
        args = strict_json(emitted.submit.raw_arguments_json)
        if args.get("capability_id") != node["capability_id"] or any(
            args.get(field) != expected[field] for field in MATCH_FIELDS
        ):
            continue
        # Only the exact-asset spot adapter ignores the free query prose. All
        # effect-bearing request fields are pinned; no event/readback expansion.
        if any(
            args.get(key) not in (None, [])
            for key in (
                "event_id",
                "event_at",
                "window_start_at",
                "window_end_at",
                "requested_event_offsets",
                "evidence_ref",
                "content_ref",
            )
        ):
            continue
        loci = positions.get(meta["call_id"], [])
        if len(loci) != 1:
            raise ValueError("skill_gateway_first_attempt_census_unavailable")
        reservation = session.get(
            ResearchToolCallReservationRecord, (run_id, meta["call_id"]), populate_existing=True
        )
        attempts.append(
            {
                "call_id": meta["call_id"],
                "transcript_position": list(loci[0]),
                "provider_emission_ref": emitted_ref.model_dump(mode="json"),
                "status": reservation.status if reservation else "no_reservation",
                "error_json": reservation.error_json if reservation else None,
            }
        )
    attempts.sort(key=lambda item: item["transcript_position"])
    if not attempts or attempts[0]["call_id"] != requested_call_id:
        raise SkillGatewayAttemptFailure("skill_gateway_not_first_matching_attempt", attempts)
    if attempts[0]["status"] != "completed":
        raise SkillGatewayAttemptFailure("skill_gateway_first_attempt_failed", attempts)
    reservation = session.get(
        ResearchToolCallReservationRecord, (run_id, requested_call_id), populate_existing=True
    )
    prepared, anchor = prepared_reader(session, run_id, generation)
    call, originals, problem = _trajectory_call(
        session,
        reservation,
        requested_call_id,
        occurrence.arguments,
        occurrence_proof["call_hash"],
        occurrence_proof["result_hash"],
        prepared,
        anchor,
    )
    if problem or call is None or call.adapter_output_id is None:
        raise ValueError("skill_gateway_admission_original_unavailable:" + str(problem))
    completed = as_utc(reservation.completed_at)
    if (
        completed is None
        or completed < call_at
        or completed > aware_utc(occurrence.provider_input.known_at)
    ):
        raise ValueError("skill_gateway_completion_clock_invalid")
    result = ResearchCapabilityResult.model_validate_json(reservation.result_json)
    native_result = ResearchCapabilityResult.model_validate_json(encoded(occurrence.result))
    if result != native_result or result.request_id != requested_call_id:
        raise ValueError("skill_gateway_native_result_mismatch")
    evidence, facts, times = [], [], []
    for item in result.evidence_candidates:
        row = session.get(ResearchEvidenceRecord, item.evidence_id, populate_existing=True)
        if row is None or row.run_id != run_id or _evidence_model(row) != item:
            raise ValueError("skill_gateway_admitted_evidence_changed")
        if (
            item.quality == "accepted"
            and item.research_session_id == identity["dsh_session_id"]
            and item.round == generation
            and item.tool_call_id == requested_call_id
        ):
            evidence.append(item.evidence_id)
            times.append(item.received_at)
    instrument = selection["scope_id"].split(":")[3]
    for item in result.facts or ():
        row = session.get(ResearchFactRecord, item.fact_id, populate_existing=True)
        if row is None or row.run_id != run_id or _fact_model(row) != item:
            raise ValueError("skill_gateway_admitted_fact_changed")
        if (
            item.quality == "accepted"
            and item.evidence_id in evidence
            and item.instrument == instrument
            and item.venue == "okx"
        ):
            facts.append(item.fact_id)
            times.append(item.received_at)
    if not evidence or not facts or max(times) > completed:
        raise ValueError("skill_gateway_accepted_typed_facts_required")
    adapter = next(row for row in originals if row.output_id == call.adapter_output_id)
    adapter_body = decoded(adapter.payload_json)
    proof = {
        "kind": "gateway",
        "native_research_call": occurrence_proof,
        "provider_emission_ref": occurrence.emission_ref.model_dump(mode="json"),
        "gateway_invocation_ref": {
            "run_id": run_id,
            "request_id": requested_call_id,
            "research_session_id": identity["dsh_session_id"],
            "generation": generation,
            "capability_id": node["capability_id"],
            "query_hash": reservation.query_hash,
            "result_hash": sha(reservation.result_json.encode()),
        },
        "gateway_result_ref": row_reference(adapter).model_dump(mode="json"),
        "effective_query_hash": adapter_body["effective_adapter_query_hash"],
        "adapter_result_hash": adapter_body["adapter_result_hash"],
    }
    use.packages.contracts.check("gateway_execution_proof_v1", proof)
    output = result.model_dump(mode="json")
    compiler.validate_node_output(compiled, node_id, output)
    return GatewayNodeOriginal(
        node_id,
        bound.input_json.encode(),
        encoded(output),
        encoded(proof),
        encoded(attempts),
        completed,
        max(times),
    )
