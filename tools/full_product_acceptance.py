"""Start the Windows product if needed and run the complete local acceptance gate.

This combines the existing unit/integration suites with live DSH, API, UI,
document and research checks. It preserves durable data and reuses the labelled
document fixture's idempotency key on repeated invocations.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import requests

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "data" / "product-local"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
API = "http://127.0.0.1:8047"
DSH = "http://127.0.0.1:3097"


def product_ready() -> bool:
    try:
        api = requests.get(API + "/health/ready", timeout=3)
        dsh = requests.get(
            DSH + "/decision-hub/v1/readiness",
            headers={"X-Decision-Hub-Host-Key": "decision-hub-local-host-key"},
            timeout=3,
        )
        acquisition = requests.get("http://127.0.0.1:8038/health/ready", timeout=3)
        operations = requests.get(API + "/v1/operations", timeout=3)
        services = {item["service_id"]: item["status"] for item in operations.json()["services"]}
        # Match the seven roles in OperationsQueryService.EXPECTED_SERVICES.
        expected = {
            "hub-api",
            "hub-realtime-worker",
            "hub-research-worker",
            "hub-research-priority-worker",
            "hub-documents",
            "hub-historical-worker",
            "hub-evolution-worker",
        }
        dsh_state = dsh.json()
        with socket.create_connection(("127.0.0.1", 8037), timeout=3):
            pass
        return (
            api.status_code == 200
            and acquisition.status_code == 200
            and operations.status_code == 200
            and dsh.status_code == 200
            and all(
                dsh_state.get(key) is True
                for key in (
                    "ready",
                    "version_compatible",
                    "session_controller",
                    "client_plugin",
                    "hub_reachable",
                )
            )
            and operations.json()["runtime"]["runtime_id"] == "dsh-web"
            and all(services.get(service) == "online" for service in expected)
        )
    except (requests.RequestException, ValueError, KeyError, OSError):
        return False


def ensure_product() -> str:
    if product_ready():
        print("product startup: existing instance ready", flush=True)
        return "reused"
    # A degraded managed product keeps report access. Let its owner repair
    # individual roles instead of tearing down a healthy API on every check.
    supervisor_file = RUNTIME / "supervisor.json"
    if supervisor_file.is_file():
        state = json.loads(supervisor_file.read_text(encoding="utf-8-sig"))
        if state.get("state") in {"running", "starting"}:
            check = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    ". ./infra/dsh/product-processes.ps1; "
                    "$s=Get-Content data/product-local/supervisor.json -Raw | ConvertFrom-Json; "
                    "if (Test-ProductProcessIdentity $s) { exit 0 } else { exit 1 }",
                ],
                cwd=ROOT,
                capture_output=True,
                timeout=15,
                check=False,
            )
            if check.returncode == 0:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if product_ready():
                        print("product startup: managed roles recovered", flush=True)
                        return "recovered"
                    time.sleep(2)
                raise RuntimeError(
                    "Managed product is degraded; API and logs were preserved. "
                    "Inspect processes.json and Operations before restarting roles."
                )
    RUNTIME.mkdir(parents=True, exist_ok=True)
    stop = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "infra/dsh/stop-product.ps1"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    if stop.returncode != 0:
        raise RuntimeError(f"Managed product stop failed: {stop.stderr[-600:]}")
    started_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    log_path = RUNTIME / f"acceptance-launcher-{started_at}-{uuid4().hex}.log"
    print(f"product startup: launcher log {log_path.resolve()}", flush=True)
    # Exclusive creation also protects a prior attempt if a name ever collides.
    with log_path.open("x", encoding="utf-8") as log:
        launcher = subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ROOT / "infra/dsh/run-product.ps1"),
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        if product_ready():
            print("product startup: launched and ready", flush=True)
            return "launched"
        if launcher.poll() is not None:
            raise RuntimeError(
                f"Product launcher exited with {launcher.returncode}; inspect {log_path}"
            )
        time.sleep(2)
    raise RuntimeError(f"Product did not become ready within 240s; inspect {log_path}")


def run_gate(
    name: str, command: list[str], *, env: dict[str, str] | None = None, timeout: int = 2400
) -> dict[str, object]:
    started = time.monotonic()
    print(f"running {name}", flush=True)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        check=False,
    )
    output = (completed.stdout + "\n" + completed.stderr).strip()
    result: dict[str, object] = {
        "name": name,
        "status": "passed" if completed.returncode == 0 else "failed",
        "exit_code": completed.returncode,
        "seconds": round(time.monotonic() - started, 1),
        "tail": "\n".join(output.splitlines()[-8:]),
    }
    print(f"{name}: {result['status']} ({result['seconds']}s)", flush=True)
    if completed.returncode != 0:
        print(result["tail"], file=sys.stderr, flush=True)
    return result


def main() -> int:
    if os.name != "nt":
        raise RuntimeError("The full product launcher is currently Windows-specific")
    if not PYTHON.is_file():
        raise RuntimeError(f"Missing project Python environment: {PYTHON}")
    startup = ensure_product()
    py = str(PYTHON)
    pnpm = shutil.which("pnpm.cmd")
    node = shutil.which("node.exe")
    if not pnpm or not node:
        raise RuntimeError("Node.js and pnpm are required for the browser and frontend gates")
    steps: list[dict[str, object]] = []
    standard = [
        ("web-build", [pnpm, "web:build"]),
        ("dsh-plugin-build", [pnpm, "--dir", "extensions/dsh/decision-hub", "build"]),
        ("python-all", [py, "-m", "pytest", "-q"]),
        ("web-tests", [pnpm, "web:test"]),
        ("dsh-plugin-tests", [pnpm, "--dir", "extensions/dsh/decision-hub", "test"]),
        ("acquisition-typecheck", [pnpm, "--dir", "apps/source_acquisition_worker", "typecheck"]),
        ("acquisition-browser-smoke", [pnpm, "--dir", "apps/source_acquisition_worker", "smoke"]),
        ("research-recovery", [py, "-m", "tools.research_recovery_smoke"]),
        ("document-flow", [py, "-m", "tools.product_document_acceptance", "--start-research"]),
        ("live-api-matrix", [py, "-m", "tools.product_api_smoke"]),
        ("live-browser-matrix", [node, "tools/product_ui_smoke.mjs"]),
    ]
    document_run_id: str | None = None
    document_version_id: str | None = None
    for name, command in standard:
        step = run_gate(name, command)
        steps.append(step)
        if name == "document-flow" and step["status"] == "passed":
            try:
                document_result = json.loads(str(step["tail"]).splitlines()[-1])
                document_run_id = document_result["research_run_id"]
                document_version_id = document_result["version_id"]
            except (KeyError, ValueError, TypeError):
                step["status"] = "failed"
                step["tail"] = "Document flow did not return a research Run ID"
        if step["status"] != "passed":
            break
    if all(step["status"] == "passed" for step in steps) and document_run_id:
        steps.append(
            run_gate(
                "live-library-research",
                [
                    py,
                    "-m",
                    "tools.local_product_acceptance",
                    "--run-id",
                    document_run_id,
                    "--timeout-seconds",
                    "2100",
                ],
                timeout=2250,
            )
        )
        if steps[-1]["status"] == "passed":
            steps.append(
                run_gate(
                    "fixture-evidence-exclusion",
                    [
                        py,
                        "-m",
                        "tools.product_fixture_exclusion",
                        "--run-id",
                        document_run_id,
                        "--version-id",
                        document_version_id,
                    ],
                )
            )
        if steps[-1]["status"] == "passed":
            browser_env = dict(os.environ, DECISION_HUB_RUN_ID=document_run_id)
            steps.append(
                run_gate(
                    "live-browser-result", [node, "tools/product_ui_smoke.mjs"], env=browser_env
                )
            )
    status = "passed" if steps and all(step["status"] == "passed" for step in steps) else "failed"
    result = {
        "status": status,
        "checked_at": datetime.now(UTC).isoformat(),
        "startup": startup,
        "research_run_id": document_run_id,
        "steps": steps,
    }
    RUNTIME.mkdir(parents=True, exist_ok=True)
    report_path = RUNTIME / "full-acceptance-latest.json"
    report_path.write_text(json.dumps(result, ensure_ascii=True, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": status,
                "startup": startup,
                "research_run_id": document_run_id,
                "steps": len(steps),
                "report": str(report_path),
            }
        ),
        flush=True,
    )
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"full product acceptance failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
