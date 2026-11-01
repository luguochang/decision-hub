"""Actual adapters and isolated SQL; synthetic Host/install originals, no V3 claim/model."""

from __future__ import annotations

import asyncio
import hashlib
import json
import stat
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import text

from apps.hub_worker.market_risk_loops import PathShadowTick, SpotResearchTick
from apps.hub_worker.spot_research_bootstrap import (
    CAPABILITY,
    MACRO_CAPABILITY,
    InstalledSpotResearch,
    _bytes,
    _publish_original,
)
from packages.contracts_py.decision_hub_contracts import ObservationCreate
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.market_risk_data import MarketRiskDataService
from packages.kernel.decision_hub_kernel.application.risk_path_ledger import RiskPathLedger
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.spot_scope_admission import (
    EffectiveSpotRecipe,
    SpotCapabilityRoute,
)
from packages.kernel.decision_hub_kernel.application.study import ensure_study
from packages.kernel.decision_hub_kernel.persistence.db import Database, ReviewScheduleRecord
from packages.kernel.decision_hub_kernel.persistence.path_forecast_records import (
    RiskShadowTrialRecord,
)
from packages.kernel.decision_hub_kernel.persistence.risk_research_records import (
    RiskResearchRequestRecord,
)
from packages.kernel.decision_hub_kernel.persistence.spot_scope_admission import (
    SpotResearchAdmissionRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope
from packages.provider_adapters.market.okx_path import OKXMarketPathAdapter
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from tests.market_risk.test_market_data import NOW, PublicFixture


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    root = tmp_path / "data"
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(root))
    monkeypatch.setenv("DECISION_HUB_RESEARCH_CAPABILITIES", f"{CAPABILITY},{MACRO_CAPABILITY}")
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'bootstrap.sqlite3'}")
    db.create_all()
    now, requests = [NOW], []
    raw = {
        "schema_version": "dsh-host-readiness.v1",
        "ready": True,
        "version_compatible": True,
        "upstream_identity": {"plugin_build_hash": "f" * 64},
        "checked_at": NOW.isoformat(),
    }
    response = [raw]

    def host(request):
        requests.append(str(request.url))
        return httpx.Response(200, json=response[0])

    installed = InstalledSpotResearch(
        db,
        root,
        enabled=True,
        clock=lambda: now[0],
        transport=httpx.MockTransport(host),
    )
    effective = {}
    for instrument in ("BTC-USDT", "ETH-USDT"):
        spot_manifest = installed.manifests[CAPABILITY]
        effective[instrument] = EffectiveSpotRecipe(
            research_scope_id=f"{instrument.split('-')[0].lower()}-spot-macro.v1",
            instrument=instrument,
            recipe=installed._store({"synthetic_installation": "recipe-never-official-v3"}),
            pack=installed._store({"synthetic_installation": "prepared-pack"}),
            installed_pointer=installed._store({"synthetic_installation": "pointer"}),
            plugin_build_hash="f" * 64,
            required_capability_ids=(CAPABILITY, MACRO_CAPABILITY),
            routes=(
                SpotCapabilityRoute(
                    capability_id=CAPABILITY,
                    asset_mode="instrument",
                    instrument=instrument,
                    manifest=installed._store(spot_manifest.model_dump(mode="json")),
                    route=installed._store(
                        installed.spot.signed_route_receipt(
                            instrument,
                            spot_manifest,
                            signing_key=installed.key,
                        )
                    ),
                ),
                SpotCapabilityRoute(
                    capability_id=MACRO_CAPABILITY,
                    asset_mode="global_macro",
                    manifest=installed._store(
                        installed.manifests[MACRO_CAPABILITY].model_dump(mode="json")
                    ),
                    route=installed._store(installed._signed_macro()),
                ),
            ),
        )
    monkeypatch.setattr(installed, "_compose_effective", effective.__getitem__)
    data = MarketRiskDataService(
        db,
        RawBodyArchive(root / "raw-captures"),
        root / "market-risk/features",
        adapter=OKXMarketPathAdapter(fetcher=PublicFixture(now).fetch),
        scopes=(spot_scope("BTC-USDT"), spot_scope("ETH-USDT")),
        enabled=True,
        clock=lambda: now[0],
    )
    yield SimpleNamespace(
        db=db,
        installed=installed,
        data=data,
        root=root,
        now=now,
        raw=raw,
        response=response,
        requests=requests,
        effective=effective,
    )
    db.engine.dispose()


