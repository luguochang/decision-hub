"""Explicit protocol declarations and one real required two-round offline chain.

No Provider or live database is used. Declaration controls grant no provenance.
The joint gate uses the real Factory, Runtime, Kernel, Method3 and Graph readers.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import event, select

from packages.contracts_py.decision_hub_contracts import DshSessionResult, DshSessionSubmit
from packages.contracts_py.decision_hub_contracts import binding_contract_base as shapes
from packages.contracts_py.decision_hub_contracts import (
    context_execution_contract_base as context_base,
)
from packages.contracts_py.decision_hub_contracts import context_selection_semantics as semantics
from packages.contracts_py.decision_hub_contracts import context_selection_v3_contract_base as base3
from packages.contracts_py.decision_hub_contracts import execution_contract_base as execution_base
from packages.contracts_py.decision_hub_contracts.generated.research_context_selection_v3 import (
    ResearchContextSelectionReceiptV3,
)
from packages.evals.analytical_assessment import assess_outlook_in_session
from packages.kernel.decision_hub_kernel.application import (
    research_context_selection as selection,
)
from packages.kernel.decision_hub_kernel.application import (
    research_context_selection_versions as versions,
)
from packages.kernel.decision_hub_kernel.application import research_graph_binding as binding
from packages.kernel.decision_hub_kernel.application import research_graph_projection as projection
from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_json,
)
from packages.kernel.decision_hub_kernel.application.research_review import ResearchReviewService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalOutlookRecord,
    FactVersionRecord,
    RawCaptureRecord,
    ResearchEvidenceRecord,
    ResearchFactRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
    utcnow,
)
from packages.orchestration.langgraph.graphs.agentic_research_graph import (
    build_agentic_research_graph,
)
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.orchestration.test_graph_observed_result_v2 import (
    OWNER,
    MaterialHost,
    _write_observation,  # pyright: ignore[reportPrivateUsage] # Save genuine offline receipts.
    continuation_inputs,
)
from tests.research.test_full_method_v2_bundle import forbid_external_connections
from tests.research.test_graph_recipe_selection_v2 import (
    _rewrite_graph_row,  # pyright: ignore[reportPrivateUsage] # Canonical saved corruption, never pass.
)
from tests.research.test_research_publication_consumer_v2 import (
    _sql,  # pyright: ignore[reportPrivateUsage] # Original read snapshots, no verdict seam.
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert continuation_inputs is not None and forbid_external_connections is not None
ROOT = Path(__file__).resolve().parents[2]
CASES: list[dict[str, Any]] = json.loads(
    (ROOT / "tests/fixtures/research_context_selection_graph2_cases.json").read_text("utf-8")
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_current_graph_prior_explicit_union_and_unchanged_time_rules(case: dict[str, Any]) -> None:
    before = json.dumps(case["value"], sort_keys=True)
    if case["expected_shape"] and case["expected_time_semantics"]:
        ResearchContextSelectionReceiptV3.model_validate(case["value"])
    else:
        with pytest.raises(ValidationError):
            ResearchContextSelectionReceiptV3.model_validate(case["value"])
    assert json.dumps(case["value"], sort_keys=True) == before


def test_original_private_contract_and_full_registry_keep_v1_strict_semantics() -> None:
    originals = versions._verified_resources()  # pyright: ignore[reportPrivateUsage]
    original = versions._load_modules(originals)  # pyright: ignore[reportPrivateUsage]
    v1 = CASES[0]["value"]
    # Inspect the exact retained implementation, never mint a new receipt.
    assert original._selector_identity() == versions._IDENTITY  # pyright: ignore[reportPrivateUsage]
    original.ResearchContextSelectionReceiptV3.model_validate(v1)
    with pytest.raises(ValidationError):
        original.ResearchContextSelectionReceiptV3.model_validate(CASES[1]["value"])
    # Assert private module isolation and the actual preserved schema registry.
    base = versions._MODULES[versions._CONTRACT_BASE]  # pyright: ignore[reportPrivateUsage]
    generated = versions._MODULES[versions._GENERATED]  # pyright: ignore[reportPrivateUsage]
    assert original.ResearchContextSelectionReceiptV3 is generated.ResearchContextSelectionReceiptV3
    documents, _registry = base.context_selection_v3_schema_registry()
    assert set(documents) == set(base.SCHEMA_FILES) and len(documents) == 8
    assert (
        "research-context-selection.v3"
        == documents[base.SCHEMA_FILES[0]]["$defs"]["research_context_selection_receipt_v3"][
            "properties"
        ]["schema_version"]["const"]
    )


def test_loaded_selector_copied_module_refuses_later_source_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the actual imported callable, then change one real copied input."""
    copied_root = tmp_path / "selector-root"
    for relative in selection._IDENTITY_PATHS:  # pyright: ignore[reportPrivateUsage]
        copied = copied_root / relative
        copied.parent.mkdir(parents=True, exist_ok=True)
        copied.write_bytes((ROOT / relative).read_bytes())
    source_path = (
        copied_root
        / "packages/kernel/decision_hub_kernel/application/research_context_selection.py"
    )
    module = ModuleType("_test_loaded_context_selector")
    module.__file__ = str(source_path)
    module.__package__ = selection.__package__
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(compile(source_path.read_bytes(), str(source_path), "exec"), module.__dict__)
    loaded_identity = module._selector_identity
    original = loaded_identity()
    assert original == selection._selector_identity()  # pyright: ignore[reportPrivateUsage]
    dependency = (
        copied_root / "packages/kernel/decision_hub_kernel/application/research_graph_recipe.py"
    )
    dependency.write_bytes(dependency.read_bytes() + b"\n# isolated source drift control\n")
    with pytest.raises(ValueError, match="^context_selection_loaded_source_changed$"):
        loaded_identity()
    assert original == selection._selector_identity()  # pyright: ignore[reportPrivateUsage]


