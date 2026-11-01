"""Exact ETH perpetual Task routing; temporary originals only, no provider/model."""

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts import ObservationCreate, ResearchCapabilityQuery
from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSettingsCommandV1,
)
from packages.contracts_py.decision_hub_contracts.models import SourceType
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.decision_settings import (
    DecisionSettingsService,
)
from packages.kernel.decision_hub_kernel.application.decision_settings_binding import (
    DecisionSettingsBindingReader,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    ResearchCapabilityGatewayService,
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.study import ensure_study
from packages.kernel.decision_hub_kernel.persistence.db import Database, RawCaptureRecord, RunRecord
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from packages.provider_adapters.research import CryptoMacroFactPack
from packages.workbench_adapters.durable_research_gateway import (
    DurableResearchCapabilityGateway,
    _SavedTaskScope,
)
from tests.decision_product import test_perpetual_close_source as close_fixture
from tests.decision_product.test_okx_perpetual_original import NOW, capture, metadata_row
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import AT, OWNER, factory_for

ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / "packs/crypto_macro"
ETH = "ETH-USDT-SWAP"


def _gateway(database):
    return DurableResearchCapabilityGateway(
        ResearchCapabilityGatewayService([], [], enabled_capabilities=[]),
        DshSessionLinkService(database),
        ResearchEvidenceService(database),
        ResearchObservabilityService(database),
        requirements={},
    )


def _query(asset, capability="market.crypto_derivatives"):
    symbol = (
        f"{asset}-USDT"
        if capability == "spot_market_facts"
        else f"crypto.{asset.lower()}_usdt.ohlcv_1d"
        if capability == "data.historical"
        else f"{asset}-USDT-SWAP"
    )
    return ResearchCapabilityQuery.model_validate(
        dict(
            schema_version="research-capability-query.v1",
            request_id="eth-scope-call",
            capability_id=capability,
            requirement_id="crypto_spot_confirmation",
            query="Read only the exact admitted asset.",
            target_url=None,
            symbols=[symbol],
            fields=[],
            allowed_domains=[],
            max_results=1,
            max_cost_usd=0,
            research_session_id="eth-scope-test",
            round=1,
            mode="live",
            observed_at=AT,
            cutoff_at=AT,
        )
    )


def test_eth_swap_pack_uses_existing_eth_projection_and_preserves_btc_spot_contracts():
    eth_swap = CryptoMacroFactPack.from_pack(PACK_ROOT, instrument=ETH)
    eth_spot = CryptoMacroFactPack.from_pack(PACK_ROOT, instrument="ETH-USDT")
    family = "crypto_market_snapshot"
    expected = eth_spot.contract_requirements_for_event(family)
    actual = eth_swap.contract_requirements_for_event(family)
    assert actual == expected
    assert any("ETH" in item.description for item in actual)
    assert all("BTC" not in item.description for item in actual)
    assert all("btc" not in units for item in actual for units in item.field_units.values())
    default = CryptoMacroFactPack.from_pack(PACK_ROOT).contract_requirements_for_event(family)
    for instrument in ("BTC-USDT-SWAP", "BTC-USDT"):
        assert (
            CryptoMacroFactPack.from_pack(
                PACK_ROOT, instrument=instrument
            ).contract_requirements_for_event(family)
            == default
        )
    with pytest.raises(ValueError, match="crypto_macro_source_manifest_invalid") as error:
        CryptoMacroFactPack.from_pack(PACK_ROOT, instrument="SOL-USDT-SWAP")
    assert str(error.value.__cause__) == "crypto_macro_fact_pack_instrument_not_supported"


def test_eth_swap_gateway_keeps_exact_asset_scope_and_refuses_cross_asset():
    database = Database("sqlite+pysqlite:///:memory:")
    gateway = _gateway(database)
    try:
        for instrument, asset in (
            ("BTC-USDT", "BTC"),
            ("BTC-USDT-SWAP", "BTC"),
            ("ETH-USDT", "ETH"),
            (ETH, "ETH"),
        ):
            scope = _SavedTaskScope("a" * 64, {}, "b" * 64, "crypto", instrument)
            for capability in (
                "market.crypto_derivatives",
                "market.crypto_crowding",
                "spot_market_facts",
                "data.historical",
            ):
                gateway._validate_task_query_asset(_query(asset, capability), scope)
                with pytest.raises(ResearchCapabilityError) as error:
                    gateway._validate_task_query_asset(
                        _query("ETH" if asset == "BTC" else "BTC", capability), scope
                    )
                assert error.value.error_code == "research_task_asset_mismatch"
        with pytest.raises(ResearchCapabilityError) as error:
            gateway._validate_task_query_asset(_query("ETH"), None)
        assert error.value.error_code == "research_task_asset_scope_unavailable"
        gateway._validate_task_query_asset(_query("ETH", "official.macro"), None)
    finally:
        database.engine.dispose()


def test_factory_freezes_eth_swap_requirements_and_saved_task_asset_scope(
    persisted_original, monkeypatch, tmp_path
):
    database, archive, source, _ = persisted_original
    original, raw = capture("metadata", metadata_row(ETH), ETH)
    original = replace(original, capture_id="raw_synthetic_metadata_eth")
    assert archive.put(raw) == (original.body_hash, original.body_ref)
    with database.session() as session:
        session.add(RawCaptureRecord(**asdict(original), media_type="application/json"))
    monkeypatch.setattr(close_fixture, "INST", ETH)
    ids = close_fixture.pages((database, archive, source, original))
    body = json.loads(
        (ROOT / "apps/decision-desk/src/api/decisionSettingsReceiptFixture.json").read_text(
            encoding="utf-8"
        )
    )["intent"]["command"]
    body["partition"]["instrument_id"] = "okx:" + ETH
    values = body["values"]
    reference = source.metadata_contract_reference(original.capture_id).model_dump(mode="json")
    values["instrument"] = reference
    values["position_context"].update(instrument_id="okx:" + ETH, instrument_ref=reference)
    values["account_snapshot"]["as_of"] = AT.isoformat().replace("+00:00", "Z")
    utility = values["utility_policy"]
    utility["hash"] = canonical_hash(
        {key: value for key, value in utility.items() if key != "hash"}
    )
    settings = DecisionSettingsCommandV1.model_validate(body)
    settings_service = DecisionSettingsService(
        database,
        now=lambda: AT,
        binding_reader=DecisionSettingsBindingReader(source, now=lambda: AT),
    )
    settings_service.save(OWNER, settings)
    event_id, _, accepted = AdmissionService(database, clock=lambda: AT).admit(
        ObservationCreate(
            source_id="eth-task-scope-test",
            source_type=SourceType.transcript,
            source_url="https://example.com/eth-task-scope",
            text="Federal Reserve research observation for the ETH market.",
            observed_at=AT,
            published_at=NOW,
            language="en",
            event_hint="central_bank_speech",
        )
    )
    assert accepted
    run_id, created = RunService(database, clock=lambda: AT).create(
        event_id, strategy_version="research.v1"
    )
    assert created
    with database.session() as session:
        study_id = ensure_study(
            session,
            question_key="eth-perpetual-task-scope",
            title="ETH scope test",
            market="crypto",
            instrument=ETH,
            horizon_scope="4h",
            identity_basis="exact_perpetual_decision_v2",
            now=AT,
        )
        session.get(RunRecord, run_id).study_id = study_id
    case = dict(
        db=database,
        archive=archive,
        source=source,
        ids=ids,
        settings=settings,
        settings_service=settings_service,
        event_id=event_id,
        run_id=run_id,
    )
    factory, _ = factory_for(case, tmp_path)
    request = factory.build(event_id, run_id)
    requirements = request.evidence_requirements
    spot = next(item for item in requirements if item.requirement_id == "crypto_spot_confirmation")
    history = next(item for item in requirements if item.requirement_id == "market_regime_context")
    checks = dict(
        exact_eth_spot="ETH" in spot.description and "BTC" not in spot.description,
        no_btc_descriptions=all("BTC" not in item.description for item in requirements),
        no_btc_units=all(
            "btc" not in units for item in requirements for units in item.field_units.values()
        ),
        historical_context_only="historical context only" in history.description,
        no_seven_day_forward_request="next seven natural days" not in history.description,
        exact_frozen_forward_target="frozen perpetual target and horizon" in history.description,
    )
    assert all(checks.values()), checks
    from packages.kernel.decision_hub_kernel.application.research_binding import (
        read_research_bundle_in_session,
    )

    gateway = _gateway(database)
    with database.session() as session:
        bundle = read_research_bundle_in_session(session, run_id)
        assert bundle.task.scope.schema_version == "research-task-scope.v2"
        assert bundle.task.scope.instrument == ETH and bundle.task.scope.horizon_scope == ["4h"]
        assert bundle.task.scope.requirements_hash == canonical_hash(
            [item.model_dump(mode="json") for item in requirements]
        )
        scope = gateway._task_scope_in_session(session, run_id, _query("ETH"))
        assert scope.instrument == ETH
        with pytest.raises(ResearchCapabilityError) as error:
            gateway._task_scope_in_session(session, run_id, _query("BTC"))
        assert error.value.error_code == "research_task_asset_mismatch"
    assert factory.build(event_id, run_id) == request
