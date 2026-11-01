"""Actual normal Worker dispatch for original tasks; offline controlled Host only.

The Method2 seed is produced by the actual Graph projection and transactional
preparation helper, then stopped before any Host submit. Compatibility is tested
through normal composition.tick(), not the private factory-selection getter.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from apps.hub_worker.composition import ResearchWorker, build_research_worker
from apps.hub_worker.research import ResearchRequestFactory, ResearchWorkerReport
from packages.contracts_py.decision_hub_contracts import (
    DshHostReadiness,
    DshSessionResult,
    DshSessionStatus,
    DshSessionSubmit,
    ResearchSessionRequest,
    ResearchSessionResult,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import (
    DshSessionLinkService,
)
from packages.kernel.decision_hub_kernel.application.research_binding import BUNDLE_ROLES
from packages.kernel.decision_hub_kernel.application.research_context_selection import (
    ContextSelectionReference,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.application.research_task import (
    ROLE as TASK_ROLE,
    ResearchTaskService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRecord
from packages.kernel.decision_hub_kernel.ports.research import ResearchTraceSink
from packages.kernel.decision_hub_kernel.ports.research_observation import (
    ObservedResearchResult,
)
from packages.orchestration.langgraph.executor import LangGraphResearchExecutor
from packages.runtime_adapters.dsh_runtime import research_runtime_binding as binding
from packages.runtime_adapters.dsh_runtime.profile import PROMPT_POLICY_HASH
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_dsh_web_runtime import FakeHostClient
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.evolution.test_research_worker import (
    _admitted_run,  # pyright: ignore[reportPrivateUsage]
)
from tests.orchestration.test_graph_observed_result_v2 import inputs
from tests.research.test_full_method_v2_bundle import forbid_external_connections

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert inputs is not None and forbid_external_connections is not None

LEGACY_PACK = Path(__file__).resolve().parents[2] / "packs" / "crypto_macro"
OWNER = "legacy-composition-control"
IMMUTABLE_EXECUTION_ROLES = {
    "research_execution_method",
    "dsh_execution_request:1",
    "dsh_prompt_integrity:1",
}
MATERIAL_TABLES = (
    "research_evidence",
    "research_facts",
    "research_results",
    "artifacts",
    "outbox",
    "snapshots",
    "research_tool_call_reservations",
)


def _full_sql(database: Database) -> dict[str, list[dict[str, Any]]]:
    """Capture every physical table and every original column, not a subset."""
    path = database.engine.url.database
    assert path is not None and path != ":memory:"
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            name: [
                dict(row)
                for row in connection.execute(
                    'SELECT * FROM "' + name.replace('"', '""') + '" ORDER BY rowid'
                )
            ]
            for name in names
        }


def _role_rows(
    state: dict[str, list[dict[str, Any]]], run_id: str
) -> dict[str, dict[str, Any]]:
    rows = [row for row in state["run_role_outputs"] if row["run_id"] == run_id]
    assert len({row["role"] for row in rows}) == len(rows)
    return {row["role"]: row for row in rows}


def _link_identity(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row[key]
        for key in (
            "run_id",
            "dsh_session_id",
            "request_hash",
            "generation",
            "upstream_identity_json",
            "plugin_build_hash",
            "deadline_at",
            "max_tool_calls",
            "model_step_timeout_ms",
            "created_at",
        )
    }


def _assert_no_selected_assets(state: dict[str, list[dict[str, Any]]], run_id: str) -> None:
    rows = _role_rows(state, run_id)
    assert not any(role.startswith("context_select.") for role in rows)
    assert not any(
        role.startswith("context_execution_link:") and role.split(":", 1)[1].isdigit()
        for role in rows
    )
    assert not any(
        row["schema_version"]
        in {"research-execution-method.v3", "dsh-execution-request.v3"}
        for row in rows.values()
    )


class CountingHost(FakeHostClient):
    def __init__(self) -> None:
        super().__init__()
        self.readiness_calls = 0
        self.status_calls: list[DshSessionSubmit] = []
        self.result_calls: list[DshSessionSubmit] = []
        self.closed = False

    async def readiness(self) -> DshHostReadiness:
        self.readiness_calls += 1
        return await super().readiness()

    async def status(self, payload: DshSessionSubmit) -> DshSessionStatus:
        self.status_calls.append(payload)
        return await super().status(payload)

    async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
        self.result_calls.append(payload)
        return await super().result(payload)

    async def close(self) -> None:
        self.closed = True
        await super().close()


class OriginalEntryRuntime(DshWebResearchRuntime):
    """Run original entries unchanged; expose a mistaken selected dispatch."""

    def __init__(self, database: Database, host: CountingHost) -> None:
        super().__init__(
            DshSessionLinkService(database), host, poll_interval_seconds=0.001
        )
        self.original_requests: list[ResearchSessionRequest] = []
        self.observed_requests: list[ResearchSessionRequest] = []
        self.selected_requests: list[ResearchSessionRequest] = []

    async def execute(
        self,
        request: ResearchSessionRequest,
        trace_sink: ResearchTraceSink | None = None,
    ) -> ResearchSessionResult:
        self.original_requests.append(request)
        return await super().execute(request, trace_sink=trace_sink)

    async def execute_observed(
        self,
        request: ResearchSessionRequest,
        trace_sink: ResearchTraceSink | None = None,
    ) -> ObservedResearchResult:
        self.observed_requests.append(request)
        return await super().execute_observed(request, trace_sink=trace_sink)

    async def execute_selected_observed(
        self,
        request: ResearchSessionRequest,
        selection_reference: ContextSelectionReference,
        *,
        expected_lease_owner: str,
        trace_sink: ResearchTraceSink | None = None,
    ) -> ObservedResearchResult:
        del selection_reference, expected_lease_owner, trace_sink
        self.selected_requests.append(request)
        raise AssertionError("an original Method/Task was dispatched to the selected entry")


class PreparedBeforeHost(Exception):
    """Explicit fixture stop after actual Method2 commit, before Host submit."""


class PrepareOnlyRuntime:
    runtime_id = DshWebResearchRuntime.runtime_id
    runtime_version = "dsh-web-unverified"
    profile_ref = DshWebResearchRuntime.profile_ref

    def __init__(self, database: Database, host: CountingHost) -> None:
        self.database = database
        self.host = host
        self.request: ResearchSessionRequest | None = None
        self.turn: binding.RuntimePreparedTurn | None = None

    async def execute(
        self,
        request: ResearchSessionRequest,
        trace_sink: ResearchTraceSink | None = None,
    ) -> ResearchSessionResult:
        del request, trace_sink
        raise AssertionError("prepared original Graph must use execute_observed")

    async def execute_observed(
        self,
        request: ResearchSessionRequest,
        trace_sink: ResearchTraceSink | None = None,
    ) -> ObservedResearchResult:
        del trace_sink
        ready = await self.host.readiness()
        upstream = ready.upstream_identity
        assert ready.ready and upstream is not None
        self.runtime_version = (
            f"dsh-web-{upstream.source_version}+{upstream.plugin_build_hash[:12]}"
        )
        with self.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            anchor = binding.read_runtime_task_anchor(session, request.run_id)
            self.turn = binding.prepare_runtime_turn_in_session(
                session,
                self.database,
                anchor,
                request,
                upstream,
                runtime_id=self.runtime_id,
                runtime_version=self.runtime_version,
                profile_ref=self.profile_ref,
                prompt_policy_hash=PROMPT_POLICY_HASH,
            )
        self.request = request
        assert self.turn.prepared.method.schema_version == "research-execution-method.v2"
        assert not self.host.submit_calls
        raise PreparedBeforeHost("the genuine original preparation committed; Host not submitted")

    async def close(self) -> None:
        await self.host.close()


def _composition(
    database: Database,
    runtime: OriginalEntryRuntime,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capabilities: tuple[str, ...],
) -> ResearchWorker:
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "normal-composition"))
    monkeypatch.setenv("DECISION_HUB_INSTANCE_ID", OWNER)
    monkeypatch.setenv("DECISION_HUB_RESEARCH_EXECUTION_MODE", "replay")
    monkeypatch.setenv("DECISION_HUB_RESEARCH_MIN_PRIORITY", "")
    return build_research_worker(
        database,
        runtime=runtime,
        allowed_capabilities=capabilities,
        lease_seconds=240,
    )


async def _close(worker: ResearchWorker, runtime: OriginalEntryRuntime) -> None:
    await worker.close()
    runtime.cancellation_projector._executor.shutdown(  # pyright: ignore[reportPrivateUsage]
        wait=True, cancel_futures=True
    )


def _observation(
    tmp_path: Path,
    *,
    kind: str,
    before: dict[str, list[dict[str, Any]]],
    after: dict[str, list[dict[str, Any]]],
    host: CountingHost,
    runtime: OriginalEntryRuntime,
    report: ResearchWorkerReport | None,
    error: BaseException | None,
    extra: dict[str, Any] | None = None,
) -> None:
    body = {
        "kind": kind,
        "entry": "normal build_research_worker.tick",
        "before": before,
        "after": after,
        "physical_table_count": len(before),
        "changed_tables": [name for name in before if before[name] != after[name]],
        "report": None if report is None else report.model_dump(mode="json"),
        "exception": None if error is None else {
            "type": type(error).__name__,
            "message": str(error),
            "code": getattr(error, "error_code", None),
            "cause_code": getattr(error, "cause_code", None),
        },
        "host_readiness_calls": host.readiness_calls,
        "host_submits": [item.model_dump(mode="json") for item in host.submit_calls],
        "host_statuses": [item.model_dump(mode="json") for item in host.status_calls],
        "host_results": [item.model_dump(mode="json") for item in host.result_calls],
        "host_cancelled": host.cancelled,
        "host_closed": host.closed,
        "original_requests": [item.model_dump(mode="json") for item in runtime.original_requests],
        "observed_requests": [item.model_dump(mode="json") for item in runtime.observed_requests],
        "selected_requests": [item.model_dump(mode="json") for item in runtime.selected_requests],
        "cleanup": "normal worker.close and test-owned executor joined before SQL observation",
        "extra": extra or {},
    }
    (tmp_path / "legacy-composition-observation.json").write_text(
        json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_original_method2_recovered_by_normal_composition_without_upgrade(
    inputs: PreparedInputs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = CountingHost()
    runtime = OriginalEntryRuntime(inputs.database, host)
    worker = _composition(
        inputs.database, runtime, tmp_path, monkeypatch,
        tuple(inputs.request.allowed_capabilities),
    )
    seed_host = CountingHost()
    seed = PrepareOnlyRuntime(inputs.database, seed_host)
    with inputs.database.session() as session:
        run = session.get(RunRecord, inputs.request.run_id, populate_existing=True)
        assert run is not None
        run.status = "running"
        run.lease_owner = worker.worker.worker_id
        run.lease_expires_at = datetime.now(UTC) + timedelta(minutes=4)
    report = None
    error = None
    before: dict[str, list[dict[str, Any]]] = {}
    after: dict[str, list[dict[str, Any]]] = {}
    original_seed: dict[str, list[dict[str, Any]]] = {}
    try:
        with pytest.raises(PreparedBeforeHost):
            await LangGraphResearchExecutor(
                inputs.database, seed, expected_lease_owner=worker.worker.worker_id
            ).execute(inputs.request)
        assert seed.request is not None and seed.turn is not None
        original_seed = _full_sql(inputs.database)
        raw_roles = _role_rows(original_seed, inputs.request.run_id)
        assert raw_roles["research_execution_method"]["schema_version"] == (
            "research-execution-method.v2"
        )
        assert not seed_host.submit_calls
        path = inputs.database.engine.url.database
        assert path is not None
        with sqlite3.connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            affected = connection.execute(
                "UPDATE runs SET lease_expires_at=? WHERE run_id=? AND lease_owner=?",
                (
                    (datetime.now(UTC) - timedelta(seconds=1)).replace(tzinfo=None).isoformat(" "),
                    inputs.request.run_id,
                    worker.worker.worker_id,
                ),
            ).rowcount
            assert affected == 1
        before = _full_sql(inputs.database)
        report = await asyncio.wait_for(worker.tick(), timeout=120)
    except BaseException as exc:
        error = exc
        raise
    finally:
        await seed.close()
        await _close(worker, runtime)
        after = _full_sql(inputs.database)
        _observation(
            tmp_path,
            kind="original Method2, expired lease, normal composition recovery",
            before=before or original_seed,
            after=after,
            host=host,
            runtime=runtime,
            report=report,
            error=error,
            extra={
                "seed_raw_rows": original_seed,
                "seed_request": None if seed.request is None else seed.request.model_dump(mode="json"),
                "seed_host_submits": len(seed_host.submit_calls),
                "seed_has_checkpoint": False,
            },
        )
    assert report is not None and report.run_id == inputs.request.run_id
    assert report.status == "reject" and report.artifact_id is not None
    assert runtime.original_requests == [] and runtime.selected_requests == []
    assert len(runtime.observed_requests) == 1
    assert runtime.observed_requests[0] == seed.request
    assert len(host.submit_calls) == 1 and len(host.result_calls) == 1
    original = _role_rows(before, inputs.request.run_id)
    final = _role_rows(after, inputs.request.run_id)
    for role in (*BUNDLE_ROLES, *sorted(IMMUTABLE_EXECUTION_ROLES)):
        assert final[role] == original[role], role
    assert after["dsh_session_prompts"] == before["dsh_session_prompts"]
    assert _link_identity(after["dsh_session_links"][0]) == _link_identity(
        before["dsh_session_links"][0]
    )
    assert host.submit_calls[0].deadline_at == inputs.request.deadline_at
    _assert_no_selected_assets(after, inputs.request.run_id)


def _legacy_task(database: Database, event_id: str, run_id: str) -> ResearchSessionRequest:
    request = ResearchRequestFactory(
        database,
        pack_root=LEGACY_PACK,
        allowed_capabilities=("replay.research",),
        execution_mode="replay",
    ).build(event_id, run_id)
    # The original legacy Factory returns a request; the real pre-execution Task
    # producer freezes it. Do not claim Factory alone already persisted Task1.
    return ResearchTaskService(database).freeze(request)


@pytest.mark.asyncio
async def test_original_legacy_task_without_method_uses_normal_composition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, event_id, run_id = _admitted_run(tmp_path)
    host = CountingHost()
    runtime = OriginalEntryRuntime(database, host)
    worker = _composition(database, runtime, tmp_path, monkeypatch, ("replay.research",))
    report = None
    error = None
    before: dict[str, list[dict[str, Any]]] = {}
    after: dict[str, list[dict[str, Any]]] = {}
    request = None
    try:
        request = _legacy_task(database, event_id, run_id)
        before = _full_sql(database)
        original = _role_rows(before, run_id)
        assert original[TASK_ROLE]["schema_version"] == "research-task-request.v1"
        assert original["research_request_pack"]["schema_version"] == "research-request-pack.v1"
        assert "research_execution_method" not in original
        report = await asyncio.wait_for(worker.tick(), timeout=120)
    except BaseException as exc:
        error = exc
        raise
    finally:
        await _close(worker, runtime)
        after = _full_sql(database)
        _observation(
            tmp_path,
            kind="real original legacy Task, no Method, normal composition",
            before=before or after,
            after=after,
            host=host,
            runtime=runtime,
            report=report,
            error=error,
            extra={"original_full_task": None if request is None else request.model_dump(mode="json")},
        )
        database.engine.dispose()
    assert request is not None and report is not None and report.run_id == run_id
    assert report.status == "reject" and report.artifact_id is not None
    assert len(runtime.original_requests) == 1
    assert runtime.observed_requests == [] and runtime.selected_requests == []
    assert len(host.submit_calls) == 1 and len(host.result_calls) == 1
    final = _role_rows(after, run_id)
    for role in (TASK_ROLE, "research_request_pack"):
        assert final[role] == original[role], role
    assert final["research_execution_method"]["schema_version"] == "research-execution-method.v1"
    assert host.submit_calls[0].deadline_at == request.deadline_at
    assert datetime.fromisoformat(after["dsh_session_links"][0]["deadline_at"]).replace(
        tzinfo=UTC
    ) == request.deadline_at.astimezone(UTC)
    _assert_no_selected_assets(after, run_id)


@pytest.mark.asyncio
async def test_partial_original_link_and_pack_cannot_recreate_missing_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, event_id, run_id = _admitted_run(tmp_path)
    host = CountingHost()
    runtime = OriginalEntryRuntime(database, host)
    worker = _composition(database, runtime, tmp_path, monkeypatch, ("replay.research",))
    report = None
    error = None
    before: dict[str, list[dict[str, Any]]] = {}
    after: dict[str, list[dict[str, Any]]] = {}
    original_full: dict[str, list[dict[str, Any]]] = {}
    try:
        request = _legacy_task(database, event_id, run_id)
        digest = research_request_hash(request)
        session_id, request_id = DshSessionLinkService.deterministic_ids(run_id, digest)
        submit = DshSessionSubmit(
            schema_version="dsh-session-submit.v1",
            run_id=run_id,
            request_hash=digest,
            deterministic_session_id=session_id,
            deterministic_request_id=request_id,
            workspace_ref="decision-hub://workspace/default",
            prompt_ref=f"hub://runs/{run_id}/prompts/1",
            agent_preset="decision-research",
            permission_ref="decision-hub://permissions/research-only",
            deadline_at=request.deadline_at,
            max_tool_calls=request.execution_budget.max_tool_calls,
            model_step_timeout_ms=request.execution_budget.per_model_step_timeout_seconds * 1000,
            generation=1,
        )
        # A real public old reservation, with no Host call or fabricated Method.
        seed_host = CountingHost()
        try:
            readiness = await seed_host.readiness()
            assert readiness.upstream_identity is not None
            DshSessionLinkService(database).reserve(submit, readiness.upstream_identity)
        finally:
            await seed_host.close()
        original_full = _full_sql(database)
        path = database.engine.url.database
        assert path is not None
        with sqlite3.connect(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            affected = connection.execute(
                "DELETE FROM run_role_outputs WHERE run_id=? AND role=?",
                (run_id, TASK_ROLE),
            ).rowcount
            assert affected == 1
        before = _full_sql(database)
        report = await asyncio.wait_for(worker.tick(), timeout=60)
    except BaseException as exc:
        error = exc
        raise
    finally:
        await _close(worker, runtime)
        after = _full_sql(database)
        _observation(
            tmp_path,
            kind="original old reservation and Pack, Task removed by second connection",
            before=before or original_full or after,
            after=after,
            host=host,
            runtime=runtime,
            report=report,
            error=error,
            extra={"original_full_rows_before_task_delete": original_full},
        )
        database.engine.dispose()
    assert report is not None and report.run_id == run_id and report.status == "failed"
    assert report.artifact_id is None
    assert report.error_code == "research_task_request_missing_before_execution"
    assert host.readiness_calls == 0 and not host.submit_calls
    assert not host.status_calls and not host.result_calls and not host.cancelled
    assert runtime.original_requests == [] and runtime.observed_requests == []
    assert runtime.selected_requests == []
    final = _role_rows(after, run_id)
    original = _role_rows(before, run_id)
    assert TASK_ROLE not in final and "research_execution_method" not in final
    assert final["research_request_pack"] == original["research_request_pack"]
    assert after["dsh_session_links"] == before["dsh_session_links"]
    assert after["dsh_session_prompts"] == before["dsh_session_prompts"] == []
    for table in MATERIAL_TABLES:
        assert after[table] == before[table], table
    _assert_no_selected_assets(after, run_id)
