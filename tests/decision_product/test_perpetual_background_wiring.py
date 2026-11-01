"""Only composition/discovery wiring; financial service gates live in their packets."""

import ast
import inspect
import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

AT = datetime(2026, 10, 20, 12, tzinfo=UTC)


def test_event_discovery_follows_periodic_and_uses_existing_protocol(monkeypatch):
    from packages.kernel.decision_hub_kernel.application.perpetual_research_drivers import (
        PerpetualResearchAdmissionDrivers,
    )
    from packages.kernel.decision_hub_kernel.ports.perpetual_research_schedule import (
        EVENT_PROTOCOL,
    )

    database = object()
    entry = SimpleNamespace(database=database, total_deadline_seconds=3600)
    drivers = PerpetualResearchAdmissionDrivers(database, entry, now=lambda: AT)
    order = []
    monkeypatch.setattr(drivers.plan, "discover", lambda: order.append("plan"))
    monkeypatch.setattr(drivers.periodic, "discover", lambda: order.append("periodic"))
    monkeypatch.setattr(drivers.event, "discover", lambda: order.append("event"))
    drivers.discover()
    assert order == ["plan", "periodic", "event"]
    assert drivers._driver(SimpleNamespace(protocol_version=EVENT_PROTOCOL)) is drivers.periodic
    assert drivers.supported_protocols == (
        *drivers.periodic.supported_protocols,
        *drivers.plan.supported_protocols,
    )


@pytest.mark.asyncio
async def test_real_paper_services_share_funding_and_retry_in_existing_execution_thread(
    tmp_path, monkeypatch
):
    from apps.hub_worker import composition
    from packages.kernel.decision_hub_kernel.application.perpetual_paper_experience import (
        PerpetualPaperExperienceService,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import Database
    from packages.provider_adapters.research.raw_capture import RawBodyArchive

    database = Database(f"sqlite:///{tmp_path / 'wiring.sqlite3'}")
    archive = RawBodyArchive(tmp_path / "raw")
    manual, experience = composition.build_perpetual_paper_execution(
        database, archive, clock=lambda: AT
    )
    assert isinstance(experience, PerpetualPaperExperienceService)
    assert manual.funding_history is experience.funding_source
    assert experience.funding_source.database is database
    assert experience.funding_source.archive is archive
    assert manual.paper.source.archive is archive
    order = []

    class Execution:
        def process_due(self, *, max_intents, checkpoint):
            checkpoint()
            assert max_intents == 8
            order.append(("execution", threading.get_ident()))
            return {"processed": 1, "errors": []}

    class Bootstrap:
        def process_due(self, *, max_registrations, checkpoint):
            checkpoint()
            assert max_registrations == 4
            order.append(("bootstrap", threading.get_ident()))

    def retry(*, limit):
        assert limit == 4
        order.append(("experience", threading.get_ident()))
        return ({"status": "blocked" if len(order) == 3 else "recorded"},)

    monkeypatch.setattr(experience, "process_due", retry)
    tick = composition.build_paper_experience_execution_tick(
        Execution(), bootstrap=Bootstrap(), paper_experience=experience
    )
    assert await tick() == 1
    assert await tick() == 1
    assert [name for name, _ in order] == ["bootstrap", "execution", "experience"] * 2
    for start in (0, 3):
        assert len({ident for _, ident in order[start : start + 3]}) == 1
        assert order[start][1] != threading.get_ident()


def test_realtime_builder_uses_the_tested_paper_constructors_and_tick():
    from apps.hub_worker import composition

    tree = ast.parse(inspect.getsource(composition.build_realtime_worker))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    named = {node.func.id for node in calls if isinstance(node.func, ast.Name)}
    assert "build_perpetual_paper_execution" in named
    assert "build_paper_experience_execution_tick" in named
