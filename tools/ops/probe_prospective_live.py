"""Observe new live inputs in an isolated registered comparison; at most two model calls."""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import dotenv_values

from apps.hub_worker.prospective import ProspectiveWorker
from packages.evals.prospective_pairs import ProspectivePairService
from packages.evals.prospective_trials import ProspectiveTrialService, TrialRequest
from packages.kernel.decision_hub_kernel.application.evolution import EvolutionAssetService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.orchestration.langgraph.evolution_executor import CandidateArtifactStore
from packages.runtime_adapters.candidate_runtime import CandidateConfigurationRuntime
from packages.runtime_adapters.langgraph_agent.runtime import LangGraphAgentRuntime


def mirror_future_inputs(source: Path, target: Path, starts_at: datetime):
    """Copy exact source rows from one read snapshot; never rewrite their timestamps."""
    copied = []
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src:
        src.row_factory = sqlite3.Row
        src.execute("BEGIN")
        with sqlite3.connect(target) as dst:

            def copy_row(table, row, key):
                if row is None:
                    raise ValueError("probe_source_lineage_missing")
                data = dict(row)
                old = dst.execute(
                    f'SELECT * FROM "{table}" WHERE "{key}"=?', (data[key],)
                ).fetchone()
                if old:
                    if tuple(data.values()) != old:
                        raise ValueError("probe_source_row_changed")
                    return
                names = ",".join('"' + name + '"' for name in data)
                marks = ",".join("?" for _ in data)
                dst.execute(
                    f'INSERT INTO "{table}" ({names}) VALUES ({marks})', tuple(data.values())
                )
                copied.append(
                    {
                        "table": table,
                        "id": data[key],
                        "row_hash": hashlib.sha256(
                            json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
                        ).hexdigest(),
                    }
                )

            for outlook in src.execute(
                "SELECT * FROM analytical_outlooks WHERE recorded_at>=? "
                "ORDER BY recorded_at,outlook_id",
                (starts_at.replace(tzinfo=None).isoformat(sep=" "),),
            ).fetchall():
                refs = src.execute(
                    "SELECT * FROM run_input_refs WHERE run_id=?", (outlook["run_id"],)
                ).fetchall()
                for ref in refs:
                    version = src.execute(
                        "SELECT * FROM fact_versions WHERE fact_version_id=?",
                        (ref["fact_version_id"],),
                    ).fetchone()
                    parsed = src.execute(
                        "SELECT * FROM parsed_versions WHERE parsed_version_id=?",
                        (version["parsed_version_id"],),
                    ).fetchone()
                    copy_row("parsed_versions", parsed, "parsed_version_id")
                    for identity in json.loads(parsed["capture_ids_json"]):
                        copy_row(
                            "raw_captures",
                            src.execute(
                                "SELECT * FROM raw_captures WHERE capture_id=?",
                                (identity,),
                            ).fetchone(),
                            "capture_id",
                        )
                    copy_row("fact_versions", version, "fact_version_id")
                    copy_row("run_input_refs", ref, "fact_id")
                copy_row(
                    "publication_decisions",
                    src.execute(
                        "SELECT * FROM publication_decisions WHERE artifact_id=?",
                        (outlook["artifact_id"],),
                    ).fetchone(),
                    "artifact_id",
                )
                copy_row("analytical_outlooks", outlook, "outlook_id")
    return copied


