"""Real temporary SQLite paper persistence; synthetic archived venue originals."""

import importlib
import importlib.util
import json
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal as D

import pytest
from sqlalchemy import func, select

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSettingsPartitionV1,
)
from packages.kernel.decision_hub_kernel.application.decision_execution_profile import (
    PROFILE_REF,
    paper_execution_profile_reference,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    ProductCommandRecord,
    RawCaptureRecord,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate
from packages.kernel.decision_hub_kernel.ports.action_quote import CaptureOriginal
from tests.decision_product.test_okx_perpetual_original import (
    INST,
    NOW,
    save_contract,
)
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)

MODULE = "packages.kernel.decision_hub_kernel.application.perpetual_paper"


def api():
    assert importlib.util.find_spec(MODULE) is not None, (
        "persistent perpetual paper service missing"
    )
    return importlib.import_module(MODULE)


@pytest.fixture
def runtime(persisted_original):
    db, archive, source, capture = persisted_original
    ref = save_contract(
        db,
        source,
        source.prepare_contract(
            inst_id=INST,
            capture_ids={"metadata": capture.capture_id},
            cutoff=NOW,
        ),
    )
    return db, archive, source, ref


def creation(runtime, request="create", cash="2500", account="practice"):
    _, _, _, ref = runtime
    return api().CreatePaperAccount(
        request_id=request,
        account_id=account,
        instrument_refs=(ref,),
        profile=paper_execution_profile_reference("okx:" + INST),
        initial_cash_usdt=D(cash),
        simulation_acknowledged=True,
        execution_mode="manual_after_visible",
    )


def service(runtime):
    db, _, source, _ = runtime
    return api().PerpetualPaperService(db, source, now=lambda: NOW)


def partition(account="practice"):
    return DecisionSettingsPartitionV1(
        account_kind="paper",
        account_id=account,
        instrument_id="okx:" + INST,
        profile_ref=PROFILE_REF,
    )


def test_explicit_creation_survives_reader_restart_and_is_owner_isolated(runtime):
    svc = service(runtime)
    with runtime[0].session() as session, pytest.raises(ValueError, match="account_missing"):
        svc.account_reader(session, "alice", partition())
    receipt = svc.create_account("alice", creation(runtime))
    assert receipt["state_version"] == 1
    with runtime[0].session() as session:
        account, position = service(runtime).account_reader(session, "alice", partition())
        assert account.equity == "2500" and account.available_margin == "2500"
        assert account.source == "paper_policy" and account.original is not None
        assert position.signed_contracts == "0" and position.state == "flat"
        assert position.instrument_ref == runtime[3]
        with pytest.raises(ValueError, match="account_missing"):
            svc.account_reader(session, "bob", partition())


def test_create_command_replay_conflict_and_no_automatic_default(runtime):
    svc = service(runtime)
    command = creation(runtime)
    first = svc.create_account("alice", command)
    assert svc.create_account("alice", command) == first
    with pytest.raises(ValueError, match="request_conflict"):
        svc.create_account("alice", creation(runtime, cash="2501"))
    with pytest.raises(ValueError, match="account_exists"):
        svc.create_account("alice", creation(runtime, request="different"))
    with pytest.raises(ValueError, match="simulation_ack"):
        svc.create_account(
            "alice",
            api().CreatePaperAccount(
                **{
                    **asdict(command),
                    "instrument_refs": command.instrument_refs,
                    "profile": command.profile,
                    "simulation_acknowledged": False,
                }
            ),
        )
    with runtime[0].session() as session:
        assert session.scalar(select(func.count()).select_from(ProductCommandRecord)) == 1


def book(
    runtime,
    *,
    bid="9990",
    ask="10010",
    size="20",
    capture_id="book",
    at=NOW,
    inst=INST,
    sequence=10,
):
    import hashlib

    mod = importlib.import_module("packages.kernel.decision_hub_kernel.ports.perpetual_quote")
    raw = json.dumps(
        {
            "code": "0",
            "data": [
                {
                    "bids": [[bid, size, "0", "1"]],
                    "asks": [[ask, size, "0", "1"]],
                    "ts": str(int(at.timestamp() * 1000)),
                    "seqId": sequence,
                }
            ],
        },
        separators=(",", ":"),
    ).encode()
    digest = hashlib.sha256(raw).hexdigest()
    capture = CaptureOriginal(
        capture_id,
        f"{mod.VERSION}:{inst}:" + "1" * 32,
        mod.public_url(inst),
        200,
        f"raw://sha256/{digest}",
        digest,
        len(raw),
        mod.VERSION,
        None,
        at,
        at,
        at,
    )
    runtime[1].put(raw)
    with runtime[0].session() as session:
        session.add(RawCaptureRecord(**asdict(capture), media_type="application/json"))
    return capture_id


