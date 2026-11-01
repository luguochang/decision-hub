"""Lossless model-view baseline references; reconstruction confers no authority.

The caller must independently authenticate the original delivery. The runtime
mapper uses reconstruction only as a candidate for subsequent Kernel checks.
"""

from __future__ import annotations

from typing import Any

from .provider_input_receipts import sha, tool_arguments_json

PROJECTION_SCHEMA = "research-baseline-delivery-projection.v1"
SOURCE_SCHEMA = "research-baseline-delivery.v1"


def project_fact_attributes(facts: Any) -> dict[str, Any]:
    """Pool only identical, repeated large attributes; retain full bodies inline."""
    _objects(facts, "fact_id")
    counts: dict[str, int] = {}
    bodies: dict[str, Any] = {}
    for fact in facts:
        if "attributes" not in fact or "attributes_ref" in fact:
            raise ValueError("research_fact_projection_source_invalid")
        encoded = tool_arguments_json(fact["attributes"])
        if len(encoded.encode("utf-8")) >= 1024:
            digest = sha(encoded)
            if digest in bodies and tool_arguments_json(bodies[digest]) != encoded:
                raise ValueError("research_fact_projection_hash_collision")
            counts[digest] = counts.get(digest, 0) + 1
            bodies[digest] = fact["attributes"]
    pool = {key: bodies[key] for key in sorted(counts) if counts[key] >= 2}
    projected = []
    for fact in facts:
        digest = sha(tool_arguments_json(fact["attributes"]))
        projected.append(
            {
                **{key: value for key, value in fact.items() if key != "attributes"},
                "attributes_ref": {"body_hash": digest},
            }
            if digest in pool
            else fact
        )
    return {
        "facts": projected,
        "shared_fact_attributes": [{"body_hash": key, "body": body} for key, body in pool.items()],
    }


def restore_fact_attributes(facts: Any, shared_fact_attributes: Any) -> list[dict[str, Any]]:
    """Strict actual-inline reconstruction only; caller still reads source originals."""
    _objects(facts, "fact_id")
    if not isinstance(shared_fact_attributes, list):
        raise ValueError("research_fact_projection_pool_invalid")
    pool: dict[str, Any] = {}
    for item in shared_fact_attributes:
        if (
            not isinstance(item, dict)
            or set(item) != {"body_hash", "body"}
            or not isinstance(item["body_hash"], str)
            or item["body_hash"] in pool
            or sha(tool_arguments_json(item["body"])) != item["body_hash"]
        ):
            raise ValueError("research_fact_projection_pool_invalid")
        pool[item["body_hash"]] = item["body"]
    restored = []
    for fact in facts:
        if "attributes_ref" not in fact:
            if "attributes" not in fact:
                raise ValueError("research_fact_projection_reference_invalid")
            restored.append(fact)
            continue
        ref = fact["attributes_ref"]
        if (
            "attributes" in fact
            or not isinstance(ref, dict)
            or set(ref) != {"body_hash"}
            or not isinstance(ref["body_hash"], str)
            or ref["body_hash"] not in pool
        ):
            raise ValueError("research_fact_projection_reference_invalid")
        restored.append(
            {
                **{key: value for key, value in fact.items() if key != "attributes_ref"},
                "attributes": pool[ref["body_hash"]],
            }
        )
    expected = project_fact_attributes(restored)
    actual = {"facts": facts, "shared_fact_attributes": shared_fact_attributes}
    if tool_arguments_json(expected) != tool_arguments_json(actual):
        raise ValueError("research_fact_projection_noncanonical")
    return restored


def _objects(values: Any, key: str) -> dict[str, dict[str, Any]]:
    if not isinstance(values, list):
        raise ValueError("research_baseline_projection_material_invalid")
    result = {}
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get(key), str) or not value[key]:
            raise ValueError("research_baseline_projection_material_invalid")
        if value[key] in result:
            raise ValueError("research_baseline_projection_material_ambiguous")
        tool_arguments_json(value)  # Reject nonfinite/unsafe wire values too.
        result[value[key]] = value
    return result


