# pyright: reportPrivateUsage=false
"""Authenticated activity ingestion and maintenance, without model submission."""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from apps.hub_api.main import create_app
from apps.hub_worker import main
from apps.hub_worker.composition import ResearchWorker, build_research_worker
from apps.hub_worker.dsh_activity import DshActivityCollector, build_dsh_activity_collector
from packages.contracts_py.decision_hub_contracts import (
    ResearchSessionRequest,
    ResearchSessionResult,
)
from packages.kernel.decision_hub_kernel.application.dsh_activity import (
    DshHostActivityRequest,
    DshHostActivityScope,
    activity_binding,
    activity_link_fingerprint,
    host_activity_inactive_in_session,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    ReviewScheduleRecord,
    RunRecord,
    RunRoleOutputRecord,
    ServiceHeartbeatRecord,
    ServiceRuntimeConfigRecord,
    StudyRecord,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.pilot_runtime import PilotSettings
from packages.runtime_adapters.dsh_runtime.activity_transport import DshActivityTransportConfig
from packages.runtime_adapters.dsh_runtime.web_host_client import DshWebHostClient, DshWebHostConfig
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from packages.source_adapters.registry import SourceRegistry


@dataclass
class ActivityCase:
    database: Database
    config: DshActivityTransportConfig
    scope: DshHostActivityScope
    run_id: str
    calls: list[str] = field(default_factory=list)
    fault: str | None = None
    before_get: Callable[[], None] | None = None
    captured_body: bytes | None = None

    def snapshot(self) -> dict[str, object]:
        with self.database.session() as session:
            run = session.get(RunRecord, self.run_id)
            link = session.get(DshSessionLinkRecord, self.run_id)
            assert run is not None and link is not None
            binding = activity_binding(run, link).model_dump(mode="json")
        now = datetime.now(UTC)
        return {
            "schema_version": "dsh-host-activity-snapshot.v1",
            "scope": self.scope.model_dump(mode="json"),
            "observed_at": now.isoformat(), "expires_at": (now + timedelta(seconds=30)).isoformat(),
            "census_complete": True,
            "records": [{**binding, "state": "current_scope_inactive", "presence": "absent",
                         "running": False, "queue_count": 0, "active_job_count": 0,
                         "descendant_ids": [], "fence_id": "lineage-fence-verified",
                         "reason_code": "current_scope_fenced", "terminal_state": "unknown"}],
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Decision-Hub-Host-Key"] == "host-activity-key"
        assert request.url.path == "/decision-hub/v1/current-activity"
        assert isinstance(self.database.engine.pool, QueuePool)
        assert self.database.engine.pool.checkedout() == 0  # No Session crosses HTTP.
        self.calls.append(request.method)
        if self.fault == "transport":
            return httpx.Response(503)
        if request.method == "GET":
            if self.before_get is not None:
                hook, self.before_get = self.before_get, None
                hook()
            if self.fault == "epoch" and len(self.calls) > 1:
                self.scope = self.scope.model_copy(update={"host_instance_id": uuid4()})
            return httpx.Response(200, json={
                "schema_version": "dsh-host-activity-scope.v1",
                "scope": self.scope.model_dump(mode="json"),
                "observed_at": datetime.now(UTC).isoformat(),
            })
        assert request.method == "POST"
        batch = DshHostActivityRequest.model_validate_json(request.content)
        value = self.snapshot()
        records = value["records"]
        assert isinstance(records, list)
        assert len(batch.records) == len(records) == 1
        if self.fault == "binding":
            records[0]["link_fingerprint"] = "f" * 64
        if self.fault == "fence":
            records[0]["fence_id"] = None
        if self.fault == "ttl":
            value["expires_at"] = (datetime.now(UTC) + timedelta(seconds=31)).isoformat()
        self.captured_body = json.dumps(value, indent=2, ensure_ascii=False).encode()
        return httpx.Response(200, content=self.captured_body)


@pytest.fixture
def case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ActivityCase:
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    url = f"sqlite+pysqlite:///{tmp_path / 'activity.sqlite3'}"
    database = Database(url)
    database.engine.dispose()
    database.engine = create_engine(
        url, poolclass=QueuePool, pool_size=1, max_overflow=0, pool_timeout=0.1,
        connect_args={"check_same_thread": False},
    )
    database.factory = sessionmaker(database.engine, expire_on_commit=False)
    database.create_all()
    now = datetime.now(UTC)
    home, workspace = tmp_path / "home", tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    host = DshWebHostConfig(base_url="http://host.test", bridge_key="host-activity-key")
    config = DshActivityTransportConfig(host, "http://hub.test", home, workspace, "callback-key")
    scope = DshHostActivityScope(
        host_instance_id=uuid4(), process_id=1234, started_at=now - timedelta(minutes=1),
        hub_base_url=config.hub_base_url, workspace_id="official-workspace-random-id",
        workspace_cwd_hash=hashlib.sha256(
            str(workspace.resolve()).replace("\\", "/").lower().encode(),
        ).hexdigest(),
    )
    run_id, study_id = "run_activity_source", "study_activity"
    session_id, _ = DshSessionLinkService.deterministic_ids(run_id, "a" * 64, 1)
    with database.session() as session:
        session.add(StudyRecord(
            study_id=study_id, scope_key="a" * 64, title="Current activity control",
            market="crypto", instrument="BTC-USDT-SWAP", horizon_scope="30m/24h/72h",
            question_key="activity", identity_basis="explicit", status="active",
            created_at=now - timedelta(hours=2), updated_at=now,
        ))
        session.add(RunRecord(
            run_id=run_id, event_id="event_activity", study_id=study_id, status="failed",
            strategy_version="research.v1", runtime_version="dsh-web-original",
            created_at=now - timedelta(hours=2), updated_at=now - timedelta(hours=1),
            finished_at=now - timedelta(hours=1), lease_owner=None, lease_expires_at=None,
        ))
        session.add(DshSessionLinkRecord(
            run_id=run_id, dsh_session_id=session_id, request_hash="a" * 64, state="unknown",
            generation=1, upstream_identity_json="{}", plugin_build_hash="b" * 64,
            created_at=now - timedelta(hours=2), updated_at=now - timedelta(hours=1),
        ))
        session.add(ReviewScheduleRecord(
            review_plan_id="review_activity", parent_run_id=run_id, horizon="30m",
            trigger_type="next_review", trigger_ref="activity-control", trigger_hash="c" * 64,
            trigger_at=now - timedelta(seconds=1), status="planned", child_run_id=None,
            created_at=now - timedelta(hours=1), updated_at=now,
        ))
    return ActivityCase(database, config, scope, run_id)


def _unchanged(case: ActivityCase) -> tuple[str, str, int]:
    with case.database.session() as session:
        link = session.get(DshSessionLinkRecord, case.run_id)
        run = session.get(RunRecord, case.run_id)
        assert link is not None and run is not None
        return (
            activity_link_fingerprint(link), run.status,
            session.query(ServiceHeartbeatRecord).count(),
        )


def _inactive(case: ActivityCase) -> bool:
    with case.database.session() as session:
        run = session.get(RunRecord, case.run_id)
        link = session.get(DshSessionLinkRecord, case.run_id)
        assert run is not None and link is not None
        return host_activity_inactive_in_session(session, run=run, link=link, now=datetime.now(UTC))


@pytest.mark.asyncio
async def test_actual_collector_to_authenticated_api_keeps_raw_and_old_link(
    case: ActivityCase,
) -> None:
    before = _unchanged(case)
    async with httpx.AsyncClient(transport=httpx.MockTransport(case.handler)) as transport:
        host = DshWebHostClient(case.config.host, client=transport)
        app = create_app(case.database, dsh_callback_secret="callback-key",
                         dsh_activity_config=case.config, dsh_activity_host_client=host)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://hub.test",
        ) as api:
            collector = DshActivityCollector(case.database, host, case.config, sender=api)
            assert await collector.collect_once() == 1
            assert case.calls == ["GET", "POST", "GET", "GET"]
            assert _inactive(case)
            assert _unchanged(case) == before
            with case.database.session() as session:
                rows = session.query(RunRoleOutputRecord).all()
                assert len(rows) == 2 and case.captured_body is not None
                original = next(
                    row for row in rows if row.role.startswith("dsh_activity_snapshot:")
                )
                assert original.payload_json.encode() == case.captured_body
                assert original.payload_hash == hashlib.sha256(case.captured_body).hexdigest()
            missing_type = await api.post(
                "/v1/dsh/activity-snapshots", content=case.captured_body,
                headers={"X-Decision-Hub-Bridge-Key": "callback-key"},
            )
            assert missing_type.status_code == 422
            assert missing_type.json()["detail"][0]["loc"] == ["body"]
            assert missing_type.json()["detail"][0]["type"] == "model_attributes_type"
            assert case.calls == ["GET", "POST", "GET", "GET"]
            replay = await api.post(
                "/v1/dsh/activity-snapshots", content=case.captured_body,
                headers={"X-Decision-Hub-Bridge-Key": "callback-key",
                         "Content-Type": "application/json"},
            )
            assert replay.status_code == 200 and replay.json()["recorded_count"] == 0
            assert _unchanged(case) == before
            with case.database.session() as session:
                assert [(row.output_id, row.payload_json, row.payload_hash) for row in rows] == [
                    (row.output_id, row.payload_json, row.payload_hash)
                    for row in session.query(RunRoleOutputRecord).all()
                ]


@pytest.mark.parametrize("secret,provided,status", [
    ("callback-key", None, 401), ("callback-key", "wrong", 401), ("", "callback-key", 503),
])
@pytest.mark.asyncio
async def test_api_authentication_precedes_host_probe_or_any_receipt(
    case: ActivityCase, secret: str, provided: str | None, status: int,
) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(case.handler)) as transport:
        host = DshWebHostClient(case.config.host, client=transport)
        app = create_app(case.database, dsh_callback_secret=secret,
                         dsh_activity_config=case.config, dsh_activity_host_client=host)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://hub.test",
        ) as api:
            headers = {"X-Decision-Hub-Bridge-Key": provided} if provided is not None else {}
            response = await api.post(
                "/v1/dsh/activity-snapshots", json=case.snapshot(), headers=headers,
            )
            assert response.status_code == status and not case.calls
            response = await api.post("/v1/dsh/activity-scope-probe", headers=headers)
            assert response.status_code == status and not case.calls
    with case.database.session() as session:
        assert session.query(RunRoleOutputRecord).count() == 0
        assert session.query(ServiceRuntimeConfigRecord).count() == 0


