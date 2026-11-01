import asyncio
import threading
from datetime import timedelta

import pytest

from apps.hub_worker.market_risk_loops import PathShadowTick
from packages.kernel.decision_hub_kernel.application.market_path_store import MarketPathStore
from packages.kernel.decision_hub_kernel.application.risk_path_ledger import RiskPathLedger
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.persistence.path_forecast_records import (
    RiskShadowTrialRecord,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from tests.market_risk.test_market_data import NOW


@pytest.mark.asyncio
async def test_restart_keeps_deadline_and_cancellation_joins_the_original_thread(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'loop.sqlite'}")
    db.create_all()
    clock = [NOW]
    ledger = RiskPathLedger(
        db, MarketPathStore(db, RawBodyArchive(tmp_path)), clock=lambda: clock[0]
    )
    first = PathShadowTick(ledger, owner_id="one")
    clock[0] += timedelta(days=35)
    restarted = PathShadowTick(ledger, owner_id="two")
    assert first.trial_id == restarted.trial_id
    with db.session() as session:
        assert session.query(RiskShadowTrialRecord).count() == 1
    entered, release = threading.Event(), threading.Event()

    def bounded_work(*, limit):
        assert limit == 4
        entered.set()
        assert release.wait(3)
        return 0

    restarted.jobs.process_due = bounded_work
    task = asyncio.create_task(restarted.tick())
    await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    await asyncio.sleep(0.02)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    db.engine.dispose()
