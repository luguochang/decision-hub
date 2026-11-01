"""Saved paper risk policy with exact trade scenarios; no real network or native/model authority."""

from datetime import datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from packages.kernel.decision_hub_kernel.application.perpetual_manual_execution import (
    PerpetualManualPaperService,
)
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_manual_execution import paper_publication
from tests.decision_product.test_perpetual_research_binding import OWNER
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case


@pytest.mark.parametrize(
    "side,stop,expected,within",
    [
        ("long", "9000", "10.112991", True),
        ("short", "11000", "10.127011", True),
        ("long", "7000", "30.098993", False),
        ("short", "13000", "30.141013", False),
    ],
)
def test_stop_trade_plan_uses_contracts_adverse_tick_and_both_fees(side, stop, expected, within):
    from packages.kernel.decision_hub_kernel.decision.perpetual_accounting import LinearContractSpec

    spec = LinearContractSpec(
        base="BTC",
        quote="USDT",
        settle="USDT",
        ct_val=Decimal("0.01"),
        ct_mult=Decimal(1),
        ct_val_ccy="BTC",
        lot_sz=Decimal("0.01"),
        min_sz=Decimal("0.01"),
        tick_sz=Decimal("0.1"),
    )
    risk = PerpetualManualPaperService._planned_stop_loss(
        spec,
        contracts=Decimal(1),
        entry_price=Decimal("10000"),
        stop_price=Decimal(stop),
        target_side=side,
        protocol={"fee_rate": "0.0005", "slippage_bps": "2"},
        limit=Decimal(25),
    )
    assert risk["planned_trade_loss_usdt"] == expected
    assert risk["within_limit"] is within
    assert Decimal(risk["entry_fee_usdt"]) > 0 and Decimal(risk["exit_fee_usdt"]) > 0
    assert Decimal(risk["planned_trade_loss_usdt"]) > -Decimal(risk["gross_pnl_usdt"])
    assert risk["funding_cash_usdt"] is None and risk["guaranteed_loss_cap"] is False
    assert risk["stop_trigger_basis"] == ("bid" if side == "long" else "ask")