def test_actual_catalog_and_macro_router_have_executable_asset_safe_shapes(isolated):
    instance = isolated.installed
    catalog = {item["capability_id"]: item for item in instance.catalog["capabilities"]}
    assert set(catalog) == {CAPABILITY, MACRO_CAPABILITY}
    spot = catalog[CAPABILITY]
    assert spot["routes"][0]["symbols"] == ["BTC-USDT", "ETH-USDT"]
    assert "funding_rate" not in spot["routes"][0]["fields"]
    macro = instance._macro_material()
    assert macro["catalog"] == catalog[MACRO_CAPABILITY]
    assert len(macro["implementations"]) == 3  # router + FRED + Yahoo, actual classes
    assert all(len(item["source"]["sha256"]) == 64 for item in macro["implementations"])
    assert isolated.requests == []


@pytest.mark.parametrize(
    "mutation",
    [
        "list",
        "null",
        "identity_null",
        "identity_list",
        "hash_null",
        "hash_bad",
        "checked_missing",
        "checked_null",
        "checked_list",
        "checked_bad",
        "checked_naive",
        "ready_string",
        "version_null",
    ],
)
@pytest.mark.asyncio
async def test_malformed_host_shapes_return_exact_failure_not_unhandled_exception(
    isolated, mutation
):
    raw = deepcopy(isolated.raw)
    if mutation == "list":
        raw = []
    elif mutation == "null":
        raw = None
    elif mutation.startswith("identity_"):
        raw["upstream_identity"] = None if mutation.endswith("null") else []
    elif mutation.startswith("hash_"):
        raw["upstream_identity"]["plugin_build_hash"] = None if mutation.endswith("null") else "bad"
    elif mutation == "checked_missing":
        del raw["checked_at"]
    elif mutation.startswith("checked_"):
        raw["checked_at"] = {
            "checked_null": None,
            "checked_list": [],
            "checked_bad": "invalid",
            "checked_naive": NOW.replace(tzinfo=None).isoformat(),
        }[mutation]
    elif mutation == "ready_string":
        raw["ready"] = "true"
    else:
        raw["version_compatible"] = None
    isolated.response[0] = raw
    with pytest.raises(ValueError, match="^spot_effective_host_response_invalid$"):
        await isolated.installed.prepare("BTC-USDT")
    with isolated.db.session() as session:
        assert session.query(SpotResearchAdmissionRecord).count() == 0


@pytest.mark.parametrize(
    "mutation", ["not_ready", "incompatible", "other_build", "stale", "future"]
)
@pytest.mark.asyncio
async def test_valid_json_but_ineligible_host_never_admits(isolated, mutation):
    raw = isolated.response[0]
    if mutation == "not_ready":
        raw["ready"] = False
    elif mutation == "incompatible":
        raw["version_compatible"] = False
    elif mutation == "other_build":
        raw["upstream_identity"]["plugin_build_hash"] = "e" * 64
    else:
        offset = -180 if mutation == "stale" else 1
        raw["checked_at"] = (NOW + timedelta(seconds=offset)).isoformat()
    with pytest.raises(ValueError, match="^spot_effective_host_not_ready$"):
        await isolated.installed.prepare("ETH-USDT")


@pytest.mark.asyncio
async def test_bad_host_is_persisted_as_bounded_pending_request_not_silent_success(isolated):
    isolated.response[0] = []
    tick = SpotResearchTick(isolated.db, isolated.installed)
    assert await tick.tick() == 0
    with isolated.db.session() as session:
        rows = session.query(RiskResearchRequestRecord).all()
        assert len(rows) == 2
        assert {row.status for row in rows} == {"pending_admission"}
        assert {row.error_code for row in rows} == {"spot_effective_host_response_invalid"}
        assert {row.attempts for row in rows} == {1}


