"""Offline workflow DESIGN validator. No nodes, tools, models or IO are executed.

Only the three adjacent design files are read. Admission is a caller-owned
fixture argument, not authentication. Production needs original-bound catalog,
permissions, actual inputs/outputs, reservations and DSH native-use receipts.
"""
from __future__ import annotations

import copy
import heapq
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

BASE = Path(__file__).parent
SCHEMA = json.loads((BASE / "workflow.schema.json").read_text("utf-8"))
Draft202012Validator.check_schema(SCHEMA)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
OP_CATALOG = frozenset({"fact.select", "evidence.acquire", "component.compute",
                        "case.retrieve", "hypothesis.check", "decision.compose", "monitor.propose"})


class WorkflowContractError(ValueError):
    """A stable semantic failure code; raw source or credentials are not exposed."""


def _schema(catalog: dict, reference: str) -> dict:
    value = catalog.get(reference)
    if not isinstance(value, dict):
        raise WorkflowContractError("workflow_schema_ref_unknown")
    # Reject references rather than allowing jsonschema to retrieve remote URLs.
    def no_refs(item: object) -> None:
        if isinstance(item, dict):
            if any(key in item for key in ("$ref", "$dynamicRef", "$recursiveRef")):
                raise WorkflowContractError("workflow_schema_ref_unsupported")
            for child in item.values():
                no_refs(child)
        elif isinstance(item, list):
            for child in item:
                no_refs(child)
    no_refs(value)
    try:
        Draft202012Validator.check_schema(value)
    except Exception as error:
        raise WorkflowContractError("workflow_catalog_schema_invalid") from error
    return value


def _field(schema: dict, pointer: str) -> dict:
    current = schema
    for part in pointer.split("/")[1:] if pointer else ():
        properties = current.get("properties", {})
        if part not in properties:
            raise WorkflowContractError("workflow_binding_field_unknown")
        current = properties[part]
    return current


def _types(schema: dict) -> set[str]:
    kind = schema.get("type")
    if isinstance(kind, str):
        return {kind}
    if isinstance(kind, list) and all(isinstance(item, str) for item in kind):
        return set(kind)
    # This reference supports explicit primitive/object/array types only.
    raise WorkflowContractError("workflow_binding_schema_unsupported")


def _compatible(source: dict, target: dict) -> bool:
    expected = _types(target)
    if "number" in expected:
        expected.add("integer")
    return _types(source).issubset(expected)


def _binding(binding: dict, target: dict, *, predecessor_ids: set[str],
             nodes: dict, catalog: dict, root_input: dict) -> None:
    if binding["from"] == "literal":
        if not Draft202012Validator(target, format_checker=FormatChecker()).is_valid(binding["value"]):
            raise WorkflowContractError("workflow_literal_invalid")
        return
    if binding["from"] == "workflow_input":
        source = _field(root_input, binding["pointer"])
    else:
        reference = binding["node_id"]
        if reference not in nodes:
            raise WorkflowContractError("workflow_binding_node_missing")
        if reference not in predecessor_ids:
            raise WorkflowContractError("workflow_binding_not_predecessor")
        source = _field(_schema(catalog, nodes[reference]["output_schema"]), binding["pointer"])
    if not _compatible(source, target):
        raise WorkflowContractError("workflow_binding_type_mismatch")


def _bindings(bindings: dict, target: dict, **context) -> None:
    properties = target.get("properties", {})
    if not set(target.get("required", ())).issubset(bindings):
        raise WorkflowContractError("workflow_input_binding_missing")
    if not set(bindings).issubset(properties):
        raise WorkflowContractError("workflow_input_binding_unknown")
    for name, binding in bindings.items():
        _binding(binding, properties[name], **context)


