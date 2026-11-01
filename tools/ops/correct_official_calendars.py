"""Preview or apply the audited v1 calendar repair using immutable official HTML.

Apply requires a new backup path. Rehearse on a database copy before the live DB.
No network request, scheduler execution, historical deletion, or Run creation occurs.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from packages.kernel.decision_hub_kernel.application.calendar_correction import (
    correct_legacy_calendar,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.source_adapters.official_feeds import (
    OfficialHtmlCalendarSource,
    official_source_presets,
    parse_official_schedule_html,
)
from tools.ops.database import backup, integrity


def counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        return {
            name: db.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
            for name in (
                "runs",
                "artifacts",
                "research_evidence",
                "research_facts",
                "macro_event_vintages",
                "macro_events",
                "event_watches",
                "event_window_samples",
            )
        }


async def reconcile(
    database: Database,
    snapshot_dir: Path,
    *,
    apply: bool,
    audited_invalid_ids: set[str] | None = None,
) -> list[dict]:
    manifest = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
    bodies = {}
    for source_id, entry in manifest.items():
        raw = (snapshot_dir / f"{source_id}.html").read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError(f"calendar_snapshot_hash_mismatch:{source_id}")
        bodies[entry["url"]] = raw.decode("utf-8")

    async def fetcher(url: str) -> tuple[int, str]:
        return 200, bodies[url]

    prepared = []
    # Validate every source before writing any correction.
    for source in official_source_presets(html_calendar_fetcher=fetcher):
        if not isinstance(source, OfficialHtmlCalendarSource):
            continue
        result = await source.poll()
        valid = parse_official_schedule_html(
            bodies[source.endpoint],
            source_id=source.manifest.source_id,
            family=source.family,
            event_title=source.event_title,
        )
        if not result.envelopes:
            raise ValueError(f"calendar_snapshot_no_future_coverage:{source.manifest.source_id}")
        prepared.append((source, result, {entry[0] for entry in valid}))
    records = []
    for source, result, valid in prepared:
        if apply:
            record = correct_legacy_calendar(
                database,
                source_id=source.manifest.source_id,
                parser_version=source.manifest.version,
                source_content_hash=manifest[source.manifest.source_id]["sha256"],
                source_content_ref=str(
                    (snapshot_dir / f"{source.manifest.source_id}.html").resolve()
                ),
                valid_external_ids=valid,
                envelopes=result.envelopes,
                now=datetime.now(UTC),
                audited_invalid_ids={
                    uid
                    for uid in (audited_invalid_ids or set())
                    if uid.startswith(source.manifest.source_id + ":")
                },
            )
        else:
            record = {"source_id": source.manifest.source_id, "validated_ids": sorted(valid)}
        record["coverage"] = (
            result.content_snapshot.model_dump() if result.content_snapshot else None
        )
        records.append(record)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument(
        "--snapshot-dir", type=Path, default=Path("fixtures/sources/official-calendar-2026-09-29")
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--invalidate-external-id", action="append", default=[])
    args = parser.parse_args()
    if not args.database.is_file() or args.database.stat().st_size == 0:
        raise ValueError("calendar_repair_requires_existing_nonempty_database")
    database = Database(f"sqlite+pysqlite:///{args.database.resolve()}")
    # Complete parser/hash validation before even migrating or backing up.
    asyncio.run(reconcile(database, args.snapshot_dir, apply=False))
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "database": str(args.database.resolve()),
        "applied": args.apply,
        "before": counts(args.database),
    }
    if args.apply:
        if (
            not args.backup
            or args.backup.exists()
            or args.backup.resolve() == args.database.resolve()
        ):
            raise ValueError("calendar_repair_requires_new_backup_path")
        backup(args.database, args.backup)
        if not integrity(args.backup):
            raise RuntimeError("calendar_repair_backup_invalid")
        report["backup"] = str(args.backup.resolve())
        database.initialize()
    report["corrections"] = asyncio.run(
        reconcile(
            database,
            args.snapshot_dir,
            apply=args.apply,
            audited_invalid_ids=set(args.invalidate_external_id),
        )
    )
    report["after"] = counts(args.database)
    report["integrity_ok"] = integrity(args.database)
    report["completed_at"] = datetime.now(UTC).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "report": str(args.output),
                "integrity_ok": report["integrity_ok"],
                "corrections": [
                    {
                        "source": row["source_id"],
                        "invalidated": len(row.get("invalidated", [])),
                        "verified": len(row.get("verified", [])),
                    }
                    for row in report["corrections"]
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
