from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict, cast

import yaml

ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / "packs" / "crypto_macro"


class _Horizon(TypedDict):
    horizon: str
    action: str
    probability: float
    trigger: str
    invalidation: str


class _Baseline(TypedDict):
    runtime: str
    status: str
    gate_status: str
    elapsed_ms: int
    tool_call_count: int
    probability_provenance: str | None
    horizons: list[_Horizon]


class _FailureFixture(TypedDict):
    baseline: _Baseline
    declared_missing_evidence: list[str]
    expected_violations: list[str]


def _fixture() -> _FailureFixture:
    return cast(
        _FailureFixture,
        json.loads(
            (PACK_ROOT / "fixtures/warsh-insufficient-evidence.json").read_text(encoding="utf-8")
        ),
    )


def test_warsh_fixture_reproduces_the_fixed_workflow_failure() -> None:
    fixture = _fixture()
    baseline = fixture["baseline"]
    horizons = baseline["horizons"]

    assert baseline["status"] == "degraded"
    assert baseline["gate_status"] == "research_only"
    assert baseline["elapsed_ms"] == 93700
    assert baseline["tool_call_count"] == 0
    assert len(fixture["declared_missing_evidence"]) >= 8

    signatures = {
        (
            item["action"],
            item["probability"],
            item["trigger"],
            item["invalidation"],
        )
        for item in horizons
    }
    assert {item["horizon"] for item in horizons} == {"30m", "24h", "72h"}
    assert len(signatures) == 1
    assert baseline["probability_provenance"] is None

    assert set(fixture["expected_violations"]) == {
        "missing_hard_evidence",
        "no_tool_continuation",
        "horizon_output_not_distinct",
        "uncalibrated_probability",
        "not_agentic_runtime",
    }


def test_pack_gate_explicitly_rejects_the_known_failure_shape() -> None:
    policy = yaml.safe_load((PACK_ROOT / "gates/horizons.yaml").read_text(encoding="utf-8"))

    assert policy["directional_publish"]["require_all_hard_evidence"] is True
    assert policy["horizon_independence"]["reject_exact_duplicate_signature"] is True
    assert policy["bounded_stop"]["unresolved_hard_gap_gate"] == "research_only"
