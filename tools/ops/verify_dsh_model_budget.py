"""Read the actual Host watchdog and compare it with one durable Run link."""

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import httpx

from packages.contracts_py.decision_hub_contracts import DshSessionStatus
from packages.runtime_adapters.dsh_runtime.web_host_client import DshWebHostConfig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source", choices=["submission", "durable_link", "legacy_default"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.database.is_file() or args.output.exists():
        raise ValueError("existing_database_and_new_output_required")
    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT run_id,dsh_session_id,generation,request_hash,model_step_timeout_ms "
            "FROM dsh_session_links WHERE run_id=?", (args.run_id,),
        ).fetchone()
    if row is None:
        raise ValueError("durable_session_missing")
    config = DshWebHostConfig.from_env()
    with httpx.Client(trust_env=False, timeout=15) as client:
        response = client.get(
            config.base_url.rstrip("/") + "/decision-hub/v1/runs/" + args.run_id,
            headers={"X-Decision-Hub-Host-Key": config.bridge_key},
        )
    if response.status_code != 200:
        raise ValueError(f"host_status_http_{response.status_code}")
    status = DshSessionStatus.model_validate(response.json())
    saved = dict(row)
    expected = saved["model_step_timeout_ms"]
    checks = {
        "run_matches": status.run_id == saved["run_id"],
        "session_matches": status.dsh_session_id == saved["dsh_session_id"],
        "generation_matches": status.generation == saved["generation"],
        "budget_matches": status.model_step_timeout_ms == (expected or 150_000),
        "source_matches": status.model_step_timeout_source == args.source if args.source else (
            status.model_step_timeout_source in (
                {"legacy_default"} if expected is None else {"submission", "durable_link"}
            )
        ),
    }
    proof = {
        "checked_at": datetime.now(UTC).isoformat(), "durable_link": saved,
        "host": status.model_dump(mode="json"), "checks": checks,
        "legacy_budget_unknown": expected is None,
        "scope": "watchdog ceiling and correlation only; no Provider attestation",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(proof, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"saved": str(args.output), "checks": checks}))
    if not all(checks.values()):
        raise SystemExit("host_watchdog_verification_failed")


if __name__ == "__main__":
    main()