def test_v3_memo_keeps_fresh_dtos_and_rejects_changed_schema_and_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = deepcopy(CASES[0]["value"])

    with shapes.shape_validation_scope() as stats:
        first = ResearchContextSelectionReceiptV3.model_validate(original)
        before = stats.hit_count
        second = ResearchContextSelectionReceiptV3.model_validate(deepcopy(original))
        assert first == second and first is not second and stats.hit_count > before
        validator = base3._validator(ResearchContextSelectionReceiptV3)  # pyright: ignore[reportPrivateUsage]
        documents, _ = base3.context_selection_v3_schema_registry()
        top = documents[base3.SCHEMA_FILES[0]]["$defs"][base3.TOP_DEFINITION]
        monkeypatch.setitem(top["properties"]["schema_version"], "const", "rejected-version")
        with pytest.raises(ValidationError, match="context_selection_contract_invalid"):
            ResearchContextSelectionReceiptV3.model_validate(original)
        assert validator.schema


def test_v3_memo_bypasses_changed_timestamp_callable_and_unknown_formatter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = deepcopy(CASES[0]["value"])

    def reject_timestamp(_value: object) -> bool:
        return False

    with shapes.shape_validation_scope() as stats:
        ResearchContextSelectionReceiptV3.model_validate(original)
        previous = stats.bypass_count
        with monkeypatch.context() as changed:
            changed.setattr(semantics, "timestamp_shape_valid", reject_timestamp)
            with pytest.raises(ValidationError, match="context_selection_contract_invalid"):
                ResearchContextSelectionReceiptV3.model_validate(original)
            assert stats.bypass_count > previous
        validator = base3._validator(ResearchContextSelectionReceiptV3)  # pyright: ignore[reportPrivateUsage]
        assert validator.format_checker is not None
        calls: list[object] = []

        def unknown(value: object) -> bool:
            calls.append(value)
            return False

        monkeypatch.setitem(validator.format_checker.checkers, "date-time", (unknown, ()))
        with pytest.raises(ValidationError, match="context_selection_contract_invalid"):
            ResearchContextSelectionReceiptV3.model_validate(original)
        assert calls


