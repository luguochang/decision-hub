"""Synthetic raw market originals through real store/readset/monitor transactions.

Only the current-source inventory guard is replaced for concurrent development;
no provider/model is called and no fixture is presented as live market success.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, update

from packages.kernel.decision_hub_kernel.application import action_monitor as monitor
from packages.kernel.decision_hub_kernel.application.action_plan import ActionPlanService
from packages.kernel.decision_hub_kernel.application.market_path_store import MarketPathStore
from packages.kernel.decision_hub_kernel.application.outbox import OutboxService
from packages.kernel.decision_hub_kernel.application.research_graph_binding import _ReadSourceGuard
from packages.kernel.decision_hub_kernel.decision.action_features import (
    ACTION_MONITOR_MANIFEST_REF,
    action_feature_manifest,
)
from packages.kernel.decision_hub_kernel.decision.action_research_policy import (
    research_action_catalog,
)
from packages.kernel.decision_hub_kernel.persistence import original_preparation as preparation
from packages.kernel.decision_hub_kernel.persistence.action_records import (
    ActionConditionEvaluationRecord,
)
from packages.kernel.decision_hub_kernel.persistence.action_repository import ActionRepository
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    OutboxRecord,
    RawCaptureRecord,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate
from packages.kernel.decision_hub_kernel.persistence.risk_records import MarketCandleRecord
from packages.kernel.decision_hub_kernel.ports.action_plan import ConditionEvaluation
from packages.kernel.decision_hub_kernel.ports.market_path import (
    MarketCandle,
    MarketPricePath,
    canonical_hash,
    path_hashes,
    spot_scope,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive

from .action_fixtures import (
    ASOF,
    make_head,
    make_opportunity,
    make_revision,
    make_snapshot,
    seal,
    seed_plan,
)


def condition(identifier, purpose, *, required=1, threshold="99", cmp="gt", feature="close"):
    scope = spot_scope("BTC-USDT").scope_id
    return {
        "id": identifier,
        "purpose": purpose,
        "expression": {
            "op": "compare",
            "feature": feature,
            "scope_id": scope,
            "unit": "USDT/BTC",
            "cmp": cmp,
            "threshold": threshold,
            "max_age_seconds": 180,
        },
        "min_fresh_samples": required,
        "max_age_seconds": 180,
        "evaluation_seconds": 60,
        "calendar_ref": "crypto-24x7.v1",
        "valid_until": ASOF + timedelta(hours=24),
        "original_text": "Synthetic condition",
        "input_requirements": [
            {"metric": feature, "scope": scope, "source": "okx", "unit": "USDT/BTC"}
        ],
    }


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "isolated"))
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'monitor.sqlite3'}")
    database.create_all()
    now = [ASOF + timedelta(minutes=2, seconds=5)]
    archive = RawBodyArchive(tmp_path / "raw")
    store = MarketPathStore(database, archive, clock=lambda: now[0])
    service = monitor.ActionMonitorService(
        database, store, feature_root=tmp_path / "features", clock=lambda: now[0]
    )
    guard = SimpleNamespace(root=tmp_path, valid=True)
    guard.matches = lambda: guard.valid
    monkeypatch.setattr(_ReadSourceGuard, "capture", classmethod(lambda _cls: guard))
    monkeypatch.setattr(
        preparation._ConsumerSourceGuard, "capture", classmethod(lambda _cls, *_args: guard)
    )
    value = SimpleNamespace(
        db=database,
        now=now,
        archive=archive,
        store=store,
        service=service,
        tmp=tmp_path,
        guard=guard,
    )
    yield value
    database.engine.dispose()


def plan(
    ctx,
    *,
    entry=None,
    exit=None,
    invalidation=None,
    state="armed",
    action="enter_long",
    position="flat",
    suffix="",
    manifest=True,
    monitor_state="active",
    **changes,
):
    catalog = research_action_catalog("BTC-USDT")
    opportunity = make_opportunity(
        id="opportunity" + suffix,
        key="key" + suffix,
        target_spec_hash=catalog["targets"]["24h"]["hash"],
    )
    snapshot = make_snapshot(opportunity, id="decision" + suffix)
    refs = [{"ref": "fixture:basis", "hash": "a" * 64}]
    if manifest:
        refs.append(
            {
                "ref": ACTION_MONITOR_MANIFEST_REF,
                "hash": canonical_hash(action_feature_manifest("BTC-USDT")),
            }
        )
    revision = make_revision(
        snapshot,
        opportunity,
        plan_id="plan" + suffix,
        revision_id="revision" + suffix,
        action=action,
        position_context=position,
        entry=entry if entry is not None else [condition("entry", "entry", required=2)],
        exit=exit if exit is not None else [condition("exit", "exit", threshold="90", cmp="lt")],
        invalidation=invalidation
        if invalidation is not None
        else [condition("invalidation", "invalidation", threshold="80", cmp="lt")],
        cost_policy_ref=catalog["cost_policy"]["ref"],
        cost_policy_hash=catalog["cost_policy"]["hash"],
        target_spec_ref=catalog["targets"]["24h"]["ref"],
        basis_refs=refs,
        **changes,
    )
    head = make_head(revision, state=state, monitor_state=monitor_state)
    with ctx.db.session() as session:
        begin_immediate(session)
        seed_plan(session, opportunity=opportunity, snapshot=snapshot, revision=revision, head=head)
    return revision


def bar(ctx, *, price="100", close_at=None):
    scope = spot_scope("BTC-USDT")
    close_at = close_at or ctx.now[0].replace(second=0, microsecond=0)
    open_at = close_at - timedelta(minutes=1)
    value = Decimal(price)
    row = [
        str(int(open_at.timestamp() * 1000)),
        price,
        str(value + 1),
        str(value - 1),
        price,
        "1",
        "100",
        "100",
        "1",
    ]
    body = json.dumps({"code": "0", "data": [row]}).encode()
    digest, reference = ctx.archive.put(body)
    with ctx.db.session() as session:
        session.add(
            RawCaptureRecord(
                capture_id="capture_" + canonical_hash([digest, ctx.now[0].isoformat()])[:40],
                context_id="synthetic-market-control",
                url=(
                    "https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT"
                    f"&bar=1m&after={int(close_at.timestamp() * 1000)}&limit=300"
                ),
                status_code=200,
                body_hash=digest,
                body_ref=reference,
                byte_count=len(body),
                media_type="application/json",
                adapter_version="synthetic-control.v1",
                error_code=None,
                started_at=ctx.now[0],
                completed_at=ctx.now[0],
                recorded_at=ctx.now[0],
            )
        )
    candle = MarketCandle(
        scope_id=scope.scope_id,
        venue=scope.venue,
        instrument=scope.instrument,
        open_at=open_at,
        close_at=close_at,
        open=value,
        high=value + 1,
        low=value - 1,
        close=value,
        volume_base=Decimal(1),
        volume_quote=Decimal(100),
        received_at=ctx.now[0],
        capture_ref=reference,
        capture_hash=digest,
        row_index=0,
    )
    semantic, provenance = path_hashes(scope, (candle,))
    ctx.store.ingest(
        MarketPricePath(
            scope=scope,
            window_start_at=open_at,
            window_end_at=close_at,
            candles=(candle,),
            page_count=1,
            request_count=1,
            status="complete",
            semantic_hash=semantic,
            manifest_hash=provenance,
        )
    )
    return candle


def latest(ctx, revision):
    with ctx.db.session() as session:
        repository = ActionRepository(session)
        return (
            repository.get_head(revision.plan_id),
            repository.get_latest_evaluation(revision.plan_id, revision_id=revision.revision_id),
        )


def counts(ctx):
    with ctx.db.session() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (ActionConditionEvaluationRecord, OutboxRecord)
        )


def test_real_originals_independent_confirmation_restart_and_single_notification(context):
    ctx = context
    revision = plan(
        ctx, entry=[condition("two", "entry", required=2), condition("three", "entry", required=3)]
    )
    for minute in range(3):
        if minute:
            ctx.now[0] += timedelta(minutes=1)
        bar(ctx)
        # A fresh service has no in-memory streak authority.
        service = monitor.ActionMonitorService(
            ctx.db, ctx.store, feature_root=ctx.tmp / "features", clock=lambda: ctx.now[0]
        )
        assert service.process_due() == 1
        head, evaluation = latest(ctx, revision)
        assert [item.confirmation_count for item in evaluation.per_condition[:2]] == [
            min(minute + 1, 2),
            minute + 1,
        ]
        assert head.state == ("triggered" if minute == 2 else "armed")
        # Missing hour/U/volume history cannot suppress complete close-only
        # entry conditions and close-only exit/invalidation protection.
        assert head.monitor_state == "active"
        assert head.enter_streak == 0
    assert counts(ctx) == (3, 1)
    assert ctx.service.process_due() == 0
    assert counts(ctx) == (3, 1)


def test_close_only_reduction_not_blocked_by_missing_twenty_hour_features(context):
    revision = plan(
        context, action="reduce_long", position="long", entry=[condition("reduce", "entry")]
    )
    bar(context)
    assert context.service.process_due() == 1
    head, evaluation = latest(context, revision)
    assert head.state == "triggered" and head.monitor_state == "active"
    assert evaluation.truth == "true" and evaluation.status == "ok"
    payload = json.loads(Path(evaluation.input_ref).read_text(encoding="utf-8"))
    assert any("risk_unit_20h" in gap for gap in payload["gaps"])


def test_missing_manifest_degrades_without_rewriting_plan_or_triggering(context):
    revision = plan(context, manifest=False)
    bar(context)
    assert context.service.process_due() == 1
    head, evaluation = latest(context, revision)
    assert head.state == "armed" and head.monitor_state == "degraded"
    assert evaluation.status == "error" and evaluation.truth == "unknown"
    assert "manifest_missing_or_changed" in evaluation.per_condition[0].reason
    assert counts(context) == (1, 0)


@pytest.mark.parametrize(
    "state,expected", [("armed", "expired"), ("triggered", "completed"), ("proposed", "expired")]
)
def test_expiry_without_any_market_input_does_not_claim_fill(context, state, expected):
    revision = plan(
        context,
        state="armed" if state == "triggered" else state,
        action="reduce_long",
        position="long",
        entry=[condition("entry", "entry")],
    )
    if state == "triggered":
        bar(context)
        assert context.service.process_due() == 1
        assert latest(context, revision)[0].state == "triggered"
    context.now[0] = revision.valid_until
    assert context.service.process_due() == 1
    head, evaluation = latest(context, revision)
    assert head.state == expected and head.monitor_state == "stopped" and head.next_due is None
    assert evaluation.status == "ok"
    assert counts(context) == ((2, 2) if state == "triggered" else (1, 1))


def test_pause_resume_resets_each_streak_and_fences_inflight_owner(context):
    ctx = context
    revision = plan(ctx)
    bar(ctx)
    assert ctx.service.process_due() == 1
    ctx.now[0] += timedelta(minutes=1)
    bar(ctx)
    claim = ctx.service.claim(revision.plan_id)
    assert claim is not None
    commands = ActionPlanService(ctx.db, clock=lambda: ctx.now[0])
    for operation in ("pause", "resume"):
        with ctx.db.session() as session:
            head = ActionRepository(session).get_head(revision.plan_id)
        commands.record_command(
            owner_id="test-user",
            request_id=operation,
            operation=operation,
            plan_id=revision.plan_id,
            expected_revision_id=revision.revision_id,
            expected_state_version=head.state_version,
        )
    with ctx.db.session() as session:
        begin_immediate(session)
        with pytest.raises(ValueError, match="action_monitor_fence_lost"):
            ActionRepository(session).require_monitor_claim(claim, now=ctx.now[0])
    assert ctx.service.process_due() == 1
    head, evaluation = latest(ctx, revision)
    assert head.state == "armed" and evaluation.per_condition[0].confirmation_count == 1
    assert counts(ctx) == (2, 0)


@pytest.mark.parametrize("target", ["raw", "candle", "capture", "source"])
def test_read_after_prepare_change_prevents_half_commit(context, monkeypatch, target):
    ctx = context
    revision = plan(ctx)
    candle = bar(ctx)
    original = ctx.service._complete

    def changed(*args, **kwargs):
        if target == "raw":
            (ctx.archive.root / candle.capture_hash[:2] / candle.capture_hash).write_bytes(
                b"changed"
            )
        elif target == "source":
            ctx.guard.valid = False
        else:
            with ctx.db.session() as session:
                if target == "candle":
                    session.execute(update(MarketCandleRecord).values(capture_hash="f" * 64))
                else:
                    session.execute(update(RawCaptureRecord).values(status_code=500))
        return original(*args, **kwargs)

    monkeypatch.setattr(ctx.service, "_complete", changed)
    with pytest.raises(ValueError, match="original_changed|readset_changed|source_changed"):
        ctx.service.process_due()
    assert counts(ctx) == (0, 0)
    assert latest(ctx, revision)[0].state == "armed"


def test_outbox_exception_rolls_back_evaluation_and_head(context, monkeypatch):
    ctx = context
    revision = plan(
        ctx, action="reduce_long", position="long", entry=[condition("reduce", "entry")]
    )
    bar(ctx)

    def fail(*_args, **_kwargs):
        raise RuntimeError("synthetic-outbox-failure")

    monkeypatch.setattr(OutboxService, "enqueue_subject", fail)
    with pytest.raises(RuntimeError, match="synthetic-outbox-failure"):
        ctx.service.process_due()
    head, _ = latest(ctx, revision)
    assert head.state == "armed" and head.state_version == 1
    assert counts(ctx) == (0, 0)


def test_invalidation_wins_entry_and_exit(context):
    revision = plan(
        context,
        action="reduce_long",
        position="long",
        entry=[condition("entry", "entry")],
        exit=[condition("exit", "exit")],
        invalidation=[condition("invalid", "invalidation")],
    )
    bar(context)
    assert context.service.process_due() == 1
    assert latest(context, revision)[0].state == "invalidated"
    assert counts(context) == (1, 1)


def test_market_store_same_session_callback_and_legacy_load_equal(context):
    candle = bar(context)
    scope = spot_scope("BTC-USDT")
    refs = []
    with context.db.session() as session:
        data = context.store.load_in_session(
            session,
            scope,
            start_at=candle.open_at,
            end_at=candle.close_at,
            as_of=context.now[0],
            on_original=lambda ref, digest: refs.append((ref, digest)),
        )
        assert session.connection().connection.driver_connection.in_transaction
    assert refs == [(candle.capture_ref, candle.capture_hash)]
    assert data == context.store.load(
        scope, start_at=candle.open_at, end_at=candle.close_at, as_of=context.now[0]
    )
    assert (
        hashlib.sha256(context.archive.read(candle.capture_ref)).hexdigest() == candle.capture_hash
    )


def test_duplicate_old_bar_never_increments_and_later_gap_resets(context):
    ctx = context
    revision = plan(ctx, entry=[condition("entry", "entry", required=3)])
    bar(ctx)
    assert ctx.service.process_due() == 1
    assert latest(ctx, revision)[1].per_condition[0].confirmation_count == 1
    ctx.now[0] += timedelta(minutes=1)
    assert ctx.service.process_due() == 1
    head, evaluation = latest(ctx, revision)
    assert head.state == "armed"
    assert evaluation.per_condition[0].confirmation_count == 1
    ctx.now[0] += timedelta(minutes=3)
    assert ctx.service.process_due() == 1
    assert latest(ctx, revision)[0].monitor_state == "degraded"
    assert latest(ctx, revision)[1].per_condition[0].confirmation_count == 0
    ctx.now[0] += timedelta(minutes=1)
    bar(ctx)
    assert ctx.service.process_due() == 1
    assert latest(ctx, revision)[0].monitor_state == "active"
    assert latest(ctx, revision)[1].per_condition[0].confirmation_count == 1


def test_fresh_foreign_known_bar_is_excluded_by_fixed_cutoff(context):
    ctx = context
    revision = plan(ctx)
    ctx.now[0] += timedelta(seconds=20)
    bar(ctx)
    ctx.now[0] -= timedelta(seconds=20)
    assert ctx.service.process_due() == 1
    head, evaluation = latest(ctx, revision)
    assert head.state == "armed" and head.monitor_state == "degraded"
    assert evaluation.per_condition[0].raw_truth == "unknown"
    assert counts(ctx) == (1, 0)


def test_expired_lease_reclaimed_and_prior_owner_is_fenced(context):
    ctx = context
    revision = plan(ctx)
    first = ctx.service.claim(revision.plan_id)
    assert first is not None and ctx.service.claim(revision.plan_id) is None
    ctx.now[0] += timedelta(seconds=91)
    second = ctx.service.claim(revision.plan_id)
    assert second is not None and second.generation == first.generation + 1
    assert second.tick_at == first.tick_at
    with ctx.db.session() as session:
        begin_immediate(session)
        with pytest.raises(ValueError, match="action_monitor_fence_lost"):
            ActionRepository(session).require_monitor_claim(first, now=ctx.now[0])
        assert (
            ActionRepository(session).require_monitor_claim(second, now=ctx.now[0]) == second.head
        )
    assert counts(ctx) == (0, 0)


def test_exact_tick_replay_returns_original_before_expiry_and_changed_input_conflicts(
    context, monkeypatch
):
    ctx = context
    revision = plan(ctx)
    bar(ctx)
    original = ctx.service._complete
    captured = []

    def complete(*args, **kwargs):
        captured.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(ctx.service, "_complete", complete)
    assert ctx.service.process_due() == 1
    receipt = latest(ctx, revision)[1]
    ctx.now[0] = revision.valid_until + timedelta(days=1)
    args, kwargs = captured[0]
    assert original(*args, **kwargs) == receipt
    changed = (*args[:2], replace(args[2], input_hash="f" * 64))
    with pytest.raises(ValueError, match="action_monitor_tick_conflict"):
        original(*changed, **kwargs)
    assert counts(ctx) == (1, 0)


def test_head_update_failure_rolls_back_inserted_evaluation(context, monkeypatch):
    ctx = context
    revision = plan(ctx)
    bar(ctx)
    original = ActionRepository._cas_head
    calls = []

    def cas(self, old, new):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("synthetic-head-cas-failure")
        return original(self, old, new)

    monkeypatch.setattr(ActionRepository, "_cas_head", cas)
    with pytest.raises(RuntimeError, match="synthetic-head-cas-failure"):
        ctx.service.process_due()
    assert counts(ctx) == (0, 0)
    assert latest(ctx, revision)[0].state_version == 1


def test_legacy_evaluation_bytes_hash_stay_original_and_do_not_grant_streak(context):
    ctx = context
    revision = plan(ctx)
    old = seal(
        ConditionEvaluation,
        {
            "schema_version": "decision-condition-evaluation.v1",
            "id": "old-v1",
            "plan_id": revision.plan_id,
            "revision_id": revision.revision_id,
            "tick_at": ASOF,
            "as_of": ASOF,
            "evaluated_at": ASOF,
            "lease_generation": 1,
            "input_ref": "synthetic:old-v1",
            "input_hash": "c" * 64,
            "truth": "true",
            "status": "ok",
            "per_condition": [],
            "state_before": "armed",
            "state_after": "armed",
        },
        "evaluation_hash",
    )
    original_json = json.dumps(old.model_dump(mode="json"), indent=3)
    with ctx.db.session() as session:
        session.add(
            ActionConditionEvaluationRecord(
                evaluation_id=old.id,
                plan_id=old.plan_id,
                revision_id=old.revision_id,
                tick_at=old.tick_at,
                lease_generation=old.lease_generation,
                input_hash=old.input_hash,
                payload_json=original_json,
                payload_hash=old.evaluation_hash,
            )
        )
    bar(ctx)
    assert ctx.service.process_due() == 1
    assert latest(ctx, revision)[1].per_condition[0].confirmation_count == 1
    with ctx.db.session() as session:
        stored = session.get(ActionConditionEvaluationRecord, old.id)
        assert stored.payload_json == original_json and stored.payload_hash == old.evaluation_hash
        assert (
            ActionRepository(session).get_evaluation_for_tick(
                old.plan_id, revision_id=old.revision_id, tick_at=old.tick_at
            )
            == old
        )


def test_proposed_does_not_arm_from_fresh_market(context):
    revision = plan(context, state="proposed", monitor_state="degraded")
    bar(context)
    assert context.service.process_due() == 0
    assert latest(context, revision)[0].state == "proposed"
    assert counts(context) == (0, 0)


def test_corrupt_original_is_durable_unknown_error_not_market_false(context):
    revision = plan(context)
    candle = bar(context)
    (context.archive.root / candle.capture_hash[:2] / candle.capture_hash).write_bytes(b"corrupt")
    assert context.service.process_due() == 1
    head, evaluation = latest(context, revision)
    assert head.state == "armed" and head.monitor_state == "degraded"
    assert evaluation.status == "error" and evaluation.truth == "unknown"
    assert all(
        item.raw_truth == "unknown" and item.confirmation_count == 0
        for item in evaluation.per_condition
    )
    assert "raw_capture_archive_corrupt" in evaluation.per_condition[0].reason


def test_input_time_regression_cannot_reuse_prior_confirmation(context):
    ctx = context
    revision = plan(ctx)
    bar(ctx, close_at=ctx.now[0].replace(second=0) - timedelta(minutes=1))
    current = bar(ctx)
    assert ctx.service.process_due() == 1
    assert latest(ctx, revision)[1].per_condition[0].confirmation_count == 1
    with ctx.db.session() as session:
        session.query(MarketCandleRecord).filter(
            MarketCandleRecord.close_at == current.close_at,
        ).delete(synchronize_session=False)
    ctx.now[0] += timedelta(minutes=1)
    assert ctx.service.process_due() == 1
    head, evaluation = latest(ctx, revision)
    assert head.state == "armed" and head.monitor_state == "degraded"
    assert evaluation.per_condition[0].raw_truth == "unknown"
    assert evaluation.per_condition[0].confirmation_count == 0
    assert evaluation.per_condition[0].reason == "action_monitor_input_time_regressed"


@pytest.mark.parametrize("purpose", ["exit", "invalidation"])
@pytest.mark.parametrize("known_true", [True, False])
def test_protection_any_uses_confirmed_fact_without_hiding_other_error(
    context, monkeypatch, purpose, known_true
):
    ctx = context
    conditions = [
        condition("protect-known", purpose, threshold="90", cmp="lt",
                  feature="close" if known_true else "box_high_6h"),
        condition("protect-broken", purpose, feature="risk_unit_20h"),
    ]
    choices = {
        "exit": [condition("exit-false", "exit", threshold="1", cmp="lt")],
        "invalidation": [condition("invalid-false", "invalidation", threshold="1", cmp="lt")],
        purpose: conditions,
    }
    revision = plan(ctx, action="reduce_long", position="long",
                    entry=[condition("entry", "entry")], **choices)
    bar(ctx)
    assert ctx.service.process_due() == 1
    assert latest(ctx, revision)[0].state == "triggered"
    original = monitor.build_action_features

    def faulty_calculator(*args, **kwargs):
        # Controlled calculator defect: the real evaluator must retain the
        # conflicting unit as an error, while the close fact stays authentic.
        frame = original(*args, **kwargs)
        payload = json.loads(frame.payload_json)
        broken = next(row for row in payload["features"] if row["feature"] == "risk_unit_20h")
        broken["unit"] = "wrong-unit"
        digest = canonical_hash({k: v for k, v in broken.items()
                                 if k not in {"source_hash", "source_ref"}})
        broken.update(source_hash=digest, source_ref="action-feature:" + digest)
        features = tuple(
            replace(item, unit="wrong-unit", source_hash=digest,
                    source_ref=broken["source_ref"])
            if item.feature == "risk_unit_20h" else item for item in frame.features
        )
        return replace(frame, features=features, input_hash=canonical_hash(payload),
                       payload_json=monitor._body(payload))

    monkeypatch.setattr(monitor, "build_action_features", faulty_calculator)
    ctx.now[0] += timedelta(minutes=1)
    bar(ctx, price="85")
    assert ctx.service.process_due() == 1
    head, evaluation = latest(ctx, revision)
    rows = {item.id: item for item in evaluation.per_condition}
    assert rows["protect-broken"].status == "error"
    assert rows["protect-broken"].reason == "action_feature_binding_mismatch"
    assert evaluation.status == "error" and evaluation.truth == "unknown"
    if known_true:
        assert rows["protect-known"].confirmed is True
        assert head.state == ("completed" if purpose == "exit" else "invalidated")
        assert counts(ctx) == (2, 2)
        with ctx.db.session() as session:
            notification = session.scalar(select(OutboxRecord).order_by(OutboxRecord.id.desc()))
            assert "protect-known" in notification.payload_json
            assert "protect-broken" not in notification.payload_json
    else:
        assert rows["protect-known"].raw_truth == "unknown"
        assert head.state == "triggered" and head.monitor_state == "degraded"
        assert counts(ctx) == (2, 1)


def test_unsupported_condition_cadence_is_explicitly_degraded(context):
    entry = condition("entry", "entry", required=2)
    entry["evaluation_seconds"] = 120
    revision = plan(context, entry=[entry])
    bar(context)
    assert context.service.process_due() == 1
    head, evaluation = latest(context, revision)
    assert head.state == "armed" and head.monitor_state == "degraded"
    assert evaluation.per_condition[0].reason == "action_monitor_condition_cadence_unsupported"
    assert counts(context) == (1, 0)


def test_due_budget_is_four_opportunities_with_independent_position_branches(context):
    ctx = context
    with ctx.db.session() as session:
        begin_immediate(session)
        for number in range(5):
            opportunity = make_opportunity(
                id=f"op-{number}", key=f"key-{number}", asset="ETH" if number % 2 else "BTC"
            )
            snapshot = make_snapshot(opportunity, id=f"decision-{number}")
            for position in ("flat", "long", "unknown"):
                revision = make_revision(
                    snapshot,
                    opportunity,
                    plan_id=f"p-{number}-{position}",
                    revision_id=f"r-{number}-{position}",
                    position_context=position,
                )
                head = make_head(
                    revision, monitor_state="paused" if position == "unknown" else "active"
                )
                seed_plan(
                    session,
                    opportunity=opportunity,
                    snapshot=snapshot,
                    revision=revision,
                    head=head,
                )
    with ctx.db.session() as session:
        selected = ActionRepository(session).list_due_monitor_plan_ids(now=ctx.now[0])
        assert len(selected) == 8 and all("unknown" not in value for value in selected)
        assert {value.split("-")[1] for value in selected} == {"0", "1", "2", "3"}
    with pytest.raises(ValueError, match="action_monitor_batch_invalid"):
        ctx.service.process_due(max_opportunities=5)
