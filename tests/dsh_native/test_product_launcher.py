from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "infra" / "dsh" / "run-product.sh"
LIVE_WEB_LAUNCHER = ROOT / "infra" / "dsh" / "run-live-web.sh"
WINDOWS_LAUNCHER = ROOT / "infra" / "dsh" / "run-product.ps1"
WINDOWS_STOPPER = ROOT / "infra" / "dsh" / "stop-product.ps1"


def _run_launcher(
    tmp_path: Path, *, capabilities: str | None, search_canary_status: str | None = None
) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capture = tmp_path / "docker-invocation.txt"
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'capabilities=%s\\n' \"$DECISION_HUB_RESEARCH_CAPABILITIES\" "
        '> "$PRODUCT_CAPTURE"\n'
        'printf \'sources=%s\\n\' "$DECISION_HUB_SOURCES_ENABLED" >> "$PRODUCT_CAPTURE"\n'
        'printf \'args=%s\\n\' "$*" >> "$PRODUCT_CAPTURE"\n'
        "exit 91\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "PRODUCT_CAPTURE": str(capture),
            "OPENAI_API_KEY": "test-only-not-a-real-key",
            "DSH_PRODUCT_KEEP_SERVICES": "1",
        }
    )
    if capabilities is None:
        env.pop("DECISION_HUB_RESEARCH_CAPABILITIES", None)
    else:
        env["DECISION_HUB_RESEARCH_CAPABILITIES"] = capabilities
    if search_canary_status is None:
        env.pop("DECISION_HUB_SEARCH_CANARY_STATUS", None)
    else:
        env["DECISION_HUB_SEARCH_CANARY_STATUS"] = search_canary_status

    result = subprocess.run(
        [str(LAUNCHER)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, capture


@pytest.mark.skipif(os.name == "nt", reason="POSIX launcher execution requires WSL or Linux CI")
def test_live_product_defaults_to_audited_fact_capabilities_and_passes_local_env_file(
    tmp_path: Path,
) -> None:
    result, capture = _run_launcher(tmp_path, capabilities=None)

    assert result.returncode == 91
    invocation = capture.read_text(encoding="utf-8")
    assert (
        "capabilities=official.macro,market.cross_asset,market.crypto_derivatives,web.fetch,document.readback,data.historical"
        in invocation
    )
    assert f"--env-file {ROOT / 'data' / 'dsh-live' / '.env'}" in invocation
    assert "replay.research" not in invocation
    assert "sources=1" in invocation
    assert (
        "build hub-api hub-realtime-worker hub-evolution-worker research-mcp hub-research-worker"
    ) in invocation


@pytest.mark.skipif(os.name == "nt", reason="POSIX launcher execution requires WSL or Linux CI")
def test_live_product_enables_stable_web_search_only_after_canary_passes(
    tmp_path: Path,
) -> None:
    result, capture = _run_launcher(
        tmp_path,
        capabilities=None,
        search_canary_status="passed",
    )

    assert result.returncode == 91
    invocation = capture.read_text(encoding="utf-8")
    expected = "market.crypto_derivatives,web.fetch,document.readback,data.historical,web.search"
    assert expected in invocation


def test_product_launcher_requires_the_current_product_api_contract() -> None:
    launcher = LAUNCHER.read_text(encoding="utf-8")

    assert "/v1/research/inbox?limit=1" in launcher
    assert "research-inbox-view.v1" in launcher
    assert "product API contract did not become ready" in launcher
    assert launcher.index("/v1/research/inbox?limit=1") < launcher.index(
        "Starting official DSH Web"
    )


def test_research_worker_starts_only_after_dsh_host_readiness_barrier() -> None:
    launcher = LAUNCHER.read_text(encoding="utf-8")

    first_control_plane_start = launcher.index(
        "up -d --no-build --force-recreate hub-api hub-realtime-worker "
        "hub-evolution-worker research-mcp"
    )
    readiness_barrier = launcher.index("host_readiness_url=")
    research_worker_start = launcher.index(
        "--no-deps --no-build --force-recreate hub-research-worker"
    )
    product_ready = launcher.index("Product is ready")

    assert first_control_plane_start < readiness_barrier < research_worker_start
    assert research_worker_start < product_ready
    assert (
        "up -d --no-build --force-recreate hub-api hub-realtime-worker "
        "hub-evolution-worker research-mcp hub-research-worker"
    ) not in launcher
    assert "research worker did not enter running state" in launcher


def test_windows_product_launcher_sets_valid_worker_policy_and_waits_for_heartbeat() -> None:
    launcher = WINDOWS_LAUNCHER.read_text(encoding="utf-8")
    common = launcher.split("$common = @{", 1)[1].split("}", 1)[0]

    assert "web.search" in launcher
    assert "Read-DotEnvSubset" in launcher
    assert "DECISION_HUB_SEARCH_PROVIDER" in launcher
    assert "DECISION_HUB_SEARCH_CANARY_STATUS" in launcher

    assert "DECISION_HUB_SOURCES_ENABLED = '1'" in common
    assert "DECISION_HUB_MARKET_ENABLED = '1'" in common
    assert "function Wait-ResearchWorkerReady" in launcher
    assert "service_id -eq 'hub-research-worker'" in launcher
    assert "status -eq 'online'" in launcher
    assert launcher.index("Wait-ResearchWorkerReady -Process") < launcher.index(
        "$supervisorEntry.state = 'running'"
    )


def test_windows_product_stopper_prevents_supervisor_respawn_and_verifies_exit() -> None:
    stopper = WINDOWS_STOPPER.read_text(encoding="utf-8")
    assert stopper.index("stop.request") < stopper.index("Stop-ProductProcessTree")
    assert "Process state was preserved for retry" in stopper
    assert "$entries = @(Get-Content" not in stopper


@pytest.mark.skipif(os.name != "nt", reason="Windows process ownership rehearsal")
def test_windows_supervision_recovers_worker_without_interrupting_api() -> None:
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "tools/ops/probe_product_supervision.ps1"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"bounded_failures":true' in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="POSIX launcher execution requires WSL or Linux CI")
def test_live_product_rejects_replay_capability_before_starting_services(
    tmp_path: Path,
) -> None:
    result, capture = _run_launcher(tmp_path, capabilities="web.search,replay.research")

    assert result.returncode == 2
    assert not capture.exists()
    assert "live product refuses replay.research" in result.stderr


def test_product_image_context_excludes_local_state_credentials_and_upstream_cache() -> None:
    patterns = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "data" in patterns
    assert ".cache" in patterns
    assert "**/.env" in patterns
    assert "**/.env.*" in patterns

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "public.ecr.aws/docker/library/node@sha256:" in dockerfile
    assert "ghcr.io/astral-sh/uv@sha256:" in dockerfile
    assert "ARG NPM_REGISTRY=https://registry.npmmirror.com" in dockerfile
    assert "pnpm install --frozen-lockfile" in dockerfile

    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert 'command: ["research-mcp"]' not in compose
    assert 'command: ["hub-worker"' not in compose
    assert '"-m", "apps.research_mcp.main"' in compose
    assert '"-m", "apps.hub_worker.main"' in compose

    # The durable MCP gateway must share the Hub SQLite volume. Without this
    # mount its session-link lookup runs against an empty container-local DB.
    research_mcp = compose.split("  research-mcp:\n", 1)[1].split("  hub-research-worker:\n", 1)[0]
    assert "      - decision-hub-data:/app/data/decision-hub" in research_mcp
    assert ("    depends_on:\n      hub-api:\n        condition: service_healthy") in research_mcp


def test_live_web_launcher_keeps_provider_credentials_in_their_own_namespace() -> None:
    launcher = LIVE_WEB_LAUNCHER.read_text(encoding="utf-8")

    # DSH resolves inherited environment variables before DSH_HOME/.env. An
    # OPENAI_* -> DEEPSEEK_* alias would therefore override a valid official
    # DeepSeek credential and endpoint stored in the live profile.
    assert 'export DEEPSEEK_API_KEY="$OPENAI_API_KEY"' not in launcher
    assert 'export DEEPSEEK_BASE_URL="$OPENAI_BASE_URL"' not in launcher
    assert "Provider credential namespaces remain isolated" in launcher


def test_decision_research_web_preset_enables_official_search_and_fetch() -> None:
    preset = (
        ROOT / "infra" / "dsh" / "presets" / "decision-research" / "agent.cordis.yml"
    ).read_text(encoding="utf-8")

    assert "@deepseek-ai/dsh-tool-web" in preset
    assert "fetch: true" in preset
    assert "DECISION_HUB_SEARCH_CANARY_STATUS === 'passed'" in preset
    assert "decision_hub_research" in preset


def test_decision_research_web_preset_exposes_research_workers_and_confines_reviewer() -> None:
    preset = (
        ROOT / "infra" / "dsh" / "presets" / "decision-research" / "agent.cordis.yml"
    ).read_text(encoding="utf-8")

    worker = preset.split("    - id: tool-subagent\n", 1)[1].split(
        "\n\n    - id: tool-subagent-reviewer", 1
    )[0]
    reviewer = preset.split("    - id: tool-subagent-reviewer\n", 1)[1].split(
        "\n\n- id: tool-todo", 1
    )[0]
    assert "Decision Hub research specialist" in worker
    assert "backgroundMode: continuable" in worker
    assert "decision_hub_research" in worker
    assert "maxDepth: 1" in worker
    assert "independent Decision Hub evidence reviewer" in reviewer
    assert "toolFilter:\n          allow: []" in reviewer
    assert "backgroundMode: one-shot" in reviewer
    assert "maxDepth: 1" in reviewer
