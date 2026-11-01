"""Bounded event preparation reference manifest; no financial transformation.

Only duplicate JSON objects/arrays are factored. The complete expanded plan and
recipe must retain identical canonical bytes; every original is read exactly
once, with no external resolution, orphan originals or hidden branches.
"""

import json
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

from ..decision.action_comparison import content_hash

MARKER = "$e"
OBJECT = "$o"
SOURCE_REF = "$r"
DICT = "$d"
HASH = "$h"
PREFIX = "event-scenario-manifest:"
_VALIDATED_MATERIAL: ContextVar[dict[str, dict] | None] = ContextVar(
    "event_scenario_validated_material", default=None
)


@contextmanager
def event_scenario_validation_scope():
    """Reuse pure validation only within one caller's exact replay operation."""
    if _VALIDATED_MATERIAL.get() is not None:
        yield
        return
    token = _VALIDATED_MATERIAL.set({})
    try:
        yield
    finally:
        _VALIDATED_MATERIAL.reset(token)


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _is_hash(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(item in "0123456789abcdef" for item in value)
    )


def pack_event_scenario_manifest(material: dict) -> dict:
    counts = Counter()
    shapes = {}
    source_names = set()

    def count(value):
        if isinstance(value, (dict, list)):
            if isinstance(value, dict) and any(
                key in value for key in (MARKER, OBJECT, SOURCE_REF, DICT, HASH)
            ):
                raise ValueError("event_manifest_reserved_marker")
            if isinstance(value, dict):
                shapes.setdefault(tuple(sorted(value)), []).append(value)
                if (
                    set(value) == {"ref", "hash"}
                    and isinstance(value["ref"], str)
                    and isinstance(value["hash"], str)
                    and len(value["hash"]) == 64
                    and not value["ref"].endswith(value["hash"])
                ):
                    source_names.add(value["ref"])
            text = canonical(value)
            if len(text.encode()) >= 96:
                counts[text] += 1
            for item in value.values() if isinstance(value, dict) else value:
                count(item)

    count(material)
    prefixes = {
        name[: index + 1]
        for name in source_names
        for index, character in enumerate(name)
        if character == ":" and 16 <= index + 1 <= 1024
    }
    selected, assigned = [], dict.fromkeys(source_names, 0)
    while prefixes and len(selected) < 256:

        def saving(prefix):
            return (
                sum(
                    max(0, len(prefix) - assigned[name])
                    for name in source_names
                    if name.startswith(prefix)
                )
                - len(prefix)
                - 16
            )

        best = min(prefixes, key=lambda prefix: (-saving(prefix), prefix))
        if saving(best) <= 64:
            break
        selected.append(best)
        prefixes.remove(best)
        for name in source_names:
            if name.startswith(best):
                assigned[name] = max(assigned[name], len(best))
    ref_prefixes = sorted(selected)
    templates = {}
    for shape, rows in shapes.items():
        if len(rows) < 2:
            continue
        values = list({canonical(row): row for row in rows}.values())
        fields = [{key: canonical(row[key]) for key in shape} for row in values]
        common_sets = {}
        # Position/action subgroups can share more fields than the whole shape.
        # Intersections retain only equal explicit fields, never defaults.
        if len(shape) > 3:
            for left in range(len(values)):
                for right in range(left + 1, len(values)):
                    common = {
                        key: values[left][key]
                        for key in shape
                        if fields[left][key] == fields[right][key]
                    }
                    if (
                        common
                        and len(common) < len(shape)
                        and len(canonical(common).encode()) > 256
                    ):
                        common_sets[canonical(common)] = common
        candidates = sorted(
            common_sets.values(), key=lambda row: (-len(canonical(row).encode()), canonical(row))
        )
        if candidates:
            templates[shape] = candidates
    originals = {}

    def encode(value, *, root=False, template=True):
        if not isinstance(value, (dict, list)):
            return value
        if (
            isinstance(value, dict)
            and set(value) == {"ref", "hash"}
            and isinstance(value["ref"], str)
            and isinstance(value["hash"], str)
            and len(value["hash"]) == 64
            and value["ref"].endswith(value["hash"])
        ):
            return {SOURCE_REF: [value["ref"][:-64], value["hash"]]}
        if (
            isinstance(value, dict)
            and set(value) == {"ref", "hash"}
            and isinstance(value["ref"], str)
            and isinstance(value["hash"], str)
            and len(value["hash"]) == 64
        ):
            matches = [prefix for prefix in ref_prefixes if value["ref"].startswith(prefix)]
            if matches:
                prefix = max(matches, key=len)
                return {
                    SOURCE_REF: [
                        ref_prefixes.index(prefix),
                        value["ref"][len(prefix) :],
                        value["hash"],
                    ]
                }
        candidates = (
            templates.get(tuple(sorted(value)), []) if template and isinstance(value, dict) else []
        )
        common = next(
            (
                base
                for base in candidates
                if all(key in value and value[key] == item for key, item in base.items())
            ),
            None,
        )
        if common is not None:
            base = encode(common, root=True, template=False)[MARKER]
            encoded = {
                OBJECT: [
                    base,
                    {key: encode(item) for key, item in value.items() if key not in common},
                ]
            }
        else:
            encoded = (
                {key: encode(item) for key, item in value.items()}
                if isinstance(value, dict)
                else [encode(item) for item in value]
            )
        text = canonical(value)
        if not root and counts[text] < 2:
            return encoded
        digest = content_hash(value)
        originals[digest] = encoded
        return {MARKER: digest}

    root = encode(material, root=True)[MARKER]

    # Count uses in the factored graph, not the expanded tree. A common parent
    # can turn a previously repeated child into a single use. Inline fragments
    # whose own table/marker overhead exceeds their exact shared-body saving.
    def references(value):
        if isinstance(value, list):
            for item in value:
                yield from references(item)
        elif isinstance(value, dict):
            if set(value) == {MARKER}:
                yield value[MARKER]
            elif set(value) == {OBJECT}:
                yield value[OBJECT][0]
                yield from references(value[OBJECT][1])
            else:
                for item in value.values():
                    yield from references(item)

    while True:
        uses = Counter(key for body in originals.values() for key in references(body))
        inline = set()
        marker_size = len(json.dumps(canonical({MARKER: "0" * 64})).encode())
        for key, body in originals.items():
            if key == root:
                continue
            text = canonical(body)
            row_size = len(canonical(dict(hash=key, body=body)).encode())
            saved = uses[key] * (len(text.encode()) - marker_size)
            if saved <= row_size:
                inline.add(key)
        if not inline:
            break

        def expand_inline(value, *, selected=inline, source_nodes=originals):
            if isinstance(value, list):
                return [expand_inline(item) for item in value]
            if isinstance(value, dict):
                if set(value) == {MARKER} and value[MARKER] in selected:
                    return expand_inline(source_nodes[value[MARKER]])
                if set(value) == {OBJECT}:
                    base, overrides = value[OBJECT]
                    if base in selected:
                        common = expand_inline(source_nodes[base])
                        # A template base is a plain subset object. Its own
                        # nested values may remain locally referenced.
                        return {**common, **expand_inline(overrides)}
                    return {OBJECT: [base, expand_inline(overrides)]}
                return {key: expand_inline(item) for key, item in value.items()}
            return value

        originals = {
            key: expand_inline(body) for key, body in originals.items() if key not in inline
        }

    # Shared tables describe the reachable packed graph only. Local node
    # addresses are deliberately excluded from the original-value hash pool:
    # they change when the retained representation is re-addressed below.
    shape_counts, hash_counts, reachable = Counter(), Counter(), set()

    def collect_node(key):
        if key not in reachable:
            reachable.add(key)
            collect(originals[key])

    def collect(value):
        if isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            if set(value) == {MARKER}:
                collect_node(value[MARKER])
            elif set(value) == {OBJECT}:
                collect_node(value[OBJECT][0])
                collect(value[OBJECT][1])
            elif set(value) == {SOURCE_REF}:
                digest = value[SOURCE_REF][-1]
                if _is_hash(digest):
                    hash_counts[digest] += 1
            else:
                shape_counts[tuple(sorted(value))] += 1
                for item in value.values():
                    collect(item)
        elif _is_hash(value):
            hash_counts[value] += 1

    collect_node(root)

    def shape_saving(shape):
        # Use the largest possible index overhead when selecting profitable
        # shapes. This is representation selection, never omission of fields.
        removed = sum(len(canonical(key).encode()) + 1 for key in shape)
        overhead = len(canonical({DICT: [1023, []]}).encode()) - 2
        table_bytes = len(canonical(list(shape)).encode()) + 1
        return shape_counts[shape] * (removed - overhead) - table_bytes

    selected_shapes = sorted(
        (shape for shape in shape_counts if shape_saving(shape) > 0),
        key=lambda shape: (-shape_saving(shape), shape),
    )[:1024]
    object_shapes = [list(shape) for shape in sorted(selected_shapes)]
    shape_indices = {tuple(shape): index for index, shape in enumerate(object_shapes)}
    shared_hashes = sorted(
        sorted(
            (digest for digest, uses in hash_counts.items() if uses > 1),
            key=lambda digest: (-hash_counts[digest], digest),
        )[:4096]
    )
    hash_indices = {digest: index for index, digest in enumerate(shared_hashes)}

    # Re-address the complete acyclic graph after inlining. Packed-body hashes
    # authenticate the actual retained representation, with one 64-hex local
    # identity per marker rather than repeating its ref prefix and hash.
    records, addressed = {}, {}

    def address(key):
        if key in addressed:
            return addressed[key]

        def rewrite(value):
            if isinstance(value, list):
                return [rewrite(item) for item in value]
            if isinstance(value, dict):
                if set(value) == {MARKER}:
                    return {MARKER: address(value[MARKER])["hash"]}
                if set(value) == {OBJECT}:
                    return {OBJECT: [address(value[OBJECT][0])["hash"], rewrite(value[OBJECT][1])]}
                if set(value) == {SOURCE_REF}:
                    parts = value[SOURCE_REF]
                    return {SOURCE_REF: [*parts[:-1], hash_indices.get(parts[-1], parts[-1])]}
                shape = tuple(sorted(value))
                if shape in shape_indices:
                    return {DICT: [shape_indices[shape], [rewrite(value[key]) for key in shape]]}
                return {name: rewrite(item) for name, item in value.items()}
            if _is_hash(value) and value in hash_indices:
                return {HASH: hash_indices[value]}
            return value

        body = rewrite(originals[key])
        digest = content_hash(body)
        reference = dict(ref=PREFIX + digest, hash=digest)
        records[reference["ref"]] = dict(hash=digest, body=body)
        addressed[key] = reference
        return reference

    root_ref = address(root)
    result = dict(
        schema_version="event-scenario-preparation-manifest.v1",
        root_ref=root_ref,
        ref_prefixes=ref_prefixes,
        object_shapes=object_shapes,
        hashes=shared_hashes,
        originals=[records[key] for key in sorted(records)],
        expanded_hash=content_hash(material),
    )
    if unpack_event_scenario_manifest(result) != material:
        raise ValueError("event_manifest_round_trip_changed")
    return result


