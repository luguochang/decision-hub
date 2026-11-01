"""Compile a frozen declaration into prepared metadata; never execute rules."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from packages.contracts_py.decision_hub_contracts.generated.agentic_research import RoleProfile
from packages.contracts_py.decision_hub_contracts.generated.research_policy import (
    ResearchCompiledPolicy,
    ResearchGatePolicy,
    ResearchPolicyResource,
    ResearchRoleRubricBinding,
    ResearchRubricPolicy,
    ResearchTaskScope,
    ResearchTaskScopeV2,
)
from packages.kernel.decision_hub_kernel.application.research_policy_resources import (
    MAX_RESOURCES,
    MAX_TOTAL_BYTES,
    canonical_hash,
    normalized_relative_ref,
    resource_definition,
)
from packages.kernel.decision_hub_kernel.decision import research_policy_registry as registry

COMPILER_VERSION = "research-policy-compiler.v1"
ROOT = Path(__file__).resolve().parents[4]
CODE_FILES = {
    "compiler_source_sha256": Path(__file__),
    "resources_source_sha256": Path(__file__).with_name("research_policy_resources.py"),
    "strict_base_source_sha256": (
        ROOT / "packages/contracts_py/decision_hub_contracts/policy_contract_base.py"
    ),
    "registry_source_sha256": Path(registry.__file__),
    "canonical_schema_sha256": ROOT / "contracts/schemas/research_policy.schema.yaml",
    "generated_contract_sha256": (
        ROOT / "packages/contracts_py/decision_hub_contracts/generated/research_policy.py"
    ),
    "role_contract_source_sha256": (
        ROOT / "packages/contracts_py/decision_hub_contracts/generated/agentic_research.py"
    ),
    "role_canonical_schema_sha256": ROOT / "contracts/schemas/agentic_research.schema.yaml",
}


def code_identity() -> dict[str, str]:
    """Actual implementation bytes, not caller-provided hashes or an executor proof."""
    return {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in CODE_FILES.items()}


def scope_hash(scope_body: dict[str, Any]) -> str:
    """Hash canonical typed scope excluding its self digest; UTC uses DTO JSON form."""
    body = {key: value for key, value in scope_body.items() if key != "scope_hash"}
    # Validate before normalizing datetime strings. This digest is an identity,
    # not proof that a Factory read a particular original Task or Watch.
    model = ResearchTaskScopeV2 if body.get("schema_version") == "research-task-scope.v2" else ResearchTaskScope
    temporary = model.model_validate({**body, "scope_hash": canonical_hash(body)})
    canonical = temporary.model_dump(mode="json")
    canonical.pop("scope_hash")
    return canonical_hash(canonical)


def _validate_scope(scope: ResearchTaskScope) -> ResearchTaskScope:
    model = ResearchTaskScopeV2 if scope.schema_version == "research-task-scope.v2" else ResearchTaskScope
    parsed = model.model_validate(scope.model_dump(mode="json"))
    if parsed.scope_hash != scope_hash(parsed.model_dump(mode="json")):
        raise ValueError("research_policy_scope_hash_mismatch")
    if parsed.pit_cutoff_at > parsed.deadline_at:
        raise ValueError("research_policy_scope_time_invalid")
    if (parsed.research_mode == "historical_review") != (parsed.scope_kind == "historical_review"):
        raise ValueError("research_policy_scope_mode_mismatch")
    if parsed.event_family_basis == "watch":
        if parsed.watch_id is None:
            raise ValueError("research_policy_original_watch_required")
    elif parsed.watch_id is not None:
        raise ValueError("research_policy_scope_watch_mismatch")
    if (
        parsed.event_family_basis == "unknown_conservative_default"
        and parsed.event_family != "unknown"
    ):
        raise ValueError("research_policy_unknown_family_cannot_be_promoted")
    return parsed


def _validate_roles(
    roles: list[ResearchRoleRubricBinding],
    rubric: ResearchPolicyResource,
) -> list[ResearchRoleRubricBinding]:
    if len(roles) > 32:
        raise ValueError("research_policy_roles_exceeded")
    parsed_roles: list[ResearchRoleRubricBinding] = []
    ids: set[str] = set()
    for role in roles:
        parsed = ResearchRoleRubricBinding.model_validate(role.model_dump(mode="json"))
        profile = RoleProfile.model_validate(resource_definition(parsed.profile_resource))
        resolved = normalized_relative_ref(
            profile.evaluation_policy_ref,
            parent_reference=parsed.profile_resource.relative_ref,
        )
        if (
            parsed.profile_id != profile.profile_id
            or parsed.profile_id in ids
            or parsed.profile_ref != parsed.profile_resource.relative_ref
            or parsed.evaluation_policy_ref != profile.evaluation_policy_ref
            or resolved != rubric.relative_ref
            or parsed.rubric_relative_ref != resolved
            or parsed.rubric_raw_sha256 != rubric.raw_sha256
            or parsed.rubric_canonical_definition_hash != rubric.canonical_definition_hash
        ):
            raise ValueError("research_policy_role_rubric_mismatch")
        ids.add(parsed.profile_id)
        parsed_roles.append(parsed)
    return parsed_roles


def _validate_declarations(gate: ResearchGatePolicy, rubric: ResearchRubricPolicy) -> None:
    gate_ids = [rule.rule_id for rule in gate.rules]
    rubric_ids = [rule.rule_id for rule in rubric.checks]
    ids = gate_ids + rubric_ids
    if len(ids) != len(set(ids)):
        raise ValueError("research_policy_duplicate_rule_id")
    if set(gate_ids) != set(registry.GATE_RULE_IDS) or set(rubric_ids) != set(
        registry.RUBRIC_RULE_IDS
    ):
        raise ValueError("research_policy_mandatory_rule_set_missing")
    for rule in [*gate.rules, *rubric.checks]:
        # A supports a single complete, closed registry definition. An input
        # phase is preserved, not automatically relabeled as already executed.
        if set(rule.applies_to) != set(registry.SCOPE_KINDS):
            raise ValueError("research_policy_mandatory_scope_excluded")
        if rule.rule_id in registry.NONBLOCKING_RULE_IDS:
            expected = set()
        elif rule.rule_id == "execution-permission":
            expected = {"execution_permission"}
        else:
            expected = {"research_completeness", "research_publication"}
        if set(rule.blocking_for) != expected:
            raise ValueError("research_policy_blocking_obligation_changed")
        if (
            rule.rule_id == "kernel-cap"
            and rule.params.maximum_subjective_probability != gate.kernel_safeguards.probability_cap
        ):
            raise ValueError("research_policy_probability_cap_inconsistent")


def compile_policy(
    gate_resource: ResearchPolicyResource,
    rubric_resource: ResearchPolicyResource,
    role_bindings: list[ResearchRoleRubricBinding],
    *,
    scope: ResearchTaskScope,
) -> ResearchCompiledPolicy:
    """No DB, model, network, clock, permissions or rule outcome evaluation."""
    gate_resource = ResearchPolicyResource.model_validate(gate_resource.model_dump(mode="json"))
    rubric_resource = ResearchPolicyResource.model_validate(rubric_resource.model_dump(mode="json"))
    gate = ResearchGatePolicy.model_validate(resource_definition(gate_resource))
    rubric = ResearchRubricPolicy.model_validate(resource_definition(rubric_resource))
    parsed_scope = _validate_scope(scope)
    roles = _validate_roles(role_bindings, rubric_resource)
    resources: dict[str, ResearchPolicyResource] = {}
    for resource in [gate_resource, rubric_resource, *(role.profile_resource for role in roles)]:
        existing = resources.get(resource.relative_ref)
        if existing is not None and existing != resource:
            raise ValueError("research_policy_resource_identity_conflict")
        resources[resource.relative_ref] = resource
    if len(resources) > MAX_RESOURCES:
        raise ValueError("research_policy_resource_count_exceeded")
    if (
        sum(len(resource.raw_utf8.encode("utf-8")) for resource in resources.values())
        > MAX_TOTAL_BYTES
    ):
        raise ValueError("research_policy_resource_total_exceeded")
    _validate_declarations(gate, rubric)
    declarations = [
        *((rule, "gate") for rule in gate.rules),
        *((rule, "rubric") for rule in rubric.checks),
    ]
    compiled_rules = []
    for rule, source in sorted(declarations, key=lambda pair: pair[0].rule_id):
        applicability = (
            "unknown"
            if parsed_scope.event_family_basis == "unknown_conservative_default"
            else "applicable"
            if parsed_scope.scope_kind in rule.applies_to
            else "not_applicable"
        )
        compiled_rules.append(
            {
                "definition": rule.model_dump(mode="json"),
                "declaration_source": source,
                "applicability": applicability,
                "handler_availability": "not_available",
                "status": "not_available",
                "reason_code": "prepared_not_executed",
            }
        )
    from .research_policy_checker import citation_execution_identity

    identity = code_identity()
    checker = citation_execution_identity()
    body = {
        "schema_version": "research-compiled-policy.v1",
        "status": "prepared",
        "allowed_execution": False,
        "checker_activation": "prepared",
        "gate_resource": gate_resource.model_dump(mode="json"),
        "rubric_resource": rubric_resource.model_dump(mode="json"),
        "role_rubric_bindings": [role.model_dump(mode="json") for role in roles],
        "compiled_rules": compiled_rules,
        "compiler_version": COMPILER_VERSION,
        "compiler_code_hash": canonical_hash(identity),
        "checker_version": checker["version"],
        "checker_code_hash": checker["code_hash"],
        "code_identity": identity,
        "scope_hash": parsed_scope.scope_hash,
        "requirements_hash": parsed_scope.requirements_hash,
        "registered_rule_ids": list(registry.REGISTERED_RULE_IDS),
        "supported_rule_ids": [],
        "unavailable_rule_ids": list(registry.REGISTERED_RULE_IDS),
    }
    return ResearchCompiledPolicy.model_validate({**body, "compiled_hash": canonical_hash(body)})
