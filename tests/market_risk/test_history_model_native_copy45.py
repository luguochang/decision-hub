"""Private native-copy controls; no real corpus, model, qualification or finance P."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application import history_model_freeze_v3 as freeze
from packages.kernel.decision_hub_kernel.application import history_forecast_transfer39 as transfer


TABLE = "market_history_v2_originals"
COLUMNS = tuple(transfer.PROJECTIONS[TABLE].split(","))


@contextmanager
def source(tmp_path, rows):
    path = tmp_path / "private-source.sqlite3"
    with sqlite3.connect(path) as writer:
        writer.execute(
            f'CREATE TABLE "{TABLE}" ('
            + ",".join(f'"{name}"' + (" PRIMARY KEY" if index == 0 else "")
                       for index, name in enumerate(COLUMNS)) + ")"
        )
        writer.executemany(
            f'INSERT INTO "{TABLE}" VALUES ({",".join("?" for _ in COLUMNS)})', rows
        )
    engine = create_engine("sqlite+pysqlite:///file:" + path.as_posix() + "?mode=ro&uri=true")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA query_only=ON")
            connection.exec_driver_sql("BEGIN")
            with Session(bind=connection) as session:
                native = connection.connection.driver_connection
                changes = native.total_changes
                schemas = ((TABLE, tuple(tuple(row) for row in connection.exec_driver_sql(
                    f'PRAGMA table_xinfo("{TABLE}")').all())),)
                keys = {TABLE: {row[0]: row for row in rows}}
                yield session, schemas, keys, native
                assert native.total_changes == changes
                assert connection.exec_driver_sql("PRAGMA query_only").scalar_one() == 1
    finally:
        engine.dispose()


def row(key, text, blob=b"native\0bytes"):
    # Unaffinitized source deliberately includes every SQLite scalar class.
    return (key, None, 1, 1.0, blob, text, "hash", "reference", "original", "clock")


def stored(path):
    with sqlite3.connect(path) as reader:
        return tuple(tuple(value) for value in reader.execute(f'SELECT * FROM "{TABLE}" ORDER BY original_id'))


def test_native45_large_unicode_nul_text_blob_and_all_scalar_types(tmp_path):
    text = "开\0é🙂" * 1800000  # 18,000,000 UTF8 bytes; cross-chunk code points.
    blob = b"\xff\0native" * 2400000  # 19,200,000 exact BLOB bytes.
    expected = row("large", text, blob)
    target = tmp_path / "private-native.sqlite3"
    with source(tmp_path, (expected,)) as (session, schemas, keys, native):
        calls = []
        native.set_trace_callback(calls.append)
        freeze._copy_native_snapshot45(session, schemas, keys, target, lambda: None)
        native.set_trace_callback(None)
        assert not any('SELECT * FROM "market_history_v2_originals"' in value for value in calls)
        assert transfer.native_equal39(stored(target), (expected,))
        with sqlite3.connect(target) as reader:
            assert reader.execute(f'SELECT typeof(owner_claim),typeof(request_id),typeof(plan_id),'
                                  f'typeof(payload_json),length(CAST(payload_json AS BLOB)) FROM "{TABLE}"').fetchone() == (
                "integer", "real", "blob", "text", 18000000
            )


def test_native45_over_16mib_batch_splits_without_whole_batch_fetch(tmp_path):
    rows = tuple(row(str(index), "x" * (4 * 1024 * 1024)) for index in range(5))
    target = tmp_path / "private-native.sqlite3"
    with source(tmp_path, rows) as (session, schemas, keys, native):
        reads = []
        native.set_trace_callback(reads.append)
        freeze._copy_native_snapshot45(session, schemas, keys, target, lambda: None)
        native.set_trace_callback(None)
        selected = [value for value in reads if value.startswith(f'SELECT * FROM "{TABLE}"')]
        assert len(selected) == 2
        assert all(value.count(",") <= 2 for value in selected)
        assert transfer.native_equal39(stored(target), rows)


def test_native45_changed_large_proof_rejected_without_source_write(tmp_path):
    text = "x" * (17 * 1024 * 1024)
    expected = row("large", text)
    with source(tmp_path, (expected,)) as (session, schemas, keys, _native):
        altered = list(expected)
        altered[5] = text[:-1] + "y"
        keys[TABLE]["large"] = tuple(altered)
        with pytest.raises(ValueError, match="history_qualification_selected_native_row_changed"):
            freeze._copy_native_snapshot45(session, schemas, keys, tmp_path / "private-native.sqlite3", lambda: None)


def test_native45_full_native_extra_job_fields_retained(tmp_path):
    table = "market_history_v2_jobs"
    columns = transfer.FULL_SCHEMA_COLUMNS[table]
    path, target = tmp_path / "source-jobs.sqlite3", tmp_path / "native-jobs.sqlite3"
    full = ("job", "epoch", "plan", "scope", "start", "end", b"\0cursor", 700,
            3.0, None, 0, 2, "state", b"lease", 1.0, None, "尾\0🙂" * 2200000)
    with sqlite3.connect(path) as writer:
        writer.execute(f'CREATE TABLE "{table}" (' + ",".join(f'"{name}"' for name in columns) + ")")
        writer.execute(f'INSERT INTO "{table}" VALUES ({",".join("?" for _ in full)})', full)
    engine = create_engine("sqlite+pysqlite:///file:" + path.as_posix() + "?mode=ro&uri=true")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA query_only=ON")
            connection.exec_driver_sql("BEGIN")
            with Session(bind=connection) as session:
                schemas = ((table, tuple(tuple(value) for value in connection.exec_driver_sql(f'PRAGMA table_xinfo("{table}")'))),)
                expected = tuple(full[columns.index(name)] for name in transfer.PROJECTIONS[table].split(","))
                freeze._copy_native_snapshot45(session, schemas, {table: {"job": expected}}, target, lambda: None)
                assert connection.connection.driver_connection.total_changes == 0
        with sqlite3.connect(target) as reader:
            assert transfer.native_equal39(tuple(reader.execute(f'SELECT * FROM "{table}"').fetchone()), full)
    finally:
        engine.dispose()


def test_native45_unchanged_retention_bound_rejects_private_oversize(tmp_path, monkeypatch):
    assert freeze.MAX_BYTES == 512 * 1024 * 1024
    assert freeze.MAX_ROWS == 300000
    assert freeze.NATIVE_BATCH_BYTES == 16 * 1024 * 1024
    monkeypatch.setattr(freeze, "MAX_BYTES", 1024 * 1024)
    with source(tmp_path, (row("large", "x" * (2 * 1024 * 1024)),)) as (session, schemas, keys, _native):
        with pytest.raises(freeze.HistoryModelQualificationBoundV3Error, match="retention_bound_reached"):
            freeze._copy_native_snapshot45(session, schemas, keys, tmp_path / "private-native.sqlite3", lambda: None)


def test_native45_cancel_during_stream_keeps_source_ro(tmp_path):
    calls = 0

    def check():
        nonlocal calls
        calls += 1
        if calls == 7:
            raise ValueError("private_control_cancelled")

    with source(tmp_path, (row("large", "x" * (17 * 1024 * 1024)),)) as (session, schemas, keys, _native):
        with pytest.raises(ValueError, match="private_control_cancelled"):
            freeze._copy_native_snapshot45(session, schemas, keys, tmp_path / "private-native.sqlite3", check)
    assert calls == 7


def test_native45_refuses_non_readonly_source(tmp_path):
    with source(tmp_path, (row("small", "text"),)) as (session, schemas, keys, _native):
        session.connection().exec_driver_sql("PRAGMA query_only=OFF")
        try:
            with pytest.raises(ValueError, match="native_stream_utf8_readonly_required"):
                freeze._copy_native_snapshot45(session, schemas, keys, tmp_path / "private-native.sqlite3", lambda: None)
        finally:
            session.connection().exec_driver_sql("PRAGMA query_only=ON")
