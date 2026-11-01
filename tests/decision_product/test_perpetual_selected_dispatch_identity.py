"""Real SQLite selected-request identity through the pre-dispatch boundary."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest
import yaml

from apps.hub_worker.research import ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import DshHostReadiness, DshUpstreamIdentity
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
    read_native_perpetual_binding,
    read_perpetual_research_materials_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_binding import (
    PreparedResearchPolicyRefs,
)
from packages.kernel.decision_hub_kernel.application.research_context_dispatch import (
    parse_context_selection_reference,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.orchestration.langgraph.graphs.agentic_research_graph import (
    build_agentic_research_graph,
    initial_research_state,
)
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import AT, factory_for
from tests.decision_product.test_perpetual_research_binding import (
    binding_case as _binding_case,  # noqa: F401
)

OWNER = "binding-test"
CLOCK = AT + timedelta(seconds=20)


_REAL_DATETIME = datetime


class _FixedDateTimeMeta(type):
    def __instancecheck__(cls, value):
        return isinstance(value, _REAL_DATETIME)


class _FixedDateTime(datetime, metaclass=_FixedDateTimeMeta):
    @classmethod
    def now(cls, tz=None):
        return CLOCK if tz is not None else CLOCK.replace(tzinfo=None)


class _DispatchBoundaryReached(BaseException):
    """Stop exactly where a real client would hand work to the model host."""


class _BoundaryHost:
    def __init__(self) -> None:
        self.submit_calls = []

    async def readiness(self):
        return DshHostReadiness(
            schema_version="dsh-host-readiness.v1",
            ready=True,
            version_compatible=True,
            session_controller=True,
            client_plugin=True,
            hub_reachable=True,
            upstream_identity=DshUpstreamIdentity(
                source_commit="c" * 40,
                source_version="selected-dispatch-gate",
                package_versions={},
                plugin_build_hash="b" * 64,
            ),
            checked_at=CLOCK,
            error_code=None,
        )

    async def submit(self, payload):
        self.submit_calls.append(payload)
        raise _DispatchBoundaryReached

    async def close(self):
        return None


def _selection_reference(execution):
    identity = execution.current_selection_row.model_dump(mode="json")
    identity["schema_version"] = identity.pop("row_schema_version")
    return parse_context_selection_reference(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def _freeze_clock(monkeypatch) -> None:
    from packages.kernel.decision_hub_kernel.application import (
        research_context_execution,
        research_execution_binding,
        research_graph_binding,
        research_perpetual_graph,
    )
    from packages.runtime_adapters.dsh_runtime import research_runtime_binding, web_runtime

    for module in (
        research_context_execution,
        research_execution_binding,
        research_runtime_binding,
        web_runtime,
    ):
        monkeypatch.setattr(module, "datetime", _FixedDateTime)
    monkeypatch.setattr(research_graph_binding, "utcnow", lambda: CLOCK)
    monkeypatch.setattr(research_perpetual_graph, "utcnow", lambda: CLOCK)


def _live_factory(case, prepared_factory, binding_service):
    manifest = yaml.safe_load((prepared_factory.pack_root / "pack.yaml").read_text("utf-8"))
    return ResearchRequestFactory(
        case["db"],
        pack_root=prepared_factory.pack_root,
        execution_mode="live",
        allowed_capabilities=("replay.research", "document.readback", "web.search"),
        prepared_policy_refs=PreparedResearchPolicyRefs(
            gate_policy_ref=manifest["gate_policy_ref"],
            rubric_policy_ref=manifest["evaluation_policy_ref"],
            role_profile_refs=tuple(manifest["role_profile_refs"]),
        ),
        perpetual_binding_service=binding_service,
        clock=lambda: AT,
    )


def test_normal_worker_graph_selected_request_reaches_dispatch_and_native_catalog(
    _binding_case, tmp_path, monkeypatch  # noqa: F811 - pytest fixture keeps alias name
):
    _freeze_clock(monkeypatch)
    case = _binding_case
    prepared_factory, binding_service = factory_for(case, tmp_path)
    factory = _live_factory(case, prepared_factory, binding_service)
    original = factory.build(case["event_id"], case["run_id"])
    assert RunService(case["db"], clock=lambda: CLOCK).claim_for_execution(
        original.run_id, worker_id=OWNER, lease_seconds=300
    )

    host = _BoundaryHost()
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(case["db"]),
        host,
        poll_interval_seconds=0.001,
        readiness_grace_seconds=0.1,
    )
    graph = build_agentic_research_graph(
        runtime,
        ResearchEvidenceService(case["db"]),
        expected_lease_owner=OWNER,
        require_context_selection=True,
        selection_clock=lambda: CLOCK,
        execution_clock=lambda: CLOCK,
    )

    with pytest.raises(_DispatchBoundaryReached):
        asyncio.run(graph.ainvoke(initial_research_state(original)))

    assert len(host.submit_calls) == 1
    submit = host.submit_calls[0]
    with case["db"].session() as session:
        prepared = read_prepared_execution_in_session(session, original.run_id)
        selected = prepared.execution.request
        reference = _selection_reference(prepared.execution)
        assert selected.model_dump(mode="json") != original.model_dump(mode="json")
        selected_budget = selected.execution_budget.model_dump(mode="json")
        original_budget = original.execution_budget.model_dump(mode="json")
        assert {
            key: value for key, value in selected_budget.items() if key != "max_tool_calls"
        } == {
            key: value for key, value in original_budget.items() if key != "max_tool_calls"
        }
        assert 0 < selected_budget["max_tool_calls"] <= original_budget["max_tool_calls"]
        assert selected.output_schema_ref == original.output_schema_ref
        with pytest.raises(ValueError, match="perpetual_research_request_identity_mismatch"):
            read_perpetual_research_materials_in_session(session, selected)
        materials = read_perpetual_research_materials_in_session(
            session,
            selected,
            selection_reference=reference,
            expected_lease_owner=OWNER,
        )
        assert materials["context"]["horizon"] == "4h"
        assert materials["review_originals"]["as_of"] == CLOCK.isoformat().replace(
            "+00:00", "Z"
        )

        changed_budget = selected.model_copy(
            update={
                "execution_budget": selected.execution_budget.model_copy(
                    update={"max_tool_calls": selected.execution_budget.max_tool_calls - 1}
                )
            }
        )
        with pytest.raises(ValueError, match="perpetual_research_request_identity_mismatch"):
            read_perpetual_research_materials_in_session(
                session,
                changed_budget,
                selection_reference=reference,
                expected_lease_owner=OWNER,
            )

    catalog = read_native_perpetual_binding(
        case["db"],
        original.run_id,
        submit.request_hash,
        submit.generation,
        now=lambda: CLOCK,
    )
    assert catalog["request_hash"] == submit.request_hash
    assert catalog["binding_ref"]["hash"]

