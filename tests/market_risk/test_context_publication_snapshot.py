"""Exact saved originals, candidate-only dependent read; no HTTP or provider.

The candidate modules are compiled with their approved formal source locations.
All original Graph/dependency readers remain installed 18; these finite controls
prove projection/consumer compatibility, not a candidate standard installation.
Corruption is connection-private TEMP storage over a main mode=ro connection.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

FORMAL = Path("E:/project/study/codex/dsh/decision-hub")
DEV = FORMAL.parent / ".market-risk-b2-b4"
PREFIX = "packages.kernel.decision_hub_kernel.application."
BTC = "run_31edbfc6f7864b8a92015be939964a01"
OLD_ETH = "run_e6545db744fd46538ec870808461cd73"


@pytest.fixture(scope="module")
def candidates():
    # This separate process imports only the approved formal implementation of
    # the original Graph. Its source and archive guard is not patched.
    sys.path.insert(0, str(FORMAL))
    from packages.kernel.decision_hub_kernel.application import research_graph_binding
    from packages.kernel.decision_hub_kernel.application import (
        research_graph_history_versions as original_history,
    )
    from packages.kernel.decision_hub_kernel.application import research_graph_recipe_v3
    from packages.kernel.decision_hub_kernel.application import (
        research_context_delivery as original_context,
    )
    from packages.kernel.decision_hub_kernel.application import research_quality_view

    # Pytest may already have imported DEV packages through the repository root;
    # this is an explicit required fixture boundary, never silently redirected.
    assert Path(research_graph_binding.__file__).resolve().is_relative_to(FORMAL)
    assert Path(research_quality_view.__file__).resolve().is_relative_to(FORMAL)
    receipt = FORMAL / "artifacts/mr-release-b2b4-18/product-validated-builds.json"
    digest = hashlib.sha256(receipt.read_bytes()).hexdigest()
    original_history.configure_retained_recipe_registry_from_validated_release(
        receipt_path=receipt, receipt_sha256=digest
    )
    research_graph_recipe_v3.configure_graph_recipe_v3_from_validated_release(
        receipt_path=receipt, receipt_sha256=digest
    )
    loaded = {}
    for name in ("research_graph_history_versions", "research_quality_view", "research_context_delivery"):
        relative = "packages/kernel/decision_hub_kernel/application/" + name + ".py"
        module = ModuleType(PREFIX + "_snapshot19_" + name)
        module.__file__ = str(FORMAL / relative)
        module.__package__ = PREFIX.rstrip(".")
        sys.modules[module.__name__] = module
        exec(compile((DEV / relative).read_bytes(), module.__file__, "exec"), module.__dict__)
        loaded[name] = module
    loaded["research_graph_history_versions"].configure_retained_recipe_registry_from_validated_release(
        receipt_path=receipt, receipt_sha256=digest
    )
    prior = {PREFIX + name: sys.modules[PREFIX + name] for name in ("research_graph_history_versions", "research_quality_view")}
    for name in ("research_graph_history_versions", "research_quality_view"):
        sys.modules[PREFIX + name] = loaded[name]
    yield SimpleNamespace(**loaded, original_context=original_context)
    for name, module in prior.items():
        sys.modules[name] = module


@pytest.fixture
def physical():
    path = FORMAL / "data/core-live-20260913/db/decision_hub.sqlite3"

    def connect():
        return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)

    engine = create_engine("sqlite://", creator=connect, poolclass=NullPool)

    @contextmanager
    def session():
        with Session(engine) as value:
            try:
                yield value
            finally:
                value.rollback()

    yield SimpleNamespace(engine=engine, session=session)
    engine.dispose()


def mapped_ref(session, run_id):
    from packages.kernel.decision_hub_kernel.application.provider_input_receipts import row_reference
    from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
    from sqlalchemy import select

    row = session.scalar(select(RunRoleOutputRecord).where(
        RunRoleOutputRecord.run_id == run_id,
        RunRoleOutputRecord.schema_version == "research-mapped-observation.v1",
    ))
    assert row is not None
    return row_reference(row)


def test_current_saved_publication_null_selected_stays_unavailable_one_full_final(candidates, physical):
    saved = json.loads((FORMAL / "artifacts/market-risk-implementation-20261007/B2/formal-real-dsh18/btc-quality06.json").read_bytes())
    at = datetime.fromisoformat(saved["as_of"].replace("Z", "+00:00"))
    calls = []
    quality = candidates.research_quality_view
    original_read = quality.read_graph_final_in_session

    def read(*args, **kwargs):
        calls.append(kwargs["reference"])
        return original_read(*args, **kwargs)

    quality.read_graph_final_in_session = read
    try:
        with physical.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            native = session.connection().connection.driver_connection
            before = native.total_changes
            def consume(view, selected):
                assert view.model_dump(mode="json") == saved
                assert selected is None
                assert session.get_transaction() is transaction
                return "unavailable_no_selected_original"

            transaction = session.get_transaction()
            actual = quality.consume_published_research_originals_in_session(
                session, BTC, as_of=at, consume=consume
            )
            assert actual == "unavailable_no_selected_original"
            assert len(calls) == 1
            assert native.in_transaction and native.total_changes == before == 0
    finally:
        quality.read_graph_final_in_session = original_read


def test_retained_saved_original_consumed_inside_same_verified_scope(candidates, physical):
    history = candidates.research_graph_history_versions
    with physical.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        native = session.connection().connection.driver_connection
        reference = mapped_ref(session, OLD_ETH)
        expected = candidates.original_context.read_context_delivery_in_session(
            session, mapped_ref=reference
        )
        transaction = session.get_transaction()

        def consume(original):
            from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedExecutionRef

            scope = history._VERIFIED_RELEASE_READ.get()
            assert scope is not None and scope.session is session
            assert history._V3_READ_SCOPE.get()[1] is session
            assert session.get_transaction() is transaction
            selected = ObservedExecutionRef.model_validate_json(original.selected_observation_ref_json)
            assert selected == reference
            view = json.loads(original.view_json)
            assert view["run_id"] == OLD_ETH and view["gate_status"] == "research_only"
            return candidates.research_context_delivery.read_context_delivery_in_session(
                session, mapped_ref=selected
            )

        matched, actual = history.consume_retained_publication_quality_in_session(
            session, OLD_ETH, as_of=datetime.fromisoformat("2026-10-09T00:00:00+00:00"), consume=consume
        )
        assert matched and actual.model_dump(mode="json") == expected.model_dump(mode="json")
        assert history._VERIFIED_RELEASE_READ.get() is None
        assert history._V3_READ_SCOPE.get() is None
        assert native.in_transaction and native.total_changes == 0


@pytest.mark.parametrize("role", ["research_publication_source", "research_graph_final", "model_input"])
def test_corrupted_exact_original_not_replaced_by_locator(candidates, physical, role):
    with physical.engine.connect() as connection:
        native = connection.connection.driver_connection
        native.execute("CREATE TEMP TABLE run_role_outputs AS SELECT * FROM main.run_role_outputs WHERE run_id=?", (BTC,))
        if role == "research_graph_final":
            row = native.execute("SELECT output_id FROM temp.run_role_outputs WHERE schema_version='research-graph-final-projection.v3'").fetchone()
        elif role == "model_input":
            row = native.execute("SELECT output_id FROM temp.run_role_outputs WHERE schema_version='research-provider-input.v1' ORDER BY created_at LIMIT 1").fetchone()
        else:
            row = native.execute("SELECT output_id FROM temp.run_role_outputs WHERE role=?", (role,)).fetchone()
        assert row is not None
        native.execute("UPDATE temp.run_role_outputs SET payload_hash=? WHERE output_id=?", ("0" * 64, row[0]))
        native.commit()
        connection.exec_driver_sql("PRAGMA query_only=ON")
        before = native.total_changes
        with pytest.raises((ValueError, PermissionError)):
            candidates.research_context_delivery.ResearchContextDeliveryQuery(
                SimpleNamespace(session=lambda: Session(connection))
            ).read(BTC)
        assert native.total_changes == before


def test_dependent_read_transaction_switch_is_not_authorized(candidates, physical):
    with physical.session() as session:
        session.connection().exec_driver_sql("BEGIN")

        def consume(_view, _selected):
            session.rollback()
            session.connection().exec_driver_sql("BEGIN")
            return "must_not_escape"

        with pytest.raises(ValueError, match="source_changed|snapshot|boundary"):
            candidates.research_quality_view.consume_published_research_originals_in_session(
                session, BTC, as_of=datetime.fromisoformat("2026-10-09T00:00:00+00:00"), consume=consume
            )
