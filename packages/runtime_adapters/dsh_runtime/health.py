from __future__ import annotations

import importlib.metadata
import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from .client import DshRuntimeConfig
from .profile import inspect_profile


class DshReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str
    checked_at: datetime
    sdk_version: str | None
    runtime_name: str | None
    runtime_version: str | None
    profile_ref: str | None
    profile_hash: str | None
    provider: str
    model: str
    provider_secret_configured: bool
    local_handshake: bool
    error_code: str | None


def check_local_readiness(config: DshRuntimeConfig | None = None) -> DshReadinessReport:
    selected = config or DshRuntimeConfig.from_env()
    checked_at = datetime.now(UTC)
    secret_configured = any(
        os.getenv(name)
        for name in (
            "DECISION_HUB_DSH_API_KEY",
            "DEEPSEEK_API_KEY",
            "SUB2API_API_KEY",
            "OPENAI_API_KEY",
        )
    )
    try:
        profile = inspect_profile(selected.profile_path)
        sdk_version = importlib.metadata.version("deepseek-harness-sdk")
        if sdk_version != selected.sdk_version:
            raise RuntimeError("dsh_sdk_version_mismatch")
        from deepseek_harness import HarnessClient, HarnessConfig

        with tempfile.TemporaryDirectory(prefix="decision-hub-dsh-readiness-") as raw_temp:
            temp = Path(raw_temp)
            with _local_research_mcp() as research_mcp_url:
                client = HarnessClient(
                    HarnessConfig(
                        cwd=str(temp),
                        env={
                            "DSH_CORDIS_CONFIG": str(profile.path),
                            "DSH_SESSION_ROOT": str(temp / "sessions"),
                            "DSH_CWD": str(temp),
                            "DECISION_HUB_RESEARCH_MCP_URL": research_mcp_url,
                            "DEEPSEEK_API_KEY": "sk-dummy-local-handshake-only",
                            "DEEPSEEK_BASE_URL": "http://127.0.0.1:9",
                        },
                        request_timeout_seconds=20,
                        shutdown_timeout_seconds=2,
                    )
                )
                client.start()
                try:
                    initialized = client.initialize(
                        cwd=str(temp),
                        provider=selected.provider,
                        model=selected.model,
                        max_tokens=selected.max_tokens,
                    )
                finally:
                    client.close()
        server = initialized.serverInfo
        return DshReadinessReport(
            status="ready" if secret_configured else "local_ready_provider_secret_missing",
            checked_at=checked_at,
            sdk_version=sdk_version,
            runtime_name=server.name if server else None,
            runtime_version=server.version if server else None,
            profile_ref=profile.profile_ref,
            profile_hash=profile.content_hash,
            provider=selected.provider,
            model=selected.model,
            provider_secret_configured=secret_configured,
            local_handshake=True,
            error_code=None,
        )
    except (ImportError, importlib.metadata.PackageNotFoundError):
        error_code = "dsh_sdk_unavailable"
    except BaseException as exc:
        error_code = str(exc) if str(exc).startswith("dsh_") else "dsh_handshake_failed"
    return DshReadinessReport(
        status="not_ready",
        checked_at=checked_at,
        sdk_version=None,
        runtime_name=None,
        runtime_version=None,
        profile_ref=None,
        profile_hash=None,
        provider=selected.provider,
        model=selected.model,
        provider_secret_configured=secret_configured,
        local_handshake=False,
        error_code=error_code,
    )


@contextmanager
def _local_research_mcp() -> Generator[str]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = int(listener.getsockname()[1])
    environment = {
        **os.environ,
        "DECISION_HUB_RESEARCH_CAPABILITIES": "",
        "DECISION_HUB_RESEARCH_MCP_HOST": "127.0.0.1",
        "DECISION_HUB_RESEARCH_MCP_PORT": str(port),
        "PYTHONPATH": str(Path.cwd()),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "apps.research_mcp.main"],
        cwd=Path.cwd(),
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("dsh_research_mcp_startup_failed")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("dsh_research_mcp_startup_failed")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
