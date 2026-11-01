"""Isolated source runtime boundaries; never a release or financial certificate."""

from __future__ import annotations

import ast
import asyncio
import importlib
import importlib.util
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

REPOSITORY = Path(__file__).resolve().parents[1]
RUNTIME = "packages.runtime_adapters.development_runtime"
PREFIX = "DECISION_HUB_"


def _runtime() -> ModuleType:
    assert importlib.util.find_spec(RUNTIME), "development pre-database guard is missing"
    return importlib.import_module(RUNTIME)


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    repository = tmp_path / "source"
    repository.mkdir()
    root = repository / "data/development/control"
    for name in os.environ:
        if name.startswith(PREFIX) and any(part in name for part in (
            "HARNESS_", "HISTORY_", "RETAINED_RECIPE_", "SETTINGS_PRIVATE_",
        )):
            monkeypatch.delenv(name, raising=False)
    values = {
        "DEVELOPMENT_MODE": "1", "DEVELOPMENT_ROOT": str(root),
        "DATA_DIR": str(root / "data"), "DSH_HOME": str(root / "dsh-home"),
        "CHECKPOINT_PATH": str(root / "data/checkpoints/decision_graph.sqlite3"),
        "PUBLICATION_DATA_DIR": str(
            repository.parent / ".decision-hub-development/control/publication"
        ),
        "SOURCE_ACQUISITION_ROOT": str(root / "data/source-acquisition"),
        "DOCUMENT_BUNDLE_ROOT": str(root / "data/document-bundles"),
        "SPOT_RESEARCH_ENABLED": "0", "ACTION_PRODUCT_ENABLED": "0",
        "ACTION_REVIEW_ENABLED": "0", "ACTION_EXECUTION_ENABLED": "0",
        "RESEARCH_LEARNING_ENABLED": "0", "DURABLE_SKILL_CALLBACKS_ENABLED": "0",
        "RESEARCH_SKILLS_ENABLED": "0", "LLM_ENABLED": "0",
        "OPERATIONS_RESEARCH_RUNTIME": "", "RESEARCH_RUNTIME": "fake",
    }
    for name, value in values.items():
        monkeypatch.setenv(PREFIX + name, value)
    return repository, root


def test_development_guard_accepts_only_its_paths_without_creating_them(
    layout: tuple[Path, Path],
) -> None:
    repository, root = layout
    selected = _runtime().development_runtime_from_environment(repository)
    assert selected is not None and selected.root == root
    assert not root.exists()
    assert not (repository.parent / ".decision-hub-development").exists()


@pytest.mark.parametrize("name,value", [
    ("DEVELOPMENT_MODE", "yes"), ("DEVELOPMENT_ROOT", "data/development/control"),
    ("DATA_DIR", "data/product-local"), ("DSH_HOME", "data/dsh-live"),
    ("CHECKPOINT_PATH", "data/formal.sqlite3"),
    ("PUBLICATION_DATA_DIR", "artifacts/research-publication-bundles"),
    ("DOCUMENT_BUNDLE_ROOT", "data/formal-documents"),
    ("SOURCE_ACQUISITION_ROOT", "data/formal-acquisition"),
    ("RETAINED_RECIPE_RECEIPT", "formal-receipt.json"),
    ("RETAINED_RECIPE_RECEIPT_SHA256", "a" * 64),
    ("HISTORY_PRIVATE_CONFIG", "formal-history.json"),
    ("HISTORY_QUALIFICATION_PRIVATE_CONFIG_SHA256", "a" * 64),
    ("HARNESS_COMPARE_CONFIG", "formal-harness.json"),
    ("ACTION_EXECUTION_ENABLED", "1"), ("SPOT_RESEARCH_ENABLED", "1"),
    ("RESEARCH_LEARNING_ENABLED", "true"),
])
def test_bad_development_environment_is_rejected_without_directory_writes(
    layout: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, name: str, value: str,
) -> None:
    repository, root = layout
    monkeypatch.setenv(PREFIX + name, value)
    with pytest.raises(ValueError, match="development_"):
        _runtime().development_runtime_from_environment(repository)
    assert not root.exists()


