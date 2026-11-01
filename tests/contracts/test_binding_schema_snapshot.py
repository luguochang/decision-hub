"""Pure schema snapshots only; no database, runtime or provenance verdict."""

from __future__ import annotations

import copy
from types import FunctionType, MethodType
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from pydantic import BaseModel, StrictInt
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012

from packages.contracts_py.decision_hub_contracts import binding_contract_base as binding
from packages.contracts_py.decision_hub_contracts.policy_contract_base import policy_format_checker


class SnapshotProbe(BaseModel):
    value: StrictInt


def test_bound_reference_operation_keeps_exact_owner_descriptor_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = DRAFT202012.anchors_in
    assert type(original) is MethodType and DRAFT202012.anchors_in is not original
    same = binding._same_reference_callable  # pyright: ignore[reportPrivateUsage]
    assert same(DRAFT202012.anchors_in, original)
    assert not same(MethodType(original.__func__, object()), original)
    assert not same(MethodType(lambda *_args: (), DRAFT202012), original)
    assert binding._reference_operations_unchanged()  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(original.__func__, "__defaults__", (object(),))
    assert not binding._reference_operations_unchanged()  # pyright: ignore[reportPrivateUsage]


def controlled_schema() -> tuple[Draft202012Validator, dict[str, Any], dict[str, Any]]:
    leaf: dict[str, Any] = {
        "$id": "urn:snapshot:leaf",
        "$defs": {"value": {"type": "integer", "maximum": 5}},
    }
    source: dict[str, Any] = {
        "$id": "urn:snapshot:source",
        "$defs": {
            "probe": {
                "type": "object",
                "required": ["value"],
                "additionalProperties": False,
                "properties": {"value": {"$ref": "urn:snapshot:leaf#/$defs/value"}},
            }
        },
    }
    alternate: dict[str, Any] = {
        "$id": "urn:snapshot:alternate",
        "$defs": {
            "value": {"type": "integer", "const": 0},
            "probe": {"type": "object", "properties": {"value": {"const": 0}}},
        },
    }
    registry: Registry[Any] = Registry(
        retrieve=binding._deny_retrieve,  # pyright: ignore[reportPrivateUsage] # Closed pure test.
    )
    for document in (source, leaf, alternate):
        registry = registry.with_resource(
            document["$id"],
            Resource.from_contents(
                document,
                default_specification=DRAFT202012,
            ),
        )
    validator = Draft202012Validator(
        {"$ref": "urn:snapshot:source#/$defs/probe"}, registry=registry
    )
    return validator, source, leaf


def validate(validator: Draft202012Validator, value: int = 2) -> None:
    binding._validate_shape(  # pyright: ignore[reportPrivateUsage] # Exercise actual pure helper.
        SnapshotProbe,
        validator,
        {"value": value},
    )


def test_snapshot_hot_path_still_resolves_each_ref_without_full_signature_walk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator, _source, _leaf = controlled_schema()
    original = binding._shape_schema_signature_checked  # pyright: ignore[reportPrivateUsage]
    original_lookup = binding._snapshot_lookup  # pyright: ignore[reportPrivateUsage]
    walks = 0
    lookups = 0

    def walk(current: Draft202012Validator) -> bytes | None:
        nonlocal walks
        walks += 1
        return original(current)  # Original algorithm and return value are unchanged.

    def lookup(current: Any, reference: str) -> tuple[Any, Any] | None:
        nonlocal lookups
        lookups += 1
        return original_lookup(current, reference)

    monkeypatch.setattr(binding, "_shape_schema_signature_checked", walk)
    monkeypatch.setattr(binding, "_snapshot_lookup", lookup)
    with binding.shape_validation_scope() as stats:
        validate(validator)
        first_lookups = lookups
        validate(validator)
        assert walks == 1 and lookups > first_lookups
        assert stats.hit_count == 1 and stats.schema_snapshot_hit_count == 1


@pytest.mark.parametrize("change", ["nested", "add_ref", "delete_ref", "replace_ref"])
def test_snapshot_detects_transitive_mutation_and_added_deleted_replaced_refs(change: str) -> None:
    validator, source, leaf = controlled_schema()
    with binding.shape_validation_scope() as stats:
        validate(validator)
        probe = source["$defs"]["probe"]
        if change == "nested":
            leaf["$defs"]["value"]["maximum"] = 0
        elif change == "add_ref":
            probe["allOf"] = [{"$ref": "urn:snapshot:alternate#/$defs/probe"}]
        elif change == "delete_ref":
            probe["properties"]["value"] = {"type": "string"}
        else:
            probe["properties"]["value"]["$ref"] = "urn:snapshot:alternate#/$defs/value"
        with pytest.raises(ValidationError):
            validate(validator)
        assert stats.hit_count == stats.schema_snapshot_hit_count == 0


