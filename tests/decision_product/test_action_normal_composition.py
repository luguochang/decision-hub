"""Normal construction in a private DB; no Host execution or product startup."""

from pathlib import Path

import pytest

from apps.hub_worker.composition import build_research_worker
from packages.kernel.decision_hub_kernel.application.action_publication import (
    ActionPublicationService,
)
from packages.kernel.decision_hub_kernel.application.action_research_integration import (
    ActionResearchIntegration,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.runtime_adapters.dsh_runtime import DshResearchRuntime


@pytest.fixture
def private_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    monkeypatch.setenv("DECISION_HUB_DOCUMENT_CORRECTION_LLM_ENABLED", "0")
    monkeypatch.delenv("DECISION_HUB_ACTION_PRODUCT_ENABLED", raising=False)
    monkeypatch.delenv("DECISION_HUB_SPOT_RESEARCH_ENABLED", raising=False)
    monkeypatch.delenv("DECISION_HUB_DOCUMENT_BUNDLE_ROOT", raising=False)
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'composition.sqlite3'}")
    database.create_all()
    try:
        yield database
    finally:
        database.engine.dispose()


def test_default_normal_composition_keeps_legacy_factories(private_database: Database):
    research = build_research_worker(
        private_database, runtime=DshResearchRuntime(), allowed_capabilities=("replay.research",)
    )
    assert research.worker.factory.decision_output_enabled is False
    assert research.worker.prepared_factory is not None
    assert research.worker.prepared_factory.decision_output_enabled is False
    assert research.worker.spot_prepared_factory is None
    assert research.worker.action_integration.publisher.document_store is None


@pytest.mark.parametrize("raw", ["true", "2", " 1"])
def test_invalid_action_flag_rejected_before_runtime_or_storage(
    private_database: Database, monkeypatch: pytest.MonkeyPatch, raw: str
):
    monkeypatch.setenv("DECISION_HUB_ACTION_PRODUCT_ENABLED", raw)
    monkeypatch.setenv("DECISION_HUB_RESEARCH_RUNTIME", "must-not-be-read")
    with pytest.raises(ValueError, match="^action_product_flag_invalid$"):
        build_research_worker(private_database)


def test_action_requires_spot_before_runtime_or_storage(
    private_database: Database, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DECISION_HUB_ACTION_PRODUCT_ENABLED", "1")
    monkeypatch.setenv("DECISION_HUB_RESEARCH_RUNTIME", "must-not-be-read")
    with pytest.raises(ValueError, match="^action_product_spot_research_required$"):
        build_research_worker(private_database)


def test_normal_enabled_composition_uses_configured_document_store(
    private_database: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    bundle_root = tmp_path / "dedicated-bundles"
    monkeypatch.setenv("DECISION_HUB_ACTION_PRODUCT_ENABLED", "1")
    monkeypatch.setenv("DECISION_HUB_SPOT_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("DECISION_HUB_DOCUMENT_BUNDLE_ROOT", str(bundle_root))
    research = build_research_worker(
        private_database,
        runtime=DshResearchRuntime(),
        allowed_capabilities=("replay.research", "spot_market_facts"),
    )
    worker = research.worker
    assert worker.spot_prepared_factory is not None
    assert worker.spot_prepared_factory.decision_output_enabled is True
    assert isinstance(worker.action_integration, ActionResearchIntegration)
    assert isinstance(worker.action_integration.publisher, ActionPublicationService)
    store = worker.action_integration.publisher.document_store
    assert store is not None and store.root == bundle_root.resolve()
    context = worker.spot_prepared_factory.opinion_context
    assert context is not None
    assert context.library.store is store
    assert context.library.public_opinion_importer is not None
    assert context.library.public_opinion_importer.library is context.library
    # Constructors do not publish a result or perform a model request.
    assert worker.action_integration.publisher.database is private_database
