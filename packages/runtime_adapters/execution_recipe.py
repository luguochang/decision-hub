"""Credential-free runtime definitions for pinned evaluation execution."""

import hashlib
import json

from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError


def canonical_recipe(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def recipe_hash(value) -> str:
    return hashlib.sha256(canonical_recipe(value).encode("utf-8")).hexdigest()


def runtime_recipe(runtime) -> dict:
    reader = getattr(runtime, "execution_recipe", None)
    if reader:
        return json.loads(canonical_recipe(reader()))
    return {"schema_version": "execution-recipe.v1", "live_attestable": False,
            "runtime_id": runtime.runtime_id, "runtime_version": runtime.runtime_version,
            "max_attempts": runtime.max_attempts, "cost_budget": runtime.cost_budget}


def assert_recipe(runtime, expected: dict) -> None:
    if recipe_hash(runtime_recipe(runtime)) != recipe_hash(expected):
        raise AgentExecutionError("runtime_recipe_changed", "runtime differs from frozen recipe")
