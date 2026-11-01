from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest

from tools.research_evaluation.plan_core_data_inputs import plan


@pytest.mark.parametrize(
    ("dataset", "expected_cases"),
    [("r2r_dev_core_v3", 6), ("r2r_holdout_core_v3", 6)],
)
def test_core_data_input_plan_covers_every_frozen_case(dataset: str, expected_cases: int) -> None:
    payload = plan(dataset)
    assert payload["schema_version"] == "core-data-input-plan.v1"
    assert payload["source_timezone"] == "America/Chicago"
    manifest_bytes = (
        Path("packs/crypto_macro/evaluations") / dataset / "manifest.json"
    ).read_bytes()
    assert payload["source_manifest_hash"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert len(cast(dict[str, str], payload["source_fixture_hashes"])) == expected_cases
    cases = cast(list[dict[str, Any]], payload["cases"])
    assert len(cases) == expected_cases
    macro_instruments = {
        str(item["instrument"])
        for item in cast(dict[str, list[dict[str, Any]]], cases[0]["requested_slots"])[
            "macro_transmission"
        ]
    }
    assert macro_instruments == {"US2Y", "DXY"}
    assert all(
        len(cast(dict[str, list[dict[str, Any]]], case["requested_slots"])["macro_transmission"])
        == 4
        for case in cases
    )
    summary = cast(dict[str, Any], payload["summary"])
    assert summary["by_requirement"]["macro_transmission"]["hard_cases"] == expected_cases - 1
    assert summary["by_requirement"]["crypto_spot_confirmation"]["hard_cases"] == expected_cases
    assert summary["by_requirement"]["derivatives_crowding"]["hard_cases"] == expected_cases


def test_core_data_input_plan_rejects_path_escape() -> None:
    with pytest.raises(ValueError, match="core_data_input_dataset_invalid"):
        plan("../r2r_dev_core_v3")


def test_core_data_input_plan_rejects_invalid_timezone() -> None:
    with pytest.raises(ValueError, match="core_data_input_timezone_invalid"):
        plan("r2r_dev_core_v3", source_timezone="Mars/Olympus")


def test_core_data_input_plan_is_reproducible() -> None:
    first = json.dumps(plan("r2r_dev_core_v3"), sort_keys=True)
    second = json.dumps(plan("r2r_dev_core_v3"), sort_keys=True)
    assert first == second