def validate_workflow(workflow: dict, admission: dict) -> list[str]:
    """Return a validation order only. Does not compile or execute a runtime."""
    errors = list(VALIDATOR.iter_errors(workflow))
    if errors:
        if any(error.validator == "enum" and tuple(error.path)[-1:] == ("op",) for error in errors):
            raise WorkflowContractError("workflow_op_not_installed")
        raise WorkflowContractError("workflow_schema_invalid")
    if workflow["scope_id"] not in admission["allowed_scope_ids"]:
        raise WorkflowContractError("workflow_scope_not_admitted")
    if workflow["capability_catalog_hash"] != admission["catalog_hash"]:
        raise WorkflowContractError("workflow_catalog_binding_changed")
    permitted = set(admission["allowed_capabilities"])
    required = set(workflow["required_capabilities"])
    if not required.issubset(permitted):
        raise WorkflowContractError("workflow_capability_not_admitted")
    catalog = admission["schema_catalog"]
    root_input = _schema(catalog, workflow["input_schema"])
    root_output = _schema(catalog, workflow["output_schema"])
    nodes = {node["id"]: node for node in workflow["nodes"]}
    if len(nodes) != len(workflow["nodes"]):
        raise WorkflowContractError("workflow_node_id_duplicate")
    for node in nodes.values():
        if node["op"] not in OP_CATALOG:
            raise WorkflowContractError("workflow_op_not_installed")
        if not set(node["depends_on"]).issubset(nodes):
            raise WorkflowContractError("workflow_dependency_missing")
        _schema(catalog, node["input_schema"])
        _schema(catalog, node["output_schema"])
        contract = admission["op_contracts"].get(node["op"])
        expected = {"input_schema": node["input_schema"], "output_schema": node["output_schema"]}
        if contract != expected:
            raise WorkflowContractError("workflow_op_contract_mismatch")
        if node["op"] == "evidence.acquire":
            capability = node["capability_id"]
            if capability not in permitted:
                raise WorkflowContractError("workflow_capability_not_admitted")
            if capability not in required:
                raise WorkflowContractError("workflow_capability_undeclared")
            if admission["capability_contracts"].get(capability) != expected:
                raise WorkflowContractError("workflow_capability_contract_mismatch")
        if node["op"] == "component.compute":
            component = admission["installed_components"].get(node["component_ref"])
            if component is None:
                raise WorkflowContractError("workflow_component_not_installed")
            if component != expected:
                raise WorkflowContractError("workflow_component_contract_mismatch")
    # Kahn's algorithm; deterministic order is metadata, not a task runner.
    indices = {key: index for index, key in enumerate(nodes)}
    remaining = {key: set(node["depends_on"]) for key, node in nodes.items()}
    ready = [(indices[key], key) for key, deps in remaining.items() if not deps]
    heapq.heapify(ready)
    order = []
    while ready:
        _, key = heapq.heappop(ready)
        order.append(key)
        for other, deps in remaining.items():
            if key in deps:
                deps.remove(key)
                if not deps:
                    heapq.heappush(ready, (indices[other], other))
    if len(order) != len(nodes):
        raise WorkflowContractError("workflow_dependency_cycle")
    ancestors: dict[str, set[str]] = {}
    for key in order:
        node = nodes[key]
        predecessors = set(node["depends_on"])
        for dependency in node["depends_on"]:
            predecessors.update(ancestors[dependency])
        ancestors[key] = predecessors
        _bindings(node["inputs"], _schema(catalog, node["input_schema"]),
                  predecessor_ids=predecessors, nodes=nodes, catalog=catalog, root_input=root_input)
    _bindings(workflow["exports"], root_output, predecessor_ids=set(nodes),
              nodes=nodes, catalog=catalog, root_input=root_input)
    return order


def _patched(source: dict, patches: list[dict]) -> dict:
    result = copy.deepcopy(source)
    for patch in patches:
        pieces = patch["path"].split("/")[1:]
        target = result
        for piece in pieces[:-1]:
            target = target[int(piece)] if isinstance(target, list) else target[piece]
        key = int(pieces[-1]) if isinstance(target, list) else pieces[-1]
        if patch.get("op") == "remove":
            del target[key]
        else:
            target[key] = copy.deepcopy(patch["value"])
    return result


def run_checks() -> list[dict]:
    """Independent synthetic golden fixtures; no production qualification."""
    example = json.loads((BASE / "workflow.example.json").read_text("utf-8"))
    if example.get("data_kind") != "synthetic_contract_fixture" or example.get("not_runtime_admission") is not True:
        raise WorkflowContractError("workflow_fixture_marker_missing")
    results = []
    for case in example["golden"]:
        try:
            order = validate_workflow(_patched(example["workflow"], case["patch"]), copy.deepcopy(example["admission"]))
            actual = "valid"
            if "expected_order" in case and order != case["expected_order"]:
                actual = "workflow_order_mismatch"
        except WorkflowContractError as error:
            actual = str(error)
        except Exception as error:
            actual = "unexpected:" + type(error).__name__
        results.append({"id": case["id"], "passed": actual == case["expected"],
                        "expected": case["expected"], "actual": actual})
    return results


if __name__ == "__main__":
    checks = run_checks()
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    raise SystemExit(0 if checks and all(check["passed"] for check in checks) else 1)
