"""Bounded real-provider evolution probe on a new snapshot, never on the live DB."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values

from apps.hub_worker.composition import build_evolution_worker
from packages.kernel.decision_hub_kernel.application.live_observation import EvolutionJobPolicy
from packages.kernel.decision_hub_kernel.persistence.db import Database, EvolutionJobRecord
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.langgraph_agent.evolution import EvolutionPlanningRuntime
from packages.runtime_adapters.langgraph_agent.runtime import LangGraphAgentRuntime


class MeteredRuntime:
    def __init__(self, runtime, calls):
        self.runtime = runtime
        self.calls = calls
        self.runtime_id = runtime.runtime_id
        self.runtime_version = runtime.runtime_version
        self.max_attempts = runtime.max_attempts
        self.cost_budget = runtime.cost_budget
        self.enabled = True

    async def execute(self, request):
        if len(self.calls) >= 30:
            raise AgentExecutionError("probe_call_limit", "bounded probe exhausted")
        entry = {"role": request.role, "started_at": datetime.now(UTC).isoformat()}
        self.calls.append(entry)
        try:
            result = await self.runtime.execute(request)
        except AgentExecutionError as exc:
            entry["error_code"] = exc.error_code
            entry["cause_code"] = exc.cause_code
            cause = exc.__cause__
            entry["cause_type"] = type(cause).__name__ if cause else None
            entry["http_status"] = getattr(cause, "status_code", None)
            # Record only classification, never provider response/request bodies.
            body = getattr(cause, "body", None)
            if isinstance(body, dict):
                error = body.get("error", body)
                if isinstance(error, dict):
                    entry["provider_error_code"] = error.get("code")
                    entry["provider_error_param"] = error.get("param")
            raise
        entry.update(
            latency_ms=result.latency_ms,
            usage=asdict(result.usage),
            runtime_id=result.runtime_id,
            payload=result.payload,
        )
        return result


async def probe(args):
    source = args.database.resolve()
    destination = args.output_dir.resolve()
    if not source.is_file() or destination.exists():
        raise ValueError("existing_database_and_new_output_directory_required")
    config = dotenv_values(args.provider_env)
    if not config.get("OPENAI_API_KEY"):
        raise ValueError("configured_provider_key_required")
    for key in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "DECISION_HUB_MODEL",
        "DECISION_HUB_LLM_API_MODE",
    ):
        if config.get(key):
            os.environ[key] = config[key]
    os.environ.update(
        DECISION_HUB_LLM_ENABLED="1",
        DECISION_HUB_MAX_RETRIES="0",
        DECISION_HUB_TIMEOUT_SECONDS="45",
        DECISION_HUB_TOKEN_BUDGET="2500",
        DECISION_HUB_DATA_DIR=str(destination / "artifacts"),
    )
    destination.mkdir(parents=True)
    copy = destination / "snapshot.sqlite3"
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original:
        with sqlite3.connect(copy) as snapshot:
            original.backup(snapshot)
    db = Database(f"sqlite+pysqlite:///{copy.as_posix()}")
    # Isolated snapshot only; include newly added diagnostic tables.
    db.create_all()
    calls = []
    proof = {
        "started_at": datetime.now(UTC).isoformat(),
        "isolated_database": str(copy),
        "source_database": str(source),
        "mode": "real_provider",
        "calls": calls,
        "formal_database_mutated": False,
        "promotion_requested": False,
    }
    try:
        worker = build_evolution_worker(
            db,
            runtime=MeteredRuntime(LangGraphAgentRuntime(), calls),
            planning_runtime=MeteredRuntime(EvolutionPlanningRuntime(), calls),
            policy=EvolutionJobPolicy(
                failure_occurrence_threshold=1000000,
                evaluation_batch_size=1000000,
                scheduled_scan_enabled=False,
                max_attempts=3 if args.job_id else 1,
            ),
            heartbeat_interval_seconds=2,
        )
        before = worker.executor.assets.active_pointer("crypto_macro.v1")
        proof["pointer_before"] = before.model_dump(mode="json") if before else None
        target_job_id = args.job_id
        if args.job_id:
            # Only the disposable snapshot is changed; the failed live record stays intact.
            with db.session() as session:
                target = session.get(EvolutionJobRecord, args.job_id)
                if target is None or target.status != "failed" or target.candidate_id:
                    raise ValueError("probe_requires_failed_job_without_candidate")
                proof["original_job"] = {
                    "job_id": target.job_id,
                    "attempt": target.attempt,
                    "status": target.status,
                    "error_code": target.last_error_code,
                }
                if not args.context_recovery:
                    target.status, target.finished_at, target.last_error_code = "queued", None, None
            if args.context_recovery:
                target_job_id = worker.executor.jobs.enqueue_context_recovery(args.job_id).job_id
        worker.executor.scanner.scan()
        pending = [
            job
            for job in worker.executor.jobs.list_jobs()
            if job.status in {"queued", "running", "retry_wait"}
        ]
        matching = (
            (pending and pending[-1].job_id == target_job_id)
            if args.job_id
            else (pending and pending[-1].input_refs == [f"feedback:{args.feedback_id}"])
        )
        if not matching:
            raise ValueError("probe_requires_oldest_matching_feedback_job")
        async with asyncio.timeout(240):
            result = await worker.tick()
        proof["job"] = result.model_dump(mode="json") if result else None
        proof["overview"] = worker.executor.assets.overview().model_dump(mode="json")
        after = worker.executor.assets.active_pointer("crypto_macro.v1")
        proof["pointer_unchanged"] = after == before
    except AgentExecutionError as exc:
        proof["error_code"] = exc.error_code
    except TimeoutError:
        proof["error_code"] = "probe_deadline_exceeded"
    except Exception as exc:
        # No provider response text, credentials, or endpoint URLs in console/proof errors.
        proof["error_code"] = type(exc).__name__
    finally:
        proof["finished_at"] = datetime.now(UTC).isoformat()
        (destination / "proof.json").write_text(
            json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "proof": str(destination / "proof.json"),
                "status": (proof.get("job") or {}).get("status"),
                "error": proof.get("error_code"),
                "calls": len(calls),
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--provider-env", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--context-recovery", action="store_true")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--feedback-id")
    target.add_argument("--job-id")
    asyncio.run(probe(parser.parse_args()))


if __name__ == "__main__":
    main()
