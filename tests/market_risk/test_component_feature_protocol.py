"""Finite native capture/ledger controls, explicit synthetic transport, no statistical advantage."""

import asyncio
import inspect
import json
import socket
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from apps.hub_worker.market_component_originals import ProductionComponentOriginalReader
from apps.hub_worker.market_components import InstalledComponent
from packages.kernel.decision_hub_kernel.application.component_feature_protocol import (
    FEATURE_PROTOCOL_HASH,
    FeatureOriginalProtocol,
)
from packages.kernel.decision_hub_kernel.application.component_registry import ComponentRegistry
from packages.kernel.decision_hub_kernel.application.market_component_functions import (
    FeatureComponentInput,
    build_market_feature_component,
)
from packages.kernel.decision_hub_kernel.persistence.market_case_records import (
    MarketComponentAuditRecord,
)
from packages.kernel.decision_hub_kernel.persistence.risk_records import MarketCandleRecord
from packages.kernel.decision_hub_kernel.ports.component_protocol import (
    LOCAL_EXECUTION_POLICY,
    verify_local_execution_cost,
)
from packages.kernel.decision_hub_kernel.ports.market_path import MarketCandle, canonical_hash
from tests.market_risk.test_market_data import NOW, SCOPE
from tests.market_risk.test_market_data import runtime as data_runtime


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    yield from data_runtime.__wrapped__(tmp_path, monkeypatch)


def setup(runtime):
    db, data, _, now = runtime
    now[0] = NOW - timedelta(minutes=5)
    callback = build_market_feature_component
    installed = InstalledComponent(
        "feature",
        "spot.features.v1",
        SCOPE.scope_id,
        "actual-feature-test",
        "market-feature-component-input.v1",
        "market-feature-snapshot.v1",
        callback,
        {"actual_callback": Path(inspect.getsourcefile(callback))},
        {},
        {},
    )
    reader = ProductionComponentOriginalReader(
        db,
        (installed,),
        data.raw_archive,
        clock=lambda: now[0],
        protocol_handlers=(FeatureOriginalProtocol(),),
    )
    registry = ComponentRegistry(db, original_reader=reader, clock=lambda: now[0])
    manifest = registry.register(installed.manifest())
    trial = registry.shadow(
        "feature-shadow",
        version_id=installed.version_id,
        protocol_hash=FEATURE_PROTOCOL_HASH,
        baseline_version_id=None,
        start_at=NOW,
        end_at=NOW + timedelta(hours=1),
    )
    now[0] = NOW
    assert asyncio.run(data.refresh_due()) == 1
    cutoff = NOW + timedelta(seconds=10)
    bars = data.store.load(
        SCOPE, start_at=cutoff - timedelta(minutes=34), end_at=cutoff, as_of=cutoff
    )
    payload = FeatureComponentInput(
        scope=SCOPE, candles=bars, as_of=cutoff, stale_after_seconds=180
    )
    digest, ref = data.raw_archive.put(payload.model_dump_json().encode())
    now[0] = cutoff + timedelta(seconds=1)
    with db.session() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        capture = reader.record_shadow_in_session(
            session,
            request_id="real-feature-capture",
            trial_id=trial.trial_id,
            input_ref=ref,
            input_hash=digest,
            source_known_at=max(c.known_at for c in bars),
            cutoff_at=cutoff,
        )
    return registry, reader, manifest, trial, capture, payload


def evaluate(runtime, reader, trial):
    with runtime[0].session() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        return reader.record_component_evaluation_in_session(
            session,
            request_id="feature-evaluation",
            trial_id=trial.trial_id,
            original_refs=(),
        )


def test_native_feature_formula_eval_sidecar_restart_and_no_false_approval(runtime):
    registry, reader, manifest, trial, _, payload = setup(runtime)
    body = evaluate(runtime, reader, trial)
    assert body == evaluate(runtime, reader, trial)
    result = body["result"]
    assert result["status"] == "insufficient" and result["metrics"]["formula_verified"] == 1
    assert result["metrics"]["native_bars"] == len(payload.candles)
    assert result["original_refs"] and all(
        r["original_ref"].startswith("market_candle:") for r in result["original_refs"]
    )
    assert "component_feature_same_input_ablation_originals_missing" in result["reasons"]
    assert result["cost_verified"]
    assert result["metrics"]["local_execution_cost_verified"]
    assert not result["metrics"]["financial_cost_qualified"]
    review = registry.review(
        "actual-feature-review",
        trial_id=trial.trial_id,
        reviewer_ref="ops://controlled-feature-review",
    )
    assert review.decision == "not_admitted"
    assert not review.qualification.sample_qualified and review.qualification.cost_verified
    # An unchanged durable sidecar is recovered by a new trusted reader, no latest selection.
    restarted = ProductionComponentOriginalReader(
        runtime[0],
        tuple(reader.installed.values()),
        runtime[1].raw_archive,
        clock=lambda: runtime[3][0],
        protocol_handlers=(FeatureOriginalProtocol(),),
    )
    assert restarted.verify(manifest, trial) == review.qualification


