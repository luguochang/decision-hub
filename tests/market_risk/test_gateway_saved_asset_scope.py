"""Isolated real SQLite/Factory scope originals; synthetic providers, no model/network.

The spot fixture's installation/candles are explicit engineering fixtures. These
tests prove a pre-adapter identity boundary, not provider availability or finance.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from apps.hub_worker.research import ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import ResearchCapabilityQuery
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_binding import (
    PreparedResearchPolicyRefs,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    ResearchToolCallReservationRecord,
    RunRoleOutputRecord,
    StudyRecord,
)
from packages.workbench_adapters.durable_research_gateway import DurableResearchCapabilityGateway
from tests.evolution.test_research_worker import _admitted_run
from tests.market_risk.test_recipe_selection_v3 import installation_fixture, manifest
from tests.market_risk.test_spot_research_v3_integration import (
    spot_component_consumer,
    spot_context_provider,
    spot_event_text,
    spot_inputs,
)
from tests.market_risk.test_spot_scope_admission import prepared
from tests.research.test_full_method_v2_bundle import _prepared_pack, forbid_external_connections
from tests.research.test_gateway_execution_v2 import (
    ControlledAdapter,
    _gateway,
    _query,
    inputs,
)
from tests.research.test_gateway_task_scope import (
    ControlledCrossAssetAdapter,
    gateway_and_query,
    prepare_task,
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert all((installation_fixture, manifest, prepared, spot_inputs, spot_component_consumer,
            spot_context_provider, spot_event_text, forbid_external_connections, inputs))


class NeverInvoked:
    def __init__(self):
        self.calls = 0

    async def execute(self, query):
        self.calls += 1
        raise AssertionError("An asset refusal must occur before adapter entry")


@pytest.fixture
def asset_tasks(tmp_path: Path, spot_inputs):
    """BTC V2 and ETH V3 genuine Factory receipts, each in a private database."""
    eth, _ = spot_inputs
    eth_links = DshSessionLinkService(eth.database, clock=lambda: eth.now)
    eth_links.reserve(eth.submit, eth.upstream)
    directory = tmp_path / "btc"
    directory.mkdir()
    btc_db, event_id, run_id = _admitted_run(directory)
    root, definition = _prepared_pack(directory)
    request = ResearchRequestFactory(
        btc_db, pack_root=root, execution_mode="live",
        allowed_capabilities=("data.historical", "market.crypto_derivatives",
                              "market.crypto_crowding", "market.cross_asset"),
        prepared_policy_refs=PreparedResearchPolicyRefs(
            gate_policy_ref=definition["gate_policy_ref"],
            rubric_policy_ref=definition["evaluation_policy_ref"],
            role_profile_refs=tuple(definition["role_profile_refs"]),
        ),
    ).build(event_id, run_id)
    digest = research_request_hash(request)
    session_id, request_id = eth_links.deterministic_ids(run_id, digest)
    btc_submit = eth.submit.model_copy(update={
        "run_id": run_id, "request_hash": digest,
        "deterministic_session_id": session_id, "deterministic_request_id": request_id,
        "prompt_ref": f"hub://runs/{run_id}/prompts/1", "deadline_at": request.deadline_at,
        "max_tool_calls": request.execution_budget.max_tool_calls,
    })
    DshSessionLinkService(btc_db).reserve(btc_submit, eth.upstream)
    try:
        yield ((btc_db, request, session_id), (eth.database, eth.request,
               eth.submit.deterministic_session_id))
    finally:
        btc_db.engine.dispose()


def gateway(db):
    inner = NeverInvoked()
    service = DurableResearchCapabilityGateway(
        inner, DshSessionLinkService(db), ResearchEvidenceService(db),
        ResearchObservabilityService(db), requirements={},
    )
    return service, inner


def query(task, *, capability, symbols, session_id="scope-only",
          requirement="crypto_spot_confirmation", request_id="asset-call"):
    return ResearchCapabilityQuery(
        schema_version="research-capability-query.v1", request_id=request_id,
        capability_id=capability, requirement_id=requirement,
        query="Untrusted question says a different asset; it is not the scope.",
        target_url=None,
        symbols=symbols, fields=[], allowed_domains=[], max_results=1, max_cost_usd=0.1,
        research_session_id=session_id, round=1, mode="live", observed_at=datetime.now(UTC),
        cutoff_at=task.pit_cutoff_at, deadline_at=task.deadline_at,
    )


def test_both_real_factory_originals_reject_opposite_mixed_missing_and_unknown(asset_tasks):
    for db, task, _ in asset_tasks:
        service, inner = gateway(db)
        with db.session() as session:
            from packages.kernel.decision_hub_kernel.application.research_binding import (
                read_research_bundle_in_session,
            )
            scope = read_research_bundle_in_session(session, task.run_id).task.scope
            expected = scope.instrument.split("-")[0]
            opposite = "ETH" if expected == "BTC" else "BTC"
            cases = (
                ("spot_market_facts", [f"{opposite}-USDT"], "research_task_asset_mismatch"),
                ("market.crypto_derivatives", [f"{opposite}-USDT-SWAP"],
                 "research_task_asset_mismatch"),
                ("market.crypto_crowding", [f"{opposite}-USDT-SWAP"],
                 "research_task_asset_mismatch"),
                ("market.crypto_derivatives", [f"{expected}-USDT-SWAP",
                                              f"{opposite}-USDT-SWAP"],
                 "research_task_asset_mismatch"),
                ("spot_market_facts", [], "research_task_asset_symbols_required"),
                ("data.historical", ["crypto.unknown_usdt.ohlcv_1d"],
                 "research_task_asset_symbol_unbound"),
            )
            for capability, symbols, code in cases:
                with pytest.raises(ResearchCapabilityError) as error:
                    service._task_scope_in_session(session, task.run_id,
                        query(task, capability=capability, symbols=symbols))
                assert error.value.error_code == code
            same_asset = service._task_scope_in_session(session, task.run_id,
                query(task, capability="spot_market_facts", symbols=[f"{expected}-USDT"]))
            assert same_asset.instrument == scope.instrument
            assert same_asset.scope_hash == scope.scope_hash
            derivative = service._task_scope_in_session(session, task.run_id,
                query(task, capability="market.crypto_derivatives",
                      symbols=[f"{expected}-USDT-SWAP"]))
            assert derivative == same_asset
        assert inner.calls == 0  # scope acceptance never claims a provider was invoked


@pytest.mark.asyncio
async def test_both_actual_sessions_refusal_is_audited_without_call_or_evidence(asset_tasks):
    for db, task, session_id in asset_tasks:
        service, inner = gateway(db)
        with db.session() as session:
            saved = service._task_scope_in_session(session, task.run_id,
                query(task, capability="market.cross_asset", symbols=["DGS10"],
                      requirement="macro_transmission"))
        opposite = "BTC" if saved.instrument == "ETH-USDT" else "ETH"
        attempts = [
            ("market.crypto_derivatives", [f"{opposite}-USDT-SWAP"], "derivatives_crowding"),
            ("market.crypto_crowding", [f"{opposite}-USDT-SWAP"], "derivatives_crowding"),
            ("spot_market_facts", [f"{opposite}-USDT"], "crypto_spot_confirmation"),
        ]
        attempts.append(("data.historical", [f"crypto.{opposite.lower()}_usdt.ohlcv_1d"],
                         "market_regime_context"))
        for index, (capability, symbols, requirement) in enumerate(attempts):
            call = query(task, capability=capability, symbols=symbols, requirement=requirement,
                         session_id=session_id, request_id=f"cross-asset-{index}")
            for _ in range(2):  # rejected replay remains audit-idempotent
                with pytest.raises(ResearchCapabilityError) as error:
                    await service.execute(call)
                assert error.value.error_code == "research_task_asset_mismatch"
        traces = ResearchObservabilityService(db).list_trace(task.run_id)
        assert len(traces) == len(attempts)
        assert all(item.status == "denied" and item.error_code == "research_task_asset_mismatch"
                   for item in traces)
        assert inner.calls == 0
        assert ResearchEvidenceService(db).list_run_evidence(task.run_id) == []
        with db.session() as session:
            assert session.query(ResearchToolCallReservationRecord).count() == 0


def test_macro_remains_legal_and_exact_own_history_preserves_asset_gate(asset_tasks):
    for db, task, _ in asset_tasks:
        service, _ = gateway(db)
        with db.session() as session:
            for capability, symbols in (("market.cross_asset", ["DGS10"]),
                                         ("data.historical", ["macro.fred.dgs10"])):
                saved = service._task_scope_in_session(session, task.run_id,
                    query(task, capability=capability, symbols=symbols,
                          requirement="macro_transmission"))
                assert saved is not None
            own = saved.instrument.split("-")[0].lower()
            accepted = service._task_scope_in_session(session, task.run_id,
                query(task, capability="data.historical",
                      symbols=[f"crypto.{own}_usdt.ohlcv_1d"],
                      requirement="market_regime_context"))
            assert accepted == saved
            with pytest.raises(ResearchCapabilityError) as error:
                service._task_scope_in_session(session, task.run_id,
                    query(task, capability="data.historical",
                          symbols=[f"crypto.{own}_usdt.ohlcv_2h"],
                          requirement="market_regime_context"))
            assert error.value.error_code == "research_task_asset_symbol_unbound"


@pytest.mark.asyncio
async def test_legacy_no_asset_scope_closed_while_actual_macro_adapter_still_runs(tmp_path):
    from packages.kernel.decision_hub_kernel.persistence.db import Database
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'legacy.sqlite3'}")
    db.create_all()
    try:
        task = prepare_task(db)
        adapter = ControlledCrossAssetAdapter()
        service, _, macro = gateway_and_query(db, task, adapter)
        with pytest.raises(ResearchCapabilityError) as error:
            await service.execute(macro.model_copy(update={
                "capability_id": "market.crypto_derivatives", "symbols": ["BTC-USDT-SWAP"],
            }))
        assert error.value.error_code == "research_task_asset_scope_unavailable"
        assert adapter.calls == []
        await service.execute(macro)
        assert len(adapter.calls) == 1
        assert any(item.status == "denied" and item.error_code == error.value.error_code
                   for item in ResearchObservabilityService(db).list_trace(task.run_id))
    finally:
        db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["payload_hash", "current_study"])
async def test_bad_original_or_current_study_never_supplies_replacement_asset(asset_tasks, damage):
    db, task, session_id = asset_tasks[1]
    service, inner = gateway(db)
    with db.session() as session:
        if damage == "payload_hash":
            row = session.query(RunRoleOutputRecord).filter_by(
                run_id=task.run_id, role="research_task_request").one()
            row.payload_hash = "f" * 64
        else:
            row = session.query(StudyRecord).filter_by(instrument="ETH-USDT").one()
            row.instrument = "BTC-USDT"
    with pytest.raises(ResearchCapabilityError) as error:
        await service.execute(query(task, capability="spot_market_facts", symbols=["BTC-USDT"],
                                    session_id=session_id))
    assert error.value.error_code == "research_task_request_invalid"
    assert inner.calls == 0
    assert any(item.status == "denied" and item.error_code == error.value.error_code
               for item in ResearchObservabilityService(db).list_trace(task.run_id))


@pytest.mark.asyncio
async def test_scope_hash_only_drift_keeps_actual_adapter_cost_without_admission(
    inputs, monkeypatch,
):
    """Real frozen Method/Selection/Prompt admission; controlled scope-hash drift.

    The actual V2 SQLite reservation and provider/cost writes remain enabled.
    Only the second scope-read identity is changed, without changing Task hash.
    Offline fixtures are not a live Host or financial qualification.
    """
    inputs.prepare()
    db, task = inputs.database, inputs.request
    adapter = ControlledAdapter(inputs)
    service = _gateway(inputs, adapter)
    call = _query(inputs)
    read_scope = service._task_scope_in_session

    def changed_scope(session, run_id, query):
        original = read_scope(session, run_id, query)
        return replace(original, scope_hash="f" * 64) if adapter.calls else original

    monkeypatch.setattr(service, "_task_scope_in_session", changed_scope)
    with pytest.raises(ResearchCapabilityError) as error:
        await service.execute(call)
    assert error.value.error_code == "research_task_request_changed_during_call"
    assert len(adapter.calls) == 1
    assert ResearchEvidenceService(db).list_run_evidence(task.run_id) == []
    with db.session() as session:
        row = session.get(ResearchToolCallReservationRecord, (task.run_id, call.request_id))
        assert row.status == "completed" and row.result_json is not None
        assert json.loads(row.result_json)["cost_usd"] == 0.01
    assert any(item.reference_type == "task_scope" and item.status == "denied"
               for item in ResearchObservabilityService(db).list_trace(task.run_id))


@pytest.mark.asyncio
async def test_refusal_survives_audit_storage_failure_without_adapter_entry(tmp_path, monkeypatch):
    from packages.kernel.decision_hub_kernel.persistence.db import Database
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'audit-failure.sqlite3'}")
    db.create_all()
    try:
        task = prepare_task(db)
        adapter = ControlledCrossAssetAdapter()
        service, _, macro = gateway_and_query(db, task, adapter)

        def fail_audit(_event):
            raise OSError("controlled private audit unavailable")

        monkeypatch.setattr(service.observability, "append_trace_once", fail_audit)
        with pytest.raises(ResearchCapabilityError) as error:
            await service.execute(macro.model_copy(update={
                "capability_id": "market.crypto_derivatives", "symbols": ["BTC-USDT-SWAP"],
            }))
        assert error.value.error_code == "research_task_asset_scope_unavailable"
        assert "research_denial_audit_unknown:OSError" in error.value.__notes__
        assert adapter.calls == []
        with db.session() as session:
            assert session.query(ResearchToolCallReservationRecord).count() == 0
    finally:
        db.engine.dispose()
