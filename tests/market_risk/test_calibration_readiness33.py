# pyright: reportPrivateUsage=false
"""Unexecuted, one-node readiness33 mechanics control for the exact DEV pins.

Deploy the proposed calibration.py to its normal package path before running
this node. Current DEV deadline33 alone is deliberately rejected by the pin.
Private unverified metadata is a raw sample upper bound, never qualification.
No model, HTTP, fit replacement, source-guard replacement, or ROOT compatibility
claim is part of this control. Arbitrary I/O latency and every lease/deadline
race remain outside this node; deadline33 has its own separate draft.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import mkdtemp
from typing import Any, NoReturn

import httpx
import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from packages.evals import assessment_basis
from packages.kernel.decision_hub_kernel.application import calibration
from packages.kernel.decision_hub_kernel.application.calibration_inputs import (
    AIProbabilityScope,
    CalibrationEvaluationPolicy,
    CalibrationPlan,
)
from packages.kernel.decision_hub_kernel.persistence import db as db_module
from packages.kernel.decision_hub_kernel.persistence import original_preparation as preparation
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalOutlookRecord,
    Database,
    StrategyCalibrationJobRecord,
    StrategyCalibrationProductionRecord,
    as_utc,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive


@pytest.mark.asyncio
async def test_calibration_readiness33_private_sqlite_upper_bound_and_owned_deferral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # These are deployment prerequisites, not a mocked loaded implementation.
    pins = (
        (calibration, "4aac5e590c24aac373aa815920573f67808240ed5d8b80f2c24479b437ab34ad"),
        (preparation, "d52f298c9eeadad00ffb484dbbbed1a57406e57d58e37037e6870388ec3fb1af"),
        (db_module, "1558ee732813e5eab5788bbacfad4bf233c581f065f4f20adfd6e899cedad069"),
        (assessment_basis, "a61374ba334ffac4189b11079c84a23d62688cde6d4f471907803fc88032fafa"),
    )
    for module, expected in pins:
        assert module.__file__ is not None
        assert hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() == expected, (
            "readiness33 requires its proposed calibration deployed over the exact DEV base; "
            "this node does not establish ROOT production compatibility"
        )

    now = datetime(2026, 10, 9, 9, 0, tzinfo=UTC)
    private_base = Path(db_module.__file__).resolve().parents[4] / ".readiness33-private01"
    private_base.mkdir(exist_ok=True)
    private_root = Path(mkdtemp(prefix="control-", dir=private_base))
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(private_root / "data"))
    databases: list[Database] = []
    preserved_pointer = "private-unvalidated-prior-pointer"
    preserved_error = "private_previous_unconfirmed"

    def forbidden_network(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("private readiness control attempted external HTTP")

    async def forbidden_async_network(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("private readiness control attempted external HTTP")

    monkeypatch.setattr(httpx.Client, "request", forbidden_network)
    monkeypatch.setattr(httpx.AsyncClient, "request", forbidden_async_network)

    def new_case(name: str) -> tuple[Database, calibration.KernelCalibrationProducer, CalibrationPlan]:
        database = Database(f"sqlite+pysqlite:///{private_root / (name + '.sqlite3')}")
        database.create_all()
        databases.append(database)
        producer = calibration.KernelCalibrationProducer(
            database, archive=RawBodyArchive(private_root / name / "raw"), clock=lambda: now,
        )
        stable = "1" * 64
        plan = producer.register_plan(
            request_id="readiness-control-" + name,
            scope=AIProbabilityScope(
                market="crypto", instrument="BTC-USDT", horizon="24h",
                stable_recipe_hash=stable,
                strategy_version=calibration.ai_probability_strategy_version(stable),
                pack_definition_hash="2" * 64, compiled_policy_recipe_hash="3" * 64,
                method_recipe_hash="4" * 64, graph_recipe_hash="5" * 64,
            ),
            policy=CalibrationEvaluationPolicy(purpose="observational_diagnostic"),
            start_at=now - timedelta(days=2), end_at=now - timedelta(days=1),
            approval_source_ref="private-control:raw-unverified-metadata-only",
        )
        assert plan.policy.thresholds.min_samples == 100
        with database.session() as session:
            job = session.get(StrategyCalibrationJobRecord, plan.job_id)
            assert job is not None
            # Deliberately opaque existing fields: neither is treated as an
            # authentic production or a permission to publish a probability.
            job.last_production_id = preserved_pointer
            job.error_code = preserved_error
        return database, producer, plan

    def outlook(
        plan: CalibrationPlan, identity: str, *, recorded_at: datetime | None = None,
        study_id: str | None = "private-study", **changes: Any,
    ) -> AnalyticalOutlookRecord:
        recorded = recorded_at if recorded_at is not None else plan.start_at + timedelta(minutes=1)
        fields = dict(
            outlook_id=identity, run_id="run-" + identity, artifact_id="artifact-" + identity,
            study_id=study_id, market=plan.scope.market, instrument=plan.scope.instrument,
            method_family="ai_synthesis", method_version="private-unverified.v1",
            horizon=plan.scope.horizon, action="abstain", subjective_probability=None,
            probability_status="uncalibrated", data_as_of=recorded,
            window_start_at=recorded, window_end_at=recorded + timedelta(hours=24),
            result_hash="0" * 64, evidence_refs_json="[]", fact_version_refs_json="[]",
            raw_payload_json="{}", trigger="private control", invalidation="unverified",
            cohort="control", eligibility="unchecked", exclusion_reasons_json="[]",
            research_quality="unknown", recorded_at=recorded,
        )
        fields.update(changes)
        return AnalyticalOutlookRecord(**fields)

    def seed(database: Database, plan: CalibrationPlan, count: int) -> None:
        with database.session() as session:
            session.add_all(outlook(plan, f"candidate-{index}") for index in range(count))

    def snapshot_bound(database: Database, plan: CalibrationPlan):
        return preparation.prepare_originals(
            database, lambda session: calibration._cohort_upper_bound(session, plan, now),
            checkpoint=lambda: None,
        )

    def no_new_production(database: Database) -> None:
        with database.session() as session:
            assert session.query(StrategyCalibrationProductionRecord).count() == 0

    try:
        # Shared query: earlier prefix and the inclusive outer end boundary are
        # counted; null Study and mismatched scope/horizon/family do not enter.
        bounds_db, _bounds_producer, bounds_plan = new_case("predicate-boundaries")
        with bounds_db.session() as session:
            session.add_all([
                outlook(bounds_plan, "at-start", recorded_at=bounds_plan.start_at),
                outlook(bounds_plan, "earlier-prefix", recorded_at=bounds_plan.start_at - timedelta(days=1)),
                outlook(bounds_plan, "at-end", recorded_at=bounds_plan.end_at),
                outlook(bounds_plan, "after-end", recorded_at=bounds_plan.end_at + timedelta(microseconds=1)),
                outlook(bounds_plan, "unseeded-study-prefix", study_id="unseeded-study",
                        recorded_at=bounds_plan.start_at - timedelta(days=1)),
                outlook(bounds_plan, "null-study", study_id=None),
                outlook(bounds_plan, "other-market", market="equity"),
                outlook(bounds_plan, "other-instrument", instrument="ETH-USDT"),
                outlook(bounds_plan, "other-horizon", horizon="72h"),
                outlook(bounds_plan, "other-family", method_family="daily_rule"),
                outlook(bounds_plan, "after-cutoff", recorded_at=now + timedelta(hours=1)),
            ])

        def compare_query(session: Session, plan: CalibrationPlan) -> tuple[int | None, set[str]]:
            upper = calibration._cohort_upper_bound(session, plan, now)
            identifiers = {item.outlook_id for item in calibration._cohort_rows(session, plan, now)}
            assert upper == len(identifiers)
            return upper, identifiers

        exact = preparation.prepare_originals(
            bounds_db, lambda session: compare_query(session, bounds_plan), checkpoint=lambda: None,
        )
        assert exact.value == (3, {"at-start", "earlier-prefix", "at-end"})
        extended_plan = bounds_plan.model_copy(update={"end_at": now + timedelta(days=1)})
        cutoff = preparation.prepare_originals(
            bounds_db, lambda session: compare_query(session, extended_plan), checkpoint=lambda: None,
        )
        assert cutoff.value == (4, {"at-start", "earlier-prefix", "at-end", "after-end"})

        # The ordinary fast route uses actual producer/RO receipt/writer/CAS.
        fast_db, fast, fast_plan = new_case("fourteen-insufficient")
        seed(fast_db, fast_plan, 14)
        with fast_db.session() as session:
            job = session.get(StrategyCalibrationJobRecord, fast_plan.job_id)
            assert job is not None
            original_plan = job.plan_json, job.plan_hash, job.registered_at
        assert snapshot_bound(fast_db, fast_plan).value == 14
        assert await fast.process_due(deadline_at=asyncio.get_running_loop().time() + 30) == 0
        no_new_production(fast_db)
        with fast_db.session() as session:
            job = session.get(StrategyCalibrationJobRecord, fast_plan.job_id)
            assert job is not None and job.status == "waiting" and job.attempt == 1
            assert as_utc(job.next_attempt_at) == now + timedelta(minutes=5)
            assert job.lease_owner is None and job.lease_expires_at is None
            assert job.last_production_id == preserved_pointer and job.error_code == preserved_error
            assert (job.plan_json, job.plan_hash, job.registered_at) == original_plan
        assert await fast.process_due(deadline_at=asyncio.get_running_loop().time() + 30) == 0
        with fast_db.session() as session:
            job = session.get(StrategyCalibrationJobRecord, fast_plan.job_id)
            assert job is not None and job.attempt == 1
            assert as_utc(job.next_attempt_at) == now + timedelta(minutes=5)
        no_new_production(fast_db)

        # A real write between the RO snapshot and the second BEGIN changes the
        # matching range or owner. The actual producer must reject the old proof.
        for change in ("phantom", "owner"):
            db, producer, plan = new_case("changed-" + change)
            seed(db, plan, 14)
            begins = 0
            mutated = False

            def change_before_writer(
                _connection: Connection, _cursor: Any, statement: str,
                _parameters: Any, _context: Any, _many: bool,
            ) -> None:
                nonlocal begins, mutated
                if statement == "BEGIN IMMEDIATE":
                    begins += 1
                    if begins == 2 and not mutated:
                        mutated = True
                        # Original writer has not executed BEGIN yet. A second
                        # private connection commits a real intervening change.
                        with db.session() as other:
                            if change == "phantom":
                                other.add(outlook(plan, "matching-phantom"))
                            else:
                                job = other.get(StrategyCalibrationJobRecord, plan.job_id)
                                assert job is not None and job.status == "running"
                                job.lease_owner = "different-private-owner"

            event.listen(db.engine, "before_cursor_execute", change_before_writer)
            try:
                assert await producer.process_due(deadline_at=asyncio.get_running_loop().time() + 30) == 0
            finally:
                event.remove(db.engine, "before_cursor_execute", change_before_writer)
            assert mutated
            no_new_production(db)
            with db.session() as session:
                job = session.get(StrategyCalibrationJobRecord, plan.job_id)
                assert job is not None and job.attempt == 1 and job.status != "waiting"
                assert job.last_production_id == preserved_pointer
                if change == "phantom":
                    assert job.status == "retry" and job.error_code == "original_preparation_readset_changed"
                else:
                    assert job.status == "running" and job.lease_owner == "different-private-owner"
                    assert job.error_code == preserved_error

        # Equality to min and a capped 513 sentinel both reach the genuine full
        # row SELECT. Cancel at that barrier before costly body authentication;
        # reaching it grants no sample qualification or publication success.
        for count in (100, 513):
            db, producer, plan = new_case(f"full-route-{count}")
            seed(db, plan, count)
            assert snapshot_bound(db, plan).value == (100 if count == 100 else None)
            if count == 513:
                with pytest.raises(ValueError, match="calibration_cohort_prefix_incomplete"):
                    preparation.prepare_originals(
                        db, lambda session: calibration._cohort_rows(session, plan, now),
                        checkpoint=lambda: None,
                    )
            entered, release = threading.Event(), threading.Event()

            def full_row_barrier(
                _connection: Connection, _cursor: Any, statement: str,
                _parameters: Any, _context: Any, _many: bool,
            ) -> None:
                upper = statement.upper()
                if "ANALYTICAL_OUTLOOKS.RAW_PAYLOAD_JSON" in upper and not entered.is_set():
                    entered.set()
                    assert release.wait(20), "private full-route barrier was not released"

            event.listen(db.engine, "after_cursor_execute", full_row_barrier)
            task = asyncio.create_task(producer.process_due(
                deadline_at=asyncio.get_running_loop().time() + 30,
            ))
            try:
                assert await asyncio.to_thread(entered.wait, 15), count
                task.cancel()
                await asyncio.sleep(0.02)
                assert not task.done()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=15)
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                event.remove(db.engine, "after_cursor_execute", full_row_barrier)
            no_new_production(db)
            with db.session() as session:
                job = session.get(StrategyCalibrationJobRecord, plan.job_id)
                assert job is not None and job.status == "running" and job.attempt == 1
                assert job.last_production_id == preserved_pointer and job.error_code == preserved_error

        # The fast-wait CAS really commits before uncertainty is raised. No
        # replay, fresh claim, fabricated production, or passing result follows.
        uncertain_db, uncertain, uncertain_plan = new_case("fast-commit-uncertain")
        seed(uncertain_db, uncertain_plan, 14)
        real_commit = Session.commit
        uncertainty_count = 0

        def mark_wait_cas(
            connection: Connection, _cursor: Any, statement: str,
            _parameters: Any, context: Any, _many: bool,
        ) -> None:
            if statement.lstrip().upper().startswith("UPDATE STRATEGY_CALIBRATION_JOBS") and any(
                item.get("status") == "waiting" for item in context.compiled_parameters
            ):
                connection.info["private_readiness_wait_cas"] = True

        def commit_then_uncertain(session: Session) -> None:
            nonlocal uncertainty_count
            uncertain_commit = False
            if session.get_bind() is uncertain_db.engine:
                uncertain_commit = session.connection().info.pop("private_readiness_wait_cas", False)
            real_commit(session)
            if uncertain_commit:
                uncertainty_count += 1
                raise RuntimeError("private_fast_wait_commit_outcome_unknown")

        event.listen(uncertain_db.engine, "after_cursor_execute", mark_wait_cas)
        try:
            with monkeypatch.context() as patch:
                patch.setattr(Session, "commit", commit_then_uncertain)
                with pytest.raises(RuntimeError, match="private_fast_wait_commit_outcome_unknown"):
                    await uncertain.process_due(deadline_at=asyncio.get_running_loop().time() + 30)
        finally:
            event.remove(uncertain_db.engine, "after_cursor_execute", mark_wait_cas)
        assert uncertainty_count == 1
        no_new_production(uncertain_db)
        with uncertain_db.session() as session:
            job = session.get(StrategyCalibrationJobRecord, uncertain_plan.job_id)
            assert job is not None and job.status == "waiting" and job.attempt == 1
            assert as_utc(job.next_attempt_at) == now + timedelta(minutes=5)
            assert job.last_production_id == preserved_pointer and job.error_code == preserved_error
            assert job.lease_owner is None and job.lease_expires_at is None
    finally:
        for database in databases:
            database.engine.dispose()
        # Retain the private databases with the first control receipt.
