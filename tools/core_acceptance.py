from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9]{20,}")


def _run(command: list[str]) -> None:
    print("$", " ".join(command))
    completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def _secret_scan() -> None:
    ignored = {".git", ".venv", "node_modules", "dist", "__pycache__", ".pytest_cache"}
    matches: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in ignored for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if _SECRET_PATTERN.search(text):
            matches.append(str(path.relative_to(ROOT)))
    if matches:
        raise SystemExit(f"secret scan failed: {matches}")


def _durability_and_replay_smoke() -> None:
    from packages.kernel.decision_hub_kernel.persistence.db import Database
    from tools.ops.database import backup, integrity, restore
    from tools.replay.run_fixture import compare_fixture, run_fixture

    fixture = ROOT / "fixtures" / "replay" / "powell-higher-for-longer.json"
    with tempfile.TemporaryDirectory(prefix="decision-hub-acceptance-") as temp_dir:
        temp = Path(temp_dir)
        baseline_path = temp / "baseline.sqlite3"
        candidate_path = temp / "candidate.sqlite3"
        comparison = asyncio.run(compare_fixture(fixture, baseline_path, candidate_path))
        comparison_meta = comparison["comparison"]
        if not isinstance(comparison_meta, dict):
            raise SystemExit("replay comparison metadata is invalid")
        if comparison_meta.get("same_snapshot") is not True:
            raise SystemExit("replay snapshot is not deterministic")
        if comparison_meta.get("candidate_overwrote_baseline") is not False:
            raise SystemExit("candidate replay overwrote baseline")
        baseline = comparison.get("baseline")
        if not isinstance(baseline, dict) or baseline.get("evaluation_count") != 3:
            raise SystemExit("baseline evaluation evidence is incomplete")

        migrated_path = temp / "migrated.sqlite3"
        migrated = Database(f"sqlite+pysqlite:///{migrated_path}")
        migrated.initialize()
        with migrated.session() as session:
            revision = session.execute(
                __import__("sqlalchemy").text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        expected_head = migrated.expected_migration_heads()
        if revision not in expected_head:
            raise SystemExit(f"unexpected migration head: {revision}")

        live_path = temp / "live.sqlite3"
        asyncio.run(run_fixture(fixture, live_path))
        backup_path = temp / "backup.sqlite3"
        restored_path = temp / "restored.sqlite3"
        backup(live_path, backup_path)
        if not integrity(backup_path):
            raise SystemExit("backup integrity check failed")
        restore(backup_path, restored_path)
        if not integrity(restored_path):
            raise SystemExit("restore integrity check failed")
        restored = Database(f"sqlite+pysqlite:///{restored_path}")
        restored_inspector = restored.get_run_inspector(restored.latest_runs(1)[0].run_id)
        if restored_inspector is None or restored_inspector.artifact is None:
            raise SystemExit("restored database lost the run inspector artifact")
        print(json.dumps({"replay": "ok", "durability": "ok"}, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the offline Decision Hub core acceptance gate"
    )
    parser.add_argument("--skip-frontend", action="store_true")
    args = parser.parse_args()
    commands = [
        [sys.executable, "-m", "pytest", "-m", "not live", "-q"],
        [sys.executable, "-m", "ruff", "check", "packages", "apps", "migrations", "tests", "tools"],
        [sys.executable, "-m", "pyright", "packages", "apps", "tests", "tools/canary"],
        [sys.executable, "-m", "tools.contract_codegen", "check"],
        [sys.executable, str(ROOT / "tools" / "docs" / "check_module_docs.py")],
    ]
    if not args.skip_frontend:
        commands.extend(
            [
                ["pnpm", "--dir", "apps/decision-desk", "test"],
                ["pnpm", "--dir", "apps/decision-desk", "build"],
            ]
        )
    try:
        for command in commands:
            _run(command)
        _secret_scan()
        _durability_and_replay_smoke()
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
