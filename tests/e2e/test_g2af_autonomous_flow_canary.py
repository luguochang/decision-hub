# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.runtime_adapters.dsh_runtime import DshWebHostConfig
from tools.canary import run_g2af_autonomous_flow_canary as canary


def test_autonomous_canary_requires_explicit_network_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DECISION_HUB_G2AF_AUTONOMOUS_CANARY", raising=False)

    with pytest.raises(RuntimeError, match="DECISION_HUB_G2AF_AUTONOMOUS_CANARY=1"):
        asyncio.run(canary._run())


def test_autonomous_canary_rejects_non_temporary_product_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    product_dir = tmp_path / "decision-hub-product"
    (product_dir / "db").mkdir(parents=True)
    (product_dir / "db" / "decision_hub.sqlite3").touch()
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(product_dir))

    with pytest.raises(RuntimeError, match="decision-hub-g2af"):
        canary._isolated_data_dir()


def test_autonomous_canary_accepts_named_isolated_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "decision-hub-g2af-test"
    (data_dir / "db").mkdir(parents=True)
    (data_dir / "db" / "decision_hub.sqlite3").touch()
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(data_dir))

    assert canary._isolated_data_dir() == data_dir.resolve()


def test_autonomous_canary_accepts_only_loopback_dsh_host() -> None:
    with pytest.raises(RuntimeError, match="loopback"):
        canary._require_loopback_host(
            DshWebHostConfig(
                base_url="https://dsh.example.test",
                bridge_key="fixture-secret",
            )
        )


def test_autonomous_canary_requires_an_actual_dsh_native_search_invocation() -> None:
    without_search = SimpleNamespace(
        rounds=[SimpleNamespace(tool_invocations=[SimpleNamespace(capability_id="web.fetch")])]
    )
    with_search = SimpleNamespace(
        rounds=[
            SimpleNamespace(
                tool_invocations=[SimpleNamespace(capability_id="dsh.native.web_search")]
            )
        ]
    )

    assert not canary._used_native_search(without_search)
    assert canary._used_native_search(with_search)
