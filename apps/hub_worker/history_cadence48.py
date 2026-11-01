"""Wake the existing guarded producer once per UTC hour, outside source work.

The clock gate only limits dispatch and idle IO. Original model authority,
durable first slots, unknown fences, cutoffs and classification remain in the
unchanged producer and outcome runtime.
"""

from __future__ import annotations

from time import monotonic

from apps.hub_worker.history_hourly41 import HistoryHourly41
from apps.hub_worker.history_hourly_v2 import SCOPES
from apps.hub_worker.history_outcome_runtime40 import HistoryOutcomeRuntime40
from apps.hub_worker.history_cadence_diagnostics50 import HistoryCadenceDiagnostics50


class HistoryCadence48:
    def __init__(self, hourly, outcome=None):
        self.hourly = hourly
        self.outcome = outcome
        self._attempted_hour = None
        self._busy = False
        self._outcome_diagnostics50 = HistoryCadenceDiagnostics50(outcome)

    def _hour(self):
        return self.hourly.originals.now().replace(minute=0, second=0, microsecond=0)

    async def tick_hourly(self):
        # The scheduler checks this cheap clock once a second, never the full
        # source/model reader after this hour's finite dispatch has finished.
        hour = self._hour()
        if self._busy or (self._attempted_hour is not None and hour <= self._attempted_hour):
            return 0
        self._busy = True
        self._attempted_hour = hour
        deadline = monotonic() + 115.0
        count = 0
        try:
            for _scope in SCOPES:
                # No second action is moved into a new hour by a slow first
                # action. No historical hour/cutoff is passed to the producer.
                remaining = min(90.0, deadline - monotonic())
                if remaining < 1.0 or self._hour() != hour:
                    break
                processed = await self.hourly.tick(remaining)
                count += processed
                if processed == 0:
                    break  # Durable slots/fences are still the only authority.
            return count
        finally:
            # Official tick joins its thread on cancellation before this owner
            # is released. A failed/unknown hour is not automatically replayed.
            self._busy = False

    async def tick_outcome(self):
        token = self._outcome_diagnostics50.enter(self.outcome)
        try:
            processed = 0 if self.outcome is None else await self.outcome.tick(115.0)
        except BaseException as error:
            self._outcome_diagnostics50.raised(self.outcome, token, error)
            raise
        self._outcome_diagnostics50.exit(self.outcome, token, processed)
        return processed


def install_history_cadence48_on_realtime(realtime, *, enhancement_tick):
    """Rebind only the already installed v41 producer and original runtime.

    ``enhancement_tick`` is captured explicitly before the legacy installers;
    no closure inspection, protocol creation or new grant occurs here.
    """
    scheduler = realtime.scheduler
    hourly = getattr(scheduler, "_history_hourly_v2", None)
    outcome = getattr(scheduler, "_history_outcome_v2", None)
    if hourly is None:
        return None
    if type(hourly) is not HistoryHourly41:
        raise ValueError("history_cadence48_actual_v41_producer_required")
    if outcome is not None and type(outcome) is not HistoryOutcomeRuntime40:
        raise ValueError("history_cadence48_actual_outcome_runtime_required")
    if (
        getattr(scheduler, "_history_cadence48", None) is not None
        or scheduler.history_hourly_tick is not None
        or scheduler.history_outcome_tick is not None
    ):
        raise ValueError("history_cadence48_already_bound")
    cadence = HistoryCadence48(hourly, outcome)
    scheduler.risk_enhancement_tick = enhancement_tick
    scheduler.history_hourly_tick = cadence.tick_hourly
    scheduler.history_outcome_tick = cadence.tick_outcome if outcome is not None else None
    scheduler._history_cadence48 = cadence
    return cadence