@pytest.mark.parametrize("fault,expected_error,message", [
    ("epoch", ValueError, "dsh_activity_epoch_changed"),
    ("binding", ValueError, "dsh_activity_snapshot_binding_changed"),
    ("fence", ValidationError, "dsh_activity_inactive_unproven"),
    ("ttl", ValidationError, "dsh_activity_time_invalid"),
    ("transport", AgentExecutionError, "DSH Host returned a server error"),
])
@pytest.mark.asyncio
async def test_unproven_host_observation_never_mints_inactive_receipt(
    case: ActivityCase, fault: str, expected_error: type[Exception], message: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    case.fault = fault
    before = _unchanged(case)
    async with httpx.AsyncClient(transport=httpx.MockTransport(case.handler)) as transport:
        host = DshWebHostClient(case.config.host, client=transport)
        app = create_app(case.database, dsh_callback_secret="callback-key",
                         dsh_activity_config=case.config, dsh_activity_host_client=host)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://hub.test",
        ) as api:
            collector = DshActivityCollector(case.database, host, case.config, sender=api)
            with pytest.raises(expected_error, match=message) as failure:
                await collector.collect_once()
            if fault == "transport":
                assert isinstance(failure.value, AgentExecutionError)
                assert failure.value.error_code == "dsh_host_unavailable"
                assert failure.value.cause_code == "http_503" and failure.value.retryable
    assert not _inactive(case) and _unchanged(case) == before
    stage = {
        "epoch": "snapshot_binding", "binding": "snapshot_binding",
        "fence": "snapshot_validation", "ttl": "snapshot_validation",
        "transport": "host_scope_before",
    }[fault]
    diagnostics = [row.getMessage() for row in caplog.records
                   if row.name == "apps.hub_worker.dsh_activity"]
    assert any(f"stage={stage} targets=1 error_class=" in row for row in diagnostics)
    assert all("http://" not in row and "host-activity-key" not in row
               and "callback-key" not in row for row in diagnostics)
    with case.database.session() as session:
        assert session.query(RunRoleOutputRecord).count() == 0


