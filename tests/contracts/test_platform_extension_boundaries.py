from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_platform_core_does_not_import_harness_or_domain_implementations() -> None:
    forbidden_prefixes = (
        "packages.orchestration",
        "packages.runtime_adapters",
        "packs",
        "langgraph",
        "langchain",
        "langchain_core",
        "dsh",
    )
    violations: list[str] = []

    for path in sorted((ROOT / "packages/kernel").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = (node.module,)
            else:
                continue
            for module in modules:
                if module.startswith(forbidden_prefixes):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: {module}")

    assert violations == []


def test_domain_pack_is_data_only_and_does_not_own_business_history() -> None:
    pack_root = ROOT / "packs" / "crypto_macro"
    assert list(pack_root.rglob("*.py")) == []

    disallowed_terms = (
        "create table",
        "drop table",
        "alembic",
        "active_pointer =",
        "automatic_trade",
    )
    violations: list[str] = []
    for path in sorted(pack_root.rglob("*")):
        if not path.is_file() or path.suffix not in {".yaml", ".json", ".md"}:
            continue
        lowered = path.read_text(encoding="utf-8").lower()
        for term in disallowed_terms:
            if term in lowered:
                violations.append(f"{path.relative_to(ROOT)}: {term}")

    assert violations == []


def test_asr_is_kept_behind_future_text_source_boundary() -> None:
    schema = (
        (ROOT / "contracts/schemas/agentic_research.schema.yaml")
        .read_text(encoding="utf-8")
        .lower()
    )
    pack_manifest = (ROOT / "packs/crypto_macro/pack.yaml").read_text(encoding="utf-8").lower()

    assert "asr" not in schema
    assert "asr" not in pack_manifest
    assert "langgraph" not in pack_manifest
    assert "dsh" not in pack_manifest