class TwoRoundCitationHost(MaterialHost):
    """Controlled directions over the original historical MCP inputs/windows."""

    def __init__(self, fixture: PreparedInputs):
        super().__init__(fixture)
        self.clock_observations: list[dict[str, Any]] = []

    async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
        response = await super().result(payload)
        events = json.loads(response.events_json)
        synthesis = (
            json.loads(response.final_response)
            if payload.generation == 1
            else events[-1]["payload"]["event"]["data"]["result"]
        )
        proposal_at = utcnow()
        original_windows = [
            {key: horizon[key] for key in ("horizon", "next_review_at", "expires_at")}
            for horizon in synthesis["horizons"]
        ]
        for horizon in synthesis["horizons"]:
            horizon["action"] = "long"
        capability = events[1]["payload"]["event"]["data"]["message"]["content"][0][
            "structuredContent"
        ]
        self.clock_observations.append(
            {
                "generation": payload.generation,
                "proposal_constructed_at": proposal_at.isoformat(),
                "original_proposal_windows": original_windows,
                "current_proposal_windows": [
                    {key: horizon[key] for key in ("horizon", "next_review_at", "expires_at")}
                    for horizon in synthesis["horizons"]
                ],
                "original_evidence_times": [
                    {
                        key: evidence[key]
                        for key in ("evidence_id", "published_at", "observed_at", "received_at")
                    }
                    for evidence in capability["evidence_candidates"]
                ],
            }
        )
        final_response = json.dumps(synthesis) if payload.generation == 1 else ""
        encoded = json.dumps(events)
        return response.model_copy(
            update={
                "events_json": encoded,
                "final_response": final_response,
                "result_hash": hashlib.sha256(
                    (final_response + "\0" + encoded).encode()
                ).hexdigest(),
            }
        )


def _ref(row: RunRoleOutputRecord) -> selection.ContextSelectionReference:
    recorded = as_utc(row.created_at)
    assert recorded is not None
    return selection.ContextSelectionReference(
        output_id=row.output_id,
        run_id=row.run_id,
        role=row.role,
        payload_hash=row.payload_hash,
        recorded_at=recorded,
    )


def _original_material_rows(fixture: PreparedInputs) -> dict[str, Any]:
    """Snapshot actual originals; this helper grants no source/method verdict."""
    with fixture.database.session() as session:
        return {
            model.__tablename__: {
                tuple(getattr(row, key.name) for key in model.__mapper__.primary_key): {
                    column.key: getattr(row, column.key) for column in model.__table__.columns
                }
                for row in session.scalars(select(model))
            }
            for model in (
                RunRoleOutputRecord,
                ResearchEvidenceRecord,
                ResearchFactRecord,
                RawCaptureRecord,
                FactVersionRecord,
            )
        }


