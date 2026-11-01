"""Pure shape memo controls; no database, runtime, provenance, or network."""

from __future__ import annotations

import asyncio
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
from types import FunctionType
from typing import Any, ClassVar

import pytest
from jsonschema import Draft202012Validator
from pydantic import ConfigDict, StrictBool, StrictInt, StrictStr, ValidationError, model_validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from packages.contracts_py.decision_hub_contracts import binding_contract_base as binding
from packages.contracts_py.decision_hub_contracts.generated import research_binding as generated
from packages.contracts_py.decision_hub_contracts.generated.research_binding import (
    ResearchFactoryEventSource,
)
from packages.contracts_py.decision_hub_contracts.policy_contract_base import policy_format_checker


class Probe(binding.ResearchBindingContract):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")
    flag: StrictBool
    value: StrictInt
    tags: list[StrictStr]
    checks: ClassVar[int] = 0
    reject: ClassVar[bool] = False

    @model_validator(mode="after")
    def fresh_semantics(self) -> Probe:
        type(self).checks += 1
        if type(self).reject:
            raise ValueError("probe_semantics_rejected")
        return self


def body(value: int = 1) -> dict[str, Any]:
    return {"flag": True, "value": value, "tags": ["original"]}


def test_real_canonical_reference_repeated_shape_has_hits_and_fresh_dtos() -> None:
    path = Path(__file__).resolve().parents[1] / "fixtures/research_binding_contract_cases.json"
    original = json.loads(path.read_text(encoding="utf-8"))[0]["value"]
    with binding.shape_validation_scope() as stats:
        first = ResearchFactoryEventSource.model_validate(original)
        after_first = stats.hit_count
        second = ResearchFactoryEventSource.model_validate(dict(original))
        assert second is not first and first == second
        assert stats.hit_count > after_first and stats.miss_count >= 1


def test_only_shape_success_reused_and_pydantic_semantics_run_every_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Probe, "checks", 0)
    with binding.shape_validation_scope() as stats:
        first = Probe.model_validate(body())
        second = Probe.model_validate(body())
        assert second is not first and Probe.checks == 2
        assert stats.hit_count == stats.miss_count == 1
        monkeypatch.setattr(Probe, "reject", True)
        with pytest.raises(ValidationError, match="probe_semantics_rejected"):
            Probe.model_validate(body())
        assert Probe.checks == 3 and stats.hit_count == 2


def test_input_mutation_numeric_boolean_and_failures_never_reuse_success() -> None:
    payload = body()
    with binding.shape_validation_scope() as stats:
        Probe.model_validate(payload)
        payload["flag"] = 1
        for _ in range(2):
            with pytest.raises(ValidationError):
                Probe.model_validate(payload)
        assert stats.hit_count == 0 and stats.miss_count == 3
        payload["flag"] = True
        Probe.model_validate(payload)
        assert stats.hit_count == 1


def test_tuple_and_custom_scalar_do_not_collide_with_native_json() -> None:
    class CustomString(str):
        pass

    with binding.shape_validation_scope() as stats:
        Probe.model_validate(body())
        invalid = body()
        invalid["tags"] = ("original",)
        with pytest.raises(ValidationError):
            Probe.model_validate(invalid)
        custom = body()
        custom["tags"] = [CustomString("original")]
        Probe.model_validate(custom)
        assert stats.bypass_count == 2 and stats.hit_count == 0
        Probe.model_validate(body())
        assert stats.hit_count == 1


def test_schema_root_mutation_cannot_reuse_prior_result(monkeypatch: pytest.MonkeyPatch) -> None:
    validator = binding._validator(Probe)  # pyright: ignore[reportPrivateUsage]
    with binding.shape_validation_scope() as stats:
        Probe.model_validate(body())
        monkeypatch.setitem(validator.schema["properties"]["value"], "maximum", 0)
        with pytest.raises(ValidationError):
            Probe.model_validate(body())
        assert stats.hit_count == 0 and stats.miss_count == 2


