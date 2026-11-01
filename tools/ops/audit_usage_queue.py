"""Read-only usage queue inventory, lateness and immutable receipt verification."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path


def utc_stamp(value):
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def audit(database: Path, *, now=None):
    now = now or datetime.now(UTC)
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT j.*, r.status AS run_status, s.payload_json FROM usage_collection_jobs j "
            "LEFT JOIN runs r ON r.run_id=j.run_id "
            "LEFT JOIN research_usage_snapshots s ON s.snapshot_id=j.last_snapshot_id"
        ).fetchall()
        invalid = 0
        receipt_count = 0
        for row in db.execute("SELECT payload_json,payload_sha256 FROM research_usage_snapshots"):
            receipt_count += 1
            canonical = json.dumps(
                json.loads(row[0]), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            invalid += hashlib.sha256(canonical.encode()).hexdigest() != row[1]
    states, run_states, issues, limitations = Counter(), Counter(), Counter(), Counter()
    overdue, overdue_ten, longest = 0, 0, 0
    intervals = []
    for row in rows:
        states[row["status"]] += 1
        run_states[row["run_status"]] += 1
        due = utc_stamp(row["next_check_at"])
        late = max(0, (now - due).total_seconds())
        overdue += late > 0
        overdue_ten += late > 600
        longest = max(longest, late)
        if row["checked_at"]:
            checked = utc_stamp(row["checked_at"])
            intervals.append(round((due - checked).total_seconds()))
        if row["payload_json"]:
            payload = json.loads(row["payload_json"])
            limitations.update(payload["view"]["limitations"])
            for session in payload["view"]["sessions"]:
                issues.update(session["issues"])
    return {
        "schema_version": "usage-queue-audit.v1", "checked_at": now.isoformat(),
        "session_versions": len(rows), "unique_runs": len({row["run_id"] for row in rows}),
        "states": dict(states), "run_states": dict(run_states), "due": overdue,
        "overdue_10m": overdue_ten, "maximum_lateness_seconds": round(longest),
        "scheduled_intervals_seconds": dict(sorted(Counter(intervals).items())),
        "latest_receipt_session_issues": dict(issues),
        "latest_receipt_limitations": dict(limitations),
        "verified_receipts": receipt_count, "invalid_receipt_hashes": invalid,
        "note": "Counts describe bound session versions; they are not summed tokens or a bill.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps({key: result[key] for key in (
        "checked_at", "session_versions", "states", "due", "overdue_10m",
        "maximum_lateness_seconds", "verified_receipts", "invalid_receipt_hashes",
    )}))
    if result["invalid_receipt_hashes"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