@pytest.mark.asyncio
async def test_prepare_signature_readthrough_restart_same_writer_and_tamper(isolated, monkeypatch):
    assert await isolated.data.refresh_due() == 2
    await isolated.installed.prepare("ETH-USDT")
    restarted = InstalledSpotResearch(isolated.db, isolated.root, enabled=True, clock=lambda: NOW)
    monkeypatch.setattr(restarted, "_compose_effective", isolated.effective.__getitem__)
    assert restarted.key == isolated.installed.key
    with isolated.db.session() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        with monkeypatch.context() as current:

            def no_second_session():
                raise AssertionError("resolver opened a second database session")

            current.setattr(isolated.db, "session", no_second_session)
            verified = restarted.resolve_in_session(
                session, SimpleNamespace(instrument="ETH-USDT"), NOW
            )
        assert verified.admission_id == isolated.installed.admission_ids["ETH-USDT"]
        assert verified.proof.effective.instrument == "ETH-USDT"
        assert not verified.proof.product_release_qualified
    receipt = Path(verified.proof.host.receipt.ref)
    original = receipt.read_bytes()
    changed = json.loads(original)
    changed["body"]["response"]["ready"] = False
    receipt.write_bytes(_bytes(changed))
    with isolated.db.session() as session:
        with pytest.raises(ValueError, match="^spot_host_capture_not_authenticated$"):
            restarted.resolve_in_session(session, SimpleNamespace(instrument="ETH-USDT"), NOW)
    receipt.write_bytes(original)  # isolated test original only


def test_atomic_original_concurrency_preserves_bytes_and_rejects_corruption(tmp_path):
    root = tmp_path / "isolated"
    path = root / "spot-admissions/originals/one.json"
    body = b"synthetic immutable original\n"
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: _publish_original(root, path, body), range(12)))
    assert all(value == body for value in results)
    assert not list(path.parent.glob("*.tmp"))
    path.write_bytes(b"corrupted fixture")
    with pytest.raises(ValueError, match="spot_resource_original_corrupt"):
        _publish_original(root, path, body)
    assert path.read_bytes() == b"corrupted fixture"
    outside = tmp_path / "outside"
    with pytest.raises(ValueError, match="outside_registered_roots"):
        _publish_original(root, outside, body)
    assert not outside.exists()


def test_cross_process_key_publication_reuses_one_winner_without_partial_reads(tmp_path):
    root = tmp_path / "cross-process"
    script = (
        "import hashlib,os,sys; from pathlib import Path; "
        "from apps.hub_worker.spot_research_bootstrap import _publish_original; "
        "root=Path(sys.argv[1]); "
        "key=_publish_original(root,root/'spot-admissions/route-signing.key',"
        "os.urandom(32),same_bytes=False); "
        "assert len(key)==32; print(hashlib.sha256(key).hexdigest())"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(root)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for _ in range(2)
    ]
    outputs = []
    try:
        for process in processes:
            output, error = process.communicate(timeout=30)
            assert process.returncode == 0, error
            outputs.append(output.strip())
        assert outputs[0] == outputs[1]
        assert len((root / "spot-admissions/route-signing.key").read_bytes()) == 32
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


def test_reparse_parent_is_rejected_before_publication(tmp_path, monkeypatch):
    root = tmp_path / "root"
    parent = root / "spot-admissions"
    parent.mkdir(parents=True)
    original = Path.lstat

    def metadata(path):
        if path == parent:
            return SimpleNamespace(
                st_mode=stat.S_IFDIR, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT
            )
        return original(path)

    monkeypatch.setattr(Path, "lstat", metadata)
    with pytest.raises(ValueError, match="reparse_parent"):
        _publish_original(root, parent / "new.key", b"test")
    assert not (parent / "new.key").exists()


