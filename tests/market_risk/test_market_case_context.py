"""Private SQLite wiring controls; synthetic witnesses never establish native eligibility.

The first-Factory test uses real Factory/V3 selectors with existing controlled
installation fixtures. Registry positive tests deliberately replace only the
private original authenticator, and prove storage/selection control, not a Host
execution or a financially qualified case. No model/network/formal DB access.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy import event, select

from apps.hub_worker import market_case_context as context
from apps.hub_worker.market_case_context import MarketCaseContextProvider
from packages.kernel.decision_hub_kernel.application.market_case import MarketCaseService
from packages.kernel.decision_hub_kernel.application.research_binding import (
    PreparedResearchPolicyRefs,
)
from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
    GraphFinalProjectionRef,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    canonical_recipe,
    recipe_hash,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRecord,
    RunRoleOutputRecord,
    StudyRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_case import CaseContextReference
from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedExecutionRef
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from tests.market_risk import test_spot_research_v3_integration as integration
from tests.market_risk.test_market_case_components import seed_case
from tests.market_risk.test_recipe_selection_v3 import configure, installation_fixture, manifest
from tests.market_risk.test_spot_scope_admission import prepared

assert installation_fixture is not None and manifest is not None and prepared is not None
ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 8, 4, 0, tzinfo=UTC)


def provider(database, tmp_path, *, enabled=True, clock=lambda: NOW):
    pack_root = ROOT / "packs/crypto_macro_prepared/1.2.0"
    body = yaml.safe_load((pack_root / "pack.yaml").read_text(encoding="utf-8"))
    return MarketCaseContextProvider(
        database,
        RawBodyArchive(tmp_path / "raw"),
        prepared_pack_root=pack_root,
        refs=PreparedResearchPolicyRefs(
            gate_policy_ref=body["gate_policy_ref"],
            rubric_policy_ref=body["evaluation_policy_ref"],
            role_profile_refs=tuple(body["role_profile_refs"]),
        ),
        allowed_capabilities=tuple(body["capability_refs"]),
        execution_mode="live",
        runtime_id="dsh",
        profile_ref="decision-research.web.v1",
        memory_enabled=enabled,
        clock=clock,
    )


def study(instrument="BTC-USDT"):
    return SimpleNamespace(market="crypto", instrument=instrument, horizon_scope="30m,24h,72h")


def synthetic_proof(
    run_id="controlled-witness", *, stable=None, known_at=NOW - timedelta(seconds=2)
):
    """Private seam only; no production reader is bypassed by an API argument."""

    def ref(role, digit):
        return ObservedExecutionRef(
            output_id=role + "-" + run_id,
            role=role,
            schema_version="synthetic.fixture.v1",
            payload_hash=digit * 64,
            recorded_at=known_at,
        )

    return context._AuthenticatedMethod(
        "BTC-USDT",
        stable or {"schema_version": "synthetic.current-method.fixture.v1"},
        ref("method", "1"),
        ref("compiled", "2"),
        ref("selection", "3"),
        GraphFinalProjectionRef(row=ref("final", "4")),
        known_at,
    )


@pytest.fixture
def registry(tmp_path, monkeypatch, installation_fixture):
    configure(installation_fixture)
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'case-context.sqlite3'}")
    database.create_all()
    with database.session() as session:
        session.add(
            StudyRecord(
                study_id="study-controlled",
                scope_key="controlled",
                title="synthetic",
                market="crypto",
                instrument="BTC-USDT",
                horizon_scope="30m,24h,72h",
                question_key="fixture",
                identity_basis="explicit",
                status="active",
                created_at=NOW - timedelta(minutes=1),
                updated_at=NOW - timedelta(minutes=1),
            )
        )
        session.add(
            RunRecord(
                run_id="controlled-witness",
                event_id="controlled-event",
                study_id="study-controlled",
                status="running",
                strategy_version="research.v1",
                runtime_version="synthetic",
                created_at=NOW - timedelta(minutes=1),
                updated_at=NOW - timedelta(minutes=1),
            )
        )
    value = provider(database, tmp_path)
    # Controlled authenticator seam for registry mechanics, not native proof.
    monkeypatch.setattr(context, "read_run_spot_admission_in_session", lambda s, r: object())
    monkeypatch.setattr(value, "_authenticate", lambda s, r, c: synthetic_proof(r))
    yield value, database
    database.engine.dispose()


def select_inputs(value, database, *, at=NOW):
    with database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        return value(session, study(), at)


def diagnostic(inputs):
    return json.loads(inputs[0].excerpt)


def test_current_configuration_authenticates_pack_policy_profile_and_installation(registry):
    value, _ = registry
    current = value._current("BTC-USDT")
    assert current.pack.research_policy.manager_ref == "crypto_macro.manager.v1"
    assert current.identity["plugin_build_hash"] == "a" * 64
    assert (
        current.identity["loaded_sources"]["prompt_profile"] == context.profile.PROMPT_POLICY_HASH
    )
    assert current.identity["compiler"] and current.identity["checker"]["code_hash"]
    assert current.digest != value._current("ETH-USDT").digest


def test_first_method_unknown_is_frozen_and_protected_by_actual_factory(
    tmp_path,
    monkeypatch,
    prepared,
    installation_fixture,
):
    original_factory = integration.ResearchRequestFactory

    def factory(database, **kwargs):
        kwargs["context_inputs_provider"] = provider(database, tmp_path)
        return original_factory(database, **kwargs)

    monkeypatch.setattr(integration, "ResearchRequestFactory", factory)
    inputs, _ = integration.spot_inputs.__wrapped__(prepared, installation_fixture)
    cases = [
        item for item in inputs.request.input_evidence if item.source_id == "market_case_ledger.v1"
    ]
    assert len(cases) == 1 and cases[0].kind == "document" and cases[0].authority == "unverified"
    assert json.loads(cases[0].excerpt)["status"] == "current_stable_method_unavailable"
    with inputs.database.session() as session:
        selected = context.graph_recipe.read_graph_recipe_selection_v3_in_session(
            session,
            inputs.request.run_id,
        )
        assert (
            cases[0].evidence_id
            in selected.receipt.quality_context.additional_required_context_refs
        )
        assert (
            session.scalar(
                select(RunRoleOutputRecord).where(
                    RunRoleOutputRecord.role == context.ROLE,
                )
            )
            is None
        )  # Factory reads never register a method.


def test_register_restart_and_repeat_preserve_original_identity_and_time(
    registry, tmp_path, monkeypatch
):
    value, database = registry
    value.observe("controlled-witness")
    with database.session() as session:
        saved = session.scalar(
            select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.role == context.ROLE,
            )
        )
        original = (saved.output_id, saved.payload_json, saved.payload_hash)
    value.clock = lambda: NOW + timedelta(hours=1)
    value.observe("controlled-witness")
    restarted = provider(database, tmp_path)
    monkeypatch.setattr(restarted, "_authenticate", lambda s, r, c: synthetic_proof(r))
    output = select_inputs(restarted, database)
    assert diagnostic(output)["status"] == "no_eligible_cases"
    with database.session() as session:
        row = session.get(RunRoleOutputRecord, original[0])
        assert (row.output_id, row.payload_json, row.payload_hash) == original
    assert (
        diagnostic(select_inputs(value, database, at=NOW - timedelta(microseconds=1)))["status"]
        == "current_stable_method_not_yet_known"
    )


def test_current_configuration_change_does_not_borrow_latest_run(registry, monkeypatch):
    value, database = registry
    value.observe("controlled-witness")
    old = value._current("BTC-USDT")
    current = context._CurrentConfiguration(
        {**old.identity, "profile_ref": "new.current.profile"},
        old.pack,
        old.installation,
    )
    monkeypatch.setattr(value, "_current", lambda instrument: current)
    assert (
        diagnostic(select_inputs(value, database))["status"] == "current_stable_method_unavailable"
    )


@pytest.mark.parametrize(
    "field", ["payload_hash", "run_id", "created_at", "stable_recipe", "registry_id"]
)
def test_registry_changes_fail_closed(registry, field):
    value, database = registry
    value.observe("controlled-witness")
    with database.session() as session:
        row = session.scalar(
            select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.role == context.ROLE,
            )
        )
        if field == "payload_hash":
            row.payload_hash = "f" * 64
        elif field == "run_id":
            row.run_id = "other"
        elif field == "created_at":
            row.created_at = NOW - timedelta(seconds=1)
        else:
            body = json.loads(row.payload_json)
            body[field] = {"changed": True} if field == "stable_recipe" else "other-id"
            row.payload_json = canonical_recipe(body)
            row.payload_hash = recipe_hash(body)
    result = diagnostic(select_inputs(value, database))
    assert result["status"] == "current_stable_method_unverifiable"
    assert result["reason_code"] == "market_case_current_method_registry_changed"


def test_new_native_witness_cannot_replace_different_stable_method(registry, monkeypatch):
    value, database = registry
    value.observe("controlled-witness")
    monkeypatch.setattr(
        value,
        "_authenticate",
        lambda s, r, c: synthetic_proof(
            r,
            stable={"different": True} if r == "second" else None,
        ),
    )
    with database.session() as session:
        session.add(
            RunRecord(
                run_id="second",
                event_id="controlled-event",
                study_id="study-controlled",
                status="running",
                strategy_version="research.v1",
                runtime_version="synthetic",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    with pytest.raises(ValueError, match="current_method_ambiguous"):
        value.observe("second")


def test_original_witness_failure_surfaces_unknown_and_no_case_selection(registry, monkeypatch):
    value, database = registry
    value.observe("controlled-witness")

    def unavailable(*args):
        raise ValueError("original_method_changed")

    monkeypatch.setattr(value, "_authenticate", unavailable)
    monkeypatch.setattr(value.cases, "context_in_session", lambda *a, **k: pytest.fail("case read"))
    result = diagnostic(select_inputs(value, database))
    assert result["status"] == "current_stable_method_unverifiable"
    assert result["reason_code"] == "original_method_changed"


def test_actual_case_gate_excludes_unverified_and_future_records(registry, tmp_path):
    value, database = registry
    seed_case(database, identity="same")
    seed_case(database, identity="future", known_at=NOW + timedelta(days=1))
    service = MarketCaseService(database, RawBodyArchive(tmp_path / "raw"), clock=lambda: NOW)
    same = service.capture("same", revision_id="rev-same", outlook_id="out-same")
    future = service.capture("future", revision_id="rev-future", outlook_id="out-future")
    assert not same.eligible and not future.eligible
    value.observe("controlled-witness")
    result = diagnostic(select_inputs(value, database))
    assert result["status"] == "no_eligible_cases" and result["total_scanned"] == 1
    assert not result["selected_refs"]


def test_max_four_contexts_plus_receipt_same_snapshot_and_no_fact_authority(registry, monkeypatch):
    value, database = registry
    value.observe("controlled-witness")
    current = value._current("BTC-USDT")
    seen_sessions = []

    def controlled_port(session, **kwargs):
        # A bounded synthetic port tests packaging. Actual qualification gate is tested above.
        assert kwargs["max_items"] == 4 and kwargs["scope"].stable_recipe_hash == recipe_hash(
            synthetic_proof().stable_recipe
        )
        seen_sessions.append(session)
        refs = tuple(
            CaseContextReference(
                ref=f"market_case:fixture-{n}",
                payload_hash=str(n + 1) * 64,
                available_at=NOW - timedelta(seconds=1),
                revision_id=f"revision-{n}",
                run_id=f"fixture-{n}",
                excerpt="synthetic control" * 150,
            )
            for n in range(4)
        )
        inputs = tuple(
            context.ResearchInputEvidence(
                evidence_id=f"case-input-{n}",
                kind="document",
                authority="unverified",
                source_id="market_case_ledger.v1",
                source_url=None,
                published_at=None,
                observed_at=ref.available_at,
                received_at=ref.available_at,
                content_hash=ref.payload_hash,
                excerpt=ref.excerpt,
            )
            for n, ref in enumerate(refs)
        )
        return inputs, refs, SimpleNamespace(excluded=(), total_scanned=4, truncated=False)

    monkeypatch.setattr(value.cases, "context_in_session", controlled_port)
    statements = []
    with database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        connection = session.connection()
        event.listen(
            connection,
            "before_cursor_execute",
            lambda c, cur, statement, p, ctxt, many: statements.append(statement),
        )
        output = value(session, study(), NOW)
        assert seen_sessions == [session] and session.get_transaction().is_active
    assert len(output) == 5 and len(output[0].excerpt) <= 4000
    assert all(item.kind == "document" and item.authority == "unverified" for item in output)
    assert diagnostic(output)["status"] == "selected"
    assert diagnostic(output)["registry_id"] == value._registry_id(current)
    assert all(statement.lstrip().upper().startswith("SELECT ") for statement in statements)


def test_memory_rollback_does_no_registry_case_or_current_source_read(registry, monkeypatch):
    value, database = registry
    value.memory_enabled = False
    monkeypatch.setattr(value, "_current", lambda i: pytest.fail("current read"))
    value.observe("controlled-witness")
    assert diagnostic(select_inputs(value, database))["status"] == "market_case_memory_disabled"
    with database.session() as session:
        assert (
            session.scalar(
                select(RunRoleOutputRecord).where(
                    RunRoleOutputRecord.role == context.ROLE,
                )
            )
            is None
        )


def test_old_run_observer_is_noop_and_scope_mismatch_unknown(registry, monkeypatch):
    value, database = registry
    monkeypatch.setattr(context, "read_run_spot_admission_in_session", lambda s, r: None)
    value.observe("controlled-witness")
    with database.session() as session:
        assert (
            session.scalar(
                select(RunRoleOutputRecord).where(
                    RunRoleOutputRecord.role == context.ROLE,
                )
            )
            is None
        )
        mismatch = SimpleNamespace(market="crypto", instrument="BTC-USDT-SWAP", horizon_scope="24h")
        result = value(session, mismatch, NOW)
    assert diagnostic(result)["status"] == "market_case_exact_scope_unavailable"


def test_explicit_mode_adapter_rejects_historical_before_any_source_read(tmp_path, monkeypatch):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'no-tables.sqlite3'}")
    value = provider(database, tmp_path)
    monkeypatch.setattr(value, "_current", lambda _: pytest.fail("current source read"))
    with database.session() as session:
        result = value.for_research_mode(
            session,
            study(),
            NOW,
            research_mode="historical_review",
        )
    assert diagnostic(result)["status"] == "current_stable_method_mode_unavailable"
    assert diagnostic(result)["research_mode"] == "historical_review"
    assert "stable_hub_recipe" not in diagnostic(result)
    database.engine.dispose()


def test_frozen_selected_method_hash_must_match_actual_and_old_task_cannot_register():
    stable = {"schema_version": "controlled.stable-method.fixture.v1", "method": "one"}
    selected = MarketCaseContextProvider._diagnostic(
        NOW,
        reason="selected",
        stable_hub_recipe=recipe_hash(stable),
    )
    assert MarketCaseContextProvider._verify_frozen_context([selected], stable)
    with pytest.raises(ValueError, match="frozen_current_method_mismatch"):
        MarketCaseContextProvider._verify_frozen_context([selected], {**stable, "method": "two"})
    unknown = MarketCaseContextProvider._diagnostic(NOW, reason="current_stable_method_unavailable")
    assert MarketCaseContextProvider._verify_frozen_context([unknown], stable)
    assert not MarketCaseContextProvider._verify_frozen_context([], stable)
    tampered = unknown.model_copy(update={"excerpt": unknown.excerpt + " "})
    with pytest.raises(ValueError, match="frozen_selection_changed"):
        MarketCaseContextProvider._verify_frozen_context([tampered], stable)
