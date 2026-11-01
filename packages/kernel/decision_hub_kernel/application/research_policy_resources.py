"""Bounded, exact-byte resources for prepared policy compilation only."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Hashable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml
from yaml.events import AliasEvent

from packages.contracts_py.decision_hub_contracts.generated.agentic_research import RoleProfile
from packages.contracts_py.decision_hub_contracts.generated.research_policy import (
    ResearchPolicyResource,
    ResearchRoleRubricBinding,
)

MAX_RESOURCE_BYTES = 65_536
MAX_TOTAL_BYTES = 262_144
MAX_RESOURCES = 32
MAX_YAML_NODES = 8_192
MAX_YAML_DEPTH = 32


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _json_domain(value: Any) -> None:
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("research_policy_non_string_key")
        for item in value.values():
            _json_domain(item)
    elif isinstance(value, list):
        for item in value:
            _json_domain(item)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("research_policy_non_finite_number")
    elif value is not None and not isinstance(value, str | int | float | bool):
        raise ValueError("research_policy_non_json_value")


class _PolicyYamlLoader(yaml.SafeLoader):
    # Keep dates as strings for strict contract validation. Do not mutate the
    # global SafeLoader resolver table used by existing v1 policies.
    yaml_implicit_resolvers = {
        key: [(tag, regex) for tag, regex in resolvers if tag != "tag:yaml.org,2002:timestamp"]
        for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._policy_nodes = 0
        self._policy_depth = 0

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(AliasEvent):
            raise ValueError("research_policy_yaml_alias_not_allowed")
        self._policy_nodes += 1
        self._policy_depth += 1
        try:
            if self._policy_nodes > MAX_YAML_NODES:
                raise ValueError("research_policy_yaml_nodes_exceeded")
            if self._policy_depth > MAX_YAML_DEPTH:
                raise ValueError("research_policy_yaml_depth_exceeded")
            return super().compose_node(parent, index)
        finally:
            self._policy_depth -= 1

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[Hashable, Any]:
        result: dict[Hashable, Any] = {}
        for key_node, value_node in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                raise ValueError("research_policy_yaml_merge_not_allowed")
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise ValueError("research_policy_non_string_key")
            if key in result:
                raise ValueError("research_policy_duplicate_yaml_key")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def parse_resource_bytes(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > MAX_RESOURCE_BYTES:
        raise ValueError("research_policy_resource_size_invalid")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("research_policy_invalid_utf8") from exc
    loader = _PolicyYamlLoader(text)
    try:
        value = loader.get_single_data()
    except yaml.YAMLError as exc:
        raise ValueError("research_policy_yaml_invalid") from exc
    finally:
        loader.dispose()
    if not isinstance(value, dict):
        raise ValueError("research_policy_resource_object_required")
    _json_domain(value)
    return value


def normalized_relative_ref(reference: str, *, parent_reference: str | None = None) -> str:
    if not reference or "\\" in reference or "\x00" in reference:
        raise ValueError("research_policy_reference_invalid")
    path = PurePosixPath(reference)
    if path.is_absolute() or PureWindowsPath(reference).drive:
        raise ValueError("research_policy_reference_outside_root")
    parts: list[str] = []
    parent = PurePosixPath(parent_reference).parent if parent_reference is not None else None
    for part in (*(() if parent is None else parent.parts), *path.parts):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise ValueError("research_policy_reference_outside_root")
            parts.pop()
        else:
            parts.append(part)
    if not parts:
        raise ValueError("research_policy_reference_invalid")
    return PurePosixPath(*parts).as_posix()


def freeze_resource_bytes(
    relative_ref: str,
    raw: bytes,
    *,
    expected_schema_version: str,
) -> ResearchPolicyResource:
    normalized = normalized_relative_ref(relative_ref)
    definition = parse_resource_bytes(raw)
    if definition.get("schema_version") != expected_schema_version:
        raise ValueError("research_policy_resource_version_mismatch")
    return ResearchPolicyResource.model_validate(
        {
            "relative_ref": normalized,
            "source_schema_version": expected_schema_version,
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "raw_utf8": raw.decode("utf-8"),
            "canonical_definition_hash": canonical_hash(definition),
        }
    )


def resource_definition(resource: ResearchPolicyResource) -> dict[str, Any]:
    # A model_construct instance or mutable field is not trusted input.
    parsed = ResearchPolicyResource.model_validate(resource.model_dump(mode="json"))
    raw = parsed.raw_utf8.encode("utf-8")
    definition = parse_resource_bytes(raw)
    if (
        hashlib.sha256(raw).hexdigest() != parsed.raw_sha256
        or canonical_hash(definition) != parsed.canonical_definition_hash
        or definition.get("schema_version") != parsed.source_schema_version
        or normalized_relative_ref(parsed.relative_ref) != parsed.relative_ref
    ):
        raise ValueError("research_policy_resource_integrity_mismatch")
    return definition


class PolicyResourceLoader:
    """Read each resolved resource once, with per-file and aggregate limits."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("research_policy_root_unavailable")
        self._resources: dict[str, ResearchPolicyResource] = {}
        self._total_bytes = 0

    def load(
        self,
        reference: str,
        *,
        expected_schema_version: str,
        parent_reference: str | None = None,
    ) -> ResearchPolicyResource:
        relative = normalized_relative_ref(reference, parent_reference=parent_reference)
        path = (self.root / relative).resolve(strict=True)
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ValueError("research_policy_reference_outside_root")
        relative = path.relative_to(self.root).as_posix()
        cached = self._resources.get(relative)
        if cached is not None:
            if cached.source_schema_version != expected_schema_version:
                raise ValueError("research_policy_resource_version_mismatch")
            return cached.model_copy(deep=True)
        if len(self._resources) >= MAX_RESOURCES:
            raise ValueError("research_policy_resource_count_exceeded")
        # stat is not a size gate: check the bytes actually read, including a
        # file replaced/grown after path resolution. Never read the rest.
        with path.open("rb") as stream:
            raw = stream.read(MAX_RESOURCE_BYTES + 1)
        if (self.root / relative).resolve(strict=True) != path:
            raise ValueError("research_policy_resource_path_changed")
        frozen = freeze_resource_bytes(
            relative, raw, expected_schema_version=expected_schema_version
        )
        if self._total_bytes + len(raw) > MAX_TOTAL_BYTES:
            raise ValueError("research_policy_resource_total_exceeded")
        self._total_bytes += len(raw)
        self._resources[relative] = frozen
        return frozen.model_copy(deep=True)

    def load_role_binding(
        self,
        profile_ref: str,
        *,
        rubric: ResearchPolicyResource,
    ) -> ResearchRoleRubricBinding:
        profile_resource = self.load(profile_ref, expected_schema_version="role-profile.v1")
        body = resource_definition(profile_resource)
        profile = RoleProfile.model_validate(body)
        loaded_rubric = self.load(
            profile.evaluation_policy_ref,
            parent_reference=profile_resource.relative_ref,
            expected_schema_version="research-rubric-policy.v2",
        )
        if loaded_rubric != rubric:
            raise ValueError("research_policy_role_rubric_mismatch")
        return ResearchRoleRubricBinding.model_validate(
            {
                "profile_id": profile.profile_id,
                "profile_ref": profile_resource.relative_ref,
                "profile_resource": profile_resource,
                "evaluation_policy_ref": profile.evaluation_policy_ref,
                "rubric_relative_ref": rubric.relative_ref,
                "rubric_raw_sha256": rubric.raw_sha256,
                "rubric_canonical_definition_hash": rubric.canonical_definition_hash,
            }
        )
