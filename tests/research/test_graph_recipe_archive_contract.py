"""Actual producer-source declaration contract; no retained code execution."""

from __future__ import annotations

import ast
from pathlib import Path

from packages.kernel.decision_hub_kernel.application import research_graph_recipe as recipe
from packages.kernel.decision_hub_kernel.application import research_recipe_archive as archive

ROOTS = (
    "packages.kernel.decision_hub_kernel.application.research_graph_binding",
    "packages.kernel.decision_hub_kernel.application.research_graph_recipe",
    "packages.kernel.decision_hub_kernel.application.research_graph_projection_v2",
    "packages.kernel.decision_hub_kernel.decision.citation_membership_v2",
    "packages.kernel.decision_hub_kernel.decision.action_comparison",
    "packages.kernel.decision_hub_kernel.decision.perpetual_publication",
)


def source_bytes() -> bytes:
    assert recipe.__file__ is not None
    return Path(recipe.__file__).read_bytes()


def test_actual_producer_source_keeps_the_original_archive_initial_contract() -> None:
    parser = vars(archive)["_recipe_initial_modules"]
    assert parser(source_bytes(), version=2) == ROOTS


def test_literal_archive_roots_equal_the_static_memo_root_declaration() -> None:
    raw = source_bytes()
    declarations = [
        node
        for node in ast.parse(raw).body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "_GRAPH_RECIPE_ROOTS"
    ]
    assert len(declarations) == 1
    static_roots = ast.literal_eval(declarations[0].value)
    parser = vars(archive)["_recipe_initial_modules"]
    assert type(static_roots) is tuple
    assert parser(raw, version=2) == static_roots == ROOTS
