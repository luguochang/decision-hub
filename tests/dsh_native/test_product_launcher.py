from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "infra" / "dsh" / "run-product.sh"


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
        "capabilities=official.macro,market.cross_asset,market.crypto_derivatives"
        in invocation
    )
    assert f"--env-file {ROOT / 'data' / 'dsh-live' / '.env'}" in invocation
    assert "replay.research" not in invocation
    assert "sources=1" in invocation


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
