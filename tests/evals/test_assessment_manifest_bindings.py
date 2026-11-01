"""Real bounded archive files; no database, release or financial certification."""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, BinaryIO, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "packages/evals/assessment_basis.py"
BODY = b'{"same-original.py":"' + b"a" * 64 + b'"}'


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def verifier(tmp_path: Path) -> tuple[ModuleType, Path]:
    """Load actual verifier source against an explicit, small test-only root."""
    root = tmp_path / "root"
    body = SOURCE.read_bytes()
    tree = ast.parse(body)
    directories = next(
        cast(tuple[str, ...], ast.literal_eval(node.value))
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_SOURCE_DIRS"
                for target in node.targets)
    )
    for relative in directories:
        (root / relative).mkdir(parents=True, exist_ok=True)
    gateway = root / "packages/workbench_adapters/durable_research_gateway.py"
    gateway.parent.mkdir(parents=True)
    gateway.write_bytes(b"GATEWAY = 1\n")
    path = root / "packages/evals/assessment_basis.py"
    name = "_assessment_manifest_binding_control"
    loaded = ModuleType(name)
    loaded.__file__ = str(path)
    sys.modules[name] = loaded
    try:
        exec(compile(body, str(path), "exec"), loaded.__dict__)
    finally:
        sys.modules.pop(name, None)
    return loaded, root


def _binding(root: Path, number: int, raw: bytes = BODY) -> Path:
    directory = root / "packages/kernel/market_component_versions" / f"20261028T000000{number:03d}Z"
    directory.mkdir(parents=True)
    path = directory / "source-binding.json"
    path.write_bytes(raw)
    _index(path, _sha(raw))
    return path


def _index(
    path: Path, digest: str, *, schema: str = "market-component-retained-release.v1",
) -> None:
    path.with_name("index.json").write_text(json.dumps({
        "schema_version": schema, "source_binding_sha256": digest,
    }), encoding="utf-8")


def test_duplicate_transport_is_charged_once_after_each_path_is_authenticated(
    verifier: tuple[ModuleType, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, root = verifier
    _binding(root, 1)
    second = _binding(root, 2)
    monkeypatch.setattr(module, "MAX_IMMUTABLE_BINDING_TOTAL_BYTES", len(BODY))
    first_manifest = module.assessment_manifest_hash()
    assert isinstance(first_manifest, str) and len(first_manifest) == 64
    # Both archived paths remain in the ABI even when their bytes are equal.
    second.unlink()
    assert module.assessment_manifest_hash() != first_manifest


def test_unique_transport_still_exhausts_the_original_content_budget(
    verifier: tuple[ModuleType, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, root = verifier
    _binding(root, 1)
    _binding(root, 2, BODY.replace(b"a", b"b"))
    monkeypatch.setattr(module, "MAX_IMMUTABLE_BINDING_TOTAL_BYTES", len(BODY))
    with pytest.raises(ValueError, match="^assessment_basis_immutable_binding_limit$"):
        module.assessment_manifest_hash()


def test_previously_seen_digest_never_skips_authentication_of_another_path(
    verifier: tuple[ModuleType, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, root = verifier
    _binding(root, 1)
    second = _binding(root, 2)
    second.write_bytes(BODY.replace(b"a", b"b"))
    monkeypatch.setattr(module, "MAX_IMMUTABLE_BINDING_TOTAL_BYTES", len(BODY))
    with pytest.raises(ValueError, match="^assessment_basis_binding_index_invalid$"):
        module.assessment_manifest_hash()


@pytest.mark.parametrize("fault", ["schema", "sha", "json", "shape"])
def test_invalid_binding_index_is_rejected(
    verifier: tuple[ModuleType, Path], fault: str,
) -> None:
    module, root = verifier
    path = _binding(root, 1)
    if fault == "schema":
        _index(path, _sha(BODY), schema="unregistered")
    elif fault == "sha":
        _index(path, "0" * 64)
    else:
        path.with_name("index.json").write_bytes(b"{" if fault == "json" else b"[]")
    with pytest.raises(ValueError, match="^assessment_basis_binding_index_invalid$"):
        module.assessment_manifest_hash()


@pytest.mark.parametrize("target", ["binding", "index"])
def test_single_transport_or_index_cannot_exceed_the_file_budget(
    verifier: tuple[ModuleType, Path], monkeypatch: pytest.MonkeyPatch, target: str,
) -> None:
    module, root = verifier
    path = _binding(root, 1, b"x" * 1025 if target == "binding" else BODY)
    if target == "index":
        path.with_name("index.json").write_bytes(b" " * 1025)
    monkeypatch.setattr(module, "MAX_SOURCE_FILE_BYTES", 1024)
    with pytest.raises(ValueError, match="^assessment_basis_source_limit$"):
        module.assessment_manifest_hash()


def test_duplicate_paths_still_count_toward_the_file_budget(
    verifier: tuple[ModuleType, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, root = verifier
    _binding(root, 1)
    _binding(root, 2)
    monkeypatch.setattr(module, "MAX_SOURCE_FILES", 3)
    with pytest.raises(ValueError, match="^assessment_basis_source_limit$"):
        module.assessment_manifest_hash()


def test_live_source_bytes_still_exhaust_their_independent_budget(
    verifier: tuple[ModuleType, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, root = verifier
    (root / "packs/live.py").write_bytes(b"a" * 11)
    (root / "packs/other.py").write_bytes(b"b" * 11)
    monkeypatch.setattr(module, "MAX_SOURCE_TOTAL_BYTES", 21)
    with pytest.raises(ValueError, match="^assessment_basis_source_limit$"):
        module.assessment_manifest_hash()


def test_same_length_binding_drift_changes_manifest_even_with_matching_new_index(
    verifier: tuple[ModuleType, Path],
) -> None:
    module, root = verifier
    path = _binding(root, 1)
    before = module.assessment_manifest_hash()
    changed = BODY.replace(b"a", b"b")
    assert len(changed) == len(BODY)
    path.write_bytes(changed)
    _index(path, _sha(changed))
    assert module.assessment_manifest_hash() != before


def test_binding_stream_is_bounded_and_checkpoints_between_chunks(
    verifier: tuple[ModuleType, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, root = verifier
    path = _binding(root, 1, b"x" * (3 * 65536 + 1))
    original_open = Path.open
    checkpoints = 0
    reads: list[int] = []

    def checkpoint() -> None:
        nonlocal checkpoints
        checkpoints += 1

    class Reader:
        def __init__(self, raw: BinaryIO) -> None:
            self.raw = raw
            self.last_checkpoint = -1

        def __enter__(self) -> Reader:
            return self

        def __exit__(self, *_args: object) -> None:
            self.raw.close()

        def read(self, size: int = -1) -> bytes:
            assert 0 < size <= 65536, "binding read must have a bounded chunk"
            assert checkpoints > self.last_checkpoint, "checkpoint missing between chunks"
            self.last_checkpoint = checkpoints
            reads.append(size)
            return self.raw.read(size)

    def opened(candidate: Path, *args: Any, **kwargs: Any) -> Any:
        stream = original_open(candidate, *args, **kwargs)
        return Reader(cast(BinaryIO, stream)) if candidate == path else stream

    monkeypatch.setattr(Path, "open", opened)
    module.assessment_manifest_hash(checkpoint=checkpoint)
    assert len(reads) >= 4