@pytest.mark.parametrize("name", ["DEVELOPMENT_ROOT", "DATA_DIR", "CHECKPOINT_PATH", "DSH_HOME"])
def test_required_development_paths_cannot_silently_fall_back(
    layout: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    repository, root = layout
    monkeypatch.delenv(PREFIX + name)
    with pytest.raises(ValueError, match="development_"):
        _runtime().development_runtime_from_environment(repository)
    assert not root.exists()


@pytest.mark.parametrize("location", ["ancestor", "child", "hardlink"])
def test_linked_development_data_is_rejected(
    layout: tuple[Path, Path], tmp_path: Path, location: str,
) -> None:
    repository, root = layout
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        if location == "ancestor":
            (root / "data").symlink_to(outside, target_is_directory=True)
        elif location == "child":
            (root / "data").mkdir()
            (root / "data/child").symlink_to(outside, target_is_directory=True)
        else:
            original = outside / "formal.txt"
            original.write_bytes(b"original")
            (root / "data").mkdir()
            (root / "data/shared.txt").hardlink_to(original)
    except OSError as error:
        pytest.skip(f"local link unavailable: {type(error).__name__}")
    with pytest.raises(ValueError, match="development_path_linked"):
        _runtime().development_runtime_from_environment(repository)


def test_formal_mode_keeps_existing_environment_contract(
    layout: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _root = layout
    monkeypatch.setenv(PREFIX + "DEVELOPMENT_MODE", "0")
    monkeypatch.setenv(PREFIX + "RETAINED_RECIPE_RECEIPT", "existing-formal-receipt.json")
    assert _runtime().development_runtime_from_environment(repository) is None


@pytest.fixture(scope="module")
def api_module() -> Iterator[ModuleType]:
    """Execute the actual API module without its eager default app construction."""
    path = REPOSITORY / "apps/hub_api/main.py"
    tree = ast.parse(path.read_bytes(), str(path))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "app" for target in node.targets)
    )]
    name = "_development_api_composition_control"
    module = ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    try:
        exec(compile(tree, str(path), "exec"), module.__dict__)
        yield module
    finally:
        sys.modules.pop(name, None)