@pytest.mark.parametrize("mode,stop", [("under", "9000"), ("over", "5000"), ("stale", "9000")])
def test_saved_loss25_is_enforced_at_first_eligible_quote(binding_case, monkeypatch, mode, stop):
    # Append low-volatility SYNTHETIC close originals for this paper-domain gate.
    # The common 100/200 stress fixture correctly selects no_change under loss25.
    # Its existing originals remain intact; this is not a native research qualification.
    import json

    from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
        DecisionSettingsCommandV1,
    )
    from packages.kernel.decision_hub_kernel.application import perpetual_paper
    from packages.kernel.decision_hub_kernel.application.perpetual_quote_sampling import (
        PerpetualQuoteSamplingAdapter,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord
    from packages.kernel.decision_hub_kernel.persistence.perpetual_paper_repository import (
        PerpetualPaperRepository,
    )
    from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
    from packages.kernel.decision_hub_kernel.ports.perpetual_manual_execution import (
        ManualDisplayCommand,
        ManualExecutionCommand,
    )
    from packages.provider_adapters.research import raw_capture

    close_ids = []
    for index, old_id in enumerate(binding_case["ids"]):
        with binding_case["db"].session() as session:
            old = session.get(RawCaptureRecord, old_id)
            record = {column.name: getattr(old, column.name) for column in old.__table__.columns}
        payload = json.loads(binding_case["archive"].read(record["body_ref"]))
        for row in payload["data"]:
            for column in range(1, 5):
                if row[column] == "200":
                    row[column] = "100.01"
        raw = json.dumps(payload, separators=(",", ":")).encode()
        digest, ref = binding_case["archive"].put(raw)
        new_id = f"risk_close_{index}"
        record.update(
            capture_id=new_id,
            context_id=record["context_id"].replace("1" * 32, "2" * 32),
            body_hash=digest,
            body_ref=ref,
            byte_count=len(raw),
        )
        with binding_case["db"].session() as session:
            session.add(RawCaptureRecord(**record))
        close_ids.append(new_id)
    binding_case["ids"] = tuple(close_ids)
    # Configure the new paper settings before its real immutable publication is made.
    body = binding_case["settings"].model_dump(mode="json")
    policy = body["values"]["utility_policy"]
    policy["max_planned_loss"] = {"unit": "USDT", "value": "25"}
    policy["max_gross_notional"] = {"unit": "USDT", "value": "500"}
    policy["hash"] = canonical_hash({key: value for key, value in policy.items() if key != "hash"})
    binding_case["settings"] = DecisionSettingsCommandV1.model_validate(body)
    paper, protocols, at, identity, _ = paper_publication(binding_case)
    clock = [at]
    svc = PerpetualManualPaperService(binding_case["db"], paper, protocols, clock=lambda: clock[0])
    shown = svc.record_display(
        OWNER,
        ManualDisplayCommand(
            schema_version="perpetual-manual-display.v1", request_id="risk-visible", **identity
        ),
    )
    clock[0] += timedelta(milliseconds=10)
    command = ManualExecutionCommand(
        schema_version="perpetual-manual-intent-command.v1",
        request_id="risk-confirm",
        **identity,
        display_receipt={"ref": shown.request_id, "hash": shown.receipt_hash},
        max_entry_notional_usdt="80",
        stop_price_usdt=stop,
        take_profit_price_usdt="12000",
        exit_at=(at + timedelta(minutes=5)).isoformat(),
        simulation_acknowledged=True,
    )
    accepted = svc.submit_intent(OWNER, command)
    assert accepted.response["status"] == "queued"
    intent_id = accepted.response["intent"]["id"]
    assert svc.read_execution(OWNER, intent_id)["receipt"] is None
    assert binding_case["settings"].values.utility_policy.max_planned_loss.value == "25"

    def wall():
        clock[0] += timedelta(milliseconds=1)
        return clock[0]

    class Wall(datetime):
        @classmethod
        def now(cls, tz=None):
            return wall()

    monkeypatch.setattr(raw_capture, "datetime", Wall)
    monkeypatch.setattr(perpetual_paper, "utcnow", wall)
    monkeypatch.setattr(
        perpetual_paper, "monotonic_ns", lambda: int(clock[0].timestamp() * 1_000_000_000)
    )
    calls = []

    async def response(transport, request):
        calls.append(request.url.path)
        row = {"ts": str(int(clock[0].timestamp() * 1000))}
        if request.url.path == "/api/v5/market/books":
            row.update(
                bids=[["9990", "20", "0", "1"]], asks=[["10010", "20", "0", "1"]], seqId=len(calls)
            )
        return httpx.Response(200, request=request, json={"code": "0", "data": [row]})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", response)
    clock[0] = datetime.fromisoformat(accepted.response["intent"]["eligible_at"]) + timedelta(
        milliseconds=200
    )
    assert PerpetualQuoteSamplingAdapter(svc).process_due() == 1
    if mode == "stale":
        clock[0] += timedelta(seconds=6)
    result = svc.execute_intent(intent_id)
    view = svc.read_execution(OWNER, intent_id)
    assert calls == ["/api/v5/public/time", "/api/v5/market/books", "/api/v5/public/time"]
    with binding_case["db"].session() as session:
        ledger = PerpetualPaperRepository(session).current(OWNER, "manual-account")
    if mode == "under":
        assert result["status"] == "filled" and ledger["state_version"] == 2
        risk = view["episode"]["planned_loss_assessment"]
        assert risk["limit_usdt"] == "25" and risk["within_limit"] is True
        assert Decimal(risk["planned_trade_loss_usdt"]) < 25
        assert risk["funding_cash_usdt"] is None and not risk["guaranteed_loss_cap"]
    else:
        assert result["status"] == "no_fill" and result["fill_id"] is None
        assert result["reason"] == (
            "perp_manual_planned_loss_limit_exceeded"
            if mode == "over"
            else "first_eligible_quote_missed_or_expired"
        )
        assert ledger["state_version"] == 1 and view["episode"] is None
        assert svc.execute_intent(intent_id) == result
    assert binding_case["settings"].values.utility_policy.max_planned_loss.value == "25"
