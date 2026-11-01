"""BEA national Personal Income and Outlays release schedule, not PCE actuals."""

from datetime import datetime

from packages.contracts_py.decision_hub_contracts.models import TextEnvelope
from packages.source_adapters.official_feeds.calendar import OfficialCalendarSource

BEA_SCHEDULE_URL = "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics"
BEA_SCHEDULE_VERSION = "bea-personal-income-schedule.v1"


class BeaReleaseScheduleSource(OfficialCalendarSource):
    """Reuse native ICS timing, cursor revisions and capture infrastructure.

    The BEA calendar also contains GDP, state and county income releases.
    Those titles do not identify the national monthly PCE release package.
    DTSTART precision is preserved; a date without a time never implies 08:30.
    """

    def _parse_events(self, body: bytes, received_at: datetime) -> list[TextEnvelope]:
        return [
            event
            for event in super()._parse_events(body, received_at)
            if event.raw_text.casefold().startswith("personal income and outlays,")
        ]
