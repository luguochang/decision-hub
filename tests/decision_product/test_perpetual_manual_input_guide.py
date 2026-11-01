"""Prepared manual-paper input guide replays existing quote originals; no network in the query."""

from datetime import datetime, timedelta

import pytest

from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_manual_execution import paper_publication
from tests.decision_product.test_perpetual_research_binding import OWNER
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case


def test_prepared_guide_is_owner_bound_read_only_and_reports_pending_fresh_stale(
    binding_case, monkeypatch
):
    import httpx

    from packages.kernel.decision_hub_kernel.application import perpetual_paper
    from packages.kernel.decision_hub_kernel.application.action_execution import (
        ActionExecutionService,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_manual_execution import (
        PerpetualManualPaperService,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_manual_input_guide import (
        PerpetualManualInputGuideQuery,
    )
    from packages.kernel.decision_hub_kernel.persistence.action_execution_records import (
        ExecutionEpisodeHeadRecord,
        ExecutionIntentRecord,
    )
    from packages.kernel.decision_hub_kernel.persistence.action_quote_sampling_records import (
        QuoteSamplingHead,
        QuoteSamplingReceipt,
    )
    from packages.kernel.decision_hub_kernel.persistence.perpetual_paper_repository import (
        PerpetualPaperRepository,
    )
    from packages.kernel.decision_hub_kernel.ports.perpetual_manual_execution import (
        ManualPreparationCommand,
    )
    from packages.provider_adapters.research import raw_capture
    from tests.decision_product.test_perpetual_research_entry import BASE, client_for

    paper, protocols, at, _identity, view = paper_publication(binding_case)
    clock = [at]
    service = PerpetualManualPaperService(
        binding_case["db"], paper, protocols, clock=lambda: clock[0]
    )
    command = ManualPreparationCommand(
        schema_version="perpetual-manual-prepare.v1",
        request_id="prepare-input-guide",
        partition=view["partition"],
        settings_revision=view["settings_revision"],
        settings_state_version=view["settings_state_version"],
        revision={
            "ref": view["revision"]["revision_id"],
            "hash": view["revision"]["payload_hash"],
        },
        horizon="4h",
    )
    prepared = service.prepare_context(OWNER, command)
    query = PerpetualManualInputGuideQuery(service, clock=lambda: clock[0])
    latest_entry = datetime.fromisoformat(view["revision"]["valid_until"])
    latest_exit = datetime.fromisoformat(view["target"]["window_end"])
    assert latest_entry < latest_exit
    assert datetime.fromisoformat(prepared.response["protocol"]["T1"]) == latest_exit

    with binding_case["db"].session() as session:
        ledger_before = PerpetualPaperRepository(session).current(OWNER, "manual-account")
        intent_count_before = session.query(ExecutionIntentRecord).count()
        assert intent_count_before == 0
        assert session.query(ExecutionEpisodeHeadRecord).count() == 0
        assert session.query(QuoteSamplingReceipt).count() == 0

    pending = query.read(OWNER, command.request_id)
    assert pending["quote"] == {"status": "pending"}
    assert pending["target_side"] in {"long", "short"}
    assert pending["tick_sz"] == "0.1"
    assert datetime.fromisoformat(pending["latest_entry_at"]) == latest_entry
    assert pending["latest_exit_at"] == prepared.response["protocol"]["T1"]
    with pytest.raises((ValueError, PermissionError), match="owner|missing"):
        query.read("foreign-owner", command.request_id)

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

    async def response(_transport, request):
        row = {"ts": str(int(clock[0].timestamp() * 1000))}
        if request.url.path == "/api/v5/market/books":
            assert request.url.params["instId"] == "BTC-USDT-SWAP"
            row.update(
                bids=[["9990", "20", "0", "1"]],
                asks=[["10010", "20", "0", "1"]],
                seqId=1,
            )
        return httpx.Response(200, request=request, json={"code": "0", "data": [row]})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", response)
    executor = ActionExecutionService(
        binding_case["db"],
        market_store=None,
        feature_root=binding_case["archive"].root,
        clock=lambda: clock[0],
        enabled=True,
        perpetual_execution=service,
    )
    assert executor.process_due()["errors"] == []

    fresh = query.read(OWNER, command.request_id)
    assert fresh["quote"]["status"] == "fresh"
    assert fresh["quote"]["bid"] == "9990" and fresh["quote"]["ask"] == "10010"
    assert fresh["quote"]["sampling_receipt"]["ref"].startswith("aqsr_")
    assert fresh["quote"]["capture_ref"]["ref"].startswith("raw_")
    assert fresh["quote"]["expires_at"] > fresh["quote"]["observed_local_lower"]

    api, _, _ = client_for(binding_case, clock=lambda: clock[0])
    reply = api.get(BASE + "/paper-execution/input-guide/" + command.request_id)
    assert reply.status_code == 200 and reply.json() == fresh

    clock[0] += timedelta(seconds=6)
    assert query.read(OWNER, command.request_id)["quote"]["status"] == "stale"
    with binding_case["db"].session() as session:
        assert PerpetualPaperRepository(session).current(OWNER, "manual-account") == ledger_before
        assert session.query(ExecutionIntentRecord).count() == intent_count_before
        generation = session.get(QuoteSamplingHead, "perp-book:okx:BTC-USDT-SWAP").generation
    # An unfilled preparation stops sampling when entry permission expires,
    # while the new holding protocol remains valid through the forecast target.
    clock[0] = latest_entry + timedelta(seconds=1)
    assert clock[0] < latest_exit
    assert executor.process_due()["errors"] == []
    with binding_case["db"].session() as session:
        head = session.get(QuoteSamplingHead, "perp-book:okx:BTC-USDT-SWAP")
        assert head.generation == generation
