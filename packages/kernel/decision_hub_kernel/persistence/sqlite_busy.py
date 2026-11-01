"""Classify genuine SQLite contention without matching error-message text."""
from __future__ import annotations

import sqlite3

from sqlalchemy.exc import StatementError


def is_sqlite_storage_busy(error: BaseException) -> bool:
    original = error.orig if isinstance(error, StatementError) else error
    if not isinstance(original, sqlite3.OperationalError):
        return False
    code = getattr(original, "sqlite_errorcode", None)
    return type(code) is int and (code & 255) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}


def is_sqlite_initial_begin_busy(error: BaseException) -> bool:
    if not isinstance(error, StatementError) or not is_sqlite_storage_busy(error):
        return False
    original = error.orig
    if getattr(original, "sqlite_errorcode", None) == sqlite3.SQLITE_BUSY_SNAPSHOT:
        return False
    statement = error.statement
    return isinstance(statement, str) and statement.strip().upper() == "BEGIN IMMEDIATE"
