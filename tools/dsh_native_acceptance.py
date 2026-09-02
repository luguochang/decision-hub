from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "bin" / "python"
RUN_ROOT = ROOT / "tmp" / "dsh-native-core"
UPSTREAM = ROOT / ".cache" / "dsh-upstream" / "source"
UPSTREAM_LOCK = json.loads((ROOT / "infra" / "dsh" / "upstream.lock.json").read_text())
REPLAY_PLUGIN = UPSTREAM / "packages" / "test-support" / "llm-replay"
REPLAY_PATCH = ROOT / "infra" / "dsh" / "presets" / "decision-research" / "replay.patch.yml"
HOST_KEY = "dsh-native-acceptance-host"
CALLBACK_KEY = "dsh-native-acceptance-callback"
RESEARCH_TOOL_KEY = "dsh-native-acceptance-research-tool"

Scenario = Literal["success", "partial_failure", "insufficient_or_stale"]
SCENARIOS: tuple[Scenario, ...] = (
    "success",
    "partial_failure",
    "insufficient_or_stale",
)


@dataclass
class ManagedProcess:
    name: str
    process: subprocess.Popen[str]
    log_path: Path
    log_handle: object

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        close = getattr(self.log_handle, "close", None)
        if callable(close):
            close()

    def wait(self, *, timeout: float) -> int:
        code = self.process.wait(timeout=timeout)
        close = getattr(self.log_handle, "close", None)
        if callable(close):
            close()
        return code


@dataclass(frozen=True)
class ScenarioResult:
    scenario: Scenario
    run_id: str
    dsh_session_id: str
    run_status: str
    gate_status: str | None
    coverage_status: str | None
    hard_coverage_ratio: float | None
    stop_reason: str | None
    evidence_count: int
    tool_failure_count: int
    session_log: str
    session_log_sha256: str
    output_dir: str


