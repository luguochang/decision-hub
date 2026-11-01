"""Actual Factory/SQLite/native Tool functions with a synthetic child backend; no financial grant."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from runpy import run_path

import pytest

from packages.contracts_py.decision_hub_contracts import (
    DshSessionResult,
)
from packages.kernel.decision_hub_kernel.application import research_graph_binding as graph
from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
    read_perpetual_research_materials_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    native_observation_context_in_session,
    observation_sha,
    store_mapped_perpetual_observation,
    store_transport_observation,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.application.research_review import (
    research_result_content_hash,
)
from packages.kernel.decision_hub_kernel.ports.research_observation import (
    ObservedResearchResult,
    ResearchMappedObservationV2,
    ResearchPerpetualSynthesisObservation,
    ResearchTransportObservation,
)
from packages.runtime_adapters.dsh_runtime.event_mapper import map_notifications
from packages.runtime_adapters.dsh_runtime.result_mapper import map_session_result_with_witness
from packages.runtime_adapters.dsh_runtime.web_runtime import _sdk_run
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import AT, factory_for
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case

ROOT = Path(__file__).resolve().parents[2]


def packet_for(request, materials):
    helpers = run_path(str(ROOT / "tests/contracts/test_d2_candidate_contract.py"))
    packet = helpers["review_request"]()
    synthesis = packet["synthesis"]
    context = deepcopy(materials["context"])
    synthesis.update(request_id=request.request_id, opportunity_id=materials["opportunity_id"],
                     as_of=AT.isoformat().replace("+00:00", "Z"), known_at_max=AT.isoformat().replace("+00:00", "Z"),
                     candidate_target=deepcopy(materials["candidate_target"]), anchor_rule=deepcopy(materials["anchor_rule"]),
                     method_ref=materials["method_ref"], method_code_hash=materials["method_code_hash"])
    candidates = synthesis["candidate_set"]
    candidates.update(context=context, as_of=synthesis["as_of"], valid_until=materials["candidate_target"]["valid_until"],
                      candidate_target_ref=materials["candidate_target_ref"], anchor_rule_ref=materials["anchor_rule_ref"])
    origin = next(row['source_ref'] for row in materials['review_originals']['originals'] if row['source_kind'] == 'server_material' and row['role'] == 'close_window')
    synthesis['reasoning_refs'] = [origin]
    synthesis['analysis'] = helpers['causal_analysis_for'](synthesis)
    for candidate in candidates["candidates"]:
        candidate.update(context=deepcopy(context), valid_until=candidates["valid_until"])
    candidates["candidate_refs"] = [helpers["body_ref"](row["candidate_id"], row) for row in candidates["candidates"]]
    synthesis["candidate_set_ref"] = helpers["body_ref"]("set", candidates)
    for estimate in [synthesis["event_probability"]["occurrence_distribution"], synthesis["event_probability"]["outcome_given_occurrence"], synthesis["market_distribution"]["direction_distribution"]]:
        estimate["as_of"] = synthesis["as_of"]
    synthesis["event_probability_ref"] = helpers["body_ref"]("event", synthesis["event_probability"])
    paths = synthesis["path_distribution"]
    paths.update(market_target_ref=materials["candidate_target_ref"], conditioning_ref=materials["candidate_target"]["conditioning_ref"])
    threshold = Decimal(materials["candidate_target"]["threshold_fraction"])
    amplitude = max(Decimal("0.02"), threshold * 2)
    paths["scenarios"][0]["terminal_return_fraction"] = str(amplitude)
    paths["scenarios"][2]["terminal_return_fraction"] = str(-min(Decimal("0.9"), amplitude))
    market = synthesis["market_distribution"]
    market.update(market_target_ref=materials["candidate_target_ref"], instrument_ref=context["instrument_ref"],
                  event_scenario_ref=synthesis["event_probability_ref"], conditioning_ref=paths["conditioning_ref"],
                  path_distribution_ref=helpers["body_ref"]("assumed-paths", paths))
    market["direction_distribution"].update(target_ref=materials["candidate_target_ref"], conditioning_ref=paths["conditioning_ref"])
    grouped = dict.fromkeys(["up", "neutral", "down"], Decimal(0))
    for scenario in paths["scenarios"]:
        terminal = Decimal(scenario["terminal_return_fraction"])
        grouped["up" if terminal > threshold else "down" if terminal < -threshold else "neutral"] += Decimal(scenario["probability"])
    for probability in market["direction_distribution"]["probabilities"]:
        probability["value"] = str(grouped[probability["outcome_id"]])
    synthesis["market_distribution_ref"] = helpers["body_ref"]("market", market)
    synthesis["economic_input_refs"] = [market["path_distribution_ref"]]
    policy = materials["allowed_domain_policy"]
    domain = dict(schema_version="action-comparison-allowed-domain.v1", candidate_set_ref=synthesis["candidate_set_ref"],
                  candidate_target_ref=materials["candidate_target_ref"], anchor_rule_ref=materials["anchor_rule_ref"],
                  event_probability_ref=synthesis["event_probability_ref"], market_distribution_ref=synthesis["market_distribution_ref"],
                  **{key: policy[key] for key in ["method_ref", "method_code_hash", "variables", "invalidation_refs", "valid_until"]})
    synthesis["allowed_domain_ref"] = helpers["body_ref"]("domain", domain)
    packet.update(synthesis_ref=helpers["body_ref"]("synth", synthesis), allowed_domain_ref=synthesis["allowed_domain_ref"],
                  allowed_domain=domain, requested_at=AT.isoformat().replace("+00:00", "Z"))
    return packet


def prepare(case, request):
    from tests.decision_product.test_perpetual_research_binding import prepare_execution
    prepare_execution(case, request)
    with case["db"].session() as session:
        return native_observation_context_in_session(session, request.run_id), read_perpetual_research_materials_in_session(session, request)


def build_perpetual_native_graph_case(binding_case, tmp_path, monkeypatch, *, before_graph=None, packet_builder=packet_for, phased=False, run_graph=True, request_setup=None, execution_mode="replay"):
    case = binding_case
    factory, _service = factory_for(case, tmp_path, execution_mode=execution_mode)
    if request_setup is None:
        request = factory.build(case["event_id"], case["run_id"])
        context, materials = prepare(case, request)
    else:
        request, context, materials = request_setup(case, factory, _service)
    from packages.kernel.decision_hub_kernel.ports.research_action_synthesis import (
        normalize_action_synthesis,
    )
    packet = packet_builder(request, materials)
    if not phased:
        normalize_action_synthesis(packet["synthesis"])
    input_path, output_path = tmp_path / "native-input.json", tmp_path / "native-output.json"
    native_input = dict(request=request.model_dump(mode="json"), request_hash=research_request_hash(request), review_request=packet, review_originals=materials['review_originals'])
    if phased:
        from tests.decision_product.perpetual_draft_fixture import draft_from_packet
        native_input['draft'] = draft_from_packet(packet, materials['review_originals'])
    input_path.write_text(json.dumps(native_input), encoding="utf-8")
    env = dict(os.environ, D2_NATIVE_INPUT=str(input_path), D2_NATIVE_OUTPUT=str(output_path), D2_WRITE_NATIVE_FIXTURE="1")
    completed = subprocess.run([str(ROOT / "apps/decision-desk/node_modules/.bin/vitest.cmd"), "run", "tests/native-v2.spec.ts", "--environment", "node", "-t", "prepares one authored draft" if phased else "launches one separate child"], cwd=ROOT / "extensions/dsh/decision-hub", env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    transcript = json.loads(output_path.read_text(encoding='utf-8'))
    events = [{"method": "session.event", "payload": {"sessionId": context.dsh_session_id, "event": event}} for event in transcript["context"]["events"]]
    events_json = json.dumps(events)
    payload = DshSessionResult(schema_version="dsh-session-result.v1", run_id=request.run_id, dsh_session_id=context.dsh_session_id,
        generation=1, last_seq=7, final_response="", finish_reason="completed", events_json=events_json,
        started_at=AT, finished_at=AT + timedelta(seconds=8), trace_ref="synthetic://native-transcript",
        result_hash=hashlib.sha256(("\0" + events_json).encode()).hexdigest())
    sdk = _sdk_run(payload)
    traces = map_notifications(sdk.notifications, run_id=request.run_id, research_session_id=context.dsh_session_id, received_at=payload.finished_at, request=request)
    from packages.kernel.decision_hub_kernel.application.research_execution_binding import read_prepared_execution_in_session
    with case["db"].session() as session:
        actual_definition = read_prepared_execution_in_session(session, request.run_id).method.definition
    mapped = map_session_result_with_witness(sdk, request, traces, runtime_version=actual_definition.runtime_version, profile_ref=actual_definition.profile_ref, started_at=payload.started_at, finished_at=payload.finished_at, accepted_synthesis_only=True)
    native = mapped.perpetual_synthesis
    assert native is not None
    # Real shared quota reservations for this replay backend. They attest budget
    # consumption only; the Graph below cannot claim financial/provider approval.
    budget = ResearchObservabilityService(case["db"], clock=lambda: AT + timedelta(seconds=9))
    for index in range(mapped.result.total_tool_calls):
        reservation = budget.reserve_tool_call(run_id=request.run_id, request_id=f"replay-native-{index}",
            research_session_id=context.dsh_session_id, generation=1, capability_id="replay.research", query_hash="a" * 64,
            admission_check=lambda _session, saved: observation_sha(saved.execution.request.model_dump(mode="json")))
        assert reservation.outcome == "reserved"
    with case["db"].session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        raw_ref = store_transport_observation(session, ResearchTransportObservation(schema_version="research-transport-observation.v1", context=context,
            phase="result", reply=payload, error_code=None, events_json_hash=hashlib.sha256(events_json.encode()).hexdigest(), final_response_hash=hashlib.sha256(b"").hexdigest(), recorded_at=AT + timedelta(seconds=10)))
        selected = native.selection.model_dump(mode="json")
        selected.update(call_event_hash=observation_sha(events[native.selection.call_event_index]), result_event_hash=observation_sha(events[native.selection.result_event_index]))
        nested = {key: value for key, value in asdict(native).items() if key not in {"synthesis", "review_request", "authorization", "selection"}}
        nested.update(synthesis=native.synthesis, review_request=native.review_request, authorization=native.authorization, selection=selected)
        carrier = ResearchMappedObservationV2(schema_version="research-mapped-observation.v2", context=context, mapping_input_ref=raw_ref, latest_transport_ref=raw_ref,
            mapping_input_generation=1, latest_transport_generation=1, perpetual_synthesis=ResearchPerpetualSynthesisObservation.model_validate(nested),
            result=mapped.result, result_content_hash=research_result_content_hash(mapped.result), recorded_at=AT + timedelta(seconds=11))
        ref = store_mapped_perpetual_observation(session, carrier, binding_validator=lambda current: native_observation_context_in_session(current, request.run_id) == context or (_ for _ in ()).throw(ValueError("changed context")))
    monkeypatch.setattr(graph, "utcnow", lambda: AT + timedelta(seconds=20))
    from packages.kernel.decision_hub_kernel.application import research_perpetual_graph
    monkeypatch.setattr(research_perpetual_graph, "utcnow", lambda: AT + timedelta(seconds=20))
    value = dict(case=case, db=case["db"], request=request, context=context, materials=materials,
                 native=native, mapped=mapped, mapped_ref=ref, raw_ref=raw_ref, payload=payload, events=events,
                 transcript=transcript, result=mapped.result, lease_owner="binding-test", clock=lambda: AT + timedelta(seconds=20))
    from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
        read_prepared_execution_in_session,
    )
    with case["db"].session() as session:
        value["execution"] = read_prepared_execution_in_session(session, request.run_id)
    if before_graph is not None:
        before_graph(value)
    if not run_graph:
        return value
    from packages.kernel.decision_hub_kernel.application.research_evidence import (
        ResearchEvidenceService,
    )
    from packages.orchestration.langgraph.graphs.agentic_research_graph import (
        build_agentic_research_graph,
        initial_research_state,
    )

    class StoredObservedRuntime:
        calls = 0

        async def execute_observed(self, actual, trace_sink=None):
            assert actual.model_dump(mode="json") == request.model_dump(mode="json")
            self.calls += 1
            return ObservedResearchResult(result=mapped.result, observation_ref=ref)

    runtime = StoredObservedRuntime()
    compiled = build_agentic_research_graph(runtime, ResearchEvidenceService(case["db"]), expected_lease_owner="binding-test",
        selection_clock=value["clock"], execution_clock=value["clock"])
    state = asyncio.run(compiled.ainvoke(initial_research_state(request)))
    assert runtime.calls == 1 and state["financial_qualification"] == "unqualified"
    with case["db"].session() as session:
        final = graph.read_graph_final_in_session(session, run_id=request.run_id, reference=graph.parse_graph_final_ref(state["graph_final_projection_ref"]), expected_lease_owner="binding-test", require_current_permission=True)
        frozen = graph.read_graph_round_in_session(session, run_id=request.run_id, reference=final.receipt.last_admission_ref, expected_lease_owner="binding-test", require_current_permission=True)
        value.update(round=frozen, final=final, graph_final_ref=final.reference, graph_state=state)
    with case["db"].session() as session:
        reopened = graph.read_graph_final_in_session(session, run_id=request.run_id, reference=final.reference, expected_lease_owner=None, require_current_permission=False)
        assert reopened == final
    return value


@pytest.fixture
def perpetual_native_graph_case(binding_case, tmp_path, monkeypatch):
    return build_perpetual_native_graph_case(binding_case, tmp_path, monkeypatch)


def test_actual_factory_native_carrier_reaches_graph_without_v1_projection(perpetual_native_graph_case):
    value = perpetual_native_graph_case
    final = value["final"]
    assert final.receipt.financial_qualification == "unqualified"
    assert final.receipt.selected_synthesis_observation_ref == value["mapped_ref"]
    assert final.result.horizons == [] and final.result.causal_case is None and not final.result.reviews