def test_transitive_resource_mutation_and_validator_replacement_invalidate_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = Probe.model_json_schema()
    document = {"$id": "urn:binding-memo:test", "$defs": {"probe": definition}}
    registry: Registry[Any] = Registry(
        retrieve=binding._deny_retrieve,  # pyright: ignore[reportPrivateUsage] # Closed reader.
    ).with_resource(
        document["$id"], Resource.from_contents(document, default_specification=DRAFT202012),
    )
    validator = Draft202012Validator(
        {"$ref": "urn:binding-memo:test#/$defs/probe"}, registry=registry,
    )
    monkeypatch.setattr(binding, "_validator", lambda _model: validator)
    with binding.shape_validation_scope() as stats:
        Probe.model_validate(body())
        Probe.model_validate(body())
        assert stats.hit_count == stats.miss_count == 1
        definition["properties"]["value"]["maximum"] = 0
        with pytest.raises(ValidationError):
            Probe.model_validate(body())
        assert stats.hit_count == 1 and stats.miss_count == 2
        definition["properties"]["value"].pop("maximum")
        replacement = Draft202012Validator(validator.schema, registry=registry)
        monkeypatch.setattr(binding, "_validator", lambda _model: replacement)
        Probe.model_validate(body())
        assert stats.hit_count == 1 and stats.miss_count == 3


def test_unknown_formatter_never_consumes_a_prior_success(monkeypatch: pytest.MonkeyPatch) -> None:
    schema = Probe.model_json_schema()
    schema["properties"]["tags"]["items"]["format"] = "date-time"
    checker = policy_format_checker()
    validator = Draft202012Validator(schema, format_checker=checker)
    monkeypatch.setattr(binding, "_validator", lambda _model: validator)
    payload = {**body(), "tags": ["2026-10-04T12:00:00Z"]}
    with binding.shape_validation_scope() as stats:
        Probe.model_validate(payload)
        checker.checkers = {**checker.checkers, "date-time": (lambda _value: False, ())}
        with pytest.raises(ValidationError):
            Probe.model_validate(payload)
        assert stats.hit_count == 0 and stats.bypass_count == 1


def test_same_formatter_code_in_foreign_globals_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    checker = policy_format_checker()
    known, raises = checker.checkers["date-time"]
    assert isinstance(known, FunctionType)
    replacement = FunctionType(known.__code__, dict(known.__globals__))
    checker.checkers = {**checker.checkers, "date-time": (replacement, raises)}
    schema = Probe.model_json_schema()
    schema["properties"]["tags"]["items"]["format"] = "date-time"
    validator = Draft202012Validator(schema, format_checker=checker)
    monkeypatch.setattr(binding, "_validator", lambda _model: validator)
    payload = {**body(), "tags": ["2026-10-04T12:00:00Z"]}
    with binding.shape_validation_scope() as stats:
        Probe.model_validate(payload)
        Probe.model_validate(payload)
        assert stats.hit_count == stats.miss_count == 0 and stats.bypass_count == 2


def test_unknown_retriever_cannot_use_custom_equality_to_enter_memo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PretendingRetrieve:
        def __eq__(self, _other: object) -> bool:
            return True

        def __call__(self, _uri: str) -> Resource[Any]:
            raise AssertionError("Shape memo must never fetch a resource")

    document = {"$id": "urn:binding-memo:unknown", "$defs": {"probe": Probe.model_json_schema()}}
    registry: Registry[Any] = Registry(retrieve=PretendingRetrieve()).with_resource(
        document["$id"], Resource.from_contents(document, default_specification=DRAFT202012),
    )
    validator = Draft202012Validator(
        {"$ref": "urn:binding-memo:unknown#/$defs/probe"}, registry=registry,
    )
    monkeypatch.setattr(binding, "_validator", lambda _model: validator)
    with binding.shape_validation_scope() as stats:
        Probe.model_validate(body())
        Probe.model_validate(body())
        assert stats.hit_count == stats.miss_count == 0 and stats.bypass_count == 2


def test_limits_bypass_retention_and_nested_scope_reuses_only_outer_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(binding, "MAX_SHAPE_MEMO_ENTRIES", 2)
    with binding.shape_validation_scope() as outer:
        Probe.model_validate(body(1))
        with binding.shape_validation_scope() as nested:
            assert nested is outer
            Probe.model_validate(body(1))
            Probe.model_validate(body(2))
            Probe.model_validate(body(3))
            Probe.model_validate(body(3))
        assert outer.hit_count == 1 and outer.miss_count == 4
    monkeypatch.setattr(binding, "MAX_SHAPE_MEMO_TOTAL_BYTES", 1)
    with binding.shape_validation_scope() as full:
        Probe.model_validate(body())
        Probe.model_validate(body())
        assert full.hit_count == 0 and full.miss_count == 2
    with binding.shape_validation_scope() as oversized:
        payload = {**body(), "tags": ["x" * 32_768]}
        Probe.model_validate(payload)
        Probe.model_validate(payload)
        assert oversized.hit_count == 0 and oversized.bypass_count == 2