class ScenarioRuntime:
    def __init__(
        self,
        scenario: Scenario,
        root: Path,
        *,
        replay_pace_ms: int = 0,
        callback_timeout_ms: int = 5_000,
        callback_attempts: int = 3,
    ) -> None:
        self.scenario: Scenario = scenario
        self.root = root
        self.data_dir = root / "hub-data"
        self.web_home = root / "dsh-home"
        self.workspace = root / "Crypto Macro Trader"
        self.artifacts = root / "artifacts"
        self.logs = root / "logs"
        self.api_port = _free_port()
        self.mcp_port = _free_port()
        self.web_port = _free_port()
        self.processes: list[ManagedProcess] = []
        self.authenticated_url: str | None = None
        self.replay_pace_ms = replay_pace_ms
        self.callback_timeout_ms = callback_timeout_ms
        self.callback_attempts = callback_attempts
        self._common_env: dict[str, str] = {}
        self._api_env: dict[str, str] = {}
        self._mcp_env: dict[str, str] = {}
        self._web_env: dict[str, str] = {}
        self._web_command: list[str] = []
        self._archive_path: Path | None = None
        self._restart_generation = 0

    def start(self) -> None:
        for path in (self.data_dir, self.web_home, self.workspace, self.artifacts, self.logs):
            path.mkdir(parents=True, exist_ok=True)
        snapshot, override, archive = _write_scenario_fixtures(self.scenario, self.root)
        self._archive_path = archive
        self._common_env = {
            **os.environ,
            "PYTHONPATH": str(ROOT),
            "DECISION_HUB_DATA_DIR": str(self.data_dir),
            "DECISION_HUB_DSH_CALLBACK_SECRET": CALLBACK_KEY,
            "DECISION_HUB_DSH_RESEARCH_TOOL_KEY": RESEARCH_TOOL_KEY,
            "DECISION_HUB_LLM_ENABLED": "0",
            "DECISION_HUB_SOURCES_ENABLED": "0",
            "DECISION_HUB_MARKET_ENABLED": "0",
        }
        self._api_env = {
            **self._common_env,
            "HUB_API_PORT": str(self.api_port),
            "DECISION_HUB_INSTANCE_ID": "native-acceptance-api",
        }
        self._start_hub_api()
        _wait_http(f"http://127.0.0.1:{self.api_port}/health/ready", timeout=45)
        self._mcp_env = {
            **self._common_env,
            "DECISION_HUB_RESEARCH_MCP_PORT": str(self.mcp_port),
            "DECISION_HUB_RESEARCH_CAPABILITIES": "replay.research",
            "DECISION_HUB_RESEARCH_REPLAY_FIXTURES": str(archive),
        }
        self._start_research_mcp()
        # Streamable HTTP GET keeps a response stream open by design. A TCP
        # readiness probe verifies the listener without misclassifying that
        # long-lived response as a timeout.
        _wait_port(self.mcp_port, timeout=45)
        self._web_command = [
            str(ROOT / "infra" / "dsh" / "run-web.sh"),
            "--port",
            str(self.web_port),
            "--patch",
            str(REPLAY_PATCH),
        ]
        self._web_env = {
            **self._common_env,
            "DSH_WEB_HOME": str(self.web_home),
            "DSH_REPLAY_PLUGIN": str(REPLAY_PLUGIN),
            "DSH_SNAPSHOT_FILE": str(snapshot),
            "DSH_SNAPSHOT_OVERRIDE": str(override),
            "DSH_REPLAY_PACE_MS": str(self.replay_pace_ms),
            "DECISION_HUB_API_URL": f"http://127.0.0.1:{self.api_port}",
            "DECISION_HUB_DESK_URL": f"http://127.0.0.1:{self.api_port}",
            "DECISION_HUB_DSH_HOST_KEY": HOST_KEY,
            "DECISION_HUB_DSH_CALLBACK_KEY": CALLBACK_KEY,
            "DECISION_HUB_DSH_CALLBACK_TIMEOUT_MS": str(self.callback_timeout_ms),
            "DECISION_HUB_DSH_CALLBACK_ATTEMPTS": str(self.callback_attempts),
            "DECISION_HUB_DSH_RUNTIME_MODE": "replay",
            "DECISION_HUB_DSH_SOURCE_COMMIT": str(UPSTREAM_LOCK["commit"]),
            "DECISION_HUB_DSH_SOURCE_VERSION": str(UPSTREAM_LOCK["source_version"]),
            "DECISION_HUB_DSH_WORKSPACE_CWD": str(self.workspace),
            "DECISION_HUB_RESEARCH_MCP_URL": f"http://127.0.0.1:{self.mcp_port}/mcp",
            "DECISION_HUB_RESEARCH_TOOL_URL": (
                f"http://127.0.0.1:{self.mcp_port}"
                "/decision-hub/v1/research-capabilities/execute"
            ),
        }
        self._start_web()

    def run(self) -> ScenarioResult:
        if self.authenticated_url is None:
            raise RuntimeError("dsh_native_runtime_not_started")
        run_id = self.admit_observation()
        worker = self.run_worker_once()
        if worker.returncode not in {0, 1}:
            raise RuntimeError(
                f"hub research worker exited {worker.returncode}: {_tail(worker.stderr)}"
            )
        return self.collect_result(run_id)

    def admit_observation(self, *, variant: str | None = None) -> str:
        observation_time = datetime.now(UTC) - timedelta(seconds=1)
        identity_suffix = f" {variant}" if variant else ""
        payload = {
            "text": (
                f"DSH Native {self.scenario} acceptance: verify a central-bank event and "
                f"its bounded cross-market transmission without using live data.{identity_suffix}"
            ),
            "source_id": f"dsh-native-{self.scenario}{'-' + variant if variant else ''}",
            "source_type": "transcript",
            "observed_at": observation_time.isoformat(),
            "published_at": (observation_time - timedelta(seconds=1)).isoformat(),
            "language": "en",
            "event_hint": "central_bank_speech",
        }
        idempotency_key = (
            f"dsh-native-{self.scenario}{'-' + variant if variant else ''}-{uuid.uuid4().hex}"
        )
        with httpx.Client(timeout=30, trust_env=False) as client:
            response = client.post(
                f"http://127.0.0.1:{self.api_port}/v1/research/observations",
                headers={"Idempotency-Key": idempotency_key},
                json=payload,
            )
            response.raise_for_status()
            return str(response.json()["run_id"])

    def stop(self) -> None:
        for process in reversed(self.processes):
            process.stop()
        self.processes.clear()

    def browser_url(self, dsh_session_id: str) -> str:
        if self.authenticated_url is None:
            raise RuntimeError("dsh_native_runtime_not_started")
        separator = "&" if "?" in self.authenticated_url else "?"
        return f"{self.authenticated_url}{separator}session_id={dsh_session_id}"

    def _start_process(
        self,
        name: str,
        command: list[str],
        env: dict[str, str],
        *,
        log_name: str | None = None,
    ) -> ManagedProcess:
        if any(item.name == name and item.process.poll() is None for item in self.processes):
            raise RuntimeError(f"managed process is already running: {name}")
        log_path = self.logs / f"{log_name or name}.log"
        log_handle = log_path.open("w", encoding="utf-8")
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        managed = ManagedProcess(name, process, log_path, log_handle)
        self.processes.append(managed)
        return managed

    def _process(self, name: str) -> ManagedProcess:
        for process in reversed(self.processes):
            if process.name == name and process.process.poll() is None:
                return process
        raise RuntimeError(f"managed process is not running: {name}")

    def stop_process(self, name: str) -> None:
        process = self._process(name)
        process.stop()
        self.processes.remove(process)

    def _start_hub_api(self, *, log_name: str = "hub-api") -> None:
        self._start_process(
            "hub-api",
            [str(PYTHON), "-m", "apps.hub_api.main"],
            self._api_env,
            log_name=log_name,
        )

    def restart_hub_api(self) -> None:
        if any(
            item.name == "hub-api" and item.process.poll() is None
            for item in self.processes
        ):
            self.stop_process("hub-api")
        self._restart_generation += 1
        self._start_hub_api(log_name=f"hub-api-restart-{self._restart_generation}")
        _wait_http(f"http://127.0.0.1:{self.api_port}/health/ready", timeout=45)

    def _start_research_mcp(self, *, log_name: str = "research-mcp") -> None:
        self._start_process(
            "research-mcp",
            [str(PYTHON), "-m", "apps.research_mcp.main"],
            self._mcp_env,
            log_name=log_name,
        )

    def refresh_replay_archive(self) -> None:
        if self._archive_path is None:
            raise RuntimeError("replay archive is not initialized")
        self._archive_path.write_text(
            json.dumps(research_archive(self.scenario), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.stop_process("research-mcp")
        self._restart_generation += 1
        self._start_research_mcp(
            log_name=f"research-mcp-refresh-{self._restart_generation}"
        )
        _wait_port(self.mcp_port, timeout=45)

    def _start_web(
        self,
        *,
        env_overrides: dict[str, str] | None = None,
        expect_ready: bool = True,
        log_name: str = "dsh-web",
    ) -> None:
        process = self._start_process(
            "dsh-web",
            self._web_command,
            {**self._web_env, **(env_overrides or {})},
            log_name=log_name,
        )
        self.authenticated_url = self._wait_web_url(process, timeout=120)
        self._register_workspace(self.authenticated_url)
        if expect_ready:
            self._wait_host_ready(timeout=60)

    def restart_web(
        self,
        *,
        env_overrides: dict[str, str] | None = None,
        expect_ready: bool = True,
        label: str = "restart",
    ) -> None:
        self.stop_process("dsh-web")
        self._restart_generation += 1
        self._start_web(
            env_overrides=env_overrides,
            expect_ready=expect_ready,
            log_name=f"dsh-web-{label}-{self._restart_generation}",
        )

    def _wait_web_url(self, process: ManagedProcess, *, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        pattern = re.compile(r"^dsh web: (http://127\.0\.0\.1:\d+/\?token=\S+)", re.MULTILINE)
        while time.monotonic() < deadline:
            if process.process.poll() is not None:
                raise RuntimeError(
                    f"DSH Web exited before readiness: {_safe_log(process.log_path)}"
                )
            text = process.log_path.read_text(encoding="utf-8", errors="replace")
            match = pattern.search(text)
            if match is not None:
                return match.group(1)
            time.sleep(0.1)
        raise TimeoutError(
            f"DSH Web did not publish an authenticated URL: {_safe_log(process.log_path)}"
        )

    def _register_workspace(self, authenticated_url: str) -> None:
        with httpx.Client(follow_redirects=True, timeout=20, trust_env=False) as client:
            response = client.get(authenticated_url)
            response.raise_for_status()
            rpc_id = f"acceptance-workspace-{uuid.uuid4().hex}"
            response = client.post(
                f"http://127.0.0.1:{self.web_port}/api/workspace/create",
                json={
                    "type": "client-request",
                    "rpcId": rpc_id,
                    "method": "workspace/create",
                    "payload": {"args": {"request": {"path": str(self.workspace)}}},
                },
            )
            response.raise_for_status()
            body = response.json()
            if body.get("rpcId") != rpc_id or body.get("result", {}).get("ok") is not True:
                raise RuntimeError(f"DSH workspace registration failed: {body}")

    def _wait_host_ready(self, *, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        last = ""
        with httpx.Client(timeout=10, trust_env=False) as client:
            while time.monotonic() < deadline:
                response = client.get(
                    f"http://127.0.0.1:{self.web_port}/decision-hub/v1/readiness",
                    headers={"X-Decision-Hub-Host-Key": HOST_KEY},
                )
                last = response.text
                if response.status_code == 200 and response.json().get("ready") is True:
                    return
                time.sleep(0.2)
        raise TimeoutError(f"DSH Host plugin did not become ready: {last}")

    def host_request(self, method: str, path: str) -> httpx.Response:
        with httpx.Client(timeout=30, trust_env=False) as client:
            return client.request(
                method,
                f"http://127.0.0.1:{self.web_port}{path}",
                headers={"X-Decision-Hub-Host-Key": HOST_KEY},
            )

    def wait_link(
        self,
        run_id: str,
        *,
        predicate: Callable[[dict[str, object]], bool],
        timeout: float,
    ) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        last = ""
        with httpx.Client(timeout=5, trust_env=False) as client:
            while time.monotonic() < deadline:
                try:
                    response = client.get(
                        f"http://127.0.0.1:{self.api_port}/v1/dsh/sessions/{run_id}"
                    )
                    last = response.text
                    if response.status_code == 200:
                        payload = response.json()
                        if predicate(payload):
                            return payload
                except httpx.HTTPError as exc:
                    last = str(exc)
                time.sleep(0.1)
        raise TimeoutError(f"DSH link did not reach the expected state: {last}")

    def worker_env(self, *, poll_interval_seconds: float = 0.05) -> dict[str, str]:
        return {
            **os.environ,
            "PYTHONPATH": str(ROOT),
            "DECISION_HUB_DATA_DIR": str(self.data_dir),
            "DECISION_HUB_RESEARCH_RUNTIME": "dsh-web",
            "DECISION_HUB_RESEARCH_EXECUTION_MODE": "replay",
            "DECISION_HUB_RESEARCH_CAPABILITIES": "replay.research",
            "DECISION_HUB_DSH_WEB_URL": f"http://127.0.0.1:{self.web_port}",
            "DECISION_HUB_DSH_HOST_SECRET": HOST_KEY,
            "DECISION_HUB_DSH_POLL_INTERVAL_SECONDS": str(poll_interval_seconds),
            "DECISION_HUB_DSH_HOST_TIMEOUT_SECONDS": "20",
            "DECISION_HUB_INSTANCE_ID": f"native-acceptance-worker-{self.scenario}",
            "DECISION_HUB_LLM_ENABLED": "0",
            "DECISION_HUB_SOURCES_ENABLED": "0",
            "DECISION_HUB_MARKET_ENABLED": "0",
        }

    def start_worker(self, *, poll_interval_seconds: float) -> ManagedProcess:
        return self._start_process(
            "hub-worker",
            [str(PYTHON), "-m", "apps.hub_worker.main", "--role", "research", "--once"],
            self.worker_env(poll_interval_seconds=poll_interval_seconds),
        )

    def run_worker_once(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(PYTHON), "-m", "apps.hub_worker.main", "--role", "research", "--once"],
            cwd=ROOT,
            env=self.worker_env(),
            capture_output=True,
            text=True,
            timeout=240,
        )

    def collect_result(self, run_id: str, *, artifact_prefix: str = "") -> ScenarioResult:
        prefix = f"{artifact_prefix}-" if artifact_prefix else ""
        with httpx.Client(timeout=30, trust_env=False) as client:
            detail_response = client.get(
                f"http://127.0.0.1:{self.api_port}/v1/research/runs/{run_id}"
            )
            detail_response.raise_for_status()
            detail = detail_response.json()
            product_response = client.get(
                f"http://127.0.0.1:{self.api_port}/v1/runs/{run_id}"
            )
            product_response.raise_for_status()
            product_run = product_response.json()
            link_response = client.get(f"http://127.0.0.1:{self.api_port}/v1/dsh/sessions/{run_id}")
            link_response.raise_for_status()
            link = link_response.json()
            business_response = client.get(
                f"http://127.0.0.1:{self.api_port}/v1/dsh/sessions/{run_id}/business-status"
            )
            business_response.raise_for_status()
            business = business_response.json()
            host_response = client.get(
                f"http://127.0.0.1:{self.web_port}/decision-hub/v1/runs/{run_id}/result",
                headers={"X-Decision-Hub-Host-Key": HOST_KEY},
            )
            host_response.raise_for_status()
            host_result = host_response.json()
        dsh_session_id = str(link["dsh_session_id"])
        if host_result.get("dsh_session_id") != dsh_session_id:
            raise AssertionError("DSH Host result and Hub link disagree on session identity")
        session_log = find_session_log(self.web_home, dsh_session_id)
        session_copy = self.artifacts / f"{dsh_session_id}{''.join(session_log.suffixes)}"
        shutil.copy2(session_log, session_copy)
        detail_path = self.artifacts / f"{prefix}hub-run-detail.json"
        detail_path.write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
        product_path = self.artifacts / f"{prefix}hub-product-run.json"
        product_path.write_text(
            json.dumps(product_run, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        link_path = self.artifacts / f"{prefix}hub-dsh-link.json"
        link_path.write_text(json.dumps(link, ensure_ascii=False, indent=2), encoding="utf-8")
        business_path = self.artifacts / f"{prefix}hub-business-status.json"
        business_path.write_text(
            json.dumps(business, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        host_path = self.artifacts / f"{prefix}dsh-host-result.json"
        host_path.write_text(
            json.dumps(host_result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        run = detail.get("run") or {}
        coverage = run.get("coverage") or {}
        trace = detail.get("trace") or []
        failures = [
            item for item in trace if item.get("status") == "failed" or item.get("error_code")
        ]
        result = ScenarioResult(
            scenario=self.scenario,
            run_id=run_id,
            dsh_session_id=dsh_session_id,
            run_status=str(run.get("status")),
            gate_status=product_run.get("gate_status"),
            coverage_status=coverage.get("status"),
            hard_coverage_ratio=coverage.get("hard_coverage_ratio"),
            stop_reason=(run.get("stop_reason") or {}).get("code"),
            evidence_count=len(detail.get("evidence") or []),
            tool_failure_count=len(failures),
            session_log=str(session_copy),
            session_log_sha256=_sha256(session_copy),
            output_dir=str(self.root),
        )
        if business.get("gate_status") != result.gate_status:
            raise AssertionError("business projection and canonical product Gate disagree")
        if business.get("coverage_status") != result.coverage_status:
            raise AssertionError("business projection and research coverage disagree")
        if business.get("hard_coverage_ratio") != result.hard_coverage_ratio:
            raise AssertionError("business projection and hard coverage disagree")
        if result.scenario == "partial_failure" and not business.get("failures"):
            raise AssertionError("partial failure is missing from the business projection")
        assert_scenario(result)
        (self.artifacts / f"{prefix}summary.json").write_text(
            json.dumps(result.__dict__, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return result


def _write_scenario_fixtures(scenario: Scenario, root: Path) -> tuple[Path, Path, Path]:
    fixture_root = root / "fixtures"
    fixture_root.mkdir(parents=True, exist_ok=True)
    snapshot = fixture_root / "session.jsonl"
    snapshot.write_text(
        json.dumps(
            {
                "type": "session",
                "version": 0,
                "id": "dsh-native-acceptance-source",
                "createdAt": int(time.time() * 1000),
                "cwd": str(root / "workspace"),
                "agentPreset": "decision-research",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    archive = fixture_root / "research-archive.json"
    archive.write_text(
        json.dumps(research_archive(scenario), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    override = fixture_root / "replay.override.json"
    override.write_text(
        json.dumps(_replay_entries(scenario), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return snapshot, override, archive


def research_archive(scenario: Scenario) -> dict[str, object]:
    now = datetime.now(UTC) - timedelta(seconds=15)
    stale = now - timedelta(days=30)
    authorities = {
        "event_identity": ("official", "official", ("fed-event",)),
        "policy_or_data_delta": ("official", "official", ("fed-policy",)),
        "expectation_pricing": ("market", "exchange", ("rates-pricing",)),
        "macro_transmission": ("market", "exchange", ("rates-yield", "usd-index")),
        "cross_asset_confirmation": ("web", "verified_web", ("cross-asset",)),
        "crypto_spot_confirmation": ("market", "exchange", ("btc-spot",)),
        "derivatives_crowding": ("market", "exchange", ("btc-derivatives",)),
        "counter_thesis": ("web", "verified_web", ("counter-thesis",)),
    }
    selected: tuple[str, ...]
    if scenario == "success":
        selected = tuple(authorities)
    elif scenario == "partial_failure":
        selected = ("event_identity",)
    else:
        selected = ("event_identity",)
    queries: list[dict[str, object]] = []
    for requirement in selected:
        kind, authority, sources = authorities[requirement]
        observed = stale if scenario == "insufficient_or_stale" else now
        candidates = [
            {
                "evidence_id": f"fixture-{requirement}-{source}",
                "requirement_id": requirement,
                "kind": kind,
                "authority": authority,
                "source_id": source,
                "source_url": None,
                "published_at": (observed - timedelta(seconds=1)).isoformat(),
                "observed_at": observed.isoformat(),
                "received_at": observed.isoformat(),
                "content_hash": "0" * 64,
                "excerpt": (
                    f"Controlled DSH Native replay evidence for {requirement} from {source}."
                ),
                "structured_payload_ref": None,
                "tool_call_id": None,
                "research_session_id": "fixture-session",
                "round": 1,
                "quality": "candidate",
                "freshness_status": "unknown",
                "conflict_group": None,
            }
            for source in sources
        ]
        queries.append(
            {"query": f"native:{scenario}:{requirement}", "evidence_candidates": candidates}
        )
    return {"schema_version": "research-replay-fixtures.v1", "queries": queries}


def _replay_entries(scenario: Scenario) -> list[dict[str, object]]:
    rounds = replay_rounds(scenario)
    entries: list[dict[str, object]] = []
    call_index = 0
    for round_number, requirements in enumerate(rounds, start=1):
        for requirement in requirements:
            entries.append(
                _tool_call_entry(
                    scenario,
                    requirement,
                    call_index,
                    round_number=round_number,
                )
            )
            call_index += 1
        entries.append(_final_entry(scenario))
    return entries


def replay_rounds(scenario: Scenario) -> tuple[tuple[str, ...], ...]:
    if scenario == "success":
        return (
            (
                "event_identity",
                "policy_or_data_delta",
                "expectation_pricing",
                "macro_transmission",
                "cross_asset_confirmation",
                "crypto_spot_confirmation",
                "derivatives_crowding",
                "counter_thesis",
            ),
        )
    elif scenario == "partial_failure":
        # Round one preserves one accepted fact and one capability failure.
        # That new fact deliberately causes the outer lifecycle to request a
        # second turn in the same DSH Session. Round two proves that the agent
        # continues the ladder before stopping on no additional evidence.
        return (
            ("event_identity", "policy_or_data_delta"),
            ("expectation_pricing",),
        )
    return (("event_identity",),)


def _tool_call_entry(
    scenario: Scenario,
    requirement: str,
    index: int,
    *,
    round_number: int,
) -> dict[str, object]:
    request = {
        "request_id": "{{fromRequest:(research-request:[a-z0-9_]+)}}",
        "capability_id": "replay.research",
        "requirement_id": requirement,
        "query": f"native:{scenario}:{requirement}",
        "round": round_number,
        "mode": "replay",
        "observed_at": '{{fromRequest:"pit_cutoff_at":"([^"]+)}}',
        "cutoff_at": '{{fromRequest:"pit_cutoff_at":"([^"]+)}}',
        "target_url": None,
        "symbols": [],
        "fields": [],
        "allowed_domains": [],
        "max_results": 10,
        "max_cost_usd": 0,
    }
    call_id = f"native-{scenario}-{index + 1}"
    return {
        "kind": "chunks",
        "chunks": [
            {"type": "block-start", "index": 0, "blockType": "tool-call"},
            {
                "type": "block-end",
                "index": 0,
                "block": {
                    "type": "tool-call",
                    "id": call_id,
                    "name": "decision_hub_research",
                    "arguments": json.dumps(request, separators=(",", ":")),
                },
            },
            {"type": "finish", "reason": {"kind": "tool-calls"}},
        ],
    }


def _final_entry(scenario: Scenario) -> dict[str, object]:
    now = datetime.now(UTC)
    evidence_ref = "{{fromRequest:(ev_[a-f0-9]+)}}"
    synthesis: dict[str, object] = {
        "schema_version": "research-synthesis-candidate.v1",
        "request_id": "{{fromRequest:(research-request:[a-z0-9_]+)}}",
        "causal_case": None,
        "horizons": [],
    }
    if scenario == "success":
        link = {
            "link_id": "native-main-link",
            "claim_type": "fact",
            "statement": (
                "The controlled event and its transmission evidence satisfy the bounded fact pack."
            ),
            "evidence_refs": [evidence_ref],
            "confirmation": (
                "All hard evidence requirements pass the deterministic sufficiency gate."
            ),
            "invalidation": "Any source revision or PIT violation invalidates the candidate.",
            "affected_horizons": ["30m", "24h", "72h"],
        }
        opposite = {
            **link,
            "link_id": "native-opposite-link",
            "claim_type": "scenario",
            "statement": "The market may already have priced the controlled event.",
        }
        synthesis["causal_case"] = {
            "case_id": "native-success-case",
            "thesis": "The bounded replay supports a directional research candidate.",
            "main_chain": [link],
            "opposite_chain": [opposite],
            "unresolved_questions": [],
            "evidence_refs": [evidence_ref],
        }
        horizons = ("30m", "24h", "72h")
        expire_minutes = (30, 24 * 60, 72 * 60)
        review_minutes = (10, 6 * 60, 24 * 60)
        synthesis["horizons"] = [
            {
                "horizon": horizon,
                "action": "short",
                "subjective_probability": 0.65 - index * 0.05,
                "probability_status": "uncalibrated",
                "evidence_refs": [evidence_ref],
                "trigger": f"{horizon} controlled confirmation trigger",
                "invalidation": f"{horizon} controlled invalidation condition",
                "expires_at": (now + timedelta(minutes=expire_minutes[index])).isoformat(),
                "next_review_at": (now + timedelta(minutes=review_minutes[index])).isoformat(),
                "missing_facts": [],
                "confidence_cap_reason": None,
            }
            for index, horizon in enumerate(horizons)
        ]
    text = json.dumps(synthesis, ensure_ascii=False, separators=(",", ":"))
    return {
        "kind": "chunks",
        "chunks": [
            {"type": "block-start", "index": 0, "blockType": "text"},
            {"type": "text-delta", "index": 0, "text": text},
            {"type": "block-end", "index": 0, "block": {"type": "text", "text": text}},
            {"type": "finish", "reason": {"kind": "stop"}},
        ],
    }


def assert_scenario(result: ScenarioResult) -> None:
    if result.scenario == "success":
        assert result.run_status == "completed", result
        assert result.coverage_status == "sufficient", result
        assert result.stop_reason == "sufficient", result
        assert result.gate_status == "publish", result
        assert result.evidence_count >= 9, result
    elif result.scenario == "partial_failure":
        assert result.run_status in {"degraded", "rejected"}, result
        assert result.coverage_status == "insufficient", result
        assert result.gate_status in {"research_only", "reject"}, result
        assert result.tool_failure_count >= 1, result
    else:
        assert result.run_status in {"degraded", "rejected"}, result
        assert result.coverage_status == "insufficient", result
        assert result.gate_status in {"research_only", "reject"}, result
        assert result.evidence_count >= 1, result


def find_session_log(web_home: Path, session_id: str) -> Path:
    candidates = _session_logs(web_home, session_id)
    if len(candidates) != 1:
        all_logs = list(web_home.rglob("session.jsonl*"))
        raise RuntimeError(
            f"expected one DSH JSONL for {session_id}, found {len(candidates)}; all={all_logs}"
        )
    return candidates[0]


def _session_logs(web_home: Path, session_id: str) -> list[Path]:
    return [
        path
        for path in web_home.rglob("session.jsonl*")
        if session_id in path.parts
    ]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_http(url: str, *, timeout: float, accepted: tuple[int, ...] = ()) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    with httpx.Client(timeout=2, trust_env=False) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(url)
                last = f"HTTP {response.status_code} {response.text[:200]}"
                if response.is_success or response.status_code in accepted:
                    return
            except httpx.HTTPError as exc:
                last = str(exc)
            time.sleep(0.1)
    raise TimeoutError(f"service did not become reachable at {url}: {last}")


def _wait_port(port: int, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError as exc:
            last = str(exc)
            time.sleep(0.1)
    raise TimeoutError(f"service did not listen on 127.0.0.1:{port}: {last}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tail(value: str | None, limit: int = 2000) -> str:
    return (value or "")[-limit:]


def _safe_log(path: Path) -> str:
    if not path.exists():
        return "log unavailable"
    return re.sub(r"(token=)[^\s]+", r"\1[redacted]", _tail(path.read_text(errors="replace")))


def _scenario_root(scenario: Scenario) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return RUN_ROOT / f"{timestamp}-{scenario}-{uuid.uuid4().hex[:8]}"


def _run_one(scenario: Scenario, *, serve: bool) -> ScenarioResult:
    root = _scenario_root(scenario)
    runtime = ScenarioRuntime(scenario, root)
    try:
        runtime.start()
        result = runtime.run()
        print(json.dumps(result.__dict__, ensure_ascii=False, indent=2), flush=True)
        if serve:
            print(f"BROWSER_URL={runtime.browser_url(result.dsh_session_id)}", flush=True)
            print(
                f"HUB_RUN_URL=http://127.0.0.1:{runtime.api_port}/v1/research/runs/{result.run_id}",
                flush=True,
            )
            print("SERVING=1", flush=True)
            stop = False

            def request_stop(_signum: int, _frame: object) -> None:
                nonlocal stop
                stop = True

            signal.signal(signal.SIGINT, request_stop)
            signal.signal(signal.SIGTERM, request_stop)
            while not stop:
                time.sleep(0.25)
        return result
    finally:
        runtime.stop()


def _run_recovery_acceptance() -> dict[str, object]:
    root = _scenario_root("success")
    runtime = ScenarioRuntime(
        "success",
        root,
        replay_pace_ms=250,
        callback_timeout_ms=100,
        callback_attempts=1,
    )
    try:
        runtime.start()
        run_id = runtime.admit_observation()
        worker = runtime.start_worker(poll_interval_seconds=20)
        accepted_link = runtime.wait_link(
            run_id,
            predicate=lambda value: value.get("accepted_at") is not None,
            timeout=60,
        )

        runtime.stop_process("hub-api")
        callback_failure = _wait_for_callback_failure(runtime, run_id, timeout=90)
        runtime.restart_hub_api()

        reconciled_response = runtime.host_request(
            "GET", f"/decision-hub/v1/runs/{run_id}/result"
        )
        reconciled_response.raise_for_status()
        reconciled_result = reconciled_response.json()
        terminal_link = runtime.wait_link(
            run_id,
            predicate=lambda value: value.get("terminal_at") is not None,
            timeout=30,
        )
        worker_code = worker.wait(timeout=240)
        if worker_code not in {0, 1}:
            raise RuntimeError(
                f"recovery worker exited {worker_code}: {_safe_log(worker.log_path)}"
            )
        result = runtime.collect_result(run_id, artifact_prefix="callback-recovery")
        web_restart = _verify_web_restart(runtime, result)
        version_rollback = _verify_version_rollback(runtime, result)
        summary: dict[str, object] = {
            "status": "passed",
            "run_id": result.run_id,
            "dsh_session_id": result.dsh_session_id,
            "callback_recovery": {
                "accepted_at": accepted_link.get("accepted_at"),
                "failure_status": callback_failure["status"],
                "failure_code": callback_failure["error_code"],
                "terminal_at": terminal_link.get("terminal_at"),
                "result_hash": reconciled_result.get("result_hash"),
                "worker_exit_code": worker_code,
            },
            "web_restart": web_restart,
            "version_rollback": version_rollback,
            "output_dir": str(root),
        }
        (runtime.artifacts / "recovery-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return summary
    finally:
        runtime.stop()


def _wait_for_callback_failure(
    runtime: ScenarioRuntime,
    run_id: str,
    *,
    timeout: float,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        response = runtime.host_request("GET", f"/decision-hub/v1/runs/{run_id}")
        last = response.text
        if response.status_code == 503:
            payload = response.json()
            error = payload.get("error") or {}
            if error.get("code") == "host_callback_unreachable":
                return {
                    "status": response.status_code,
                    "error_code": error["code"],
                }
        elif response.status_code != 200:
            raise RuntimeError(
                "unexpected Host response while awaiting callback gap: "
                f"{response.status_code} {last}"
            )
        time.sleep(0.1)
    raise TimeoutError(f"terminal callback failure was not observed: {last}")


def _verify_web_restart(
    runtime: ScenarioRuntime,
    result: ScenarioResult,
) -> dict[str, object]:
    before_response = runtime.host_request(
        "GET", f"/decision-hub/v1/runs/{result.run_id}/result"
    )
    before_response.raise_for_status()
    before = before_response.json()
    before_logs = _session_logs(runtime.web_home, result.dsh_session_id)
    if len(before_logs) != 1:
        raise AssertionError(f"expected one Session before Web restart, got {before_logs}")

    runtime.restart_web(label="recovery")
    after_response = runtime.host_request(
        "GET", f"/decision-hub/v1/runs/{result.run_id}/result"
    )
    after_response.raise_for_status()
    after = after_response.json()
    after_logs = _session_logs(runtime.web_home, result.dsh_session_id)
    if len(after_logs) != 1:
        raise AssertionError(f"Web restart changed Session cardinality: {after_logs}")
    if before.get("dsh_session_id") != after.get("dsh_session_id"):
        raise AssertionError("Web restart changed the durable Session identity")
    if before.get("result_hash") != after.get("result_hash"):
        raise AssertionError("Web restart changed the terminal result hash")
    evidence: dict[str, object] = {
        "status": "passed",
        "dsh_session_id": after.get("dsh_session_id"),
        "result_hash": after.get("result_hash"),
        "session_log": str(after_logs[0]),
        "session_log_sha256": _sha256(after_logs[0]),
    }
    (runtime.artifacts / "web-restart.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return evidence


def _verify_version_rollback(
    runtime: ScenarioRuntime,
    retained: ScenarioResult,
) -> dict[str, object]:
    retained_response = runtime.host_request(
        "GET", f"/decision-hub/v1/runs/{retained.run_id}/result"
    )
    retained_response.raise_for_status()
    retained_result_hash = retained_response.json().get("result_hash")
    runtime.restart_web(
        env_overrides={"DECISION_HUB_DSH_SOURCE_VERSION": "0.0.0-incompatible"},
        expect_ready=False,
        label="incompatible",
    )
    readiness = runtime.host_request("GET", "/decision-hub/v1/readiness")
    if readiness.status_code != 503:
        raise AssertionError(f"incompatible Host readiness was {readiness.status_code}")
    readiness_body = readiness.json()
    if readiness_body.get("version_compatible") is not False:
        raise AssertionError("incompatible Host did not fail the version gate")
    if readiness_body.get("error_code") != "host_version_incompatible":
        raise AssertionError(f"unexpected readiness error: {readiness_body}")
    blocked = runtime.host_request(
        "GET", f"/decision-hub/v1/runs/{retained.run_id}/result"
    )
    blocked_body = blocked.json()
    if blocked.status_code != 503 or (blocked_body.get("error") or {}).get("code") != (
        "host_version_incompatible"
    ):
        raise AssertionError("incompatible Host did not reject the Run route")

    runtime.restart_web(label="locked-rollback")
    restored = runtime.host_request("GET", "/decision-hub/v1/readiness")
    restored.raise_for_status()
    restored_result = runtime.host_request(
        "GET", f"/decision-hub/v1/runs/{retained.run_id}/result"
    )
    restored_result.raise_for_status()
    if restored_result.json().get("result_hash") != retained_result_hash:
        raise AssertionError("rollback changed the retained terminal result hash")

    runtime.refresh_replay_archive()
    rollback_run_id = runtime.admit_observation(variant="locked-rollback")
    worker = runtime.run_worker_once()
    if worker.returncode not in {0, 1}:
        raise RuntimeError(
            f"rollback worker exited {worker.returncode}: {_tail(worker.stderr)}"
        )
    rollback_result = runtime.collect_result(
        rollback_run_id,
        artifact_prefix="locked-rollback",
    )
    if rollback_result.dsh_session_id == retained.dsh_session_id:
        raise AssertionError("rollback replay reused an unrelated Session identity")
    retained_log = find_session_log(runtime.web_home, retained.dsh_session_id)
    evidence: dict[str, object] = {
        "status": "passed",
        "rejected_identity": readiness_body.get("upstream_identity"),
        "rejected_error_code": readiness_body.get("error_code"),
        "restored_identity": restored.json().get("upstream_identity"),
        "retained_run_id": retained.run_id,
        "retained_dsh_session_id": retained.dsh_session_id,
        "retained_session_log_sha256": _sha256(retained_log),
        "rollback_run_id": rollback_result.run_id,
        "rollback_dsh_session_id": rollback_result.dsh_session_id,
        "rollback_gate_status": rollback_result.gate_status,
        "rollback_session_log_sha256": rollback_result.session_log_sha256,
    }
    (runtime.artifacts / "version-rollback.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the official DSH Web -> Decision Hub replay acceptance."
    )
    parser.add_argument("--scenario", choices=SCENARIOS)
    parser.add_argument(
        "--serve",
        action="store_true",
        help="keep one validated scenario running for browser inspection",
    )
    parser.add_argument(
        "--recovery",
        action="store_true",
        help="run callback-gap, official Web restart and locked-version rollback acceptance",
    )
    args = parser.parse_args()
    if not PYTHON.is_file():
        parser.error("project virtual environment is missing")
    if not REPLAY_PLUGIN.joinpath("package.json").is_file():
        parser.error("pinned upstream replay package is missing; run infra/dsh/acceptance.sh")
    if args.serve and args.scenario is None:
        parser.error("--serve requires --scenario")
    if args.recovery and (args.scenario is not None or args.serve):
        parser.error("--recovery cannot be combined with --scenario or --serve")
    if args.recovery:
        _run_recovery_acceptance()
        return 0
    if args.scenario is not None:
        _run_one(args.scenario, serve=args.serve)
        return 0
    results = [_run_one(scenario, serve=False) for scenario in SCENARIOS]
    print(
        json.dumps(
            {"status": "passed", "scenarios": [item.__dict__ for item in results]},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
