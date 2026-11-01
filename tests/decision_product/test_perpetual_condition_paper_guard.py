"""Durable condition invalidation fences manual PAPER entry; all originals are offline."""

from dataclasses import asdict
from datetime import datetime, timedelta

import pytest

from packages.kernel.decision_hub_kernel.application.action_quote_sampling import (
    claim_scope,
    finish_scope,
)
from packages.kernel.decision_hub_kernel.application.perpetual_manual_execution import (
    PerpetualManualPaperService,
)
from packages.kernel.decision_hub_kernel.persistence.action_execution_records import (
    ExecutionIntentRecord,
)
from packages.kernel.decision_hub_kernel.persistence.action_execution_repository import (
    canonical,
    seal,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    ProductCommandRecord,
    RawCaptureRecord,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate
from packages.kernel.decision_hub_kernel.persistence.perpetual_execution_adapter import (
    PerpetualExecutionAdapter,
)
from packages.kernel.decision_hub_kernel.persistence.perpetual_paper_repository import (
    PerpetualPaperRepository,
)
from packages.kernel.decision_hub_kernel.ports.action_quote import CaptureOriginal, text_at
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from packages.kernel.decision_hub_kernel.ports.perpetual_manual_execution import (
    ManualDisplayCommand,
    ManualExecutionCommand,
    ManualExecutionIntent,
    PerpetualSamplingScope,
)
from packages.kernel.decision_hub_kernel.ports.perpetual_quote import VERSION, public_url
from packages.kernel.decision_hub_kernel.ports.perpetual_quote_clock import (
    MEASUREMENT_ADAPTER,
    MEASUREMENT_URL,
    TIME_URL,
)
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_manual_execution import (
    accepted_intent,
    paper_publication,
)
from tests.decision_product.test_perpetual_research_binding import OWNER
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case

BLOCKED = "perpetual_condition_invalidated_new_exposure"


def _invalidate(case, view, at):
    """Synthetic authenticated command envelope; candle provenance has its own core gate."""
    body = dict(
        schema_version="perpetual-condition-transition.v1",
        owner_id=OWNER,
        plan_id=view["revision"]["plan_id"],
        plan_revision_ref=dict(
            ref=view["revision"]["revision_id"], hash=view["revision"]["payload_hash"]
        ),
        watch_id="paper-guard-watch",
        watch_ref=dict(ref="paper-guard-watch-consent", hash="a" * 64),
        assumption_ref=dict(ref="paper-guard-invalidation", hash="b" * 64),
        purpose="invalidation",
        status="invalidated",
        first_confirmed_bar_ref=dict(ref="paper-guard-closed-bar", hash="c" * 64),
        observed_at=at.isoformat().replace("+00:00", "Z"),
        policy_ref=dict(ref="paper-guard-policy", hash="d" * 64),
    )
    key = "perpconditionevent_" + canonical_hash(
        [
            body[k]
            for k in (
                "watch_ref",
                "plan_revision_ref",
                "assumption_ref",
                "purpose",
                "first_confirmed_bar_ref",
            )
        ]
    )
    with case["db"].session() as session:
        begin_immediate(session)
        session.add(
            ProductCommandRecord(
                request_id=key,
                owner_id=OWNER,
                target_id=body["plan_id"],
                kind="perpetual_condition_transition_v1",
                payload_hash=canonical_hash(body),
                result_json=canonical(body),
                created_at=at,
            )
        )


def _unsubmitted(case):
    paper, protocols, at, identity, view = paper_publication(case)
    clock = [at]
    svc = PerpetualManualPaperService(case["db"], paper, protocols, clock=lambda: clock[0])
    shown = svc.record_display(
        OWNER,
        ManualDisplayCommand(
            schema_version="perpetual-manual-display.v1", request_id="guard-visible", **identity
        ),
    )
    command = ManualExecutionCommand(
        schema_version="perpetual-manual-intent-command.v1",
        request_id="guard-submit",
        **identity,
        display_receipt=dict(ref=shown.request_id, hash=shown.receipt_hash),
        max_entry_notional_usdt="80",
        stop_price_usdt="9000",
        take_profit_price_usdt="12000",
        exit_at=(at + timedelta(minutes=5)).isoformat(),
        simulation_acknowledged=True,
    )
    return svc, clock, command, view


def _ledger(case):
    with case["db"].session() as session:
        return PerpetualPaperRepository(session).current(OWNER, "manual-account")


def _sample_offline(case, clock, intent, *, name, bid="9990", ask="10010"):
    """Archive a complete synthetic time/book/time bracket without any HTTP client."""
    at = clock[0]
    instrument = intent.scope_id.removeprefix("perp-book:okx:")
    context = f"{VERSION}:{instrument}:" + ("1" if name == "entry" else "2") * 32

    def save(suffix, url, value, *, adapter=VERSION, status=200):
        raw = canonical(value).encode()
        digest, reference = case["archive"].put(raw)
        capture = CaptureOriginal(
            f"guard-{name}-{suffix}",
            context,
            url,
            status,
            reference,
            digest,
            len(raw),
            adapter,
            None,
            at,
            at,
            at,
        )
        with case["db"].session() as session:
            session.add(RawCaptureRecord(**asdict(capture), media_type="application/json"))
        return capture

    timestamp = str(int(at.timestamp() * 1000))
    before = save("before", TIME_URL, dict(code="0", data=[dict(ts=timestamp)]))
    quote = save(
        "book",
        public_url(instrument),
        dict(
            code="0",
            data=[
                dict(
                    ts=timestamp,
                    bids=[[bid, "20", "0", "1"]],
                    asks=[[ask, "20", "0", "1"]],
                    seqId=1 if name == "entry" else 2,
                )
            ],
        ),
    )
    after = save("after", TIME_URL, dict(code="0", data=[dict(ts=timestamp)]))
    measured = dict(started_at=text_at(at), completed_at=text_at(at), elapsed_monotonic_ns=0)
    measurement = save(
        "measurement",
        MEASUREMENT_URL,
        dict(
            schema_version="perpetual-book-clock-measurement.v1",
            kind="internal_clock_measurement",
            captures=[item.payload() for item in (before, quote, after)],
            before=measured,
            after=measured,
            bracket=measured,
        ),
        adapter=MEASUREMENT_ADAPTER,
        status=None,
    )
    with case["db"].session() as session:
        begin_immediate(session)
        claim = claim_scope(
            session,
            scope=PerpetualSamplingScope(intent.scope_id, instrument),
            owner_id="offline-paper-guard",
            now=at,
        )
        assert claim is not None
        finish_scope(
            session,
            claim,
            now=at,
            event="captured",
            capture_ids=(quote.capture_id, measurement.capture_id),
            quote_id=quote.capture_id,
        )
    clock[0] += timedelta(milliseconds=1)


@pytest.mark.parametrize("when", ["before_prepare", "after_prepare"])
def test_invalidated_condition_blocks_new_manual_command_in_admission_transaction(
    binding_case, monkeypatch, when
):
    svc, clock, command, view = _unsubmitted(binding_case)
    before = _ledger(binding_case)
    if when == "before_prepare":
        _invalidate(binding_case, view, clock[0])
    else:
        prepare = svc._prepare

        def invalidate_after_prepare(*args, **kwargs):
            prepared = prepare(*args, **kwargs)
            _invalidate(binding_case, view, clock[0])
            return prepared

        monkeypatch.setattr(svc, "_prepare", invalidate_after_prepare)
    with pytest.raises(ValueError, match="^" + BLOCKED + "$"):
        svc.submit_intent(OWNER, command)
    assert svc.read_command(OWNER, command.request_id) is None
    assert _ledger(binding_case)["revision"] == before["revision"]
    with binding_case["db"].session() as session:
        assert session.query(ExecutionIntentRecord).count() == 0


def test_invalidated_condition_withdraws_queued_entry_without_quote_or_cash_change(binding_case):
    svc, clock, _command, accepted, view = accepted_intent(binding_case)
    before = _ledger(binding_case)
    clock[0] = datetime.fromisoformat(accepted.response["intent"]["eligible_at"])
    _invalidate(binding_case, view, clock[0])
    result = svc.execute_intent(accepted.response["intent"]["id"])
    assert result is not None and result["status"] == "no_fill"
    assert result["reason"] == BLOCKED and result["fill_id"] is None
    assert _ledger(binding_case)["revision"] == before["revision"]
    assert svc.execute_intent(accepted.response["intent"]["id"]) == result


def test_condition_invalidated_after_entry_preparation_cannot_commit_new_exposure(binding_case):
    svc, clock, _command, accepted, view = accepted_intent(binding_case)
    with binding_case["db"].session() as session:
        intent = PerpetualExecutionAdapter(session).intent(accepted.response["intent"]["id"])
    before = _ledger(binding_case)
    clock[0] = intent.eligible_at + timedelta(milliseconds=500)
    _sample_offline(binding_case, clock, intent, name="entry")
    result = svc.execute_intent(
        intent.id, checkpoint=lambda: _invalidate(binding_case, view, clock[0])
    )
    assert result["status"] == "no_fill" and result["reason"] == BLOCKED
    assert result["fill_id"] is None
    assert _ledger(binding_case)["revision"] == before["revision"]
    assert svc.read_execution(OWNER, intent.id)["episode"] is None


@pytest.mark.parametrize("phase", ["reduce", "exit"])
def test_invalidation_preserves_existing_paper_exit_protection(binding_case, phase):
    svc, clock, command, accepted, view = accepted_intent(binding_case)
    with binding_case["db"].session() as session:
        entry = PerpetualExecutionAdapter(session).intent(accepted.response["intent"]["id"])
    clock[0] = entry.eligible_at + timedelta(milliseconds=500)
    _sample_offline(binding_case, clock, entry, name="entry")
    assert svc.execute_intent(entry.id)["status"] == "filled"
    held = _ledger(binding_case)
    _invalidate(binding_case, view, clock[0])
    assert svc.observe_episode(entry.account_key) is None
    assert _ledger(binding_case)["revision"] == held["revision"]
    clock[0] = datetime.fromisoformat(command.exit_at)
    exit_intent = svc.observe_episode(entry.account_key)
    assert exit_intent is not None and exit_intent.phase == "exit"
    if phase == "reduce":
        exit_intent = seal(
            ManualExecutionIntent,
            dict(
                exit_intent.model_dump(mode="python"),
                id="guard-protective-reduce",
                event_key="guard-protective-reduce",
                phase="reduce",
            ),
        )
        with binding_case["db"].session() as session:
            begin_immediate(session)
            PerpetualExecutionAdapter(session).append_intent(exit_intent)
    clock[0] = exit_intent.eligible_at + timedelta(milliseconds=500)
    _sample_offline(binding_case, clock, exit_intent, name="exit")
    result = svc.execute_intent(exit_intent.id)
    assert result["status"] == "filled" and result["reason"] is None
    assert (
        _ledger(binding_case)["positions"][command.partition.instrument_id]["signed_contracts"]
        == "0"
    )
