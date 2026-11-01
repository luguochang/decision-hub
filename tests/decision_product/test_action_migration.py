from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

from packages.kernel.decision_hub_kernel.persistence import action_records
from packages.kernel.decision_hub_kernel.persistence.db import Base, Database

ROOT = Path(__file__).resolve().parents[2]
BEFORE = "0092_research_report_audits"
AFTER = "0093_decision_product_v1"
TABLES = (
    "decision_opportunities",
    "decision_snapshots",
    "action_plan_revisions",
    "action_plan_heads",
    "action_condition_evaluations",
    "action_outcomes",
    "action_command_receipts",
    "research_skill_use_events",
    "experience_case_skill_links",
)
IMMUTABLE = tuple(t.name for t in action_records.APPEND_ONLY_TABLES)
HASH = "a" * 64
TIME = "2026-10-13 04:00:00.000000"
LATER = "2026-10-14 04:00:00.000000"


def config(path: Path) -> Config:
    result = Config(str(ROOT / "alembic.ini"))
    result.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path}")
    result.attributes["configure_logger"] = False
    return result


def insert(connection, table, values):
    names = ",".join(f'"{name}"' for name in values)
    connection.execute(
        f'INSERT INTO "{table}" ({names}) VALUES ({",".join("?" for _ in values)})',
        tuple(values.values()),
    )


def new_row(table):
    payload = {"payload_json": '{"price":"123.4500","probability":null}', "payload_hash": HASH}
    if table == "decision_opportunities":
        return dict(
            opportunity_id="o1",
            key="opportunity:registered",
            scope_id="crypto:spot:okx:BTC-USDT:trade:1m",
            horizon="24h",
            kind="scheduled",
            event_ref=None,
            recipe_hash=HASH,
            target_hash=HASH,
            registered_at=TIME,
            start_at=TIME,
            end_at=LATER,
            cohort="prospective",
            status="open",
            reason=None,
        )
    if table == "decision_snapshots":
        return dict(
            decision_id="d1",
            opportunity_id="o1",
            run_id="run1",
            source_result_ref="original:run1",
            source_hash=HASH,
            recipe_hash=HASH,
            known_at=TIME,
            **payload,
        )
    if table == "action_plan_revisions":
        return dict(
            revision_id="r1",
            plan_id="p1",
            revision_no=1,
            decision_id="d1",
            opportunity_id="o1",
            previous_id=None,
            position_context="flat",
            published_at=TIME,
            valid_until=LATER,
            **payload,
        )
    if table == "action_plan_heads":
        return dict(
            plan_id="p1",
            current_revision_id="r1",
            state_version=1,
            state="armed",
            monitor_state="active",
            next_due_at=TIME,
            valid_until=LATER,
            lease_owner=None,
            lease_generation=0,
            lease_expires_at=None,
            last_input_watermark=None,
            enter_streak=0,
        )
    if table == "action_condition_evaluations":
        return dict(
            evaluation_id="e1",
            plan_id="p1",
            revision_id="r1",
            tick_at=TIME,
            lease_generation=1,
            input_hash=HASH,
            **payload,
        )
    if table == "action_outcomes":
        return dict(
            outcome_id="out1",
            opportunity_id="o1",
            plan_id="p1",
            revision_id="r1",
            mode="paper",
            protocol_hash=HASH,
            version=1,
            status="pending",
            due_at=LATER,
            **payload,
        )
    if table == "action_command_receipts":
        return dict(
            owner_id="owner1",
            request_id="request1",
            plan_id="p1",
            operation="display",
            payload_hash=HASH,
            response_json='{"status":"recorded"}',
            accepted_at=TIME,
        )
    if table == "research_skill_use_events":
        return dict(
            event_id="se1",
            use_id="use1",
            run_id="run1",
            generation=1,
            root_session_id=None,
            execution_session_id=None,
            skill_version_id="skill1",
            package_hash=HASH,
            workflow_hash=HASH,
            method_binding_hash=HASH,
            stage="selected",
            node_id="",
            stage_request_id="request1",
            parent_event_ref=None,
            original_ref="skill-selection:1",
            original_hash=HASH,
            first_known_at=TIME,
            **payload,
        )
    return dict(
        link_id="link1",
        scope_id="crypto:spot:okx:BTC-USDT:trade:1m",
        experience_id="experience1",
        experience_hash=HASH,
        case_id="case1",
        case_hash=HASH,
        candidate_skill_version_id="skill1",
        candidate_skill_hash=HASH,
        bridge_protocol_hash=HASH,
        available_at=TIME,
        disposition="candidate",
        reason=None,
    )


def notification(kind, ident):
    return dict(
        id=ident,
        artifact_id="report-old" if kind == "report" else None,
        subject_kind=kind,
        subject_id="report-old" if kind == "report" else "risk-old",
        payload_json=None if kind == "report" else '{"old":"风险原件"}',
        payload_hash=None if kind == "report" else HASH,
        channel="local",
        dedupe_key=f"legacy-{ident}",
        attempts=3,
        sent_at=None,
        next_attempt_at=TIME,
        failed_at=None,
        last_error_code="retryable-old",
        dispatch_status="in_flight",
        claim_owner="old-worker",
        claim_generation=7,
        claim_expires_at=LATER,
        provider_message_id="provider-old",
        provider_accepted_at=TIME,
        attempt_history_json='[{"attempt":1,"result":"transport_timeout"},{"attempt":2,"result":"accepted"}]',
    )


