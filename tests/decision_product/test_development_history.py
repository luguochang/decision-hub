"""Real finite capture and terminal read-scope boundaries, without a fake report.

Full historical publication authentication uses the two genuine reports in the
separate production-adapter acceptance. These local rows prove scope eligibility
only; no accepted research result or publication receipt is manufactured here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from packages.kernel.decision_hub_kernel.application import (
    development_history_snapshot as snapshots,
)
from packages.kernel.decision_hub_kernel.application import (
    research_graph_history_versions as history,
)
from packages.kernel.decision_hub_kernel.application.development_history_snapshot import (
    native_history_path,
)
from packages.kernel.decision_hub_kernel.application.research_graph_recipe import (
    GraphRecipeManifestV2,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_json,
    observation_sha,
)
from packages.runtime_adapters.development_runtime import (
    development_runtime_from_environment,
)
from tests.decision_product.development_history_test_support import (
    HistoryCase,
    changed_file,
    development_environment,
    original_manifest,
    sha,
)
from tests.decision_product.development_history_test_support import (
    history_case as history_case,
)


def index_path(case: HistoryCase) -> Path:
    return case.snapshot.root.parent / "index.json"


def test_real_manifest_capture_is_complete_and_never_a_formal_registration(
    history_case: HistoryCase,
) -> None:
    case = history_case
    manifest = original_manifest(case)
    assert manifest == case.manifest
    assert case.snapshot.repository_root == case.repository
    assert case.snapshot.development_root == case.development.root
    assert case.snapshot.root == (
        case.development.root / "runtime/history-snapshots"
        / case.snapshot.dependency_hash / "root"
    )
    index = native_history_path(index_path(case)).read_bytes()
    assert sha(index) == case.snapshot.index_sha256
    body = json.loads(index)
    assert index.decode() == observation_json(body)
    assert body["schema_version"] == "development-history-snapshot.v1"
    assert body["private_original_orm"] is True
    assert body["manifest"] == manifest.model_dump(mode="json")
    assert GraphRecipeManifestV2.model_validate_json(
        observation_json(body["manifest"]),
    ) == manifest
    expected = {
        item.path: item.payload_hash
        for item in (*manifest.source_files, *manifest.schema_files)
    }
    assert [item["path"] for item in body["files"]] == sorted(expected)
    for item in body["files"]:
        raw = native_history_path(case.snapshot.root / item["path"]).read_bytes()
        assert item["payload_hash"] == expected[item["path"]] == sha(raw)
        assert item["bytes"] == len(raw)
    assert history._REGISTRY_BINDING is None  # pyright: ignore[reportPrivateUsage]


def test_same_identity_reuse_does_not_replace_the_existing_snapshot(
    history_case: HistoryCase,
) -> None:
    case = history_case
    files = [index_path(case), case.snapshot.root / case.manifest.source_files[0].path]
    before = [
        (native_history_path(p).read_bytes(), native_history_path(p).stat().st_mtime_ns)
        for p in files
    ]
    second = snapshots.capture_current_development_snapshot(case.repository, case.development)
    assert second == case.snapshot
    assert [
        (native_history_path(p).read_bytes(), native_history_path(p).stat().st_mtime_ns)
        for p in files
    ] == before


def test_regular_hardlinked_source_leaf_is_copied_to_an_isolated_snapshot(
    history_case: HistoryCase,
) -> None:
    case = history_case
    item = original_manifest(case).source_files[0]
    source = case.repository / item.path
    alias = case.repository / "controlled-source-hardlink-alias"
    source_io = native_history_path(source)
    alias_io = native_history_path(alias)
    before = source_io.read_bytes()
    assert sha(before) == item.payload_hash
    assert source_io.stat().st_nlink == 1
    assert not alias_io.exists()
    root = case.repository / "data/development/hardlinked-source-control"
    assert not native_history_path(root).exists()
    development = development_runtime_from_environment(
        case.repository, environment=development_environment(case.repository, root),
    )
    assert development is not None
    try:
        os.link(source_io, alias_io)
        assert source_io.stat().st_nlink == 2
        captured = snapshots.capture_current_development_snapshot(case.repository, development)
        target_io = native_history_path(captured.root / item.path)
        assert captured.dependency_hash == case.snapshot.dependency_hash
        assert captured.manifest_json == case.snapshot.manifest_json
        assert target_io.stat().st_nlink == 1
        assert target_io.read_bytes() == before
        assert sha(target_io.read_bytes()) == item.payload_hash
        assert source_io.stat().st_nlink == 2
        assert source_io.read_bytes() == before
    finally:
        if alias_io.exists():
            alias_io.unlink()
    assert source_io.stat().st_nlink == 1


def test_windows_long_physical_paths_preserve_logical_manifest_and_exact_bytes(
    history_case: HistoryCase,
) -> None:
    case = history_case
    manifest = original_manifest(case)
    entries = (*manifest.source_files, *manifest.schema_files)
    physical_paths = [case.snapshot.root / item.path for item in entries]
    assert max(len(str(path.absolute())) for path in physical_paths) > 260
    assert not str(case.snapshot.root).startswith(("\\\\?\\", "//?/"))
    for item in entries:
        assert not item.path.startswith(("\\\\?\\", "//?/"))
        assert "\\" not in item.path
        assert not Path(item.path).is_absolute()
    reopened, payloads = snapshots.read_development_history_snapshot(
        case.snapshot, case.snapshot.manifest_json, case.snapshot.dependency_hash,
    )
    assert reopened == case.snapshot
    assert reopened.manifest_json == case.snapshot.manifest_json
    assert set(payloads) == {item.path for item in entries}
    for item in entries:
        physical = (reopened.root / item.path).absolute()
        native = native_history_path(physical)
        assert native.read_bytes() == payloads[item.path]
        assert sha(payloads[item.path]) == item.payload_hash


@pytest.mark.parametrize("kind,missing", [
    ("source", False), ("source", True), ("schema", False), ("schema", True),
])
def test_existing_identity_rejects_changed_or_missing_declared_physical_bytes(
    history_case: HistoryCase, kind: str, missing: bool,
) -> None:
    case = history_case
    item = (case.manifest.source_files if kind == "source" else case.manifest.schema_files)[0]
    target = case.snapshot.root / item.path
    with changed_file(
        target,
        None if missing else native_history_path(target).read_bytes() + b"\ncontrolled-drift",
    ):
        with pytest.raises(ValueError, match="development_history_"):
            snapshots.capture_current_development_snapshot(case.repository, case.development)
        if missing:
            assert not native_history_path(target).exists()
        else:
            assert native_history_path(target).read_bytes().endswith(b"controlled-drift")
    assert snapshots.capture_current_development_snapshot(
        case.repository, case.development,
    ) == case.snapshot


@pytest.mark.parametrize("damage", ["schema", "runtime", "dependency", "duplicate", "traversal"])
def test_rehashed_index_damage_does_not_authenticate_snapshot(
    history_case: HistoryCase, damage: str,
) -> None:
    case = history_case
    path = index_path(case)
    body = json.loads(native_history_path(path).read_bytes())
    if damage == "schema":
        body["schema_version"] = "development-history-snapshot.v999"
    elif damage == "runtime":
        body["manifest"]["runtime_versions"]["python"] += "-controlled-drift"
    elif damage == "dependency":
        body["dependency_hash"] = "b" * 64
    elif damage == "duplicate":
        body["files"].append(dict(body["files"][0]))
    else:
        body["files"][0]["path"] = "../outside.py"
    with changed_file(path, observation_json(body).encode()):
        with pytest.raises(ValueError, match="development_history_"):
            snapshots.capture_current_development_snapshot(case.repository, case.development)


def test_missing_index_is_not_silently_repaired_by_bootstrap(
    history_case: HistoryCase,
) -> None:
    case = history_case
    path = index_path(case)
    with changed_file(path, None):
        with pytest.raises(ValueError, match="development_history_"):
            snapshots.capture_current_development_snapshot(case.repository, case.development)
        assert not native_history_path(path).exists()


def test_undeclared_payload_is_not_allowed_inside_a_verified_identity(
    history_case: HistoryCase,
) -> None:
    case = history_case
    target = case.snapshot.root / "undeclared-controlled-test.py"
    assert not native_history_path(target).exists()
    native_history_path(target).write_text(
        "# not declared by the actual manifest\n", encoding="utf-8",
    )
    try:
        with pytest.raises(ValueError, match="development_history_"):
            snapshots.capture_current_development_snapshot(case.repository, case.development)
        assert native_history_path(target).exists(), (
            "verification must not silently repair a damaged identity"
        )
    finally:
        native_history_path(target).unlink()


def test_current_clone_drift_is_not_accepted_under_an_existing_dependency_hash(
    history_case: HistoryCase,
) -> None:
    case = history_case
    item = case.manifest.source_files[0]
    target = case.repository / item.path
    with changed_file(
        target, native_history_path(target).read_bytes() + b"\n# controlled current clone drift\n",
    ):
        with pytest.raises(ValueError, match="development_history_"):
            snapshots.capture_current_development_snapshot(case.repository, case.development)


def test_shared_module_index_hash_is_not_an_arbitrary_current_module_alias(
    history_case: HistoryCase,
) -> None:
    case = history_case
    path = index_path(case)
    body = json.loads(native_history_path(path).read_bytes())
    shared = body["shared_original_hashes"]
    assert set(shared) == {
        "packages/kernel/decision_hub_kernel/persistence/db.py",
        "packages/kernel/decision_hub_kernel/application/research_context_selection_versions.py",
    }
    shared[sorted(shared)[0]] = "c" * 64
    with changed_file(path, observation_json(body).encode()):
        with pytest.raises(ValueError, match="development_history_"):
            snapshots.capture_current_development_snapshot(case.repository, case.development)


@pytest.mark.parametrize("runtime", ["python", "SQLAlchemy"])
def test_runtime_drift_is_rejected_even_with_a_recomputed_manifest_digest(
    history_case: HistoryCase, runtime: str,
) -> None:
    case = history_case
    changed = case.manifest.model_dump(mode="json")
    changed["runtime_versions"][runtime] += "-controlled-drift"
    # This is a negative supplied selection, not an authenticated saved row.
    with pytest.raises(ValueError, match="development_history_snapshot_invalid"):
        snapshots.read_development_history_snapshot(
            case.snapshot, observation_json(changed), observation_sha(changed),
        )


@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_linked_snapshot_payload_is_rejected_without_following_it(
    history_case: HistoryCase, link: str,
) -> None:
    case = history_case
    target = case.snapshot.root / case.manifest.source_files[0].path
    raw = native_history_path(target).read_bytes()
    external = case.development.root / "linked-source-original"
    native_history_path(external).write_bytes(raw)
    native_history_path(target).unlink()
    try:
        try:
            if link == "symlink":
                native_history_path(target).symlink_to(native_history_path(external))
            else:
                os.link(native_history_path(external), native_history_path(target))
        except OSError as error:
            pytest.skip(f"local {link} unavailable: {type(error).__name__}")
        with pytest.raises(ValueError, match="development_history_"):
            snapshots.capture_current_development_snapshot(case.repository, case.development)
    finally:
        if native_history_path(target).is_symlink() or native_history_path(target).exists():
            native_history_path(target).unlink()
        native_history_path(target).write_bytes(raw)
        native_history_path(external).unlink()


