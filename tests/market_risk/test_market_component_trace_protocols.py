"""Synthetic exchange/clock, actual private SQLite/capture/services; no financial claim."""

import inspect
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from apps.hub_worker.market_component_originals import ProductionComponentOriginalReader
from apps.hub_worker.market_component_trace_protocols import (
    MonitorRuleOriginalProtocol,
    SourceParserOriginalProtocol,
)
from apps.hub_worker.market_components import InstalledComponent
from packages.kernel.decision_hub_kernel.application.component_registry import ComponentRegistry
from packages.kernel.decision_hub_kernel.application.market_component_functions import (
    PathComponentInput,
    RuleComponentInput,
    evaluate_market_rule_component,
    normalize_market_path_component,
)
from packages.kernel.decision_hub_kernel.application.risk_monitor import RiskMonitorService
from packages.kernel.decision_hub_kernel.persistence.db import OutboxRecord, RawCaptureRecord
from packages.kernel.decision_hub_kernel.persistence.risk_records import (
    MarketCandleRecord,
    RiskAlertRecord,
    RiskConditionEvaluationRecord,
)
from packages.kernel.decision_hub_kernel.ports.component_protocol import ComponentProtocolContext
from packages.kernel.decision_hub_kernel.ports.market_path import (
    MarketPricePath,
    canonical_hash,
    path_hashes,
)
from packages.kernel.decision_hub_kernel.ports.risk_monitor import RiskConditionV1
from packages.provider_adapters.market.okx_path import CapturedMarketPage, OKXMarketPathAdapter
from packages.provider_adapters.research import raw_capture
from packages.provider_adapters.research.raw_capture import CURRENT_CAPTURE
from tests.market_risk.test_market_data import NOW, SCOPE, refresh, snapshot
from tests.market_risk.test_market_data import runtime as data_runtime


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    generator = data_runtime.__wrapped__(tmp_path, monkeypatch)
    state = next(generator)
    now = state[3]

    class SyntheticCaptureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now[0].astimezone(tz or UTC)

    # The exchange and persistence share an explicit synthetic clock. No old
    # receipt is edited or backdated after production capture.
    monkeypatch.setattr(raw_capture, "datetime", SyntheticCaptureClock)
    yield state
    try:
        next(generator)
    except StopIteration:
        pass


def setup(runtime, kind, duration=60):
    db, data, _, now = runtime
    now[0] = NOW - timedelta(seconds=1)
    callback = (
        normalize_market_path_component
        if kind == "source_parser"
        else (evaluate_market_rule_component)
    )
    handler = (
        SourceParserOriginalProtocol()
        if kind == "source_parser"
        else (MonitorRuleOriginalProtocol())
    )
    item = InstalledComponent(
        kind,
        "okx.spot_path.v1" if kind == "source_parser" else "spot.move30.v1",
        SCOPE.scope_id,
        "synthetic-private-" + kind,
        "market-path-component-input.v1"
        if kind == "source_parser"
        else ("market-rule-component-input.v1"),
        "market-price-path.v1" if kind == "source_parser" else "market-rule-component-output.v1",
        callback,
        {"actual_callback": Path(inspect.getsourcefile(callback))},
    )
    reader = ProductionComponentOriginalReader(
        db,
        (item,),
        data.raw_archive,
        clock=lambda: now[0],
        protocol_handlers=(handler,),
    )
    registry = ComponentRegistry(db, original_reader=reader, clock=lambda: now[0])
    manifest = registry.register(item.manifest())
    trial = registry.shadow(
        "trace-" + kind,
        version_id=item.version_id,
        protocol_hash=handler.protocol_hash,
        baseline_version_id=None,
        start_at=NOW,
        end_at=NOW + timedelta(seconds=duration),
    )
    now[0] = NOW
    return handler, reader, manifest, trial, item


