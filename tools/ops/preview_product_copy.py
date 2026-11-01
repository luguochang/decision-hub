"""Serve a staged frontend and new API on an isolated snapshot, without workers."""

import argparse
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--frontend", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8067)
    args = parser.parse_args()
    source, destination, frontend = (
        args.database.resolve(),
        args.output_dir.resolve(),
        args.frontend.resolve(),
    )
    if not source.is_file() or destination.exists() or not (frontend / "index.html").is_file():
        raise ValueError("existing_database_and_frontend_and_new_output_directory_required")
    destination.mkdir(parents=True)
    snapshot = destination / "snapshot.sqlite3"
    original = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    copied = sqlite3.connect(snapshot)
    try:
        original.backup(copied)
    finally:
        copied.close()
        original.close()
    os.environ.update(
        DECISION_HUB_DATA_DIR=str(destination / "artifacts"),
        DECISION_HUB_LLM_ENABLED="0",
        DECISION_HUB_SOURCES_ENABLED="0",
        DECISION_HUB_CALENDAR_DISCOVERY_ENABLED="0",
    )
    import uvicorn

    from apps.hub_api.main import create_app
    from packages.kernel.decision_hub_kernel.persistence.db import Database

    application = create_app(
        Database(f"sqlite+pysqlite:///{snapshot.as_posix()}"),
        source_connectors=[],
        sources_enabled=False,
        frontend_dir=frontend,
    )
    (destination / "preview.json").write_text(
        json.dumps(
            {
                "created_at": datetime.now(UTC).isoformat(),
                "pid": os.getpid(),
                "source_database": str(source),
                "snapshot": str(snapshot),
                "frontend": str(frontend),
                "port": args.port,
                "workers_started": False,
                "formal_database_mutated": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    uvicorn.run(application, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
