from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as target_db:
        source_db.backup(target_db)


def restore(source: Path, destination: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as target_db:
        source_db.backup(target_db)


def retain_backups(directory: Path, *, keep: int = 7) -> list[Path]:
    if keep < 1:
        raise ValueError("keep must be positive")
    backups = sorted(
        (path for path in directory.glob("*.sqlite3") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    removed: list[Path] = []
    for path in backups[keep:]:
        path.unlink()
        removed.append(path)
    return removed


def integrity(path: Path) -> bool:
    with sqlite3.connect(path) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    return result == ("ok",)


def main() -> None:
    parser = argparse.ArgumentParser(description="Decision Hub SQLite durability operations")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("source", type=Path)
    backup_parser.add_argument("destination", type=Path)

    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("source", type=Path)
    restore_parser.add_argument("destination", type=Path)

    integrity_parser = subparsers.add_parser("integrity")
    integrity_parser.add_argument("database", type=Path)

    retention_parser = subparsers.add_parser("retention")
    retention_parser.add_argument("directory", type=Path)
    retention_parser.add_argument("--keep", type=int, default=7)

    args = parser.parse_args()
    if args.command == "backup":
        backup(args.source, args.destination)
        return
    if args.command == "restore":
        restore(args.source, args.destination)
        if not integrity(args.destination):
            raise SystemExit("restored SQLite integrity check failed")
        return
    if args.command == "retention":
        for path in retain_backups(args.directory, keep=args.keep):
            print(path)
        return
    if not integrity(args.database):
        raise SystemExit("SQLite integrity check failed")


if __name__ == "__main__":
    main()
