from __future__ import annotations

import ast
from pathlib import Path


def test_kernel_does_not_import_orchestration_or_harness_frameworks() -> None:
    kernel_root = Path("packages/kernel")
    forbidden_prefixes = (
        "packages.orchestration",
        "langgraph",
        "langchain",
        "langchain_core",
    )
    violations: list[str] = []

    for path in sorted(kernel_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...]
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = (node.module,)
            else:
                continue
            for module in modules:
                if module.startswith(forbidden_prefixes):
                    violations.append(f"{path}:{node.lineno}: {module}")

    assert violations == []
