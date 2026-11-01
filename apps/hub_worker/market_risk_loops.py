"""Normal-launch research admission and a single finite shadow owner."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from apps.hub_worker.spot_research_bootstrap import InstalledSpotResearch
from packages.kernel.decision_hub_kernel.application.path_shadow_jobs import PathShadowJobService
from packages.kernel.decision_hub_kernel.application.research_run_scope import (
    require_run_research_scope_in_session,
)
from packages.kernel.decision_hub_kernel.application.risk_admission_wait import observe_owned
from packages.kernel.decision_hub_kernel.application.risk_path_ledger import RiskPathLedger
from packages.kernel.decision_hub_kernel.application.risk_research_admission import (
    RiskResearchAdmissionService,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    ReviewScheduleRecord,
    RunRecord,
    StudyRecord,
    as_utc,
)
from packages.kernel.decision_hub_kernel.persistence.path_forecast_records import (
    RiskShadowTrialRecord,
)
from packages.kernel.decision_hub_kernel.persistence.spot_scope_admission import (
    SpotResearchAdmissionRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc, canonical_hash

SHADOW_REQUEST = "market-risk-path-shadow.v1-first-28-days"


class SpotResearchTick:
    def __init__(
        self, database: Database, installed: InstalledSpotResearch,
        *, action_review_enabled: bool = False,
    ):
        self.database, self.installed = database, installed
        self._prepared_this_tick: dict[str, tuple[datetime, str]] = {}
        self.service = RiskResearchAdmissionService(
            database,
            prepare_scope=self._prepare_scope,
            spot_admission_resolver=installed.resolve_in_session,
            enabled=installed.enabled,
            action_review_enabled=action_review_enabled,
            clock=installed.clock,
        )

    def _cached_scope_is_current(self, instrument: str) -> bool:
        cached = self._prepared_this_tick.get(instrument)
        now = aware_utc(self.installed.clock())
        effective = self.installed.effective.get(instrument)
        if (
            cached is not None
            and effective is not None
            and 0 <= (now - cached[0]).total_seconds() <= 120
        ):
            with self.database.session() as session:
                row = session.get(SpotResearchAdmissionRecord, cached[1])
                valid = bool(
                    row is not None
                    and row.instrument == instrument
                    and as_utc(row.created_at) <= now < as_utc(row.expires_at)
                    and row.binding_hash == canonical_hash(effective.model_dump(mode="json"))
                    and self.installed._verify_effective(effective)
                )
            if valid:
                return True
        return False

    async def _prepare_scope(self, instrument: str) -> None:
        if await observe_owned(asyncio.to_thread(self._cached_scope_is_current, instrument)):
            return
        await self.installed.prepare(instrument)
        self._prepared_this_tick[instrument] = (
            aware_utc(self.installed.clock()),
            self.installed.admission_ids[instrument],
        )

    def due_review_instruments(self) -> tuple[str, ...]:
        now = aware_utc(self.installed.clock())
        with self.database.session() as session:
            parents = session.scalars(
                select(RunRecord)
                .join(ReviewScheduleRecord, ReviewScheduleRecord.parent_run_id == RunRecord.run_id)
                .join(StudyRecord, StudyRecord.study_id == RunRecord.study_id)
                .where(
                    ReviewScheduleRecord.status == "planned",
                    ReviewScheduleRecord.trigger_at <= now + timedelta(seconds=60),
                    StudyRecord.market == "crypto",
                    StudyRecord.instrument.in_(("BTC-USDT", "ETH-USDT")),
                )
                .order_by(ReviewScheduleRecord.trigger_at)
                .limit(32)
            ).all()
            result = set()
            for parent in parents:
                try:
                    require_run_research_scope_in_session(session, parent)
                except ValueError:
                    continue
                study = session.get(StudyRecord, parent.study_id)
                result.add(study.instrument)
            return tuple(sorted(result))

    async def tick(self) -> int:
        self._prepared_this_tick.clear()
        try:
            for instrument in self.due_review_instruments():
                try:
                    # Host HTTP and full source verification precede any Run writer.
                    await self._prepare_scope(instrument)
                except (ValueError, OSError, RuntimeError) as error:
                    logging.getLogger(__name__).warning(
                        "spot_review_prepare_pending instrument=%s error=%s",
                        instrument,
                        str(error)[:160],
                    )
            return await self.service.process_due(limit=2)
        finally:
            self._prepared_this_tick.clear()


class PathShadowTick:
    def __init__(self, ledger: RiskPathLedger, *, owner_id: str):
        self.ledger = ledger
        self.jobs = PathShadowJobService(ledger, owner_id=owner_id)
        with ledger.database.session() as session:
            existing = session.scalar(
                select(RiskShadowTrialRecord).where(
                    RiskShadowTrialRecord.request_id == SHADOW_REQUEST,
                )
            )
            trial_id = existing.trial_id if existing else None
        if trial_id is None:
            now = ledger.now()
            first = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            trial_id = ledger.register(request_id=SHADOW_REQUEST, first_cutoff_at=first)
        # Restart and future builds keep the original deadline. No automatic
        # second trial is created after an insufficient/no-gain conclusion.
        self.trial_id = trial_id

    async def tick(self) -> int:
        task = asyncio.create_task(asyncio.to_thread(self.jobs.process_due, limit=4))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Cancellation cannot stop SQLite/CPU work in a thread. Join this
            # bounded owner before permitting another tick or shutting down.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not task.cancelled():
                task.exception()
            raise