@pytest.mark.parametrize("change", ["link", "lease"])
@pytest.mark.asyncio
async def test_api_writer_rechecks_original_link_and_live_lease(
    case: ActivityCase, change: str,
) -> None:
    payload = case.snapshot()

    def mutate() -> None:
        with case.database.session() as session:
            if change == "link":
                link = session.get(DshSessionLinkRecord, case.run_id)
                assert link is not None
                link.last_seq += 1
            else:
                run = session.get(RunRecord, case.run_id)
                assert run is not None
                run.lease_owner = "live-owner"
                run.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)

    case.before_get = mutate
    async with httpx.AsyncClient(transport=httpx.MockTransport(case.handler)) as transport:
        host = DshWebHostClient(case.config.host, client=transport)
        app = create_app(case.database, dsh_callback_secret="callback-key",
                         dsh_activity_config=case.config, dsh_activity_host_client=host)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://hub.test",
        ) as api:
            response = await api.post("/v1/dsh/activity-snapshots", json=payload,
                                      headers={"X-Decision-Hub-Bridge-Key": "callback-key"})
            assert response.status_code == 409
    assert not _inactive(case)
    with case.database.session() as session:
        assert session.query(RunRoleOutputRecord).count() == 0


@pytest.mark.parametrize("fault", ["transport", "workspace", "new_boot", "same_boot_changed"])
@pytest.mark.asyncio
async def test_independent_probe_invalidates_old_proof_without_touching_originals(
    case: ActivityCase, fault: str,
) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(case.handler)) as transport:
        host = DshWebHostClient(case.config.host, client=transport)
        app = create_app(case.database, dsh_callback_secret="callback-key",
                         dsh_activity_config=case.config, dsh_activity_host_client=host)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://hub.test",
        ) as api:
            headers = {"X-Decision-Hub-Bridge-Key": "callback-key"}
            result = await api.post(
                "/v1/dsh/activity-snapshots", json=case.snapshot(), headers=headers,
            )
            assert result.status_code == 200 and _inactive(case)
            with case.database.session() as session:
                original = [(row.output_id, row.payload_json, row.payload_hash)
                            for row in session.query(RunRoleOutputRecord).all()]
            if fault == "transport":
                case.fault = "transport"
            elif fault == "workspace":
                case.scope = case.scope.model_copy(update={"workspace_cwd_hash": "d" * 64})
            elif fault == "new_boot":
                case.scope = case.scope.model_copy(update={
                    "host_instance_id": uuid4(), "started_at": datetime.now(UTC),
                })
            else:
                case.scope = case.scope.model_copy(update={"process_id": 4321})
            result = await api.post("/v1/dsh/activity-scope-probe", headers=headers)
            expected_status = (
                200 if fault == "new_boot" else 409 if fault == "same_boot_changed" else 503
            )
            assert result.status_code == expected_status
            assert not _inactive(case)
            with case.database.session() as session:
                assert original == [(row.output_id, row.payload_json, row.payload_hash)
                                    for row in session.query(RunRoleOutputRecord).all()]
                assert session.query(ServiceHeartbeatRecord).count() == 0


