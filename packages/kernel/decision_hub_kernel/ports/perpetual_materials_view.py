"""Lossless, display-only sharing of the two FOMC stages and their coverage.

This finite protocol changes a rendering copy, never an authoritative catalog.
Consumers authenticate the actual native prompt before expanding this view.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

KEY = "fomc_originals_view"
VERSION = "perpetual-fomc-originals-view.v1"
_LOOKUP = "fomc_original_lookup"
_ERROR = "perpetual_materials_view_invalid"


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(_ERROR)
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(_ERROR)
    return value


def _row(view: dict[str, Any]) -> tuple[int, dict[str, Any], dict[str, Any]] | None:
    catalog = _object(view.get("review_originals"))
    rows = catalog["originals"]
    if not isinstance(rows, list):
        raise ValueError(_ERROR)
    matches = [(i, _object(row)) for i, row in enumerate(rows)
               if _object(row).get("source_kind") == "server_material"
               and row.get("role") == "event_agenda"]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError(_ERROR)
    index, row = matches[0]
    wrapper = _object(json.loads(_text(row["body_json"])))
    if (
        set(wrapper) != {"schema_version", "parent_ref", "child_path", "agenda"}
        or wrapper["schema_version"] != "perpetual-event-agenda-original.v1"
        or wrapper["child_path"] != "/event_agenda"
        or wrapper["parent_ref"] != view["event_agenda_ref"]
        or row["original_ref"] != wrapper["parent_ref"]
        or row["body_json"] != _canonical(wrapper)
    ):
        raise ValueError(_ERROR)
    return index, row, wrapper


def _slots(agenda: dict[str, Any]) -> tuple[list[int], int] | None:
    entries, coverage = agenda["entries"], agenda["coverage"]
    if not isinstance(entries, list) or not isinstance(coverage, list):
        raise ValueError(_ERROR)
    stages = [i for i, entry in enumerate(entries) if _object(entry).get("family") == "fomc"]
    covers = [i for i, item in enumerate(coverage) if _object(item).get("family") == "fomc"]
    if (
        len(stages) != 2 or len(covers) != 1
        or {entries[i].get("stage_id") for i in stages}
        != {"policy_statement", "press_conference"}
    ):
        return None
    return stages, covers[0]


def _candidate(
    agenda: dict[str, Any],
) -> tuple[list[int], int, str, str, dict[str, Any]] | None:
    slots = _slots(agenda)
    if slots is None:
        return None
    stages, coverage_index = slots
    first, second = (agenda["entries"][i] for i in stages)
    text = first["original_json"]
    if not isinstance(text, str) or text != second["original_json"]:
        return None
    source = dict(ref="fomc-stage-original:" + _hash(text), hash=_hash(text))
    if first["source_ref"] != source or second["source_ref"] != source:
        return None
    body = _object(json.loads(text))
    coverage = agenda["coverage"][coverage_index]["original_json"]
    if (
        not isinstance(coverage, str) or text != _canonical(body)
        or "calendar_coverage" not in body or coverage != _canonical(body["calendar_coverage"])
    ):
        return None
    return stages, coverage_index, text, coverage, body


def _binding(view: dict[str, Any], index: int, row: dict[str, Any]) -> dict[str, Any]:
    catalog = view["review_originals"]
    return dict(
        schema_version=VERSION, run_id=catalog["run_id"], request_hash=catalog["request_hash"],
        admission_ref=view["admission_ref"], task_binding_ref=view["task_binding_ref"],
        parent_ref=row["original_ref"], source_ref=row["source_ref"], catalog_index=index,
    )


def encode_materials_view(view: dict[str, Any]) -> dict[str, Any]:
    """Share only the proven identical canonical FOMC originals, or keep legacy bytes."""
    if KEY in view:
        raise ValueError(_ERROR)
    if "review_originals" not in view:
        return view
    match = _row(view)
    if match is None:
        return view
    index, row, wrapper = match
    candidate = _candidate(_object(wrapper["agenda"]))
    if candidate is None:
        return view
    if _hash(row["body_json"]) != row["source_ref"]["hash"]:
        raise ValueError(_ERROR)
    stages, coverage_index, stage, coverage, body = candidate
    rendered = deepcopy(view)
    agenda = wrapper["agenda"]
    body["calendar_coverage"] = {_LOOKUP: "coverage"}
    originals = dict(
        stage=dict(hash=_hash(stage), body=body),
        coverage=dict(hash=_hash(coverage), original_json=coverage),
    )
    for slot in stages:
        agenda["entries"][slot]["original_json"] = {_LOOKUP: "stage"}
    agenda["coverage"][coverage_index]["original_json"] = {_LOOKUP: "coverage"}
    rendered_row = rendered["review_originals"]["originals"][index]
    rendered_row["body_json"] = _canonical(wrapper)
    rendered[KEY] = dict(_binding(rendered, index, rendered_row), originals=originals)
    return rendered


def _expand_originals(protocol: dict[str, Any]) -> tuple[str, str]:
    originals = _object(protocol["originals"])
    if set(originals) != {"stage", "coverage"}:
        raise ValueError(_ERROR)
    stage, coverage = _object(originals["stage"]), _object(originals["coverage"])
    if set(stage) != {"hash", "body"} or set(coverage) != {"hash", "original_json"}:
        raise ValueError(_ERROR)
    coverage_text = _text(coverage["original_json"])
    coverage_body = _object(json.loads(coverage_text))
    if _hash(coverage_text) != coverage["hash"] or _canonical(coverage_body) != coverage_text:
        raise ValueError(_ERROR)
    body = deepcopy(_object(stage["body"]))
    if body.get("calendar_coverage") != {_LOOKUP: "coverage"}:
        raise ValueError(_ERROR)
    body["calendar_coverage"] = coverage_body
    stage_text = _canonical(body)
    if _hash(stage_text) != stage["hash"]:
        raise ValueError(_ERROR)
    return stage_text, coverage_text


def _expand(view: dict[str, Any], run_id: str | None) -> dict[str, Any]:
    protocol = _object(view[KEY])
    if protocol["schema_version"] != VERSION or type(protocol["catalog_index"]) is not int:
        raise ValueError(_ERROR)
    match = _row(view)
    if match is None:
        raise ValueError(_ERROR)
    index, row, wrapper = match
    if (
        {key: value for key, value in protocol.items() if key != "originals"}
        != _binding(view, index, row)
        or run_id is not None and protocol["run_id"] != run_id
    ):
        raise ValueError(_ERROR)
    stage, coverage = _expand_originals(protocol)
    agenda = _object(wrapper["agenda"])
    slots = _slots(agenda)
    if slots is None:
        raise ValueError(_ERROR)
    stages, coverage_index = slots
    for slot in stages:
        if agenda["entries"][slot]["original_json"] != {_LOOKUP: "stage"}:
            raise ValueError(_ERROR)
        agenda["entries"][slot]["original_json"] = stage
    if agenda["coverage"][coverage_index]["original_json"] != {_LOOKUP: "coverage"}:
        raise ValueError(_ERROR)
    agenda["coverage"][coverage_index]["original_json"] = coverage
    expanded = deepcopy(view)
    del expanded[KEY]
    expanded["review_originals"]["originals"][index]["body_json"] = _canonical(wrapper)
    # Exact re-encoding rejects unused/foreign entries and altered identities.
    # Expansion is fixed at two stages and one coverage; there is no recursive resolver.
    if _canonical(encode_materials_view(expanded)) != _canonical(view):
        raise ValueError(_ERROR)
    return expanded


def expand_materials_view(
    view: dict[str, Any], *, run_id: str | None = None,
) -> dict[str, Any]:
    """Restore original catalog bytes after native-input provenance checks."""
    if KEY not in view:
        return view
    try:
        return _expand(view, run_id)
    except (KeyError, TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise ValueError(_ERROR) from exc