def test_exception_exit_drops_scope_and_copied_thread_context_cannot_use_success() -> None:
    with pytest.raises(RuntimeError, match="reader_interrupted"):
        with binding.shape_validation_scope():
            Probe.model_validate(body())
            raise RuntimeError("reader_interrupted")
    with binding.shape_validation_scope() as stats:
        Probe.model_validate(body())
        assert stats.miss_count == 1 and stats.hit_count == 0
        context = copy_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(context.run, lambda: Probe.model_validate(body())).result(timeout=10)
        assert stats.hit_count == 0
        Probe.model_validate(body())
        assert stats.hit_count == 1


def test_copied_same_owner_context_after_exit_cannot_reactivate_closed_memo() -> None:
    with binding.shape_validation_scope() as closed:
        Probe.model_validate(body())
        copied = copy_context()
    copied.run(lambda: Probe.model_validate(body()))
    copied.run(lambda: Probe.model_validate(body()))
    assert closed.hit_count == 0 and closed.miss_count == 1
    with binding.shape_validation_scope() as fresh:
        Probe.model_validate(body())
        assert fresh.hit_count == 0 and fresh.miss_count == 1


async def test_child_async_context_isolated_and_unscoped_calls_keep_normal_pydantic() -> None:
    async def child() -> None:
        Probe.model_validate(body())  # Inherited parent memo is not its authority.
        with binding.shape_validation_scope() as local:
            Probe.model_validate(body())
            Probe.model_validate(body())
            assert local.hit_count == local.miss_count == 1

    with binding.shape_validation_scope() as parent:
        Probe.model_validate(body())
        await asyncio.create_task(child())
        assert parent.hit_count == 0 and parent.miss_count == 1
        Probe.model_validate(body())
        assert parent.hit_count == 1
    before = Probe.checks
    Probe.model_validate(body())
    Probe.model_validate(body())
    assert Probe.checks == before + 2


def canonical_rule_samples() -> list[tuple[str, dict[str, Any]]]:
    """Only controlled pure-shape samples; these are not publication receipts."""
    root = Path(__file__).resolve().parents[2]
    policy = json.loads((root / "contracts/schemas/research_policy.schema.yaml").read_text("utf-8"))
    samples: list[tuple[str, dict[str, Any]]] = []
    for branch in policy["$defs"]["research_policy_rule"]["oneOf"]:
        name = branch["$ref"].rsplit("/", 1)[1]
        shape = policy["$defs"][name]
        fields = shape["properties"]
        params: dict[str, Any] = {}
        for field, declaration in fields["params"]["properties"].items():
            if "const" in declaration:
                params[field] = declaration["const"]
            else:
                assert declaration["type"] == "number"
                params[field] = declaration["maximum"]
        samples.append((name, {
            "rule_id": fields["rule_id"]["const"],
            "rule_version": fields["rule_version"]["const"],
            "category": fields["category"]["const"],
            "phase": fields["phase"]["enum"][0],
            "applies_to": ["current_snapshot"], "blocking_for": [], "params": params,
        }))
    assert len(samples) == 14
    return samples


@pytest.mark.parametrize("definition,payload", canonical_rule_samples())
def test_each_canonical_rule_rejects_only_wrong_tag_and_still_checks_full_matching_shape(
    definition: str, payload: dict[str, Any],
) -> None:
    title = "".join(part.title() for part in definition.split("_"))
    model = getattr(generated, title)
    parsed = model.model_validate(payload)  # Original unscoped validator remains authoritative.
    with binding.shape_validation_scope() as stats:
        assert model.model_validate(copy.deepcopy(payload)) == parsed
        assert stats.fast_reject_count == 0
        for _ in range(2):
            with pytest.raises(ValidationError, match="research_binding_contract_invalid"):
                model.model_validate({**payload, "rule_id": "other-canonical-rule"})
        assert stats.fast_reject_count == 2  # A failed branch is freshly rejected every time.
        with pytest.raises(ValidationError, match="research_binding_contract_invalid"):
            model.model_validate({**payload, "params": {**payload["params"], "extra": True}})
        assert stats.fast_reject_count == 2


