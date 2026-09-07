from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "infra" / "dsh" / "run-product.sh"
LIVE_WEB_LAUNCHER = ROOT / "infra" / "dsh" / "run-live-web.sh"


def _run_launcher(
    tmp_path: Path, *, capabilities: str | None
) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capture = tmp_path / "docker-invocation.txt"
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'capabilities=%s\\n' \"$DECISION_HUB_RESEARCH_CAPABILITIES\" "
        "> \"$PRODUCT_CAPTURE\"\n"
        "printf 'sources=%s\\n' \"$DECISION_HUB_SOURCES_ENABLED\" >> \"$PRODUCT_CAPTURE\"\n"
        "printf 'args=%s\\n' \"$*\" >> \"$PRODUCT_CAPTURE\"\n"
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

    result = subprocess.run(
        [str(LAUNCHER)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, capture


def test_live_product_defaults_to_keyless_audited_fact_capabilities_and_passes_local_env_file(
    tmp_path: Path,
) -> None:
    result, capture = _run_launcher(tmp_path, capabilities=None)

    assert result.returncode == 91
    invocation = capture.read_text(encoding="utf-8")
    assert (
        "capabilities=official.macro,market.cross_asset,market.crypto_derivatives,web.fetch"
        in invocation
    )
    assert f"--env-file {ROOT / 'data' / 'dsh-live' / '.env'}" in invocation
    assert "replay.research" not in invocation
    assert "sources=1" in invocation
    assert (
        "build hub-api hub-realtime-worker hub-evolution-worker research-mcp "
        "hub-research-worker"
    ) in invocation


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
    readiness_barrier = launcher.index('host_readiness_url=')
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
    assert 'research worker did not enter running state' in launcher


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
    research_mcp = compose.split("  research-mcp:\n", 1)[1].split(
        "  hub-research-worker:\n", 1
    )[0]
    assert "      - decision-hub-data:/app/data/decision-hub" in research_mcp
    assert (
        "    depends_on:\n"
        "      hub-api:\n"
        "        condition: service_healthy"
    ) in research_mcp


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
    assert "search: true" in preset
    assert "decision_hub_research" in preset
