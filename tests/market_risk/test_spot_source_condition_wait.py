"""Isolated real source/admission gates; no live Provider or global API import."""
import asyncio
import os
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy import select

from packages.contracts_py.decision_hub_contracts import ExecutionBudgetV2
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
)
from packages.kernel.decision_hub_kernel.application.risk_admission_wait import (
    AdmissionConditionWait,
    admission_accounting_valid,
    recognized_condition_waits,
)
from packages.kernel.decision_hub_kernel.application.risk_research_admission import (
    RiskResearchAdmissionService,
)
from packages.kernel.decision_hub_kernel.application.spot_scope_admission import (
    SpotAdmissionError,
    SpotSourceFreshnessWait,
)
from packages.kernel.decision_hub_kernel.decision.research_loop import (
    research_acquisition_close_at,
)
from packages.kernel.decision_hub_kernel.persistence.db import RunRecord
from packages.kernel.decision_hub_kernel.persistence.risk_research_records import (
    RiskResearchRequestRecord,
)
from tests.market_risk.test_spot_research import query, stored
from tests.market_risk.test_spot_scope_admission import prepared

assert prepared is not None and stored is not None


@pytest.mark.asyncio
async def test_three_authenticated_stale_waits_then_one_real_run(prepared, monkeypatch):
    db, now, _, _, runtime = prepared
    current = [await asyncio.to_thread(runtime, "ETH-USDT", capture=True)]
    now[0] += timedelta(minutes=4)
    ids = {}

    async def prepare_scope(instrument):
        gate, effective, host = current[0]
        host = host.model_copy(update={"checked_at": now[0]})
        ids[instrument] = gate.approve_trial(
            request_id=f"private:{instrument}:{now[0].isoformat()}",
            effective=effective, host=host,
        ).admission_id

    def resolver(session, study, at):
        gate, effective, host = current[0]
        return gate.resolve_current_in_session(
            session, admission_id=ids[study.instrument], effective=effective,
            host=host.model_copy(update={"checked_at": at}), assessed_at=at,
        )

    service = RiskResearchAdmissionService(
        db, enabled=True, clock=lambda: now[0], prepare_scope=prepare_scope,
        spot_admission_resolver=resolver,
    )
    monkeypatch.setattr(service, "ensure_daily_plans", lambda: None)
    monkeypatch.setattr(service, "discover", lambda: None)
    request_id = service.stage(
        "ETH-USDT", request_id="user:transient-source",
        source_ref="owner-command:user:transient-source", source={"owner_id": "private-owner"},
    )["request_id"]
    for generation in range(1, 4):
        assert await service.process_due(limit=1) == 0
        with db.session() as session:
            row = session.get(RiskResearchRequestRecord, request_id)
            assert row.status == "pending_admission" and row.attempts == 0
            assert row.lease_generation == generation and row.run_id is None
            receipts = recognized_condition_waits(session, row, now=now[0])
            assert len(receipts) == generation
            assert receipts[-1]["reason_code"] == "spot_current_original_path_not_fresh"
            assert receipts[-1]["stage"] == "scope_prepare"
            assert not session.scalars(select(RunRecord)).all()
        now[0] += timedelta(seconds=20)
    # A new genuine fixture capture is committed through the real MarketPath pipeline.
    current[0] = await asyncio.to_thread(runtime, "ETH-USDT", capture=True)
    assert await service.process_due(limit=1) == 1
    assert await service.process_due(limit=1) == 0
    with db.session() as session:
        row = session.get(RiskResearchRequestRecord, request_id)
        assert row.status == "queued" and row.attempts == 1 and row.lease_generation == 4
        assert row.run_id is not None and len(session.scalars(select(RunRecord)).all()) == 1
        assert admission_accounting_valid(session, row, now=now[0])
        assert len(recognized_condition_waits(session, row, now=now[0])) == 3


@pytest.mark.asyncio
async def test_string_generic_type_and_unissued_type_consume_real_failure_budget(
    prepared, monkeypatch,
):
    db, now, *_ = prepared
    failures = [
        ValueError("spot_current_original_path_not_fresh"),
        SpotAdmissionError("spot_current_original_path_not_fresh"),
        SpotSourceFreshnessWait(
            instrument="ETH-USDT", scope_id="crypto:spot:okx:ETH-USDT:trade:1m",
            binding_hash="a" * 64, assessed_at=now[0],
            latest_close_at=now[0] - timedelta(minutes=4),
            latest_known_at=now[0] - timedelta(minutes=3), _issuer=object(),
        ),
    ]

    async def prepare_scope(_instrument):
        raise failures.pop(0)

    service = RiskResearchAdmissionService(
        db, enabled=True, clock=lambda: now[0], prepare_scope=prepare_scope,
    )
    monkeypatch.setattr(service, "ensure_daily_plans", lambda: None)
    monkeypatch.setattr(service, "discover", lambda: None)
    key = service.stage(
        "ETH-USDT", request_id="user:untrusted-error",
        source_ref="owner-command:user:untrusted-error", source={"owner_id": "private-owner"},
    )["request_id"]
    for attempt in range(1, 4):
        assert await service.process_due(limit=1) == 0
        with db.session() as session:
            row = session.get(RiskResearchRequestRecord, key)
            assert row.attempts == row.lease_generation == attempt
            assert recognized_condition_waits(session, row, now=now[0]) == ()
        now[0] += timedelta(seconds=60 * 2 ** (attempt - 1))
    assert await service.process_due(limit=1) == 0
    with db.session() as session:
        row = session.get(RiskResearchRequestRecord, key)
        assert row.status == "failed" and row.run_id is None


