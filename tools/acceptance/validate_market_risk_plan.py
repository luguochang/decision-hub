"""Check execution scope and evidence references; this never tests the product."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Any


def check_execution_contract(contract: dict[str, Any], folder: Path) -> dict[str, bool]:
    task = contract.get("current_slice", {})
    paths = task.get("allowed_paths", [])
    refs = task.get("contract_refs", [])
    scope = contract.get("scope", {})
    return {
        "supported_schema": contract.get("schema_version") == "decision-product-execution.v1",
        "authorized_implementation": contract.get("implementation_authorized") is True
        and contract.get("phase") == "implementation_in_progress"
        and bool(contract.get("approval_ref")),
        "one_current_slice": task.get("status") == "in_progress"
        and bool(task.get("slice_id"))
        and not contract.get("other_active_slices"),
        "bounded_paths": bool(paths) and all(
            isinstance(path, str) and path not in ("", ".")
            and not PureWindowsPath(path).drive and not Path(path).is_absolute()
            and ".." not in PureWindowsPath(path).parts
            and not any(char in path for char in "*?") for path in paths
        ),
        "contract_references_exist": bool(refs)
        and all((folder / ref.split("#", 1)[0]).is_file() for ref in refs),
        "task_has_result_and_checks": bool(task.get("user_outcome"))
        and bool(task.get("direct_checks")) and bool(task.get("close_when")),
        "pc_perpetual_identity": scope.get("surface") == "pc"
        and scope.get("market") == "linear_perpetual"
        and scope.get("instruments") == ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
        and scope.get("real_auto_trading") is False,
        "no_false_predictive_claim":
            contract.get("engineering_pass_implies_predictive_gain") is False,
    }


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.contract is not None:
        contract_path = args.contract.resolve()
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        checks = check_execution_contract(contract, contract_path.parent)
        receipt = {
            "at": datetime.now(UTC).isoformat(), "scope": "plan consistency only",
            "checks": checks,
            "contract_sha256": hashlib.sha256(contract_path.read_bytes()).hexdigest(),
        }
        output = args.out or contract_path.parent / "plan-consistency.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
        print(json.dumps({"checks": checks, "receipt": str(output)}, ensure_ascii=False))
        return 0 if all(checks.values()) else 1
    folder = repo / "docs/design/market-risk-v1"
    contract = json.loads((folder / "execution-contract.json").read_text(encoding="utf-8"))
    checklist = (folder / "IMPLEMENTATION_CHECKLIST.md").read_text(encoding="utf-8")
    focus = (folder / "CURRENT_FOCUS.md").read_text(encoding="utf-8")
    items = re.findall(r"^\| (MR\d{2}) \|", checklist, re.MULTILINE)
    batches = contract["batches"]
    active = [batch["id"] for batch in batches if batch["status"] == "in_progress"]
    missing = []
    for name in contract["documentation"].values():
        if not (folder / name).is_file():
            missing.append(name)
    checks = {
        "authorized_active_goal": contract["mode"] == "implementation_authorized"
        and contract["new_goal_created"]
        and contract["new_goal_status"] == "active",
        "all_33_items_unique": len(items) == len(set(items)) == 33,
        "one_current_batch": active == [contract["current_batch"]],
        "focus_bounded": len(focus.splitlines()) <= 80,
        "documentation_exists": not missing,
        "pc_and_spot_identity": contract["scope"]["surface"] == "pc"
        and contract["scope"]["market"] == "spot"
        and contract["scope"]["instruments"] == ["BTC-USDT", "ETH-USDT"],
        "no_false_predictive_claim": not contract["gates"][
            "engineering_pass_implies_predictive_gain"
        ],
    }
    receipt = {
        "at": datetime.now(UTC).isoformat(),
        "scope": "plan consistency only",
        "checks": checks,
        "missing": missing,
        "document_hashes": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.iterdir()
            if p.suffix in (".md", ".json")
        },
    }
    output = args.out or (
        repo / "artifacts/market-risk-implementation-20261007/B0/plan-consistency.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"checks": checks, "receipt": str(output)}, ensure_ascii=False))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