@pytest.mark.asyncio
async def test_no_admission_need_does_not_scan_or_contact_host(case: ActivityCase) -> None:
    with case.database.session() as session:
        plan = session.get(ReviewScheduleRecord, "review_activity")
        assert plan is not None
        plan.trigger_at = datetime.now(UTC) + timedelta(hours=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(case.handler)) as transport:
        host = DshWebHostClient(case.config.host, client=transport)
        collector = DshActivityCollector(case.database, host, case.config, sender=transport)
        assert await collector.collect_once() == 0
    assert case.calls == []


@pytest.mark.asyncio
async def test_collector_failure_uses_real_probe_to_revoke_existing_proof(
    case: ActivityCase,
) -> None:
    before = _unchanged(case)
    async with httpx.AsyncClient(transport=httpx.MockTransport(case.handler)) as transport:
        host = DshWebHostClient(case.config.host, client=transport)
        app = create_app(case.database, dsh_callback_secret="callback-key",
                         dsh_activity_config=case.config, dsh_activity_host_client=host)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://hub.test",
        ) as api:
            collector = DshActivityCollector(case.database, host, case.config, sender=api)
            assert await collector.collect_once() == 1 and _inactive(case)
            with case.database.session() as session:
                originals = [(row.output_id, row.payload_json, row.payload_hash)
                             for row in session.query(RunRoleOutputRecord).all()]
            case.fault = "transport"
            with pytest.raises(AgentExecutionError) as failure:
                await collector.collect_once()
            assert failure.value.error_code == "dsh_host_unavailable"
            assert failure.value.cause_code == "http_503" and failure.value.retryable
            # First failed GET is collector-owned; the second is the API's
            # independently authenticated probe, which revokes the old head.
            assert case.calls[-2:] == ["GET", "GET"]
            assert not _inactive(case)
            with case.database.session() as session:
                assert originals == [(row.output_id, row.payload_json, row.payload_hash)
                                     for row in session.query(RunRoleOutputRecord).all()]
    assert _unchanged(case) == before


