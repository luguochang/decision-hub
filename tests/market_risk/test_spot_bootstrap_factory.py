"""Actual bootstrap/manifest/admission/Factory, controlled installation and Host.

No installed pointer is written, no live provider or model is invoked. Market
captures and the trusted build subset are explicit engineering fixtures.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter

import httpx
import pytest
import yaml
from sqlalchemy import select

from apps.hub_worker.research import ResearchRequestFactory
from apps.hub_worker.spot_research_bootstrap import POLICY_REFS, InstalledSpotResearch
from packages.contracts_py.decision_hub_contracts import ObservationCreate
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.market_risk_data import MarketRiskDataService
from packages.kernel.decision_hub_kernel.application.research_graph_recipe import TASK_ROLE
from packages.kernel.decision_hub_kernel.application.research_graph_recipe_v3 import (
    INSTALLED_POINTER_REF,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.study import ensure_study
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRoleOutputRecord
from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope
from packages.provider_adapters.market.okx_path import OKXMarketPathAdapter
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from tests.market_risk.test_market_data import PublicFixture
from tests.market_risk.test_recipe_selection_v3 import configure, installation_fixture, manifest

assert installation_fixture is not None and manifest is not None
ROOT = Path(__file__).resolve().parents[2]


def test_actual_composer_two_assets_first_tasks_and_corrupt_manifest_rejected(
    tmp_path, monkeypatch, installation_fixture, record_property
):
    configure(installation_fixture)
    _, receipt, bodies = installation_fixture
    original_read = Path.read_bytes
    pointer_path = (ROOT / INSTALLED_POINTER_REF).resolve()

    def installed_pointer_read(path):
        if path.resolve() == pointer_path:
            return bodies[INSTALLED_POINTER_REF]
        return original_read(path)

    # Exactly the controlled pointer bytes already validated by configure(); all
    # real Pack, source, route, capture and SQL reads retain their actual readers.
    monkeypatch.setattr(Path, "read_bytes", installed_pointer_read)
    data_root = tmp_path / "data"
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(data_root))
    pack_root = ROOT / "packs/crypto_macro_prepared/1.2.0"
    definition = yaml.safe_load((pack_root / "pack.yaml").read_text(encoding="utf-8"))
    old_definition = yaml.safe_load(
        (ROOT / "packs/crypto_macro_prepared/1.1.0/pack.yaml").read_text(encoding="utf-8")
    )
    monkeypatch.setenv(
        "DECISION_HUB_RESEARCH_CAPABILITIES", ",".join(definition["capability_refs"])
    )
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'actual-composer.sqlite3'}")
    db.create_all()
    now = [datetime.now(UTC)]
    host_requests = []

    def host(request):
        host_requests.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "schema_version": "dsh-host-readiness.v1",
                "ready": True,
                "version_compatible": True,
                "upstream_identity": {"plugin_build_hash": receipt["plugin_build_hash"]},
                "checked_at": now[0].isoformat(),
            },
        )

    installed = InstalledSpotResearch(
        db, data_root, enabled=True, clock=lambda: now[0], transport=httpx.MockTransport(host)
    )
    data = MarketRiskDataService(
        db,
        RawBodyArchive(data_root / "raw-captures"),
        data_root / "market-risk/features",
        adapter=OKXMarketPathAdapter(fetcher=PublicFixture(now).fetch),
        scopes=(spot_scope("BTC-USDT"), spot_scope("ETH-USDT")),
        enabled=True,
        clock=lambda: now[0],
    )
    factory = ResearchRequestFactory(
        db,
        pack_root=ROOT / "packs/crypto_macro_prepared/1.1.0",
        spot_prepared_pack_root=pack_root,
        prepared_policy_refs=POLICY_REFS,
        allowed_capabilities=tuple(old_definition["capability_refs"]),
        spot_allowed_capabilities=tuple(definition["capability_refs"]),
        clock=lambda: now[0],
    )
    runs = RunService(
        db, clock=lambda: now[0], spot_admission_resolver=installed.resolve_in_session
    )
    timings = {}
    started = perf_counter()

    def new_run(instrument, suffix):
        event_id, _, _ = AdmissionService(db, clock=lambda: now[0]).admit(
            ObservationCreate(
                text=f"{instrument} public risk review {suffix}",
                source_id="risk-monitor",
                source_type="transcript",
                observed_at=now[0] - timedelta(seconds=2),
                language="en",
                event_hint="central_bank_speech",
            )
        )
        with db.session() as session:
            study_id = ensure_study(
                session,
                question_key=f"spot-bootstrap-{instrument}",
                title=f"{instrument}24h",
                market="crypto",
                instrument=instrument,
                horizon_scope="30m,24h,72h",
                now=now[0],
            )
        run_id, _ = runs.create(event_id, strategy_version="research.v1", study_id=study_id)
        return event_id, run_id

    try:
        captured = perf_counter()
        assert asyncio.run(data.refresh_due()) == 2
        timings["two_scope_capture_seconds"] = perf_counter() - captured
        for instrument in ("BTC-USDT", "ETH-USDT"):
            now[0] = datetime.now(UTC)
            prepared = perf_counter()
            # Preserve the real launcher's 45s deadline; do not relax it for tests.
            asyncio.run(asyncio.wait_for(installed.prepare(instrument), timeout=45))
            timings[f"{instrument}_prepare_seconds"] = perf_counter() - prepared
            effective = installed.effective[instrument]
            assert effective.pack.ref == "packs/crypto_macro_prepared/1.2.0/pack.yaml"
            assert effective.pack.sha256 == hashlib.sha256(
                original_read(pack_root / "pack.yaml")
            ).hexdigest()
            assert not installed.service.resolve(
                installed.admission_ids[instrument], effective=effective, host=installed.host
            ).product_release_qualified
            created = perf_counter()
            event_id, run_id = new_run(instrument, "first")
            timings[f"{instrument}_run_seconds"] = perf_counter() - created
            built = perf_counter()
            request = factory.build(event_id, run_id)
            timings[f"{instrument}_first_task_seconds"] = perf_counter() - built
            assert request.run_id == run_id
            with db.session() as session:
                assert session.scalar(
                    select(RunRoleOutputRecord.output_id).where(
                        RunRoleOutputRecord.run_id == run_id,
                        RunRoleOutputRecord.role == TASK_ROLE,
                    )
                ) is not None

        now[0] = datetime.now(UTC)
        event_id, rejected_run = new_run("BTC-USDT", "corrupt-manifest")
        manifest_path = (pack_root / "pack.yaml").resolve()

        def corrupt_original_read(path):
            body = installed_pointer_read(path)
            return body + b"\n# changed original\n" if path.resolve() == manifest_path else body

        # Simulate damaged bytes from the exact original read, never mutate the
        # shared development Pack or weaken admission/Factory verification.
        monkeypatch.setattr(Path, "read_bytes", corrupt_original_read)
        rejected = perf_counter()
        with pytest.raises(ValueError) as error:
            factory.build(event_id, rejected_run)
        timings["corrupt_manifest_rejection_seconds"] = perf_counter() - rejected
        record_property("corrupt_manifest_reason", str(error.value))
        assert "hash" in str(error.value) or "mismatch" in str(error.value)
        with db.session() as session:
            assert session.scalar(
                select(RunRoleOutputRecord.output_id).where(
                    RunRoleOutputRecord.run_id == rejected_run,
                    RunRoleOutputRecord.role == TASK_ROLE,
                )
            ) is None
        assert len(host_requests) == 2
    finally:
        timings["total_seconds"] = perf_counter() - started
        record_property("stage_timings_seconds", json.dumps(timings, sort_keys=True))
        record_property("qualification", "isolated controlled engineering proof only")
        db.engine.dispose()