def _refs(values: Any, objects: dict[str, dict[str, Any]], key: str) -> list[dict[str, str]]:
    if not isinstance(values, list):
        raise ValueError("research_baseline_projection_array_invalid")
    refs = []
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get(key), str):
            raise ValueError("research_baseline_projection_reference_invalid")
        actual = objects.get(value[key])
        if actual is None or tool_arguments_json(actual) != tool_arguments_json(value):
            raise ValueError("research_baseline_projection_material_mismatch")
        refs.append({key: value[key], "body_hash": sha(tool_arguments_json(value))})
    return refs


def project_baseline_delivery(
    delivery: dict[str, Any],
    *,
    facts: Any,
    evidence_candidates: Any,
) -> dict[str, Any]:
    """Project a complete independently-read delivery against actual top bodies."""
    if delivery.get("schema_version") != SOURCE_SCHEMA or not isinstance(
        delivery.get("results"), list
    ):
        raise ValueError("research_baseline_projection_source_invalid")
    fact_map = _objects(facts, "fact_id")
    evidence_map = _objects(evidence_candidates, "evidence_id")
    results = []
    for result in delivery["results"]:
        if not isinstance(result, dict) or not {"facts", "evidence_candidates"} <= result.keys():
            raise ValueError("research_baseline_projection_source_invalid")
        results.append(
            {
                **{
                    key: value
                    for key, value in result.items()
                    if key not in {"facts", "evidence_candidates"}
                },
                "fact_refs": None
                if result["facts"] is None
                else _refs(result["facts"], fact_map, "fact_id"),
                "evidence_refs": _refs(result["evidence_candidates"], evidence_map, "evidence_id"),
            }
        )
    return {
        **{
            key: value
            for key, value in delivery.items()
            if key not in {"schema_version", "results"}
        },
        "schema_version": PROJECTION_SCHEMA,
        "source_schema_version": SOURCE_SCHEMA,
        "results": results,
    }


def _resolve(refs: Any, objects: dict[str, dict[str, Any]], key: str) -> list[dict[str, Any]]:
    if not isinstance(refs, list):
        raise ValueError("research_baseline_projection_reference_invalid")
    values = []
    for ref in refs:
        if (
            not isinstance(ref, dict)
            or set(ref) != {key, "body_hash"}
            or not isinstance(ref[key], str)
            or not isinstance(ref["body_hash"], str)
        ):
            raise ValueError("research_baseline_projection_reference_invalid")
        value = objects.get(ref[key])
        if value is None or sha(tool_arguments_json(value)) != ref["body_hash"]:
            raise ValueError("research_baseline_projection_material_mismatch")
        values.append(value)
    return values


def restore_baseline_projection(
    projection: Any,
    *,
    facts: Any,
    evidence_candidates: Any,
) -> dict[str, Any]:
    """Resolve candidate wire only; this is not an original-reader substitute."""
    if (
        not isinstance(projection, dict)
        or projection.get("schema_version") != PROJECTION_SCHEMA
        or projection.get("source_schema_version") != SOURCE_SCHEMA
        or not isinstance(projection.get("results"), list)
    ):
        raise ValueError("research_baseline_projection_source_invalid")
    fact_map = _objects(facts, "fact_id")
    evidence_map = _objects(evidence_candidates, "evidence_id")
    results = []
    for result in projection["results"]:
        if (
            not isinstance(result, dict)
            or not {"fact_refs", "evidence_refs"} <= result.keys()
            or "facts" in result
            or "evidence_candidates" in result
        ):
            raise ValueError("research_baseline_projection_reference_invalid")
        results.append(
            {
                **{
                    key: value
                    for key, value in result.items()
                    if key not in {"fact_refs", "evidence_refs"}
                },
                "facts": None
                if result["fact_refs"] is None
                else _resolve(result["fact_refs"], fact_map, "fact_id"),
                "evidence_candidates": _resolve(
                    result["evidence_refs"], evidence_map, "evidence_id"
                ),
            }
        )
    return {
        **{
            key: value
            for key, value in projection.items()
            if key not in {"schema_version", "source_schema_version", "results"}
        },
        "schema_version": SOURCE_SCHEMA,
        "results": results,
    }