async def probe(args):
    source, output = args.database.resolve(), args.output_dir.resolve()
    if output.exists() or not source.is_file():
        raise ValueError("existing_source_and_new_output_required")
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
        DECISION_HUB_TIMEOUT_SECONDS="60",
        DECISION_HUB_TOKEN_BUDGET="2500",
        DECISION_HUB_DATA_DIR=str(output / "artifacts"),
    )
    output.mkdir(parents=True)
    target = output / "snapshot.sqlite3"
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)
    db = Database(f"sqlite+pysqlite:///{target.as_posix()}")
    assets = EvolutionAssetService(db)
    baseline = assets.ensure_release_baseline("crypto_macro.v1")
    old = Database(
        f"sqlite+pysqlite:///file:{args.candidate_database.resolve().as_posix()}?mode=ro&uri=true"
    )
    candidate = EvolutionAssetService(old).get_candidate(args.candidate_id)
    if candidate is None or not candidate.content_ref:
        raise ValueError("existing_candidate_required")
    original = Path(candidate.content_ref)
    if hashlib.sha256(original.read_bytes()).hexdigest() != candidate.content_hash:
        raise ValueError("candidate_hash_mismatch")
    store = CandidateArtifactStore(output / "artifacts" / "evolution")
    destination = store.root / "candidates" / original.name
    destination.parent.mkdir(parents=True)
    shutil.copyfile(original, destination)
    candidate = candidate.model_copy(
        update={"content_ref": str(destination), "status": "candidate"},
    )
    assets.register_candidate(candidate)
    calls = []
    base = LangGraphAgentRuntime()

    class Metered:
        runtime_id, runtime_version = base.runtime_id, base.runtime_version
        max_attempts, cost_budget = base.max_attempts, base.cost_budget

        def execution_recipe(self):
            return base.execution_recipe()

        async def execute(self, request):
            if len(calls) >= 2:
                raise ValueError("probe_call_limit")
            entry = {
                "started_at": datetime.now(UTC).isoformat(),
                "deadline_at": request.deadline_at.isoformat(),
            }
            calls.append(entry)
            try:
                result = await base.execute(request)
                entry.update(
                    runtime_id=result.runtime_id,
                    model=result.model,
                    provider_id=result.provider_id,
                    latency_ms=result.latency_ms,
                    usage=asdict(result.usage),
                )
                return result
            finally:
                entry["finished_at"] = datetime.now(UTC).isoformat()

    runtime = Metered()
    challenger = CandidateConfigurationRuntime(runtime, store.load_candidate(candidate))
    trials = ProspectiveTrialService(db, artifact_root=store.root)
    starts = datetime.now(UTC) + timedelta(seconds=5)
    registered = trials.register(
        TrialRequest(
            request_id=output.name,
            baseline_candidate_id=baseline.candidate_id,
            candidate_id=candidate.candidate_id,
            horizon="30m",
            starts_at=starts,
            ends_at=starts + timedelta(days=31),
            pair_deadline_seconds=180,
        ),
        runtime,
        challenger,
    )
    proof = {
        "mode": "real_provider_forward_input",
        "source_database_read_only": True,
        "registration": registered.model_dump(mode="json"),
        "calls": calls,
        "copied_rows": [],
        "promotion_requested": False,
        "isolated_database": str(target),
        "source_database": str(source),
    }
    (output / "ready.json").write_text(
        json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "registered_waiting_new_input",
                "trial_id": registered.trial_id,
                "starts_at": starts.isoformat(),
            }
        ),
        flush=True,
    )
    pairs = ProspectivePairService(db, trials, data_roots=[source.parent.parent, output])
    worker = ProspectiveWorker(pairs, registered.trial_id, runtime, challenger)
    stop = asyncio.get_running_loop().time() + args.watch_seconds
    try:
        while asyncio.get_running_loop().time() < stop:
            proof["copied_rows"].extend(mirror_future_inputs(source, target, starts))
            result = await worker.tick()
            if result["status"] != "no_input":
                proof["pair"] = pairs.get(result["pair_id"])
                break
            await asyncio.sleep(2)
        else:
            proof["error_code"] = "no_forward_input_within_probe_window"
    except Exception as exc:
        proof["error_code"] = type(exc).__name__
    finally:
        proof["finished_at"] = datetime.now(UTC).isoformat()
        proof["pointer_unchanged"] = assets.active_pointer("crypto_macro.v1") == baseline
        (output / "proof.json").write_text(
            json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "status": proof.get("pair", {}).get("status"),
                "error": proof.get("error_code"),
                "calls": len(calls),
                "proof": str(output / "proof.json"),
            }
        ),
        flush=True,
    )

    return 0 if (
        proof.get("pair", {}).get("status") == "predictions_saved"
        and not proof.get("error_code")
        and proof["pointer_unchanged"]
        and len(calls) == 2
    ) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--candidate-database", type=Path, required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--provider-env", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--watch-seconds", type=int, default=1200)
    args = parser.parse_args()
    if not 10 <= args.watch_seconds <= 1800:
        parser.error("watch-seconds must be between 10 and 1800")
    return asyncio.run(probe(args))


if __name__ == "__main__":
    raise SystemExit(main())