def test_missing_future_corrupt_and_wrong_stage_cannot_mint_wait(prepared, monkeypatch):
    _, now, resources, _, runtime = prepared
    gate, effective, host = runtime("ETH-USDT", capture=False)
    with pytest.raises(SpotAdmissionError) as missing:
        gate.approve_trial(request_id="missing", effective=effective, host=host)
    assert not isinstance(missing.value, SpotSourceFreshnessWait)
    monkeypatch.setattr(gate.path_store, "load", lambda *args, **kwargs: (
        SimpleNamespace(close_at=now[0] + timedelta(minutes=1), known_at=now[0]),
    ))
    with pytest.raises(SpotAdmissionError) as future:
        gate.approve_trial(request_id="future", effective=effective, host=host)
    assert not isinstance(future.value, SpotSourceFreshnessWait)
    resources[effective.recipe.ref] = b"altered original"
    with pytest.raises(SpotAdmissionError, match="hash_mismatch") as corrupt:
        gate.approve_trial(request_id="corrupt", effective=effective, host=host)
    assert not isinstance(corrupt.value, SpotSourceFreshnessWait)
    with pytest.raises(ValueError, match="stage_invalid"):
        AdmissionConditionWait(
            "durable_admission", reason_code="spot_current_original_path_not_fresh",
        )


def test_stale_tool_is_retryable_only_and_new_real_source_restores_facts(stored):
    _, _, now, _, adapters = stored
    now[0] += timedelta(minutes=4)
    adapter = adapters["ETH-USDT"]
    old = query("ETH-USDT", request_id="original-failed", cutoff_at=now[0] + timedelta(minutes=5),
                deadline_at=now[0] + timedelta(minutes=5))
    with pytest.raises(ResearchCapabilityError) as stale:
        asyncio.run(adapter.execute(old))
    assert stale.value.error_code == "spot_research_original_stale"
    assert stale.value.retryable is True
    assert stale.value.cause_code == "new_committed_source_required"
    assert asyncio.run(adapter.data.refresh_due()) == 1
    result = asyncio.run(adapter.execute(old.model_copy(update={"request_id": "new-current-data"})))
    assert result.facts and any(item.field == "price" for item in result.facts)
    assert stale.value.error_code == "spot_research_original_stale"  # Original error is unchanged.
    with pytest.raises(ResearchCapabilityError) as invalid:
        asyncio.run(adapter.execute(query("ETH-USDT", fields=["funding_rate"])))
    assert invalid.value.retryable is False


def test_only_spot_candidate_budget_changes_and_old_frozen_budget_retains_close():
    root = Path(os.environ["ACQUISITION_CANDIDATE_ROOT"])
    rel = Path("packs/crypto_macro_prepared/1.2.0/pack.yaml")
    old = yaml.safe_load((root / "before" / rel).read_text(encoding="utf-8"))
    new = yaml.safe_load((root / "proposed" / rel).read_text(encoding="utf-8"))
    old_budget = ExecutionBudgetV2.model_validate(old["execution_budget"])
    new_budget = ExecutionBudgetV2.model_validate(new["execution_budget"])
    assert old_budget.total_deadline_seconds == 1800
    assert new_budget.total_deadline_seconds == 3600
    expected = old_budget.model_dump()
    expected["total_deadline_seconds"] = 3600
    assert new_budget.model_dump() == expected
    from tests.market_risk.test_market_data import NOW
    old_request = SimpleNamespace(
        execution_budget=old_budget, deadline_at=NOW + timedelta(seconds=1800),
    )
    new_request = SimpleNamespace(
        execution_budget=new_budget, deadline_at=NOW + timedelta(seconds=3600),
    )
    assert research_acquisition_close_at(old_request) == NOW + timedelta(seconds=900)
    assert research_acquisition_close_at(new_request) == NOW + timedelta(seconds=2700)
    # Re-evaluating the retained old typed Task must never pick up current Pack values.
    assert research_acquisition_close_at(old_request) == NOW + timedelta(seconds=900)
