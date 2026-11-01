"""Actual two consumer seams on SQLite; qualification originals are synthetic.

No fixture return is real predictive qualification or a formal installed release.
"""

import asyncio
import hashlib
import inspect
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select

from apps.hub_worker.market_components import InstalledComponent, MarketComponents
from apps.hub_worker.research import ResearchRequestFactory
from packages.kernel.decision_hub_kernel.application.component_registry import ComponentRegistry
from packages.kernel.decision_hub_kernel.application.market_component_research_functions import (
    PATH_COMPONENT_INPUT,
    PATH_COMPONENT_OUTPUT,
    RECIPE_COMPONENT_INPUT,
    RECIPE_COMPONENT_OUTPUT,
    RECIPE_COMPONENT_REF,
    predict_registered_path_forecast,
    select_registered_spot_research_recipe,
)
from packages.kernel.decision_hub_kernel.application.research_binding import (
    PreparedResearchPolicyRefs,
)
from packages.kernel.decision_hub_kernel.application.risk_path_ledger import checked_payload
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
from packages.kernel.decision_hub_kernel.persistence.market_case_records import (
    MarketComponentAuditRecord,
    MarketComponentPointerRecord,
)
from packages.kernel.decision_hub_kernel.persistence.path_forecast_records import (
    RiskPathJobRecord,
    RiskPredictionRecord,
)
from packages.kernel.decision_hub_kernel.ports.component_registry import ComponentQualification
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash, spot_scope
from tests.market_risk.test_path_shadow_ledger import FIRST, first_predictions, register, runtime
from tests.market_risk.test_spot_research_v3_integration import (
    ROOT,
    installation_fixture,
    manifest,
    prepared,
    spot_inputs,
)

assert all(
    item is not None for item in (runtime, spot_inputs, prepared, installation_fixture, manifest)
)


class EngineeringOriginals:
    """Real local bytes prove this fixture protocol, no fabricated market samples."""

    def __init__(self, directory, known_at):
        self.directory, self.known_at = directory, known_at

    def verify(self, manifest, trial):
        path = self.directory / (trial.trial_id + ".json")
        raw = path.read_bytes()
        data = json.loads(raw)
        assert data == {
            "manifest_hash": manifest.payload_hash,
            "protocol_hash": trial.protocol_hash,
            "synthetic_engineering_only": True,
        }
        return ComponentQualification(
            trial_id=trial.trial_id,
            manifest_hash=manifest.payload_hash,
            protocol_hash=trial.protocol_hash,
            original_refs=("fixture://" + path.name,),
            original_hashes=(hashlib.sha256(raw).hexdigest(),),
            known_at=self.known_at,
            status="passed",
            reasons=(),
            sample_qualified=True,
            cost_verified=True,
            independent_blocks=0,
        )


def selected_consumer(db, now, tmp_path, installed):
    start = now[0]
    reader = EngineeringOriginals(tmp_path, start + timedelta(seconds=1))
    registry = ComponentRegistry(db, original_reader=reader, clock=lambda: now[0])
    for component in installed:
        manifest = component.manifest()
        registry.register(manifest)
        trial = registry.shadow(
            "trial-" + manifest.version_id,
            version_id=manifest.version_id,
            protocol_hash="1" * 64,
            baseline_version_id=None,
            start_at=start,
            end_at=start + timedelta(seconds=1),
        )
        (tmp_path / (trial.trial_id + ".json")).write_text(
            json.dumps(
                {
                    "manifest_hash": manifest.payload_hash,
                    "protocol_hash": trial.protocol_hash,
                    "synthetic_engineering_only": True,
                }
            ),
            encoding="utf-8",
        )
    now[0] = start + timedelta(seconds=2)
    for component in installed:
        review = registry.review(
            "review-" + component.version_id,
            trial_id="trial-" + component.version_id,
            reviewer_ref="controlled-engineering-reader",
        )
        registry.activate(
            "activate-" + component.version_id, review_id=review.review_id, expected_generation=0
        )
    return MarketComponents(registry, tuple(installed))


def installed_model(method, scope):
    return InstalledComponent(
        kind="model",
        component_ref=method,
        scope_id=scope.scope_id,
        version_id=scope.instrument + "." + method + ".engineering1",
        input_contract=PATH_COMPONENT_INPUT,
        output_contract=PATH_COMPONENT_OUTPUT,
        callback=predict_registered_path_forecast,
        code_files={"function": Path(inspect.getfile(predict_registered_path_forecast))},
    )


@pytest.mark.parametrize("selected", [False, True])
def test_path_pair_actual_consumer_and_samewriter_provenance(runtime, tmp_path, selected):
    db, _, _, now, ledger, _ = runtime
    items = tuple(
        installed_model(method, scope)
        for scope in (spot_scope("BTC-USDT"), spot_scope("ETH-USDT"))
        for method in ("BASE_EMPIRICAL_V1", "COND_VOL_V1")
    )
    ledger.component_consumer = (
        selected_consumer(db, now, tmp_path, items)
        if selected
        else MarketComponents(ComponentRegistry(db, clock=lambda: now[0]), ())
    )
    first_predictions(runtime)
    with db.session() as session:
        rows = session.scalars(
            select(MarketComponentAuditRecord).where(
                MarketComponentAuditRecord.audit_id.like("cconsume_%")
            )
        ).all()
        assert len(rows) == 4
        receipts = [json.loads(row.payload_json) for row in rows]
        assert {item["state"] for item in receipts} == (
            {"selected"} if selected else {"baseline_not_registry_approved"}
        )
        assert all(item["probability_qualified"] is False for item in receipts)
        for job in session.scalars(
            select(RiskPathJobRecord).where(
                RiskPathJobRecord.kind == "forecast", RiskPathJobRecord.state == "succeeded"
            )
        ):
            result = checked_payload(job.result_json, job.result_hash)
            assert len(result["component_consumption_refs"]) == 2
            assert len(result["prediction_refs"]) == 2


