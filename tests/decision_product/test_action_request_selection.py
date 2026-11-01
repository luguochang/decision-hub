"""Real temporary spot admission/Factory; no live provider or installed change."""

from pathlib import Path
from typing import Any

import pytest

from apps.hub_worker.research import ResearchRequestFactory
from packages.kernel.decision_hub_kernel.ports.research_synthesis_protocol import (
    DECISION_OUTPUT_SCHEMA_REF,
)
from tests.market_risk import test_spot_bootstrap_factory as baseline
from tests.market_risk.test_recipe_selection_v3 import (
    installation_fixture as installation_fixture,
)
from tests.market_risk.test_recipe_selection_v3 import (
    manifest as manifest,
)


def test_new_spot_task_selects_action_protocol_and_replay_keeps_frozen_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, installation_fixture: Any,
    record_property: Any,
) -> None:
    seen: list[str] = []

    class ActionFactory(ResearchRequestFactory):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs, decision_output_enabled=True)

        def build(self, event_id: str, run_id: str):
            first = super().build(event_id, run_id)
            assert first.output_schema_ref == DECISION_OUTPUT_SCHEMA_REF
            assert self.spot_factory is not None
            self.decision_output_enabled = self.spot_factory.decision_output_enabled = False
            try:
                repeated = super().build(event_id, run_id)
            finally:
                self.decision_output_enabled = self.spot_factory.decision_output_enabled = True
            assert repeated.model_dump_json() == first.model_dump_json()
            seen.append(run_id)
            return first

    # Only select the factory's supported configuration. All admission, source,
    # first-task writers and saved-original readers remain actual production.
    monkeypatch.setattr(baseline, "ResearchRequestFactory", ActionFactory)
    baseline.test_actual_composer_two_assets_first_tasks_and_corrupt_manifest_rejected(
        tmp_path, monkeypatch, installation_fixture, record_property,
    )
    assert len(set(seen)) == 2