def test_resource_replacement_and_missing_resource_are_freshly_read() -> None:
    validator, _source, leaf = controlled_schema()
    with binding.shape_validation_scope() as stats:
        validate(validator)
        current = validator._registry  # pyright: ignore[reportPrivateUsage] # Pure controlled source.
        changed = copy.deepcopy(leaf)
        changed["$defs"]["value"]["maximum"] = 0
        replacement = current.with_resource(
            leaf["$id"],
            Resource.from_contents(
                changed,
                default_specification=DRAFT202012,
            ),
        )
        # Rebind a real official resolver to the changed immutable registry.
        object.__setattr__(validator, "_registry", replacement)
        object.__setattr__(validator, "_resolver", replacement.resolver())
        with pytest.raises(ValidationError):
            validate(validator)
        missing = replacement.remove(leaf["$id"])
        object.__setattr__(validator, "_registry", missing)
        object.__setattr__(validator, "_resolver", missing.resolver())
        with pytest.raises(Unresolvable, match="urn:snapshot:leaf"):
            validate(validator)
        assert stats.hit_count == stats.schema_snapshot_hit_count == 0


def test_separately_bound_keyword_value_cannot_reuse_public_root_snapshot() -> None:
    validator, _source, _leaf = controlled_schema()
    with binding.shape_validation_scope() as stats:
        validate(validator)
        function, keyword, _bound = validator._validators[0]  # pyright: ignore[reportPrivateUsage]
        validator._validators[0] = (  # pyright: ignore[reportPrivateUsage] # Actual keyword binding.
            function,
            keyword,
            "urn:snapshot:alternate#/$defs/probe",
        )
        with pytest.raises(ValidationError):
            validate(validator)
        assert stats.hit_count == stats.schema_snapshot_hit_count == 0


def test_unknown_resolver_and_changed_lookup_use_original_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator, _source, _leaf = controlled_schema()
    official = validator._resolver  # pyright: ignore[reportPrivateUsage]
    called = 0
    original_lookup = type(official).lookup

    def changed_lookup(current: Any, reference: str) -> Any:
        nonlocal called
        called += 1
        return original_lookup(current, reference)

    with binding.shape_validation_scope() as stats:
        validate(validator)
        monkeypatch.setattr(type(official), "lookup", changed_lookup)
        validate(validator)
        assert called > 0 and stats.bypass_count == 1
        assert stats.hit_count == stats.schema_snapshot_hit_count == 0
    monkeypatch.undo()

    class UnknownResolver:
        def lookup(self, reference: str) -> Any:
            nonlocal called
            called += 1
            return original_lookup(official, reference)

    with binding.shape_validation_scope() as unknown:
        validate(validator)
        object.__setattr__(validator, "_resolver", UnknownResolver())
        before = called
        validate(validator)
        assert called > before and unknown.bypass_count == 1
        assert unknown.hit_count == unknown.schema_snapshot_hit_count == 0


@pytest.mark.parametrize(
    "current,saved",
    [
        (True, 1),
        (1, 1.0),
        ([1], (1,)),
        ({"value": True}, {"value": 1}),
        (0.0, -0.0),
        (float("inf"), float("inf")),
    ],
)
def test_exact_native_compare_preserves_type_and_float_bytes(current: Any, saved: Any) -> None:
    assert not binding._same_native(  # pyright: ignore[reportPrivateUsage] # Narrow native control.
        current,
        saved,
    )


def test_native_compare_never_calls_unknown_value_or_key_equality() -> None:
    class EqualString(str):
        def __eq__(self, _other: object) -> bool:
            raise AssertionError("Unknown equality must not run")

        __hash__ = str.__hash__

    assert not binding._same_native(  # pyright: ignore[reportPrivateUsage]
        {EqualString("value"): 1},
        {"value": 1},
    )
    assert not binding._same_native(  # pyright: ignore[reportPrivateUsage]
        {"value": EqualString("same")},
        {"value": "same"},
    )
    assert not binding._same_native(  # pyright: ignore[reportPrivateUsage]
        {"value": 1},
        {EqualString("value"): 1},
    )


def test_invalid_shape_never_installs_a_schema_snapshot() -> None:
    validator, _source, _leaf = controlled_schema()
    with binding.shape_validation_scope() as stats:
        for _ in range(2):
            with pytest.raises(ValidationError):
                validate(validator, 6)
        memo = binding._SHAPE_MEMO.get()  # pyright: ignore[reportPrivateUsage]
        assert memo is not None and not memo.schemas and not memo.successes
        assert stats.miss_count == 2 and stats.schema_snapshot_hit_count == 0


