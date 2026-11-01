"""ETH metadata/normal queue/ledger; synthetic venue bytes, no network or native call."""

import json
from dataclasses import asdict, replace
from pathlib import Path

from packages.contracts_py.decision_hub_contracts import ObservationCreate
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
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.study import ensure_study
from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord, RunRecord
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from tests.decision_product import test_perpetual_close_source as close_fixture
from tests.decision_product.test_okx_perpetual_original import capture, metadata_row
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_manual_execution import normal_queue_lifecycle
from tests.decision_product.test_perpetual_research_binding import AT, OWNER


def test_eth_metadata_same_normal_queue_protective_exit_ledger(persisted_original, monkeypatch):
    db, archive, source, _ = persisted_original
    original, raw = capture("metadata", metadata_row("ETH-USDT-SWAP"), "ETH-USDT-SWAP")
    original = replace(original, capture_id="raw_synthetic_eth_metadata")
    assert archive.put(raw) == (original.body_hash, original.body_ref)
    with db.session() as session:
        session.add(RawCaptureRecord(**asdict(original), media_type="application/json"))
    fixture = db, archive, source, original
    # Only the existing synthetic page generator changes instrument. Production
    # source readers still authenticate ETH URL/body/cursor/unit originals.
    monkeypatch.setattr(close_fixture, "INST", "ETH-USDT-SWAP")
    ids = close_fixture.pages(fixture)
    body = json.loads(
        (
            Path(__file__).parents[2]
            / "apps/decision-desk/src/api/decisionSettingsReceiptFixture.json"
        ).read_text("utf-8")
    )["intent"]["command"]
    body = json.loads(json.dumps(body).replace("okx:BTC-USDT-SWAP", "okx:ETH-USDT-SWAP"))
    ref = source.metadata_contract_reference(original.capture_id).model_dump(mode="json")
    body["values"]["instrument"] = ref
    body["values"]["position_context"]["instrument_ref"] = ref
    body["values"]["account_snapshot"]["as_of"] = AT.isoformat().replace("+00:00", "Z")
    utility = body["values"]["utility_policy"]
    utility["hash"] = canonical_hash({k: v for k, v in utility.items() if k != "hash"})
    settings = DecisionSettingsCommandV1.model_validate(body)
    settings_service = DecisionSettingsService(
        db, now=lambda: AT, binding_reader=DecisionSettingsBindingReader(source, now=lambda: AT)
    )
    settings_service.save(OWNER, settings)
    event_id, _, accepted = AdmissionService(db, clock=lambda: AT).admit(
        ObservationCreate(
            source_id="eth-manual-test",
            source_type=SourceType.transcript,
            source_url="https://example.com/eth-manual-test",
            text="Federal Reserve research observation.",
            observed_at=AT,
            published_at=AT,
            language="en",
            event_hint="central_bank_speech",
        )
    )
    assert accepted
    run_id, created = RunService(db, clock=lambda: AT).create(
        event_id, strategy_version="research.v1"
    )
    assert created
    with db.session() as session:
        study_id = ensure_study(
            session,
            question_key="eth-manual-test",
            title="ETH test",
            market="crypto",
            instrument="ETH-USDT-SWAP",
            horizon_scope="4h",
            identity_basis="exact_perpetual_decision_v2",
            now=AT,
        )
        session.get(RunRecord, run_id).study_id = study_id
        contract = source.read_contract_in_session(
            session, source.metadata_contract_reference(original.capture_id)
        )
        assert (
            contract.inst_id == "ETH-USDT-SWAP"
            and contract.ct_val == "0.1"
            and contract.ct_mult == "1"
        )
    case = dict(
        db=db,
        archive=archive,
        source=source,
        ids=ids,
        settings=settings,
        settings_service=settings_service,
        event_id=event_id,
        run_id=run_id,
    )
    wire = normal_queue_lifecycle(case, monkeypatch)
    from tests.decision_product.test_perpetual_research_entry import BASE, client_for

    client, _, _ = client_for(case, clock=lambda: AT)
    reply = client.get(BASE + "/paper-execution/capabilities")
    assert reply.status_code == 200
    wire["capabilities"] = reply.json()
    assert wire["capabilities"]["instrument_ids"] == ["okx:BTC-USDT-SWAP", "okx:ETH-USDT-SWAP"]
    (
        Path(__file__).parents[2] / "artifacts/d2-manual-paper-execution/eth-wire-example.json"
    ).write_text(json.dumps(wire, indent=2) + "\n", encoding="utf-8")
