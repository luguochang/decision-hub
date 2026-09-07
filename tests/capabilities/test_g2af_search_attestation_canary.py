# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from tools.canary import run_g2af_search_attestation_canary as canary


def test_g2af_canary_requires_explicit_network_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DECISION_HUB_G2AF_CANARY", raising=False)

    with pytest.raises(RuntimeError, match="DECISION_HUB_G2AF_CANARY=1"):
        asyncio.run(canary._run())


def test_g2af_canary_query_keeps_target_url_typed() -> None:
    query = canary._query(
        request_id="test-fetch",
        capability_id="web.fetch",
        requirement_id="event_identity",
        session_id="dsh-test-session",
        now=datetime(2026, 9, 4, tzinfo=UTC),
        target_url="https://www.federalreserve.gov/newsevents/speech/example.htm",
    )

    assert str(query.target_url).startswith("https://www.federalreserve.gov/")
    assert query.allowed_domains == ["federalreserve.gov"]
    assert query.mode == "live"
