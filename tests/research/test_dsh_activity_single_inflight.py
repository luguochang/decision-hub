"""One collector owner, local controlled HTTP, and private SQLite Session drain.

These gates exercise collector/transport lifetime, not the unchanged API writer
authenticator, real Host census, or live research admission.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from threading import Event as ThreadEvent
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from apps.hub_worker import dsh_activity as activity_module
from apps.hub_worker.dsh_activity import DshActivityCollector
from packages.kernel.decision_hub_kernel.application.dsh_activity import (
    DshHostActivityRequest,
    DshHostActivityScope,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.runtime_adapters.dsh_runtime.activity_transport import DshActivityTransportConfig
from packages.runtime_adapters.dsh_runtime.web_host_client import DshWebHostClient, DshWebHostConfig


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'private.sqlite3'}")
    home, workspace = tmp_path / "home", tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    host = DshWebHostConfig(base_url="http://host.test", bridge_key="test-host-key")
    config = DshActivityTransportConfig(host, "http://hub.test", home, workspace, "test-api-key")
    scope = DshHostActivityScope(
        host_instance_id=uuid4(),
        process_id=123,
        started_at=datetime.now(UTC) - timedelta(minutes=1),
        hub_base_url="http://hub.test",
        workspace_id="test-workspace",
        workspace_cwd_hash=hashlib.sha256(
            str(workspace.resolve()).replace("\\", "/").lower().encode()
        ).hexdigest(),
    )
    targets = DshHostActivityRequest(
        records=[
            {
                "run_id": "run_old",
                "study_id": "study_old",
                "dsh_session_id": "session_old",
                "generation": 1,
                "request_hash": "a" * 64,
                "link_fingerprint": "b" * 64,
            }
        ]
    )
    return SimpleNamespace(database=database, config=config, scope=scope, targets=targets)


@asynccontextmanager
async def control(prepared, monkeypatch):
    state = SimpleNamespace(
        entered=asyncio.Event(),
        release=asyncio.Event(),
        cancelled=asyncio.Event(),
        posts=0,
        ingestions=0,
        targets=prepared.targets,
        snapshots=[],
    )

    async def host_handler(request: httpx.Request):
        assert request.headers["X-Decision-Hub-Host-Key"] == "test-host-key"
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "schema_version": "dsh-host-activity-scope.v1",
                    "scope": prepared.scope.model_dump(mode="json"),
                    "observed_at": datetime.now(UTC).isoformat(),
                },
            )
        state.posts += 1
        assert request.extensions["timeout"]["read"] == 35.0
        state.entered.set()
        try:
            await state.release.wait()
        except asyncio.CancelledError:
            state.cancelled.set()
            raise
        batch = DshHostActivityRequest.model_validate_json(request.content)
        now = datetime.now(UTC)
        value = {
            "schema_version": "dsh-host-activity-snapshot.v1",
            "scope": prepared.scope.model_dump(mode="json"),
            "observed_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=30)).isoformat(),
            "census_complete": True,
            "records": [
                {
                    **item.model_dump(mode="json"),
                    "state": "current_scope_inactive",
                    "presence": "absent",
                    "running": False,
                    "queue_count": 0,
                    "active_job_count": 0,
                    "descendant_ids": [],
                    "fence_id": "test-original-fence",
                    "reason_code": "test-quiescent",
                    "terminal_state": "unknown",
                }
                for item in batch.records
            ],
        }
        raw = json.dumps(value).encode()
        state.snapshots.append(raw)
        return httpx.Response(200, content=raw)

    def api_handler(request: httpx.Request):
        assert request.url.path == "/v1/dsh/activity-snapshots"  # pending must not probe
        assert request.headers["X-Decision-Hub-Bridge-Key"] == "test-api-key"
        assert request.content == state.snapshots[-1]
        state.ingestions += 1
        return httpx.Response(
            200, json={"recorded_count": len(json.loads(request.content)["records"])}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(host_handler)) as http_host:
        host = DshWebHostClient(prepared.config.host, client=http_host)
        async with httpx.AsyncClient(transport=httpx.MockTransport(api_handler)) as api:
            collector = DshActivityCollector(prepared.database, host, prepared.config, sender=api)
            monkeypatch.setattr(collector, "_targets", lambda: state.targets)
            state.collector = collector
            try:
                yield state
            finally:
                state.release.set()
                await collector.close()
    prepared.database.engine.dispose()


@pytest.mark.asyncio
async def test_single_inflight_observation_windows_keep_original_post_and_ingestion(
    prepared, monkeypatch
):
    monkeypatch.setattr(activity_module, "ACTIVITY_TIME_BUDGET_SECONDS", 0.02)
    async with control(prepared, monkeypatch) as state:
        collector = state.collector
        beats = 0

        async def beat():
            nonlocal beats
            while not state.release.is_set():
                beats += 1
                await asyncio.sleep(0.002)

        heartbeat = asyncio.create_task(beat())
        try:
            assert await collector.collect_once() == 0
            await asyncio.wait_for(state.entered.wait(), 1)
            original = collector._inflight
            assert await collector.collect_once() == await collector.collect_once() == 0
            assert collector._inflight is original and state.posts == 1 and state.ingestions == 0
            assert original is not None and not original.cancelled() and beats >= 3
            state.release.set()
            assert await asyncio.wait_for(original, 1) == 1
            assert await collector.collect_once() == 1
            assert state.posts == state.ingestions == 1
        finally:
            state.release.set()
            await heartbeat


@pytest.mark.asyncio
async def test_single_inflight_new_targets_use_only_next_fresh_operation(prepared, monkeypatch):
    monkeypatch.setattr(activity_module, "ACTIVITY_TIME_BUDGET_SECONDS", 0.01)
    async with control(prepared, monkeypatch) as state:
        collector = state.collector
        assert await collector.collect_once() == 0
        await asyncio.wait_for(state.entered.wait(), 1)
        extra = state.targets.records[0].model_copy(
            update={"run_id": "run_new", "dsh_session_id": "session_new"}
        )
        state.targets = state.targets.model_copy(
            update={"records": [*state.targets.records, extra]}
        )
        assert await collector.collect_once() == 0 and state.posts == 1
        state.release.set()
        assert await collector.collect_once() == 1
        assert len(json.loads(state.snapshots[0])["records"]) == 1
        recorded = await collector.collect_once()
        if recorded == 0:
            pending = collector._inflight
            assert pending is not None
            assert await asyncio.wait_for(pending, 1) == 2
            recorded = await collector.collect_once()
        assert recorded == 2
        assert state.posts == state.ingestions == 2
        assert len(json.loads(state.snapshots[1])["records"]) == 2


@pytest.mark.asyncio
async def test_single_inflight_shutdown_cancels_original_host_post(prepared, monkeypatch):
    monkeypatch.setattr(activity_module, "ACTIVITY_TIME_BUDGET_SECONDS", 0.01)
    async with control(prepared, monkeypatch) as state:
        assert await state.collector.collect_once() == 0
        await asyncio.wait_for(state.entered.wait(), 1)
        await asyncio.wait_for(state.collector.close(), 1)
        assert state.cancelled.is_set() and state.posts == 1 and state.ingestions == 0
        assert state.collector._inflight is None


@pytest.mark.asyncio
async def test_single_inflight_shutdown_drains_original_read_session(prepared, monkeypatch):
    monkeypatch.setattr(activity_module, "ACTIVITY_TIME_BUDGET_SECONDS", 0.01)
    async with control(prepared, monkeypatch) as state:
        entered, release, closed = ThreadEvent(), ThreadEvent(), ThreadEvent()

        def targets():
            try:
                with prepared.database.session() as session:
                    session.execute(text("SELECT 1"))
                    entered.set()
                    assert release.wait(2)
            finally:
                closed.set()

        monkeypatch.setattr(state.collector, "_targets", targets)
        try:
            assert await state.collector.collect_once() == 0
            assert await asyncio.to_thread(entered.wait, 1)
            closing = asyncio.create_task(state.collector.close())
            await asyncio.sleep(0.02)
            assert not closing.done() and not closed.is_set()
            release.set()
            await asyncio.wait_for(closing, 1)
            assert closed.is_set() and prepared.database.engine.pool.checkedout() == 0
            assert state.posts == 0
        finally:
            release.set()
