from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run(command: list[str]) -> None:
    print("$", " ".join(command))
    completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)


@contextmanager
def _offline_environment(data_dir: Path) -> Iterator[None]:
    names = {
        "DECISION_HUB_DATA_DIR": str(data_dir),
        "DECISION_HUB_LLM_ENABLED": "0",
        "DECISION_HUB_SOURCES_ENABLED": "0",
        "DECISION_HUB_MARKET_ENABLED": "0",
        "DECISION_HUB_LLM_CANARY_STATUS": "not_run",
    }
    previous = {name: os.environ.get(name) for name in names}
    os.environ.update(names)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _three_process_evolution_and_recovery_smoke() -> None:
    from apps.hub_api.main import create_app
    from apps.hub_worker.composition import build_evolution_worker, build_realtime_worker
    from packages.contracts_py.decision_hub_contracts.models import EvolutionJobCreate
    from packages.kernel.decision_hub_kernel.application.evolution import (
        EvolutionAssetService,
    )
    from packages.kernel.decision_hub_kernel.application.live_observation import (
        EvolutionJobService,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import Database, FeedbackRecord
    from packages.pilot_runtime import PilotSettings
    from packages.source_adapters.registry import SourceRegistry

    with tempfile.TemporaryDirectory(prefix="decision-hub-live-observation-") as temp_dir:
        data_dir = Path(temp_dir)
        with _offline_environment(data_dir):
            database = Database(
                f"sqlite+pysqlite:///{data_dir / 'db' / 'acceptance.sqlite3'}"
            )
            database.initialize()
            realtime = build_realtime_worker(
                database,
                registry=SourceRegistry(),
                settings=PilotSettings.from_env(),
                heartbeat_interval_seconds=5,
            )
            realtime_report = asyncio.run(realtime.tick())
            if realtime_report.runs_failed or realtime_report.polled:
                raise SystemExit("offline realtime worker performed unexpected live work")

            evolution = build_evolution_worker(
                database,
                heartbeat_interval_seconds=5,
            )
            assets = EvolutionAssetService(database)
            pointer_before = assets.active_pointer("crypto_macro.v1")
            if pointer_before is None:
                raise SystemExit("release baseline was not installed")
            now = datetime.now(UTC)
            with database.session() as session:
                session.add(
                    FeedbackRecord(
                        feedback_id="acceptance-feedback",
                        request_id="acceptance-feedback-request",
                        target_type="run",
                        target_id="acceptance-run",
                        created_by="owner",
                        verdict="incorrect",
                        notes="Cross-asset confirmation was missing.",
                        created_at=now,
                    )
                )
            completed = asyncio.run(evolution.tick())
            if completed is None or completed.status != "pending_owner_review":
                raise SystemExit("evolution job did not stop at owner review")
            if completed.stage != "review" or len(completed.result_refs) != 3:
                raise SystemExit("evolution job did not complete replay/holdout/shadow")
            pointer_after = assets.active_pointer("crypto_macro.v1")
            if pointer_after is None or pointer_after.candidate_id != pointer_before.candidate_id:
                raise SystemExit("evolution worker changed the active pointer")

            restarted = build_evolution_worker(database, heartbeat_interval_seconds=5)
            if asyncio.run(restarted.tick()) is not None:
                raise SystemExit("restarted worker duplicated an already reviewed job")
            if len(assets.overview().candidates) != 2:
                raise SystemExit("restart duplicated or lost the release/candidate assets")

            recovery_time = now
            jobs = EvolutionJobService(database, clock=lambda: recovery_time)
            recovery = jobs.enqueue(
                EvolutionJobCreate(
                    trigger_key="scheduled:acceptance-recovery",
                    trigger_type="scheduled",
                    domain_pack_ref="crypto_macro.v1",
                    input_refs=["acceptance:lease-recovery"],
                    max_attempts=3,
                )
            )
            claimed = jobs.claim("interrupted-worker", lease_seconds=1)
            if claimed is None or claimed.job_id != recovery.job_id:
                raise SystemExit("first worker did not claim the recovery job")
            recovery_time += timedelta(seconds=2)
            reclaimed = jobs.claim("replacement-worker", lease_seconds=30)
            if reclaimed is None or reclaimed.lease_owner != "replacement-worker":
                raise SystemExit("expired evolution lease was not recovered")
            jobs.advance(
                reclaimed.job_id,
                "replacement-worker",
                stage="plan",
                status="completed",
            )

            with TestClient(
                create_app(database, source_connectors=[], sources_enabled=False)
            ) as client:
                operations = client.get("/v1/operations")
                job_response = client.get("/v1/evolution/jobs")
            if operations.status_code != 200 or job_response.status_code != 200:
                raise SystemExit("operations/evolution API smoke failed")
            operation_view = operations.json()
            services = {
                item["service_id"]: item["status"]
                for item in operation_view["services"]
            }
            if set(services) != {
                "hub-api",
                "hub-realtime-worker",
                "hub-research-worker",
                "hub-evolution-worker",
            }:
                raise SystemExit("operations view omitted an expected logical process")
            if operation_view["runtime"]["mode"] != "fake":
                raise SystemExit("offline runtime was presented as a live provider")
            if operation_view["runtime"]["provider_configured"] is not False:
                raise SystemExit("offline runtime was presented as provider-configured")
            if operation_view["runtime"]["live_canary_status"] != "not_run":
                raise SystemExit("offline runtime was presented as canary-passed")
            print(
                json.dumps(
                    {
                        "active_pointer_unchanged": True,
                        "candidate_count": 2,
                        "evolution_status": completed.status,
                        "lease_recovery": "ok",
                        "operations_services": services,
                        "runtime_mode": "fake",
                    },
                    sort_keys=True,
                )
            )


def main() -> int:
    commands = [
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/capabilities/test_search_capability.py",
            "tests/evolution/test_live_observation_jobs.py",
            "tests/evolution/test_evolution_executor.py",
            "tests/evolution/test_worker_processes.py",
            "tests/operations/test_operations_api.py",
            "-q",
        ],
        [sys.executable, "-m", "tools.contract_codegen", "check"],
        [sys.executable, "tools/docs/check_module_docs.py"],
    ]
    try:
        for command in commands:
            _run(command)
        _three_process_evolution_and_recovery_smoke()
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
