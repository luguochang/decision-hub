"""Finite event consumer controls in an isolated DB; no provider/model/account.

The native future/missing-input path uses preserved CPI source originals. The
price-trigger control supplies an explicitly engineered already-authenticated
event preparation, then uses real capture/store/features/DSL/outbox transactions.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from packages.kernel.decision_hub_kernel.application import event_opportunity_monitor as monitor
from packages.kernel.decision_hub_kernel.application.event_realization import (
    freeze_event_realization_in_session,
)
from packages.kernel.decision_hub_kernel.application.market_path_store import MarketPathStore
from packages.kernel.decision_hub_kernel.application.outbox import (
    NotificationDispatcher,
    OutboxService,
)
from packages.kernel.decision_hub_kernel.application.research_graph_binding import _ReadSourceGuard
from packages.kernel.decision_hub_kernel.decision.action_research_policy import (
    research_action_catalog,
)
from packages.kernel.decision_hub_kernel.decision.event_opportunity_policy import (
    event_policy_catalog,
)
from packages.kernel.decision_hub_kernel.decision.event_opportunity_v2 import (
    build_event_opportunity_v2,
)
from packages.kernel.decision_hub_kernel.persistence import original_preparation as preparation
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    OutboxRecord,
    RawCaptureRecord,
)
from packages.kernel.decision_hub_kernel.persistence.event_monitor_records import (
    EventOpportunityCommandRecord,
    EventOpportunityEvaluationRecord,
)
from packages.kernel.decision_hub_kernel.ports.event_macro_context import EventMacroContext
from packages.kernel.decision_hub_kernel.ports.event_response_path import EventResponseModelV2
from packages.kernel.decision_hub_kernel.ports.market_path import (
    MarketCandle,
    MarketPricePath,
    canonical_hash,
    path_hashes,
    spot_scope,
)
from packages.provider_adapters.notifications.local import LocalNotificationAdapter
from packages.provider_adapters.research.raw_capture import RawBodyArchive

from .action_fixtures import ASOF
from .test_event_realization import (
    admit_bls_fixture,
    engineering_actual_body,
    make_realization_case,
)


@pytest.fixture
def source_guard(monkeypatch, tmp_path):
    # Current source inventory is being edited by independent implementation
    # agents; the consumer still checks each native original and SQL readset.
    guard = SimpleNamespace(root=tmp_path, matches=lambda: True)
    monkeypatch.setattr(_ReadSourceGuard, "capture", classmethod(lambda _cls: guard))
    monkeypatch.setattr(
        preparation._ConsumerSourceGuard, "capture", classmethod(lambda _cls, *_args: guard)
    )


def test_native_future_waits_and_real_actual_without_plan_stays_missing(
    tmp_path,
    source_guard,
    monkeypatch,
):
    db, archive, forecast = make_realization_case(tmp_path)
    now = [forecast.scheduled_at - timedelta(days=1)]
    store = MarketPathStore(db, archive, clock=lambda: now[0])
    service = monitor.EventOpportunityMonitorService(
        db, store, archive=archive, feature_root=tmp_path / "features", clock=lambda: now[0]
    )
    assert service.process_event(forecast.forecast_id, "BTC-USDT") == 2
    heads = service.get_status(forecast.forecast_id, "BTC-USDT")
    assert {h["state"] for h in heads} == {"waiting"}
    assert all(h["next_due"] == forecast.scheduled_at + timedelta(seconds=60) for h in heads)
    assert all(h["latest_evaluation"]["data_status"] == "awaiting_release" for h in heads)
    # The ordinary minute scheduler does not prepare existing future heads again.
    monkeypatch.setattr(
        service, "process_event", lambda *args, **kwargs: pytest.fail("future_replay")
    )
    # ETH has not been registered yet, so register it once before this check.
    with db.session() as session:
        from packages.kernel.decision_hub_kernel.persistence.event_monitor_records import (
            EventOpportunityMonitorRecord,
        )

        # Check the due query for BTC through the normal target list after both
        # instruments have been initialized by a new service below.
        assert session.scalar(select(func.count()).select_from(EventOpportunityMonitorRecord)) == 2
    service = monitor.EventOpportunityMonitorService(
        db, store, archive=archive, feature_root=tmp_path / "features", clock=lambda: now[0]
    )
    assert service.process_event(forecast.forecast_id, "ETH-USDT") == 2
    monkeypatch.setattr(
        service, "process_event", lambda *args, **kwargs: pytest.fail("future_replay")
    )
    assert service.process_due() == 0
    now[0] = forecast.scheduled_at + timedelta(minutes=5, seconds=5)
    with db.session() as session:
        admit_bls_fixture(
            session,
            archive,
            body=engineering_actual_body(),
            period="2026-10",
            known_at=now[0] - timedelta(seconds=2),
        )
    with db.session() as session:
        realization = freeze_event_realization_in_session(
            session, forecast.forecast_id, cutoff_at=now[0], recorded_at=now[0], archive=archive
        )
        assert realization is not None
    service = monitor.EventOpportunityMonitorService(
        db, store, archive=archive, feature_root=tmp_path / "features", clock=lambda: now[0]
    )
    assert service.process_event(forecast.forecast_id, "BTC-USDT") == 2
    heads = service.get_status(forecast.forecast_id, "BTC-USDT")
    assert {h["state"] for h in heads} == {"missing"}
    assert all(h["latest_evaluation"]["activation_id"] == realization.realization_id for h in heads)
    assert service.list_notices(event_key=forecast.event_key) == []
    db.engine.dispose()


def _engineered_opportunity():
    at = ASOF - timedelta(minutes=30)
    scope = spot_scope("BTC-USDT")
    context = EventMacroContext(
        as_of=at, state="unknown", series=(), gaps=("engineering",), payload_hash="0" * 64
    )
    context = context.model_copy(
        update={
            "payload_hash": canonical_hash(
                context.model_dump(mode="json", exclude={"payload_hash"})
            )
        }
    )
    policies = event_policy_catalog(scope.scope_id, context)
    catalog = research_action_catalog("BTC-USDT")
    policy_hash = canonical_hash([p.model_dump(mode="json") for p in policies])
    model = EventResponseModelV2(
        instrument="BTC-USDT",
        scope_id=scope.scope_id,
        as_of=at,
        samples=(),
        excluded=(),
        fee_fraction_each_side=".001",
        slippage_fraction_each_side=".0005",
        cost_policy_ref=catalog["cost_policy"]["ref"],
        catalog_hash=canonical_hash(catalog),
        payload_hash="0" * 64,
        macro_context_hash=context.input_hash,
        policy_catalog_hash=policy_hash,
    )
    model = model.model_copy(
        update={
            "payload_hash": canonical_hash(model.model_dump(mode="json", exclude={"payload_hash"}))
        }
    )
    scenario = SimpleNamespace(
        snapshot_id="engineering-forecast",
        event_key="engineering:CPI",
        reference_period="2026-09",
        scheduled_at=ASOF,
        as_of=at - timedelta(seconds=1),
        payload_hash="a" * 64,
        scenarios=tuple(
            SimpleNamespace(
                scenario_id=p.scenario_id,
                event_result_probability=Decimal(".1") if i < 8 else Decimal(".2"),
            )
            for i, p in enumerate(policies)
        ),
    )
    return build_event_opportunity_v2(
        scenario, model, published_at=at + timedelta(seconds=1), macro_context=context
    )


def _ingest(ctx, times, *, price_at=None, volume_at=None):
    scope = spot_scope("BTC-USDT")
    for start in range(0, len(times), 300):
        chunk = times[start : start + 300]
        rows = []
        for close in chunk:
            price = price_at(close) if price_at else "100.3" if close > ASOF else "100"
            volume = volume_at(close) if volume_at else "2" if close > ASOF else "1.4" if close > ASOF - timedelta(hours=1) else "1"
            rows.append(
                [
                    str(int((close - timedelta(minutes=1)).timestamp() * 1000)),
                    price,
                    price,
                    price,
                    price,
                    volume,
                    "140",
                    "140",
                    "1",
                ]
            )
        body = json.dumps({"code": "0", "data": rows}).encode()
        digest, ref = ctx.archive.put(body)
        with ctx.db.session() as session:
            session.add(
                RawCaptureRecord(
                    capture_id="engineering-capture:" + digest,
                    context_id="engineering-event-monitor",
                    url=(
                        "https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT"
                        f"&bar=1m&after={int(chunk[-1].timestamp() * 1000)}&limit=300"
                    ),
                    status_code=200,
                    body_hash=digest,
                    body_ref=ref,
                    byte_count=len(body),
                    media_type="application/json",
                    adapter_version="engineering.v1",
                    error_code=None,
                    started_at=ctx.now[0],
                    completed_at=ctx.now[0],
                    recorded_at=ctx.now[0],
                )
            )
        candles = tuple(
            MarketCandle(
                scope_id=scope.scope_id,
                venue="okx",
                instrument="BTC-USDT",
                open_at=close - timedelta(minutes=1),
                close_at=close,
                open=Decimal(rows[i][1]),
                high=Decimal(rows[i][2]),
                low=Decimal(rows[i][3]),
                close=Decimal(rows[i][4]),
                volume_base=Decimal(rows[i][5]),
                volume_quote=Decimal("140"),
                received_at=ctx.now[0],
                capture_ref=ref,
                capture_hash=digest,
                row_index=i,
            )
            for i, close in enumerate(chunk)
        )
        semantic, provenance = path_hashes(scope, candles)
        ctx.store.ingest(
            MarketPricePath(
                scope=scope,
                window_start_at=candles[0].open_at,
                window_end_at=candles[-1].close_at,
                candles=candles,
                page_count=1,
                request_count=1,
                status="complete",
                semantic_hash=semantic,
                manifest_hash=provenance,
            )
        )


def test_native_market_confirmation_restart_atomic_notice_and_owner_receipts(
    tmp_path,
    source_guard,
    monkeypatch,
):
    db = Database("sqlite:///" + (tmp_path / "monitor.sqlite3").as_posix())
    db.create_all()
    now = [ASOF + timedelta(minutes=1, seconds=5)]
    archive = RawBodyArchive(tmp_path / "raw")
    store = MarketPathStore(db, archive, clock=lambda: now[0])
    ctx = SimpleNamespace(db=db, now=now, archive=archive, store=store)
    opportunity = _engineered_opportunity()
    assert opportunity.plans[0].scenario_strategy_net_profit_estimate.value is None
    activation = SimpleNamespace(
        realization_id="engineering-realization",
        scenario_id="cpi:headline_cooler:core_cooler",
        known_at=ASOF,
    )
    event = monitor._EventInputs(
        opportunity.event_key, opportunity.reference_period, ASOF, activation, opportunity, None
    )
    monkeypatch.setattr(
        monitor.EventOpportunityMonitorService, "_prepare_event", lambda *_args: event
    )

    def service():
        return monitor.EventOpportunityMonitorService(
            db, store, feature_root=tmp_path / "features", clock=lambda: now[0]
        )

    first = ASOF - timedelta(hours=21) + timedelta(minutes=1)
    times = [first + timedelta(minutes=i) for i in range(21 * 60 + 1)]
    _ingest(ctx, times)
    assert service().process_event(opportunity.forecast_id, "BTC-USDT") == 2
    initial = service().get_status(opportunity.forecast_id, "BTC-USDT")
    assert initial[0]["state"] == "waiting" and initial[1]["state"] == "waiting"
    assert initial[0]["confirmation_protocol"] == "event-confirmation.v1"
    assert initial[0]["strategy_probability_status"] == "unknown"
    assert initial[0]["latest_evaluation"]["per_condition"][0]["confirmation_count"] == 1
    assert service().process_event(opportunity.forecast_id, "BTC-USDT") == 0
    now[0] += timedelta(minutes=1)  # no new bar: no extra confirmation
    assert service().process_event(opportunity.forecast_id, "BTC-USDT") == 2
    assert (
        service().get_status(opportunity.forecast_id, "BTC-USDT")[0]["latest_evaluation"][
            "per_condition"
        ][0]["confirmation_count"]
        == 1
    )
    for minute, count in ((3, 1), (4, 2), (5, 3)):
        now[0] = ASOF + timedelta(minutes=minute, seconds=5)
        # Minute two arrives late with minute three. It restores the feature
        # window but cannot retroactively contribute a monitor confirmation.
        incoming = [ASOF + timedelta(minutes=minute)]
        if minute == 3:
            incoming.insert(0, ASOF + timedelta(minutes=2))
        _ingest(ctx, incoming)
        assert service().process_event(opportunity.forecast_id, "BTC-USDT") == 2
        head = service().get_status(opportunity.forecast_id, "BTC-USDT")[0]
        assert head["latest_evaluation"]["per_condition"][0]["confirmation_count"] == count
    assert head["state"] == "triggered"
    notices = service().list_notices(event_key=event.event_key, owner_id="alice")
    assert len(notices) == 1 and {n["transition"] for n in notices} == {"triggered"}
    assert "公告前" in notices[0]["body"] and "当时相对公告前价格锚" in notices[0]["body"]
    assert "event_monitor_" not in notices[0]["body"] and "engineering-" not in notices[0]["body"]
    assert all(n["display_at"] is None and n["ack_at"] is None for n in notices)
    export = tmp_path / "notifications.jsonl"
    dispatcher = NotificationDispatcher(db, {"local": LocalNotificationAdapter(export)})
    assert asyncio.run(dispatcher.dispatch_once()) == 1
    assert asyncio.run(dispatcher.dispatch_once()) == 0
    assert all(
        json.loads(line)["schema_version"] == "notification-envelope.v3"
        for line in export.read_text(encoding="utf-8").splitlines()
    )
    notice = notices[0]
    receipt = service().record_command(
        notice["notice_id"], owner_id="alice", request_id="display-1", command="display"
    )
    assert (
        service().record_command(
            notice["notice_id"], owner_id="alice", request_id="display-1", command="display"
        )
        == receipt
    )
    assert service().record_command(
        notice["notice_id"], owner_id="alice", request_id="ack-1", command="ack"
    )
    assert service().list_notices(event_key=event.event_key, owner_id="alice")[0]["ack_at"]
    assert service().list_notices(event_key=event.event_key, owner_id="bob")[0]["ack_at"] is None
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(EventOpportunityCommandRecord)) == 2
        before = session.scalar(select(func.count()).select_from(EventOpportunityEvaluationRecord))
    # A later condition transition and its immutable evaluation roll back if
    # the shared outbox fails, leaving the last visible trigger intact.
    now[0] = ASOF + timedelta(hours=25)
    monkeypatch.setattr(
        OutboxService,
        "enqueue_subject",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("outbox-control")),
    )
    with pytest.raises(RuntimeError, match="outbox-control"):
        service().process_event(opportunity.forecast_id, "BTC-USDT")
    with db.session() as session:
        assert (
            session.scalar(select(func.count()).select_from(EventOpportunityEvaluationRecord))
            == before
        )
        assert session.scalar(select(func.count()).select_from(OutboxRecord)) == 1
    assert service().get_status(opportunity.forecast_id, "BTC-USDT")[0]["state"] == "triggered"
    db.engine.dispose()


def test_announcement_drop_does_not_enter_from_positive_rolling_return(tmp_path, source_guard, monkeypatch):
    db = Database("sqlite:///" + (tmp_path / "announcement-drop.sqlite3").as_posix())
    db.create_all()
    now = [ASOF + timedelta(minutes=1, seconds=5)]
    archive = RawBodyArchive(tmp_path / "raw")
    store = MarketPathStore(db, archive, clock=lambda: now[0])
    ctx = SimpleNamespace(db=db, now=now, archive=archive, store=store)
    opportunity = _engineered_opportunity()
    activation = SimpleNamespace(realization_id="drop-realization", scenario_id="cpi:headline_cooler:core_cooler", known_at=ASOF)
    event = monitor._EventInputs(opportunity.event_key, opportunity.reference_period, ASOF, activation, opportunity, None)
    monkeypatch.setattr(monitor.EventOpportunityMonitorService, "_prepare_event", lambda *_args: event)
    service = monitor.EventOpportunityMonitorService(db, store, feature_root=tmp_path / "features", clock=lambda: now[0])
    first = ASOF - timedelta(hours=21) + timedelta(minutes=1)
    times = [first + timedelta(minutes=i) for i in range(21 * 60 + 1)]
    # Before announcement: 100 -> 120; after announcement: 118. Old rolling6h
    # is still +18%, while the announcement response is -1.6667%.
    price = lambda close: "118" if close > ASOF else "120" if close > ASOF - timedelta(minutes=20) else "100"
    volume = lambda close: "2" if close > ASOF else "1"
    _ingest(ctx, times, price_at=price, volume_at=volume)
    assert service.process_event(opportunity.forecast_id, "BTC-USDT") == 2
    heads = {h["position_context"]: h for h in service.get_status(opportunity.forecast_id, "BTC-USDT")}
    assert heads["flat"]["state"] == "invalidated"
    assert heads["long"]["state"] == "waiting"
    assert service.process_event(opportunity.forecast_id, "BTC-USDT") == 0
    now[0] += timedelta(minutes=1)
    _ingest(ctx, [ASOF + timedelta(minutes=2)], price_at=price, volume_at=volume)
    assert service.process_event(opportunity.forecast_id, "BTC-USDT") == 1
    heads = {h["position_context"]: h for h in service.get_status(opportunity.forecast_id, "BTC-USDT")}
    assert heads["long"]["state"] == "triggered"
    assert heads["long"]["latest_evaluation"]["confirmation_inputs"]["anchor_price"] == "120"
    assert service.process_event(opportunity.forecast_id, "BTC-USDT") == 0
    assert len(service.list_notices(event_key=event.event_key)) == 2
    db.engine.dispose()


def test_superseded_forecast_retires_monitor_without_user_notice(
    tmp_path, source_guard, monkeypatch
):
    db = Database("sqlite:///" + (tmp_path / "superseded.sqlite3").as_posix())
    db.create_all()
    now = [ASOF + timedelta(minutes=1, seconds=5)]
    archive = RawBodyArchive(tmp_path / "raw")
    store = MarketPathStore(db, archive, clock=lambda: now[0])
    opportunity = _engineered_opportunity()
    activation = SimpleNamespace(
        realization_id="superseded-realization",
        scenario_id="cpi:headline_cooler:core_cooler",
        known_at=ASOF,
    )
    event = monitor._EventInputs(
        opportunity.event_key,
        opportunity.reference_period,
        ASOF,
        activation,
        opportunity,
        "event_monitor_forecast_superseded",
    )
    monkeypatch.setattr(
        monitor.EventOpportunityMonitorService, "_prepare_event", lambda *_args: event
    )
    service = monitor.EventOpportunityMonitorService(
        db, store, archive=archive, feature_root=tmp_path / "features", clock=lambda: now[0]
    )

    assert service.process_event(opportunity.forecast_id, "BTC-USDT") == 2
    assert {item["state"] for item in service.get_status(opportunity.forecast_id, "BTC-USDT")} == {
        "invalidated"
    }
    assert service.list_notices(event_key=event.event_key) == []
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(EventOpportunityEvaluationRecord)) == 2
        assert session.scalar(select(func.count()).select_from(OutboxRecord)) == 0
    db.engine.dispose()


def test_legacy_superseded_notices_do_not_consume_user_facing_limit(tmp_path):
    db = Database("sqlite:///" + (tmp_path / "legacy-superseded.sqlite3").as_posix())
    db.create_all()
    archive = RawBodyArchive(tmp_path / "raw")
    service = monitor.EventOpportunityMonitorService(
        db,
        MarketPathStore(db, archive, clock=lambda: ASOF),
        archive=archive,
        feature_root=tmp_path / "features",
        clock=lambda: ASOF,
    )
    event_key = "fred-cpi:legacy-filter-control"
    with db.session() as session:
        monitor.enqueue_event_notice_in_session(
            session,
            database=db,
            event_key=event_key,
            reference_period="2026-10",
            transition="triggered",
            subject="actionable control",
            body="actionable control",
            created_at=ASOF,
            dedupe_key="legacy-filter:actionable",
            reason_codes=("event_monitor_invalidation_confirmed",),
        )
        # Reproduce a database written by old releases where repeated forecast
        # revisions could place at least 100 audit-only rows ahead of a real
        # user notification.
        for index in range(100):
            monitor.enqueue_event_notice_in_session(
                session,
                database=db,
                event_key=event_key,
                reference_period="2026-10",
                transition="invalidated",
                subject=f"superseded {index}",
                body="superseded audit control",
                created_at=ASOF + timedelta(minutes=index + 1),
                dedupe_key=f"legacy-filter:superseded:{index}",
                reason_codes=("event_monitor_forecast_superseded",),
            )

    visible = service.list_notices(event_key=event_key, limit=1)
    assert [item["subject"] for item in visible] == ["actionable control"]
    audit = service.list_notices(event_key=event_key, limit=1, include_superseded=True)
    assert audit[0]["reason_codes"] == ["event_monitor_forecast_superseded"]
    db.engine.dispose()


def test_long_protection_survives_initial_rise_without_rolling_history(tmp_path, source_guard, monkeypatch):
    db = Database("sqlite:///" + (tmp_path / "rise-then-fall.sqlite3").as_posix())
    db.create_all()
    now = [ASOF + timedelta(minutes=1, seconds=5)]
    archive = RawBodyArchive(tmp_path / "raw")
    store = MarketPathStore(db, archive, clock=lambda: now[0])
    ctx = SimpleNamespace(db=db, now=now, archive=archive, store=store)
    opportunity = _engineered_opportunity()
    activation = SimpleNamespace(realization_id="rise-fall-realization", scenario_id="cpi:headline_cooler:core_cooler", known_at=ASOF)
    event = monitor._EventInputs(opportunity.event_key, opportunity.reference_period, ASOF, activation, opportunity, None)
    monkeypatch.setattr(monitor.EventOpportunityMonitorService, "_prepare_event", lambda *_args: event)

    def service():
        return monitor.EventOpportunityMonitorService(db, store, feature_root=tmp_path / "features", clock=lambda: now[0])

    price = lambda close: "99" if close >= ASOF + timedelta(minutes=2) else "101" if close > ASOF else "100"
    times = [ASOF - timedelta(minutes=19) + timedelta(minutes=i) for i in range(21)]
    _ingest(ctx, times, price_at=price, volume_at=lambda _: "2")
    assert service().process_event(opportunity.forecast_id, "BTC-USDT") == 2
    heads = {h["position_context"]: h for h in service().get_status(opportunity.forecast_id, "BTC-USDT")}
    assert heads["long"]["state"] == "waiting"
    assert heads["long"]["latest_evaluation"]["data_status"] == "available"
    assert "继续监控" in heads["long"]["rule_summary"]
    for minute in (2, 3):
        now[0] = ASOF + timedelta(minutes=minute, seconds=5)
        _ingest(ctx, [ASOF + timedelta(minutes=minute)], price_at=price, volume_at=lambda _: "2")
        assert service().process_event(opportunity.forecast_id, "BTC-USDT") == (2 if minute == 2 else 1)
    heads = {h["position_context"]: h for h in service().get_status(opportunity.forecast_id, "BTC-USDT")}
    assert heads["long"]["state"] == "triggered"
    assert heads["flat"]["state"] == "invalidated"
    assert service().process_event(opportunity.forecast_id, "BTC-USDT") == 0
    notices = service().list_notices(event_key=event.event_key)
    assert len(notices) == 2
    assert any("已有多仓达到减险确认条件" in notice["body"] for notice in notices)
    db.engine.dispose()
