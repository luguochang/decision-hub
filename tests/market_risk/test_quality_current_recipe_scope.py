"""Finite private-SQLite controls for the publication recipe-scope wiring.

Actual current selection/installation readers, physical Session boundaries and
typed quality/context responses execute. Native full-final, publication, sidecar
and context-body authentication are explicit seams, not provider/release proof.
No HTTP, live database, model, provider or product build is used.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import ResearchReview
from packages.kernel.decision_hub_kernel.application import research_context_delivery as context
from packages.kernel.decision_hub_kernel.application import (
    research_graph_history_versions as history,
)
from packages.kernel.decision_hub_kernel.application import research_graph_recipe_v3 as recipe
from packages.kernel.decision_hub_kernel.application import research_quality_view as candidate
from packages.kernel.decision_hub_kernel.application.research_context_execution import (
    generation_proof,
)
from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
    GraphFinalProjectionRef,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_json,
)
from packages.kernel.decision_hub_kernel.application.research_recipe_read_scope import (
    current_recipe_read_scope,
)
from packages.kernel.decision_hub_kernel.decision.research_quality import assess_research_quality
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
from packages.kernel.decision_hub_kernel.ports.provider_input import ResearchContextDelivery
from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedExecutionRef
from tests.dsh_native.test_provider_recipe_read_scope import (
    forbid_external_connections as forbid_external_connections,
)
from tests.dsh_native.test_provider_recipe_read_scope import (
    installation_fixture as installation_fixture,
)
from tests.dsh_native.test_provider_recipe_read_scope import manifest as manifest
from tests.dsh_native.test_provider_recipe_read_scope import selected as selected
from tests.dsh_native.test_provider_recipe_read_scope import writer_fixture as writer_fixture
from tests.market_risk.test_quality_projection_v3 import material
from tests.market_risk.test_recipe_selection_v3 import NOW
from tests.market_risk.test_research_quality import quality_input

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
ROOT = Path(__file__).resolve().parents[2]
BEFORE = ROOT / (
    "artifacts/market-risk-implementation-20261007/B2/quality-current-scope26/"
    "research_quality_view.before01.py"
)
BEFORE_SHA = "27445d59708ceff11aed531ca7c4971ed43f2bdc767c6b1dffe69f0c78a37f38"


@pytest.fixture
def wired(selected: Any, monkeypatch: pytest.MonkeyPatch):
    database, selection = selected
    raw = BEFORE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == BEFORE_SHA
    assert candidate.__package__ is not None
    name = candidate.__package__ + "._quality_scope_before26"
    before = ModuleType(name)
    before.__file__, before.__package__ = str(BEFORE), candidate.__package__
    monkeypatch.setitem(sys.modules, name, before)
    exec(compile(raw, str(BEFORE), "exec"), before.__dict__)
    reference = GraphFinalProjectionRef(row=ObservedExecutionRef(
        output_id="controlled-final", role="research_graph_final",
        schema_version="research-graph-final-projection.v3", payload_hash="a" * 64,
        recorded_at=NOW,
    ))
    mapped = reference.row.model_copy(update={
        "output_id": "controlled-mapped", "role": "research_mapped",
        "schema_version": "research-mapped-observation.v1",
    })
    decision = assess_research_quality(quality_input(run_id="run-contract"))
    source_result = material()[1].model_copy(update={"run_id": "run-contract"})
    review = ResearchReview(
        review_id="controlled-review", role="independent_reviewer", target_claim="fixture",
        evidence_refs=["fact:rates"], result_hash=decision.source_result_hash, challenge="fixture",
        resolution="accepted", limitations=[], created_at=NOW,
    )
    runtime = observation_json({
        "prepared_budget": {"max_tool_calls": 10, "max_subagents": 0,
                            "max_evidence_rounds": 1, "max_estimated_cost_usd": 1.0},
        "deadline_at": (NOW + timedelta(minutes=10)).isoformat(),
    })
    sidecar = SimpleNamespace(
        phase="final", recorded_at=NOW, prepared_runtime_json=runtime,
        quality_decision=decision, unknown_receipt_codes=("fixture_no_authority",),
    )
    delivery = ResearchContextDelivery(
        schema_version="research-context-delivery.v1", run_id="run-contract", generation=1,
        root_session_id="controlled-session", selected_call_id=None,
        selected_candidate_hash=None, mapped_ref=mapped, transport_ref=mapped,
        provider_input_ref=None, provider_emission_ref=None, provider_finish_ref=None,
        selection_manifest_hash=None, items=(), required_refs=(),
        context_fidelity_verified=False, root_synthesis_input_verified=False,
        missing_codes=("fixture_no_native_body_authority",),
    )
    body = {
        "schema_version": candidate.PUBLICATION_SOURCE_SCHEMA_V4,
        "run_id": "run-contract", "event_id": "event-contract",
        "graph_final_ref": reference.model_dump(mode="json"),
        "quality_binding": {"quality_sidecar_id": "controlled-sidecar"},
        "gate": {"status": "research_only"},
    }
    encoded = observation_json(body)
    with database.session() as session:
        session.add_all([
            RunRoleOutputRecord(
                output_id="controlled-publication", run_id="run-contract",
                role=candidate.PUBLICATION_SOURCE_ROLE,
                schema_version=candidate.PUBLICATION_SOURCE_SCHEMA_V4,
                payload_json=encoded, payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
                created_at=NOW,
            ),
            RunRoleOutputRecord(
                output_id="controlled-review", run_id="run-contract", role="independent_reviewer",
                schema_version="fixture.v1", payload_json="{}",
                payload_hash=hashlib.sha256(b"{}").hexdigest(), created_at=NOW,
            ),
        ])

    def selection_reads(session: Session, count: int) -> None:
        for _ in range(count):
            assert recipe.read_graph_recipe_selection_v3_in_session(
                session, "run-contract"
            ) == selection

    def graph(session: Session, **_kwargs: Any):
        selection_reads(session, 2)
        return SimpleNamespace(
            result=source_result, receipt=SimpleNamespace(selected_synthesis_observation_ref=mapped)
        )

    def publication(session: Session, **_kwargs: Any) -> None:
        selection_reads(session, 2)

    def decoded_review(_row: Any) -> ResearchReview:
        return review

    def saved_sidecar(*_a: Any, **_k: Any):
        return sidecar

    def retained_fallback(*_a: Any, **_k: Any):
        return False, None

    for module in (before, candidate):
        monkeypatch.setattr(module, "read_graph_final_in_session", graph)
        monkeypatch.setattr(module, "_read_publication_source_in_session", publication)
        monkeypatch.setattr(module, "_decode_review_row", decoded_review)
        monkeypatch.setattr(module, "read_quality_sidecar_in_session", saved_sidecar)
    monkeypatch.setattr(history, "consume_retained_publication_quality_in_session",
                        retained_fallback)

    def context_body(session: Session, *, mapped_ref: ObservedExecutionRef):
        assert mapped_ref == mapped
        selection_reads(session, 2)
        return delivery

    monkeypatch.setattr(context, "read_context_delivery_in_session", context_body)
    yield SimpleNamespace(database=database, selection=selection, before=before,
                          delivery=delivery, context_body=context_body)


def begin(database: Any) -> Session:
    session = database.factory()
    session.connection().exec_driver_sql("BEGIN")
    return session


def test_quality_context_full_wire_and_first_full_fresh_exit(
    wired: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    actual_selection, actual_installation = (
        recipe._read_graph_recipe_selection_v3_original,  # pyright: ignore[reportPrivateUsage]
        recipe._installation_original,  # pyright: ignore[reportPrivateUsage]
    )
    counts = {"selection": 0, "installation": 0}

    def selection(*args: Any, **kwargs: Any):
        counts["selection"] += 1
        return actual_selection(*args, **kwargs)

    def installation():
        counts["installation"] += 1
        return actual_installation()

    monkeypatch.setattr(recipe, "_read_graph_recipe_selection_v3_original", selection)
    monkeypatch.setattr(recipe, "_installation_original", installation)
    results, receipts = [], []
    for module in (wired.before, candidate):
        counts.update(selection=0, installation=0)
        start = time.perf_counter()
        view = module.ResearchQualityQuery(wired.database).read("run-contract", as_of=NOW)
        monkeypatch.setitem(sys.modules, candidate.__name__, module)
        delivery = context.ResearchContextDeliveryQuery(wired.database).read("run-contract")
        receipts.append({"seconds": time.perf_counter() - start, "counts": dict(counts)})
        results.append((view.model_dump(mode="json"), delivery.model_dump(mode="json")))
        assert current_recipe_read_scope() is None
    assert results[0] == results[1]
    assert results[1][0]["status"] == "available"
    assert results[1][0]["decision"]["path_risk_status"] == "unknown"
    assert results[1][1]["financial_qualified"] is False
    assert receipts[0]["counts"] == {"selection": 12, "installation": 12}
    assert receipts[1]["counts"] == {"selection": 4, "installation": 4}
    (tmp_path / "quality-scope-first-profile.json").write_text(json.dumps({
        "before": receipts[0], "candidate": receipts[1], "full_wire_equal": True,
        "scope": "explicit native/publication/body seams; no actual25 latency claim",
    }), encoding="utf-8")


def test_raw_transaction_reset_release_denial_and_authorizer_fallback(wired: Any):
    for change in ("raw_reset", "rollback", "deny_release", "deny_savepoint"):
        session = begin(wired.database)
        native = cast(sqlite3.Connection, session.connection().connection.driver_connection)
        before_changes = native.total_changes
        states: list[bool] = []
        marker_actions: list[str | None] = []

        def authorizer(
            action: int, first: str | None, _second: str | None,
            _db: str | None, _trigger: str | None, *,
            _change: str = change, _markers: list[str | None] = marker_actions,
        ) -> int:
            if action == sqlite3.SQLITE_SAVEPOINT:
                _markers.append(first)
                if (_change == "deny_savepoint" and first == "BEGIN") or (
                    _change == "deny_release" and first == "RELEASE"
                ):
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        native.set_authorizer(authorizer)

        def consume(
            view: Any, _mapped: Any, *, _states: list[bool] = states,
            _change: str = change, _native: sqlite3.Connection = native,
            _session: Session = session,
        ):
            _states.append(current_recipe_read_scope() is not None)
            if _change == "raw_reset":
                _native.commit()
                _native.execute("BEGIN")
            elif _change == "rollback":
                _session.rollback()
                _session.connection().exec_driver_sql("BEGIN")
            return view.model_dump(mode="json")

        try:
            if change == "deny_savepoint":
                result = candidate.consume_published_research_originals_in_session(
                    session, "run-contract", as_of=NOW, consume=consume
                )
                assert result["status"] == "available" and states == [False]
                assert marker_actions == ["BEGIN"]
            else:
                with pytest.raises(ValueError, match="snapshot_changed|read_scope_changed"):
                    candidate.consume_published_research_originals_in_session(
                        session, "run-contract", as_of=NOW, consume=consume
                    )
                assert states == [True]
            assert current_recipe_read_scope() is None
            if change != "rollback":
                assert native.in_transaction and native.total_changes == before_changes
        finally:
            if change != "rollback":
                native.set_authorizer(None)
            session.rollback()
            session.close()


def test_same_mtime_source_exit_original_errors_and_ancestry(
    wired: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    original_read = recipe._read_resource  # pyright: ignore[reportPrivateUsage]
    raw = original_read(recipe.SOURCE_REF)
    local = tmp_path / "original-current-source.py"
    local.write_bytes(raw)
    original_stat = local.stat()
    def controlled_read(ref: str) -> bytes:
        return local.read_bytes() if ref == recipe.SOURCE_REF else original_read(ref)

    monkeypatch.setattr(recipe, "_read_resource", controlled_read)
    session = begin(wired.database)
    try:
        def consume(view: Any, _mapped: Any):
            scope = current_recipe_read_scope()
            assert scope is not None
            with generation_proof("run-contract", 2):
                assert recipe.read_graph_recipe_selection_v3_in_session(
                    session, "run-contract"
                ) == wired.selection
            assert len([key for key in scope.values if key[0] == "selection"]) == 2
            local.write_bytes(raw + b"\n ")
            os.utime(local, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            return view

        with pytest.raises(ValueError, match="effective_implementation_mismatch"):
            candidate.consume_published_research_originals_in_session(
                session, "run-contract", as_of=NOW, consume=consume
            )
        assert current_recipe_read_scope() is None
    finally:
        session.rollback()
        session.close()
        local.write_bytes(raw)
    with wired.database.session() as session:
        session.get(RunRoleOutputRecord, "controlled-publication").payload_hash = "0" * 64
    for module in (wired.before, candidate):
        with pytest.raises(ValueError, match="research_quality_publication_original_changed"):
            module.ResearchQualityQuery(wired.database).read("run-contract", as_of=NOW)
