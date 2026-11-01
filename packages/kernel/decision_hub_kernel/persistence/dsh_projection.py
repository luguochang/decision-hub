"""Dedicated short-wait SQLite sessions for cancellation maintenance.

No schema creation, global pragma changes, shared QueuePool, or fallback to a
different database. A timeout cannot forcibly interrupt an OS/disk stall.
"""
from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from sqlite3 import connect
from typing import Any
from urllib.parse import quote

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from .db import Database

SQLITE_BUSY_WAIT_MS = 50


class DshProjectionSessions:
    def __init__(self, database: Database) -> None:
        url = database.engine.url
        if url.get_backend_name() != "sqlite" or url.database in {None, "", ":memory:"}:
            raise ValueError("cancellation_projection_requires_file_sqlite")
        if url.query:
            raise ValueError("cancellation_projection_url_unsupported")
        assert url.database is not None
        # rw refuses to create a replacement empty DB if the configured file
        # disappears. No permission/query flags from a different URL are lost.
        file_uri = "file:" + quote(Path(url.database).resolve().as_posix(), safe="/:") + "?mode=rw"
        self.engine = create_engine(
            url, poolclass=NullPool, creator=lambda: connect(
                file_uri, uri=True, check_same_thread=True, timeout=SQLITE_BUSY_WAIT_MS / 1000,
            ),
        )
        event.listen(self.engine, "connect", _configure_short_connection)
        self._sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        # Creation, transaction, rollback and closure are all in the calling
        # thread. This session must never be passed across an await boundary.
        with self._sessions() as session:
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    def close(self) -> None:
        self.engine.dispose()


def _configure_short_connection(connection: Any, _record: Any) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_WAIT_MS}")
    finally:
        cursor.close()
