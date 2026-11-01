"""Small private SQLite controls for the five additive outcome tables."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

from packages.kernel.decision_hub_kernel.persistence.history_outcome_v2_records import (
    HistoryOutcomeOpportunityV2Record,
    HistoryOutcomePublicationV2Record,
    HistoryOutcomeScoreV2Record,
)

NOW = datetime(2026, 10, 10, 10, tzinfo=UTC)


def migration():
    path = Path(__file__).resolve().parents[2] / "migrations/versions/0089_history_outcome_v2.py"
    spec = importlib.util.spec_from_file_location("private_outcome_migration40", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "0088_history_hourly_v2"
    assert len(module.TABLES) == 5
    return module


def original(request="private_request"):
    return dict(
        owner_claim="private_owner",
        request_id=request,
        payload_json='{"private_fixture":true}',
        payload_hash="a" * 64,
        original_ref="private-fixture-original",
        original_hash="b" * 64,
        source_files_json="[]",
        source_original_ref="private-fixture-source",
        source_original_hash="c" * 64,
        produced_at=NOW,
        recorded_at=NOW,
        fixture_mode=True,
    )


def opportunity():
    return dict(
        **original(),
        opportunity_id="hopportunity2_" + "a" * 48,
        protocol_id="heval2_" + "b" * 48,
        scope_id="private_scope",
        model_id="hfit2_" + "c" * 48,
        hour_at=NOW + timedelta(hours=1),
        registered_at=NOW,
        dispatch_deadline_at=NOW + timedelta(hours=1, minutes=5),
        state="pending",
        attempts=0,
        generation=0,
        next_due_at=NOW + timedelta(hours=1, minutes=5),
    )


def test_additive_upgrade_and_empty_downgrade_leave_existing_table_unchanged(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "private.sqlite3"))
    module = migration()
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE private_existing (value TEXT NOT NULL)")
            connection.exec_driver_sql("INSERT INTO private_existing VALUES ('retained')")
            before = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_schema WHERE name='private_existing'"
            ).scalar_one()
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
                assert set(inspect(connection).get_table_names()) == {
                    "private_existing",
                    *(table.name for table in module.TABLES),
                }
                indexes = inspect(connection).get_indexes(
                    HistoryOutcomeOpportunityV2Record.__tablename__
                )
                assert any(
                    index["column_names"] == ["state", "next_due_at", "lease_expires_at"]
                    for index in indexes
                )
                uniques = inspect(connection).get_unique_constraints(
                    HistoryOutcomeOpportunityV2Record.__tablename__
                )
                assert any(
                    unique["column_names"] == ["protocol_id", "scope_id", "hour_at"]
                    for unique in uniques
                )
                module.downgrade()
            assert inspect(connection).get_table_names() == ["private_existing"]
            assert (
                connection.exec_driver_sql(
                    "SELECT sql FROM sqlite_schema WHERE name='private_existing'"
                ).scalar_one()
                == before
            )
            assert (
                connection.exec_driver_sql("SELECT value FROM private_existing").scalar_one()
                == "retained"
            )
    finally:
        engine.dispose()


def test_nonempty_last_table_refuses_before_dropping_any_empty_table(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "private.sqlite3"))
    module = migration()
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
                connection.execute(
                    HistoryOutcomeScoreV2Record.__table__.insert().values(
                        **original(),
                        score_id="hscore2_" + "d" * 48,
                        protocol_id="heval2_" + "b" * 48,
                        actual_as_of=NOW,
                    )
                )
                with pytest.raises(ValueError, match="nonempty_outcome_originals_preserved"):
                    module.downgrade()
            assert set(inspect(connection).get_table_names()) == {
                table.name for table in module.TABLES
            }
            assert (
                connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM market_history_v2_outcome_scores"
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()


def test_attempt_limit_first_binding_uniqueness_and_independent_acceptance_shape(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "private.sqlite3"))
    module = migration()
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
            values = opportunity()
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    HistoryOutcomeOpportunityV2Record.__table__.insert().values(
                        **{**values, "attempts": 5}
                    )
                )
            connection.execute(
                HistoryOutcomeOpportunityV2Record.__table__.insert().values(**values)
            )
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    HistoryOutcomeOpportunityV2Record.__table__.insert().values(
                        **{
                            **values,
                            "opportunity_id": "hopportunity2_" + "e" * 48,
                            "request_id": "different_request",
                        }
                    )
                )
            publication = dict(
                **{
                    **original("acceptance_request"),
                    "produced_at": NOW + timedelta(seconds=2),
                    "recorded_at": NOW + timedelta(seconds=2),
                },
                publication_id="hacceptance2_" + "f" * 48,
                protocol_id=values["protocol_id"],
                kind="acceptance",
                accepted_kind="protocol",
                subject_id=values["protocol_id"],
            )
            # Neither pre-COMMIT recorded_at nor a missing S can be accepted.
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    HistoryOutcomePublicationV2Record.__table__.insert().values(**publication)
                )
            connection.execute(
                HistoryOutcomePublicationV2Record.__table__.insert().values(
                    **publication, independently_verified_at=NOW + timedelta(seconds=1)
                )
            )
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    HistoryOutcomePublicationV2Record.__table__.insert().values(
                        **{
                            **publication,
                            "publication_id": "hacceptance2_" + "1" * 48,
                            "request_id": "another_acceptance",
                        },
                        independently_verified_at=NOW + timedelta(seconds=2),
                    )
                )
            assert (
                connection.exec_driver_sql(
                    "SELECT independently_verified_at FROM market_history_v2_outcome_publications"
                )
                .scalar_one()
                .startswith("2026-10-10 10:00:01")
            )
    finally:
        engine.dispose()
