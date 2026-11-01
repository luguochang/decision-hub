"""Normal selected request + native SDK fixture into Graph; no model invocation."""

import asyncio

import pytest

from packages.kernel.decision_hub_kernel.application import research_graph_binding as graph
from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
    read_perpetual_research_materials_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_context_selection import (
    ContextSelectionService,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    native_observation_context_in_session,
)
from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedResearchResult
from tests.decision_product import test_perpetual_native_graph as native
from tests.decision_product import test_perpetual_selected_dispatch_identity as dispatch
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case


def test_authenticated_normal_selection_native_carrier_enters_first_v2_graph(binding_case, tmp_path, monkeypatch):
    dispatch._freeze_clock(monkeypatch)
    monkeypatch.setattr(native, "AT", dispatch.CLOCK)
    original = []

    def selected_setup(case, factory, binding_service):
        factory = dispatch._live_factory(case, factory, binding_service)
        base = factory.build(case["event_id"], case["run_id"])
        original.append(base)
        assert dispatch.RunService(case["db"], clock=lambda: dispatch.CLOCK).claim_for_execution(
            base.run_id, worker_id=dispatch.OWNER, lease_seconds=300,
        )
        selected = ContextSelectionService(case["db"], clock=lambda: dispatch.CLOCK).select(
            run_id=base.run_id, graph_round=1, attempt_basis="initial_round", previous_graph_ref=None,
            expected_lease_owner=dispatch.OWNER,
        )
        host = dispatch._BoundaryHost()
        runtime = dispatch.DshWebResearchRuntime(dispatch.DshSessionLinkService(case["db"]), host,
                                                poll_interval_seconds=0.001, readiness_grace_seconds=0.1)
        with pytest.raises(dispatch._DispatchBoundaryReached):
            asyncio.run(runtime.execute_selected_observed(selected.request, selected.reference,
                                                         expected_lease_owner=dispatch.OWNER))
        assert len(host.submit_calls) == 1
        with case["db"].session() as session:
            prepared = dispatch.read_prepared_execution_in_session(session, base.run_id)
            actual = prepared.execution.request
            assert actual.model_dump(mode="json") != base.model_dump(mode="json")
            context = native_observation_context_in_session(session, base.run_id)
            materials = read_perpetual_research_materials_in_session(
                session, actual, selection_reference=dispatch._selection_reference(prepared.execution),
                expected_lease_owner=dispatch.OWNER,
            )
        return actual, context, materials

    value = native.build_perpetual_native_graph_case(
        binding_case, tmp_path, monkeypatch, run_graph=False, request_setup=selected_setup,
    )
    db = value["db"]
    observed = ObservedResearchResult(result=value["result"], observation_ref=value["mapped_ref"])
    with db.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        frozen = graph.admit_observed_round_in_session(
            session, run_id=value["request"].run_id, expected_lease_owner=dispatch.OWNER,
            observed=observed, database=db, previous=None,
        )
    with db.session() as session:
        reopened = graph.read_graph_round_in_session(
            session, run_id=value["request"].run_id, reference=frozen.reference,
            expected_lease_owner=dispatch.OWNER, require_current_permission=True,
        )
        assert reopened.reference == frozen.reference
        assert reopened.receipt.observation_ref == value["mapped_ref"]
        assert reopened.receipt.selected_mapping_generation == value["context"].generation
        assert graph._original_request(session, value["request"].run_id) == original[0]
    with db.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        with pytest.raises(ValueError, match="research_perpetual_graph_first_slice_required"):
            graph.admit_observed_round_in_session(
                session, run_id=value["request"].run_id, expected_lease_owner=dispatch.OWNER,
                observed=observed, database=db, previous=frozen.reference,
            )
