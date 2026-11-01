"""Bounded typed native material from a borrowed SQLite read snapshot.

This is an uninstalled R1 diagnostic, not a reusable permission/proof. Supported
single-table SELECTs retain the complete matching range, including zero rows,
and every physical column. Unsupported shapes remain explicit and prevent an
observed-complete candidate. No live DB, HTTP or model entry point is provided.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import sqlite3
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


class CaptureRejected(ValueError):
    """Stable diagnostic reason, never query bindings or original contents."""


@dataclass(frozen=True)
class Limits:
    """Caller-specified diagnostic bounds; no production qualification gates."""

    max_queries: int
    max_rows_per_query: int
    max_native_bytes: int
    max_source_file_bytes: int
    max_source_bytes: int

    def __post_init__(self) -> None:
        if any(type(value) is not int or value <= 0 for value in vars(self).values()):
            raise CaptureRejected("r1_positive_explicit_bounds_required")


@dataclass(frozen=True)
class Cell:
    kind: Literal["null", "integer", "real", "text", "blob"]
    value: str | None = None

    def json(self) -> dict[str, str | None]:
        return {"kind": self.kind, "value": self.value}


def cell(value: Any, *, binding: bool = False) -> Cell:
    if value is None:
        return Cell("null")
    if type(value) is bool and binding:
        return Cell("integer", "1" if value else "0")
    if type(value) is int:
        return Cell("integer", str(value))
    if type(value) is float:
        if not math.isfinite(value):
            raise CaptureRejected("r1_nonfinite_native_value")
        return Cell("real", value.hex())
    if type(value) is str:
        value.encode("utf-8", errors="strict")
        return Cell("text", value)
    if type(value) is bytes:
        return Cell("blob", base64.b64encode(value).decode("ascii"))
    raise CaptureRejected("r1_unsupported_native_value")


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@dataclass(frozen=True)
class Token:
    raw: str
    word: str
    start: int
    depth: int


def tokens(sql: str) -> tuple[Token, ...]:
    """Finite SQLite lexical subset, no regex rewriting of quoted literals."""
    result: list[Token] = []
    index = depth = 0
    while index < len(sql):
        char = sql[index]
        if char.isspace():
            index += 1
            continue
        start = index
        if sql.startswith(("--", "/*"), index) or char == ";":
            raise CaptureRejected("r1_comments_or_multistatement_unsupported")
        if char in "'\"`[":
            closing = "]" if char == "[" else char
            index += 1
            while index < len(sql):
                if sql[index] == closing:
                    if closing != "]" and index + 1 < len(sql) and sql[index + 1] == closing:
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            else:
                raise CaptureRejected("r1_sql_quote_unclosed")
            raw = sql[start:index]
            result.append(Token(raw, "LITERAL" if char == "'" else "IDENT", start, depth))
            continue
        if char == "(":
            result.append(Token(char, char, start, depth))
            depth += 1
            index += 1
            continue
        if char == ")":
            depth -= 1
            if depth < 0:
                raise CaptureRejected("r1_sql_parentheses_invalid")
            result.append(Token(char, char, start, depth))
            index += 1
            continue
        if char.isalpha() or char == "_":
            index += 1
            while index < len(sql) and (sql[index].isalnum() or sql[index] in "_$"):
                index += 1
            raw = sql[start:index]
            result.append(Token(raw, raw.upper(), start, depth))
            continue
        if char.isdigit():
            index += 1
            while index < len(sql) and (sql[index].isdigit() or sql[index] in ".eE+-"):
                index += 1
            result.append(Token(sql[start:index], "NUMBER", start, depth))
            continue
        if char not in "?,.=<>!|+-*/%~&":
            raise CaptureRejected("r1_sql_token_unsupported")
        result.append(Token(char, char, start, depth))
        index += 1
    if depth:
        raise CaptureRejected("r1_sql_parentheses_invalid")
    return tuple(result)


def identifier(token: Token) -> str:
    raw = token.raw
    if token.word == "IDENT":
        if raw.startswith("["):
            return raw[1:-1]
        return raw[1:-1].replace(raw[0] * 2, raw[0])
    if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", raw):
        return raw
    raise CaptureRejected("r1_table_identifier_unsupported")


@dataclass(frozen=True)
class SelectShape:
    table: str
    table_sql: str
    range_sql: str
    range_binding_count: int
    ordered: bool
    limited: bool


def parse_select(sql: str) -> SelectShape:
    values = tokens(sql)
    if not values or values[0].word != "SELECT":
        raise CaptureRejected("r1_select_required")
    # SQLAlchemy Query.count()'s one specific bounded base-table wrapper.
    # The inner SELECT is still parsed by the same finite predicate resolver;
    # no general subquery/JOIN or aggregate query engine is admitted.
    top_values = [value for value in values if value.depth == 0]
    top_from = [value for value in top_values if value.word == "FROM"]
    if len(top_from) == 1:
        outer_from = top_from[0]
        after = [value for value in top_values if value.start > outer_from.start]
        if after and after[0].word == "(":
            prefix = sql[:outer_from.start].strip()
            prefix_values = tokens(prefix)
            pattern = [value.word for value in prefix_values]
            if pattern[:5] != ["SELECT", "COUNT", "(", "*", ")"]:
                raise CaptureRejected("r1_subquery_unsupported")
            if len(pattern) not in {5, 7} or (len(pattern) == 7 and pattern[5] != "AS"):
                raise CaptureRejected("r1_count_wrapper_unsupported")
            if len(pattern) == 7:
                identifier(prefix_values[-1])
            if len(after) != 4 or [value.word for value in after[:3]] != ["(", ")", "AS"]:
                raise CaptureRejected("r1_count_wrapper_unsupported")
            identifier(after[-1])
            inner = sql[after[0].start + 1:after[1].start]
            inner_shape = parse_select(inner)
            if inner_shape.limited or inner_shape.ordered:
                raise CaptureRejected("r1_count_paged_wrapper_unsupported")
            return inner_shape
    if sum(value.word == "SELECT" for value in values) != 1:
        raise CaptureRejected("r1_subquery_unsupported")
    forbidden = {"JOIN", "UNION", "INTERSECT", "EXCEPT", "GROUP", "HAVING",
                 "DISTINCT", "WINDOW", "OVER", "RETURNING", "WITH", "FILTER"}
    if any(value.word in forbidden for value in values):
        raise CaptureRejected("r1_relational_shape_unsupported")
    unsafe = {"CURRENT_TIME", "CURRENT_DATE", "CURRENT_TIMESTAMP", "COLLATE"}
    if any(value.word in unsafe for value in values):
        raise CaptureRejected("r1_clock_or_collation_unsupported")
    from_start = top_from[0].start if len(top_from) == 1 else -1
    for index, value in enumerate(values[:-1]):
        allowed_count = value.word == "COUNT" and value.start < from_start
        if values[index + 1].word == "(" and not allowed_count and value.word not in {"CAST", "IN", "NOT", "AND", "OR", "WHERE"}:
            if value.word not in {"(", ",", "=", ">", "<", "!", "|", "+", "-", "*", "/", "%", "~", "&"}:
                raise CaptureRejected("r1_function_or_expression_unsupported")
    top = [(index, value) for index, value in enumerate(values) if value.depth == 0]
    froms = [(index, value) for index, value in top if value.word == "FROM"]
    wheres = [(index, value) for index, value in top if value.word == "WHERE"]
    if len(froms) != 1 or len(wheres) != 1:
        raise CaptureRejected("r1_single_table_bounded_where_required")
    from_index, from_token = froms[0]
    where_index, where_token = wheres[0]
    if any(value.word == "COUNT" for value in values[:from_index]):
        projection = values[1:from_index]
        pattern = [value.word for value in projection]
        # COUNT(*) or COUNT(one native identifier), optionally AS alias.
        closing = next((i for i, value in enumerate(projection)
                        if value.word == ")" and value.depth == 0), -1)
        if closing < 0 or pattern[:2] != ["COUNT", "("]:
            raise CaptureRejected("r1_count_projection_unsupported")
        argument = projection[2:closing]
        if not (len(argument) == 1 and argument[0].word == "*"):
            if len(argument) not in {1, 3} or (len(argument) == 3 and argument[1].word != "."):
                raise CaptureRejected("r1_count_projection_unsupported")
            identifier(argument[0])
            if len(argument) == 3:
                identifier(argument[2])
        trailing = projection[closing + 1:]
        if trailing and (len(trailing) != 2 or trailing[0].word != "AS"):
            raise CaptureRejected("r1_count_projection_unsupported")
        if trailing:
            identifier(trailing[1])
        if any(value.word in {"ORDER", "LIMIT", "OFFSET"} for _, value in top):
            raise CaptureRejected("r1_count_paged_wrapper_unsupported")
    if any(value.word == "?" for value in values[:where_index]):
        raise CaptureRejected("r1_projection_bindings_unsupported")
    table_tokens = values[from_index + 1:where_index]
    # Bare table or one alias, including SQLite/SQLAlchemy's AS spelling.
    if len(table_tokens) not in {1, 2, 3}:
        raise CaptureRejected("r1_table_alias_unsupported")
    table = identifier(table_tokens[0])
    if len(table_tokens) == 3 and table_tokens[1].word != "AS":
        raise CaptureRejected("r1_table_alias_unsupported")
    if len(table_tokens) > 1:
        identifier(table_tokens[-1])
    ends = [value.start for _, value in top if value.word in {"LIMIT", "OFFSET"}]
    end = min(ends, default=len(sql))
    range_tail = sql[where_token.start:end].rstrip()
    table_sql = sql[from_token.start + len(from_token.raw):where_token.start].strip()
    # Remove LIMIT/OFFSET for complete predicate membership; keep original
    # SQL and full returned rows separately. A large range is rejected by bounds.
    prefix = sql[:end]
    return SelectShape(table, table_sql, "SELECT * FROM " + table_sql + " " + range_tail,
                       sum(value.word == "?" for value in tokens(prefix)),
                       any(value.word == "ORDER" for _, value in top), bool(ends))


@dataclass(frozen=True)
class SourceFile:
    relative_ref: str
    expected_sha256: str


class NativeCollector:
    """One borrowed read boundary; it owns neither BEGIN nor COMMIT.

    Caller must explicitly own the trace callback slot. sqlite3 has no getter,
    so a hidden prior callback cannot safely be discovered/restored. A supplied
    previous callback is restored; adoption into product code is still UNKNOWN.
    """

    def __init__(self, session: Session, *, limits: Limits,
                 trace_slot_owned: bool,
                 previous_trace: Callable[[str], None] | None = None):
        if trace_slot_owned is not True:
            raise CaptureRejected("r1_explicit_trace_slot_ownership_required")
        self.session, self.limits = session, limits
        self.previous_trace = previous_trace
        self.owner_thread = threading.get_ident()
        self.connection = session.connection()
        self.native = self.connection.connection.driver_connection
        self.transaction = session.get_transaction()
        self.nested = session.get_nested_transaction()
        self.queries: dict[str, dict[str, Any]] = {}
        self.schemas: dict[str, dict[str, Any]] = {}
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.sources: dict[str, dict[str, Any]] = {}
        self._source_paths: dict[str, Path] = {}
        self._source_roots: dict[str, Path] = {}
        self.unresolved: list[dict[str, str]] = []
        self.observed_statement_count = self.native_select_count = 0
        self.byte_count = self.source_bytes = 0
        self._in_material = False
        self._active_statement: str | None = None
        self._entered = self._finished = False
        self.marker = "report_r1_" + uuid4().hex

    def _require_boundary(self) -> None:
        try:
            valid = (
                threading.get_ident() == self.owner_thread
                and isinstance(self.native, sqlite3.Connection)
                and self.native.in_transaction
                and not self.connection.closed
                and self.session.connection() is self.connection
                and self.connection.connection.driver_connection is self.native
                and self.session.get_transaction() is self.transaction
                and self.session.get_nested_transaction() is self.nested
                and self.native.total_changes == self.changes
                and self.native.text_factory is str
                and not (self.session.new or self.session.dirty or self.session.deleted)
            )
        except (sqlite3.Error, AttributeError):
            valid = False
        if not valid:
            raise CaptureRejected("r1_physical_read_boundary_changed")

    def __enter__(self) -> NativeCollector:
        if self._entered or not isinstance(self.native, sqlite3.Connection):
            raise CaptureRejected("r1_single_sqlite_scope_required")
        self.changes = self.native.total_changes
        self._require_boundary()
        if self.native.execute("PRAGMA query_only").fetchone() != (1,):
            raise CaptureRejected("r1_query_only_required")
        self.native.execute("SAVEPOINT " + self.marker)
        self.native.set_trace_callback(self._trace)
        event.listen(Engine, "before_cursor_execute", self._before)
        event.listen(Engine, "after_cursor_execute", self._after)
        event.listen(Engine, "handle_error", self._error)
        self._entered = True
        return self

    def _reject(self, sql: str, code: str) -> None:
        value = {"query_sha256": sha(sql.encode()), "reason_code": code}
        if value not in self.unresolved:
            if len(self.unresolved) >= self.limits.max_queries:
                raise CaptureRejected("r1_unresolved_limit")
            self.unresolved.append(value)

    def _trace(self, sql: str) -> None:
        if self._in_material:
            return
        head = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
        if head == "SELECT":
            self.native_select_count += 1
            if self._active_statement is None:
                self._reject(sql, "r1_direct_native_select_unresolved")
        elif head not in {"SAVEPOINT", "RELEASE"}:
            if re.fullmatch(r"\s*PRAGMA\s+query_only\s*;?\s*", sql, flags=re.IGNORECASE):
                # The resource scope inspects (never assigns) this physical
                # readonly state. Mutating or other PRAGMAs remain rejected.
                return
            self._reject(sql, "r1_native_nonread_statement")

    def _before(self, connection: Any, _cursor: Any, statement: str,
                _parameters: Any, _context: Any, many: bool) -> None:
        if threading.get_ident() != self.owner_thread:
            return
        self._require_boundary()
        if connection is not self.connection:
            self._reject(statement, "r1_cross_connection_query")
            raise CaptureRejected("r1_cross_connection_query")
        self.observed_statement_count += 1
        if many or not statement.lstrip().upper().startswith("SELECT "):
            self._reject(statement, "r1_nonselect_or_executemany")
            raise CaptureRejected("r1_nonselect_or_executemany")
        self._active_statement = statement

    def _after(self, connection: Any, _cursor: Any, statement: str,
               parameters: Any, _context: Any, _many: bool) -> None:
        if threading.get_ident() != self.owner_thread or connection is not self.connection:
            return
        self._active_statement = None
        self._require_boundary()
        self._in_material = True
        try:
            self._capture(statement, parameters)
        except (CaptureRejected, sqlite3.Error, UnicodeError) as error:
            code = str(error) if isinstance(error, CaptureRejected) else "r1_native_result_unreadable"
            self._reject(statement, code)
        finally:
            self._in_material = False

    def _error(self, context: Any) -> None:
        if threading.get_ident() != self.owner_thread or context.connection is not self.connection:
            return
        self._active_statement = None
        self._reject(context.statement or "failed native statement", "r1_native_execution_failed")

    def _charge(self, value: Any) -> None:
        self.byte_count += len(canonical(value))
        if self.byte_count > self.limits.max_native_bytes:
            raise CaptureRejected("r1_native_material_limit")

    def _result(self, sql: str, parameters: Sequence[Any]) -> dict[str, Any]:
        cursor = self.native.execute(sql, tuple(parameters))
        try:
            columns = tuple(item[0] for item in cursor.description or ())
            rows: list[list[dict[str, str | None]]] = []
            for row in cursor:
                if len(rows) >= self.limits.max_rows_per_query:
                    raise CaptureRejected("r1_query_row_limit")
                encoded = [cell(value).json() for value in row]
                self._charge(encoded)
                rows.append(encoded)
            return {"columns": list(columns), "rows": rows, "row_count": len(rows),
                    "sha256": sha(canonical([columns, rows]))}
        finally:
            cursor.close()

    def _schema(self, table: str) -> dict[str, Any]:
        if table in self.schemas:
            return self.schemas[table]
        record = self.native.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema "
            "WHERE tbl_name = ? ORDER BY type, name", (table,)
        ).fetchall()
        tables = [row for row in record if row[0] == "table" and row[1] == table]
        if len(tables) != 1 or tables[0][3] is None or "VIRTUAL TABLE" in tables[0][3].upper():
            raise CaptureRejected("r1_physical_base_table_required")
        metadata = self.native.execute("PRAGMA table_xinfo(" + quote(table) + ")").fetchall()
        if not metadata or any(row[6] != 0 for row in metadata):
            raise CaptureRejected("r1_generated_or_hidden_column_unsupported")
        primary = [row[1] for row in sorted(metadata, key=lambda row: row[5]) if row[5]]
        if not primary:
            raise CaptureRejected("r1_explicit_primary_key_required")
        value = {"table": table, "columns": [row[1] for row in metadata],
                 "primary_key": primary,
                 "schema_objects": [[cell(item).json() for item in row] for row in record],
                 "table_xinfo": [[cell(item).json() for item in row] for row in metadata]}
        self._charge(value)
        self.schemas[table] = value
        return value

    def _capture(self, statement: str, parameters: Any) -> None:
        if not isinstance(parameters, tuple | list):
            # Preserve the original shape failure before a binding failure.
            parse_select(statement)
            raise CaptureRejected("r1_positional_bindings_required")
        try:
            typed = [{"python_type": type(value).__name__, "cell": cell(value, binding=True).json()}
                     for value in parameters]
        except (CaptureRejected, UnicodeError):
            parse_select(statement)
            raise
        query_id = sha(canonical([statement, typed]))
        if query_id in self.queries:
            self.queries[query_id]["read_count"] += 1
            return
        shape = parse_select(statement)
        if len(self.queries) >= self.limits.max_queries:
            raise CaptureRejected("r1_distinct_query_limit")
        schema = self._schema(shape.table)
        result = self._result(statement, parameters)
        members = self._result(shape.range_sql, parameters[:shape.range_binding_count])
        if members["columns"] != schema["columns"]:
            raise CaptureRejected("r1_full_native_columns_mismatch")
        primary_indices = [members["columns"].index(name) for name in schema["primary_key"]]
        identities: list[str] = []
        for values in members["rows"]:
            if any(values[index]["kind"] == "null" for index in primary_indices):
                raise CaptureRejected("r1_null_primary_identity_unsupported")
            key = sha(canonical([values[index] for index in primary_indices]))
            native_row = {"table": shape.table, "primary_identity": key,
                          "columns": members["columns"], "cells": values}
            if (shape.table, key) in self.rows and self.rows[shape.table, key] != native_row:
                raise CaptureRejected("r1_primary_identity_conflict")
            self.rows[shape.table, key] = native_row
            identities.append(key)
        value = {"query_id": query_id, "statement": statement, "typed_parameters": typed,
                 "result": result, "table": shape.table, "range_statement": shape.range_sql,
                 "range_typed_parameters": typed[:shape.range_binding_count],
                 "range_result": members, "member_identities": identities,
                 "ordered": shape.ordered, "limited": shape.limited,
                 "empty_range": members["row_count"] == 0, "read_count": 1}
        self._charge([statement, typed, identities])
        self.queries[query_id] = value

    def collect_sources(self, root: Path, files: Sequence[SourceFile]) -> None:
        """Explicit selected closure only; not inferred source/read authority."""
        self._require_boundary()
        if not self._entered or self._finished:
            raise CaptureRejected("r1_open_scope_required")
        resolved_root = root.resolve(strict=True)
        for spec in files:
            parts = spec.relative_ref.split("/")
            if (not parts or any(part in {"", ".", ".."} for part in parts)
                    or "\\" in spec.relative_ref or ":" in spec.relative_ref
                    or not re.fullmatch(r"[a-f0-9]{64}", spec.expected_sha256)):
                raise CaptureRejected("r1_source_reference_invalid")
            path = resolved_root
            for part in parts:
                path = path / part
                status = path.lstat()
                if path.is_symlink() or getattr(status, "st_file_attributes", 0) & 0x400:
                    raise CaptureRejected("r1_source_link_forbidden")
            if not path.is_file() or path.stat().st_size > self.limits.max_source_file_bytes:
                raise CaptureRejected("r1_source_file_limit")
            digest, data = hashlib.sha256(), bytearray()
            with path.open("rb") as stream:
                while chunk := stream.read(min(65536, self.limits.max_source_file_bytes + 1 - len(data))):
                    data.extend(chunk)
                    digest.update(chunk)
                    if len(data) > self.limits.max_source_file_bytes:
                        raise CaptureRejected("r1_source_file_limit")
            if digest.hexdigest() != spec.expected_sha256:
                raise CaptureRejected("r1_source_hash_changed")
            value = {"relative_ref": spec.relative_ref, "sha256": digest.hexdigest(),
                     "bytes": len(data), "base64": base64.b64encode(data).decode("ascii")}
            if spec.relative_ref in self.sources and self.sources[spec.relative_ref] != value:
                raise CaptureRejected("r1_source_identity_conflict")
            if spec.relative_ref not in self.sources:
                self.source_bytes += len(data)
                if self.source_bytes > self.limits.max_source_bytes:
                    raise CaptureRejected("r1_source_closure_limit")
                self.sources[spec.relative_ref] = value
                self._source_paths[spec.relative_ref] = path
                self._source_roots[spec.relative_ref] = resolved_root

    def snapshot(self) -> dict[str, Any]:
        if not self._finished:
            raise CaptureRejected("r1_finished_scope_required")
        return json.loads(canonical({
            "schema_version": "research-publication-dependency-candidate.r1",
            "observed_queries_status": "complete" if not self.unresolved else "incomplete",
            "trusted_original_replay_status": "UNKNOWN",
            "real_publication_checked": False,
            "serving_authority": False, "financial_qualification": False,
            "scope": "caller-observed-primary-SQLite-read-not-global-I/O-proof",
            "observed_statement_count": self.observed_statement_count,
            "native_select_count": self.native_select_count,
            "native_changes": self.native.total_changes - self.changes,
            "native_material_bytes": self.byte_count, "source_bytes": self.source_bytes,
            "queries": list(self.queries.values()), "schemas": list(self.schemas.values()),
            "rows": list(self.rows.values()), "sources": list(self.sources.values()),
            "unresolved": self.unresolved,
        }))

    def __exit__(self, kind: Any, value: Any, traceback: Any) -> None:
        try:
            self._require_boundary()
            for relative, path in self._source_paths.items():
                current = self._source_roots[relative]
                for part in relative.split("/"):
                    current = current / part
                    status = current.lstat()
                    if current.is_symlink() or getattr(status, "st_file_attributes", 0) & 0x400:
                        raise CaptureRejected("r1_source_link_forbidden")
                digest, count = hashlib.sha256(), 0
                with path.open("rb") as stream:
                    while chunk := stream.read(65536):
                        count += len(chunk)
                        if count > self.limits.max_source_file_bytes:
                            raise CaptureRejected("r1_source_file_limit")
                        digest.update(chunk)
                if (digest.hexdigest() != self.sources[relative]["sha256"]
                        or count != self.sources[relative]["bytes"]):
                    raise CaptureRejected("r1_source_hash_changed")
            self.native.execute("RELEASE SAVEPOINT " + self.marker)
        except (CaptureRejected, sqlite3.Error, OSError):
            self._reject("physical snapshot", "r1_snapshot_marker_or_boundary_changed")
            if kind is None:
                raise CaptureRejected("r1_snapshot_marker_or_boundary_changed") from None
        finally:
            event.remove(Engine, "before_cursor_execute", self._before)
            event.remove(Engine, "after_cursor_execute", self._after)
            event.remove(Engine, "handle_error", self._error)
            self.native.set_trace_callback(self.previous_trace)
            self._finished = True
