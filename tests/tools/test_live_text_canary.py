from __future__ import annotations

import asyncio

import pytest

from tools.canary.run_live_text_canary import redact_for_output, run_canary


def test_canary_redacts_secret_and_authorization() -> None:
    rendered = redact_for_output(
        "authorization=temporary-secret bearer temporary-secret",
        "temporary-secret",
    )

    assert "temporary-secret" not in rendered
    assert "<redacted>" in rendered


def test_canary_requires_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")

    with pytest.raises(RuntimeError, match="opt-in"):
        asyncio.run(run_canary("synthetic fixture"))
