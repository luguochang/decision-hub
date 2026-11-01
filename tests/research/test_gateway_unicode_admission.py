"""Canonical query identity through real offline live-mode admission and replay."""

import socket
from datetime import timedelta

import pytest
import yaml

from packages.contracts_py.decision_hub_contracts import (
    ResearchCapabilityInvocation,
    ResearchCapabilityResult,
)
from packages.kernel.decision_hub_kernel.application import research_graph_binding
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_binding import (
    PreparedResearchPolicyRefs,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityGatewayService,
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.persistence import original_preparation
from packages.kernel.decision_hub_kernel.persistence.db import (
    DshSessionLinkRecord,
    ResearchToolCallReservationRecord,
    RunRoleOutputRecord,
)
from packages.workbench_adapters.durable_research_gateway import DurableResearchCapabilityGateway
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import (
    AT,
    factory_for,
    prepare_execution,
)
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case
from tests.research.test_durable_research_gateway import _manifest


@pytest.fixture(autouse=True)
def isolated_offline_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "isolated-data"))
    original_connect = socket.socket.connect

    def no_external_connection(sock, address):
        # Windows asyncio uses a loopback socketpair for its own wake-up pipe.
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return original_connect(sock, address)
        raise AssertionError("external connection forbidden in Gateway admission test")

    monkeypatch.setattr(socket.socket, "connect", no_external_connection)


class _ControlledLiveAdapter:
    capability_id = "web.search"
    supported_modes = frozenset({"live"})

    def __init__(self, clock):
        self.clock, self.calls = clock, []

    async def execute(self, query):
        self.calls.append(query)
        return ResearchCapabilityResult(
            schema_version="research-capability-result.v1", request_id=query.request_id,
            capability_id=query.capability_id, provider="controlled-offline", evidence_candidates=[],
            facts=[], cost_usd=0.01, completed_at=self.clock(),
        )


class _ObservedAdmission(ResearchObservabilityService):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.identities = []

    def reserve_tool_call(self, **kwargs):
        self.identities.append((kwargs["admission_originals"].value.query_hash, kwargs["query_hash"]))
        return super().reserve_tool_call(**kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("query_text", ["查询公开市场政策与跨资产证据", "Public market policy and cross asset evidence"], ids=["unicode", "ascii"])
async def test_live_query_preserves_issued_identity_and_exact_replay(binding_case, tmp_path, monkeypatch, query_text):
    # Existing Factory and PreparedExecution authenticate a real temporary Task.
    # The adapter is local and cannot contact a provider or network.
    case = binding_case
    factory, service = factory_for(case, tmp_path)
    manifest = yaml.safe_load((factory.pack_root / "pack.yaml").read_text("utf-8"))
    factory = type(factory)(
        case["db"], pack_root=factory.pack_root, execution_mode="live",
        allowed_capabilities=factory.allowed_capabilities,
        prepared_policy_refs=PreparedResearchPolicyRefs(
            gate_policy_ref=manifest["gate_policy_ref"], rubric_policy_ref=manifest["evaluation_policy_ref"],
            role_profile_refs=tuple(manifest["role_profile_refs"]),
        ), perpetual_binding_service=service, clock=lambda: AT,
    )
    request = factory.build(case["event_id"], case["run_id"])
    prepared = prepare_execution(case, request)
    clock = lambda: AT + timedelta(seconds=1)
    monkeypatch.setattr(original_preparation, "utcnow", clock)
    monkeypatch.setattr(research_graph_binding, "utcnow", clock)
    requirement = next(row for row in request.evidence_requirements
                       if "web.search" in [*row.preferred_capabilities, *row.allowed_fallbacks])
    adapter = _ControlledLiveAdapter(clock)
    capability = _manifest().model_copy(update={"capability_id": "web.search", "allowed_domains": ["example.test"]})
    observer = _ObservedAdmission(case["db"], clock=clock)
    gateway = DurableResearchCapabilityGateway(
        ResearchCapabilityGatewayService([capability], [adapter], enabled_capabilities=["web.search"], clock=clock),
        DshSessionLinkService(case["db"], clock=clock), ResearchEvidenceService(case["db"]), observer,
        requirements={row.requirement_id: row for row in request.evidence_requirements}, clock=clock,
    )
    intent = ResearchCapabilityInvocation(
        schema_version="research-capability-invocation.v1", request_id="controlled-unicode-admission",
        capability_id="web.search", requirement_id=requirement.requirement_id, query=query_text,
        allowed_domains=["example.test"], target_url=None, symbols=[], fields=[], max_results=2,
        max_cost_usd=0.1, research_session_id=prepared.link.dsh_session_id,
    )
    first = await gateway.invoke_live(intent)
    replay = await gateway.invoke_live(intent)
    assert first == replay and len(adapter.calls) == 1
    assert adapter.calls[0].query == query_text
    assert observer.identities and all(issued == reserved for issued, reserved in observer.identities)
    with case["db"].session() as session:
        link = session.get(DshSessionLinkRecord, request.run_id)
        rows = session.query(ResearchToolCallReservationRecord).filter_by(run_id=request.run_id).all()
        timings = session.query(RunRoleOutputRecord).filter(
            RunRoleOutputRecord.run_id == request.run_id,
            RunRoleOutputRecord.role.startswith("research_tool_time:"),
        ).count()
        assert link.tool_calls_started == 1 and len(rows) == 1
        assert rows[0].status == "completed" and timings == 1