def test_snapshot_and_payload_share_existing_aggregate_bound_and_outer_scope_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator, _source, _leaf = controlled_schema()
    monkeypatch.setattr(binding, "MAX_SHAPE_MEMO_TOTAL_BYTES", 256)
    with binding.shape_validation_scope() as stats:
        validate(validator)
        validate(validator)
        memo = binding._SHAPE_MEMO.get()  # pyright: ignore[reportPrivateUsage]
        assert memo is not None and not memo.schemas and memo.retained_bytes <= 256
        assert stats.schema_snapshot_hit_count == 0
    assert memo is not None and not memo.schemas and memo.retained_bytes == 0


@pytest.mark.parametrize("change", ["re", "datetime", "fullmatch", "custom_formatter"])
def test_policy_formatter_dependency_change_bypasses_snapshots_and_runs_actual_checker(
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    schema = {"type": "object", "properties": {"value": {"type": "string", "format": "date-time"}}}
    checker = policy_format_checker()
    validator = Draft202012Validator(schema, format_checker=checker)
    payload = {"value": "2026-10-04T12:00:00Z"}
    function, raises = checker.checkers["date-time"]
    assert isinstance(function, FunctionType)
    with binding.shape_validation_scope() as stats:
        binding._validate_shape(SnapshotProbe, validator, payload)  # pyright: ignore[reportPrivateUsage]
        namespace = function.__globals__
        if change == "re":

            class RejectRegex:
                @staticmethod
                def fullmatch(_pattern: str, _value: str) -> None:
                    return None

            monkeypatch.setitem(namespace, "re", RejectRegex)
        elif change == "datetime":

            class RejectDatetime:
                @staticmethod
                def fromisoformat(_value: str) -> None:
                    raise ValueError("Controlled formatter dependency changed")

            monkeypatch.setitem(namespace, "datetime", RejectDatetime)
        elif change == "fullmatch":

            def reject_fullmatch(_pattern: object, _value: object) -> None:
                return None

            monkeypatch.setattr(namespace["re"], "fullmatch", reject_fullmatch)
        else:

            def reject_format(_value: object) -> bool:
                return False

            checker.checkers = {**checker.checkers, "date-time": (reject_format, raises)}
        with pytest.raises(ValidationError):
            binding._validate_shape(SnapshotProbe, validator, payload)  # pyright: ignore[reportPrivateUsage]
        assert stats.hit_count == stats.schema_snapshot_hit_count == 0 and stats.bypass_count == 1


def test_custom_checker_mapping_cannot_present_known_items_and_a_different_lookup() -> None:
    def reject_format(_value: object) -> bool:
        return False

    class RedirectingCheckers(dict[str, Any]):
        def __getitem__(self, key: str) -> Any:
            if key == "date-time":
                return reject_format, ()
            return super().__getitem__(key)

    checker = policy_format_checker()
    validator = Draft202012Validator(
        {"type": "string", "format": "date-time"},
        format_checker=checker,
    )
    payload = "2026-10-04T12:00:00Z"
    with binding.shape_validation_scope() as stats:
        binding._validate_shape(SnapshotProbe, validator, payload)  # pyright: ignore[reportPrivateUsage]
        checker.checkers = RedirectingCheckers(checker.checkers)
        with pytest.raises(ValidationError):
            binding._validate_shape(SnapshotProbe, validator, payload)  # pyright: ignore[reportPrivateUsage]
        assert stats.hit_count == stats.schema_snapshot_hit_count == 0 and stats.bypass_count == 1


def test_original_fullmatch_default_change_bypasses_snapshot_and_keeps_original_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checker = policy_format_checker()
    function, _raises = checker.checkers["date-time"]
    assert isinstance(function, FunctionType)
    regex = function.__globals__["re"]
    validator = Draft202012Validator(
        {"type": "string", "format": "date-time"},
        format_checker=checker,
    )
    payload = "2026-10-04T12:00:00Z"
    with binding.shape_validation_scope() as stats:
        binding._validate_shape(SnapshotProbe, validator, payload)  # pyright: ignore[reportPrivateUsage]
        monkeypatch.setattr(regex.fullmatch, "__defaults__", (regex.LOCALE,))
        original_policy = binding._POLICY_FORMAT_FUNCTIONS[0]  # pyright: ignore[reportPrivateUsage]
        monkeypatch.setattr(original_policy, "__qualname__", "controlled_changed_name")
        # The actual stdlib formatter rejects this flag for a str pattern.
        with pytest.raises(ValueError, match="LOCALE"):
            binding._validate_shape(SnapshotProbe, validator, payload)  # pyright: ignore[reportPrivateUsage]
        assert stats.hit_count == stats.schema_snapshot_hit_count == 0 and stats.bypass_count == 1
