"""Verify timing against one immutable DSH archive without writing product data."""

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from packages.runtime_adapters.dsh_runtime.event_mapper import map_notifications
from packages.runtime_adapters.dsh_runtime.session_archive import load_dsh_session_archive


def verify(archive_path: Path, session_id: str, run_id: str, database: Path) -> dict:
    archive = load_dsh_session_archive(archive_path, expected_session_id=session_id)
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        link = connection.execute(
            "SELECT run_id FROM dsh_session_links WHERE dsh_session_id = ?", (session_id,)
        ).fetchone()
    finally:
        connection.close()
    if link is None or link[0] != run_id:
        raise ValueError("archive_run_identity_mismatch")
    traces = map_notifications(
        archive.notifications,
        run_id=run_id,
        research_session_id=session_id,
        received_at=archive.created_at,
    )
    starts = {}
    intervals = []
    unmatched_terminals = []
    for trace in traces:
        if trace.event_type == "tool_started":
            if trace.reference_id in starts:
                raise ValueError("duplicate_open_tool_call")
            starts[trace.reference_id] = trace.occurred_at
        elif trace.event_type in {"tool_completed", "tool_failed"}:
            started_at = starts.pop(trace.reference_id, None)
            if started_at is None:
                unmatched_terminals.append(trace.reference_id)
                continue
            latency_ms = round((trace.occurred_at - started_at).total_seconds() * 1000)
            intervals.append(
                {
                    "call_id": trace.reference_id,
                    "started_at": started_at.isoformat(),
                    "finished_at": trace.occurred_at.isoformat(),
                    "latency_ms": latency_ms,
                }
            )
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "archive": str(archive.path),
        "archive_sha256": archive.archive_sha256,
        "session_id": archive.session_id,
        "run_id": run_id,
        "identity_basis": "archive session header + readonly database session link",
        "events": len(archive.events),
        "trace_events": len(traces),
        "unique_trace_timestamps": len({trace.occurred_at for trace in traces}),
        "matched_calls": len(intervals),
        "positive_intervals": sum(item["latency_ms"] > 0 for item in intervals),
        "invalid_intervals": sum(item["latency_ms"] < 0 for item in intervals),
        "unclosed_calls": list(starts),
        "unmatched_terminals": unmatched_terminals,
        "intervals": intervals,
        "historical_database_modified": False,
        "basis": "archived DSH event timestamps; no historical report correction",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    proof = verify(args.archive, args.session_id, args.run_id, args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Proofs are append-only by filename; never replace an earlier verification.
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(proof, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"saved": str(args.output), "matched_calls": proof["matched_calls"]}))
    if proof["invalid_intervals"] or not proof["matched_calls"]:
        raise SystemExit("archive has no valid matched timing or contains negative intervals")


if __name__ == "__main__":
    main()
