"""Install event-owned numerical forecasting on the normal realtime lane."""
import asyncio,logging
import os
from pathlib import Path
from time import monotonic
from packages.kernel.decision_hub_kernel.application.event_scenario_autoproduction import EventScenarioAutoproducer
from packages.kernel.decision_hub_kernel.application.event_lifecycle import EventLifecycleProcessor, _error
from packages.kernel.decision_hub_kernel.ports.event_scenario_autoproduction import EventScenarioProducerReport
log=logging.getLogger(__name__)

def install_event_scenario_on_realtime(realtime,database,*,archive=None):
    scheduler=realtime.scheduler
    if getattr(scheduler,'_event_scenario_autoproducer',None) is not None:raise ValueError('event_scenario_realtime_already_bound')
    producer=EventScenarioAutoproducer(database,archive=archive);original=scheduler.risk_enhancement_tick
    lifecycle=EventLifecycleProcessor(database, archive=archive,
        feature_root=Path(os.getenv('DECISION_HUB_DATA_DIR', 'data'))/'action-product'/'features')
    def advance():
        try:
            forecast_report=producer.tick()
        except Exception as exc:
            # A failed future input cannot starve already-owned event monitors.
            forecast_report=EventScenarioProducerReport(failed=1,errors=(_error('forecast',exc),))
        lifecycle_report=lifecycle.tick()
        return forecast_report,lifecycle_report
    last_started=None
    async def combined():
        nonlocal last_started
        now=monotonic()
        # Numerical polling cadence avoids reparsing public chart archives on
        # every scheduler tick. This does not cap async deep research duration.
        count=0
        if last_started is None or now-last_started>=60:
            last_started=now
            owned=asyncio.create_task(asyncio.to_thread(advance))
            try:
                report,lifecycle_report=await asyncio.shield(owned)
            except asyncio.CancelledError:
                # Join synchronous owned work; channel cancellation must not
                # detach a writer and start another copy on the next cadence.
                try:await owned
                except Exception:pass
                raise
            scheduler._event_scenario_last_report=report
            scheduler._event_lifecycle_last_report=lifecycle_report
            for code in report.errors:log.warning('event_scenario_auto_status code=%s',code)
            for code in lifecycle_report.errors:log.warning('event_lifecycle_status code=%s',code)
            count+=len(report.published_forecast_ids)
            count+=lifecycle_report.realizations_written+lifecycle_report.evaluations_written+lifecycle_report.outcomes_written
        # Event numbers cannot be starved by a slow unrelated history tick.
        return count+(0 if original is None else await original())
    scheduler.risk_enhancement_tick=combined
    scheduler._event_scenario_autoproducer=producer
    scheduler._event_lifecycle_processor=lifecycle
    return producer
