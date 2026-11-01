from __future__ import annotations

from pathlib import Path

import pytest

from apps.hub_api.server_config import graceful_shutdown_timeout_seconds


@pytest.mark.parametrize("seconds", [5, 30, 120])
def test_hub_api_graceful_shutdown_timeout_accepts_bounded_values(
    monkeypatch, seconds: int
) -> None:
    monkeypatch.setenv("HUB_API_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS", str(seconds))
    assert graceful_shutdown_timeout_seconds() == seconds


@pytest.mark.parametrize("value", ["4", "121", "none", "30.5"])
def test_hub_api_graceful_shutdown_timeout_rejects_unbounded_values(
    monkeypatch, value: str
) -> None:
    monkeypatch.setenv("HUB_API_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError, match="hub_api_graceful_shutdown_timeout_invalid"):
        graceful_shutdown_timeout_seconds()


def test_hub_api_run_passes_graceful_shutdown_bound_to_uvicorn() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "apps/hub_api/main.py").read_text(encoding="utf-8")

    assert "from apps.hub_api.server_config import graceful_shutdown_timeout_seconds" in source
    assert "timeout_graceful_shutdown=graceful_shutdown_timeout_seconds()" in source