class NoModelActivityRuntime:
    runtime_id = "controlled-activity-maintenance"
    runtime_version = "controlled-activity.v1"
    profile_ref = "controlled-activity-maintenance.v1"

    def __init__(self) -> None:
        self.closed = False

    async def execute(
        self, request: ResearchSessionRequest, trace_sink: object = None,
    ) -> ResearchSessionResult:
        raise AssertionError("activity maintenance must not submit model research")

    async def close(self) -> None:
        self.closed = True


class ActivityMaintenanceProbe:
    def __init__(self) -> None:
        self.calls = 0
        self.closed = False
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def collect_once(self) -> int:
        self.calls += 1
        return 0

    async def run_forever(self) -> None:
        self.calls += 1
        self.entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()

    async def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("priority", [None, 100])
@pytest.mark.asyncio
async def test_real_once_loop_wires_activity_only_in_general_lane(
    case: ActivityCase, monkeypatch: pytest.MonkeyPatch, priority: int | None,
) -> None:
    runtime = NoModelActivityRuntime()
    worker = build_research_worker(
        case.database, runtime=runtime, allowed_capabilities=("replay.research",),
    )
    worker.worker.min_priority = priority
    maintenance = ActivityMaintenanceProbe()
    selected = 0

    def selected_worker(*_args: object, **_kwargs: object) -> ResearchWorker:
        return worker

    def selected_activity(*_args: object) -> ActivityMaintenanceProbe:
        nonlocal selected
        selected += 1
        return maintenance

    monkeypatch.setattr(main, "build_research_worker", selected_worker)
    monkeypatch.setattr(main, "build_dsh_activity_collector", selected_activity)
    result = await main._run_loop(
        database=case.database, registry=SourceRegistry(), settings=PilotSettings(),
        role="research", interval=1, once=True,
    )
    assert result == 0 and runtime.closed
    assert selected == maintenance.calls == (1 if priority is None else 0)
    assert maintenance.closed == (priority is None)
    assert not any(task.get_name() == "research-host-activity" for task in asyncio.all_tasks())


