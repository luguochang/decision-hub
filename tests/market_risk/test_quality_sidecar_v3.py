"""Isolated same-writer sidecars and V3 route boundaries; no live installation or model.

Prepared Native / selection boundaries are explicit controlled fixtures here.
Formal spot Factory, installed release guard and Host qualification remain separate gates.
"""

import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from packages.kernel.decision_hub_kernel.application import research_graph_binding as binding
from packages.kernel.decision_hub_kernel.application import (
    research_graph_projection_v3 as projection,
)
from packages.kernel.decision_hub_kernel.application.research_graph_recipe_v3 import (
    FrozenGraphRecipeSelectionV3,
    graph_recipe_manifest_v3,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_json,
    observation_sha,
)
from packages.kernel.decision_hub_kernel.application.research_quality_sidecar import (
    _row_ref,
    build_server_quality_context,
    persist_quality_sidecar_in_session,
    read_quality_sidecar_in_session,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRoleOutputRecord
from tests.market_risk.test_quality_projection_v3 import material
from tests.market_risk.test_recipe_selection_v3 import selection


@pytest.fixture
def original_fixture(tmp_path):
    request, source, state, _ = material()
    clock = source.finished_at
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'quality.sqlite3'}")
    database.create_all()
    value = selection(graph_recipe_manifest_v3())
    body = value.model_dump(mode="json")
    body["run_id"] = request.run_id
    unsigned = value.model_copy(update={"run_id": request.run_id})
    from packages.kernel.decision_hub_kernel.application.research_graph_recipe_v3 import (
        _effective_hash,
    )

    body["effective_hash"] = _effective_hash(unsigned)
    value = type(value).model_validate_json(observation_json(body))
    raw = observation_json(value.model_dump(mode="json"))
    rows = [
        RunRoleOutputRecord(
            output_id="graph_recipe_selection_" + observation_sha(value.model_dump(mode="json")),
            run_id=request.run_id,
            role="graph_recipe_selection",
            schema_version=value.schema_version,
            payload_json=raw,
            payload_hash=hashlib.sha256(raw.encode()).hexdigest(),
            created_at=clock,
        )
    ]
    for identifier, role in [
        ("method-fixture", "research_execution_method"),
        ("execution-fixture", "dsh_execution_request:1"),
    ]:
        raw = observation_json({"controlled_prepared_fixture": role})
        rows.append(
            RunRoleOutputRecord(
                output_id=identifier,
                run_id=request.run_id,
                role=role,
                schema_version="fixture.v1",
                payload_json=raw,
                payload_hash=hashlib.sha256(raw.encode()).hexdigest(),
                created_at=clock,
            )
        )
    with database.session() as session:
        session.add_all(rows)
    with database.session() as session:
        originals = list(
            session.scalars(
                select(RunRoleOutputRecord).where(RunRoleOutputRecord.run_id == request.run_id)
            )
        )
        refs = tuple(_row_ref(row) for row in originals)
    native = SimpleNamespace(generation=1, rows=refs)
    prepared = SimpleNamespace(
        method=SimpleNamespace(
            definition=SimpleNamespace(
                runtime_id="fixture", runtime_version="fixture.v1", profile_ref="fixture.profile"
            )
        ),
        execution=SimpleNamespace(request=request, request_hash="f" * 64),
    )
    frozen = FrozenGraphRecipeSelectionV3(
        next(ref for ref in refs if ref.role == "graph_recipe_selection"), value
    )
    yield database, request, source, state, clock, native, prepared, frozen
    database.engine.dispose()


def build(fixture, session):
    _, _, source, _, clock, native, prepared, frozen = fixture
    return build_server_quality_context(
        session,
        selection=frozen.receipt,
        source_result=source,
        native_context=native,
        preparation=prepared,
        assessed_at=clock,
    )


def persist(fixture, session, *, projected_hash=None):
    _, request, _, state, clock, _, _, frozen = fixture
    context, refs, runtime_json, unknowns = build(fixture, session)
    quality = projection.quality_for_state(state, quality_context=context)
    row = persist_quality_sidecar_in_session(
        session,
        selection=frozen.receipt,
        recipe_selection_ref=frozen.reference,
        logical_round=1,
        context=context,
        quality=quality,
        proof_rows=refs,
        prepared_runtime_json=runtime_json,
        unknown_receipt_codes=unknowns,
        recorded_at=clock,
        projected_result_hash=projected_hash,
    )
    assert row.run_id == request.run_id
    return row, quality


