"""The real async boundaries keep native work off-loop and drain its exact Future."""

import asyncio
import threading
from types import SimpleNamespace

import httpx
import pytest

from apps.hub_worker.market_risk_loops import SpotResearchTick
from apps.hub_worker.spot_research_bootstrap import InstalledSpotResearch
from tests.market_risk.test_risk_activity_demand29 import NOW


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["compose", "approve", "cached_verify"])
async def test_owned_prepare_preserves_loop_and_drains_native_phase(phase):
    original_loop = asyncio.get_running_loop()
    original_thread = threading.get_ident()
    entered, release = threading.Event(), threading.Event()
    calls, http_loops = [], []

    def native(name, value):
        assert threading.get_ident() != original_thread
        calls.append(name)
        if name == phase:
            entered.set()
            assert release.wait(5)
        return value

    if phase == "cached_verify":
        tick = object.__new__(SpotResearchTick)
        tick._cached_scope_is_current = lambda instrument: native("cached_verify", True)
        operation = tick._prepare_scope("BTC-USDT")
    else:
        installed = object.__new__(InstalledSpotResearch)
        effective = SimpleNamespace(
            installed_pointer=SimpleNamespace(sha256="a" * 64),
            plugin_build_hash="b" * 64,
            model_dump=lambda **_: {"instrument": "BTC-USDT"},
        )
        installed.enabled = True
        installed.effective, installed.admission_ids = {}, {}
        installed.key = b"private-test-key"
        installed.clock = lambda: NOW
        installed.host_url = "http://controlled.test/readiness"
        installed._compose_effective = lambda instrument: native("compose", effective)
        installed._store = lambda _: native("store", {"ref": "controlled:host", "sha256": "c" * 64})
        installed.service = SimpleNamespace(
            approve_trial=lambda **_: native(
                "approve", SimpleNamespace(admission_id="private:admission")
            )
        )

        async def host(request):
            http_loops.append(asyncio.get_running_loop())
            return httpx.Response(
                200,
                json={
                    "schema_version": "dsh-host-readiness.v1",
                    "ready": True,
                    "version_compatible": True,
                    "checked_at": NOW.isoformat(),
                    "upstream_identity": {"plugin_build_hash": "b" * 64},
                },
            )

        installed.transport = httpx.MockTransport(host)
        operation = installed.prepare("BTC-USDT")
    task = asyncio.create_task(operation)
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls.count(phase) == 1
    assert all(loop is original_loop for loop in http_loops)
    if phase == "approve":
        assert calls == ["compose", "store", "approve"] and len(http_loops) == 1
