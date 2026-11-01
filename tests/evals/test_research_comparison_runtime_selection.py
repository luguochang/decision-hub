"""Exercise CLI runtime construction without dispatching a paid comparison."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.evals.research_runtime import CaseScopedDshWebResearchRuntime
from packages.runtime_adapters.dsh_runtime import DshRuntimeConfig, DshWebHostConfig
from tools.research_evaluation import run_runtime_comparison as cli


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("selection", "expected_runtime", "product_graph"),
    [
        ("dsh", "dsh", None),
        ("dsh-web", "dsh-web", False),
        ("dsh-web-graph", "dsh-web-graph", True),
    ],
)
async def test_cli_constructs_the_selected_runtime_without_sdk_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    selection: str,
    expected_runtime: str,
    product_graph: bool | None,
) -> None:
    captured: dict[str, object] = {}
    counts = {"sdk_constructions": 0, "database_initializations": 0}

    class Baseline:
        runtime_id = "fixed-baseline"

        async def close(self) -> None:
            captured["baseline_closed"] = True

    class SdkRuntime:
        runtime_id = "dsh"

        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            counts["sdk_constructions"] += 1
            assert selection == "dsh", "Web selection must never construct the SDK adapter"

        async def close(self) -> None:
            captured["sdk_closed"] = True

    class IsolatedDatabase:
        def __init__(self, url: str) -> None:
            assert url == "sqlite+pysqlite:///selection-only-test.sqlite3"

        def initialize(self) -> None:
            counts["database_initializations"] += 1

    class RecordingComparisonRunner:
        def __init__(self, dataset: object, **kwargs: object) -> None:
            del dataset, kwargs

        async def run(
            self, *, experiment_id: str, baseline: object, candidate: object,
        ) -> SimpleNamespace:
            # Inspect the real Web runtime instance; never execute either runtime.
            captured["candidate"] = candidate
            assert isinstance(baseline, Baseline)
            assert isinstance(candidate, (SdkRuntime, CaseScopedDshWebResearchRuntime))
            assert baseline.runtime_id == "fixed-baseline"
            assert candidate.runtime_id == expected_runtime
            summaries = [
                SimpleNamespace(runtime_id=identity, model_dump=lambda **kwargs: {})
                for identity in ("fixed-baseline", expected_runtime)
            ]
            return SimpleNamespace(
                experiment_id=experiment_id,
                dataset_id="selection-only-test",
                baseline_runtime_id="fixed-baseline",
                candidate_runtime_id=expected_runtime,
                summaries=summaries,
            )

    def forbid_asset_writes(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("Single-case selection must not register evaluation assets")

    monkeypatch.setattr(sys, "argv", [
        "run_runtime_comparison",
        "--case-id", "powell_stanford_20240403",
        "--candidate-runtime", selection,
        "--database-url", "sqlite+pysqlite:///selection-only-test.sqlite3",
        "--output-dir", str(tmp_path),
        "--research-mcp-port", "18002",
    ])
    monkeypatch.setattr(cli, "require_live_authorization", lambda: None)
    monkeypatch.setattr(cli, "LangGraphAgentRuntime", lambda **kwargs: object())
    monkeypatch.setattr(cli, "FixedResearchRuntime", lambda runtime: Baseline())
    monkeypatch.setattr(cli.ProviderConfig, "from_env", lambda: SimpleNamespace(
        provider_id="selection-test", model="no-model-dispatch",
    ))
    monkeypatch.setattr(cli.DshWebHostConfig, "from_env", lambda: DshWebHostConfig(
        base_url="http://127.0.0.1:18097", bridge_key="selection-test-key",
    ))
    monkeypatch.setattr(cli.DshRuntimeConfig, "from_env", lambda: DshRuntimeConfig(
        workspace=tmp_path, session_root=tmp_path / "sdk-sessions",
    ))
    monkeypatch.setattr(cli, "Database", IsolatedDatabase)
    monkeypatch.setattr(cli, "CaseScopedDshResearchRuntime", SdkRuntime)
    monkeypatch.setattr(cli, "ResearchComparisonRunner", RecordingComparisonRunner)
    monkeypatch.setattr(cli, "ResearchComparisonAssetRecorder", forbid_asset_writes)

    result = await cli.run(cli._parse_args())

    candidate = captured["candidate"]
    if product_graph is None:
        assert isinstance(candidate, SdkRuntime)
        assert counts["sdk_constructions"] == 1
        assert counts["database_initializations"] == 0
        assert captured["sdk_closed"] is True
    else:
        assert isinstance(candidate, CaseScopedDshWebResearchRuntime)
        assert candidate.product_graph is product_graph
        assert candidate.research_mcp_port == 18002
        assert counts["sdk_constructions"] == 0
        assert counts["database_initializations"] == 1
    assert captured["baseline_closed"] is True
    assert result["status"] == "completed"
    assert result["case_count"] == 1
    assert result["assets_recorded"] is False
    assert result["active_pointer_changed"] is False