def capture(runtime, setup_result, payload, request):
    db, data, _, now = runtime
    _, reader, _, trial, _ = setup_result
    digest, ref = data.raw_archive.put(json.dumps(payload).encode())
    with db.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        return reader.record_shadow_in_session(
            session,
            request_id=request,
            trial_id=trial.trial_id,
            input_ref=ref,
            input_hash=digest,
            source_known_at=now[0],
            cutoff_at=now[0],
        )


def evaluate(runtime, prepared, captures):
    db, data, _, now = runtime
    handler, _, manifest, trial, item = prepared
    with db.session() as session:
        connection = session.connection()
        connection.exec_driver_sql("BEGIN")
        connection.exec_driver_sql("PRAGMA query_only=ON")
        native = connection.connection.driver_connection
        before = native.total_changes
        try:
            result = handler.evaluate(
                session,
                manifest=manifest,
                trial=trial,
                captures=tuple(captures),
                original_refs=(),
                as_of=now[0],
                context=ComponentProtocolContext(
                    data.raw_archive,
                    installed=MappingProxyType({item.version_id: item}),
                ),
            )
            assert native.in_transaction and native.total_changes == before
            assert not session.new and not session.dirty and not session.deleted
            return result
        finally:
            connection.exec_driver_sql("PRAGMA query_only=OFF")


def path_input(runtime):
    _, data, _, now = runtime
    end = now[0].replace(second=0, microsecond=0)
    start = end - timedelta(minutes=31)
    bars = data.store.load(SCOPE, start_at=start, end_at=end, as_of=now[0])
    hashes = path_hashes(SCOPE, bars)
    return PathComponentInput(
        path=MarketPricePath(
            scope=SCOPE,
            window_start_at=start,
            window_end_at=end,
            candles=bars,
            page_count=1,
            request_count=1,
            status="complete",
            semantic_hash=hashes[0],
            manifest_hash=hashes[1],
        )
    ).model_dump(mode="json")


def test_source_actual_raw_native_codec_readonly_and_measured_local_cost(runtime):
    prepared = setup(runtime, "source_parser")
    assert refresh(runtime[1]) == 1
    body = capture(runtime, prepared, path_input(runtime), "actual-source")
    runtime[3][0] = prepared[3].end_at
    result = evaluate(runtime, prepared, (body,))
    assert result.status == "passed" and result.sample_qualified
    assert result.reasons == () and result.cost_verified
    assert result.metrics["frames"][0]["codec_conformant"]
    assert result.metrics["frames"][0]["downstream_pit_verified"]
    assert result.metrics["expected_capture_ticks"] == 1
    assert {r.original_ref.split(":")[0] for r in result.original_refs} == {
        "component_shadow",
        "market_candle",
        "raw_capture",
    }
    assert not result.metrics["probability_qualified"]
    # A real immutable protocol sidecar is written by the trusted producer;
    # verify then re-reads the exact original using its independent SQL snapshot.
    with runtime[0].session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        sidecar = prepared[1].record_component_evaluation_in_session(
            session,
            request_id="source-native-review",
            trial_id=prepared[3].trial_id,
            original_refs=(),
        )
    assert sidecar["result"]["status"] == "passed"
    assert prepared[1].verify(prepared[2], prepared[3]).status == "passed"


def test_source_first_known_corruption_and_gap_denominator(runtime):
    prepared = setup(runtime, "source_parser", duration=180)
    assert refresh(runtime[1]) == 1
    body = capture(runtime, prepared, path_input(runtime), "source")
    runtime[3][0] = prepared[3].end_at
    result = evaluate(runtime, prepared, (body,))
    assert result.metrics["expected_capture_ticks"] == 3
    assert result.metrics["missing_capture_ticks"] == 2 and not result.sample_qualified
    with runtime[0].session() as session:
        record = session.scalar(
            select(RawCaptureRecord).where(
                RawCaptureRecord.body_ref == body["output"]["candles"][-1]["capture_ref"],
            )
        )
        record.recorded_at = prepared[3].end_at
    with pytest.raises(ValueError, match="source_first_known_invalid"):
        evaluate(runtime, prepared, (body,))