def apply(runtime, *, side, phase="entry", version=1, qty="2", request="fill", quote="book"):
    svc = service(runtime)
    prepared = svc.prepare_quote(instrument_ref=runtime[3], capture_id=quote, cutoff=NOW)
    from packages.kernel.decision_hub_kernel.persistence.perpetual_paper_repository import (
        PerpetualPaperRepository,
    )

    with runtime[0].session() as session:
        begin_immediate(session)
        return PerpetualPaperRepository(session).apply_fill(
            prepared=prepared,
            owner_id="alice",
            account_id="practice",
            expected_version=version,
            request_id=request,
            phase=phase,
            order_side=side,
            requested_contracts=D(qty),
            fee_rate=D("0.001"),
            slippage_bps=D("0"),
            cost_assumption_ref="synthetic-cost-policy.v1",
            now=NOW,
        )


@pytest.mark.parametrize(
    "side,signed,price,fee",
    [
        ("buy", "2", "10010", "0.2002"),
        ("sell", "-2", "9990", "0.1998"),
    ],
)
def test_entry_preserves_contract_units_opposite_signs_and_distinct_fee_cash(
    runtime,
    side,
    signed,
    price,
    fee,
):
    service(runtime).create_account("alice", creation(runtime))
    book(runtime)
    value = apply(runtime, side=side)
    event = value["event"]
    assert event["filled_contracts"] == "2" and event["base_quantity"] == "0.02"
    assert event["fill_price"] == price and event["trade_fee_usdt"] == fee
    assert event["funding_cash_usdt"] is None and value["funding_complete"] is False
    position = value["positions"]["okx:" + INST]
    assert position["signed_contracts"] == signed
    assert D(position["isolated_margin"]) == D(price) * D("0.02")
    with runtime[0].session() as session:
        account, _ = service(runtime).account_reader(session, "alice", partition())
        assert account.equity is None and account.available_margin is None


def test_quote_capacity_is_cumulative_and_stale_cas_cannot_double_spend(runtime):
    service(runtime).create_account("alice", creation(runtime))
    book(runtime)
    first = apply(runtime, side="buy", qty="1.5")
    assert first["event"]["filled_contracts"] == "1.5"
    with pytest.raises(ValueError, match="state_conflict"):
        apply(runtime, side="buy", request="stale", qty="1", version=1)
    second = apply(runtime, side="buy", request="second", qty="1", version=2)
    assert second["event"]["filled_contracts"] == "0.5"
    assert apply(runtime, side="buy", qty="1.5") == first
    with pytest.raises(ValueError, match="request_conflict"):
        apply(runtime, side="sell", qty="1.5")


def test_close_uses_opposite_quote_does_not_add_notional_and_never_crosses_zero(runtime):
    service(runtime).create_account("alice", creation(runtime))
    book(runtime)
    apply(runtime, side="sell")
    with pytest.raises(ValueError, match="cross_zero"):
        apply(runtime, side="buy", phase="exit", qty="3", version=2, request="reverse")
    closed = apply(runtime, side="buy", phase="exit", version=2, request="close")
    # short base .02 * (9990 - 10010)=-.4, fees .1998 + .2002=.4.
    assert closed["trade_cash_usdt"] == "2499.2"
    assert closed["positions"]["okx:" + INST]["signed_contracts"] == "0"
    assert closed["event"]["gross_pnl_usdt"] == "-0.4"
    assert closed["funding_complete"] is False


def test_quote_bridge_rejects_stale_or_changed_archive_and_public_execution_is_unavailable(runtime):
    svc = service(runtime)
    svc.create_account("alice", creation(runtime))
    old = book(runtime, at=NOW - timedelta(seconds=6))
    with pytest.raises(ValueError, match="quote_stale"):
        svc.prepare_quote(instrument_ref=runtime[3], capture_id=old, cutoff=NOW)
    with pytest.raises(ValueError, match="authorization_unavailable"):
        svc.execute_action()


