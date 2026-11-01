"""Full retained originals fit the bounded view without bypassing original gates."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from datetime import timedelta
from typing import Any, cast

import pytest

from packages.kernel.decision_hub_kernel.application import historical_review_material as history
from packages.kernel.decision_hub_kernel.application import perpetual_review_materials as review
from tests.decision_product._historical_review_material_budget_fixture import (
    ReaderRequest,
    SelectOnlyEvidenceReader,
    canonical,
    pad_raw_original,
    reader_materials,
    retained_inputs,
    seal_coverage,
    seal_receipt,
    sha,
)

# The existing catalog functions are unannotated; type this narrow test seam.
_catalog_api = cast(Any, review)
build_review_originals: Callable[..., dict[str, Any]] = _catalog_api.build_review_originals
read_review_originals_in_session: Callable[..., dict[str, Any]] = (
    _catalog_api.read_review_originals_in_session
)
validate_catalog: Callable[[dict[str, Any]], None] = _catalog_api.validate_catalog


def test_retained_complete_btc_originals_above_old_budget_remain_exact() -> None:
    # The old 128KiB rejection must fail this real builder roundtrip.
    inputs = retained_inputs()
    material = history.build_daily_review_material(**inputs)
    assert len(canonical(material).encode("utf8")) == 177046
    assert (
        sha(canonical(material))
        == "691c361bde8c1db5c60780641415c1f3410d886bd88764f252c2ef0456a53373"
    )
    assert material["raw_body_utf8"].encode("utf8") == inputs["raw_body"]
    assert material["raw_body_sha256"] == sha(inputs["raw_body"])
    assert material["coverage_manifest_utf8"].encode("utf8") == inputs["coverage_body"]
    assert material["coverage_manifest_ref"].rsplit("/", 1)[-1] == sha(inputs["coverage_body"])
    assert material["execution_receipt_json"] == inputs["receipt_json"]
    assert len(material["partition_originals"]) == 13
    for partition in material["partition_originals"]:
        assert partition["utf8"].encode("utf8") == inputs["partition_bodies"][partition["ref"]]
        assert partition["ref"].rsplit("/", 1)[-1] == sha(partition["utf8"])
    assert len(material["selected_rows"]) == 95
    receipt = json.loads(inputs["receipt_json"])
    assert sha(canonical(material["selected_rows"])) == receipt["normalized_rows_hash"]
    assert json.loads(inputs["evidence"].excerpt)["summary"] == json.loads(receipt["summary_json"])
    history.validate_daily_review_material(
        dict(material), inputs["evidence"], as_of=inputs["as_of"]
    )


@pytest.mark.parametrize("padding,accepted", [(19562, True), (19563, False)])
def test_complete_originals_at_new_budget_boundary(padding: int, accepted: bool) -> None:
    # Updated valid raw/capture hashes make final byte capacity the only rejection.
    inputs = retained_inputs()
    pad_raw_original(inputs, padding)
    if accepted:
        material = history.build_daily_review_material(**inputs)
        assert len(canonical(material).encode("utf8")) == 196608
        assert len(material["selected_rows"]) == 95
    else:
        with pytest.raises(ValueError, match="^historical_review_material_too_large$"):
            history.build_daily_review_material(**inputs)


@pytest.mark.parametrize(
    "corruption,code",
    [
        ("raw", "original_binding_mismatch"),
        ("coverage_hash", "coverage_hash_mismatch"),
        ("coverage_partial", "original_binding_mismatch"),
        ("coverage_gaps", "original_binding_mismatch"),
        ("partition_hash", "partition_hash_mismatch"),
        ("partition_rows", "partition_rows_mismatch"),
        ("receipt_capture", "original_binding_mismatch"),
        ("receipt_selection", "selection_mismatch"),
        ("receipt_summary", "selection_mismatch"),
        ("future_clock", "original_time_mismatch"),
        ("future_coverage", "original_time_mismatch"),
    ],
)
def test_original_integrity_and_clock_gates_remain_closed(corruption: str, code: str) -> None:
    inputs = retained_inputs()
    if corruption == "raw":
        inputs["raw_body"] += b" "
    elif corruption == "coverage_hash":
        inputs["coverage_body"] += b" "
    elif corruption in {"coverage_partial", "coverage_gaps", "future_coverage"}:
        coverage = json.loads(inputs["coverage_body"])
        if corruption == "coverage_partial":
            coverage["datasets"][0]["status"] = "partial"
        elif corruption == "coverage_gaps":
            coverage["datasets"][0]["gaps"] = ["missing-day"]
        else:
            coverage["generated_at"] = (inputs["as_of"] + timedelta(days=1)).isoformat()
        seal_coverage(inputs, coverage)
    elif corruption == "partition_hash":
        ref = next(iter(inputs["partition_bodies"]))
        inputs["partition_bodies"][ref] += b" "
    elif corruption == "partition_rows":
        ref = next(iter(inputs["partition_bodies"]))
        part = json.loads(inputs["partition_bodies"].pop(ref))
        part["rows"].pop()
        body = canonical(part).encode("utf8")
        replacement = "historical-partition://sha256/" + sha(body)
        inputs["partition_bodies"][replacement] = body
        coverage = json.loads(inputs["coverage_body"])
        refs = coverage["datasets"][0]["partition_refs"]
        refs[refs.index(ref)] = replacement
        seal_coverage(inputs, coverage)
        inputs["source_job"]["partition_refs_json"] = canonical(sorted(inputs["partition_bodies"]))
    elif corruption == "future_clock":
        inputs["as_of"] = inputs["evidence"].received_at - timedelta(microseconds=1)
    else:
        receipt = json.loads(inputs["receipt_json"])
        if corruption == "receipt_capture":
            receipt["capture_row_hash"] = "0" * 64
        elif corruption == "receipt_selection":
            receipt["normalized_rows_hash"] = "0" * 64
        else:
            receipt["summary_json"] = "{}"
        seal_receipt(inputs, receipt)
    with pytest.raises(ValueError, match="^historical_review_" + code + "$"):
        history.build_daily_review_material(**inputs)


def test_selected_rows_cannot_be_changed_after_projection() -> None:
    inputs = retained_inputs()
    material: dict[str, Any] = dict(history.build_daily_review_material(**inputs))
    material["selected_rows"] = material["selected_rows"][:-1]
    with pytest.raises(ValueError, match="^historical_review_material_changed$"):
        history.validate_daily_review_material(material, inputs["evidence"], as_of=inputs["as_of"])


def test_old_selected_summary_body_is_not_backfilled(monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = retained_inputs()
    evidence, at = inputs["evidence"], inputs["as_of"]
    request, materials = ReaderRequest(), reader_materials()
    selected = build_review_originals(request, materials, as_of=at, evidence=[evidence])
    before = canonical(selected)

    def unexpected_view(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Old selected summary must not load a newly available historical view")

    monkeypatch.setattr(history, "read_daily_review_material_in_session", unexpected_view)
    actual = read_review_originals_in_session(
        SelectOnlyEvidenceReader(evidence, at), request, materials, as_of=at, selected=selected
    )
    assert canonical(actual) == before
    row = next(row for row in actual["originals"] if row["role"] == evidence.evidence_id)
    assert history.KEY not in json.loads(row["body_json"])
    assert row["source_ref"]["hash"] == sha(canonical(evidence.model_dump(mode="json")))


def test_fresh_optional_view_falls_back_when_catalog_budget_is_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = retained_inputs()
    evidence, at = inputs["evidence"], inputs["as_of"]
    material = history.build_daily_review_material(**inputs)
    request, materials = ReaderRequest(), reader_materials()
    # Each body remains within its schema limit; only the total catalog overflows.
    for role in ("policies", "utility_policy", "settings_revision"):
        materials[role]["retained_text"] = "x" * 143334
    summary = build_review_originals(request, materials, as_of=at, evidence=[evidence])
    with pytest.raises(ValueError, match="^perpetual_review_originals_oversized$"):
        build_review_originals(
            request,
            materials,
            as_of=at,
            evidence=[evidence],
            historical_materials={evidence.evidence_id: material},
        )

    def available_view(*_args: object, **_kwargs: object) -> history.HistoricalDailyReviewMaterial:
        return material

    monkeypatch.setattr(history, "read_daily_review_material_in_session", available_view)
    actual = read_review_originals_in_session(
        SelectOnlyEvidenceReader(evidence, at), request, materials, as_of=at
    )
    assert actual == summary
    validate_catalog(actual)


def test_selected_full_view_cannot_silently_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = retained_inputs()
    evidence, at = inputs["evidence"], inputs["as_of"]
    material = history.build_daily_review_material(**inputs)
    request, materials = ReaderRequest(), reader_materials()
    selected = build_review_originals(
        request,
        materials,
        as_of=at,
        evidence=[evidence],
        historical_materials={evidence.evidence_id: material},
    )
    frozen = deepcopy(selected)

    def unavailable_view(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(history, "read_daily_review_material_in_session", unavailable_view)
    with pytest.raises(ValueError, match="^perpetual_review_original_changed_or_unavailable$"):
        read_review_originals_in_session(
            SelectOnlyEvidenceReader(evidence, at), request, materials, as_of=at, selected=selected
        )
    assert selected == frozen