def unpack_event_scenario_manifest(manifest: dict) -> dict:
    memo = _VALIDATED_MATERIAL.get()
    if memo is None:
        return _unpack_event_scenario_manifest(manifest)
    # Include the actual entire wire, not merely its claimed expanded_hash or
    # root reference. No source, permission or authorization result is cached.
    identity = canonical(manifest)
    if identity not in memo:
        memo[identity] = deepcopy(_unpack_event_scenario_manifest(manifest))
    return deepcopy(memo[identity])


def _unpack_event_scenario_manifest(manifest: dict) -> dict:
    if (
        set(manifest)
        != {"schema_version", "root_ref", "ref_prefixes", "object_shapes", "hashes",
            "originals", "expanded_hash"}
        or manifest["schema_version"] != "event-scenario-preparation-manifest.v1"
        or not 1 <= len(manifest["originals"]) <= 4096
    ):
        raise ValueError("event_manifest_shape_invalid")
    prefixes = manifest["ref_prefixes"]
    if (
        not isinstance(prefixes, list)
        or len(prefixes) > 256
        or any(not isinstance(item, str) or not 16 <= len(item) <= 1024 for item in prefixes)
        or prefixes != sorted(set(prefixes))
    ):
        raise ValueError("event_manifest_ref_prefixes_invalid")
    object_shapes, hashes = manifest["object_shapes"], manifest["hashes"]
    if (
        not isinstance(object_shapes, list)
        or len(object_shapes) > 1024
        or any(
            not isinstance(shape, list)
            or any(not isinstance(key, str) for key in shape)
            or shape != sorted(set(shape))
            for shape in object_shapes
        )
        or object_shapes != [list(shape) for shape in sorted(set(map(tuple, object_shapes)))]
    ):
        raise ValueError("event_manifest_object_shapes_invalid")
    if (
        not isinstance(hashes, list)
        or len(hashes) > 4096
        or any(not _is_hash(item) for item in hashes)
        or hashes != sorted(set(hashes))
    ):
        raise ValueError("event_manifest_hashes_invalid")

    def decode_hash(value):
        if _is_hash(value):
            return value
        if type(value) is int and 0 <= value < len(hashes):
            return hashes[value]
        raise ValueError("event_manifest_hash_index_invalid")

    originals = {}
    for original in manifest["originals"]:
        if set(original) != {"hash", "body"}:
            raise ValueError("event_manifest_original_shape_invalid")
        reference = dict(ref=PREFIX + original["hash"], hash=original["hash"])
        if (
            set(reference) != {"ref", "hash"}
            or reference["ref"] in originals
            or reference["ref"] != PREFIX + reference["hash"]
        ):
            raise ValueError("event_manifest_original_reference_invalid")
        body = original["body"]
        if not isinstance(body, (dict, list)) or content_hash(body) != reference["hash"]:
            raise ValueError("event_manifest_original_hash_mismatch")
        originals[reference["ref"]] = (reference, body)
    visiting, used, cache = set(), set(), {}

    def resolve(reference):
        name = reference["ref"]
        if name in visiting or name not in originals or originals[name][0] != reference:
            raise ValueError("event_manifest_original_missing_or_cycle")
        used.add(name)
        if name in cache:
            return cache[name]
        visiting.add(name)
        expanded = expand(originals[name][1])
        visiting.remove(name)
        cache[name] = expanded
        return expanded

    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if isinstance(value, dict):
            if HASH in value:
                if set(value) != {HASH} or type(value[HASH]) is not int:
                    raise ValueError("event_manifest_hash_marker_invalid")
                return decode_hash(value[HASH])
            if DICT in value:
                parts = value[DICT]
                if (
                    set(value) != {DICT} or not isinstance(parts, list) or len(parts) != 2
                    or type(parts[0]) is not int or not 0 <= parts[0] < len(object_shapes)
                    or not isinstance(parts[1], list)
                ):
                    raise ValueError("event_manifest_dict_marker_invalid")
                keys, values = object_shapes[parts[0]], parts[1]
                if len(keys) != len(values):
                    raise ValueError("event_manifest_dict_arity_invalid")
                return {key: expand(item) for key, item in zip(keys, values, strict=True)}
            if SOURCE_REF in value:
                parts = value[SOURCE_REF]
                if (
                    set(value) == {SOURCE_REF}
                    and isinstance(parts, list)
                    and len(parts) == 3
                    and type(parts[0]) is int
                    and 0 <= parts[0] < len(prefixes)
                    and isinstance(parts[1], str)
                ):
                    return dict(ref=prefixes[parts[0]] + parts[1], hash=decode_hash(parts[2]))
                if (
                    set(value) != {SOURCE_REF}
                    or not isinstance(parts, list)
                    or len(parts) != 2
                    or not isinstance(parts[0], str)
                ):
                    raise ValueError("event_manifest_source_ref_invalid")
                digest = decode_hash(parts[1])
                return dict(ref=parts[0] + digest, hash=digest)
            if OBJECT in value:
                if (
                    set(value) != {OBJECT}
                    or not isinstance(value[OBJECT], list)
                    or len(value[OBJECT]) != 2
                ):
                    raise ValueError("event_manifest_object_marker_invalid")
                digest, overrides = value[OBJECT]
                if (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or any(item not in "0123456789abcdef" for item in digest)
                    or not isinstance(overrides, dict)
                ):
                    raise ValueError("event_manifest_object_marker_invalid")
                common = resolve(dict(ref=PREFIX + digest, hash=digest))
                changes = expand(overrides)
                if (
                    not isinstance(common, dict)
                    or not isinstance(changes, dict)
                    or common.keys() & changes.keys()
                ):
                    raise ValueError("event_manifest_object_override_invalid")
                return {**common, **changes}
            if MARKER in value:
                if set(value) != {MARKER}:
                    raise ValueError("event_manifest_original_marker_invalid")
                digest = value[MARKER]
                if (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or any(item not in "0123456789abcdef" for item in digest)
                ):
                    raise ValueError("event_manifest_original_marker_invalid")
                return resolve(dict(ref=PREFIX + digest, hash=digest))
            return {key: expand(item) for key, item in value.items()}
        return value

    result = resolve(manifest["root_ref"])
    if used != originals.keys() or content_hash(result) != manifest["expanded_hash"]:
        raise ValueError("event_manifest_original_orphan_or_expansion_changed")
    if not isinstance(result, dict) or set(result) != {
        "event_plan", "normalized_event_plan_draft", "declared_originals"
    }:
        raise ValueError("event_manifest_material_shape_invalid")
    from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
        EventScenarioActionPlanDraftV1,
        EventScenarioActionPlanV1,
        EventScenarioAuthoredOriginalV1,
    )

    EventScenarioActionPlanV1.model_validate(result["event_plan"])
    EventScenarioActionPlanDraftV1.model_validate(result["normalized_event_plan_draft"])
    for original in result["declared_originals"]:
        EventScenarioAuthoredOriginalV1.model_validate(original)
    return result