def test_insufficient_margin_fill_rolls_back(runtime):
    service(runtime).create_account("alice", creation(runtime, cash="100"))
    book(runtime)
    with pytest.raises(ValueError, match="insufficient_margin"):
        apply(runtime, side="buy")
    with runtime[0].session() as session:
        account, position = service(runtime).account_reader(session, "alice", partition())
        assert account.equity == "100" and position.signed_contracts == "0"


def test_changed_capture_after_preparation_rejects_write_and_forged_preparation(runtime):
    from dataclasses import replace

    from packages.kernel.decision_hub_kernel.application.perpetual_paper import (
        require_prepared_quote,
    )
    from packages.kernel.decision_hub_kernel.persistence.original_preparation import (
        OriginalPreparationReadsetChanged,
    )

    svc = service(runtime)
    book(runtime)
    prepared = svc.prepare_quote(instrument_ref=runtime[3], capture_id="book", cutoff=NOW)
    with runtime[0].session() as session:
        session.get(RawCaptureRecord, "book").status_code = 500
    with runtime[0].session() as session:
        begin_immediate(session)
        with pytest.raises(ValueError, match="unissued"):
            require_prepared_quote(session, replace(prepared))
        with pytest.raises(OriginalPreparationReadsetChanged):
            require_prepared_quote(session, prepared)