def old_rows(path):
    with sqlite3.connect(path) as connection:
        names = [
            r[0]
            for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            if r[0] != "alembic_version" and r[0] not in TABLES
        ]
        return {
            name: sorted(
                connection.execute(f'SELECT * FROM "{name}"').fetchall(),
                key=lambda r: json.dumps(r, ensure_ascii=False),
            )
            for name in names
        }


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    path = tmp_path_factory.mktemp("action-migration") / "0092.sqlite3"
    command.upgrade(config(path), BEFORE)
    with sqlite3.connect(path) as connection:
        for name in TABLES:
            assert (
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
                ).fetchone()
                is None
            )
        insert(connection, "outbox", notification("report", 101))
        insert(connection, "outbox", notification("risk_alert", 202))
    return path


@pytest.fixture
def upgraded(baseline, tmp_path):
    path = tmp_path / "0093.sqlite3"
    shutil.copyfile(baseline, path)
    command.upgrade(config(path), AFTER)
    return path


def test_upgrade_downgrade_preserve_all_legacy_rows_and_outbox_claims(baseline, upgraded):
    originals = old_rows(baseline)
    assert old_rows(upgraded) == originals
    with sqlite3.connect(upgraded) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (AFTER,)
        for name in TABLES:
            assert connection.execute(f'SELECT count(*) FROM "{name}"').fetchone() == (0,)
        indexes = {
            r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert {
            "ix_action_head_due",
            "ix_skill_use_run",
            "ix_skill_use_version",
            "ix_outbox_due_claim",
            "ix_action_evaluation_plan_tick",
            "ix_action_outcome_due",
        } <= indexes
        triggers = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_%_immutable_%'"
        ).fetchall()
        assert len(triggers) == len(IMMUTABLE) * 3
    command.upgrade(config(upgraded), AFTER)
    assert old_rows(upgraded) == originals
    command.downgrade(config(upgraded), BEFORE)
    assert old_rows(upgraded) == originals
    with sqlite3.connect(upgraded) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (BEFORE,)
        for name in TABLES:
            assert (
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE name=?", (name,)
                ).fetchone()
                is None
            )
        with pytest.raises(sqlite3.IntegrityError, match="ck_outbox_subject_identity"):
            insert(connection, "outbox", notification("action_revision", 303))


@pytest.mark.parametrize("table", TABLES)
def test_nonempty_any_new_ledger_refuses_destructive_downgrade(upgraded, table):
    with sqlite3.connect(upgraded) as connection:
        insert(connection, table, new_row(table))
    originals = old_rows(upgraded)
    with pytest.raises(RuntimeError, match="decision_product_downgrade_nonempty_forbidden"):
        command.downgrade(config(upgraded), BEFORE)
    with sqlite3.connect(upgraded) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (AFTER,)
        assert connection.execute(f'SELECT count(*) FROM "{table}"').fetchone() == (1,)
        assert all(
            connection.execute("SELECT name FROM sqlite_master WHERE name=?", (name,)).fetchone()
            for name in TABLES
        )
    assert old_rows(upgraded) == originals


def test_new_outbox_subject_and_old_identity_constraints(upgraded):
    with sqlite3.connect(upgraded) as connection:
        insert(connection, "outbox", notification("action_revision", 303))
        for change in (
            {"artifact_id": "illegal-artifact"},
            {"subject_id": None},
            {"payload_json": None},
            {"payload_hash": None},
            {"subject_kind": "invented"},
        ):
            bad = notification("action_revision", 404) | change
            with pytest.raises(sqlite3.IntegrityError, match="ck_outbox_subject_identity"):
                insert(connection, "outbox", bad)
        with pytest.raises(sqlite3.IntegrityError, match="ck_outbox_claim_generation"):
            insert(
                connection,
                "outbox",
                notification("action_revision", 404) | {"claim_generation": -1},
            )
    originals = old_rows(upgraded)
    with pytest.raises(
        RuntimeError, match="decision_product_downgrade_action_notification_forbidden"
    ):
        command.downgrade(config(upgraded), BEFORE)
    assert old_rows(upgraded) == originals


@pytest.mark.parametrize("table", IMMUTABLE)
def test_originals_reject_update_and_delete_at_database_boundary(upgraded, table):
    row = new_row(table)
    key = next(iter(row))
    with sqlite3.connect(upgraded) as connection:
        insert(connection, table, row)
        before = connection.execute(f'SELECT * FROM "{table}"').fetchall()
        for sql in (f'UPDATE "{table}" SET "{key}"="{key}"', f'DELETE FROM "{table}"'):
            with pytest.raises(sqlite3.IntegrityError, match=f"{table}_immutable"):
                connection.execute(sql)
        assert connection.execute(f'SELECT * FROM "{table}"').fetchall() == before


