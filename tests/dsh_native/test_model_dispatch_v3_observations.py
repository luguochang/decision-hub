"""Real live-mode Selection3/Execution3 and callback transactions, no Provider.

The Factory, Graph pre-dispatch gate, Selection, execution builder and API
authentication stay real. Only the gap between preparation and writer is
injected. Each case copies one genuine, still-active module seed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections.abc import Callable, Generator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from apps.hub_api.main import create_app
from apps.hub_worker.research import ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import (
    DshSessionAccepted,
    DshSessionStatus,
    DshUpstreamIdentity,
)
from packages.contracts_py.decision_hub_contracts.generated.dsh_host_bridge import (
    DshModelDispatchBinding,
)
from packages.contracts_py.decision_hub_contracts.generated.research_context_execution import (
    ResearchExecutionRequestReceiptV3,
)
from packages.kernel.decision_hub_kernel.application import model_dispatch
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_binding import (
    PreparedResearchPolicyRefs,
)
from packages.kernel.decision_hub_kernel.application.research_context_selection import (
    ContextSelectionService,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    FrozenExecutionPreparation,
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
    prepare_graph_state_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence import original_preparation as preparation
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    RunRecord,
    RunRoleOutputRecord,
    utcnow,
)
from packages.orchestration.langgraph.graphs.agentic_research_graph import initial_research_state
from packages.runtime_adapters.dsh_runtime.profile import PROMPT_POLICY_HASH
from packages.runtime_adapters.dsh_runtime.research_runtime_binding import (
    prepare_runtime_turn_in_session,
    read_runtime_task_anchor,
)
from tests.evolution.test_research_worker import (
    _admitted_run,  # pyright: ignore[reportPrivateUsage]
)
from tests.research.test_full_method_v2_bundle import (
    _prepared_pack,  # pyright: ignore[reportPrivateUsage]
    forbid_external_connections,
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert forbid_external_connections is not None
OWNER = "live-v3-model-callback-test"
KEY = "isolated-callback-test-only"


@dataclass(frozen=True)
class Live:
    database: Database
    payload: DshModelDispatchBinding
    accepted: DshSessionAccepted


@pytest.fixture(scope="module")
def seed(tmp_path_factory: pytest.TempPathFactory) -> Generator[Live]:
    directory = tmp_path_factory.mktemp("actual-live-v3")
    database, event_id, run_id = _admitted_run(directory)
    try:
        root, manifest = _prepared_pack(directory)
        request = ResearchRequestFactory(
            database, pack_root=root, execution_mode="live",
            allowed_capabilities=("data.historical", "document.readback", "web.search"),
            prepared_policy_refs=PreparedResearchPolicyRefs(
                gate_policy_ref=manifest["gate_policy_ref"],
                rubric_policy_ref=manifest["evaluation_policy_ref"],
                role_profile_refs=tuple(manifest["role_profile_refs"]),
            ),
        ).build(event_id, run_id)
        assert request.execution_mode == "live"
        assert RunService(database).claim_for_execution(
            run_id, worker_id=OWNER, lease_seconds=900, strategy_version="research.v1",
        )
        with database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            state = initial_research_state(request)
            native, admitted = prepare_graph_state_in_session(session, state, OWNER)
            assert native and admitted == state
        selected = ContextSelectionService(database).select(
            run_id=run_id, graph_round=1, expected_lease_owner=OWNER,
        )
        upstream = DshUpstreamIdentity(
            source_commit="1" * 40, source_version="0.1.0", package_versions={},
            plugin_build_hash="2" * 64,
        )
        with database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            turn = prepare_runtime_turn_in_session(
                session, database, read_runtime_task_anchor(session, run_id),
                selected.request, upstream, runtime_id="dsh",
                runtime_version="dsh-web-0.1.0+222222222222",
                profile_ref="decision-research.web.v1",
                prompt_policy_hash=PROMPT_POLICY_HASH, selection_reference=selected.reference,
                expected_lease_owner=OWNER,
            )
        payload = DshModelDispatchBinding(
            schema_version="dsh-model-dispatch-binding.v1", run_id=run_id,
            dsh_session_id=turn.submit.deterministic_session_id,
            request_hash=turn.submit.request_hash, generation=turn.submit.generation,
            plugin_build_hash=upstream.plugin_build_hash, purpose="conversation",
            provider="controlled-declaration-only", model="no-provider-in-this-test",
            reasoning_effort="high", temperature=None, max_tokens=8192, stop_sequences_hash=None,
        )
        accepted = DshSessionAccepted(
            schema_version="dsh-session-accepted.v1", run_id=run_id,
            dsh_session_id=payload.dsh_session_id, generation=payload.generation,
            accepted_at=utcnow(),
        )
        links = DshSessionLinkService(database)
        links.accepted(accepted)
        with database.session() as session:
            prepared = read_prepared_execution_in_session(session, run_id)
            assert prepared.method.schema_version == "research-execution-method.v3"
            assert prepared.execution.schema_version == "dsh-execution-request.v3"
            assert (prepared.execution.current_selection_row.output_id
                    == selected.reference.output_id)
            assert session.query(RunRoleOutputRecord).filter_by(
                run_id=run_id, role="context_execution_link:1",
                schema_version="research-context-selection-execution-link.v2",
            ).one()
        yield Live(database, payload, accepted)
    finally:
        database.engine.dispose()


@pytest.fixture
def live(seed: Live, tmp_path: Path) -> Generator[Live]:
    path = seed.database.sqlite_path
    assert path is not None
    target = tmp_path / "actual-live-copy.sqlite3"
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as source:
        with sqlite3.connect(target) as destination:
            source.backup(destination)
    database = Database(f"sqlite+pysqlite:///{target.as_posix()}")
    try:
        yield Live(database, seed.payload, seed.accepted)
    finally:
        database.engine.dispose()


def _status(payload: DshModelDispatchBinding, sequence: int) -> DshSessionStatus:
    return DshSessionStatus(
        schema_version="dsh-session-status.v1", run_id=payload.run_id,
        dsh_session_id=payload.dsh_session_id, generation=payload.generation,
        state="running", last_seq=sequence, observed_at=utcnow(), error_code=None,
    )


def _originals(database: Database, run_id: str) -> list[tuple[str, str, str, str]]:
    with database.session() as session:
        return [(row.output_id, row.payload_json, row.payload_hash, row.schema_version)
                for row in session.query(RunRoleOutputRecord).filter(
                    RunRoleOutputRecord.run_id == run_id,
                    RunRoleOutputRecord.role != model_dispatch.ROLE,
                ).order_by(RunRoleOutputRecord.output_id)]


def _between(monkeypatch: pytest.MonkeyPatch, mutation: Callable[[], object]) -> None:
    original = model_dispatch.prepare_originals

    def prepare(database: Database, operation: Callable[[Session], Any],
                *, link_activity_observations: bool = False,
                link_tool_counter_observations: bool = False) -> Any:
        prepared = original(database, operation,
                            link_activity_observations=link_activity_observations,
                            link_tool_counter_observations=link_tool_counter_observations)
        mutation()
        return prepared

    monkeypatch.setattr(model_dispatch, "prepare_originals", prepare)


@pytest.mark.parametrize("callback", ["none", "first_status", "status", "accepted"])
def test_actual_live_v3_authenticated_callback_survives_observational_advance(
    live: Live, monkeypatch: pytest.MonkeyPatch, callback: str,
) -> None:
    before = _originals(live.database, live.payload.run_id)
    links = DshSessionLinkService(live.database)
    if callback == "first_status":
        _between(monkeypatch, lambda: links.observe(_status(live.payload, 1)))
    elif callback == "status":
        links.observe(_status(live.payload, 1))
        _between(monkeypatch, lambda: links.observe(_status(live.payload, 2)))
    elif callback == "accepted":
        _between(monkeypatch, lambda: links.accepted(live.accepted))
    client = TestClient(create_app(live.database, source_connectors=[], dsh_callback_secret=KEY))
    response = client.put(
        f"/v1/dsh/sessions/{live.payload.run_id}/model-binding",
        json=live.payload.model_dump(mode="json"), headers={"X-Decision-Hub-Bridge-Key": KEY},
    )
    assert response.status_code == 200, response.json()
    assert DshModelDispatchBinding.model_validate(response.json()) == live.payload
    recorded = model_dispatch.ModelDispatchService(live.database).get(live.payload.run_id)
    assert recorded == live.payload
    assert _originals(live.database, live.payload.run_id) == before


@pytest.mark.parametrize("fault", [
    "cancel", "generation", "state", "terminal", "deadline", "request_hash", "original",
])
def test_actual_live_v3_writer_still_rejects_authority_and_original_drift(
    live: Live, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    def mutate() -> None:
        with live.database.session() as session:
            link = session.get(DshSessionLinkRecord, live.payload.run_id)
            run = session.get(RunRecord, live.payload.run_id)
            assert link is not None and run is not None
            if fault == "cancel":
                run.status = "cancelled"
            elif fault == "generation":
                link.generation += 1
            elif fault == "state":
                link.state = "unknown"
            elif fault == "terminal":
                link.terminal_at = utcnow()
            elif fault == "deadline":
                link.deadline_at = utcnow() - timedelta(seconds=1)
            elif fault == "request_hash":
                link.request_hash = "a" * 64
            else:
                row = session.query(RunRoleOutputRecord).filter_by(
                    run_id=run.run_id, role="context_execution_link:1",
                ).one()
                row.payload_json += " "
                row.payload_hash = hashlib.sha256(row.payload_json.encode()).hexdigest()

    _between(monkeypatch, mutate)
    with pytest.raises((ValueError, PermissionError)):
        model_dispatch.ModelDispatchService(live.database).bind(live.payload)
    assert model_dispatch.ModelDispatchService(live.database).get(live.payload.run_id) is None


def test_rejected_native_api_logs_only_stable_reason_and_query_fingerprint(
    live: Live, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    query = "SELECT private_original WHERE secret = ?"

    def reject(_self: model_dispatch.ModelDispatchService,
               _payload: DshModelDispatchBinding) -> DshModelDispatchBinding:
        raise preparation.OriginalPreparationReadsetChanged(query)

    monkeypatch.setattr(model_dispatch.ModelDispatchService, "bind", reject)
    client = TestClient(create_app(live.database, source_connectors=[], dsh_callback_secret=KEY))
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        response = client.put(
            f"/v1/dsh/sessions/{live.payload.run_id}/model-binding",
            json=live.payload.model_dump(mode="json"), headers={"X-Decision-Hub-Bridge-Key": KEY},
        )
    assert response.status_code == 409
    assert response.json()["detail"] == "original_preparation_readset_changed"
    assert hashlib.sha256(query.encode()).hexdigest() in caplog.text
    assert "original_preparation_readset_changed" in caplog.text
    assert live.payload.run_id in caplog.text and live.payload.request_hash in caplog.text
    assert query not in caplog.text and KEY not in caplog.text


def test_actual_live_v3_child_bind_allows_another_real_reserved_tool(
    live: Live, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = model_dispatch.ModelDispatchService(live.database)
    assert service.bind(live.payload) == live.payload
    child = live.payload.model_copy(update={
        "execution_session_id": "controlled-live-child",
        "parent_session_id": live.payload.dsh_session_id,
    })
    calls: list[str] = []

    def reserve() -> None:
        _reserve(live, "parallel-real-reservation")
        calls.append("reserved")

    _between(monkeypatch, reserve)
    assert service.bind(child) == child
    assert calls == ["reserved"]
    assert service.get(child.run_id, child.execution_session_id) == child


def _reserve(live: Live, request_id: str) -> None:
    # Real reservation writer and complete native preparation. Query intent
    # is controlled; this does not claim Gateway/Provider execution.
    def admit(_session: Session, prepared: FrozenExecutionPreparation) -> str:
        assert isinstance(prepared.execution, ResearchExecutionRequestReceiptV3)
        assert prepared.execution.request.execution_mode == "live"
        assert "web.search" in prepared.execution.request.allowed_capabilities
        body = {"selection": prepared.execution.current_selection_row.model_dump(mode="json"),
                "capability": "web.search", "request": request_id}
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()

    reserved = ResearchObservabilityService(live.database).reserve_tool_call(
        run_id=live.payload.run_id, request_id=request_id,
        research_session_id=live.payload.dsh_session_id, generation=live.payload.generation,
        capability_id="web.search", query_hash="0" * 64, admission_check=admit,
    )
    assert reserved.outcome == "reserved" and reserved.tool_calls_started == 1


@pytest.mark.parametrize("fault", ["decrement", "overrun", "ceiling"])
def test_actual_child_binding_requires_monotonic_counter_and_original_budget(
    live: Live, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    service = model_dispatch.ModelDispatchService(live.database)
    service.bind(live.payload)
    _reserve(live, "original-reservation")
    child = live.payload.model_copy(update={
        "execution_session_id": "budget-child", "parent_session_id": live.payload.dsh_session_id,
    })

    def mutate() -> None:
        with live.database.session() as session:
            link = session.get(DshSessionLinkRecord, child.run_id)
            assert link is not None and link.max_tool_calls is not None
            if fault == "decrement":
                link.tool_calls_started = 0
            elif fault == "overrun":
                link.tool_calls_started = link.max_tool_calls + 1
            else:
                link.max_tool_calls += 1

    _between(monkeypatch, mutate)
    with pytest.raises(ValueError, match="dsh_model_dispatch_tool_budget_changed"):
        service.bind(child)
    assert service.get(child.run_id, child.execution_session_id) is None


@pytest.mark.parametrize("existing", [False, True])
def test_child_binding_final_counter_fence_rolls_back_new_and_replay_paths(
    live: Live, monkeypatch: pytest.MonkeyPatch, existing: bool,
) -> None:
    service = model_dispatch.ModelDispatchService(live.database)
    service.bind(live.payload)
    _reserve(live, "final-fence-original-reservation")
    child = live.payload.model_copy(update={
        "execution_session_id": "final-fence-child",
        "parent_session_id": live.payload.dsh_session_id,
    })
    if existing:
        service.bind(child)
    before = _originals(live.database, child.run_id)
    verify = preparation.PreparedOriginals.verify

    def mutate(self: Any, session: Session) -> None:
        verify(self, session)
        session.execute(update(DshSessionLinkRecord).where(
            DshSessionLinkRecord.run_id == child.run_id,
        ).values(tool_calls_started=0))

    monkeypatch.setattr(preparation.PreparedOriginals, "verify", mutate)
    with pytest.raises(ValueError, match="dsh_model_dispatch_tool_budget_changed"):
        service.bind(child)
    assert service.get(child.run_id, child.execution_session_id) == (child if existing else None)
    assert _originals(live.database, child.run_id) == before
    with live.database.session() as session:
        link = session.get(DshSessionLinkRecord, child.run_id)
        assert link is not None and link.tool_calls_started == 1


def test_publication_default_readset_still_binds_original_tool_counter(live: Live) -> None:
    def read(session: Session) -> None:
        assert session.get(DshSessionLinkRecord, live.payload.run_id) is not None

    saved = preparation.prepare_originals(live.database, read)
    _reserve(live, "publication-default-counter-change")
    with (
        pytest.raises(preparation.OriginalPreparationReadsetChanged),
        live.database.session() as session,
    ):
        preparation.begin_immediate(session)
        saved.verify(session)
