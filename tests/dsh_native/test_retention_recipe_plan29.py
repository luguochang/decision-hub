"""Three file-only controls for the v2 recipe change routing; no producer/DB/Host."""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

import pytest

from packages.kernel.decision_hub_kernel.application import research_recipe_archive as archive

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "artifacts/market-risk-implementation-20261007/B2/retention-recipe-plan29"
BINDING = "packages/kernel/decision_hub_kernel/application/research_graph_binding.py"
ACTUAL_COMPONENT = (
    "packages/kernel/market_component_versions/20261009T020357183Z/source-binding.json"
)
OVERSIZED = b"#" + b"x" * (2 * 1024 * 1024)


def _routing_filter() -> Callable[[str], bool]:
    spec = importlib.util.spec_from_file_location(
        "prepared_recipe_plan29", ROOT / "infra/dsh/prepare-product-retention.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # The script module's callable is exposed through this typed dynamic seam.
    return cast(
        Callable[[str], bool],
        getattr(module, "is_recipe_manifest_input"),  # noqa: B009
    )


def _write(root: Path, name: str, raw: bytes) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def _baseline(root: Path) -> None:
    for name in archive._INITIAL:  # pyright: ignore[reportPrivateUsage]
        _write(root, name.replace(".", "/") + ".py", b"VALUE = 'original'\n")
    _write(root, BINDING, b"from packages.control import old_module\n")
    _write(root, "packages/control/__init__.py", b"__all__ = ()\n")
    _write(root, "packages/control/old_module.py", b"OLD = True\n")
    _write(root, "contracts/schemas/changed.json", b"{}\n")
    _write(root, "contracts/schemas/deleted.yml", b"type: string\n")


def _manifest(root: Path, overlay: Mapping[str, bytes | None]) -> dict[str, object]:
    return archive._planned_manifest(  # pyright: ignore[reportPrivateUsage]
        root, overlay, {"python": "isolated-control"},
    )


def test_full_and_routed_overlays_have_identical_real_v2_manifest(tmp_path: Path) -> None:
    root = tmp_path / "isolated-root"
    _baseline(root)
    _write(root, ACTUAL_COMPONENT, OVERSIZED)
    complete: dict[str, bytes | None] = {
        BINDING: b"from packages.control import new_module\n",
        "packages/control/new_module.py": b"NEW = True\n",
        "packages/control/old_module.py": None,
        "contracts/schemas/changed.json": b'{"type":"object"}\n',
        "contracts/schemas/added.yaml": b"type: integer\n",
        "contracts/schemas/deleted.yml": None,
        ACTUAL_COMPONENT: None,
        "packages/unrelated-large.json": OVERSIZED,
        "apps/decision-desk/src/unrelated.ts": b"export const value = 1;\n",
    }
    keep = _routing_filter()
    routed = {name: raw for name, raw in complete.items() if keep(name)}
    assert ACTUAL_COMPONENT not in routed and "packages/unrelated-large.json" not in routed
    complete_manifest = _manifest(root, complete)
    assert complete_manifest == _manifest(root, routed)
    assert complete_manifest != _manifest(root, {})
    source_names = {
        row["path"] for row in cast(list[dict[str, str]], complete_manifest["source_files"])
    }
    schema_names = {
        row["path"] for row in cast(list[dict[str, str]], complete_manifest["schema_files"])
    }
    assert "packages/control/new_module.py" in source_names
    assert "packages/control/old_module.py" not in source_names
    assert schema_names == {"contracts/schemas/changed.json", "contracts/schemas/added.yaml"}


def test_recipe_python_and_each_real_schema_suffix_keep_original_2mib_rejection(
    tmp_path: Path,
) -> None:
    root = tmp_path / "isolated-root"
    _baseline(root)
    keep = _routing_filter()
    for name in (
        BINDING, "contracts/schemas/large.json", "contracts/schemas/large.yaml",
        "contracts/schemas/large.yml",
    ):
        assert keep(name), name
        path = root / name
        previous = path.read_bytes() if path.exists() else None
        _write(root, name, OVERSIZED)
        with pytest.raises(ValueError, match="research_recipe_archive_resource_oversized"):
            _manifest(root, {})
        if previous is None:
            path.unlink()
        else:
            path.write_bytes(previous)


def test_first_failure_component_binding_is_not_a_recipe_change() -> None:
    diagnosis = json.loads((EVIDENCE / "diagnosis01.json").read_text(encoding="utf-8"))
    inventory = json.loads((EVIDENCE / "actual29-candidate-source-inventory.json").read_text(
        encoding="utf-8",
    ))
    assert diagnosis["path"] == ACTUAL_COMPONENT
    assert diagnosis["baseline_sha256"] == (
        "74f6c0def1337bfd15da5bced6b4db57c59b18dd62321b210a8a4080ee0803ba"
    )
    assert diagnosis["baseline_bytes"] == 2120246
    assert diagnosis["root_sha256"] == diagnosis["baseline_sha256"]
    assert diagnosis["root_bytes"] == diagnosis["baseline_bytes"]
    assert ACTUAL_COMPONENT not in inventory
    assert diagnosis["candidate_hash"] is None and diagnosis["candidate_path"] is None
    assert not _routing_filter()(ACTUAL_COMPONENT)
