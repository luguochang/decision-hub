from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run(command: list[str]) -> None:
    print("$", " ".join(command))
    completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def _worker_preflight_smoke() -> None:
    """Prove a pilot worker refuses to start without external capabilities."""

    with tempfile.TemporaryDirectory(prefix="decision-hub-pilot-") as temp_dir:
        environment = os.environ.copy()
        environment.update(
            {
                "DECISION_HUB_DATA_DIR": temp_dir,
                "DECISION_HUB_PILOT_MODE": "1",
                "DECISION_HUB_LLM_ENABLED": "0",
                "DECISION_HUB_SOURCES_ENABLED": "0",
                "DECISION_HUB_MARKET_ENABLED": "0",
            }
        )
        for name in (
            "OPENAI_API_KEY",
            "DEEPSEEK_API_KEY",
            "SUB2API_API_KEY",
            "OKX_API_KEY",
            "OKX_SECRET_KEY",
            "OKX_PASSPHRASE",
            "BINANCE_API_KEY",
            "BINANCE_SECRET_KEY",
            "BYBIT_API_KEY",
            "BYBIT_API_SECRET",
            "DECISION_HUB_TRADING_PRIVATE_KEY",
            "EXCHANGE_PRIVATE_KEY",
        ):
            environment.pop(name, None)
        for mode in (("--preflight",), ("--pilot", "--once")):
            completed = subprocess.run(
                [sys.executable, "-m", "apps.hub_worker.main", *mode],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
            )
            if completed.returncode == 0:
                raise SystemExit(f"pilot worker {mode} unexpectedly passed without capabilities")
            try:
                report = json.loads(completed.stdout)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"pilot worker {mode} did not emit JSON") from exc
            if report.get("status") != "not_ready":
                raise SystemExit(f"pilot worker {mode} did not fail closed")
            if "OPENAI_API_KEY" in completed.stdout or "Authorization" in completed.stdout:
                raise SystemExit("pilot worker leaked a secret marker")
        print(
            json.dumps(
                {"worker_preflight": "fail_closed", "pilot_gate": "fail_closed"},
                sort_keys=True,
            )
        )


def _worker_ready_preflight_smoke() -> None:
    """Prove a complete local configuration is reported ready without network calls."""

    with tempfile.TemporaryDirectory(prefix="decision-hub-pilot-ready-") as temp_dir:
        environment = os.environ.copy()
        environment.update(
            {
                "DECISION_HUB_DATA_DIR": temp_dir,
                "DECISION_HUB_PILOT_MODE": "1",
                "DECISION_HUB_LLM_ENABLED": "1",
                "DECISION_HUB_LLM_API_MODE": "responses",
                "DECISION_HUB_PROVIDER_ID": "fixture-provider",
                "DECISION_HUB_MODEL": "fixture-model",
                "DECISION_HUB_SOURCES_ENABLED": "1",
                "DECISION_HUB_MARKET_ENABLED": "1",
                "DECISION_HUB_NOTIFICATION_CHANNEL": "local",
                "OPENAI_API_KEY": "fixture-key-only",
            }
        )
        for name in (
            "DEEPSEEK_API_KEY",
            "SUB2API_API_KEY",
            "OKX_API_KEY",
            "OKX_SECRET_KEY",
            "OKX_PASSPHRASE",
            "BINANCE_API_KEY",
            "BINANCE_SECRET_KEY",
            "BYBIT_API_KEY",
            "BYBIT_API_SECRET",
            "DECISION_HUB_TRADING_PRIVATE_KEY",
            "EXCHANGE_PRIVATE_KEY",
        ):
            environment.pop(name, None)
        completed = subprocess.run(
            [sys.executable, "-m", "apps.hub_worker.main", "--preflight"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise SystemExit("complete local pilot configuration was not ready")
        try:
            report = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise SystemExit("ready pilot preflight did not emit JSON") from exc
        if report.get("status") != "ready":
            raise SystemExit("ready pilot preflight returned a non-ready report")
        if "fixture-key-only" in completed.stdout:
            raise SystemExit("ready pilot preflight leaked the fixture key")
        print(json.dumps({"worker_preflight": "ready"}, sort_keys=True))


def main() -> int:
    commands = [
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/pilot",
            "tests/e2e/test_outbox_worker.py",
            "tests/e2e/test_realtime_source_flow.py",
            "tests/replay/test_checkpoint.py",
            "-q",
        ],
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "packages",
            "apps",
            "migrations",
            "tests",
            "tools",
        ],
        [sys.executable, "-m", "pyright", "packages", "apps", "tests", "tools/canary"],
        [sys.executable, "-m", "tools.contract_codegen", "check"],
        [sys.executable, "tools/docs/check_module_docs.py"],
    ]
    try:
        for command in commands:
            _run(command)
        _worker_preflight_smoke()
        _worker_ready_preflight_smoke()
        # Reuse the existing R0 durability/replay gate instead of creating a second one.
        from tools.core_acceptance import _durability_and_replay_smoke

        _durability_and_replay_smoke()
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
