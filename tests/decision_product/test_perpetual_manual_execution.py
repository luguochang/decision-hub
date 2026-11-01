"""Actual temporary SQLite manual-paper authority, never a financial qualification."""

from datetime import timedelta

import pytest

from tests.decision_product.test_okx_perpetual_original import NOW
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_paper import creation, service
from tests.decision_product.test_perpetual_paper import runtime as runtime
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case


def paper_publication(case):
    """Real paper-bound settings and pure persisted publication, not native review proof."""
    from copy import deepcopy
    from decimal import Decimal

    from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
        DecisionSettingsCommandV1,
    )
    from packages.kernel.decision_hub_kernel.application.decision_settings import (
        DecisionSettingsService,
    )
    from packages.kernel.decision_hub_kernel.application.decision_settings_binding import (
        DecisionSettingsBindingReader,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_execution_protocol import (
        PerpetualPaperProtocolService,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_paper import (
        CreatePaperAccount,
        PerpetualPaperService,
    )
    from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
    from tests.decision_product.test_perpetual_advice_query import current, persisted_publication
    from tests.decision_product.test_perpetual_research_binding import AT, OWNER

    paper = PerpetualPaperService(case["db"], case["source"], now=lambda: AT)
    old = case["settings"]
    paper.create_account(
        OWNER,
        CreatePaperAccount(
            request_id="create-paper",
            account_id="manual-account",
            instrument_refs=(old.values.instrument,),
            profile=old.values.execution_profile,
            initial_cash_usdt=Decimal("2500"),
            simulation_acknowledged=True,
            execution_mode="manual_after_visible",
        ),
    )
    body = deepcopy(old.model_dump(mode="json"))
    body.update(request_id="save-paper", expected_state_version=0)
    body["partition"].update(account_kind="paper", account_id="manual-account")
    from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
        DecisionSettingsPartitionV1,
    )

    partition = DecisionSettingsPartitionV1.model_validate(body["partition"])
    with case["db"].session() as session:
        account, position = paper.account_reader(session, OWNER, partition)
    body["values"].update(
        account_snapshot=account.model_dump(mode="json"),
        position_context=position.model_dump(mode="json"),
    )
    body["values"]["utility_policy"].update(
        account_ref=account.original.model_dump(mode="json"), position_scenario="flat"
    )
    policy = body["values"]["utility_policy"]
    policy["hash"] = canonical_hash({k: v for k, v in policy.items() if k != "hash"})
    settings = DecisionSettingsCommandV1.model_validate(body)
    settings_service = DecisionSettingsService(
        case["db"],
        now=lambda: AT,
        binding_reader=DecisionSettingsBindingReader(
            case["source"], now=lambda: AT, paper_account_reader=paper.account_reader
        ),
    )
    settings_service.save(OWNER, settings)
    case.update(settings=settings, settings_service=settings_service)
    # Produce the real frozen Task/catalog without invoking native/model/Graph.
    # Only the pure publication review packet is synthetic in this direct gate.
    from pathlib import Path
    from unittest.mock import patch

    from tests.decision_product.test_perpetual_action_publication import lawful_packet_for
    from tests.decision_product.test_perpetual_native_graph import prepare
    from tests.decision_product.test_perpetual_research_binding import factory_for

    task_root = Path(case["db"].engine.url.database).parent / "manual-task"
    task_root.mkdir(exist_ok=True)
    factory, _ = factory_for(case, task_root)
    request = factory.build(case["event_id"], case["run_id"])
    _, actual_materials = prepare(case, request)

    def packet_with_real_catalog(request, materials):
        return lawful_packet_for(
            request, {**materials, "review_originals": actual_materials["review_originals"]}
        )

    with patch(
        "tests.decision_product.test_perpetual_action_publication.lawful_packet_for",
        packet_with_real_catalog,
    ):
        derived = persisted_publication(case)
    at = AT + timedelta(seconds=40)
    view = current(case, now=at)
    protocols = PerpetualPaperProtocolService(case["db"], case["source"], clock=lambda: at)
    protocol = protocols.register_manual(
        owner_id=OWNER,
        account_id="manual-account",
        trial_id="unit-manual",
        start_at=at,
        end_at=derived.bound.target.window_end,
    )
    with case["db"].session() as session:
        from packages.kernel.decision_hub_kernel.persistence.action_repository import (
            ActionRepository,
        )
        from packages.kernel.decision_hub_kernel.persistence.perpetual_paper_repository import (
            PerpetualPaperRepository,
        )

        head = ActionRepository(session).get_head(view["revision"]["plan_id"])
        ledger = PerpetualPaperRepository(session).current(OWNER, "manual-account")
    identity = dict(
        partition=view["partition"],
        settings_revision=view["settings_revision"],
        settings_state_version=view["settings_state_version"],
        revision={"ref": view["revision"]["revision_id"], "hash": view["revision"]["payload_hash"]},
        expected_plan_state_version=head.state_version,
        account_revision=ledger["revision"],
        account_state_version=ledger["state_version"],
        protocol_ref=protocol.reference.model_dump(mode="json"),
    )
    return paper, protocols, at, identity, view


def accepted_intent(binding_case, *, exit_after=timedelta(minutes=5)):
    import importlib.util

    module = "packages.kernel.decision_hub_kernel.application.perpetual_manual_execution"
    assert importlib.util.find_spec(module), "manual authority service is missing"
    from packages.kernel.decision_hub_kernel.application.perpetual_manual_execution import (
        PerpetualManualPaperService,
    )
    from packages.kernel.decision_hub_kernel.ports.perpetual_manual_execution import (
        ManualDisplayCommand,
        ManualExecutionCommand,
    )
    from tests.decision_product.test_perpetual_research_binding import OWNER

    paper, protocols, at, identity, view = paper_publication(binding_case)
    clock = [at]
    svc = PerpetualManualPaperService(binding_case["db"], paper, protocols, clock=lambda: clock[0])
    display_command = ManualDisplayCommand(
        schema_version="perpetual-manual-display.v1", request_id="visible", **identity
    )
    shown = svc.record_display(OWNER, display_command)
    clock[0] += timedelta(milliseconds=10)
    command = ManualExecutionCommand(
        schema_version="perpetual-manual-intent-command.v1",
        request_id="confirm",
        **identity,
        display_receipt={"ref": shown.request_id, "hash": shown.receipt_hash},
        max_entry_notional_usdt="80",
        stop_price_usdt="9000",
        take_profit_price_usdt="12000",
        exit_at=(at + exit_after).isoformat(),
        simulation_acknowledged=True,
    )
    accepted = svc.submit_intent(OWNER, command)
    return svc, clock, command, accepted, view


def test_signed_manual_intent_persists_exact_user_policy_and_original_replay(binding_case):
    from tests.decision_product.test_perpetual_research_binding import OWNER

    svc, clock, command, accepted, view = accepted_intent(binding_case)
    assert accepted.response["status"] == "queued"
    assert (
        accepted.response["native_reviewed"] is False
        and accepted.response["financial_qualified"] is False
    )
    assert accepted.response["command"]["max_entry_notional_usdt"] == "80"
    assert (
        accepted.response["intent"]["candidate_id"]
        == view["revision"]["selected_candidate"]["candidate_id"]
    )
    assert svc.submit_intent(OWNER, command) == accepted
    assert svc.read_command(OWNER, "confirm", accepted.payload_hash) == accepted
    with pytest.raises(ValueError, match="conflict"):
        svc.submit_intent(OWNER, command.model_copy(update={"max_entry_notional_usdt": "81"}))
    with pytest.raises((ValueError, PermissionError)):
        svc.submit_intent("foreign-owner", command.model_copy(update={"request_id": "foreign"}))
    from packages.kernel.decision_hub_kernel.persistence.action_execution_records import (
        ExecutionIntentRecord,
    )

    with binding_case["db"].session() as session:
        row = session.get(ExecutionIntentRecord, accepted.response["intent"]["id"])
        assert row is not None and row.portfolio_id == accepted.response["intent"]["account_key"]
        assert row.phase == "entry"


def test_normal_queue_first_eligible_fill_exit_and_incomplete_outcome(binding_case, monkeypatch):
    normal_queue_lifecycle(binding_case, monkeypatch)


def normal_queue_lifecycle(
    binding_case, monkeypatch, *, return_runtime=False, keep_open=False, exit_after=None
):
    from datetime import datetime
    from decimal import Decimal
    from time import perf_counter

    import httpx

    from packages.kernel.decision_hub_kernel.application import perpetual_paper
    from packages.kernel.decision_hub_kernel.application.action_execution import (
        ActionExecutionService,
    )
    from packages.kernel.decision_hub_kernel.persistence.perpetual_paper_repository import (
        PerpetualPaperRepository,
    )
    from packages.provider_adapters.research import raw_capture
    from tests.decision_product.test_perpetual_research_binding import OWNER

    svc, clock, command, accepted, view = accepted_intent(
        binding_case, exit_after=exit_after or timedelta(minutes=5)
    )
    instrument_id = command.partition.instrument_id
    instrument = instrument_id.removeprefix("okx:")
    assert hasattr(svc, "execute_intent"), "normal queue manual-perp executor is missing"
    timings = []
    execute = svc.execute_intent

    def timed_execute(intent_id, **kwargs):
        started = perf_counter()
        result = execute(intent_id, **kwargs)
        elapsed = perf_counter() - started
        timings.append(
            dict(
                intent_id=intent_id,
                elapsed_seconds=elapsed,
                status=result["status"] if result else "pending",
            )
        )
        print("MANUAL_EXECUTE_WALL", timings[-1], flush=True)
        if result and result["status"] == "filled":
            assert elapsed < 5, "authentication+writer exceed the unchanged quote age budget"
        return result

    monkeypatch.setattr(svc, "execute_intent", timed_execute)

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
    prices = ["9990", "10010"]
    calls = []

    async def response(transport, request):
        calls.append(request.url.path)
        row = {"ts": str(int(clock[0].timestamp() * 1000))}
        if request.url.path == "/api/v5/market/books":
            assert request.url.params["instId"] == instrument
            row.update(
                bids=[[prices[0], "20", "0", "1"]],
                asks=[[prices[1], "20", "0", "1"]],
                seqId=len(calls),
            )
        return httpx.Response(200, request=request, json={"code": "0", "data": [row]})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", response)
    executor = ActionExecutionService(
        binding_case["db"],
        market_store=None,
        feature_root=binding_case["archive"].root,
        clock=lambda: clock[0],
        enabled=True,
        perpetual_execution=svc,
    )
    intent_id = accepted.response["intent"]["id"]
    assert accepted.response["intent"]["scope_id"] == "perp-book:" + instrument_id
    if instrument == "ETH-USDT-SWAP":
        from packages.kernel.decision_hub_kernel.persistence.perpetual_execution_adapter import (
            PerpetualExecutionAdapter,
        )

        with binding_case["db"].session() as session:
            original = PerpetualExecutionAdapter(session).intent(intent_id)
            with pytest.raises(ValueError, match="scope"):
                svc._authority(
                    session, original.model_copy(update={"scope_id": "perp-book:okx:BTC-USDT-SWAP"})
                )
        with pytest.raises(ValueError, match="already_active"):
            svc.submit_intent(
                OWNER, command.model_copy(update={"request_id": "second-account-intent"})
            )
    eligible = datetime.fromisoformat(accepted.response["intent"]["eligible_at"])
    clock[0] = eligible - timedelta(milliseconds=400)
    assert executor.process_due()["errors"] == []
    assert svc.read_execution(OWNER, intent_id)["receipt"] is None
    clock[0] = eligible.replace(second=56, microsecond=200000)
    result = executor.process_due()
    assert result["errors"] == [], result
    read = svc.read_execution(OWNER, intent_id)
    assert read["receipt"]["status"] == "filled"
    assert read["fill"]["quote_capture_id"] is not None
    assert read["fill"]["fill_price_usdt"] == "10012.1"  # Existing adverse tick-up math.
    assert Decimal(read["fill"]["filled_contracts"]) > 0
    if instrument == "ETH-USDT-SWAP":
        assert read["fill"]["filled_contracts"] == "0.07"  # 0.1 ETH per contract, not BTC 0.01.
    assert Decimal(read["fill"]["filled_notional_usdt"]) <= Decimal("80")
    assert Decimal(read["fill"]["trade_fee_usdt"]) == Decimal(
        read["fill"]["filled_notional_usdt"]
    ) * Decimal("0.0005")
    with binding_case["db"].session() as session:
        ledger = PerpetualPaperRepository(session).current(OWNER, "manual-account")
    assert ledger["revision"] == read["fill"]["ledger_revision"] and ledger["state_version"] == 2
    assert len(read["receipt"]["quote_selection_proof"]) == 2  # early book + first eligible
    assert svc.execute_intent(intent_id) == read["receipt"]
    if keep_open:
        return dict(
            wire=dict(command=command.model_dump(mode="json"), execution=read),
            service=svc,
            clock=clock,
            executor=executor,
            prices=prices,
            intent_id=intent_id,
        )
    # Trigger uses executable bid, then a later quote after the reaction delay.
    prices[:] = ["12300", "12310"]
    clock[0] = eligible.replace(minute=1, second=2, microsecond=200000)
    result = executor.process_due()
    assert result["errors"] == [], result
    assert svc.read_execution(OWNER, intent_id)["episode"]["remaining_contracts"] != "0"
    prices[:] = ["12290", "12300"]
    clock[0] = eligible.replace(minute=1, second=8, microsecond=200000)
    result = executor.process_due()
    assert result["errors"] == [], result
    final = svc.read_execution(OWNER, intent_id)
    assert final["episode"]["remaining_contracts"] == "0"
    assert final["outcome"]["status"] == "unscorable"
    assert (
        final["outcome"]["funding_cash_usdt"] is None and final["outcome"]["net_pnl_usdt"] is None
    )
    with binding_case["db"].session() as session:
        ledger = PerpetualPaperRepository(session).current(OWNER, "manual-account")
    assert ledger["positions"][instrument_id]["signed_contracts"] == "0"
    if instrument == "ETH-USDT-SWAP":
        assert "okx:BTC-USDT-SWAP" not in ledger["positions"]
    assert ledger["state_version"] == 3 and ledger["funding_complete"] is False
    assert calls == ["/api/v5/public/time", "/api/v5/market/books", "/api/v5/public/time"] * 4
    assert sum(item["status"] == "filled" for item in timings) >= 2
    wire = dict(command=command.model_dump(mode="json"), execution=final)
    if return_runtime:
        return wire, svc, clock, executor
    return wire


def test_holding_window_outlives_entry_permission_and_fill_rechecks_current_time(binding_case):
    from datetime import datetime

    from packages.kernel.decision_hub_kernel.application.perpetual_manual_execution import (
        PerpetualManualPaperService,
    )
    from packages.kernel.decision_hub_kernel.ports.perpetual_manual_execution import (
        ManualDisplayCommand,
        ManualExecutionCommand,
    )
    from tests.decision_product.test_perpetual_research_binding import OWNER

    paper, protocols, at, identity, view = paper_publication(binding_case)
    latest_entry = datetime.fromisoformat(view["revision"]["valid_until"])
    latest_exit = datetime.fromisoformat(view["target"]["window_end"])
    assert at < latest_entry < latest_exit
    clock = [at]
    service = PerpetualManualPaperService(
        binding_case["db"], paper, protocols, clock=lambda: clock[0]
    )
    display = ManualDisplayCommand(
        schema_version="perpetual-manual-display.v1",
        request_id="holding-window-visible",
        **identity,
    )
    shown = service.record_display(OWNER, display)
    clock[0] = latest_entry - timedelta(seconds=2)
    command = ManualExecutionCommand(
        schema_version="perpetual-manual-intent-command.v1",
        request_id="holding-window-confirm",
        **identity,
        display_receipt={"ref": shown.request_id, "hash": shown.receipt_hash},
        max_entry_notional_usdt="80",
        stop_price_usdt="9000",
        take_profit_price_usdt="12000",
        exit_at=(latest_exit - timedelta(seconds=1)).isoformat(),
        simulation_acknowledged=True,
    )
    accepted = service.submit_intent(OWNER, command)
    intent = accepted.response["intent"]
    assert datetime.fromisoformat(intent["quote_deadline"]) == latest_entry
    assert datetime.fromisoformat(accepted.response["command"]["exit_at"]) > latest_entry

    # accepted_at is historical evidence only. At the exact entry cutoff the
    # normal executor must record a no-fill before reading or applying a quote.
    clock[0] = latest_entry
    result = service.execute_intent(intent["id"])
    assert result["status"] == "no_fill"
    assert result["reason"] == "entry_permission_elapsed"


def test_open_episode_advances_authenticated_sampling_cursor_beyond_512_and_still_exits(
    binding_case, monkeypatch
):
    from packages.kernel.decision_hub_kernel.application.action_quote_sampling import (
        claim_scope,
        finish_scope,
    )
    from packages.kernel.decision_hub_kernel.persistence.action_quote_sampling_records import (
        QuoteSamplingHead,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import as_utc
    from packages.kernel.decision_hub_kernel.persistence.perpetual_execution_adapter import (
        PerpetualExecutionAdapter,
    )
    from packages.kernel.decision_hub_kernel.ports.perpetual_manual_execution import (
        PerpetualSamplingScope,
        manual_scope,
    )
    from tests.decision_product.test_perpetual_research_binding import OWNER

    runtime = normal_queue_lifecycle(
        binding_case,
        monkeypatch,
        keep_open=True,
        exit_after=timedelta(hours=3),
    )
    service = runtime["service"]
    clock = runtime["clock"]
    intent_id = runtime["intent_id"]
    with binding_case["db"].session() as session:
        intent = PerpetualExecutionAdapter(session).intent(intent_id)
        before = PerpetualExecutionAdapter(session).episode(intent.account_key)[0]
    initial_generation = before["sampling_generation"]
    scope = PerpetualSamplingScope(
        manual_scope(intent.scope_id.removeprefix("perp-book:okx:")),
        intent.scope_id.removeprefix("perp-book:okx:"),
    )
    scope.validate()

    def append_failed(count):
        with binding_case["db"].session() as session:
            head = session.get(QuoteSamplingHead, scope.scope_id, populate_existing=True)
            at = max(clock[0], as_utc(head.next_due_at))
            for number in range(count):
                at += timedelta(seconds=5, milliseconds=1)
                claim = claim_scope(
                    session,
                    scope=scope,
                    owner_id="cursor-gate",
                    now=at,
                )
                assert claim is not None, number
                finish_scope(
                    session,
                    claim,
                    now=at + timedelta(milliseconds=1),
                    event="failed",
                    error="bounded-cursor-fixture",
                )
            clock[0] = at + timedelta(milliseconds=2)
            return session.get(QuoteSamplingHead, scope.scope_id).generation

    generation_512 = append_failed(512)
    assert service.observe_episode(intent.account_key) is None
    with binding_case["db"].session() as session:
        advanced = PerpetualExecutionAdapter(session).episode(intent.account_key)[0]
    assert advanced["sampling_generation"] == generation_512
    assert generation_512 - initial_generation == 512

    generation_513 = append_failed(1)
    assert service.observe_episode(intent.account_key) is None
    with binding_case["db"].session() as session:
        advanced = PerpetualExecutionAdapter(session).episode(intent.account_key)[0]
    assert advanced["sampling_generation"] == generation_513

    runtime["prices"][:] = ["12300", "12310"]
    clock[0] += timedelta(seconds=6)
    assert runtime["executor"].process_due()["errors"] == []
    clock[0] += timedelta(seconds=6)
    assert runtime["executor"].process_due()["errors"] == []
    final = service.read_execution(OWNER, intent_id)
    assert final["episode"]["remaining_contracts"] == "0"
    assert final["episode"]["exit_latched_reason"] == "target"


@pytest.mark.parametrize("settlement_count", [0, 1, "legacy"])
def test_closed_episode_reconciles_only_complete_zero_settlement_coverage(
    binding_case, monkeypatch, settlement_count
):
    """A closed account is reusable only when OKX proves no settlement in its hold interval."""
    import httpx

    from packages.kernel.decision_hub_kernel.application.perpetual_funding_history_source import (
        PerpetualFundingHistorySource,
    )
    from packages.kernel.decision_hub_kernel.persistence.perpetual_execution_adapter import (
        PerpetualExecutionAdapter,
    )
    from packages.kernel.decision_hub_kernel.persistence.perpetual_paper_repository import (
        PerpetualPaperRepository,
    )
    from tests.decision_product.test_perpetual_research_binding import OWNER

    wire, svc, clock, executor = normal_queue_lifecycle(
        binding_case, monkeypatch, return_runtime=True
    )
    clock[0] += timedelta(seconds=2)
    history_calls = 0

    async def funding_response(transport, request):
        nonlocal history_calls
        if request.url.path == "/api/v5/public/time":
            return httpx.Response(
                200,
                request=request,
                json={"code": "0", "data": [{"ts": str(int(clock[0].timestamp() * 1000))}]},
            )
        assert request.url.path == "/api/v5/public/funding-rate-history"
        assert request.url.params["limit"] == "400"
        assert request.url.params["before"] and request.url.params["after"]
        history_calls += 1
        rows = []
        if settlement_count and history_calls == 1:
            lower = int(request.url.params["before"]) + 1
            upper = int(request.url.params["after"]) - 1
            rows = [
                {
                    "instId": wire["command"]["partition"]["instrument_id"].removeprefix("okx:"),
                    "instType": "SWAP",
                    "fundingTime": str((lower + upper) // 2),
                    "fundingRate": "0.0001",
                    "realizedRate": "0.0001",
                    "method": "current_period",
                    "formulaType": "withRate",
                }
            ]
        return httpx.Response(200, request=request, json={"code": "0", "data": rows})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", funding_response)
    svc.funding_history = PerpetualFundingHistorySource(binding_case["db"], binding_case["archive"])
    if settlement_count == 0:
        from packages.kernel.decision_hub_kernel.persistence.action_execution_records import (
            ExecutionEpisodeHeadRecord,
        )

        async def unavailable(**kwargs):
            raise OSError("focused offline source failure")

        with monkeypatch.context() as source_failure:
            source_failure.setattr(svc.funding_history, "collect_coverage", unavailable)
            failed = executor.process_due()
        assert failed["errors"][0]["reason"] == "perp_funding_source_temporarily_unavailable"
        with binding_case["db"].session() as session:
            adapter = PerpetualExecutionAdapter(session)
            account_key = wire["execution"]["intent"]["account_key"]
            deferred, _ = adapter.episode(account_key)
            head = session.get(ExecutionEpisodeHeadRecord, account_key, populate_existing=True)
            assert deferred["funding_status"] == "pending_source_retry"
            assert deferred["funding_retry_count"] == 1
            assert head.next_due_at is not None
            assert head.next_due_at.replace(tzinfo=clock[0].tzinfo) > clock[0]
        assert executor.process_due()["errors"] == []
        assert history_calls == 0
        clock[0] += timedelta(minutes=5, seconds=1)
    if settlement_count == "legacy":
        from packages.kernel.decision_hub_kernel.persistence.action_execution_records import (
            ExecutionEpisodeHeadRecord,
        )
        from packages.kernel.decision_hub_kernel.persistence.original_preparation import (
            begin_immediate,
        )

        with binding_case["db"].session() as session:
            begin_immediate(session)
            adapter = PerpetualExecutionAdapter(session)
            old_body, old_ref = adapter.episode(wire["execution"]["intent"]["account_key"])
            legacy = dict(old_body)
            legacy.pop("initial_ledger_revision")
            legacy["version"] += 1
            entry = adapter.intent(old_body["entry_intent_id"])
            adapter.append_episode(
                entry, legacy, now=clock[0], expected_version=old_body["version"]
            )
            before_ledger = PerpetualPaperRepository(session).current(OWNER, "manual-account")
        result = executor.process_due()
        assert result["errors"][0]["reason"] == "perp_funding_legacy_initial_revision_missing"
        assert history_calls == 0 and result["funding_reconciled"] == 0
        with binding_case["db"].session() as session:
            adapter = PerpetualExecutionAdapter(session)
            parked, _ = adapter.episode(entry.account_key)
            head = session.get(
                ExecutionEpisodeHeadRecord, entry.account_key, populate_existing=True
            )
            assert parked["funding_status"] == "blocked_legacy_inputs"
            assert parked["funding_gaps"] == ["perp_funding_legacy_initial_revision_missing"]
            assert head.next_due_at is None
            assert (
                PerpetualPaperRepository(session).current(OWNER, "manual-account") == before_ledger
            )
            assert "initial_ledger_revision" not in legacy
        assert svc.read_execution(OWNER, entry.id)["outcome"]["status"] == "unscorable"
        return
    result = executor.process_due()
    assert result["errors"] == [], result

    command = wire["command"]
    with binding_case["db"].session() as session:
        ledger = PerpetualPaperRepository(session).current(OWNER, "manual-account")
        account, position = svc.paper.account_reader(
            session,
            OWNER,
            __import__(
                "packages.contracts_py.decision_hub_contracts.generated.decision_product_v2",
                fromlist=["DecisionSettingsPartitionV1"],
            ).DecisionSettingsPartitionV1.model_validate(command["partition"]),
        )
        if settlement_count == 0:
            PerpetualExecutionAdapter(session).require_idle(ledger["account_key"])

    execution = svc.read_execution(OWNER, wire["execution"]["intent"]["id"])
    if settlement_count == 0:
        assert result["funding_reconciled"] == 1
        assert history_calls == 1  # the first page is already the authenticated terminal empty page
        assert ledger["funding_complete"] is True
        assert ledger["event"]["kind"] == "paper_funding_reconciled"
        assert ledger["event"]["funding_cash_usdt"] == "0"
        assert account.available_margin == ledger["trade_cash_usdt"]
        assert position.state == "flat"
        assert execution["outcome"]["status"] == "settled"
        assert execution["outcome"]["funding_cash_usdt"] == "0"
        assert execution["outcome"]["net_pnl_usdt"] == execution["outcome"]["trade_cash_delta_usdt"]
        repeated = executor.process_due()
        assert repeated["funding_reconciled"] == 0
        with binding_case["db"].session() as session:
            assert (
                PerpetualPaperRepository(session).current(OWNER, "manual-account")["state_version"]
                == ledger["state_version"]
            )
        # Persist a domain-only next episode through the real account head CAS.
        # This does not claim a second fresh research/publication acceptance.
        from types import SimpleNamespace

        from packages.kernel.decision_hub_kernel.persistence.action_execution_records import (
            ExecutionEpisodeHeadRecord,
        )
        from packages.kernel.decision_hub_kernel.persistence.original_preparation import (
            begin_immediate,
        )

        previous = execution["episode"]
        next_id, head_version, clean_body = svc._episode_predecessor(
            (previous, {}), SimpleNamespace(id="domain-second-entry", phase="entry")
        )
        assert clean_body == {} and head_version == previous["version"]
        next_episode = dict(
            clean_body,
            episode_id=next_id,
            version=head_version + 1,
            owner_id=OWNER,
            account_id="manual-account",
            entry_intent_id="domain-second-entry",
            command_json="domain-only-new-policy",
            remaining_contracts="1",
            target_side="long",
            entry_at=clock[0].isoformat(),
            initial_ledger_revision=ledger["revision"],
            ledger_revision=ledger["revision"],
            gross_pnl_usdt="0",
            trade_fees_usdt="0",
            outcome_id=None,
            updated_at=clock[0].isoformat(),
        )
        with binding_case["db"].session() as session:
            begin_immediate(session)
            adapter = PerpetualExecutionAdapter(session)
            old_entry = adapter.intent(wire["execution"]["intent"]["id"])
            adapter.append_episode(
                old_entry, next_episode, now=clock[0], expected_version=head_version
            )
            head = session.get(
                ExecutionEpisodeHeadRecord, ledger["account_key"], populate_existing=True
            )
            assert head.episode_id == next_id and head.version == head_version + 1
        old_read = svc.read_execution(OWNER, old_entry.id)
        assert old_read["receipt"] == execution["receipt"]
        assert old_read["episode"] == previous
        assert old_read["outcome"] == execution["outcome"]
        assert not any(
            key.startswith("exit_") or key.startswith("funding_") for key in next_episode
        )
    else:
        assert result["funding_reconciled"] == 0
        assert history_calls == 2  # data page plus an authenticated terminal empty page
        assert ledger["funding_complete"] is False
        assert account.available_margin is None
        assert execution["outcome"]["status"] == "unscorable"
        assert execution["outcome"]["funding_cash_usdt"] is None
        assert execution["outcome"]["net_pnl_usdt"] is None


def test_manual_protocol_is_explicit_version_in_same_registry(runtime):
    from packages.kernel.decision_hub_kernel.application.perpetual_execution_protocol import (
        PerpetualPaperProtocolService,
    )
    from packages.kernel.decision_hub_kernel.persistence.action_execution_repository import (
        ExecutionRepository,
    )

    service(runtime).create_account("alice", creation(runtime))
    db, _, source, _ = runtime
    protocols = PerpetualPaperProtocolService(db, source, clock=lambda: NOW)
    assert hasattr(protocols, "register_manual"), (
        "manual paper must have an explicit protocol version"
    )
    value = protocols.register_manual(
        owner_id="alice",
        account_id="practice",
        trial_id="manual",
        start_at=NOW,
        end_at=NOW + timedelta(hours=4),
    )
    assert value.schema_version == "perp-manual-paper-protocol.v1"
    assert value.execution_authority is False and value.native_reviewed is False
    assert value.policy_authority == "user_explicit_paper_simulation"
    assert value.required_user_originals == ("manual_size", "manual_exit")
    assert value.required_reviewed_originals == ()
    with db.session() as session:
        assert protocols.read_in_session(session, value.reference) == value
        assert ExecutionRepository(session).perpetual_manual_protocol(value.id) == value
        with pytest.raises(ValueError, match="version"):
            ExecutionRepository(session).perpetual_protocol(value.id)
        with pytest.raises(ValueError, match="version"):
            ExecutionRepository(session).protocol(value.id)


def test_manual_signed_boundary_requires_explicit_preparation_and_original_readback(
    binding_case, monkeypatch
):
    monkeypatch.setenv("DECISION_HUB_ACTION_PRODUCT_ENABLED", "1")
    monkeypatch.setenv("DECISION_HUB_ACTION_EXECUTION_ENABLED", "1")
    import importlib.util

    module = "apps.hub_api.perpetual_manual_paper_routes"
    assert importlib.util.find_spec(module), "signed manual-paper boundary is missing"
    from tests.decision_product.test_perpetual_research_entry import BASE, client_for

    paper, protocols, at, identity, view = paper_publication(binding_case)
    clock = [at]
    client, _, collector = client_for(binding_case, clock=lambda: clock[0])
    anonymous, _, _ = client_for(binding_case, pair=False, clock=lambda: clock[0])
    body = dict(
        schema_version="perpetual-manual-prepare.v1",
        request_id="pc-prepare",
        **{
            k: identity[k]
            for k in ("partition", "settings_revision", "settings_state_version", "revision")
        },
        horizon="4h",
    )
    route = BASE + "/paper-execution"
    assert client.get(route + "/capabilities").json()["configured"] is True
    monkeypatch.setenv("DECISION_HUB_ACTION_EXECUTION_ENABLED", "0")
    assert (
        client.post(
            route + "/prepare", json=body, headers={"Idempotency-Key": body["request_id"]}
        ).status_code
        == 409
    )
    assert client.get(route + "/capabilities").json()["configured"] is False
    monkeypatch.setenv("DECISION_HUB_ACTION_EXECUTION_ENABLED", "1")
    assert (
        anonymous.post(
            route + "/prepare", json=body, headers={"Idempotency-Key": body["request_id"]}
        ).status_code
        == 403
    )
    prepared = client.post(
        route + "/prepare", json=body, headers={"Idempotency-Key": body["request_id"]}
    )
    assert prepared.status_code == 200, prepared.text
    assert prepared.json()["response"]["status"] == "prepared"
    assert client.get(route + "/commands/pc-prepare").json() == prepared.json()
    assert collector.calls == 0
    context = prepared.json()["response"]["context"]
    clock[0] += timedelta(seconds=3)
    display = dict(schema_version="perpetual-manual-display.v1", request_id="pc-display", **context)
    shown = client.post(
        route + "/display", json=display, headers={"Idempotency-Key": display["request_id"]}
    )
    assert shown.status_code == 200, shown.text
    intent = dict(
        schema_version="perpetual-manual-intent-command.v1",
        request_id="pc-confirm",
        **context,
        display_receipt={"ref": "pc-display", "hash": shown.json()["receipt_hash"]},
        max_entry_notional_usdt="80",
        stop_price_usdt="9000",
        take_profit_price_usdt="12000",
        exit_at=(at + timedelta(minutes=5)).isoformat(),
        simulation_acknowledged=True,
    )
    assert (
        client.post(
            route + "/intents",
            json={**intent, "verified_balance": "999999"},
            headers={"Idempotency-Key": intent["request_id"]},
        ).status_code
        == 422
    )
    saved = client.post(
        route + "/intents", json=intent, headers={"Idempotency-Key": intent["request_id"]}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["response"]["status"] == "queued"
    assert client.get(route + "/commands/pc-confirm").json() == saved.json()
    state = client.get(route + "/intents/" + saved.json()["response"]["intent"]["id"])
    assert state.status_code == 200 and state.json()["receipt"] is None
    assert collector.calls == 0  # signed commands do not synchronously collect a fill


def test_new_entry_uses_fresh_episode_body_with_monotonic_head_version():
    """Domain transition only; full fresh publication remains a separate UI gate."""
    from types import SimpleNamespace

    from packages.kernel.decision_hub_kernel.application.perpetual_manual_execution import (
        PerpetualManualPaperService,
    )
    from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash

    old = dict(
        episode_id="old-episode",
        version=7,
        entry_intent_id="old-entry",
        entry_at="old-time",
        exit_latched_reason="old-take-profit",
        funding_status="complete_zero_settlements",
        trade_fees_usdt="9",
        initial_ledger_revision={"ref": "old", "hash": "a" * 64},
    )
    new = SimpleNamespace(id="new-entry", phase="entry")
    episode_id, version, body = PerpetualManualPaperService._episode_predecessor((old, {}), new)
    assert episode_id == "perp_episode_" + canonical_hash(new.id)
    assert version == 7 and body == {}
    assert old["entry_intent_id"] == "old-entry"
    exiting = SimpleNamespace(id="exit-intent", phase="exit")
    assert PerpetualManualPaperService._episode_predecessor((old, {}), exiting) == (
        "old-episode",
        7,
        old,
    )
