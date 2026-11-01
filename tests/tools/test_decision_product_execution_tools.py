from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from tools.acceptance import validate_market_risk_plan as plan
from tools.context import build_task_context as context


def execution_contract(folder: Path) -> dict:
    (folder / "03_RUNTIME.md").write_text("# runtime\n", encoding="utf-8")
    return {
        "schema_version": "decision-product-execution.v1",
        "baseline": "review-5",
        "approval_ref": "owner approved implementation",
        "implementation_authorized": True,
        "phase": "implementation_in_progress",
        "goal_status_observed": "unavailable",
        "scope": {
            "surface": "pc",
            "market": "linear_perpetual",
            "instruments": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
            "real_auto_trading": False,
        },
        "engineering_pass_implies_predictive_gain": False,
        "current_slice": {
            "slice_id": "D1-01",
            "status": "in_progress",
            "user_outcome": "sameRun report and action in PC",
            "allowed_paths": ["extensions/dsh/decision-hub/src/host/bridge.ts"],
            "contract_refs": ["03_RUNTIME.md#runtime"],
            "forbidden_paths": ["unrelated modules"],
            "direct_checks": ["real native success and PC readback"],
            "close_when": "sameRun success",
            "same_blocker_failed_attempts": 0,
        },
    }


def test_approved_perpetual_plan_does_not_depend_on_goal_card(tmp_path: Path) -> None:
    checks = plan.check_execution_contract(execution_contract(tmp_path), tmp_path)
    assert checks and all(checks.values())


@pytest.mark.parametrize(
    ("change", "failed_check"),
    [
        ({"implementation_authorized": False}, "authorized_implementation"),
        ({"other_active_slices": ["D2-02"]}, "one_current_slice"),
        ({"engineering_pass_implies_predictive_gain": True}, "no_false_predictive_claim"),
    ],
)
def test_invalid_execution_state_is_rejected(
    tmp_path: Path, change: dict, failed_check: str
) -> None:
    contract = execution_contract(tmp_path)
    contract.update(change)
    assert plan.check_execution_contract(contract, tmp_path)[failed_check] is False


def test_unbounded_or_parent_paths_cannot_authorize_work(tmp_path: Path) -> None:
    contract = execution_contract(tmp_path)
    for path in ("**", "../other-project", "E:/outside"):
        altered = deepcopy(contract)
        altered["current_slice"]["allowed_paths"] = [path]
        assert plan.check_execution_contract(altered, tmp_path)["bounded_paths"] is False


def test_context_uses_current_task_permissions_and_contracts(tmp_path: Path) -> None:
    contract = execution_contract(tmp_path)
    rendered = context.render_contract_context(contract, "abc123")
    assert "extensions/dsh/decision-hub/src/host/bridge.ts" in rendered
    assert "03_RUNTIME.md#runtime" in rendered
    assert "real native success and PC readback" in rendered
    assert "DSH`/`Pi` runtime internals" not in rendered
    assert "text-envelope.v1" not in rendered
