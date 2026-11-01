"""Verify one real local Decision Hub research request through the product API.

Run after ``infra/dsh/run-product.ps1``. This intentionally rejects a healthy
API with an unavailable research worker, and a terminal Run without an Artifact.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid

import requests

TERMINAL = {"completed", "degraded", "research_only", "rejected", "failed", "cancelled"}
REPORT_STATUSES = {"completed", "degraded", "research_only", "rejected"}
CORE_SERVICES = {"hub-api", "hub-research-worker", "hub-research-priority-worker"}


def _json_get(base_url: str, path: str) -> object:
    response = requests.get(base_url + path, timeout=20)
    response.raise_for_status()
    if "application/json" not in response.headers.get("content-type", ""):
        raise RuntimeError(f"{path} returned a non-JSON response")
    return response.json()


def _service_preflight(base_url: str, dsh_url: str, dsh_host_key: str) -> dict[str, object]:
    ready = _json_get(base_url, "/health/ready")
    if not isinstance(ready, dict) or ready.get("status") != "ready":
        raise RuntimeError(f"Hub API is not ready: {ready!r}")
    inbox = _json_get(base_url, "/v1/research/inbox?limit=1")
    if not isinstance(inbox, dict) or inbox.get("schema_version") != "research-inbox-view.v1":
        raise RuntimeError("Hub API is not serving the current research contract")
    overview = _json_get(base_url, "/v1/operations")
    if not isinstance(overview, dict):
        raise RuntimeError("Operations returned an invalid payload")
    runtime = overview.get("runtime")
    if not isinstance(runtime, dict) or runtime.get("runtime_id") != "dsh-web":
        raise RuntimeError(f"Research runtime is not dsh-web: {runtime!r}")
    services = overview.get("services")
    if not isinstance(services, list):
        raise RuntimeError("Operations did not return service heartbeats")
    statuses = {
        item.get("service_id"): item.get("status")
        for item in services
        if isinstance(item, dict)
    }
    offline = {service_id: statuses.get(service_id) for service_id in CORE_SERVICES if statuses.get(service_id) != "online"}
    if offline:
        raise RuntimeError(f"Core service heartbeat is not online: {offline!r}")
    dsh_response = requests.get(
        dsh_url + "/decision-hub/v1/readiness",
        headers={"X-Decision-Hub-Host-Key": dsh_host_key},
        timeout=20,
    )
    dsh_response.raise_for_status()
    dsh_ready = dsh_response.json()
    if not isinstance(dsh_ready, dict) or not all(
        dsh_ready.get(key) is True
        for key in ("ready", "version_compatible", "session_controller", "client_plugin", "hub_reachable")
    ):
        raise RuntimeError(f"DSH Host is not ready: {dsh_ready!r}")
    page = requests.get(base_url + "/", timeout=20)
    page.raise_for_status()
    if "text/html" not in page.headers.get("content-type", ""):
        raise RuntimeError("Decision Desk did not return HTML")
    return {"runtime_id": runtime["runtime_id"], "core_services": statuses}


def _submit(base_url: str) -> str:
    payload = {
        "text": "获取最新操作建议。请研究 BTC 与宏观环境，列出可核验的证据、主要反证和交易权限边界。",
        "source_id": "local-product-acceptance",
        "source_type": "manual",
        "language": "zh",
        "event_hint": "crypto_market_snapshot",
    }
    response = requests.post(
        base_url + "/v1/research/observations",
        headers={"Idempotency-Key": f"local-product-acceptance-{uuid.uuid4().hex}"},
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    if response.status_code != 202:
        raise RuntimeError(f"Research admission returned {response.status_code}")
    data = response.json()
    run_id = data.get("run_id")
    if data.get("status") != "queued" or not isinstance(run_id, str) or not run_id:
        raise RuntimeError(f"Research admission returned an invalid receipt: {data!r}")
    print(f"queued run_id={run_id}", flush=True)
    return run_id


def _verify_terminal(base_url: str, run_id: str, detail: dict[str, object]) -> dict[str, object]:
    run = detail.get("run")
    if not isinstance(run, dict):
        raise RuntimeError("Research detail has no Run")
    status = run.get("status")
    artifact_id = run.get("artifact_id")
    if status not in REPORT_STATUSES or not artifact_id:
        failure = run.get("failure")
        raise RuntimeError(f"Research did not commit a report: status={status}, failure={failure!r}")
    evidence = detail.get("evidence")
    trace = detail.get("trace")
    summary = detail.get("action_summary")
    if not isinstance(evidence, list) or not evidence:
        raise RuntimeError("Committed report has no inspectable Evidence")
    if not isinstance(trace, list) or not trace:
        raise RuntimeError("Committed report has no normalized research trace")
    if not isinstance(summary, dict) or not summary.get("headline"):
        raise RuntimeError("Committed report has no action summary")
    run_view = _json_get(base_url, f"/v1/runs/{run_id}/view")
    if not isinstance(run_view, dict) or not isinstance(run_view.get("artifact"), dict):
        raise RuntimeError("Run view cannot retrieve the committed Artifact")
    artifact = run_view["artifact"]
    if artifact.get("artifact_id") != artifact_id:
        raise RuntimeError("Research and Run views disagree on Artifact identity")
    return {
        "run_id": run_id,
        "status": status,
        "artifact_id": artifact_id,
        "evidence_count": len(evidence),
        "trace_count": len(trace),
        "headline": summary["headline"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8047")
    parser.add_argument("--dsh-url", default="http://127.0.0.1:3097")
    parser.add_argument(
        "--dsh-host-key",
        default=os.getenv("DECISION_HUB_DSH_HOST_SECRET", "decision-hub-local-host-key"),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--submit", action="store_true", help="Submit one new live research request")
    source.add_argument("--run-id", help="Verify a previously submitted Run without creating another")
    parser.add_argument("--timeout-seconds", type=int, default=2100)
    parser.add_argument("--poll-seconds", type=int, default=5)
    args = parser.parse_args()
    base_url = args.api_url.rstrip("/")
    try:
        preflight = _service_preflight(base_url, args.dsh_url.rstrip("/"), args.dsh_host_key)
        print(json.dumps({"preflight": "passed", "runtime_id": preflight["runtime_id"]}), flush=True)
        run_id = _submit(base_url) if args.submit else args.run_id
        assert run_id is not None
        deadline = time.monotonic() + args.timeout_seconds
        previous = None
        while time.monotonic() < deadline:
            detail = _json_get(base_url, f"/v1/research/runs/{run_id}")
            if not isinstance(detail, dict):
                raise RuntimeError("Research detail returned an invalid payload")
            run = detail.get("run")
            if not isinstance(run, dict) or run.get("run_id") != run_id:
                raise RuntimeError("Research detail returned the wrong Run")
            state = (run.get("status"), run.get("stage"), len(detail.get("evidence", [])))
            if state != previous:
                print(f"run_id={run_id} status={state[0]} stage={state[1]} evidence={state[2]}", flush=True)
                previous = state
            if run.get("status") in TERMINAL:
                result = _verify_terminal(base_url, run_id, detail)
                # Keep the machine-readable result intact on Windows consoles
                # whose active code page cannot represent the Chinese headline.
                print(json.dumps({"acceptance": "passed", **result}, ensure_ascii=True), flush=True)
                return 0
            time.sleep(args.poll_seconds)
        raise RuntimeError(f"Research Run {run_id} did not reach a terminal status within {args.timeout_seconds}s")
    except (requests.RequestException, RuntimeError) as exc:
        print(f"acceptance failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