def test_native_row_clock_and_raw_capture_cannot_be_replaced_by_archived_json(runtime):
    _, reader, _, trial, _, payload = setup(runtime)
    db = runtime[0]
    bar = payload.candles[0]
    identity = (
        "candle_" + canonical_hash([bar.scope_id, canonical_hash(bar.semantic_payload())])[:48]
    )
    with pytest.raises(ValueError, match="scope_or_time_invalid"):
        with db.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            session.execute(
                text("UPDATE market_candles SET known_at=:late WHERE candle_id=:id"),
                {
                    "late": (payload.as_of + timedelta(microseconds=1)).replace(tzinfo=None),
                    "id": identity,
                },
            )
            reader.record_component_evaluation_in_session(
                session,
                request_id="future-row",
                trial_id=trial.trial_id,
                original_refs=(),
            )
    with db.session() as session:
        assert (
            session.get(MarketCandleRecord, identity).known_at.replace(tzinfo=bar.known_at.tzinfo)
            == bar.known_at
        )
    # Valid typed raw can be archived, but without actual native row and capture it is not evidence.
    fake = bar.model_copy(
        update={
            "open": bar.open + 10,
            "high": bar.high + 10,
            "low": bar.low + 10,
            "close": bar.close + 10,
        }
    )
    MarketCandle.model_validate(fake.model_dump(mode="json"))
    data = payload.model_copy(update={"candles": (fake, *payload.candles[1:])})
    digest, ref = runtime[1].raw_archive.put(data.model_dump_json().encode())
    with db.session() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        reader.record_shadow_in_session(
            session,
            request_id="archived-but-not-native",
            trial_id=trial.trial_id,
            input_ref=ref,
            input_hash=digest,
            source_known_at=bar.known_at,
            cutoff_at=payload.as_of,
        )
    with pytest.raises(ValueError, match="original_missing"):
        evaluate(runtime, reader, trial)


def test_after_flush_native_poison_rolls_back_eval_and_originals(runtime):
    _, reader, _, trial, _, payload = setup(runtime)
    db = runtime[0]
    with db.session() as session:
        original = {r.candle_id: r.candle_json for r in session.scalars(select(MarketCandleRecord))}
        audits = session.scalar(select(MarketComponentAuditRecord.audit_id))
    fired = []

    def poison(session, context):
        if any(
            isinstance(r, MarketComponentAuditRecord)
            and r.request_id.startswith("component-protocol-evaluation:")
            for r in session.new
        ):
            bar = payload.candles[0]
            row_id = (
                "candle_"
                + canonical_hash([bar.scope_id, canonical_hash(bar.semantic_payload())])[:48]
            )
            session.execute(
                text("UPDATE market_candles SET known_at=:late WHERE candle_id=:id"),
                {"late": (payload.as_of + timedelta(seconds=1)).replace(tzinfo=None), "id": row_id},
            )
            fired.append(True)

    event.listen(Session, "after_flush", poison)
    try:
        with pytest.raises(ValueError, match="scope_or_time_invalid"):
            evaluate(runtime, reader, trial)
    finally:
        event.remove(Session, "after_flush", poison)
    assert fired == [True]
    with db.session() as session:
        assert {
            r.candle_id: r.candle_json for r in session.scalars(select(MarketCandleRecord))
        } == original
        assert session.query(MarketComponentAuditRecord).count() == 1
        assert session.scalar(select(MarketComponentAuditRecord.audit_id)) == audits


def _swallow_acquisition(payload, parameters):
    try:
        socket.socket()
    except ValueError:
        pass
    return payload


def test_local_callback_boundary_denies_swallowed_acquisition_without_shadow_write(runtime):
    db, data, _, now = runtime
    now[0] = NOW - timedelta(minutes=5)
    installed = InstalledComponent(
        "feature",
        "hostile-fixture",
        SCOPE.scope_id,
        "hostile-fixture-v1",
        "fixture.v1",
        "fixture.v1",
        _swallow_acquisition,
        {"callback": Path(__file__)},
        {},
        {},
    )
    reader = ProductionComponentOriginalReader(
        db,
        (installed,),
        data.raw_archive,
        clock=lambda: now[0],
        protocol_handlers=(FeatureOriginalProtocol(),),
    )
    registry = ComponentRegistry(db, original_reader=reader, clock=lambda: now[0])
    registry.register(installed.manifest())
    trial = registry.shadow(
        "denied-acquisition",
        version_id=installed.version_id,
        protocol_hash=FEATURE_PROTOCOL_HASH,
        baseline_version_id=None,
        start_at=NOW,
        end_at=NOW + timedelta(hours=1),
    )
    digest, ref = data.raw_archive.put(b'{"fixture":"network-denied"}')
    now[0] = NOW
    with pytest.raises(ValueError, match="external_acquisition_forbidden"):
        with db.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            reader.record_shadow_in_session(
                session,
                request_id="denied-acquisition",
                trial_id=trial.trial_id,
                input_ref=ref,
                input_hash=digest,
                source_known_at=NOW,
                cutoff_at=NOW,
            )
    with db.session() as session:
        assert session.query(MarketComponentAuditRecord).count() == 0


def test_measured_cost_originals_exact_binding_and_fixed_limit(runtime):
    _, _, _, _, capture, _ = setup(runtime)
    assert capture["schema_version"] == "market-component-shadow-execution.v2"
    assert verify_local_execution_cost((capture,)) == (True, ())
    legacy = {k: v for k, v in capture.items() if k != "execution_usage"}
    legacy["schema_version"] = "market-component-shadow-execution.v1"
    assert verify_local_execution_cost((legacy,)) == (
        False,
        ("component_local_execution_usage_originals_missing",),
    )
    altered = json.loads(json.dumps(capture))
    altered["execution_usage"]["input_hash"] = "f" * 64
    with pytest.raises(ValueError, match="usage_binding_invalid"):
        verify_local_execution_cost((altered,))
    altered = json.loads(json.dumps(capture))
    altered["execution_usage"]["elapsed_ns"] = LOCAL_EXECUTION_POLICY["maximum_elapsed_ns"] + 1
    assert verify_local_execution_cost((altered,)) == (
        False,
        ("component_local_execution_cost_exceeds_frozen_limit",),
    )