@pytest.mark.asyncio
async def test_due_review_requires_fresh_prepare_once_per_tick_even_with_old_admission(
    isolated, monkeypatch
):
    assert await isolated.data.refresh_due() == 2
    await isolated.installed.prepare("BTC-USDT")
    event, _, _ = AdmissionService(isolated.db, clock=lambda: NOW).admit(
        ObservationCreate(
            text="Synthetic BTC spot research test",
            source_id="isolated-bootstrap-test",
            language="en",
        )
    )
    with isolated.db.session() as session:
        study_id = ensure_study(
            session,
            question_key="synthetic-risk24h",
            title="Synthetic isolated review",
            market="crypto",
            instrument="BTC-USDT",
            horizon_scope="24h",
            now=NOW,
        )
    runs = RunService(
        isolated.db,
        clock=lambda: NOW,
        spot_admission_resolver=isolated.installed.resolve_in_session,
    )
    run, _ = runs.create(
        event, "synthetic-parent", strategy_version="research.v1", study_id=study_id
    )
    with isolated.db.session() as session:
        session.add(
            ReviewScheduleRecord(
                review_plan_id="synthetic-review",
                parent_run_id=run,
                horizon="24h",
                trigger_type="time",
                trigger_ref="synthetic",
                trigger_at=NOW + timedelta(seconds=10),
                trigger_hash=hashlib.sha256(b"synthetic-review").hexdigest(),
                status="planned",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    tick = SpotResearchTick(isolated.db, isolated.installed)
    assert tick.due_review_instruments() == ("BTC-USDT",)
    calls = len(isolated.requests)

    async def requests_same_scope(*, limit):
        assert limit == 2
        await tick.service.prepare_scope("BTC-USDT")
        return 0

    monkeypatch.setattr(tick.service, "process_due", requests_same_scope)
    assert await tick.tick() == 0
    assert len(isolated.requests) == calls + 1
    assert tick._prepared_this_tick == {}
    assert await tick.tick() == 0
    assert len(isolated.requests) == calls + 2  # new tick may not reuse previous gate


@pytest.mark.parametrize("failure", ["invalid_json", "http_failure"])
@pytest.mark.asyncio
async def test_host_http_and_json_failures_have_distinct_codes(isolated, failure):
    def response(_request):
        return (
            httpx.Response(200, text="not json")
            if failure == "invalid_json"
            else httpx.Response(503)
        )

    isolated.installed.transport = httpx.MockTransport(response)
    code = (
        "spot_effective_host_response_invalid"
        if failure == "invalid_json"
        else "spot_effective_host_unavailable"
    )
    with pytest.raises(ValueError, match=f"^{code}$"):
        await isolated.installed.prepare("BTC-USDT")


def test_concurrent_initial_shadow_owners_keep_one_trial_then_restart(isolated):
    barrier = threading.Barrier(2)

    def start(owner):
        ledger = RiskPathLedger(isolated.db, isolated.data.store, clock=lambda: isolated.now[0])
        barrier.wait(timeout=3)
        return PathShadowTick(ledger, owner_id=owner).trial_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        owners = list(pool.map(start, ("synthetic-one", "synthetic-two")))
    assert owners[0] == owners[1]
    isolated.now[0] += timedelta(days=35)
    restarted = PathShadowTick(
        RiskPathLedger(isolated.db, isolated.data.store, clock=lambda: isolated.now[0]),
        owner_id="synthetic-restarted",
    )
    assert restarted.trial_id == owners[0]
    with isolated.db.session() as session:
        assert session.query(RiskShadowTrialRecord).count() == 1


@pytest.mark.asyncio
async def test_repeated_cancel_still_joins_original_bounded_thread(isolated):
    tick = PathShadowTick(
        RiskPathLedger(isolated.db, isolated.data.store, clock=lambda: isolated.now[0]),
        owner_id="synthetic-cancel",
    )
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()

    def work(*, limit):
        assert limit == 4
        entered.set()
        assert release.wait(timeout=3)
        exited.set()
        return 0

    tick.jobs.process_due = work
    task = asyncio.create_task(tick.tick())
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done() and not exited.is_set()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert exited.is_set()


@pytest.mark.asyncio
async def test_managed_local_host_does_not_inherit_system_proxy(isolated, monkeypatch):
    assert await isolated.data.refresh_due() == 2
    original = httpx.AsyncClient
    options = []

    def client(*args, **kwargs):
        options.append((kwargs.get("trust_env"), kwargs.get("follow_redirects")))
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    await isolated.installed.prepare("BTC-USDT")
    assert options == [(False, False)]