def test_books_use_contract_multiplier_and_recaptured_snapshot_cannot_replenish(runtime):
    from dataclasses import replace

    from tests.decision_product.test_okx_perpetual_original import capture, metadata_row

    db, archive, source, _ = runtime
    original, raw = capture("metadata", {**metadata_row(), "ctMult": "3"})
    original = replace(original, capture_id="multiplier-metadata")
    archive.put(raw)
    with db.session() as session:
        session.add(RawCaptureRecord(**asdict(original), media_type="application/json"))
    # A distinct test Run owns this different frozen contract version.
    from packages.kernel.decision_hub_kernel.persistence.db import RunRecord

    with db.session() as session:
        session.add(
            RunRecord(
                run_id="multiplier-run",
                event_id="test",
                status="running",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    prepared = source.prepare_contract(
        inst_id=INST, capture_ids={"metadata": original.capture_id}, cutoff=NOW
    )
    with db.session() as session:
        begin_immediate(session)

        def require_run(current):
            if current.get(RunRecord, "multiplier-run").status != "running":
                raise ValueError("test_run_not_running")

        ref = source.save_prepared_in_session(
            session, run_id="multiplier-run", prepared=prepared, writer_guard=require_run
        )
    variant = db, archive, source, ref
    service(variant).create_account("alice", creation(variant))
    book(variant)
    first = apply(variant, side="buy")
    assert first["event"]["base_quantity"] == "0.06"
    assert first["event"]["trade_fee_usdt"] == "0.6006"
    book(variant, capture_id="recaptured")
    with pytest.raises(ValueError, match="capacity_exhausted"):
        apply(variant, side="buy", quote="recaptured", version=2, request="again")


def test_account_head_tamper_and_immutable_original_mutation_are_rejected(runtime):
    from sqlalchemy import update
    from sqlalchemy.exc import IntegrityError

    from packages.kernel.decision_hub_kernel.persistence.perpetual_paper_records import (
        PerpetualPaperHeadRecord,
        PerpetualPaperRevisionRecord,
    )

    svc = service(runtime)
    svc.create_account("alice", creation(runtime))
    with pytest.raises(IntegrityError, match="immutable"):
        with runtime[0].session() as session:
            session.execute(update(PerpetualPaperRevisionRecord).values(payload_json="{}"))
    with runtime[0].session() as session:
        session.execute(update(PerpetualPaperHeadRecord).values(state_version=2))
    with runtime[0].session() as session, pytest.raises(ValueError, match="head_original"):
        svc.account_reader(session, "alice", partition())


def test_forged_head_cannot_project_another_owners_account(runtime):
    from sqlalchemy import update

    from packages.kernel.decision_hub_kernel.persistence.perpetual_paper_records import (
        PerpetualPaperHeadRecord,
    )

    svc = service(runtime)
    svc.create_account("alice", creation(runtime))
    other = svc.create_account("bob", creation(runtime, cash="999"))
    with runtime[0].session() as session:
        session.execute(
            update(PerpetualPaperHeadRecord)
            .where(PerpetualPaperHeadRecord.owner_id == "alice")
            .values(current_revision_id=other["revision"]["ref"])
        )
    with runtime[0].session() as session, pytest.raises(ValueError, match="head_original"):
        svc.account_reader(session, "alice", partition())


def test_btc_and_eth_share_one_margin_budget_and_account_cas(runtime):
    from dataclasses import replace

    from tests.decision_product.test_okx_perpetual_original import capture, metadata_row

    db, archive, source, ref = runtime
    eth = "ETH-USDT-SWAP"
    original, raw = capture("metadata", metadata_row(eth), eth)
    original = replace(original, capture_id="eth-metadata")
    archive.put(raw)
    with db.session() as session:
        session.add(RawCaptureRecord(**asdict(original), media_type="application/json"))
    ethref = save_contract(
        db,
        source,
        source.prepare_contract(
            inst_id=eth, capture_ids={"metadata": original.capture_id}, cutoff=NOW
        ),
    )
    command = replace(creation(runtime, cash="300"), instrument_refs=(ref, ethref))
    service(runtime).create_account("alice", command)
    book(runtime)
    apply(runtime, side="buy", qty="1")
    ethruntime = db, archive, source, ethref
    book(ethruntime, bid="999", ask="1001", capture_id="eth-book", inst=eth)
    second = apply(ethruntime, side="sell", qty="1", version=2, quote="eth-book", request="eth")
    assert second["state_version"] == 3
    assert second["positions"]["okx:" + INST]["isolated_margin"] == "100.1"
    assert second["positions"]["okx:" + eth]["isolated_margin"] == "99.9"
    with pytest.raises(ValueError, match="insufficient_margin"):
        apply(ethruntime, side="sell", qty="1", version=3, quote="eth-book", request="eth-extra")


def test_real_paper_reader_binds_settings_and_rejects_client_balance(runtime):
    from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
        DecisionSettingsCommandV1,
    )
    from packages.kernel.decision_hub_kernel.application.decision_settings import (
        DecisionSettingsService,
    )
    from packages.kernel.decision_hub_kernel.application.decision_settings_binding import (
        DecisionSettingsBindingReader,
    )

    svc = service(runtime)
    svc.create_account("alice", creation(runtime))
    with runtime[0].session() as session:
        account, position = svc.account_reader(session, "alice", partition())
    policy = dict(
        schema_version="utility-policy.v2",
        policy_ref="test-policy",
        hash="1" * 64,
        source="user",
        objective="mean_net",
        risk_penalty="0",
        max_gross_notional=None,
        max_planned_loss=None,
        tail_constraints=None,
        position_scenario="flat",
        margin_buffer_policy=None,
        allowed_leverage=None,
        allowed_sides=None,
        account_ref=account.original.model_dump(mode="json"),
    )
    body = dict(
        schema_version="decision-settings-command.v1",
        request_id="paper-settings",
        expected_state_version=0,
        operation="upsert",
        partition=partition().model_dump(mode="json"),
        values=dict(
            instrument=runtime[3].model_dump(mode="json"),
            execution_profile=paper_execution_profile_reference("okx:" + INST).model_dump(
                mode="json"
            ),
            account_snapshot=account.model_dump(mode="json"),
            position_context=position.model_dump(mode="json"),
            utility_policy=policy,
        ),
    )
    binding = DecisionSettingsBindingReader(
        runtime[2], now=lambda: NOW, paper_account_reader=svc.account_reader
    )
    settings = DecisionSettingsService(runtime[0], now=lambda: NOW, binding_reader=binding)
    saved = settings.save("alice", DecisionSettingsCommandV1.model_validate(body))
    assert saved.settings_saved is True
    body["request_id"] = "forged-paper-settings"
    body["expected_state_version"] = 1
    body["values"]["account_snapshot"]["equity"] = "9999"
    with pytest.raises(ValueError, match="paper_original_mismatch"):
        settings.save("alice", DecisionSettingsCommandV1.model_validate(body))


def test_frozen_migration_creates_real_tables_and_immutable_triggers(tmp_path):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.exc import IntegrityError

    path = "migrations/versions/0100_perpetual_paper.py"
    spec = importlib.util.spec_from_file_location("perp_paper_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine("sqlite:///" + (tmp_path / "migration.sqlite3").as_posix())
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
            assert set(inspect(connection).get_table_names()) == {
                "perpetual_paper_revisions",
                "perpetual_paper_heads",
                "perpetual_paper_quote_capacity",
            }
            connection.execute(
                text("INSERT INTO perpetual_paper_revisions VALUES ('r1','a1',1,'{}','hash')")
            )
        with pytest.raises(IntegrityError, match="immutable"):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE perpetual_paper_revisions SET payload_json = 'bad'")
                )
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                module.downgrade()
            assert inspect(connection).get_table_names() == []
    finally:
        engine.dispose()


def test_ledger_cash_does_not_depend_on_callers_decimal_precision(runtime):
    from decimal import localcontext

    service(runtime).create_account("alice", creation(runtime))
    book(runtime)
    with localcontext() as context:
        context.prec = 6
        result = apply(runtime, side="buy")
    assert result["trade_cash_usdt"] == "2499.7998"


def test_collection_rejects_spot_endpoint_before_network(runtime):
    import asyncio

    with pytest.raises(ValueError, match="instrument_unsupported"):
        asyncio.run(service(runtime).collect_quote(inst_id="BTC-USDT"))


def test_account_list_is_owner_scoped_and_creation_receipt_keeps_original_after_fill(runtime):
    svc = service(runtime)
    assert svc.list_accounts("alice") == ()
    command = creation(runtime)
    created = svc.create_account("alice", command)
    svc.create_account("bob", creation(runtime, cash="999"))
    book(runtime)
    apply(runtime, side="buy")
    listed = svc.list_accounts("alice")
    assert len(listed) == 1 and listed[0]["account_id"] == "practice"
    assert listed[0]["owner_id"] == "alice" and listed[0]["state_version"] == 2
    assert listed[0]["execution_enabled"] is False
    assert svc.creation_receipt("alice", "create", command) == created
    assert svc.creation_receipt("alice", "create", command)["state_version"] == 1
    with pytest.raises(ValueError, match="request_conflict"):
        svc.creation_receipt("alice", "create", creation(runtime, cash="2501"))
    with pytest.raises(ValueError, match="command_missing"):
        svc.creation_receipt("nobody", "create", command)


@pytest.mark.asyncio
async def test_real_capture_pipeline_retains_decodable_book_endpoint(runtime, monkeypatch):
    import httpx

    from packages.kernel.decision_hub_kernel.persistence.db import utcnow

    async def transport_response(transport, request):
        assert request.method == "GET" and request.url.path == "/api/v5/market/books"
        assert request.url.params["instId"] == INST
        return httpx.Response(
            200,
            request=request,
            json={
                "code": "0",
                "data": [
                    {
                        "bids": [["9990", "20", "0", "1"]],
                        "asks": [["10010", "20", "0", "1"]],
                        "ts": str(int(utcnow().timestamp() * 1000)),
                        "seqId": 123,
                    }
                ],
            },
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", transport_response)
    svc = service(runtime)
    capture_id = await svc.collect_quote(inst_id=INST)
    prepared = svc.prepare_quote(instrument_ref=runtime[3], capture_id=capture_id, cutoff=utcnow())
    assert prepared.value.book.bid_contracts == "20"
    assert prepared.value.book.ask == "10010"


def test_weighted_entry_remains_readable_when_exact_average_repeats(runtime):
    svc = service(runtime)
    svc.create_account("alice", creation(runtime))
    book(runtime)
    apply(runtime, side="buy", qty="1")
    book(runtime, ask="10000", size="40", capture_id="second-price", sequence=11)
    apply(runtime, side="buy", qty="2", quote="second-price", version=2, request="add")
    with runtime[0].session() as session:
        _, position = svc.account_reader(session, "alice", partition())
    assert position.signed_contracts == "3"
    assert position.entry_price == "10003.333333333333333333"