@pytest.mark.parametrize("table", IMMUTABLE)
def test_replace_cannot_delete_original_even_with_recursive_triggers_off(upgraded, table):
    row = new_row(table)
    with sqlite3.connect(upgraded) as connection:
        connection.execute("PRAGMA recursive_triggers=OFF")
        insert(connection, table, row)
        before = connection.execute(f'SELECT * FROM "{table}"').fetchall()
        replacements = [row]
        if table != "action_command_receipts":
            replacements.append(row | {next(iter(row)): "different-primary-key"})
        for replacement in replacements:
            names = ",".join(f'"{name}"' for name in replacement)
            placeholders = ",".join("?" for _ in replacement)
            with pytest.raises(sqlite3.IntegrityError, match="UNIQUE immutable identity"):
                connection.execute(
                    f'INSERT OR REPLACE INTO "{table}" ({names}) VALUES ({placeholders})',
                    tuple(replacement.values()),
                )
            assert connection.execute(f'SELECT * FROM "{table}"').fetchall() == before


def test_owner_idempotency_and_skill_non_node_deduplication(upgraded):
    with sqlite3.connect(upgraded) as connection:
        row = new_row("action_command_receipts")
        insert(connection, "action_command_receipts", row)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            insert(connection, "action_command_receipts", row | {"payload_hash": "b" * 64})
        insert(connection, "action_command_receipts", row | {"owner_id": "owner2"})
        skill = new_row("research_skill_use_events")
        insert(connection, "research_skill_use_events", skill)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            insert(connection, "research_skill_use_events", skill | {"event_id": "se2"})
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
            insert(
                connection,
                "research_skill_use_events",
                skill | {"event_id": "se3", "node_id": None},
            )
        insert(
            connection, "research_skill_use_events", skill | {"event_id": "se4", "stage": "failed"}
        )


def test_fence_counters_revision_slot_uniqueness_and_outcome_new_version(upgraded):
    with sqlite3.connect(upgraded) as connection:
        for table, field, bad in (
            ("action_plan_heads", "state_version", 0),
            ("action_plan_heads", "lease_generation", -1),
            ("action_plan_heads", "enter_streak", -1),
            ("action_plan_heads", "lease_owner", "worker_without_expiry"),
            ("action_plan_revisions", "revision_no", 0),
            ("action_outcomes", "version", 0),
        ):
            with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
                insert(connection, table, new_row(table) | {field: bad})
        for table, pk in (
            ("action_plan_revisions", "revision_id"),
            ("action_condition_evaluations", "evaluation_id"),
            ("action_outcomes", "outcome_id"),
            ("decision_snapshots", "decision_id"),
        ):
            row = new_row(table)
            insert(connection, table, row)
            with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
                insert(connection, table, row | {pk: "other-id"})
        insert(
            connection,
            "action_outcomes",
            new_row("action_outcomes") | {"outcome_id": "out2", "version": 2, "status": "settled"},
        )
        assert connection.execute(
            "SELECT version,status FROM action_outcomes ORDER BY version"
        ).fetchall() == [(1, "pending"), (2, "settled")]
        insert(connection, "action_plan_heads", new_row("action_plan_heads"))
        connection.execute(
            "UPDATE action_plan_heads SET state_version=2,lease_generation=1,"
            "lease_owner='worker',lease_expires_at=?",
            (LATER,),
        )
        insert(connection, "decision_opportunities", new_row("decision_opportunities"))
        connection.execute("UPDATE decision_opportunities SET status='decided'")


def test_create_all_registration_and_frozen_migration_schema_match(upgraded, tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "isolated-data"))
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'orm.sqlite3'}")
    db.create_all()
    migrated = create_engine(f"sqlite+pysqlite:///{upgraded}")
    try:
        left, right = inspect(db.engine), inspect(migrated)
        for name in TABLES:

            def signature(inspector, table_name):
                return [
                    (c["name"], str(c["type"]), c["nullable"], c["primary_key"], c["default"])
                    for c in inspector.get_columns(table_name)
                ]

            assert signature(left, name) == signature(right, name)
            assert {c["name"]: c["sqltext"] for c in left.get_check_constraints(name)} == {
                c["name"]: c["sqltext"] for c in right.get_check_constraints(name)
            }
            assert {
                c["name"]: tuple(c["column_names"]) for c in left.get_unique_constraints(name)
            } == {c["name"]: tuple(c["column_names"]) for c in right.get_unique_constraints(name)}
            assert {c["name"]: tuple(c["column_names"]) for c in left.get_indexes(name)} == {
                c["name"]: tuple(c["column_names"]) for c in right.get_indexes(name)
            }
        with db.engine.begin() as connection:
            table = Base.metadata.tables["action_command_receipts"]
            row = new_row("action_command_receipts")
            row["accepted_at"] = datetime.fromisoformat(TIME)
            connection.execute(table.insert().values(**row))
        with pytest.raises(IntegrityError, match="action_command_receipts_immutable"):
            with db.engine.begin() as connection:
                connection.execute(table.delete())
    finally:
        db.engine.dispose()
        migrated.dispose()
