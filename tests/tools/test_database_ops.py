from __future__ import annotations

import sqlite3
from pathlib import Path

from tools.ops.database import backup, integrity, restore, retain_backups


def test_sqlite_backup_and_integrity(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    destination = tmp_path / "backup" / "copy.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE fixture (value TEXT)")
        connection.execute("INSERT INTO fixture VALUES ('ok')")
        connection.commit()

    backup(source, destination)

    assert destination.exists()
    assert integrity(destination)


def test_sqlite_restore_and_retention(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    backup_path = tmp_path / "backup" / "copy.sqlite3"
    restored = tmp_path / "restore" / "restored.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE fixture (value TEXT)")
        connection.execute("INSERT INTO fixture VALUES ('restore-ok')")
        connection.commit()
    backup(source, backup_path)
    restore(backup_path, restored)
    with sqlite3.connect(restored) as connection:
        assert connection.execute("SELECT value FROM fixture").fetchone() == ("restore-ok",)
    for index in range(3):
        path = tmp_path / "retention" / f"{index}.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    removed = retain_backups(tmp_path / "retention", keep=2)
    assert len(removed) == 1
