"""Private construction controls, not a Main source/issuer or publication proof.

Registry/SQL lifecycle are explicit seams. Real factory path checks, source-root
binding, preparer and archive file IO execute. API/Worker tests execute only their
actual assembly statements, avoiding module-level API/DB startup.
"""

from __future__ import annotations

import ast
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol, cast

import pytest

from packages.kernel.decision_hub_kernel.application import (
    research_publication_runtime48 as runtime,
)

REPOSITORY = Path(__file__).resolve().parents[2]
ENVIRONMENT = "DECISION_HUB_PUBLICATION_DATA_DIR"
REQUIRED = {
    "research_publication_bundle_jobs", "research_publication_bundles",
    "research_publication_bundle_confirmations", "research_publication_bundle_state_events",
}


class Rooted(Protocol):
    root: Path


class Preparer(Rooted, Protocol):
    stage_parent: Path
    authority: Rooted


class Archive(Rooted, Protocol):
    def exclusive(self, relative: str, raw: bytes) -> None: ...


class Lifecycle(Protocol):
    archive: Archive
    preparer: Preparer


class Service(Protocol):
    lifecycle: Lifecycle

    def close(self) -> None: ...


class Factory(Protocol):
    def __call__(
        self, *, database: object, repository_root: Path,
        publication_data_root: object = None,
    ) -> Service: ...


class ConstructionLifecycle:
    def __init__(self, database: object, archive: Archive, *, preparer: Preparer) -> None:
        self.database, self.archive, self.preparer = database, archive, preparer

    def close(self) -> None:
        pass


@dataclass
class Construction:
    root: Path
    database: object
    tables: set[str]

    def create(self, **options: object) -> Service:
        factory = cast(Factory, runtime.create_managed_publication_bundle_service)
        return factory(database=self.database, repository_root=self.root, **options)


@pytest.fixture
def construction(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Construction]:
    root = tmp_path / "source-root"
    root.mkdir()
    control = Construction(root, SimpleNamespace(engine=object()), set(REQUIRED))
    monkeypatch.setattr(runtime.normal_history, "_CURRENT_ROOT", root)
    monkeypatch.setattr(runtime.normal_history, "_REGISTRY_BINDING", object())
    monkeypatch.setattr(runtime.normal_history, "FROZEN_PUBLICATION_HOST_CONTRACT",
                        "publication-frozen-host.v1")
    monkeypatch.setattr(runtime.normal_recipe, "FROZEN_PUBLICATION_CURRENT_CONTRACT",
                        "publication-current-installation.v1")

    class Inspection:
        def get_table_names(self) -> list[str]:
            return sorted(control.tables)

    def inspect_engine(_engine: object) -> Inspection:
        return Inspection()

    monkeypatch.setattr(runtime, "inspect", inspect_engine)
    monkeypatch.setattr(runtime, "DurablePublicationLifecycle", ConstructionLifecycle)
    yield control


def test_default_and_explicit_none_preserve_original_layout(construction: Construction) -> None:
    for options in ({}, {"publication_data_root": None}):
        service = construction.create(**options)
        try:
            assert service.lifecycle.preparer.stage_parent == (
                construction.root.parent / ".decision-hub-publication-stage"
            )
            assert service.lifecycle.archive.root == (
                construction.root / "artifacts/research-publication-bundles"
            )
            assert service.lifecycle.preparer.authority.root == construction.root
        finally:
            service.close()


def test_explicit_instance_owns_files_without_changing_source_root(
    construction: Construction, tmp_path: Path,
) -> None:
    instance = tmp_path / "private-instance"
    service = construction.create(publication_data_root=instance)
    try:
        assert service.lifecycle.preparer.root == construction.root
        assert service.lifecycle.preparer.authority.root == construction.root
        assert service.lifecycle.preparer.stage_parent == instance / "stage"
        assert service.lifecycle.archive.root == instance / "archive"
        assert (instance / "stage").is_dir()
        reference = "bundles/" + "a" * 64 + ".json"
        service.lifecycle.archive.exclusive(reference, b'{"controlled":"archive"}')
        reopened = runtime.BundleArchive(instance / "archive", max_bundle_bytes=1024,
                                        max_material_bytes=1024)
        assert (reopened.root / reference).read_bytes() == b'{"controlled":"archive"}'
        assert not (construction.root / "artifacts").exists()
        assert not (construction.root.parent / ".decision-hub-publication-stage").exists()
    finally:
        service.close()


