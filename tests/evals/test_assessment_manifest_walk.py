"""The same selected bytes, without entering already-excluded archive payloads."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.evals.test_assessment_manifest_bindings import verifier as verifier

IGNORED = {"__pycache__", "node_modules", "tests", "data", ".git"}


def _write(root: Path, relative: str, raw: bytes = b"fixture\n") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def _old_selected(module: ModuleType) -> list[Path]:
    """The pre-pruning path selection, independent of the new walker."""
    selected = []
    for relative in module._SOURCE_DIRS:
        directory = module._ROOT / relative
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in module._SOURCE_SUFFIXES:
                continue
            if any(part in IGNORED for part in path.relative_to(module._ROOT).parts):
                continue
            if module._retained_payload_path(path.relative_to(module._ROOT)):
                continue
            selected.append(path)
    return selected


def _new_selected(module: ModuleType) -> list[Path]:
    assert hasattr(module, "_manifest_source_paths"), "pre-enumeration pruning is missing"
    selected = []
    for relative in module._SOURCE_DIRS:
        for path in module._manifest_source_paths(module._ROOT / relative, checkpoint=None):
            if (path.is_file() and path.suffix.lower() in module._SOURCE_SUFFIXES
                    and not any(part in IGNORED for part in path.relative_to(module._ROOT).parts)
                    and not module._retained_payload_path(path.relative_to(module._ROOT))):
                selected.append(path)
    return selected


def _maps(paths: list[Path], root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths}


def test_installed_payloads_and_ignored_directories_are_pruned_before_entering(
    verifier: tuple[ModuleType, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, root = verifier
    archive = "packages/kernel/research_recipe_versions/abcd0123"
    _write(root, archive + "/release.json", b"{}\n")
    _write(root, archive + "/root/deep/payload.py")
    _write(root, "packages/kernel/tests/deep/ignored.py")
    excluded = [root / archive / "root", root / "packages/kernel/tests"]
    original_walk = Path.walk

    def guarded_walk(
        path: Path, *args: Any, **kwargs: Any,
    ) -> Iterator[tuple[Path, list[str], list[str]]]:
        for current, directories, files in original_walk(path, *args, **kwargs):
            assert not any(current == item or item in current.parents for item in excluded), (
                "manifest enumerated an already-excluded directory"
            )
            yield current, directories, files

    monkeypatch.setattr(Path, "walk", guarded_walk)
    assert len(module.assessment_manifest_hash()) == 64


def test_pruning_preserves_selected_path_order_and_raw_sha_map(
    verifier: tuple[ModuleType, Path],
) -> None:
    module, root = verifier
    cases = [
        ("research_recipe_versions/abcd0123", "release.json", False),
        ("research_recipe_versions/" + "a" * 64, "release.json", False),
        ("research_recipe_versions/DEADBEEF", "release.json", True),
        ("research_recipe_versions/abcd01234", "release.json", True),
        ("research_recipe_versions/" + "b" * 64, None, True),
        ("market_component_versions/20261028T000000001Z", "index.json", False),
        ("market_component_versions/20261028T00000002Z", "index.json", True),
        ("market_component_versions/20261028T000000003Z", None, True),
    ]
    for relative, index, live in cases:
        prefix = "packages/kernel/" + relative
        if index:
            _write(root, prefix + "/" + index, b"{}\n")
        _write(root, prefix + "/source-binding.json", b"{}\n")
        _write(root, prefix + "/extra.json", b"{}\n")
        payload = _write(root, prefix + "/root/deep/payload.py")
        _write(root, prefix + "/not-root/visible.py")
        assert module._retained_payload_path(payload.relative_to(root)) is not live
    directory_index = "packages/kernel/market_component_versions/20261028T000000004Z"
    (root / directory_index / "index.json").mkdir(parents=True)
    _write(root, directory_index + "/root/visible.py")
    _write(root, "packages/kernel/unrelated/root/visible.py")
    _write(root, "packages/kernel/nested/Tests/visible.py")
    _write(root, "packs/UPPER.YAML")
    for ignored in IGNORED:
        _write(root, f"packages/kernel/{ignored}/deep/ignored.py")
    old, new = _old_selected(module), _new_selected(module)
    assert old == new
    assert _maps(old, root) == _maps(new, root)
    selected = _maps(new, root)
    for relative, index, live in cases:
        prefix = "packages/kernel/" + relative
        assert (prefix + "/root/deep/payload.py" in selected) is live
        assert prefix + "/source-binding.json" in selected
        assert prefix + "/extra.json" in selected
        if index:
            assert prefix + "/" + index in selected
    assert directory_index + "/root/visible.py" in selected
    assert "packages/kernel/nested/Tests/visible.py" in selected


def test_file_symlink_selection_matches_original_walk(
    verifier: tuple[ModuleType, Path], tmp_path: Path,
) -> None:
    module, root = verifier
    original = _write(tmp_path, "original.py")
    try:
        (root / "packs/linked.py").symlink_to(original)
    except OSError as error:
        pytest.skip(f"file symlink unavailable: {type(error).__name__}")
    old, new = _old_selected(module), _new_selected(module)
    assert old == new and _maps(old, root) == _maps(new, root)
    assert root / "packs/linked.py" in new


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction conformance")
def test_directory_junction_selection_matches_original_walk(
    verifier: tuple[ModuleType, Path], tmp_path: Path,
) -> None:
    module, root = verifier
    outside = tmp_path / "outside"
    _write(outside, "visible.py")
    script = tmp_path / "junction.ps1"
    script.write_text(
        "param([string]$Path,[string]$Target)\n"
        "New-Item -ItemType Junction -Path $Path -Target $Target -ErrorAction Stop | Out-Null\n",
        encoding="utf-8",
    )
    completed = subprocess.run([
        "pwsh", "-NoProfile", "-File", str(script), str(root / "packs/junction"), str(outside),
    ], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    old, new = _old_selected(module), _new_selected(module)
    assert old == new and _maps(old, root) == _maps(new, root)


def test_binding_metadata_still_authenticates_and_invalidates_after_pruning(
    verifier: tuple[ModuleType, Path],
) -> None:
    module, root = verifier
    prefix = "packages/kernel/market_component_versions/20261028T000000001Z"
    binding = _write(root, prefix + "/source-binding.json", b"binding-a")
    _write(root, prefix + "/index.json", json.dumps({
        "schema_version": "market-component-retained-release.v1",
        "source_binding_sha256": hashlib.sha256(b"binding-a").hexdigest(),
    }).encode())
    _write(root, prefix + "/root/deep/retained.py")
    before = module.assessment_manifest_hash()
    binding.write_bytes(b"binding-b")
    with pytest.raises(ValueError, match="assessment_basis_binding_index_invalid"):
        module.assessment_manifest_hash()
    _write(root, prefix + "/index.json", json.dumps({
        "schema_version": "market-component-retained-release.v1",
        "source_binding_sha256": hashlib.sha256(b"binding-b").hexdigest(),
    }).encode())
    assert module.assessment_manifest_hash() != before
