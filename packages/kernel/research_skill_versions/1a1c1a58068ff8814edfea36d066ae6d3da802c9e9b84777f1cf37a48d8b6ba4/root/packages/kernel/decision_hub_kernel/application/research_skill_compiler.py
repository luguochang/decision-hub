"""Pure skill compiler, staged for application/ during the deployment freeze.

Context is supplied by a future trusted selection reader, never tool arguments.
Compilation and concrete JSON validation do not authenticate native execution.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal, NoReturn, cast

from jsonschema import Draft202012Validator, FormatChecker
from packages.kernel.decision_hub_kernel.ports.component_registry import ComponentManifest

from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash

OPS = frozenset(
    {
        "fact.select",
        "evidence.acquire",
        "component.compute",
        "case.retrieve",
        "hypothesis.check",
        "decision.compose",
        "monitor.propose",
    }
)
_HASH = re.compile(r"^[a-f0-9]{64}$")
_POINTER = re.compile(r"^(?:/[A-Za-z_][A-Za-z0-9_]*)*$")
_JSON_TYPES = frozenset({"object", "array", "string", "number", "integer", "boolean", "null"})
_ANNOTATIONS = frozenset({"title", "description", "examples", "$schema", "$id", "$defs"})


class SkillCompileError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.error_code = code


def _fail(code: str) -> NoReturn:
    raise SkillCompileError(code)


def _json(value: Any) -> str:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    except (TypeError, ValueError) as error:
        raise SkillCompileError("skill_json_invalid") from error


def _decode(body: bytes | str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in items:
            if key in result:
                _fail("skill_json_duplicate_key")
            result[key] = value
        return result

    try:
        value = json.loads(
            body, object_pairs_hook=pairs, parse_constant=lambda _: _fail("skill_json_nonfinite")
        )
        _json(value)  # Also rejects overflow such as 1e999.
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise SkillCompileError("skill_json_invalid") from error


@dataclass(frozen=True)
class ResourceBytes:
    path: str
    body: bytes
    sha256: str


@dataclass(frozen=True)
class PortType:
    pointer: str
    json_types: tuple[str, ...]
    unit: str | None
    scope_id: str | None


@dataclass(frozen=True)
class SchemaDefinition:
    reference: str
    resource_path: str
    schema_hash: str
    ports: tuple[PortType, ...]


@dataclass(frozen=True)
class InstalledOperation:
    op: str
    input_contract: str
    output_contract: str
    adapter_code_hash: str


@dataclass(frozen=True)
class CapabilitySurface:
    protocol_ref: str
    adapter_version: str
    adapter_code_hash: str
    input_schema_hash: str
    output_schema_hash: str
    scope_ids: tuple[str, ...]
    unit_bindings_json: bytes
    controlled_routes_json: bytes


@dataclass(frozen=True)
class CapabilityBinding:
    capability_id: str
    input_contract: str
    output_contract: str
    catalog_entry_hash: str
    surface: CapabilitySurface | None = None


@dataclass(frozen=True)
class InstalledComponent:
    manifest: ComponentManifest


@dataclass(frozen=True)
class CompilerContext:
    scope_id: str
    execution_mode: Literal["live", "replay"]
    compiler_hash: str
    workflow_schema_path: str
    workflow_schema_hash: str
    schemas: tuple[SchemaDefinition, ...]
    operations: tuple[InstalledOperation, ...]
    capabilities: tuple[CapabilityBinding, ...]
    components: tuple[InstalledComponent, ...]
    catalog_json: bytes
    allowed_capabilities: frozenset[str]
    execution_identity_json: bytes | None = None


@dataclass(frozen=True)
class CompiledWorkflow:
    plan_json: str
    plan_hash: str

    def read(self) -> dict[str, Any]:
        value = _decode(self.plan_json)
        if _json(value) != self.plan_json or canonical_hash(value) != self.plan_hash:
            _fail("skill_compiled_plan_hash_mismatch")
        return value


@dataclass(frozen=True)
class BoundNodeInput:
    input_json: str
    input_hash: str
    parent_output_hashes: tuple[tuple[str, str], ...]


def _resources(resources: tuple[ResourceBytes, ...]) -> dict[str, ResourceBytes]:
    if not 5 <= len(resources) <= 128:
        _fail("skill_resource_count_invalid")
    result = {}
    total = 0
    for item in resources:
        path = PurePosixPath(item.path)
        if (
            not item.path
            or len(item.path) > 240
            or path.is_absolute()
            or "\\" in item.path
            or ":" in item.path
            or any(part in {"", ".", ".."} for part in item.path.split("/"))
            or path.suffix not in {".md", ".json"}
            or any(part.endswith((".", " ")) for part in path.parts)
            or any(
                part.split(".")[0].upper()
                in {
                    "CON",
                    "PRN",
                    "AUX",
                    "NUL",
                    *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10)),
                }
                for part in path.parts
            )
        ):
            _fail("skill_resource_path_invalid")
        if item.path.casefold() in {key.casefold() for key in result}:
            _fail("skill_resource_duplicate")
        if (
            type(item.body) is not bytes
            or not _HASH.fullmatch(item.sha256)
            or hashlib.sha256(item.body).hexdigest() != item.sha256
        ):
            _fail("skill_resource_hash_mismatch")
        total += len(item.body)
        if len(item.body) > 1_000_000 or total > 4_000_000:
            _fail("skill_resource_size_exceeded")
        result[item.path] = item
    for name in ("SKILL.md", "workflow.json", "evaluation-protocol.json"):
        if name not in result:
            _fail("skill_resource_missing")
    try:
        if not result["SKILL.md"].body.decode("utf-8").strip():
            _fail("skill_method_empty")
    except UnicodeError as error:
        raise SkillCompileError("skill_method_encoding_invalid") from error
    if not isinstance(_decode(result["evaluation-protocol.json"].body), dict):
        _fail("skill_evaluation_protocol_invalid")
    return result


def _resolve_schema(
    value: Any, root: dict[str, Any], seen: tuple[str, ...] = (), depth: int = 0
) -> Any:
    if depth > 32:
        _fail("skill_schema_depth_exceeded")
    if isinstance(value, list):
        return [_resolve_schema(item, root, seen, depth + 1) for item in value]
    if not isinstance(value, dict):
        return value
    if "$dynamicRef" in value or "$recursiveRef" in value:
        _fail("skill_schema_reference_unsupported")
    if "$ref" in value:
        ref = value["$ref"]
        if not isinstance(ref, str) or not re.fullmatch(r"#/\$defs/[A-Za-z0-9_.-]+", ref):
            _fail("skill_schema_remote_reference_denied")
        if ref in seen:
            _fail("skill_schema_recursive_reference_denied")
        target = root.get("$defs", {}).get(ref.removeprefix("#/$defs/"))
        if not isinstance(target, dict):
            _fail("skill_schema_local_reference_missing")
        siblings = {key: val for key, val in value.items() if key != "$ref"}
        # General $ref intersections are deliberately unsupported.
        if set(siblings) - _ANNOTATIONS:
            _fail("skill_schema_reference_sibling_unsupported")
        return _resolve_schema(target, root, (*seen, ref), depth + 1)
    output = {}
    schema_maps = {"properties", "patternProperties", "dependentSchemas"}
    schema_children = {
        "items",
        "contains",
        "additionalProperties",
        "unevaluatedProperties",
        "unevaluatedItems",
        "propertyNames",
        "not",
        "if",
        "then",
        "else",
    }
    schema_arrays = {"allOf", "anyOf", "oneOf", "prefixItems"}
    for key, val in value.items():
        if key in _ANNOTATIONS:
            continue
        if key in schema_maps and isinstance(val, dict):
            output[key] = {
                name: _resolve_schema(child, root, seen, depth + 1) for name, child in val.items()
            }
        elif key in schema_children or key in schema_arrays:
            output[key] = _resolve_schema(val, root, seen, depth + 1)
        else:
            # Instance literals (const/default/enum) and property names are data.
            output[key] = val
    return output


def _schema(body: bytes) -> dict[str, Any]:
    raw = _decode(body)
    if not isinstance(raw, dict):
        _fail("skill_schema_invalid")
    # Resolve/reject before validator creation; no URL retrieval is possible.
    resolved = _resolve_schema(raw, raw)
    try:
        Draft202012Validator.check_schema(resolved)
    except Exception as error:
        raise SkillCompileError("skill_schema_invalid") from error
    return resolved


def _field(schema: dict[str, Any], pointer: str, *, require_present: bool = True) -> dict[str, Any]:
    if not _POINTER.fullmatch(pointer):
        _fail("skill_port_pointer_invalid")
    current = schema
    for part in pointer.split("/")[1:] if pointer else ():
        if current.get("type") != "object" or part not in current.get("properties", {}):
            _fail("skill_binding_field_unknown")
        if require_present and part not in current.get("required", []):
            _fail("skill_binding_optional_source")
        current = current["properties"][part]
    return current


def _types(schema: dict[str, Any]) -> frozenset[str]:
    kind = schema.get("type")
    if isinstance(kind, str) and kind in _JSON_TYPES:
        return frozenset({kind})
    if isinstance(kind, list) and kind and set(kind) <= _JSON_TYPES:
        return frozenset(kind)
    # Nullable Pydantic primitives are supported; arbitrary union inference isn't.
    if set(schema) == {"anyOf"}:
        return frozenset().union(*(_types(item) for item in schema["anyOf"]))
    _fail("skill_port_shape_unsupported")


def _valid(schema: dict[str, Any], value: Any, code: str) -> None:
    value = _decode(_json(value))  # Strict JSON: bool stays bool, numbers are finite.
    if not Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(value):
        _fail(code)


def _unique[T](items: Iterable[T], key: Callable[[T], str], code: str) -> dict[str, T]:
    result = {}
    for item in items:
        identity = key(item)
        if identity in result:
            _fail(code)
        result[identity] = item
    return result


def _surface_value(binding: CapabilityBinding) -> dict[str, Any]:
    surface = binding.surface
    if surface is None:
        _fail("skill_capability_surface_missing")
    if (
        not surface.protocol_ref
        or not surface.adapter_version
        or not surface.scope_ids
        or len(set(surface.scope_ids)) != len(surface.scope_ids)
        or any(
            not _HASH.fullmatch(value)
            for value in (
                surface.adapter_code_hash,
                surface.input_schema_hash,
                surface.output_schema_hash,
            )
        )
    ):
        _fail("skill_capability_surface_invalid")
    units = _decode(surface.unit_bindings_json)
    routes = _decode(surface.controlled_routes_json)
    if (
        not isinstance(units, dict)
        or not isinstance(routes, list)
        or not 1 <= len(routes) <= 16
        or any(
            not isinstance(key, str) or not isinstance(value, str) or not value
            for key, value in units.items()
        )
    ):
        _fail("skill_capability_surface_invalid")
    return {
        "capability_id": binding.capability_id,
        "protocol_ref": surface.protocol_ref,
        "adapter_version": surface.adapter_version,
        "adapter_code_hash": surface.adapter_code_hash,
        "input_contract": binding.input_contract,
        "output_contract": binding.output_contract,
        "input_schema_hash": surface.input_schema_hash,
        "output_schema_hash": surface.output_schema_hash,
        "scope_ids": sorted(surface.scope_ids),
        "unit_bindings": units,
        "controlled_routes": sorted(routes, key=_json),
    }


def required_capability_surface_hash(bindings: tuple[CapabilityBinding, ...]) -> str:
    """Stable method dependencies only; this function grants no execution permission."""
    unique = _unique(bindings, lambda item: item.capability_id, "skill_capability_duplicate")
    return canonical_hash([_surface_value(unique[key]) for key in sorted(unique)])


def _require_surface_routes(
    binding: CapabilityBinding, entry: dict[str, Any], scope_id: str
) -> None:
    value = _surface_value(binding)
    if scope_id not in value["scope_ids"]:
        _fail("skill_capability_surface_scope_denied")
    # Only the route fields consumed by this method are part of its surface.
    # Added unrelated routes/fields do not change a reusable method package.
    lists = {"fields", "symbols", "modes", "allowed_domains", "sources"}
    scalars = {
        "provider_id",
        "requires_event_window",
        "symbols_required",
        "max_symbols",
        "target_url_required",
        "evidence_ref_required",
    }
    for required in value["controlled_routes"]:
        if (
            not isinstance(required, dict)
            or set(required) - lists - scalars
            or "provider_id" not in required
            or not required.get("modes")
        ):
            _fail("skill_capability_surface_route_invalid")
        matches = []
        for actual in entry.get("routes", []):
            supported = True
            for key, expected in required.items():
                current = actual.get(key)
                if key in lists:
                    if (
                        not isinstance(expected, list)
                        or not isinstance(current, list)
                        or not {_json(item) for item in expected}
                        <= {_json(item) for item in current}
                    ):
                        supported = False
                        break
                elif expected != current:
                    supported = False
                    break
            if supported:
                matches.append(actual)
        if len(matches) != 1:
            _fail("skill_capability_surface_route_not_admitted")


@dataclass(frozen=True)
class ValidatedSkillPackage:
    """Package metadata only. Never accepted by a runtime node binder."""

    metadata_json: str

    def read(self) -> dict:
        return _decode(self.metadata_json)


def compile_skill_package(
    resources: tuple[ResourceBytes, ...], context: CompilerContext
) -> ValidatedSkillPackage:
    value = _compile_skill_workflow(resources, context, for_execution=False).read()
    return ValidatedSkillPackage(
        _json(
            {
                "schema_version": "skill-package-validation.v1",
                "workflow": value["workflow"],
                "workflow_hash": value["workflow_hash"],
                "required_capability_surface_hash": value.get("required_capability_surface_hash"),
            }
        )
    )


def compile_skill_workflow(
    resources: tuple[ResourceBytes, ...], context: CompilerContext
) -> CompiledWorkflow:
    return _compile_skill_workflow(resources, context, for_execution=True)


def _compile_skill_workflow(
    resources: tuple[ResourceBytes, ...],
    context: CompilerContext,
    *,
    for_execution: bool,
) -> CompiledWorkflow:
    files = _resources(resources)
    if not _HASH.fullmatch(context.compiler_hash) or not context.scope_id:
        _fail("skill_compiler_context_invalid")
    if context.execution_mode not in {"live", "replay"}:
        _fail("skill_execution_mode_invalid")
    if context.workflow_schema_path not in files:
        _fail("skill_resource_missing")
    if files[context.workflow_schema_path].sha256 != context.workflow_schema_hash:
        _fail("skill_workflow_schema_hash_mismatch")
    workflow_schema = _schema(files[context.workflow_schema_path].body)
    workflow = _decode(files["workflow.json"].body)
    _valid(workflow_schema, workflow, "skill_workflow_schema_invalid")
    if workflow["scope_id"] != context.scope_id:
        _fail("skill_scope_not_admitted")

    decoded_catalog = _decode(context.catalog_json)
    if not isinstance(decoded_catalog, dict):
        _fail("skill_catalog_invalid")
    catalog = cast(dict[str, Any], decoded_catalog)
    if catalog.get("schema_version") != "research-query-catalog.v1":
        _fail("skill_catalog_invalid")
    claimed = catalog.pop("catalog_hash", None)
    # Existing research_query_catalog uses ensure_ascii=True for its wire hash.
    actual = hashlib.sha256(
        json.dumps(catalog, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    if claimed != actual:
        _fail("skill_catalog_binding_changed")
    new_protocol = workflow["schema_version"] == "skill-workflow.v2"
    if not new_protocol and workflow["capability_catalog_hash"] != actual:
        _fail("skill_catalog_binding_changed")
    if new_protocol and for_execution:
        if (
            context.execution_identity_json is None
            or "scope" not in catalog
            or _json(_decode(context.execution_identity_json)) != _json(catalog["scope"])
        ):
            _fail("skill_execution_catalog_identity_mismatch")
    catalog_entries = _unique(
        catalog.get("capabilities", []),
        lambda item: item["capability_id"],
        "skill_catalog_duplicate_capability",
    )
    if "scope" in catalog and (
        catalog["scope"].get("status") != "bound"
        or catalog["scope"].get("execution_mode") != context.execution_mode
    ):
        _fail("skill_catalog_scope_not_bound")
    operations = _unique(context.operations, lambda item: item.op, "skill_op_duplicate")
    if set(operations) - OPS:
        _fail("skill_op_not_installed")
    cap_bindings = _unique(
        context.capabilities, lambda item: item.capability_id, "skill_capability_duplicate"
    )
    components = _unique(
        context.components, lambda item: item.manifest.component_ref, "skill_component_duplicate"
    )
    definitions = _unique(context.schemas, lambda item: item.reference, "skill_schema_duplicate")
    schemas: dict[str, dict[str, Any]] = {}
    ports: dict[str, dict[str, PortType]] = {}
    schema_hashes: dict[str, str] = {}
    for reference, definition in definitions.items():
        if definition.resource_path not in files:
            _fail("skill_resource_missing")
        resource = files[definition.resource_path]
        if resource.sha256 != definition.schema_hash:
            _fail("skill_schema_resource_mismatch")
        schemas[reference] = _schema(resource.body)
        schema_hashes[reference] = resource.sha256
        ports[reference] = _unique(
            definition.ports, lambda item: item.pointer, "skill_port_duplicate"
        )
        for port in ports[reference].values():
            if (
                not port.json_types
                or len(set(port.json_types)) != len(port.json_types)
                or frozenset(port.json_types)
                != _types(_field(schemas[reference], port.pointer, require_present=False))
            ):
                _fail("skill_port_declaration_mismatch")
            if port.scope_id is not None and port.scope_id != context.scope_id:
                _fail("skill_port_scope_mismatch")
            if port.unit == "":
                _fail("skill_port_unit_invalid")
    for reference in (workflow["input_schema"], workflow["output_schema"]):
        if reference not in schemas:
            _fail("skill_schema_ref_unknown")
    nodes = _unique(workflow["nodes"], lambda item: item["id"], "skill_node_duplicate")
    used_caps = {
        node["capability_id"] for node in nodes.values() if node["op"] == "evidence.acquire"
    }
    if used_caps != set(workflow["required_capabilities"]):
        _fail("skill_capability_declaration_mismatch")
    if not used_caps <= context.allowed_capabilities:
        _fail("skill_capability_not_admitted")
    surface_hash = None
    if new_protocol:
        if not used_caps <= cap_bindings.keys():
            _fail("skill_capability_surface_missing")
        surface_hash = required_capability_surface_hash(
            tuple(cap_bindings[key] for key in sorted(used_caps))
        )
        if workflow["required_capability_surface_hash"] != surface_hash:
            _fail("skill_capability_surface_hash_mismatch")
    node_contracts = {}
    for node_id, node in nodes.items():
        op = operations.get(node["op"])
        if node["op"] not in OPS or op is None:
            _fail("skill_op_not_installed")
        if not _HASH.fullmatch(op.adapter_code_hash):
            _fail("skill_adapter_hash_invalid")
        if not set(node["depends_on"]) <= nodes.keys():
            _fail("skill_dependency_missing")
        pair = (node["input_schema"], node["output_schema"])
        if not set(pair) <= schemas.keys():
            _fail("skill_schema_ref_unknown")
        if pair != (op.input_contract, op.output_contract):
            _fail("skill_op_contract_mismatch")
        contract: dict[str, Any] = {"adapter_code_hash": op.adapter_code_hash}
        if node["op"] == "evidence.acquire":
            cap_id = node["capability_id"]
            cap, entry = cap_bindings.get(cap_id), catalog_entries.get(cap_id)
            if cap is None or entry is None or context.execution_mode not in entry.get("modes", []):
                _fail("skill_capability_not_admitted")
            if cap.catalog_entry_hash != canonical_hash(entry):
                _fail("skill_capability_catalog_changed")
            if pair != (cap.input_contract, cap.output_contract):
                _fail("skill_capability_contract_mismatch")
            if new_protocol:
                _require_surface_routes(cap, entry, context.scope_id)
                assert cap.surface is not None  # Required by surface reader above.
                if (cap.surface.input_schema_hash, cap.surface.output_schema_hash) != (
                    schema_hashes[pair[0]],
                    schema_hashes[pair[1]],
                ):
                    _fail("skill_capability_surface_schema_mismatch")
            contract["capability_entry_hash"] = cap.catalog_entry_hash
        if node["op"] == "component.compute":
            installed = components.get(node["component_ref"])
            if installed is None:
                _fail("skill_component_not_installed")
            manifest = ComponentManifest.model_validate(installed.manifest.model_dump(mode="json"))
            if manifest.scope_id != context.scope_id:
                _fail("skill_component_scope_mismatch")
            if manifest.kind not in {"source_parser", "feature", "model", "monitor_rule"}:
                _fail("skill_component_kind_invalid")
            if pair != (manifest.input_contract, manifest.output_contract):
                _fail("skill_component_contract_mismatch")
            contract["component_manifest"] = manifest.model_dump(mode="json")
            contract["component_manifest_hash"] = manifest.payload_hash
        node_contracts[node_id] = contract

    remaining = {key: set(node["depends_on"]) for key, node in nodes.items()}
    ready = [key for key, parents in remaining.items() if not parents]
    heapq.heapify(ready)
    order = []
    while ready:
        key = heapq.heappop(ready)
        order.append(key)
        for other, parents in remaining.items():
            if key in parents:
                parents.remove(key)
                if not parents:
                    heapq.heappush(ready, other)
    if len(order) != len(nodes):
        _fail("skill_dependency_cycle")
    ancestors = {}

    def bindings(
        items: dict[str, dict[str, Any]], target_ref: str, allowed_nodes: set[str]
    ) -> None:
        target = schemas[target_ref]
        if target.get("type") != "object" or target.get("additionalProperties") is not False:
            _fail("skill_input_contract_not_closed")
        if not set(target.get("required", [])) <= items.keys():
            _fail("skill_input_binding_missing")
        if not items.keys() <= target.get("properties", {}).keys():
            _fail("skill_input_binding_unknown")
        for name, binding in items.items():
            target_pointer = "/" + name
            target_port = ports[target_ref].get(target_pointer)
            if target_port is None:
                _fail("skill_port_declaration_missing")
            target_field = _field(target, target_pointer, require_present=False)
            if binding["from"] == "literal":
                # A bare scalar cannot declare the provenance of a measured quantity.
                if target_port.unit is not None or target_port.scope_id is not None:
                    _fail("skill_literal_semantic_port_denied")
                _valid(target_field, binding["value"], "skill_literal_invalid")
                continue
            if binding["from"] == "workflow_input":
                source_ref = workflow["input_schema"]
            else:
                parent = binding["node_id"]
                if parent not in allowed_nodes:
                    _fail("skill_binding_not_predecessor")
                source_ref = nodes[parent]["output_schema"]
            pointer = binding["pointer"]
            source_port = ports[source_ref].get(pointer)
            if source_port is None:
                _fail("skill_port_declaration_missing")
            source_field = _field(schemas[source_ref], pointer)
            if source_port.unit != target_port.unit:
                _fail("skill_binding_unit_mismatch")
            if source_port.scope_id != target_port.scope_id:
                _fail("skill_binding_scope_mismatch")
            if canonical_hash(source_field) != canonical_hash(target_field):
                _fail("skill_binding_contract_mismatch")

    for key in order:
        ancestors[key] = set(nodes[key]["depends_on"])
        for parent in nodes[key]["depends_on"]:
            ancestors[key].update(ancestors[parent])
        bindings(nodes[key]["inputs"], nodes[key]["input_schema"], ancestors[key])
    bindings(workflow["exports"], workflow["output_schema"], set(nodes))
    dependencies = {
        "schemas": schema_hashes,
        "ports": {
            ref: {
                key: {
                    "json_types": list(port.json_types),
                    "unit": port.unit,
                    "scope_id": port.scope_id,
                }
                for key, port in values.items()
            }
            for ref, values in ports.items()
        },
        "nodes": node_contracts,
    }
    plan = {
        "schema_version": "compiled-skill-workflow.v1",
        "workflow": workflow,
        "scope_id": context.scope_id,
        "execution_mode": context.execution_mode,
        "compiler_hash": context.compiler_hash,
        "catalog_hash": actual,
        **(
            {
                "required_capability_surface_hash": surface_hash,
                "execution_identity": catalog.get("scope"),
            }
            if new_protocol
            else {}
        ),
        "resource_manifest": [
            {"path": key, "sha256": item.sha256, "size": len(item.body)}
            for key, item in sorted(files.items())
        ],
        "workflow_hash": files["workflow.json"].sha256,
        "workflow_schema_hash": files[context.workflow_schema_path].sha256,
        "dependency_manifest": dependencies,
        "dependency_hash": canonical_hash(dependencies),
        "schemas": schemas,
        "topological_order": order,
    }
    return CompiledWorkflow(_json(plan), canonical_hash(plan))


def _value_pointer(value: Any, pointer: str) -> Any:
    for part in pointer.split("/")[1:] if pointer else ():
        if not isinstance(value, dict) or part not in value:
            _fail("skill_step_input_path_missing")
        value = value[part]
    return value


def validate_node_output(compiled: CompiledWorkflow, node_id: str, value: Any) -> str:
    """Return canonical output JSON, not an executed or consumed receipt."""
    if not isinstance(compiled, CompiledWorkflow):
        _fail("skill_execution_compile_required")
    plan = compiled.read()
    node = next((item for item in plan["workflow"]["nodes"] if item["id"] == node_id), None)
    if node is None:
        _fail("skill_node_unknown")
    _valid(plan["schemas"][node["output_schema"]], value, "skill_step_output_invalid")
    return _json(value)


def bind_node_inputs(
    compiled: CompiledWorkflow,
    node_id: str,
    workflow_input: Any,
    completed_outputs: dict[str, Any],
) -> BoundNodeInput:
    """Bind actual JSON supplied by a future original reader; never execute it."""
    if not isinstance(compiled, CompiledWorkflow):
        _fail("skill_execution_compile_required")
    plan = compiled.read()
    workflow, schemas = plan["workflow"], plan["schemas"]
    nodes = {item["id"]: item for item in workflow["nodes"]}
    if node_id not in nodes:
        _fail("skill_node_unknown")
    node = nodes[node_id]
    _valid(schemas[workflow["input_schema"]], workflow_input, "skill_workflow_input_invalid")
    if not set(node["depends_on"]) <= completed_outputs.keys():
        _fail("skill_step_dependency_not_ready")
    used = set(node["depends_on"])
    used.update(
        binding["node_id"]
        for binding in node["inputs"].values()
        if binding["from"] == "node_output"
    )
    parents = []
    for parent in sorted(used):
        if parent not in completed_outputs:
            _fail("skill_step_dependency_not_ready")
        body = validate_node_output(compiled, parent, completed_outputs[parent])
        parents.append((parent, hashlib.sha256(body.encode()).hexdigest()))
    payload = {}
    for name, binding in node["inputs"].items():
        if binding["from"] == "literal":
            payload[name] = binding["value"]
        else:
            source = (
                workflow_input
                if binding["from"] == "workflow_input"
                else completed_outputs[binding["node_id"]]
            )
            payload[name] = _value_pointer(source, binding["pointer"])
    _valid(schemas[node["input_schema"]], payload, "skill_step_input_invalid")
    body = _json(payload)
    return BoundNodeInput(body, hashlib.sha256(body.encode()).hexdigest(), tuple(parents))
