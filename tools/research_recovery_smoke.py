"""Cross-process recovery smoke test for the durable research worker."""

from __future__ import annotations

# The direct-script bootstrap must run before repository-package imports.
# ruff: noqa: E402
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

# Support both ``python -m tools.research_recovery_smoke`` and direct script
# execution used by the stage runbook.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from apps.hub_api.main import create_app
from apps.hub_worker.research import ResearchRequestFactory
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord,
    Database,
    OutboxRecord,
    ResearchEvidenceRecord,
    RunRecord,
    RunStatus,
    utcnow,
)
from packages.orchestration.langgraph.executor import LangGraphResearchExecutor
from packages.runtime_adapters.replay_runtime import ReplayResearchRuntime

PACK_ROOT = ROOT / "packs" / "crypto_macro"
FIXTURE = PACK_ROOT / "fixtures" / "research-worker-replay.json"


def _run_worker(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "apps.hub_worker.main", "--role", "research", "--once"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _counts(database: Database, run_id: str) -> dict[str, int]:
    with database.session() as session:
        artifact_ids = [
            item.artifact_id
            for item in session.query(ArtifactRecord).filter_by(run_id=run_id).all()
        ]
        return {
            "artifacts": len(artifact_ids),
            "evidence": session.query(ResearchEvidenceRecord).filter_by(run_id=run_id).count(),
            "outbox": (
                session.query(OutboxRecord)
                .filter(OutboxRecord.artifact_id.in_(artifact_ids))
                .count()
                if artifact_ids
                else 0
            ),
        }


async def _write_checkpoint_without_commit(
    database: Database,
    *,
    event_id: str,
    run_id: str,
    checkpoint_path: Path,
) -> None:
    runtime = ReplayResearchRuntime.from_path(FIXTURE)
    request = ResearchRequestFactory(
        database,
        pack_root=PACK_ROOT,
        allowed_capabilities=("replay.research",),
    ).build(event_id, run_id)
    executor = LangGraphResearchExecutor(
        database,
        runtime,
        checkpoint_path=checkpoint_path,
    )
    await executor.execute(request)
    if not await executor.has_checkpoint(run_id):
        raise AssertionError("research checkpoint was not persisted")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="decision-hub-research-recovery-") as raw_dir:
        data_dir = Path(raw_dir)
        checkpoint_path = data_dir / "checkpoints" / "research_graph.sqlite3"
        env_updates = {
            "DECISION_HUB_DATA_DIR": str(data_dir),
            "DECISION_HUB_LLM_ENABLED": "0",
            "DECISION_HUB_RESEARCH_CAPABILITIES": "replay.research",
            "DECISION_HUB_RESEARCH_RUNTIME": "replay",
            "DECISION_HUB_RESEARCH_RUNTIME_FIXTURE": str(FIXTURE),
        }
        worker_env = {**os.environ, **env_updates}
        with patch.dict(os.environ, env_updates, clear=False):
            database = Database()
            database.initialize()
            with TestClient(create_app(database)) as client:
                response = client.post(
                    "/v1/research/observations",
                    headers={"Idempotency-Key": "research-recovery-smoke-v1"},
                    json={
                        "text": "The official event confirms elevated inflation risks.",
                        "source_id": "research-recovery-smoke",
                        "source_type": "transcript",
                        "language": "en",
                    },
                )
            if response.status_code != 202:
                raise AssertionError(f"research admission failed: {response.text}")
            payload = response.json()
            event_id = str(payload["event_id"])
            run_id = str(payload["run_id"])

            claim = RunService(database).claim_next(
                strategy_version="research.v1",
                worker_id="research-recovery-crashed",
                lease_seconds=240,
            )
            if claim is None or claim.run_id != run_id:
                raise AssertionError("research run was not claimed for crash setup")
            asyncio.run(
                _write_checkpoint_without_commit(
                    database,
                    event_id=event_id,
                    run_id=run_id,
                    checkpoint_path=checkpoint_path,
                )
            )
            if _counts(database, run_id)["artifacts"] != 0:
                raise AssertionError("crash setup unexpectedly committed an artifact")
            with database.session() as session:
                run = session.get(RunRecord, run_id)
                if run is None:
                    raise AssertionError("research run disappeared before recovery")
                run.status = RunStatus.running.value
                run.lease_expires_at = utcnow() - timedelta(seconds=1)

            recovered = _run_worker(worker_env)
            if recovered.returncode != 0:
                raise AssertionError(
                    "research worker recovery failed: "
                    f"stdout={recovered.stdout!r} stderr={recovered.stderr!r}"
                )
            if "status=research_only" not in recovered.stdout:
                raise AssertionError(
                    "research worker did not fail closed with a committed research-only result: "
                    f"{recovered.stdout!r}"
                )

            run = database.get_run_record(run_id)
            if run is None or run.status not in {
                RunStatus.completed.value,
                RunStatus.degraded.value,
            }:
                raise AssertionError("recovered run did not reach a terminal status")
            if run.lease_owner is not None or run.lease_expires_at is not None:
                raise AssertionError("recovered run retained its worker lease")
            first_counts = _counts(database, run_id)
            # Every committed research Artifact is owner-visible, including a
            # fail-closed research-only report. Recovery must create exactly one
            # matching outbox row, and the second worker tick must remain idempotent.
            if first_counts != {"artifacts": 1, "evidence": 1, "outbox": 1}:
                raise AssertionError(f"unexpected recovered ledger counts: {first_counts}")

            repeated = _run_worker(worker_env)
            if repeated.returncode != 0 or "status=no_run" not in repeated.stdout:
                raise AssertionError(
                    "second recovery worker did not remain idle: "
                    f"stdout={repeated.stdout!r} stderr={repeated.stderr!r}"
                )
            if _counts(database, run_id) != first_counts:
                raise AssertionError("second worker tick duplicated recovered ledger records")

            print(
                json.dumps(
                    {
                        "status": "passed",
                        "scope": "research_cross_process_checkpoint_recovery",
                        "run_status": run.status,
                        "counts": first_counts,
                        "second_worker": "no_run",
                    },
                    sort_keys=True,
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
