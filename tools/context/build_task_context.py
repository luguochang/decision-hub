from __future__ import annotations

import argparse
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--objective", required=True)
    parser.add_argument("--paths", nargs="+", required=True)
    parser.add_argument("--out", default="tmp/task-context.md")
    args = parser.parse_args()
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        commit = "uncommitted-worktree"
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
    destination.write_text(text)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
