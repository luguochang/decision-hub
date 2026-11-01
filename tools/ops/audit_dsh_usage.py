"""Read-only provider-usage audit; never exports messages or invents a total bill."""

import argparse
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from packages.query_views.research_usage import _session_usage, _stamp
from packages.query_views.usage_accounting import summarize as summarize


def audit(database: Path, sessions: Path, run_id: str):
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute(
            "SELECT dsh_session_id,state,generation FROM dsh_session_links WHERE run_id=?",
            (run_id,),
        ).fetchone()
    if row is None or not re.fullmatch(r"[a-zA-Z0-9_-]+", row[0]):
        raise ValueError("missing_or_invalid_session_identity")
    matches = list(sessions.glob(f"*/{row[0]}/session.jsonl.zstd"))
    if len(matches) != 1:
        raise ValueError("session_archive_not_unique")
    if not matches[0].resolve().is_relative_to(sessions.resolve()):
        raise ValueError("session_archive_outside_root")
    meta, summary, digest = _session_usage(*_stamp(matches[0]))
    if meta.get("id") != row[0]:
        raise ValueError("session_archive_identity_mismatch")
    return {
        "schema_version": "dsh-usage-audit.v1",
        "run_id": run_id,
        "dsh_session_id": row[0],
        "ledger_state": row[1],
        "generation": row[2],
        "observed_at": datetime.now(UTC).isoformat(),
        "archive_sha256": digest,
        **summary,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--sessions", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.database, args.sessions, args.run_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