@pytest.mark.parametrize("case", ["relative", "parent", "root", "child", "bool", "str", "file"])
def test_explicit_invalid_root_rejects_without_default_fallback(
    construction: Construction, tmp_path: Path, case: str,
) -> None:
    file = tmp_path / "not-a-directory"
    file.write_bytes(b"controlled")
    values: dict[str, object] = {
        "relative": Path("private-relative"), "parent": tmp_path / "x/../private",
        "root": construction.root, "child": construction.root / "private",
        "bool": True, "str": str(tmp_path / "private"), "file": file,
    }
    with pytest.raises(ValueError):
        construction.create(publication_data_root=values[case])
    assert not (construction.root / "artifacts").exists()
    assert not (construction.root.parent / ".decision-hub-publication-stage").exists()
    assert file.read_bytes() == b"controlled"


@pytest.mark.parametrize("child", ["stage", "archive"])
def test_existing_child_file_is_rejected_before_creating_other_child(
    construction: Construction, tmp_path: Path, child: str,
) -> None:
    instance = tmp_path / "private"
    instance.mkdir()
    (instance / child).write_bytes(b"controlled-existing-file")
    with pytest.raises(ValueError):
        construction.create(publication_data_root=instance)
    assert sorted(path.name for path in instance.iterdir()) == [child]
    assert (instance / child).read_bytes() == b"controlled-existing-file"


def test_symlink_ancestor_cannot_redirect_instance_data(
    construction: Construction, tmp_path: Path,
) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "alias"
    try:
        link.symlink_to(actual, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlink unavailable: {type(error).__name__}")
    with pytest.raises(ValueError):
        construction.create(publication_data_root=link / "instance")
    assert not (actual / "instance").exists()


def test_reparse_attribute_is_rejected_even_without_is_symlink(
    construction: Construction, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = tmp_path / "reparse-control"
    instance.mkdir()
    original = Path.lstat

    def lstat(path: Path) -> os.stat_result:
        observed = original(path)
        if path == instance:
            return cast(
                os.stat_result, SimpleNamespace(st_file_attributes=0x400, st_mode=observed.st_mode),
            )
        return observed

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ValueError):
        construction.create(publication_data_root=instance)
    assert list(instance.iterdir()) == []


@pytest.mark.parametrize("failure", ["registry", "root", "contract", "migration"])
def test_existing_authority_preconditions_still_precede_private_directory_writes(
    construction: Construction, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    if failure == "registry":
        monkeypatch.setattr(runtime.normal_history, "_REGISTRY_BINDING", None)
    elif failure == "root":
        monkeypatch.setattr(runtime.normal_history, "_CURRENT_ROOT", tmp_path / "foreign")
    elif failure == "contract":
        monkeypatch.setattr(runtime.normal_recipe, "FROZEN_PUBLICATION_CURRENT_CONTRACT", "wrong")
    else:
        construction.tables.remove("research_publication_bundle_jobs")
    instance = tmp_path / "private"
    with pytest.raises(ValueError):
        construction.create(publication_data_root=instance)
    assert not instance.exists()


def _consumer(relative: str, factory: Callable[..., object]) -> object:
    source = REPOSITORY / relative
    tree = ast.parse(source.read_bytes(), filename=str(source))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.If)):
            continue
        body = node.body
        for index, statement in enumerate(body):
            if (isinstance(statement, ast.ImportFrom) and statement.module is not None
                    and statement.module.endswith("research_publication_runtime48")):
                statements = body[index + 1:]
                stop = next(i for i, item in enumerate(statements)
                            if isinstance(item, ast.FunctionDef)
                            and item.name == "confirmed_report_snapshot_in_session")
                module = ast.Module(body=statements[:stop], type_ignores=[])
                namespace: dict[str, object] = {
                    "Path": Path, "os": os, "__file__": str(source), "db": "api-database",
                    "database": "worker-database",
                    "development": None,
                    "create_managed_publication_bundle_service": factory,
                    "research": SimpleNamespace(worker=SimpleNamespace(min_priority=None)),
                }
                exec(compile(ast.fix_missing_locations(module), str(source), "exec"), namespace)
                return namespace["publication_service"]
    raise AssertionError("original publication assembly not found")


@pytest.mark.parametrize("relative", ["apps/hub_api/main.py", "apps/hub_worker/main.py"])
@pytest.mark.parametrize("configured", [False, True])
def test_api_and_worker_only_forward_explicit_instance_data(
    relative: str, configured: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = tmp_path / "instance"
    if configured:
        monkeypatch.setenv(ENVIRONMENT, str(instance))
    else:
        monkeypatch.delenv(ENVIRONMENT, raising=False)
    calls: list[dict[str, object]] = []
    result = object()

    def factory(**kwargs: object) -> object:
        calls.append(kwargs)
        return result

    assert _consumer(relative, factory) is result
    expected: dict[str, object] = {
        "database": "api-database" if "hub_api" in relative else "worker-database",
        "repository_root": REPOSITORY,
    }
    if configured:
        expected["publication_data_root"] = instance
    assert calls == [expected]