def test_server_builder_records_actual_prepared_identity_without_inventing_qualification(
    original_fixture,
):
    database, _, _, _, _, _, _, _ = original_fixture
    with database.session() as session:
        context, refs, runtime_json, unknowns = build(original_fixture, session)
        assert context.runtime_attestation_ref is None
        assert context.context_fidelity_verified is False
        assert context.path_risk_model_qualified is False
        assert context.effective_budget_ref.startswith("hub:row:execution-fixture@")
        assert (
            "fixture.profile" in runtime_json
            and "actual_usage_cost_receipt_unavailable" in unknowns
        )
        assert len(refs) == 3


def test_round_and_final_store_source_and_projected_hash_independently_and_reentry(
    original_fixture,
):
    database, _, _, _, _, _, _, frozen = original_fixture
    with database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row, quality = persist(original_fixture, session)
        repeat, _ = persist(original_fixture, session)
        assert row.sidecar_id == repeat.sidecar_id
        final, _ = persist(original_fixture, session, projected_hash="9" * 64)
        assert row.phase == "round" and final.phase == "final"
        assert final.source_result_hash == quality.source_result_hash
        assert final.projected_result_hash == "9" * 64
        saved = read_quality_sidecar_in_session(session, row.sidecar_id, selection=frozen.receipt)
        assert saved.quality_decision.publication_mode == "research_only"
        assert saved.quality_decision.full_report_json == quality.full_report_json


@pytest.mark.parametrize("damage", ["raw", "projection", "proof"])
def test_sidecar_and_original_proof_damage_is_rejected(original_fixture, damage):
    database, _, _, _, _, _, _, frozen = original_fixture
    with database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row, _ = persist(original_fixture, session)
        if damage == "raw":
            row.payload_json += " "
        elif damage == "projection":
            row.publication_mode = "complete"
        else:
            session.get(RunRoleOutputRecord, "execution-fixture").payload_json += " "
        session.flush()
        with pytest.raises(
            ValueError,
            match="sidecar_original_changed|sidecar_projection_changed|proof_original_changed",
        ):
            read_quality_sidecar_in_session(session, row.sidecar_id, selection=frozen.receipt)


def test_real_sqlite_borrowed_session_requires_physical_writer(original_fixture):
    database, *_ = original_fixture
    with database.session() as session:
        with pytest.raises(ValueError, match="writer_required"):
            persist(original_fixture, session)


def test_v3_round_projection_uses_server_context_and_legacy_continuation_is_not_called(
    original_fixture, monkeypatch
):
    database, request, source, state, clock, native, prepared, frozen = original_fixture
    mapped = SimpleNamespace(
        result=source,
        context=native,
        selection=SimpleNamespace(kind="structured_response"),
        mapping_input_generation=1,
    )
    monkeypatch.setattr(binding, "read_prepared_execution_in_session", lambda *_args: prepared)

    def prepared_only_context(session, **kwargs):
        # This fixture has preparation rows, no Native/mapped/provider rows.
        # Preserve its explicit unresolved projection scope rather than claim
        # that a graph-selection reference is an authenticated observation.
        assert kwargs.pop("mapped_ref") == frozen.reference
        return build_server_quality_context(session, **kwargs)

    monkeypatch.setattr(binding, "build_server_quality_context", prepared_only_context)
    with database.session() as session:
        projected = binding._project_observed_v3(
            session,
            state=binding.initial_research_state(request),
            base=request,
            actual=request,
            observation_ref=frozen.reference,
            mapped=mapped,
            evidence=source.evidence_candidates,
            facts=[],
            admitted=source.evidence_candidates,
            previous=None,
            active=True,
            selection=frozen,
            assessed_at=clock,
        )
        assert projected[5].publication_mode == "research_only"
        assert not projected[4].context_fidelity_verified
        assert projected[0]["quality_source_result"] == source.model_dump(mode="json")
        assert not projection.should_continue_after_round(projected[0])

    def forbidden(*_args):
        raise AssertionError("V1 continuation must not be used for V3")

    monkeypatch.setattr(binding, "should_continue_after_round", forbidden)
    receipt = binding.ResearchGraphRoundAdmissionV3.model_construct()
    assert not binding._continue_for_round(SimpleNamespace(receipt=receipt, state=projected[0]))


def test_v3_native_failure_is_retained_and_prevents_trusted_synthesis():
    from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError

    request, source, state, context = material()
    state.update(
        binding.project_failure(
            state,
            request,
            AgentExecutionError("provider_timeout", "controlled timeout", retryable=False),
            source.evidence_candidates,
            [],
        )
    )
    quality = projection.quality_for_state(state, quality_context=context)
    assert quality.report_status == "untrusted" and quality.status == "unresolved"
    assert "research_budget_timeout" in quality.reason_codes
    assert state["continuation_failure"]["provenance"]["error_code"] == "provider_timeout"
    assert quality.source_result_hash == observation_sha(source.model_dump(mode="json"))
