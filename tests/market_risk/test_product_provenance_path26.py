"""Pure path equivalence and frozen receipt replay; no product SQL or authority."""

from __future__ import annotations

import hashlib
import importlib.util
import itertools
import json
import sys
from collections.abc import Callable
from pathlib import Path
from time import perf_counter, thread_time
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
FORMAL = ROOT.parent / "decision-hub"
ARTIFACT = ROOT / "artifacts/market-risk-implementation-20261007/B2/product-provenance-path26"
SOURCE = "packages/kernel/decision_hub_kernel/application/product_release_provenance.py"
RECEIPTS = (
    (
        "artifacts/mr-release-b2b4-21b/product-validated-builds.json",
        "0ba4a22a23f7dcf47d346cfc4ed46f55718d4f9da0a10dacda4367369ca5fcf4",
    ),
    (
        "artifacts/mr-release-b2b4-26/product-validated-builds.json",
        "f9b8509cfcf0ca4df60874cba01f71c029c592a4c0effb436447e3b443b3396f",
    ),
)


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def codecs() -> tuple[ModuleType, ModuleType]:
    before = ARTIFACT / "before/product_release_provenance.py"
    assert hashlib.sha256(before.read_bytes()).hexdigest() == (
        "737249ccc1258c391f50dbc253bd1f1a5071a0af39457fb91357f918aa01ce94"
    )
    return _load(before, "path26_before"), _load(ROOT / SOURCE, "path26_after")


def _save(name: str, value: object) -> None:
    target = ARTIFACT / "execution01" / name
    target.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def _outcome(codec: ModuleType, value: object) -> tuple[str, object]:
    try:
        return "accepted", codec.relative_path(value)
    except ValueError as error:
        return "rejected", str(error)


def _reader(codec: ModuleType) -> Callable[[str], bytes]:
    def read(name: str) -> bytes:
        return codec.read_repository_file(FORMAL, name)

    return read


def _bad_resource(_name: str) -> bytes:
    return b"{}"


def test_path_predicate_exact_equivalence(codecs: tuple[ModuleType, ModuleType]) -> None:
    class TextSubclass(str):
        pass

    values: list[object] = [
        None,
        True,
        7,
        b"a",
        Path("a"),
        {},
        [],
        TextSubclass("a"),
        "",
        "/a",
        "//a",
        "a/",
        "a//b",
        ".",
        "..",
        "a/./b",
        "a/../b",
        "a.",
        "a ",
        "a./b",
        "a /b",
        "C:/a",
        "C:a",
        "\\a",
        "a\\b",
        "CON",
        "NUL",
        "a..b",
        "a b/c",
        "p/" + "x" * 65_536 + ".py",
    ]
    for code in (*range(256), 0x2028, 0x2029, 0x2060, 0xFEFF, 0xD800, 0xDFFF, 0x10FFFF):
        char = chr(code)
        values.extend((char + "x", "x" + char + "y", "x" + char))
    for value in values:
        assert _outcome(codecs[0], value) == _outcome(codecs[1], value), repr(value)
    for code in range(32):
        assert _outcome(codecs[1], "a" + chr(code) + "b")[0] == "rejected"
    for char in '\\<>:"|?*':
        assert _outcome(codecs[1], "a" + char + "b")[0] == "rejected"
    for char in ("\x7f", "\x80", "\u2028", "\ud800", "\udfff"):
        assert _outcome(codecs[1], "a" + char + "b") == ("accepted", "a" + char + "b")
    _save("predicate01.json", {"cases": len(values), "equivalent": True, "SQL": 0, "HTTP": 0})


@pytest.fixture(scope="module")
def actual_maps(codecs: tuple[ModuleType, ModuleType]) -> tuple[list[str], list[dict[str, Any]]]:
    names: list[str] = []
    observations: list[dict[str, Any]] = []
    for ref, digest in RECEIPTS:
        raw = (FORMAL / ref).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == digest
        resolved = []
        timings = []
        for codec in codecs:
            wall, cpu = perf_counter(), thread_time()
            result = codec.resolve_product_receipt(
                raw,
                expected_root=FORMAL,
                expected_sha256=digest,
                read_resource=_reader(codec),
            )
            timings.append(
                {"wall_seconds": perf_counter() - wall, "cpu_seconds": thread_time() - cpu}
            )
            resolved.append(result)
        left, right = resolved
        assert left.raw == right.raw == raw and left.sha256 == right.sha256 == digest
        assert dict(left.value) == dict(right.value)
        assert dict(left.source_sizes) == dict(right.source_sizes)
        assert dict(left.transport) == dict(right.transport)
        assert tuple(dict(x) for x in left.owners) == tuple(dict(x) for x in right.owners)
        logical = dict(right.value["sources"])
        names.extend(logical)
        assert all(codec.relative_path(name) == name for codec in codecs for name in logical)
        observations.append(
            {
                "path": ref,
                "sha256": digest,
                "raw_bytes": len(raw),
                "schema": right.value["schema"],
                "sources": len(logical),
                "artifacts": len(right.value["artifacts"]),
                "transport_resources": len(right.transport),
                "owners": len(right.owners),
                "full_map_equal": True,
                "before_after": timings,
            }
        )
    return names, observations


def test_actual_v1_v2_maps_and_transport_rejections(
    codecs: tuple[ModuleType, ModuleType],
    actual_maps: tuple[list[str], list[dict[str, Any]]],
) -> None:
    for codec in codecs:
        for ref, _ in RECEIPTS:
            raw = (FORMAL / ref).read_bytes()
            value = json.loads(raw)
            value["sources"]["../escape.py"] = "0" * 64
            with pytest.raises(ValueError, match="product_provenance_path_invalid"):
                codec.resolve_product_receipt(
                    codec.canonical(value),
                    expected_root=FORMAL,
                    read_resource=_reader(codec),
                )
            with pytest.raises(ValueError, match="product_provenance_receipt_changed"):
                codec.resolve_product_receipt(
                    raw,
                    expected_root=FORMAL,
                    expected_sha256="0" * 64,
                    read_resource=_reader(codec),
                )
        raw = (FORMAL / RECEIPTS[1][0]).read_bytes()
        with pytest.raises(ValueError, match="product_provenance_resource_changed"):
            codec.resolve_product_receipt(raw, expected_root=FORMAL, read_resource=_bad_resource)
    _save(
        "actual-maps01.json",
        {
            "observations": actual_maps[1],
            "negative_cases": 10,
            "authority_granted": False,
            "SQL": 0,
            "HTTP": 0,
            "models": 0,
        },
    )


def test_fixed_path_workload_cpu_comparison(
    codecs: tuple[ModuleType, ModuleType],
    actual_maps: tuple[list[str], list[dict[str, Any]]],
) -> None:
    corpus = tuple(itertools.islice(itertools.cycle(actual_maps[0]), 100_000))
    observations = []
    for codec in codecs:
        wall, cpu = perf_counter(), thread_time()
        for name in corpus:
            assert codec.relative_path(name) == name
        observations.append(
            {
                "calls": len(corpus),
                "wall_seconds": perf_counter() - wall,
                "cpu_seconds": thread_time() - cpu,
            }
        )
    _save(
        "cpu-comparison01.json",
        {
            "before_after": observations,
            "cpu_ratio_after_before": observations[1]["cpu_seconds"]
            / observations[0]["cpu_seconds"],
            "actual_quality_latency_measured": False,
            "actual_callback_latency_measured": False,
            "SQL": 0,
            "HTTP": 0,
            "models": 0,
        },
    )
