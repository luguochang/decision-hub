"""Pure prepared-policy boundaries using real files and canonical contracts."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from packages.contracts_py.decision_hub_contracts import (
    ResearchPolicyResource,
    ResearchRoleRubricBinding,
    ResearchTaskScope,
)
from packages.kernel.decision_hub_kernel.application.research_policy_compiler import (
    CODE_FILES,
    code_identity,
    compile_policy,
    scope_hash,
)
from packages.kernel.decision_hub_kernel.application.research_policy_resources import (
    MAX_RESOURCE_BYTES,
    PolicyResourceLoader,
    canonical_hash,
    freeze_resource_bytes,
    normalized_relative_ref,
    parse_resource_bytes,
    resource_definition,
)
from packages.kernel.decision_hub_kernel.decision.research_policy_registry import (
    GATE_RULE_IDS,
    NONBLOCKING_RULE_IDS,
    REGISTERED_RULE_IDS,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "contracts/schemas/research_policy.schema.yaml").read_text("utf-8"))


def policy_bodies() -> tuple[dict[str, Any], dict[str, Any]]:
    rules = []
    for branch in SCHEMA["$defs"]["research_policy_rule"]["oneOf"]:
        spec = SCHEMA["$defs"][branch["$ref"].rsplit("/", 1)[1]]["properties"]
        rule_id = spec["rule_id"]["const"]
        params = {
            key: definition.get("const", 0.65)
            for key, definition in spec["params"]["properties"].items()
        }
        rules.append(
            {
                "rule_id": rule_id,
                "rule_version": "1.0.0",
                "category": spec["category"]["const"],
                "phase": "commit",
                "applies_to": ["event", "current_snapshot", "historical_review"],
                "blocking_for": (
                    []
                    if rule_id in NONBLOCKING_RULE_IDS
                    else ["execution_permission"]
                    if rule_id == "execution-permission"
                    else ["research_completeness", "research_publication"]
                ),
                "params": params,
            }
        )
    safeguards = {
        key: definition.get("const", 0.65)
        for key, definition in SCHEMA["$defs"]["research_kernel_safeguards"]["properties"].items()
    }
    gate = {
        "schema_version": "research-gate-policy.v2",
        "policy_id": "test-gate",
        "policy_version": "2.0.0",
        "horizon_contract_version": "research-horizon-contract.v1",
        "kernel_safeguards": safeguards,
        "rules": [rule for rule in rules if rule["rule_id"] in GATE_RULE_IDS],
    }
    rubric = {
        "schema_version": "research-rubric-policy.v2",
        "rubric_id": "test-rubric",
        "rubric_version": "2.0.0",
        "checks": [rule for rule in rules if rule["rule_id"] not in GATE_RULE_IDS],
        "prospective_metrics": ["brier_score"],
        "operational_metrics": ["estimated_cost_usd"],
        "promotion_policy": "owner_review_required_replay_not_profitability",
    }
    return gate, rubric


def frozen(body: dict[str, Any], ref: str = "gate.json") -> ResearchPolicyResource:
    return freeze_resource_bytes(
        ref, json.dumps(body).encode(), expected_schema_version=body["schema_version"]
    )


def scope(**changes: Any) -> ResearchTaskScope:
    digest = hashlib.sha256(b"controlled original full Task; no execution").hexdigest()
    body = {
        "schema_version": "research-task-scope.v1",
        "run_id": "run-test",
        "event_id": "evt-test",
        "study_id": None,
        "trigger_snapshot_id": "snap-test",
        "trigger_snapshot_hash": digest,
        "market": "crypto",
        "instrument": "BTC",
        "horizon_scope": ["30m", "24h", "72h"],
        "domain_pack_ref": "crypto_macro.v1",
        "execution_mode": "replay",
        "research_mode": "current_decision",
        "scope_kind": "event",
        "event_family": "monetary_policy",
        "event_family_basis": "event",
        "watch_id": None,
        "full_task_hash": digest,
        "requirements_hash": digest,
        "allowed_capabilities_hash": digest,
        "pit_cutoff_at": "2026-10-01T12:00:00Z",
        "deadline_at": "2026-10-01T12:30:00Z",
    }
    body.update(changes)
    return ResearchTaskScope.model_validate({**body, "scope_hash": scope_hash(body)})


def test_prepared_compilation_never_makes_outcome_or_execution_claim() -> None:
    gate, rubric = policy_bodies()
    result = compile_policy(frozen(gate), frozen(rubric, "rubric.json"), [], scope=scope())
    assert result.status == result.checker_activation == "prepared"
    assert result.allowed_execution is False and result.supported_rule_ids == []
    assert set(result.unavailable_rule_ids) == set(REGISTERED_RULE_IDS)
    assert all(
        item.status == "not_available" and item.handler_availability == "not_available"
        for item in result.compiled_rules
    )
    body = result.model_dump(mode="json")
    assert body.pop("compiled_hash") == canonical_hash(body)
    assert result.compiler_code_hash == canonical_hash(code_identity())
    assert (
        result.checker_code_hash
        == hashlib.sha256(CODE_FILES["registry_source_sha256"].read_bytes()).hexdigest()
    )
    assert all(
        getattr(result.code_identity, key) == hashlib.sha256(path.read_bytes()).hexdigest()
        for key, path in CODE_FILES.items()
    )


@pytest.mark.parametrize(
    "mutation", ["unknown", "version", "params", "cap", "false-guard", "extra"]
)
def test_closed_contract_rejects_unknown_and_weakened_definitions(mutation: str) -> None:
    gate, rubric = policy_bodies()
    if mutation == "unknown":
        gate["rules"][0]["rule_id"] = "future-executable"
    elif mutation == "version":
        gate["rules"][0]["rule_version"] = "9.0"
    elif mutation == "params":
        gate["rules"][0]["params"] = {"expression": "pass"}
    elif mutation == "cap":
        gate["kernel_safeguards"]["probability_cap"] = 0.7
    elif mutation == "false-guard":
        gate["kernel_safeguards"]["pit_required"] = False
    else:
        gate["arbitrary"] = True
    with pytest.raises(ValidationError):
        compile_policy(frozen(gate), frozen(rubric, "rubric.json"), [], scope=scope())


@pytest.mark.parametrize(
    "mutation",
    ["duplicate", "duplicate-params", "cross-duplicate", "missing", "blocking", "applies"],
)
def test_compiler_rejects_duplicate_id_and_removed_obligations(mutation: str) -> None:
    gate, rubric = policy_bodies()
    if mutation == "duplicate":
        other = copy.deepcopy(gate["rules"][0])
        other["phase"] = "finalization"
        gate["rules"].append(other)
    elif mutation == "duplicate-params":
        other = copy.deepcopy(
            next(rule for rule in rubric["checks"] if rule["rule_id"] == "kernel-cap")
        )
        other["params"]["maximum_subjective_probability"] = 0.6
        rubric["checks"].append(other)
    elif mutation == "cross-duplicate":
        rubric["checks"].append(copy.deepcopy(gate["rules"][0]))
    elif mutation == "missing":
        gate["rules"].pop()
    elif mutation == "blocking":
        gate["rules"][0]["blocking_for"] = []
    else:
        gate["rules"][0]["applies_to"] = ["event"]
    with pytest.raises(
        ValueError, match="duplicate_rule_id|mandatory_rule|blocking_obligation|scope_excluded"
    ):
        compile_policy(frozen(gate), frozen(rubric, "rubric.json"), [], scope=scope())


@pytest.mark.parametrize(
    "raw,code",
    [
        (b"x: 1\nx: 2", "duplicate_yaml_key"),
        (b"1: test", "non_string_key"),
        (b"x: &x [1]\ny: *x", "alias_not_allowed"),
        (b"x: .nan", "non_finite_number"),
        (b"x: .inf", "non_finite_number"),
        (b"\xff", "invalid_utf8"),
        (b"---\nx: 1\n---\ny: 2", "yaml_invalid"),
        (b"[]", "object_required"),
        (b"x: " + b"[" * 40 + b"0" + b"]" * 40, "depth_exceeded"),
        (b"x: [" + b"0," * 8200 + b"0]", "nodes_exceeded"),
    ],
)
def test_resource_rejects_unsafe_yaml_before_unbounded_construction(raw: bytes, code: str) -> None:
    with pytest.raises(ValueError, match=code):
        parse_resource_bytes(raw)


def test_actual_bytes_limit_and_raw_bom_crlf_identity(tmp_path: Path) -> None:
    base = b"schema_version: role-profile.v1\nx: 1\n"
    raw = base + b" " * (MAX_RESOURCE_BYTES - len(base))
    path = tmp_path / "exact.yaml"
    path.write_bytes(raw)
    loader = PolicyResourceLoader(tmp_path)
    exact = loader.load("exact.yaml", expected_schema_version="role-profile.v1")
    assert len(exact.raw_utf8.encode()) == MAX_RESOURCE_BYTES
    (tmp_path / "large.yaml").write_bytes(raw + b" ")
    with pytest.raises(ValueError, match="size_invalid"):
        loader.load("large.yaml", expected_schema_version="role-profile.v1")
    crlf = freeze_resource_bytes(
        "crlf.yaml",
        b"\xef\xbb\xbf" + base.replace(b"\n", b"\r\n"),
        expected_schema_version="role-profile.v1",
    )
    plain = freeze_resource_bytes("plain.yaml", base, expected_schema_version="role-profile.v1")
    assert crlf.canonical_definition_hash == plain.canonical_definition_hash
    assert crlf.raw_sha256 != plain.raw_sha256
    assert crlf.raw_sha256 == hashlib.sha256(crlf.raw_utf8.encode()).hexdigest()


@pytest.mark.parametrize(
    "reference", ["../outside.yaml", "/absolute.yaml", "C:/file.yaml", "a\\b", "\\\\host\\file"]
)
def test_reference_cannot_escape_root(reference: str) -> None:
    with pytest.raises(ValueError):
        normalized_relative_ref(reference)


def test_parent_relative_inside_is_legal_and_role_binds_exact_rubric(tmp_path: Path) -> None:
    gate, rubric = policy_bodies()
    (tmp_path / "profiles").mkdir()
    (tmp_path / "evaluations").mkdir()
    (tmp_path / "evaluations/rubric.json").write_text(json.dumps(rubric), "utf-8")
    profile = yaml.safe_load((ROOT / "packs/crypto_macro/profiles/manager.yaml").read_text("utf-8"))
    profile["evaluation_policy_ref"] = "../evaluations/rubric.json"
    (tmp_path / "profiles/manager.yaml").write_text(yaml.safe_dump(profile), "utf-8")
    loader = PolicyResourceLoader(tmp_path)
    resource = loader.load(
        "evaluations/rubric.json", expected_schema_version="research-rubric-policy.v2"
    )
    role = loader.load_role_binding("profiles/manager.yaml", rubric=resource)
    compiled = compile_policy(frozen(gate), resource, [role], scope=scope())
    assert compiled.role_rubric_bindings[0].rubric_relative_ref == "evaluations/rubric.json"
    changed = role.model_copy(update={"rubric_raw_sha256": hashlib.sha256(b"wrong").hexdigest()})
    with pytest.raises(ValueError, match="role_rubric_mismatch"):
        compile_policy(frozen(gate), resource, [changed], scope=scope())


@pytest.mark.parametrize("field", ["raw_utf8", "raw_sha256", "canonical_definition_hash"])
def test_mutable_or_constructed_resource_is_revalidated(field: str) -> None:
    gate, _ = policy_bodies()
    resource = frozen(gate)
    body = resource.model_dump()
    body[field] = "changed" if field == "raw_utf8" else hashlib.sha256(b"changed").hexdigest()
    corrupt = ResearchPolicyResource.model_construct(**body)
    with pytest.raises(ValueError):
        resource_definition(corrupt)


def test_scope_tamper_and_unknown_family_do_not_gain_proof() -> None:
    gate, rubric = policy_bodies()
    valid = scope()
    corrupt = valid.model_copy(update={"requirements_hash": hashlib.sha256(b"new").hexdigest()})
    with pytest.raises(ValueError, match="scope_hash_mismatch"):
        compile_policy(frozen(gate), frozen(rubric, "rubric.json"), [], scope=corrupt)
    unknown = scope(event_family="unknown", event_family_basis="unknown_conservative_default")
    result = compile_policy(frozen(gate), frozen(rubric, "rubric.json"), [], scope=unknown)
    assert all(
        rule.applicability == "unknown" and rule.status == "not_available"
        for rule in result.compiled_rules
    )


def test_v1_definition_is_not_silently_upgraded() -> None:
    raw = (ROOT / "packs/crypto_macro/gates/horizons.yaml").read_bytes()
    with pytest.raises(ValueError, match="version_mismatch"):
        freeze_resource_bytes(
            "gates/horizons.yaml", raw, expected_schema_version="research-gate-policy.v2"
        )


@pytest.mark.parametrize(
    "count,padding,code", [(31, 0, "count_exceeded"), (6, 50_000, "total_exceeded")]
)
def test_direct_compile_input_must_obey_aggregate_resource_limits(
    count: int, padding: int, code: str
) -> None:
    gate, rubric = policy_bodies()
    rubric_resource = frozen(rubric, "evaluations/rubric.json")
    original = yaml.safe_load(
        (ROOT / "packs/crypto_macro/profiles/manager.yaml").read_text("utf-8")
    )
    roles = []
    for index in range(count):
        body = {
            **original,
            "profile_id": f"test.role{index}",
            "evaluation_policy_ref": "../evaluations/rubric.json",
        }
        resource = freeze_resource_bytes(
            f"profiles/role{index}.json",
            json.dumps(body).encode() + b" " * padding,
            expected_schema_version="role-profile.v1",
        )
        roles.append(
            ResearchRoleRubricBinding.model_validate(
                {
                    "profile_id": body["profile_id"],
                    "profile_ref": resource.relative_ref,
                    "profile_resource": resource,
                    "evaluation_policy_ref": body["evaluation_policy_ref"],
                    "rubric_relative_ref": rubric_resource.relative_ref,
                    "rubric_raw_sha256": rubric_resource.raw_sha256,
                    "rubric_canonical_definition_hash": rubric_resource.canonical_definition_hash,
                }
            )
        )
    with pytest.raises(ValueError, match=code):
        compile_policy(frozen(gate), rubric_resource, roles, scope=scope())


def test_same_relative_resource_cannot_mean_two_different_definitions() -> None:
    gate, rubric = policy_bodies()
    with pytest.raises(ValueError, match="resource_identity_conflict"):
        compile_policy(
            frozen(gate, "policy.json"), frozen(rubric, "policy.json"), [], scope=scope()
        )


def test_real_directory_link_outside_root_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "resource.json").write_text('{"schema_version":"role-profile.v1"}', "utf-8")
    link = root / "linked"
    if os.name == "nt":
        # Both junction ends are isolated test directories.
        command = (
            "New-Item -ItemType Junction -Path '"
            + str(link).replace("'", "''")
            + "' -Target '"
            + str(outside).replace("'", "''")
            + "' | Out-Null"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", command], check=True, capture_output=True
        )
    else:
        link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="outside_root"):
        PolicyResourceLoader(root).load(
            "linked/resource.json", expected_schema_version="role-profile.v1"
        )


def test_model_construct_cannot_disable_mandatory_guard() -> None:
    gate, rubric = policy_bodies()
    resource = frozen(gate)
    gate["kernel_safeguards"]["pit_required"] = 1
    raw = json.dumps(gate)
    corrupt = resource.model_copy(
        update={
            "raw_utf8": raw,
            "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "canonical_definition_hash": canonical_hash(gate),
        }
    )
    with pytest.raises(ValidationError):
        compile_policy(corrupt, frozen(rubric, "rubric.json"), [], scope=scope())


def test_stricter_declared_cap_is_prepared_without_changing_kernel_or_granting_trade() -> None:
    gate, rubric = policy_bodies()
    gate["kernel_safeguards"]["probability_cap"] = 0.6
    next(rule for rule in rubric["checks"] if rule["rule_id"] == "kernel-cap")["params"][
        "maximum_subjective_probability"
    ] = 0.6
    result = compile_policy(frozen(gate), frozen(rubric, "rubric.json"), [], scope=scope())
    assert result.allowed_execution is False
    permission = next(
        item for item in result.compiled_rules if item.definition.rule_id == "execution-permission"
    )
    assert permission.definition.blocking_for == ["execution_permission"]
    assert permission.status == "not_available"
