"""Same-Run frozen materials for pure Advice fixtures; no execution admission."""

from pathlib import Path
from typing import Any

from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
    read_perpetual_research_materials_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_task import ResearchTaskService
from tests.decision_product.test_perpetual_research_binding import factory_for

from packages.contracts_py.decision_hub_contracts import ResearchSessionRequest


def publication_materials(case: dict[str, Any]) -> tuple[ResearchSessionRequest, dict[str, Any]]:
    database = case["db"]
    request = ResearchTaskService(database).get(case["run_id"])
    if request is None:
        filename = database.engine.url.database
        if not isinstance(filename, str):
            raise ValueError("advice_fixture_private_database_path_required")
        directory = Path(filename).parent / "advice-task"
        directory.mkdir(exist_ok=True)
        factory, _ = factory_for(case, directory)
        request = factory.build(case["event_id"], case["run_id"])
    with database.session() as session:
        materials = read_perpetual_research_materials_in_session(session, request)
    return request, materials