def _point_guard_at_fixture(
    module: ModuleType, repository: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def validate(_source: Path) -> Any:
        return _runtime().development_runtime_from_environment(repository)

    monkeypatch.setattr(
        module, "development_runtime_from_environment", validate, raising=False,
    )


def test_api_rejects_bad_development_paths_before_constructing_database(
    api_module: ModuleType, layout: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, root = layout
    _point_guard_at_fixture(api_module, repository, monkeypatch)
    monkeypatch.setenv(PREFIX + "DATA_DIR", str(repository / "formal-data"))

    def forbidden_database() -> None:
        raise AssertionError("database constructed before development validation")

    monkeypatch.setattr(api_module, "Database", forbidden_database)
    with pytest.raises(ValueError, match="development_"):
        api_module.create_app()
    assert not root.exists()


def test_development_api_skips_formal_factory_and_closes_without_fake_confirmation(
    api_module: ModuleType, layout: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from packages.kernel.decision_hub_kernel.persistence.db import Database

    repository, root = layout
    _point_guard_at_fixture(api_module, repository, monkeypatch)
    runtime = importlib.import_module(
        "packages.kernel.decision_hub_kernel.application.research_publication_runtime48"
    )

    def forbidden_factory(**_kwargs: Any) -> None:
        raise AssertionError("development called formal publication factory")

    monkeypatch.setattr(runtime, "create_managed_publication_bundle_service", forbidden_factory)
    database = Database()
    readers: list[Any] = []
    original = api_module.ReviewScheduleService

    def reviews(*args: Any, **kwargs: Any) -> Any:
        readers.append(kwargs["confirmed_report_reader"])
        return original(*args, **kwargs)

    monkeypatch.setattr(api_module, "ReviewScheduleService", reviews)
    try:
        app = api_module.create_app(database=database, source_connectors=[], sources_enabled=False)
        with TestClient(app) as client:
            response = client.get("/v1/development/status")
            assert response.status_code == 200
            assert response.json() == {
                "mode": "development", "formal_publication_enabled": False,
                "financial_qualified": False, "execution_authorized": False,
            }
        assert readers
        with pytest.raises(ValueError, match="development_formal_publication_unavailable"):
            readers[0](None, "no-confirmed-run")
        paths = {route.path for route in app.routes if hasattr(route, "path")}
        assert "/v1/research/runs/{run_id}/report/recovery/status" not in paths
        assert not (repository.parent / ".decision-hub-development").exists()
        assert (root / "data/db/decision_hub.sqlite3").is_file()
    finally:
        database.engine.dispose()


def test_formal_api_still_requires_original_publication_factory(
    api_module: ModuleType, layout: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from packages.kernel.decision_hub_kernel.persistence.db import Database

    repository, _root = layout
    _point_guard_at_fixture(api_module, repository, monkeypatch)
    monkeypatch.setenv(PREFIX + "DEVELOPMENT_MODE", "0")
    runtime = importlib.import_module(
        "packages.kernel.decision_hub_kernel.application.research_publication_runtime48"
    )

    def required_factory(**_kwargs: Any) -> None:
        raise ValueError("original_formal_factory_required")

    monkeypatch.setattr(runtime, "create_managed_publication_bundle_service", required_factory)
    database = Database()
    try:
        with pytest.raises(ValueError, match="original_formal_factory_required"):
            api_module.create_app(database=database)
    finally:
        database.engine.dispose()


def test_worker_rejects_bad_development_environment_before_database(
    layout: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, root = layout
    worker = importlib.import_module("apps.hub_worker.main")
    _point_guard_at_fixture(worker, repository, monkeypatch)
    monkeypatch.setenv(PREFIX + "PUBLICATION_DATA_DIR", str(repository / "formal-publication"))
    monkeypatch.setattr(sys, "argv", ["hub-worker", "--role", "research", "--once"])

    def forbidden_database() -> None:
        raise AssertionError("worker database constructed before development validation")

    monkeypatch.setattr(worker, "Database", forbidden_database)
    with pytest.raises(ValueError, match="development_"):
        worker.run()
    assert not root.exists()


async def test_general_development_worker_keeps_usage_and_heartbeat_without_formal_publisher(
    layout: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _root = layout
    worker = importlib.import_module("apps.hub_worker.main")
    _point_guard_at_fixture(worker, repository, monkeypatch)
    runtime = importlib.import_module(
        "packages.kernel.decision_hub_kernel.application.research_publication_runtime48"
    )
    events: list[str] = []
    readers: list[Any] = []

    def forbidden_factory(**_kwargs: Any) -> None:
        raise AssertionError("development worker called formal publication factory")

    class Research:
        worker = SimpleNamespace(
            min_priority=None, runtime=object(),
            review_schedule=SimpleNamespace(bind_confirmed_report_reader=readers.append),
        )

        async def tick(self) -> None:
            await asyncio.sleep(0)
            raise RuntimeError("controlled_worker_stop")

        async def _heartbeat_while_running(self) -> None:
            events.append("heartbeat")
            await asyncio.Event().wait()

        async def close(self) -> None:
            events.append("closed")

    class Usage:
        def __init__(self, *_args: Any) -> None:
            pass

        async def run_forever(self, **_kwargs: Any) -> None:
            events.append("usage")
            await asyncio.Event().wait()

    def recovery(_runtime: object) -> None:
        events.append("recovery")

    def research_worker(*_args: Any, **_kwargs: Any) -> Research:
        return Research()

    def activity(*_args: Any) -> None:
        return None

    monkeypatch.setattr(runtime, "create_managed_publication_bundle_service", forbidden_factory)
    monkeypatch.setattr(worker, "build_research_worker", research_worker)
    monkeypatch.setattr(worker, "UsageCollector", Usage)
    monkeypatch.setattr(worker, "cancelled_session_recovery", recovery)
    monkeypatch.setattr(worker, "build_dsh_activity_collector", activity)
    with pytest.raises(RuntimeError, match="controlled_worker_stop"):
        await worker._run_loop(
            database=object(), registry=object(), settings=object(),
            role="research", interval=0.01, once=False,
        )
    assert {"usage", "heartbeat", "recovery", "closed"} <= set(events)
    assert readers
    with pytest.raises(ValueError, match="development_formal_publication_unavailable"):
        readers[0](None, "no-confirmed-run")


@pytest.mark.parametrize("name", ["DSH_HOME", "PUBLICATION_DATA_DIR", "CHECKPOINT_PATH"])
def test_development_paths_reject_the_wrong_file_kind_before_database_writes(
    layout: tuple[Path, Path], name: str,
) -> None:
    repository, _root = layout
    path = Path(os.environ[PREFIX + name])
    path.parent.mkdir(parents=True)
    if name == "CHECKPOINT_PATH":
        path.mkdir()
    else:
        path.write_bytes(b"not a directory")
    with pytest.raises(ValueError, match="development_path_invalid"):
        _runtime().development_runtime_from_environment(repository)


def test_development_worker_rejects_explicit_formal_baseline_before_database(
    layout: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, root = layout
    worker = importlib.import_module("apps.hub_worker.main")
    _point_guard_at_fixture(worker, repository, monkeypatch)
    monkeypatch.setattr(sys, "argv", [
        "hub-worker", "--role", "research", "--once",
        "--harness-release-baseline", str(repository / "formal-receipt.json"),
        "--harness-install-sha256", "a" * 64,
    ])

    def forbidden_database() -> None:
        raise AssertionError("development baseline reached database construction")

    monkeypatch.setattr(worker, "Database", forbidden_database)
    with pytest.raises(ValueError, match="development_release_features_forbidden"):
        worker.run()
    assert not root.exists()
