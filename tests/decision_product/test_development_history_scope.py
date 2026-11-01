"""Real finite capture and terminal read-scope boundaries, without a fake report.

Full historical publication authentication uses the two genuine reports in the
separate production-adapter acceptance. These local rows prove scope eligibility
only; no accepted research result or publication receipt is manufactured here.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import replace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from packages.kernel.decision_hub_kernel.application import (
    development_history_reader as readers,
)
from packages.kernel.decision_hub_kernel.application import (
    research_graph_history_versions as history,
)
from packages.kernel.decision_hub_kernel.application.development_history_snapshot import (
    native_history_path,
)
from packages.kernel.decision_hub_kernel.application.perpetual_saved_report import (
    PerpetualSavedReportQuery,
)
from packages.kernel.decision_hub_kernel.persistence.db import RunRecord
from packages.runtime_adapters.development_runtime import (
    development_runtime_from_environment,
)
from tests.decision_product.development_history_test_support import (
    AT,
    REFERENCE_JSON,
    RUN_ID,
    HistoryCase,
    changed_run,
    development_environment,
    original_manifest,
    other_database,
    private_module_names,
    scope_only_reference,
)
from tests.decision_product.development_history_test_support import (
    history_case as history_case,
)
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import (
    binding_case as binding_case,
)
from tests.decision_product.test_perpetual_research_binding import freeze


@pytest.mark.parametrize("status", ["completed", "degraded"])
@pytest.mark.parametrize("incoming_query_only", [0, 1])
def test_legal_terminal_scope_preserves_caller_transaction_and_query_only(
    history_case: HistoryCase, status: str, incoming_query_only: int,
) -> None:
    case = history_case
    reader = case.reader()
    with changed_run(case, status=status), case.session(begin=False) as session:
        connection = session.connection()
        connection.exec_driver_sql(f"PRAGMA query_only={incoming_query_only}")
        connection.exec_driver_sql("BEGIN")
        native = connection.connection.driver_connection
        transaction = session.get_transaction()
        before_changes = native.total_changes
        with reader.publication_scope(session, RUN_ID, REFERENCE_JSON):
            assert native.execute("PRAGMA query_only").fetchone()[0] == 1
            assert session.scalar(
                text("SELECT status FROM runs WHERE run_id=:id"), {"id": RUN_ID},
            ) == status
            assert native.in_transaction
            assert session.connection() is connection
            assert session.get_transaction() is transaction
        assert native.in_transaction
        assert native.total_changes == before_changes
        assert native.execute("PRAGMA query_only").fetchone()[0] == incoming_query_only
        assert session.get_transaction() is transaction


@pytest.mark.parametrize("damage", ["running", "failed", "artifact", "finished", "missing"])
def test_ineligible_run_never_enters_original_publication_scope(
    history_case: HistoryCase, damage: str,
) -> None:
    case = history_case
    reader = case.reader()
    values = (
        {"artifact_id": None} if damage == "artifact" else
        {"finished_at": None} if damage == "finished" else
        {"status": damage if damage != "missing" else "completed"}
    )
    with changed_run(case, **values), case.session() as session:
        with pytest.raises(
            (ValueError, PermissionError), match="development_history_publication_not_terminal",
        ):
            with reader.publication_scope(
                session, "missing-run" if damage == "missing" else RUN_ID, REFERENCE_JSON,
            ):
                pytest.fail("ineligible Run entered a terminal-only scope")


def test_scope_rejects_a_different_physical_database(history_case: HistoryCase) -> None:
    case = history_case
    reader = case.reader()
    with other_database() as session:
        with pytest.raises(ValueError, match="development_history_database_mismatch"):
            with reader.publication_scope(session, RUN_ID, REFERENCE_JSON):
                pytest.fail("reader accepted a different SQLite database")


def test_reader_factory_cannot_mix_validated_roots_and_database(
    history_case: HistoryCase,
) -> None:
    case = history_case
    altered = replace(case.development, root=case.development.root.parent / "other")
    with pytest.raises(ValueError, match="development_history_"):
        readers.prepare_development_history_reader(case.repository, altered, case.database)


def test_dirty_session_is_rejected_before_read_scope(history_case: HistoryCase) -> None:
    reader = history_case.reader()
    with history_case.session() as session:
        row = session.get(RunRecord, RUN_ID)
        assert row is not None
        row.status = "failed"
        with pytest.raises(ValueError, match="(?:development_history|research_history)_"):
            with reader.publication_scope(session, RUN_ID, REFERENCE_JSON):
                pytest.fail("dirty ORM session entered an original read scope")


@pytest.mark.parametrize("operation", ["sql", "flush", "commit", "native_sql"])
def test_scope_blocks_consumer_writes_at_orm_and_native_boundaries(
    history_case: HistoryCase, operation: str,
) -> None:
    case = history_case
    reader = case.reader()
    with case.session() as session:
        with reader.publication_scope(session, RUN_ID, REFERENCE_JSON):
            native = session.connection().connection.driver_connection
            before_changes = native.total_changes
            with pytest.raises((ValueError, DBAPIError, sqlite3.OperationalError)):
                if operation == "sql":
                    session.execute(
                        text("UPDATE runs SET status='failed' WHERE run_id=:id"), {"id": RUN_ID},
                    )
                elif operation == "native_sql":
                    native.execute("UPDATE runs SET status='failed' WHERE run_id=?", (RUN_ID,))
                elif operation == "commit":
                    session.commit()
                else:
                    row = session.get(RunRecord, RUN_ID)
                    assert row is not None
                    row.status = "failed"
                    session.flush()
            if operation == "flush":
                session.expire_all()
            assert native.total_changes == before_changes
    with case.database.session() as session:
        row = session.get(RunRecord, RUN_ID)
        assert row is not None and row.status == "completed"


@pytest.mark.parametrize("incoming_query_only", [0, 1])
def test_scope_exception_restores_previous_query_only_and_does_not_leak_authority(
    history_case: HistoryCase, incoming_query_only: int,
) -> None:
    case = history_case
    reader = case.reader()
    before_modules = private_module_names()
    with case.session(begin=False) as session:
        connection = session.connection()
        connection.exec_driver_sql(f"PRAGMA query_only={incoming_query_only}")
        connection.exec_driver_sql("BEGIN")
        native = connection.connection.driver_connection
        with pytest.raises(RuntimeError, match="controlled-scope-error"):
            with reader.publication_scope(session, RUN_ID, REFERENCE_JSON):
                raise RuntimeError("controlled-scope-error")
        assert native.in_transaction
        assert native.execute("PRAGMA query_only").fetchone()[0] == incoming_query_only
        assert history.replay_retained_publication_in_session(
            session, run_id=RUN_ID, reference_json=REFERENCE_JSON,
            declared_json="{}", expected_lease_owner=None,
        ) is None
    assert private_module_names() == before_modules


def test_changed_physical_connection_invalidates_read_and_resets_scope(
    history_case: HistoryCase,
) -> None:
    case = history_case
    reader = case.reader()
    with case.session() as session:
        connection = session.connection()
        with pytest.raises(ValueError, match="development_history_read_cleanup_failed"):
            with reader.publication_scope(session, RUN_ID, REFERENCE_JSON):
                connection.invalidate()
        assert connection.invalidated
    with case.session() as later:
        assert history.replay_retained_publication_in_session(
            later, run_id=RUN_ID, reference_json=REFERENCE_JSON,
            declared_json="{}", expected_lease_owner=None,
        ) is None


def test_nested_scope_restores_outer_binding_and_rejects_wrong_ref(
    history_case: HistoryCase,
) -> None:
    case = history_case
    reader = case.reader()
    with case.session() as session:
        with reader.publication_scope(session, RUN_ID, REFERENCE_JSON):
            with reader.publication_scope(session, RUN_ID, REFERENCE_JSON):
                assert session.scalar(text("SELECT 1")) == 1
            with pytest.raises(ValueError, match="development_history_scope_mismatch"):
                history.replay_retained_publication_in_session(
                    session, run_id=RUN_ID,
                    reference_json=scope_only_reference(payload_hash="b" * 64),
                    declared_json="{}", expected_lease_owner=None,
                )


@pytest.mark.parametrize("damage", ["run", "session"])
def test_fallback_cannot_borrow_another_run_or_session_scope(
    history_case: HistoryCase, damage: str,
) -> None:
    case = history_case
    reader = case.reader()
    with case.session() as outer, case.session() as other:
        with reader.publication_scope(outer, RUN_ID, REFERENCE_JSON):
            with pytest.raises(ValueError, match="development_history_scope_mismatch"):
                history.replay_retained_publication_in_session(
                    other if damage == "session" else outer,
                    run_id="another-run" if damage == "run" else RUN_ID,
                    reference_json=REFERENCE_JSON,
                    declared_json="{}", expected_lease_owner=None,
                )


def test_absent_scope_keeps_the_existing_default_dispatch(history_case: HistoryCase) -> None:
    with history_case.session() as session:
        assert history.replay_retained_publication_in_session(
            session, run_id=RUN_ID, reference_json=REFERENCE_JSON,
            declared_json="{}", expected_lease_owner=None,
        ) is None
    assert history._REGISTRY_BINDING is None  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("fail_inside", [False, True])
def test_real_private_orm_namespace_is_distinct_and_discarded_on_all_exits(
    history_case: HistoryCase, fail_inside: bool,
) -> None:
    # This directly verifies the typed loader mechanism, not recipe/report
    # authority. Only primitive observations escape the namespace.
    case = history_case
    manifest = original_manifest(case)
    payloads = {
        item.path: native_history_path(case.snapshot.root / item.path).read_bytes()
        for item in (*manifest.source_files, *manifest.schema_files)
    }
    before = private_module_names()

    def inspect_original() -> tuple[bool, bool]:
        with history.original_publication_namespace(
            "development_history_test_" + uuid.uuid4().hex,
            payloads,
            native_history_path(case.snapshot.root),
        ) as load:
            original_db = load("packages.kernel.decision_hub_kernel.persistence.db")
            distinct_class = original_db.RunRecord is not RunRecord
            distinct_table = original_db.RunRecord.__table__ is not RunRecord.__table__
            assert private_module_names() - before
            if fail_inside:
                raise RuntimeError("controlled-private-namespace-error")
            return distinct_class, distinct_table

    if fail_inside:
        with pytest.raises(RuntimeError, match="controlled-private-namespace-error"):
            inspect_original()
    else:
        assert inspect_original() == (True, True)
    assert private_module_names() == before


def test_formal_mode_has_no_validated_development_object(history_case: HistoryCase) -> None:
    case = history_case
    environment = development_environment(case.repository, case.development.root)
    environment["DECISION_HUB_DEVELOPMENT_MODE"] = "0"
    assert development_runtime_from_environment(
        case.repository, environment=environment,
    ) is None


def test_query_default_reader_still_rejects_an_unsaved_run_before_publication(
    history_case: HistoryCase,
) -> None:
    # The real admission reader finds no admission row. A bare terminal local
    # Run is not relabelled an authenticated report by the new scope feature.
    with pytest.raises(ValueError, match="perpetual_report_not_applicable"):
        PerpetualSavedReportQuery(
            history_case.database, now=lambda: AT, historical_reader=None,
        ).read("foreign-owner", RUN_ID)


def test_actual_owner_guard_still_precedes_publication_for_a_genuine_admission(
    binding_case,
) -> None:
    # This compact existing fixture saves real settings/raw/admission originals;
    # it does not launch native Vitest or construct publication_case/Graph data.
    freeze(binding_case)
    with pytest.raises(PermissionError, match="perpetual_report_owner_mismatch"):
        PerpetualSavedReportQuery(binding_case["db"], historical_reader=None).read(
            "foreign-owner", binding_case["run_id"],
        )