@pytest.mark.asyncio
async def test_actual_required_selection3_method3_graph2_second_round_and_saved_v1_mix(
    continuation_inputs: PreparedInputs,
    tmp_path: Path,
) -> None:
    """Run this exact node before/after the patch; before must fail at round2.

    The genuine first round is saved for diagnosis on RED. The passing path
    authenticates the complete joint chain, then rejects a correctly encoded
    saved v1 prior under the unchanged real v2 Task and rolls it back.
    """
    fixture = continuation_inputs
    request = fixture.request
    phase_index = 0

    def _phase_call[T](phase: str, read: Callable[[], T]) -> T:
        nonlocal phase_index
        phase_index += 1
        started = time.perf_counter()

        def record(status: str) -> None:
            _write_observation(
                tmp_path,
                f"joint-phase-{phase_index:02d}-{phase}-{status}.json",
                {
                    "schema_version": "graph-joint-phase.v1",
                    "phase_index": phase_index,
                    "phase": phase,
                    "status": status,
                    "run_id": request.run_id,
                    "occurred_at": utcnow().isoformat(),
                    "elapsed_seconds": time.perf_counter() - started,
                    "provider_executed": False,
                    "call_only_not_joint_acceptance": True,
                },
            )

        record("started")
        returned = read()
        record("completed")
        return returned

    assert RunService(fixture.database).claim_for_execution(
        request.run_id,
        worker_id=OWNER,
        lease_seconds=900,
        strategy_version="research.v1",
    )
    with fixture.database.session() as session:
        run = session.get(RunRecord, request.run_id)
        assert run is not None
        claimed_at = utcnow()
        lease_expires_at = as_utc(run.lease_expires_at)
        assert lease_expires_at is not None
        assert run.lease_owner == OWNER and lease_expires_at > claimed_at
        original_lease = {
            "owner": run.lease_owner,
            "claimed_check_at": claimed_at.isoformat(),
            "expires_at": lease_expires_at.isoformat(),
        }
    host = TwoRoundCitationHost(fixture)
    # The genuine Factory may already have constructed these pure registries.
    # Clear only canonical schema/validator construction caches to expose the
    # Runtime's initialization boundary; all persisted proofs stay untouched.
    for registry in (
        shapes.binding_schema_registry,
        execution_base.execution_schema_registry,
        context_base.context_execution_registry,
        base3.context_selection_v3_schema_registry,
    ):
        registry.cache_clear()
    shapes._validator.cache_clear()  # pyright: ignore[reportPrivateUsage] # Pure construction only.
    execution_base._validator.cache_clear()  # pyright: ignore[reportPrivateUsage]
    context_base._validator.cache_clear()  # pyright: ignore[reportPrivateUsage]
    base3._validator.cache_clear()  # pyright: ignore[reportPrivateUsage]
    constructor_started = time.perf_counter()
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(fixture.database),
        host,
        poll_interval_seconds=0.001,
    )
    constructor_seconds = time.perf_counter() - constructor_started
    graph = build_agentic_research_graph(
        runtime,
        ResearchEvidenceService(fixture.database),
        expected_lease_owner=OWNER,
        require_context_selection=True,
    )
    try:
        try:
            state = await graph.ainvoke(projection.initial_research_state(request))
        except ValueError as error:
            with fixture.database.session() as session:
                rows = list(
                    session.scalars(
                        select(RunRoleOutputRecord).where(
                            RunRoleOutputRecord.run_id == request.run_id,
                        )
                    )
                )
                _write_observation(
                    tmp_path,
                    "required-joint-before-failure.json",
                    {
                        "boundary": "real_factory_runtime_required_selection3_method3_graph2",
                        "provider_executed": False,
                        "cold_registry_boundary": (
                            "four registries and validators cleared before Runtime ctor"
                        ),
                        "runtime_constructor_seconds": constructor_seconds,
                        "task_pit_cutoff_at": request.pit_cutoff_at.isoformat(),
                        "task_deadline_at": request.deadline_at.isoformat(),
                        "original_claim": original_lease,
                        "controlled_host_clock_observations": host.clock_observations,
                        "error": str(error),
                        "error_type": type(error).__name__,
                        "host_generations": [call.generation for call in host.submit_calls],
                        "original_roles": [
                            {
                                "role": row.role,
                                "schema": row.schema_version,
                                "payload_hash": row.payload_hash,
                                "body": json.loads(row.payload_json),
                            }
                            for row in rows
                        ],
                    },
                )
            raise
        final_ref = binding.parse_graph_final_ref(state["graph_final_projection_ref"])
        with fixture.database.session() as session, shapes.shape_validation_scope():
            session.connection().exec_driver_sql("BEGIN")
            final = _phase_call(
                "Final",
                lambda: binding.read_graph_final_in_session(
                    session,
                    run_id=request.run_id,
                    reference=final_ref,
                    expected_lease_owner=OWNER,
                    require_current_permission=True,
                ),
            )
            assert isinstance(final.receipt, binding.ResearchGraphFinalProjectionV2)
            last = _phase_call(
                "Round2",
                lambda: binding.read_graph_round_in_session(
                    session,
                    run_id=request.run_id,
                    reference=final.receipt.last_admission_ref,
                    expected_lease_owner=OWNER,
                    require_current_permission=True,
                ),
            )
            assert last.receipt.previous_ref is not None
            first = _phase_call(
                "Round1",
                lambda reference=last.receipt.previous_ref: binding.read_graph_round_in_session(
                    session,
                    run_id=request.run_id,
                    reference=reference,
                    expected_lease_owner=OWNER,
                    require_current_permission=True,
                ),
            )
            assert isinstance(first.receipt, binding.ResearchGraphRoundAdmissionV2)
            assert isinstance(last.receipt, binding.ResearchGraphRoundAdmissionV2)
            assert first.state["loop_should_continue"] is True
            rows = list(
                session.scalars(
                    select(RunRoleOutputRecord)
                    .where(
                        RunRoleOutputRecord.run_id == request.run_id,
                        RunRoleOutputRecord.role.in_(
                            ("context_select.r1.a1", "context_select.r2.a1")
                        ),
                    )
                    .order_by(RunRoleOutputRecord.role)
                )
            )
            assert len(rows) == 2
            selected = [
                _phase_call(
                    "Selection1" if row.role == "context_select.r1.a1" else "Selection2",
                    lambda row=row: selection.ContextSelectionService(
                        fixture.database
                    ).read_in_session(
                        session,
                        run_id=request.run_id,
                        reference=_ref(row),
                        require_current_permission=True,
                        expected_lease_owner=OWNER,
                    ),
                )
                for row in rows
            ]
            assert [item.receipt.graph_round for item in selected] == [1, 2]
            assert selected[0].receipt.decision_basis_ref is None
            prior = selected[1].receipt.decision_basis_ref
            assert prior is not None
            assert prior.row.schema_version == "research-graph-round-admission.v2"
            assert prior.row.output_id == first.reference.row.output_id
            assert prior.row.payload_hash == first.reference.row.payload_hash
            assert any(
                item.source_kind == "same_run_evidence"
                for item in selected[1].receipt.considered_inputs
            )
            prepared = _phase_call(
                "Prepared2",
                lambda: read_prepared_execution_in_session(session, request.run_id),
            )
            assert prepared.method.schema_version == "research-execution-method.v3"
            assert prepared.execution.request.current_round == 2
            original = {
                row.role: (row.output_id, row.payload_json, row.payload_hash) for row in rows
            }
            first_bytes = first.receipt.model_dump_json()
        assert [call.generation for call in host.submit_calls] == [1, 2]
        with fixture.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = session.get(RunRoleOutputRecord, first.reference.row.output_id)
            selection_row = session.get(RunRoleOutputRecord, selected[1].reference.output_id)
            assert row is not None and selection_row is not None
            body = json.loads(row.payload_json)
            for field in ("recipe_selection_ref", "citation_check", "citation_disposition"):
                body.pop(field)
            body["schema_version"] = binding.ROUND_SCHEMA
            body["recipe_version"] = projection.RECIPE_VERSION
            body["recipe_hash"] = hashlib.sha256(Path(projection.__file__).read_bytes()).hexdigest()
            old_typed = binding.ResearchGraphRoundAdmission.model_validate_json(
                observation_json(body),
            )
            old_ref = _rewrite_graph_row(
                session,
                row,
                old_typed.model_dump(mode="json"),
                prefix="research_graph_round",
            )
            altered = selected[1].receipt.model_dump(mode="json")
            altered["decision_basis_ref"]["row"] = old_ref.model_dump(mode="json")
            # Canonical encoding makes this a real saved mixed-version negative.
            encode = selection._encode  # pyright: ignore[reportPrivateUsage]
            _typed, selection_row.payload_json = encode(altered)
            selection_row.payload_hash = hashlib.sha256(
                selection_row.payload_json.encode()
            ).hexdigest()
            session.flush()
            with pytest.raises(
                ValueError, match="^context_selection_graph_prior_version_mismatch$"
            ):
                selection.ContextSelectionService(fixture.database).read_in_session(
                    session,
                    run_id=request.run_id,
                    reference=_ref(selection_row),
                    require_current_permission=False,
                )
            session.rollback()
        with fixture.database.session() as session:
            for role, expected in original.items():
                row = session.scalar(
                    select(RunRoleOutputRecord).where(
                        RunRoleOutputRecord.run_id == request.run_id,
                        RunRoleOutputRecord.role == role,
                    )
                )
                assert (
                    row is not None
                    and (row.output_id, row.payload_json, row.payload_hash) == expected
                )
            row = session.get(RunRoleOutputRecord, first.reference.row.output_id)
            assert row is not None
            assert (
                binding.ResearchGraphRoundAdmissionV2.model_validate_json(
                    row.payload_json,
                ).model_dump_json()
                == first_bytes
            )
        # Publish this same actual two-round final after the mixed-v1 rollback.
        # The original accepted tools, selections and Runtime generations are
        # re-read by actual Review, Commit and assessor implementations.
        originals_before_publication = _original_material_rows(fixture)
        review = _phase_call(
            "Review",
            lambda: ResearchReviewService(fixture.database).review(
                request.run_id,
                final.result,
                graph_final_ref=final_ref,
                expected_lease_owner=OWNER,
            ),
        )
        reviewed = final.result.model_copy(update={"reviews": [review]})
        artifact_id, _publication = _phase_call(
            "Commit",
            lambda: CommitDecisionService(fixture.database).commit_research_result(
                request.run_id,
                request.event_id,
                reviewed,
                graph_final_ref=final_ref,
                expected_lease_owner=OWNER,
            ),
        )
        originals_after_publication = _original_material_rows(fixture)
        for table, rows_before in originals_before_publication.items():
            for primary_key, row_before in rows_before.items():
                assert originals_after_publication[table][primary_key] == row_before
        before_assessment = _sql(fixture)
        with fixture.database.session() as session:
            connection = session.connection()
            connection.exec_driver_sql("BEGIN")
            assessment_statements: list[str] = []

            def reject_assessment_write(
                _connection: Any,
                _cursor: Any,
                statement: str,
                _parameters: Any,
                _context: Any,
                _executemany: bool,
            ) -> None:
                if not statement.lstrip().upper().startswith("SELECT "):
                    raise AssertionError("assessment_sql_must_be_select_only")
                assessment_statements.append(statement)

            event.listen(connection, "before_cursor_execute", reject_assessment_write)
            try:
                outlooks = session.scalars(
                    select(AnalyticalOutlookRecord).where(
                        AnalyticalOutlookRecord.run_id == request.run_id,
                        AnalyticalOutlookRecord.method_family == "ai_synthesis",
                    )
                ).all()
                assert {row.horizon for row in outlooks} == {"30m", "24h", "72h"}
                selected_outlook = next(row for row in outlooks if row.horizon == "30m")
                with shapes.shape_validation_scope():
                    assessment = _phase_call(
                        "Assessment",
                        lambda: assess_outlook_in_session(
                            session,
                            selected_outlook.outlook_id,
                            assessed_at=utcnow(),
                        ),
                    )
                assert assessment.source_status == assessment.method_status == "verified"
                assert (
                    assessment.source_reason is None
                    and len(assessment.stable_recipe_hash or "") == 64
                )
                assert assessment.method_scope == "hub_recipe_provider_unattested"
                assert not assessment.directional_usable
                assert not session.new and not session.dirty and not session.deleted
                assert assessment_statements
            finally:
                event.remove(connection, "before_cursor_execute", reject_assessment_write)
        assert _sql(fixture) == before_assessment
        assert _original_material_rows(fixture) == originals_after_publication
        _write_observation(
            tmp_path,
            "required-joint-original.json",
            {
                "boundary": "real_factory_runtime_required_selection3_method3_graph2",
                "provider_executed": False,
                "cold_registry_boundary": (
                    "four registries and validators cleared before Runtime ctor"
                ),
                "runtime_constructor_seconds": constructor_seconds,
                "task_pit_cutoff_at": request.pit_cutoff_at.isoformat(),
                "task_deadline_at": request.deadline_at.isoformat(),
                "original_claim": original_lease,
                "controlled_host_clock_observations": host.clock_observations,
                "runtime_prepare_confirmed_generation": prepared.execution.generation,
                "method_schema_version": prepared.method.schema_version,
                "published_artifact_id": artifact_id,
                "assessment": assessment.model_dump(mode="json"),
                "original_raw_fact_evidence_roles_unchanged": True,
                "assessment_performed_no_sql_write": True,
                "assessment_select_only_statement_count": len(assessment_statements),
                "host_generations": [1, 2],
                "selected_originals": [item.receipt.model_dump(mode="json") for item in selected],
                "first_round": first.receipt.model_dump(mode="json"),
                "second_round": last.receipt.model_dump(mode="json"),
                "final": final.receipt.model_dump(mode="json"),
                "saved_v1_under_v2_task_rejected": True,
                "originals_restored_by_rollback": True,
            },
        )
    finally:
        await runtime.close()