def test_source_missing_captures_keep_full_window_and_protocol_identity(runtime):
    prepared = setup(runtime, "source_parser", duration=120)
    runtime[3][0] = prepared[3].end_at
    result = evaluate(runtime, prepared, ())
    assert result.metrics["missing_capture_ticks"] == 2
    assert "component_source_capture_unavailable" in result.reasons
    changed = list(prepared)
    changed[3] = prepared[3].model_copy(update={"protocol_hash": "f" * 64})
    with pytest.raises(ValueError, match="protocol_binding_invalid"):
        evaluate(runtime, changed, ())


def test_source_late_native_acceptance_is_not_raw_first_known(runtime):
    prepared = setup(runtime, "source_parser")
    assert refresh(runtime[1]) == 1
    body = capture(runtime, prepared, path_input(runtime), "late-native")
    with runtime[0].session() as session:
        row = session.scalar(
            select(MarketCandleRecord).order_by(MarketCandleRecord.close_at.desc())
        )
        value = json.loads(row.candle_json)
        value["accepted_at"] = (NOW + timedelta(seconds=1)).isoformat()
        row.candle_json = json.dumps(value)
        row.known_at = NOW + timedelta(seconds=1)
    runtime[3][0] = prepared[3].end_at
    result = evaluate(runtime, prepared, (body,))
    assert "insufficient_for_downstream_pit" in result.reasons
    assert result.metrics["frames"][0]["source_first_known_at"] == NOW.isoformat()
    assert not result.sample_qualified and result.cost_verified


def test_legacy_capture_without_measured_usage_is_readable_but_not_cost_qualified(runtime):
    from packages.kernel.decision_hub_kernel.persistence.market_case_records import (
        MarketComponentAuditRecord,
    )

    prepared = setup(runtime, "source_parser")
    assert refresh(runtime[1]) == 1
    body = capture(runtime, prepared, path_input(runtime), "legacy")
    # Explicit legacy native fixture: its original schema has no usage field.
    body["schema_version"] = "market-component-shadow-execution.v1"
    body.pop("execution_usage")
    with runtime[0].session() as session:
        row = session.get(MarketComponentAuditRecord, body["audit_id"])
        row.payload_json, row.payload_hash = json.dumps(body), canonical_hash(body)
    runtime[3][0] = prepared[3].end_at
    result = evaluate(runtime, prepared, (body,))
    assert result.sample_qualified and not result.cost_verified and result.status == "insufficient"
    assert "component_local_execution_usage_originals_missing" in result.reasons


class PhaseTransport:
    """Synthetic drop followed by two new-bar recovery observations."""

    def __init__(self, now):
        self.now, self.drop = now, True

    async def fetch(self, url):
        after = int(parse_qs(urlparse(url).query)["after"][0])
        cutoff = NOW.replace(second=0) - timedelta(minutes=20)
        rows = []
        for offset in range(1, 301):
            stamp = after - offset * 60000
            price = Decimal(98 if self.drop and stamp >= cutoff.timestamp() * 1000 else 100)
            rows.append(
                [
                    str(stamp),
                    str(price),
                    str(price + 1),
                    str(price - 1),
                    str(price),
                    "1",
                    "100",
                    "100",
                    "1",
                ]
            )
        body = json.dumps({"code": "0", "data": rows}).encode()
        response = httpx.Response(200, content=body, request=httpx.Request("GET", url))
        batch = CURRENT_CAPTURE.get()
        batch.record(url=url, started_at=self.now[0], completed_at=self.now[0], response=response)
        return CapturedMarketPage(body, self.now[0], batch.captures[-1]["body_ref"])


def rule():
    return RiskConditionV1(
        condition_id="synthetic-move30",
        scope_id=SCOPE.scope_id,
        expression={
            "left": {"fn": "abs", "metric": "close_return", "window_seconds": 1800},
            "right": "0.015",
        },
    )


