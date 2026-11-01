"""Reference factoring preserves every event financial original exactly."""
from copy import deepcopy
from importlib import import_module

import pytest

from packages.kernel.decision_hub_kernel.decision.event_scenario_action_plan import (
    build_event_scenario_action_plan,
)
from tests.decision_product.test_event_scenario_action_plan import fixture_plan


def test_manifest_round_trip_preserves_full_plan_and_rejects_one_changed_original():
    codec = import_module("packages.kernel.decision_hub_kernel.ports.event_scenario_manifest")
    draft, originals = fixture_plan()
    plan = build_event_scenario_action_plan(draft, originals)
    material = dict(event_plan=plan, normalized_event_plan_draft=draft, declared_originals=[])
    manifest = codec.pack_event_scenario_manifest(material)
    assert codec.unpack_event_scenario_manifest(manifest) == material
    assert len(codec.canonical(manifest).encode()) < len(codec.canonical(material).encode())
    changed = deepcopy(manifest)
    changed["originals"][0]["body"] = {"changed": True}
    with pytest.raises(ValueError, match="event_manifest_original"):
        codec.unpack_event_scenario_manifest(changed)


def test_manifest_shares_object_keys_and_original_hash_strings_without_losing_material():
    codec = import_module("packages.kernel.decision_hub_kernel.ports.event_scenario_manifest")
    draft, originals = fixture_plan()
    material = dict(
        event_plan=build_event_scenario_action_plan(draft, originals),
        normalized_event_plan_draft=draft, declared_originals=[],
    )
    packed = codec.pack_event_scenario_manifest(material)
    assert packed["object_shapes"]
    assert packed["hashes"]
    assert codec.canonical(codec.unpack_event_scenario_manifest(packed)) == codec.canonical(
        material
    )
    changed = deepcopy(packed)
    changed["object_shapes"][0] = ["duplicate", "duplicate"]
    with pytest.raises(ValueError, match="event_manifest_object_shapes_invalid"):
        codec.unpack_event_scenario_manifest(changed)


@pytest.mark.parametrize("body", [
    {"$h": True}, {"$h": 1}, {"$d": [True, []]}, {"$d": [0, []]},
])
def test_manifest_rejects_invalid_shared_table_index_or_dictionary_arity(body):
    codec = import_module("packages.kernel.decision_hub_kernel.ports.event_scenario_manifest")
    digest = codec.content_hash(body)
    packed = dict(
        schema_version="event-scenario-preparation-manifest.v1",
        root_ref=dict(ref=codec.PREFIX + digest, hash=digest),
        ref_prefixes=[], object_shapes=[["field"]], hashes=["a" * 64],
        originals=[dict(hash=digest, body=body)], expanded_hash="b" * 64,
    )
    with pytest.raises(ValueError, match="event_manifest_(hash|dict)_"):
        codec.unpack_event_scenario_manifest(packed)


def test_exact_validation_reuse_is_local_and_does_not_accept_changed_wire(monkeypatch):
    codec = import_module("packages.kernel.decision_hub_kernel.ports.event_scenario_manifest")
    contracts = import_module(
        "packages.contracts_py.decision_hub_contracts.generated.decision_product_v2"
    )
    draft, originals = fixture_plan()
    material = dict(
        event_plan=build_event_scenario_action_plan(draft, originals),
        normalized_event_plan_draft=draft, declared_originals=[],
    )
    packed = codec.pack_event_scenario_manifest(material)
    validated = []
    original_validate = contracts.EventScenarioActionPlanV1.model_validate

    def count_validation(value, *args, **kwargs):
        validated.append(True)
        return original_validate(value, *args, **kwargs)

    monkeypatch.setattr(contracts.EventScenarioActionPlanV1, "model_validate", count_validation)
    with codec.event_scenario_validation_scope():
        first = codec.unpack_event_scenario_manifest(packed)
        first["event_plan"]["branches"].clear()
        with codec.event_scenario_validation_scope():
            assert codec.unpack_event_scenario_manifest(packed) == material
        assert len(validated) == 1
        changed = deepcopy(packed)
        changed["originals"][0]["body"] = {"changed": True}
        with pytest.raises(ValueError, match="event_manifest_original"):
            codec.unpack_event_scenario_manifest(changed)
    assert codec.unpack_event_scenario_manifest(packed) == material
    assert len(validated) == 2
