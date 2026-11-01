"""Finite recipe original-boundary tests. No DSH/financial qualification asserted."""

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from apps.hub_worker import market_component_research_protocol as protocol_module
from apps.hub_worker.market_component_research_protocol import (
    RECIPE_PROTOCOL_HASH,
    RecipeOriginalProtocol,
    _quality_metrics,
)
from apps.hub_worker.spot_research_bootstrap import POLICY_REFS
from packages.kernel.decision_hub_kernel.application.market_component_research_functions import (
    RECIPE_COMPONENT_INPUT,
    RECIPE_COMPONENT_OUTPUT,
    RECIPE_COMPONENT_REF,
    SpotResearchRecipeConsumptionInput,
)
from packages.kernel.decision_hub_kernel.application.research_binding import (
    FrozenResearchBundle,
    load_prepared_pack_definition,
)
from packages.kernel.decision_hub_kernel.application.research_graph_recipe_v3 import (
    FrozenGraphRecipeSelectionV3,
)
from packages.kernel.decision_hub_kernel.application.research_quality_view import (
    ResearchQualityViewV1,
)
from packages.kernel.decision_hub_kernel.decision.research_quality import assess_research_quality
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRecord,
    RunRoleOutputRecord,
)
from packages.kernel.decision_hub_kernel.persistence.research_quality_records import (
    ResearchQualitySidecarRecord,
)
from packages.kernel.decision_hub_kernel.ports.component_protocol import (
    ComponentOriginalRef,
    ComponentProtocolContext,
)
from packages.kernel.decision_hub_kernel.ports.component_registry import (
    ComponentManifest,
    ShadowTrial,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedExecutionRef
from tests.dsh_native.test_dsh_web_runtime import _request
from tests.market_risk.test_market_data import NOW
from tests.market_risk.test_research_quality import quality_input
from tests.market_risk.test_spot_scope_admission import prepared

assert prepared is not None
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def fixture(tmp_path, prepared):
    _, _, _, _, runtime = prepared
    _, effective, _ = runtime("BTC-USDT")
    pack = ROOT / "packs/crypto_macro_prepared/1.2.0"
    raw_manifest = yaml.safe_load((pack / "pack.yaml").read_text(encoding="utf-8"))
    definition = load_prepared_pack_definition(
        pack,
        POLICY_REFS,
        allowed_capabilities=tuple(raw_manifest["capability_refs"]),
        execution_mode="live",
    )
    inputs = SpotResearchRecipeConsumptionInput(
        run_id="candidate",
        event_id="event",
        scope_id="crypto:spot:okx:BTC-USDT:trade:1m",
        admission_hash="b" * 64,
        effective_binding_hash=canonical_hash(effective.model_dump(mode="json")),
        effective=effective,
        definition=definition,
        definition_hash=canonical_hash(definition.model_dump(mode="json")),
    )
    body = inputs.model_dump_json().encode()
    bodies = {"controlled-recipe-input": body}
    context = ComponentProtocolContext(raw_archive=SimpleNamespace(read=bodies.__getitem__))
    manifest = ComponentManifest(
        version_id="recipe-version",
        kind="research_recipe",
        component_ref=RECIPE_COMPONENT_REF,
        scope_id=inputs.scope_id,
        input_contract=RECIPE_COMPONENT_INPUT,
        output_contract=RECIPE_COMPONENT_OUTPUT,
        code_hash="c" * 64,
        resource_hash="d" * 64,
        parameters={},
    )
    trial = ShadowTrial(
        trial_id="shadow",
        version_id=manifest.version_id,
        manifest_hash=manifest.payload_hash,
        protocol_hash=RECIPE_PROTOCOL_HASH,
        baseline_version_id=None,
        registered_at=NOW - timedelta(hours=1),
        start_at=NOW,
        end_at=NOW + timedelta(days=90),
    )
    capture = {
        "intent": {
            "input_ref": "controlled-recipe-input",
            "input_hash": hashlib.sha256(body).hexdigest(),
            "cutoff_at": NOW.isoformat(),
        },
        "known_at": NOW.isoformat(),
        "input_hash": canonical_hash(inputs.model_dump(mode="json")),
        "output": definition.model_dump(mode="json"),
        "output_hash": canonical_hash(definition.model_dump(mode="json")),
    }
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'recipe-protocol.sqlite3'}")
    db.create_all()
    with db.session() as session:
        session.add(
            RunRecord(
                run_id="candidate",
                event_id="event",
                status="running",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        # Classification marker only. It never grants actual spot admission,
        # publication or quality; actual native reader reports pending.
        session.add(
            RunRoleOutputRecord(
                output_id="selection-marker",
                run_id="candidate",
                role="graph_recipe_selection",
                schema_version="research-graph-recipe-selection.v3",
                payload_json="{}",
                payload_hash=hashlib.sha256(b"{}").hexdigest(),
                created_at=NOW,
            )
        )
    yield db, context, manifest, trial, capture, bodies
    db.engine.dispose()


def evaluate(fixture, *, captures=None, trial=None):
    db, context, manifest, original_trial, capture, _ = fixture
    with db.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        native = session.connection().connection.driver_connection
        before = native.total_changes
        result = RecipeOriginalProtocol().evaluate(
            session,
            manifest=manifest,
            trial=trial or original_trial,
            captures=(capture,) if captures is None else captures,
            original_refs=(),
            as_of=NOW + timedelta(days=1),
            context=context,
        )
        assert native.total_changes == before and not session.new and not session.dirty
        return result


def test_actual_pack_config_capture_and_real_pending_publication_reader_stay_insufficient(fixture):
    result = evaluate(fixture)
    assert result.status == "insufficient"
    assert "recipe_publication_original_unavailable" in result.reasons
    assert result.metrics["published_original_count"] == 0
    assert result.sample_qualified is False and result.cost_verified is False
    assert result.metrics["predictive_gain"] is None


def test_no_shadow_samples_and_wrong_protocol_cannot_qualify(fixture):
    result = evaluate(fixture, captures=())
    assert (
        result.status == "insufficient"
        and "recipe_shadow_execution_originals_unavailable" in result.reasons
    )
    with pytest.raises(ValueError, match="protocol_identity_mismatch"):
        evaluate(fixture, trial=fixture[3].model_copy(update={"protocol_hash": "0" * 64}))


def test_changed_raw_callback_and_real_publication_original_reject(fixture):
    _, _, _, _, capture, bodies = fixture
    body = bodies["controlled-recipe-input"]
    bodies["controlled-recipe-input"] += b" "
    with pytest.raises(ValueError, match="capture_raw_changed"):
        evaluate(fixture)
    bodies["controlled-recipe-input"] = body
    capture["output_hash"] = "0" * 64
    with pytest.raises(ValueError, match="capture_identity_changed"):
        evaluate(fixture)
    capture["output_hash"] = canonical_hash(capture["output"])
    with fixture[0].session() as session:
        session.add(
            RunRoleOutputRecord(
                output_id="bad-publication",
                run_id="candidate",
                role="research_publication_source",
                schema_version="research-publication-source.v4",
                payload_json="{}",
                payload_hash="a" * 64,
                created_at=NOW,
            )
        )
    with pytest.raises(ValueError, match="publication_original_changed"):
        evaluate(fixture)


def test_quality_conformance_uses_full_sealed_report_and_preserves_counter_and_unknowns():
    # Positive conformance of the already-authenticated typed decision seam.
    # It is not a fabricated full native publication or financial qualification.
    decision = assess_research_quality(quality_input())
    view = ResearchQualityViewV1.model_construct(status="available", decision=decision)
    metrics = _quality_metrics(view, {"fact:rates", "fact:liquidity"})
    assert metrics["full_report_hash"] == decision.full_report_hash
    assert metrics["protected_missing_refs"] == []
    assert metrics["report_status"] == "complete"
    assert metrics["path_risk_status"] == "unknown"
    missing = _quality_metrics(view, {"numeric-missing"})
    assert missing["protected_missing_refs"] == ["numeric-missing"]


def test_authenticated_original_projection_seam_keeps_prepared_budget_and_usage_separate(
    fixture, monkeypatch
):
    """After-authentication seam only; full native proof is deliberately not forged.

    Real original reader rejection is covered above. This node exercises the
    downstream available-publication calculation with explicit trusted-port
    seams and still cannot qualify a component or assert real AI performance.
    """
    db, context, manifest, trial, capture, bodies = fixture
    inputs = SpotResearchRecipeConsumptionInput.model_validate_json(
        bodies["controlled-recipe-input"]
    )
    decision = assess_research_quality(quality_input(run_id="candidate"))
    raw = json.dumps(
        {
            "schema_version": "research-publication-source.v4",
            "run_id": "candidate",
            "event_id": "event",
        }
    )
    digest = hashlib.sha256(raw.encode()).hexdigest()
    with db.session() as session:
        session.add(
            RunRoleOutputRecord(
                output_id="pinned-publication",
                run_id="candidate",
                role="research_publication_source",
                schema_version="research-publication-source.v4",
                payload_json=raw,
                payload_hash=digest,
                created_at=NOW,
            )
        )
        session.add(
            ResearchQualitySidecarRecord(
                sidecar_id="controlled-sidecar",
                run_id="candidate",
                logical_round=1,
                phase="final",
                source_result_hash=decision.source_result_hash,
                projected_result_hash="a" * 64,
                quality_context_hash="b" * 64,
                quality_decision_hash=decision.decision_hash,
                status=decision.status,
                publication_mode=decision.publication_mode,
                payload_json="{}",
                payload_hash=hashlib.sha256(b"{}").hexdigest(),
                created_at=NOW,
            )
        )
    reference = ObservedExecutionRef(
        output_id="pinned-publication",
        role="research_publication_source",
        schema_version="research-publication-source.v4",
        payload_hash=digest,
        recorded_at=NOW,
    )
    view = ResearchQualityViewV1.model_construct(
        run_id="candidate",
        status="available",
        decision=decision,
        publication_ref=reference,
        quality_sidecar_id="controlled-sidecar",
    )
    request = _request("candidate").model_copy(update={"pit_cutoff_at": NOW})
    bundle = FrozenResearchBundle(
        pack=SimpleNamespace(definition_hash=inputs.definition_hash, definition=inputs.definition),
        task=SimpleNamespace(request=request),
        compiled=SimpleNamespace(),
        recorded_at=NOW,
    )
    selection = FrozenGraphRecipeSelectionV3(
        reference=reference,
        receipt=SimpleNamespace(
            quality_context=SimpleNamespace(
                additional_required_context_refs=("fact:rates", "fact:liquidity")
            )
        ),
    )
    sidecar = SimpleNamespace(
        recorded_at=NOW,
        prepared_runtime_json=json.dumps({"prepared_budget": {"max_estimated_cost_usd": 100}}),
        quality_context=SimpleNamespace(context_fidelity_verified=False),
    )
    frozen_scope = SimpleNamespace(
        scope=SimpleNamespace(scope_id=inputs.scope_id),
        event_id=inputs.event_id,
        admission_proof_hash=inputs.admission_hash,
        effective_binding_hash=inputs.effective_binding_hash,
        effective=inputs.effective,
        admitted_at=NOW,
    )
    monkeypatch.setattr(
        protocol_module, "read_published_research_quality_in_session", lambda *_args, **_kw: view
    )
    monkeypatch.setattr(
        protocol_module, "read_research_bundle_in_session", lambda *_args, **_kw: bundle
    )
    monkeypatch.setattr(
        protocol_module,
        "read_graph_recipe_selection_for_run_in_session",
        lambda *_args, **_kw: selection,
    )
    monkeypatch.setattr(
        protocol_module, "read_quality_sidecar_in_session", lambda *_args, **_kw: sidecar
    )
    monkeypatch.setattr(
        protocol_module, "read_run_spot_admission_in_session", lambda *_args, **_kw: frozen_scope
    )
    with db.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        args = dict(
            manifest=manifest,
            trial=trial,
            captures=(capture,),
            as_of=NOW + timedelta(days=1),
            context=context,
        )
        with pytest.raises(ValueError, match="publication_not_pinned"):
            RecipeOriginalProtocol().evaluate(session, original_refs=(), **args)
        before = session.connection().connection.driver_connection.total_changes
        result = RecipeOriginalProtocol().evaluate(
            session,
            original_refs=(
                ComponentOriginalRef(original_ref=reference.output_id, original_hash=digest),
            ),
            **args,
        )
        assert (
            result.status == "insufficient"
            and not result.cost_verified
            and not result.sample_qualified
        )
        assert result.metrics["published_original_count"] == 1
        metrics = result.metrics["runs"]["candidate"]
        assert metrics["actual_cost_usd"] is None and metrics["actual_total_tokens"] is None
        assert metrics["prepared_budget"]["max_estimated_cost_usd"] == 100
        assert metrics["protected_missing_refs"] == []
        assert "recipe_actual_context_fidelity_unavailable" in result.reasons
        assert session.connection().connection.driver_connection.total_changes == before
