from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def render_contract_context(contract: dict[str, Any], commit: str) -> str:
    task = contract["current_slice"]
    sections = {
        "Allowed paths": task["allowed_paths"],
        "Contract references": task["contract_refs"],
        "Forbidden paths and effects": task.get("forbidden_paths", []),
        "Required checks": task["direct_checks"],
        "Uncovered boundaries": task.get("uncovered_boundaries", []),
    }
    lines = [
        "# Task Context Manifest", "",
        f"- task_id: `{task['slice_id']}`",
        f"- objective: {task['user_outcome']}",
        f"- baseline_commit: `{commit}`",
        f"- design_baseline: `{contract['baseline']}`",
        f"- authorization_phase: `{contract['phase']}`",
        f"- close_when: {task['close_when']}",
        "- This is a task projection; authority remains the execution contract.",
    ]
    for title, values in sections.items():
        lines.extend(["", f"## {title}", *[f"- {value}" for value in values]])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--objective")
    parser.add_argument("--paths", nargs="+")
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--out", default="tmp/task-context.md")
    args = parser.parse_args()
    if args.contract is None and (not args.objective or not args.paths):
        parser.error("supply --contract or both --objective and --paths")
    if args.contract is not None and (args.objective or args.paths):
        parser.error("--contract cannot be overridden by --objective or --paths")
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        commit = "uncommitted-worktree"
    if args.contract is not None:
        contract_path = args.contract.resolve()
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        from tools.acceptance.validate_market_risk_plan import check_execution_contract

        checks = check_execution_contract(contract, contract_path.parent)
        if not all(checks.values()):
            parser.error("invalid execution contract: " + ", ".join(
                name for name, passed in checks.items() if not passed
            ))
        destination = ROOT / args.out
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(render_contract_context(contract, commit), encoding="utf-8")
        print(destination)
        return 0
    paths = "\n".join(f"- `{path}`" for path in args.paths)
    lines = [
        "# Task Context Manifest",
        "",
        f"- task_id: `{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}`",
        f"- objective: {args.objective}",
        f"- baseline_commit: `{commit}`",
        "- active_contract_versions: `contracts.v1`, `text-envelope.v1`, `run-event.v1`",
        "- active_adr_ids: `ADR-0001`",
        "",
        "## Allowed paths",
        paths,
        "",
        "## Forbidden by default",
        "- `DSH`/`Pi` runtime internals",
        "- database schema outside a migration",
        "- generated contract mirrors",
        "- unrelated modules",
        "",
        "## Required checks",
        "- canonical schema check",
        "- affected module README update",
        "- unit/contract/replay tests",
        "- rollback target recorded",
        "",
    ]
    text = "\n".join(lines)
    destination = ROOT / args.out
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
