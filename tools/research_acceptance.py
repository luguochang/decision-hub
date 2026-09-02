"""Offline acceptance gate for the R2-R durable research worker boundary."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(command: list[str]) -> None:
    print("$", " ".join(command))
    completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def main() -> int:
    commands = [
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/research/test_discovery_policy.py",
            "tests/evolution/test_research_worker.py",
            "tests/evolution/test_worker_processes.py",
            "tests/e2e/test_api_flow.py",
            "-q",
        ],
        [sys.executable, "-m", "tools.contract_codegen", "check"],
        [sys.executable, "tools/docs/check_module_docs.py"],
        [sys.executable, "-m", "tools.research_recovery_smoke"],
    ]
    try:
        for command in commands:
            _run(command)
        _run(["docker", "compose", "config", "--quiet"])
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1
    print(
        json.dumps(
            {
                "status": "passed",
                "scope": "offline_r2r_durable_research_boundary",
                "network": "disabled",
                "compose_services": [
                    "hub-api",
                    "hub-realtime-worker",
                    "hub-research-worker",
                    "hub-evolution-worker",
                    "research-mcp",
                ],
                "live_provider": "not_run",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
