"""Restore the R1 full native tables, not projected query answers.

The preparer authenticates DDL/rows against a fresh original read before this
file operation. This follows the native39 restoration protocol; no business
reader, guard or registered module is replaced.
"""

from __future__ import annotations

import hashlib
import os
import stat


def verify_material_file(path, *, expected_sha256: str, expected_bytes: int,
                         max_bytes: int, sink=None) -> None:
    """Every invocation streams the whole exact constituent; no mtime cache.

    A server selected file may be copied to a private sink while its bytes
    are verified. Ancestor links/reparse points, hardlinks, changed file
    identity, absence, growth and mismatched lengths/hash all reject.
    """
    from pathlib import Path
    if (type(expected_bytes) is not int or type(max_bytes) is not int
            or expected_bytes < 1 or expected_bytes > max_bytes):
        raise ValueError("publication_bundle_material_bound_exceeded")
    path = Path(path).absolute()
    if ".." in path.parts:
        raise ValueError("publication_bundle_material_path_invalid")

    def check_path():
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            observed = current.lstat()
            if current.is_symlink() or getattr(observed, "st_file_attributes", 0) & 0x400:
                raise ValueError("publication_bundle_material_link_forbidden")
        final = path.lstat()
        if not stat.S_ISREG(final.st_mode) or final.st_nlink != 1:
            raise ValueError("publication_bundle_material_link_forbidden")
        return final

    before = check_path()
    if before.st_size != expected_bytes:
        raise ValueError("publication_bundle_material_bytes_changed")
    count, hasher = 0, hashlib.sha256()
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_size) != (before.st_dev, before.st_ino, expected_bytes):
            raise ValueError("publication_bundle_material_file_changed")
        while chunk := stream.read(min(1024 * 1024, expected_bytes + 1 - count)):
            count += len(chunk)
            if count > expected_bytes or count > max_bytes:
                raise ValueError("publication_bundle_material_bound_exceeded")
            hasher.update(chunk)
            if sink is not None:
                sink.write(chunk)
        after_open = os.fstat(stream.fileno())
    after = check_path()
    if (count != expected_bytes or hasher.hexdigest() != expected_sha256
            or (after.st_dev, after.st_ino, after.st_size) != (before.st_dev, before.st_ino, expected_bytes)
            or (after_open.st_dev, after_open.st_ino, after_open.st_size) != (before.st_dev, before.st_ino, expected_bytes)):
        raise ValueError("publication_bundle_material_bytes_changed")

import base64
import math
import sqlite3
from collections.abc import Generator
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from .publication_dependency_native import canonical, cell, sha


def decode(value: dict):
    if set(value) != {"kind", "value"}:
        raise ValueError("publication_bundle_native_cell_invalid")
    kind, raw = value["kind"], value["value"]
    if kind == "null" and raw is None:
        return None
    if type(raw) is not str:
        raise ValueError("publication_bundle_native_cell_invalid")
    if kind == "integer":
        native = int(raw)
    elif kind == "real":
        native = float.fromhex(raw)
        if not math.isfinite(native):
            raise ValueError("publication_bundle_native_cell_invalid")
    elif kind == "text":
        native = raw
    elif kind == "blob":
        native = base64.b64decode(raw, validate=True)
    else:
        raise ValueError("publication_bundle_native_cell_invalid")
    if cell(native).json() != value:
        raise ValueError("publication_bundle_native_cell_noncanonical")
    return native


def quoted(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def restore_authenticated_native(path: Path, capture: dict) -> None:
    schemas = {item["table"]: item for item in capture["schemas"]}
    if len(schemas) != len(capture["schemas"]) or path.exists():
        raise ValueError("publication_bundle_material_schema_ambiguous")
    with closing(sqlite3.connect(path)) as native, native:
        for table, schema in schemas.items():
            objects = [tuple(map(decode, item)) for item in schema["schema_objects"]]
            ddl = [item[3] for item in objects if item[0] == "table" and item[1] == table]
            if len(ddl) != 1 or type(ddl[0]) is not str:
                raise ValueError("publication_bundle_material_schema_invalid")
            native.execute(ddl[0])
        identities = set()
        for row in capture["rows"]:
            table, columns = row["table"], row["columns"]
            schema = schemas[table]
            if columns != schema["columns"] or len(columns) != len(row["cells"]):
                raise ValueError("publication_bundle_material_columns_changed")
            primary = [row["cells"][columns.index(key)] for key in schema["primary_key"]]
            identity = sha(canonical(primary))
            if identity != row["primary_identity"] or (table, identity) in identities:
                raise ValueError("publication_bundle_material_primary_changed")
            identities.add((table, identity))
            sql = "INSERT INTO " + quoted(table) + " (" + ",".join(map(quoted, columns))
            sql += ") VALUES (" + ",".join("?" for _ in columns) + ")"
            native.execute(sql, tuple(map(decode, row["cells"])))
        for schema in schemas.values():
            for item in schema["schema_objects"]:
                kind, _name, _table, sql = map(decode, item)
                if kind in {"index", "trigger"} and sql is not None:
                    native.execute(sql)
                elif kind not in {"table", "index", "trigger"}:
                    raise ValueError("publication_bundle_material_schema_kind_unsupported")
        for table, schema in schemas.items():
            actual = native.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema "
                                    "WHERE tbl_name=? ORDER BY type,name", (table,)).fetchall()
            if [[cell(v).json() for v in row] for row in actual] != schema["schema_objects"]:
                raise ValueError("publication_bundle_material_DDL_changed")
            metadata = native.execute("PRAGMA table_xinfo(" + quoted(table) + ")").fetchall()
            if [[cell(v).json() for v in row] for row in metadata] != schema["table_xinfo"]:
                raise ValueError("publication_bundle_material_affinity_changed")
        for row in capture["rows"]:
            schema = schemas[row["table"]]
            keys = schema["primary_key"]
            sql = "SELECT * FROM " + quoted(row["table"]) + " WHERE "
            sql += " AND ".join(quoted(key) + "=?" for key in keys)
            parameters = tuple(decode(row["cells"][row["columns"].index(key)]) for key in keys)
            actual = native.execute(sql, parameters).fetchall()
            if len(actual) != 1 or [cell(v).json() for v in actual[0]] != row["cells"]:
                raise ValueError("publication_bundle_material_native_type_changed")


@contextmanager
def authenticated_material_session(stage: Path, capture: dict) -> Generator[Session, None, None]:
    stage.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="frozen-publication-", dir=stage) as directory:
        path = Path(directory) / "native.sqlite3"
        restore_authenticated_native(path, capture)
        engine = create_engine("sqlite+pysqlite://", poolclass=NullPool,
            creator=lambda: sqlite3.connect(path.as_uri() + "?mode=ro", uri=True))
        try:
            with Session(engine) as session:
                session.connection().exec_driver_sql("PRAGMA query_only=ON")
                session.connection().exec_driver_sql("BEGIN")
                yield session
                session.rollback()
        finally:
            engine.dispose()