def monitor_history(runtime):
    prepared = setup(runtime, "monitor_rule", duration=180)
    db, data, _, now = runtime
    transport = PhaseTransport(now)
    data.adapter = OKXMarketPathAdapter(fetcher=transport.fetch)
    monitor = RiskMonitorService(db, clock=lambda: now[0])
    plan_id = monitor.ensure_plan(rule())
    bodies = []
    for i in range(3):
        now[0] = NOW + timedelta(minutes=i)
        transport.drop = i == 0
        assert refresh(data) == 1
        sample = snapshot(data, now[0])
        claim = monitor.claim(plan_id)
        assert claim is not None and monitor.complete(claim, sample)
        payload = RuleComponentInput(rule=rule(), sample=sample, now=now[0]).model_dump(mode="json")
        bodies.append(capture(runtime, prepared, payload, "monitor-" + str(i)))
    with db.session() as session:
        alert = session.scalar(select(RiskAlertRecord))
        assert alert.closed_at is not None
        alert_id = alert.alert_id
    monitor.record_receipt(alert_id, "display", request_id="display")
    monitor.record_receipt(alert_id, "ack", request_id="ack")
    now[0] = prepared[3].end_at
    return prepared, bodies


def test_rule_native_sample_evaluation_episode_recovery_and_delivery_separate(runtime):
    prepared, bodies = monitor_history(runtime)
    result = evaluate(runtime, prepared, bodies)
    counts = result.metrics["counts"]
    assert counts == {
        "expected_ticks": 3,
        "evaluated_ticks": 3,
        "missing_ticks": 0,
        "true_ticks": 1,
        "false_ticks": 2,
        "unknown_ticks": 0,
        "error_ticks": 0,
        "episodes": 1,
        "recoveries": 1,
        "outbox_intents": 1,
        "provider_accepted": 0,
        "displayed_alerts": 1,
        "acknowledged_alerts": 1,
    }
    assert result.metrics["captures_with_native_evaluation"] == 3
    assert result.metrics["exposure_status"] == "unknown" and result.status == "passed"
    assert result.cost_verified and not result.metrics["probability_qualified"]


def test_rule_damaged_native_eval_and_notification_do_not_downgrade(runtime):
    prepared, bodies = monitor_history(runtime)
    with runtime[0].session() as session:
        row = session.scalar(select(RiskConditionEvaluationRecord))
        row.value_json = '"0.99"'
    with pytest.raises(ValueError, match="evaluation_recompute_changed"):
        evaluate(runtime, prepared, bodies)
    with runtime[0].session() as session:
        row = session.scalar(
            select(RiskConditionEvaluationRecord).where(
                RiskConditionEvaluationRecord.truth_state == "true",
            )
        )
        row.value_json = '"0.02"'
        outbox = session.scalar(select(OutboxRecord))
        message = json.loads(outbox.payload_json)
        message["subject_id"] = "forged-alert"
        outbox.payload_json = json.dumps(message)
    with pytest.raises(ValueError, match="notification_payload_hash_invalid"):
        evaluate(runtime, prepared, bodies)


def test_rule_missing_native_eval_keeps_denominator(runtime):
    prepared = setup(runtime, "monitor_rule", duration=180)
    assert refresh(runtime[1]) == 1
    sample = snapshot(runtime[1], runtime[3][0])
    payload = RuleComponentInput(rule=rule(), sample=sample, now=runtime[3][0]).model_dump(
        mode="json"
    )
    body = capture(runtime, prepared, payload, "not-executed-monitor")
    runtime[3][0] = prepared[3].end_at
    result = evaluate(runtime, prepared, (body,))
    assert result.metrics["captures_without_native_evaluation"] == 1
    assert result.metrics["counts"]["missing_ticks"] == 3
    assert result.status == "insufficient"
