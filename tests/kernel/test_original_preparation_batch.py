"""Post-write original read batching and preservation of row guards.

Reuse the existing temporary file SQLite fixture and production models. This
checks post-write read mechanics, not provider permission or wall-clock speed.
"""
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.persistence import original_preparation as prep
from packages.kernel.decision_hub_kernel.persistence.db import (
    DshSessionLinkRecord,
    ProductReadReceiptRecord,
    RunRoleOutputRecord,
    utcnow,
)
from tests.kernel.test_original_preparation import database

assert database is not None


def add_output(session, identity):
    session.add(RunRoleOutputRecord(
        output_id=identity, run_id="run", role=f"post-batch:{identity}", payload_json="{}",
        payload_hash="0" * 64, schema_version="original.v1", created_at=utcnow(),
    ))


def frozen_columns(session, model, identity):
    row = session.get(model, identity)
    return {column.key: getattr(row, column.key) for column in model.__table__.columns}


def prepare_outputs(database, count=1):
    with database.session() as session:
        for index in range(count - 1):
            add_output(session, f"batch-{index:05d}")

    def read(session):
        # Descending capture order deliberately differs from ordinary PK scans.
        return session.scalars(select(RunRoleOutputRecord).order_by(
            RunRoleOutputRecord.output_id.desc(),
        )).all()

    saved = prep.prepare_originals(database, read)
    assert len(saved.rows) == count
    return saved