def test_bad_second_model_pointer_rolls_back_first_prediction_and_audit(runtime):
    db, data, _, now, ledger, jobs = runtime
    register(ledger)
    assert jobs.process_due(limit=2) == 2
    now[0] = FIRST - timedelta(seconds=1)
    assert asyncio.run(data.refresh_due()) == 2
    now[0] = FIRST + timedelta(seconds=10)
    claim = jobs.claim()
    assert claim is not None and claim.kind == "forecast"
    with db.session() as session:
        session.add(
            MarketComponentPointerRecord(
                pointer_id=canonical_hash(["model", "COND_VOL_V1", claim.scope_id]),
                kind="model",
                component_ref="COND_VOL_V1",
                scope_id=claim.scope_id,
                version_id="not-installed",
                generation=1,
                changed_at=FIRST - timedelta(seconds=1),
                audit_id="missing-audit",
            )
        )
    ledger.component_consumer = MarketComponents(ComponentRegistry(db, clock=lambda: now[0]), ())
    model = ledger.fitted_model(claim.trial_id, claim.scope_id)
    features = ledger.inputs.feature_manifest(model.scope, claim.cutoff_at)
    with pytest.raises(ValueError):
        ledger.publish_pair(claim, model, features)
    with db.session() as session:
        assert session.query(RiskPredictionRecord).count() == 0
        assert session.query(MarketComponentAuditRecord).count() == 0


@pytest.fixture(params=[False, True])
def spot_component_consumer(prepared, request, tmp_path):
    db, now, _, _, _ = prepared
    if request.param:
        # spot_inputs uses the real preparation clock; register the controlled
        # component strictly before it, not at PublicFixture's future clock.
        now[0] = datetime.now(UTC) - timedelta(seconds=5)
        item = InstalledComponent(
            kind="research_recipe",
            component_ref=RECIPE_COMPONENT_REF,
            scope_id=spot_scope("ETH-USDT").scope_id,
            version_id="recipe.engineering1",
            input_contract=RECIPE_COMPONENT_INPUT,
            output_contract=RECIPE_COMPONENT_OUTPUT,
            callback=select_registered_spot_research_recipe,
            code_files={"function": Path(inspect.getfile(select_registered_spot_research_recipe))},
        )
        return selected_consumer(db, now, tmp_path, (item,))
    return MarketComponents(ComponentRegistry(db, clock=lambda: now[0]), ())


class Modes:
    def __init__(self):
        self.seen = []

    def __call__(self, session, study, cutoff):
        raise AssertionError("new mode-aware provider must receive actual task mode")

    def for_research_mode(self, session, study, cutoff, *, research_mode):
        self.seen.append(research_mode)
        return ()


@pytest.fixture
def spot_context_provider():
    return Modes()


@pytest.fixture(params=["current_decision", "historical_review"])
def spot_event_text(request):
    return "ETH macro risk " + (
        "historical review" if request.param == "historical_review" else "current decision"
    )


def test_recipe_frozen_audit_current_and_historical_mode_do_not_reselect(
    spot_inputs, spot_context_provider, spot_component_consumer
):
    inputs, event_id = spot_inputs
    db, request = inputs.database, inputs.request
    assert spot_context_provider.seen == [request.research_mode]
    with db.session() as session:
        rows = session.scalars(
            select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.run_id == request.run_id,
                RunRoleOutputRecord.role == "research_recipe_consumption",
            )
        ).all()
        assert len(rows) == 1
        body = json.loads(rows[0].payload_json)
        audit = session.get(MarketComponentAuditRecord, body["consumption_ref"]["audit_id"])
        assert audit is not None
        receipt = json.loads(audit.payload_json)
        assert receipt["state"] == (
            "selected" if spot_component_consumer.installed else "baseline_not_registry_approved"
        )
        assert body["full_task_hash"] == canonical_hash(request.model_dump(mode="json"))
    pack_root = ROOT / "packs/crypto_macro_prepared/1.2.0"
    manifest = yaml.safe_load((pack_root / "pack.yaml").read_text(encoding="utf-8"))

    def no_new_selection(*args, **kwargs):
        raise AssertionError("saved Task cannot select a different current component")

    factory = ResearchRequestFactory(
        db,
        pack_root=pack_root,
        allowed_capabilities=tuple(manifest["capability_refs"]),
        prepared_policy_refs=PreparedResearchPolicyRefs(
            gate_policy_ref=manifest["gate_policy_ref"],
            rubric_policy_ref=manifest["evaluation_policy_ref"],
            role_profile_refs=tuple(manifest["role_profile_refs"]),
        ),
        component_consumer=no_new_selection,
    )
    assert factory.build(event_id, request.run_id) == request
