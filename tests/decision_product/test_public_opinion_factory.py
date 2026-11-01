"""Real Task factory selection with synthetic article and controlled installation."""

from __future__ import annotations

from sqlalchemy import select

from apps.hub_worker.research import ResearchRequestFactory
from packages.kernel.decision_hub_kernel.application.research_signal import (
    ROLE_PREFIX,
    resolve_claim_signal_in_session,
)
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
from tests.decision_product.test_public_opinion_wiring import QUOTE, _context
from tests.market_risk import test_spot_bootstrap_factory as baseline
from tests.market_risk.test_recipe_selection_v3 import installation_fixture, manifest

assert installation_fixture is not None and manifest is not None


def test_actual_spot_factory_selects_and_freezes_public_claim_without_rewriting_existing_task(
    tmp_path,
    monkeypatch,
    installation_fixture,
    record_property,
):
    checked = []

    class FactoryWithOpinion(ResearchRequestFactory):
        def __init__(self, database, **kwargs):
            context = _context(tmp_path, database)
            context.importer.import_acquisition(
                "synthetic-acquisition",
                market_scopes=("crypto_macro",),
                instrument_scopes=(),
            )
            self.test_context = context
            super().__init__(
                database, decision_output_enabled=True, opinion_context=context.context, **kwargs
            )

        def build(self, event_id, run_id):
            request = super().build(event_id, run_id)
            claims = [
                item
                for item in request.input_evidence
                if item.evidence_id.startswith("library:claim_")
            ]
            assert len(claims) == 1
            assert QUOTE in claims[0].excerpt and claims[0].authority == "unverified"
            with self.database.session() as session:
                rows = session.scalars(
                    select(RunRoleOutputRecord).where(
                        RunRoleOutputRecord.run_id == run_id,
                        RunRoleOutputRecord.role.startswith(ROLE_PREFIX),
                    )
                ).all()
                assert len(rows) == 1
                frozen = resolve_claim_signal_in_session(
                    session,
                    run_id=run_id,
                    claim_id=claims[0].evidence_id.removeprefix("library:"),
                    cutoff=request.pit_cutoff_at,
                    store=self.test_context.store,
                )
                assert frozen is not None and frozen.source_attestation == "acquisition_verified"
                assert frozen.signal.version_id == claims[0].source_id.removeprefix(
                    "research-library:"
                )
                assert frozen.signal.supporting_quote == QUOTE
            original = request.model_dump(mode="json")
            self.opinion_context, self.decision_output_enabled = None, False
            assert super().build(event_id, run_id).model_dump(mode="json") == original
            self.opinion_context, self.decision_output_enabled = self.test_context.context, True
            checked.append(run_id)
            return request

    monkeypatch.setattr(baseline, "ResearchRequestFactory", FactoryWithOpinion)
    baseline.test_actual_composer_two_assets_first_tasks_and_corrupt_manifest_rejected(
        tmp_path,
        monkeypatch,
        installation_fixture,
        record_property,
    )
    assert len(checked) == 2
