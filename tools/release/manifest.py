from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _git_revision() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def _worktree_dirty() -> bool:
    return bool(
        subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=ROOT,
            text=True,
        ).strip()
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest() -> dict[str, object]:
    schema_manifest = ROOT / "contracts" / "generated-manifest.yaml"
    return {
        "manifest_version": "release-manifest.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "git_revision": _git_revision(),
        "worktree_dirty": _worktree_dirty(),
        "schema_manifest_sha256": _sha256(schema_manifest),
        "strategy_version": "baseline.v1",
        "pack_version": "crypto_macro.v1",
        "runtime_versions": ["fake.v1", "replay.v1", "langgraph-native.v1"],
        "migrations_head": "0007_run_cost_nullable",
        "verified": [
            "text admission and PIT snapshot",
            "LangGraph research and deterministic Gate",
            "Artifact/Forecast/Outcome/Evaluation",
            "provider failure taxonomy and bounded retry",
            "Run/Step/Attempt/Call normalized inspector",
            "checkpoint recovery and idempotent commit",
            "SQLite backup/restore/integrity/retention smoke",
            "offline PIT replay and baseline/candidate comparison",
        ],
        "opt_in_only": ["external Provider live canary"],
        "not_claimed": [
            "forecast accuracy",
            "profitability",
            "real-time news/calendar/market feeds",
            "ASR/live meeting monitoring",
            "notifications",
            "DSH/Pi production integration",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the Decision Hub R0 release manifest")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "RELEASE_MANIFEST.json")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(build_manifest(), ensure_ascii=False, indent=2) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
