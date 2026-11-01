"""Explicit protocol routing inside the existing admission tick, no new queue/timer."""

from ..persistence.risk_research_records import RiskResearchRequestRecord
from .perpetual_event_research import PerpetualEventResearchService
from .perpetual_plan_followup import PerpetualPlanFollowupService
from .perpetual_research_schedule import PerpetualResearchScheduleService


class PerpetualResearchAdmissionDrivers:
    def __init__(self, database, entry, *, now, channel="local"):
        self.database = database
        self.periodic = PerpetualResearchScheduleService(database, entry, now=now)
        self.plan = PerpetualPlanFollowupService(database, entry, now=now, channel=channel)
        self.event = PerpetualEventResearchService(database, entry, now=now)
        self.protocol_version = self.periodic.protocol_version
        self.supported_protocols = (
            *self.periodic.supported_protocols,
            *self.plan.supported_protocols,
        )

    def _driver(self, row):
        if row.protocol_version in self.periodic.supported_protocols:
            return self.periodic
        if row.protocol_version in self.plan.supported_protocols:
            return self.plan
        raise ValueError("perpetual_admission_protocol_unsupported")

    def _for_key(self, key):
        with self.database.session() as session:
            row = session.get(RiskResearchRequestRecord, key, populate_existing=True)
            if row is None:
                raise ValueError("perpetual_admission_request_missing")
            return self._driver(row)

    def discover(self):
        self.plan.discover()
        self.periodic.discover()
        self.event.discover()

    async def collect_condition_evidence(self):
        from .perpetual_plan_watch_continuous import ContinuousPlanWatch, _latest, _latest_progress

        continuous = ContinuousPlanWatch(self.plan)
        with self.database.session() as session:
            watches = []
            for row in continuous._due_commands(session):
                receipt = _latest(session, row.target_id)
                progress = _latest_progress(session, row.target_id)
                if receipt and receipt.state == "active" and progress:
                    watches.append((receipt, progress))
        return await self.plan.condition_monitor.collect_due(watches)

    def recognize_in_session(self, session, row, at):
        return self._driver(row).recognize_in_session(session, row, at)

    def claimable_in_session(self, session, row, at):
        return self._driver(row).claimable_in_session(session, row, at)

    async def prepare(self, key, *args):
        return await self._for_key(key).prepare(key, *args)

    def admit(self, key, *args):
        return self._for_key(key).admit(key, *args)
