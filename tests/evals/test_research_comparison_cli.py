from __future__ import annotations

import argparse

import pytest

from packages.evals.research_dataset import load_research_evaluation_dataset
from tools.research_evaluation.run_runtime_comparison import (
    DEFAULT_DATASET,
    main,
    require_live_authorization,
    select_case,
)


def test_live_comparison_requires_explicit_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "DECISION_HUB_R2R_LIVE_EVAL",
        "DECISION_HUB_LLM_ENABLED",
        "DECISION_HUB_DSH_API_KEY",
        "DEEPSEEK_API_KEY",
        "SUB2API_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="DECISION_HUB_R2R_LIVE_EVAL"):
        require_live_authorization()


def test_case_selection_is_exact_and_preserves_manifest() -> None:
    dataset = load_research_evaluation_dataset(DEFAULT_DATASET)

    selected = select_case(dataset, "powell_stanford_20240403")

    assert selected.manifest == dataset.manifest
    assert [item.case_id for item in selected.cases] == ["powell_stanford_20240403"]
    assert len(selected.case_paths) == 1
    with pytest.raises(ValueError, match="research_evaluation_case_not_found"):
        select_case(dataset, "missing-case")


def test_cli_error_output_does_not_render_exception_detail(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "sk-secret-must-not-render"
    monkeypatch.setattr(
        "tools.research_evaluation.run_runtime_comparison._parse_args",
        lambda: argparse.Namespace(),
    )
    async def fake_run(_args: argparse.Namespace) -> dict[str, object]:
        raise RuntimeError(secret)

    monkeypatch.setattr(
        "tools.research_evaluation.run_runtime_comparison.run", fake_run
    )

    assert main() == 1
    output = capsys.readouterr().out
    assert secret not in output
    assert "RuntimeError" in output