def test_compiled_rule_union_fast_rejects_thirteen_wrong_branches_without_failure_cache() -> None:
    _definition, rule = canonical_rule_samples()[0]
    payload = {
        "definition": rule, "declaration_source": "gate", "applicability": "applicable",
        "handler_availability": "not_available", "status": "not_available",
        "reason_code": "prepared_not_executed",
    }
    with binding.shape_validation_scope() as stats:
        first = generated.ResearchCompiledRule.model_validate(payload)
        assert stats.fast_reject_count == 13
        second = generated.ResearchCompiledRule.model_validate(copy.deepcopy(payload))
        assert first == second and first is not second
        assert stats.fast_reject_count == 26 and stats.hit_count >= 1


@pytest.mark.parametrize("tag", [None, 1, True])
def test_missing_or_non_string_rule_tag_keeps_original_validation(tag: object) -> None:
    _definition, payload = canonical_rule_samples()[0]
    if tag is None:
        payload.pop("rule_id")
    else:
        payload["rule_id"] = tag
    with binding.shape_validation_scope() as stats:
        with pytest.raises(ValidationError):
            generated.ResearchPitRule.model_validate(payload)
        assert stats.fast_reject_count == 0


def test_custom_string_rule_tag_is_not_an_exact_native_fast_reject() -> None:
    class CustomTag(str):
        pass

    _definition, payload = canonical_rule_samples()[0]
    payload["rule_id"] = CustomTag("not-pit")
    with binding.shape_validation_scope() as stats:
        with pytest.raises(ValidationError):
            generated.ResearchPitRule.model_validate(payload)
        assert stats.fast_reject_count == 0


def test_rule_tag_constraint_is_reread_and_never_accepted_as_complete_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _definition, payload = canonical_rule_samples()[0]
    documents, _registry = binding.binding_schema_registry()
    shape = documents["research_policy.schema.yaml"]["$defs"]["research_pit_rule"]
    with binding.shape_validation_scope() as stats:
        with pytest.raises(ValidationError):
            generated.ResearchPitRule.model_validate({**payload, "rule_id": "changed"})
        assert stats.fast_reject_count == 1
        monkeypatch.setitem(shape["properties"]["rule_id"], "const", "changed")
        # Canonical JSON shape now matches, but the normal Pydantic Literal still rejects.
        with pytest.raises(ValidationError):
            generated.ResearchPitRule.model_validate({**payload, "rule_id": "changed"})
        assert stats.fast_reject_count == 1
        monkeypatch.setitem(
            shape, "required", [key for key in shape["required"] if key != "rule_id"],
        )
        with pytest.raises(ValidationError):
            generated.ResearchPitRule.model_validate(payload)
        assert stats.fast_reject_count == 1


@pytest.mark.parametrize("extension", ["keyword", "formatter", "retriever", "bound_ref"])
def test_unknown_rule_validator_extension_keeps_original_reject_path(
    monkeypatch: pytest.MonkeyPatch, extension: str,
) -> None:
    _definition, payload = canonical_rule_samples()[0]
    original = binding._validator(generated.ResearchPitRule)  # pyright: ignore[reportPrivateUsage]
    validator = Draft202012Validator(
        copy.deepcopy(original.schema),
        registry=original._registry,  # pyright: ignore[reportPrivateUsage] # Actual closed source.
        format_checker=policy_format_checker(),
    )
    if extension == "keyword":
        monkeypatch.setattr(Draft202012Validator, "VALIDATORS", {**Draft202012Validator.VALIDATORS,
            "const": lambda _validator, _expected, _instance, _schema: iter(()),
        })
        validator = Draft202012Validator(
            validator.schema,
            registry=original._registry,  # pyright: ignore[reportPrivateUsage] # Closed original.
        )
    elif extension == "formatter":
        assert validator.format_checker is not None
        validator.format_checker.checkers = {**validator.format_checker.checkers,
            "unrecognized": (lambda _value: True, ()),
        }
    elif extension == "retriever":
        registry = original._registry  # pyright: ignore[reportPrivateUsage]
        validator = Draft202012Validator(validator.schema, registry=Registry(
            resources=registry._resources,  # pyright: ignore[reportPrivateUsage] # No retrieval.
            retrieve=lambda uri: (_ for _ in ()).throw(AssertionError(uri)),
        ))
    else:
        validator.schema["$ref"] = validator.schema["$ref"].replace(
            "research_pit_rule", "research_kernel_cap_rule",
        )  # Bound original $ref differs; optimization must not read this new declaration.
    monkeypatch.setattr(binding, "_validator", lambda _model: validator)
    with binding.shape_validation_scope() as stats:
        with pytest.raises(ValidationError):
            generated.ResearchPitRule.model_validate({**payload, "rule_id": "wrong"})
        assert stats.fast_reject_count == 0