@contextmanager
def selects(session):
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT "):
            statements.append(statement)

    connection = session.connection()
    event.listen(connection, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(connection, "before_cursor_execute", record)


def test_post_single_pk_257_rows_uses_bounded_roundtrips(database):
    # First TDD RED target: old method issues 257 SELECTs. No timing threshold.
    saved = prepare_outputs(database, 257)
    with database.session() as session:
        prep.begin_immediate(session)
        saved.verify(session)  # full query/range/absence contract is unchanged
        with selects(session) as statements:
            saved.verify_after_writes(session)
        assert len(statements) <= 4


def test_post_composite_pk_and_different_models_are_grouped_separately(database):
    now = utcnow()
    with database.session() as session:
        for index in range(129):
            session.add(ProductReadReceiptRecord(
                revision_id=f"revision-{index:05d}", owner_id="owner", read_at=None, updated_at=now,
            ))

    def read(session):
        assert session.get(RunRoleOutputRecord, "original") is not None
        assert len(session.scalars(select(ProductReadReceiptRecord).order_by(
            ProductReadReceiptRecord.revision_id.desc(),
        )).all()) == 129

    saved = prep.prepare_originals(database, read)
    assert len(saved.rows) == 130
    with database.session() as session:
        prep.begin_immediate(session)
        saved.verify(session)
        with selects(session) as statements:
            saved.verify_after_writes(session)
        assert len(statements) <= 4  # bounded across both identity shapes


def test_post_batches_stream_rows_instead_of_buffering_large_payloads(database):
    saved = prepare_outputs(database, 129)
    full_reads, chunk_sizes = [], []

    class ReadCursor(sqlite3.Cursor):
        def fetchall(self):
            full_reads.append(True)
            return super().fetchall()

        def fetchmany(self, size=None):
            rows = super().fetchmany() if size is None else super().fetchmany(size)
            chunk_sizes.append(len(rows))
            return rows

    class ReadConnection(sqlite3.Connection):
        def cursor(self, *args, **kwargs):
            return super().cursor(*args, **(kwargs | {"factory": ReadCursor}))

    engine = create_engine(database.engine.url, connect_args={"factory": ReadConnection})
    try:
        with Session(engine) as session:
            prep.begin_immediate(session)
            saved.verify(session)
            full_reads.clear()
            chunk_sizes.clear()
            saved.verify_after_writes(session)
        assert full_reads == []
        assert chunk_sizes and max(chunk_sizes) <= 1
    finally:
        engine.dispose()


@pytest.mark.parametrize("mutation", ["delete", "payload_json", "role"])
def test_post_missing_or_any_frozen_value_change_rejects_and_rolls_back(database, mutation):
    saved = prepare_outputs(database, 2)
    with pytest.raises(ValueError, match="^original_preparation_original_changed$"):
        with database.session() as session:
            prep.begin_immediate(session)
            saved.verify(session)
            original = session.get(RunRoleOutputRecord, "original")
            if mutation == "delete":
                session.delete(original)
            else:
                # Deliberately do not update payload_hash. Reading only hashes
                # would miss the raw bytes or non-payload frozen-column change.
                setattr(original, mutation, '{"tampered":true}' if mutation == "payload_json" else "changed-role")
            add_output(session, "own-publication")
            session.flush()
            saved.verify_after_writes(session)
    with database.session() as session:
        assert session.get(RunRoleOutputRecord, "original").payload_json == "{}"
        assert session.get(RunRoleOutputRecord, "original").role == "original"
        assert session.get(RunRoleOutputRecord, "own-publication") is None


def test_post_groups_same_model_by_exact_captured_columns(database):
    issued = prep.prepare_originals(
        database, lambda s: frozen_columns(s, RunRoleOutputRecord, "original"),
    )
    original = issued.rows[0]
    columns = ("output_id", "payload_hash")
    narrow = replace(original, columns=columns, digest=prep._row_digest(
        columns, tuple(issued.value[key] for key in columns), False,
    ))
    # Controlled projection mix isolates grouping mechanics only. This clone
    # does NOT claim issued read-snapshot authority or execution permission.
    mixed = replace(issued, rows=(narrow, original))
    with database.session() as session:
        prep.begin_immediate(session)
        issued.verify(session)
        with selects(session) as statements:
            mixed.verify_after_writes(session)
        assert len(statements) == 2
    with pytest.raises(ValueError, match="^original_preparation_original_changed$"):
        with database.session() as session:
            prep.begin_immediate(session)
            issued.verify(session)
            session.get(RunRoleOutputRecord, "original").payload_json = '{"changed":true}'
            session.flush()
            mixed.verify_after_writes(session)  # narrow must not replace the full projection


def prepare_link(database, *, activity, counter):
    now = utcnow()
    with database.session() as session:
        session.add(DshSessionLinkRecord(
            run_id="link-run", dsh_session_id="link-session", request_hash="1" * 64,
            state="admitted", generation=1, upstream_identity_json="{}",
            plugin_build_hash="2" * 64, deadline_at=now + timedelta(hours=1),
            tool_calls_started=0, last_seq=0, created_at=now, updated_at=now,
        ))
    return prep.prepare_originals(
        database, lambda s: frozen_columns(s, DshSessionLinkRecord, "link-run"),
        link_activity_observations=activity, link_tool_counter_observations=counter,
    )


def test_post_preserves_captured_volatile_exclusions_and_allowed_activity_mask(database):
    saved = prepare_link(database, activity=True, counter=True)
    with database.session() as session:
        prep.begin_immediate(session)
        saved.verify(session)
        link = session.get(DshSessionLinkRecord, "link-run")
        link.state, link.last_seq, link.tool_calls_started = "running", 17, 4
        link.last_seen_at, link.updated_at = utcnow(), utcnow()
        session.flush()
        saved.verify_after_writes(session)
    # Controlled duplicate identity with two masks must not reuse the permissive
    # digest for the stricter row. Mechanics only; this clone grants no issuance.
    original = saved.rows[0]
    strict = replace(original, link_activity_observations=False, digest=prep._row_digest(
        original.columns, tuple(saved.value[key] for key in original.columns), False,
    ))
    mixed = replace(saved, rows=(original, strict))
    with database.session() as session:
        prep.begin_immediate(session)
        with pytest.raises(ValueError, match="^original_preparation_original_changed$"):
            mixed.verify_after_writes(session)


@pytest.mark.parametrize("mutation", ["terminal", "generation", "deadline", "unmasked_state", "unmasked_counter"])
def test_post_masks_do_not_cover_terminal_identity_deadline_or_disabled_masks(database, mutation):
    saved = prepare_link(database, activity=mutation != "unmasked_state", counter=mutation != "unmasked_counter")
    with pytest.raises(ValueError, match="^original_preparation_original_changed$"):
        with database.session() as session:
            prep.begin_immediate(session)
            saved.verify(session)
            link = session.get(DshSessionLinkRecord, "link-run")
            if mutation == "terminal":
                link.state = "completed"
            elif mutation == "generation":
                link.generation += 1
            elif mutation == "deadline":
                link.deadline_at += timedelta(seconds=1)
            elif mutation == "unmasked_state":
                link.state = "running"
            else:
                link.tool_calls_started += 1
            session.flush()
            saved.verify_after_writes(session)


def test_post_keeps_no_autoflush_and_allows_own_flushed_additions(database):
    saved = prepare_outputs(database)
    with database.session() as session:
        prep.begin_immediate(session)
        saved.verify(session)
        add_output(session, "own-flushed")
        session.flush()
        add_output(session, "own-still-pending")
        pending = tuple(session.new)
        flushed = []

        def before_flush(*_args):
            flushed.append(True)

        event.listen(session, "before_flush", before_flush)
        try:
            saved.verify_after_writes(session)
        finally:
            event.remove(session, "before_flush", before_flush)
        assert tuple(session.new) == pending and not flushed
        # Post checks frozen original rows, not the old range query. Own added
        # rows are allowed AFTER the untouched full pre readset check.


def test_post_source_failure_occurs_before_any_pk_select(database, monkeypatch):
    saved = prepare_outputs(database)
    with database.session() as session:
        prep.begin_immediate(session)
        saved.verify(session)
        monkeypatch.setattr(type(saved.source), "matches", lambda _source: False)
        with selects(session) as statements, pytest.raises(ValueError, match="^original_preparation_source_changed$"):
            saved.verify_after_writes(session)
        assert statements == []


def test_post_checkpoint_cancels_during_a_batch_and_rolls_back(database):
    saved = prepare_outputs(database, 257)

    class Cancelled(Exception):
        pass

    statements = []
    with pytest.raises(Cancelled):
        with database.session() as session:
            prep.begin_immediate(session)
            saved.verify(session)
            add_output(session, "cancelled-publication")
            session.flush()
            with selects(session) as statements:
                def checkpoint():
                    if len(statements) >= 2:
                        raise Cancelled("test checkpoint")

                saved.verify_after_writes(session, checkpoint=checkpoint)
    assert len(statements) == 2  # stops before issuing the third SELECT
    with database.session() as session:
        assert session.get(RunRoleOutputRecord, "cancelled-publication") is None


# Keep existing pre bytes/phantom/absence tests and genuine source tamper test
# unchanged. No timer/lease, callback, provider or wall-clock performance fixture
# is added here. SQL counts assert batching only, not real latency improvement.