@pytest.mark.asyncio
async def test_activity_maintenance_runs_during_held_tick_and_closes_with_owner(
    case: ActivityCase, monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = NoModelActivityRuntime()
    worker = build_research_worker(
        case.database, runtime=runtime, allowed_capabilities=("replay.research",),
    )
    worker.worker.min_priority = None
    maintenance = ActivityMaintenanceProbe()
    tick_entered, usage_cancelled = asyncio.Event(), asyncio.Event()

    async def held_tick() -> None:
        tick_entered.set()
        await asyncio.Event().wait()

    async def held_usage() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            usage_cancelled.set()

    def selected_worker(*_args: object, **_kwargs: object) -> ResearchWorker:
        return worker

    def selected_activity(*_args: object) -> ActivityMaintenanceProbe:
        return maintenance

    def selected_usage(*_args: object) -> SimpleNamespace:
        return SimpleNamespace(run_forever=held_usage)

    monkeypatch.setattr(worker, "tick", held_tick)
    monkeypatch.setattr(main, "build_research_worker", selected_worker)
    monkeypatch.setattr(main, "build_dsh_activity_collector", selected_activity)
    monkeypatch.setattr(main, "UsageCollector", selected_usage)
    task = asyncio.create_task(main._run_loop(
        database=case.database, registry=SourceRegistry(), settings=PilotSettings(),
        role="research", interval=1, once=False,
    ))
    try:
        await asyncio.wait_for(tick_entered.wait(), timeout=1)
        await asyncio.wait_for(maintenance.entered.wait(), timeout=1)
        assert not task.done() and maintenance.calls == 1
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert maintenance.cancelled.is_set() and maintenance.closed
    assert usage_cancelled.is_set() and runtime.closed
    assert not any(item.get_name() == "research-host-activity" for item in asyncio.all_tasks())


@pytest.mark.asyncio
async def test_actual_web_composition_exposes_configured_activity_client(
    case: ActivityCase, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECISION_HUB_RESEARCH_RUNTIME", "dsh-web")
    monkeypatch.setenv("DECISION_HUB_DSH_WEB_URL", case.config.host.base_url)
    monkeypatch.setenv("DECISION_HUB_DSH_HOST_SECRET", case.config.host.bridge_key)
    monkeypatch.setenv("DECISION_HUB_API_URL", case.config.hub_base_url)
    monkeypatch.setenv("DECISION_HUB_DSH_HOME", str(case.config.home))
    monkeypatch.setenv("DECISION_HUB_DSH_WORKSPACE", str(case.config.workspace))
    monkeypatch.setenv("DECISION_HUB_DSH_CALLBACK_SECRET", case.config.callback_key)
    worker = build_research_worker(case.database, allowed_capabilities=("replay.research",))
    collector = build_dsh_activity_collector(case.database, worker.worker.runtime)
    try:
        assert isinstance(worker.worker.runtime, DshWebResearchRuntime)
        assert collector is not None
        assert collector.host is worker.worker.runtime.client
        assert collector.config.home == case.config.home
        assert collector.config.workspace == case.config.workspace
    finally:
        if collector is not None:
            await collector.close()
        await worker.close()


@pytest.mark.parametrize("missing", [
    "DECISION_HUB_DSH_WEB_URL", "DECISION_HUB_DSH_HOST_SECRET", "DECISION_HUB_API_URL",
    "DECISION_HUB_DSH_HOME", "DECISION_HUB_DSH_WORKSPACE", "DECISION_HUB_DSH_CALLBACK_SECRET",
])
def test_activity_configuration_requires_explicit_production_channel(
    case: ActivityCase, monkeypatch: pytest.MonkeyPatch, missing: str,
) -> None:
    values = {
        "DECISION_HUB_DSH_WEB_URL": case.config.host.base_url,
        "DECISION_HUB_DSH_HOST_SECRET": case.config.host.bridge_key,
        "DECISION_HUB_API_URL": case.config.hub_base_url,
        "DECISION_HUB_DSH_HOME": str(case.config.home),
        "DECISION_HUB_DSH_WORKSPACE": str(case.config.workspace),
        "DECISION_HUB_DSH_CALLBACK_SECRET": case.config.callback_key,
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(missing)
    with pytest.raises((ValueError, AgentExecutionError)):
        DshActivityTransportConfig.from_env()
