"""Synthetic lossless FOMC display fixtures; no private artifacts or services."""

import copy
import hashlib
import importlib
import json

import pytest

from packages.runtime_adapters.dsh_runtime.perpetual_profile import _root_material_view
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case

MODULE = "packages.kernel.decision_hub_kernel.ports.perpetual_materials_view"
KEY = "fomc_originals_view"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def synthetic_materials():
    coverage = {"notice": "Engineering fixture 原件" * 500, "events": []}
    stage = canonical({"calendar_coverage": coverage, "policy_original": "x" * 6000})
    source_ref = dict(ref="fomc-stage-original:" + sha(stage), hash=sha(stage))
    entries = [
        dict(family="fomc", stage_id=stage_id, original_json=stage,
             source_ref=source_ref, event_key=stage_id, scheduled_at=scheduled_at)
        for stage_id, scheduled_at in (
            ("policy_statement", "2026-10-29T18:00:00Z"),
            ("press_conference", "2026-10-29T18:30:00Z"),
        )
    ]
    agenda = dict(entries=entries, coverage=[
        dict(family="cpi", original_json='{"unrelated":true}'),
        dict(family="fomc", original_json=canonical(coverage),
             coverage_ref=dict(ref="coverage", hash=sha(canonical(coverage)))),
    ])
    parent = dict(ref="agenda-parent", hash="a" * 64)
    wrapper = dict(schema_version="perpetual-event-agenda-original.v1",
                   parent_ref=parent, child_path="/event_agenda", agenda=agenda)
    method = dict(source_bundle={"code": "fixture"})
    method_ref = dict(ref="method", hash=sha(canonical(method)))
    rows = [
        dict(role="comparison_method", source_kind="server_material", original_ref=method_ref,
             source_ref=method_ref, body_json=canonical(method)),
        dict(role="event_agenda", source_kind="server_material", original_ref=parent,
             source_ref=dict(ref="review-original:server_material:event_agenda",
                             hash=sha(canonical(wrapper))), body_json=canonical(wrapper)),
    ]
    return dict(event_agenda=agenda, event_agenda_ref=parent, comparison_method=method,
                method_ref=method_ref, admission_ref=dict(ref="admission", hash="b" * 64),
                task_binding_ref=dict(ref="task", hash="c" * 64), context={"horizon": "4h"},
                review_originals=dict(run_id="run-fixture", request_hash="d" * 64, originals=rows))


def old_view(materials):
    view = copy.deepcopy(materials)
    view["comparison_method"] = dict(display_only=True, source_bundle_lookup=dict(
        original_ref=materials["method_ref"], source_ref=materials["method_ref"],
        catalog_pointer="/review_originals/originals/0/body_json", field="source_bundle"))
    view["event_agenda"] = dict(display_only=True, original_lookup=dict(
        original_ref=materials["event_agenda_ref"],
        source_ref=materials["review_originals"]["originals"][1]["source_ref"],
        catalog_pointer="/review_originals/originals/1/body_json", field="agenda"))
    return view


def test_renderer_shares_readable_fomc_originals_and_recovers_every_old_byte():
    materials = synthetic_materials()
    original = canonical(materials)
    expected = old_view(materials)
    view = _root_material_view(materials)
    assert len(canonical(view).encode()) < len(canonical(expected).encode()) - 20000
    assert KEY in view
    expanded = importlib.import_module(MODULE).expand_materials_view(view, run_id="run-fixture")
    assert canonical(expanded) == canonical(expected)
    assert canonical(materials) == original
    assert "Engineering fixture 原件" in canonical(view)


@pytest.mark.parametrize("fault", [
    "missing", "hash", "foreign_run", "foreign_parent", "version", "path", "cycle",
    "extra_table", "extra_wrapper", "source_ref", "marker", "stage_order", "expansion",
])
def test_decoder_rejects_tampered_or_unbounded_display_protocol(fault):
    api = importlib.import_module(MODULE)
    view = _root_material_view(synthetic_materials())
    protocol = view[KEY]
    wrapper = json.loads(view["review_originals"]["originals"][1]["body_json"])
    if fault == "missing":
        del protocol["originals"]["coverage"]
    elif fault == "hash":
        protocol["originals"]["stage"]["hash"] = "0" * 64
    elif fault == "foreign_run":
        protocol["run_id"] = "run-other"
    elif fault == "foreign_parent":
        protocol["parent_ref"]["ref"] = "other-parent"
    elif fault == "version":
        protocol["schema_version"] = "perpetual-fomc-originals-view.v999"
    elif fault in {"path", "cycle", "marker"}:
        lookup = {"path": "../../outside", "cycle": "stage", "marker": "foreign"}[fault]
        protocol["originals"]["stage"]["body"]["calendar_coverage"] = {
            "fomc_original_lookup": lookup
        }
    elif fault == "extra_table":
        protocol["originals"]["unused"] = protocol["originals"]["stage"]
    elif fault == "extra_wrapper":
        protocol["ignored"] = True
    elif fault == "source_ref":
        wrapper["agenda"]["entries"][0]["source_ref"]["ref"] = "untrusted-source"
    elif fault == "stage_order":
        wrapper["agenda"]["entries"][0]["original_json"] = {"fomc_original_lookup": "coverage"}
    elif fault == "expansion":
        protocol["originals"]["coverage"]["original_json"] = canonical({
            "fomc_original_lookup": "stage"
        })
    view["review_originals"]["originals"][1]["body_json"] = canonical(wrapper)
    with pytest.raises(ValueError, match="perpetual_materials_view_invalid"):
        api.expand_materials_view(view, run_id="run-fixture")


@pytest.mark.parametrize("change", ["vintage", "body", "noncanonical", "other_family"])
def test_ineligible_originals_keep_legacy_view_without_sharing(change):
    materials = synthetic_materials()
    agenda = materials["event_agenda"]
    entry = agenda["entries"][1]
    if change == "vintage":
        entry["source_ref"] = dict(ref="other-vintage", hash=entry["source_ref"]["hash"])
    elif change == "body":
        entry["original_json"] += " "
    elif change == "noncanonical":
        for item in agenda["entries"]:
            item["original_json"] = json.dumps(json.loads(item["original_json"]))
    else:
        for item in agenda["entries"]:
            item["family"] = "cpi"
    row = materials["review_originals"]["originals"][1]
    wrapper = json.loads(row["body_json"])
    wrapper["agenda"] = agenda
    row["body_json"] = canonical(wrapper)
    row["source_ref"]["hash"] = sha(row["body_json"])
    assert canonical(_root_material_view(materials)) == canonical(old_view(materials))


def test_legacy_decoder_is_an_exact_noop_even_with_marker_text_in_source():
    api = importlib.import_module(MODULE)
    view = old_view(synthetic_materials())
    view["source_text"] = '{"fomc_original_lookup":"stage"}'
    assert api.expand_materials_view(view, run_id="run-fixture") is view


@pytest.mark.parametrize("index", [True, 1.0], ids=["boolean", "float"])
def test_decoder_rejects_non_integer_catalog_index(index):
    api = importlib.import_module(MODULE)
    view = _root_material_view(synthetic_materials())
    view[KEY]["catalog_index"] = index
    with pytest.raises(ValueError, match="perpetual_materials_view_invalid"):
        api.expand_materials_view(view, run_id="run-fixture")


def _synthetic_agenda_for(materials):
    from tests.decision_product.test_perpetual_research_binding import AT

    agenda = copy.deepcopy(materials["event_agenda"])
    source = synthetic_materials()["event_agenda"]
    agenda["entries"] = [dict(
        entry, event_id=entry["event_key"], scheduled_date=None, time_precision="exact",
        source_timezone="America/New_York", known_at=AT.isoformat().replace("+00:00", "Z"),
        occurrence_ref=None, identity_basis="synthetic-fixture", overlap_group=None,
        dependency_refs=[], dependency_status="unknown", materiality_reason="engineering only",
        next_check_at=None, raw_capture_refs=[], source_id="fomc-fixture",
    ) for entry in source["entries"]]
    agenda["coverage"] = [dict(
        item, source_id="synthetic-fixture", status="available", reason_codes=[],
        coverage_ref=item.get("coverage_ref"), known_at=None,
    ) for item in source["coverage"]]
    agenda["payload_hash"] = sha(canonical({
        key: value for key, value in agenda.items() if key != "payload_hash"
    }))
    return agenda


@pytest.fixture
def saved_materials_case(binding_case, tmp_path):
    from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
        read_perpetual_research_materials_in_session,
    )
    from tests.decision_product.test_perpetual_research_binding import factory_for

    factory, _ = factory_for(binding_case, tmp_path)
    request = factory.build(binding_case["event_id"], binding_case["run_id"])
    with binding_case["db"].session() as session:
        materials = read_perpetual_research_materials_in_session(session, request)
    materials["event_agenda"] = _synthetic_agenda_for(materials)
    row = next(row for row in materials["review_originals"]["originals"]
               if row["source_kind"] == "server_material" and row["role"] == "event_agenda")
    wrapper = json.loads(row["body_json"])
    wrapper["agenda"] = materials["event_agenda"]
    row["body_json"] = canonical(wrapper)
    row["source_ref"]["hash"] = sha(row["body_json"])
    return binding_case["db"], request, materials


def _consumer_prompt(view):
    return "\n".join([
        "BEGIN_FROZEN_PERPETUAL_MATERIALS_JSON", canonical(view),
        "END_FROZEN_PERPETUAL_MATERIALS_JSON",
    ])


def test_saved_identity_consumer_restores_original_catalog_and_accepts_legacy(saved_materials_case):
    from packages.kernel.decision_hub_kernel.application.perpetual_experience_use import (
        _frozen_materials,
    )

    db, request, materials = saved_materials_case
    view = _root_material_view(materials)
    with db.session() as session:
        restored = _frozen_materials(
            session, request, _consumer_prompt(view), materials["original_task_hash"]
        )
        legacy = _frozen_materials(
            session, request, _consumer_prompt(restored), materials["original_task_hash"]
        )
    assert restored["review_originals"] == materials["review_originals"]
    assert legacy == restored


@pytest.mark.parametrize("fault", ["admission", "task", "context", "original_task", "run", "body"])
def test_saved_identity_consumer_rejects_foreign_binding_and_catalog_tamper(
    saved_materials_case, fault,
):
    from packages.kernel.decision_hub_kernel.application.perpetual_experience_use import (
        _frozen_materials,
    )

    db, request, materials = saved_materials_case
    view = _root_material_view(materials)
    task_hash = materials["original_task_hash"]
    error = "perpetual_experience_materials_task_mismatch"
    if fault in {"admission", "task"}:
        view[fault + ("_binding_ref" if fault == "task" else "_ref")]["ref"] = "foreign"
        view[KEY]["schema_version"] = "invalid-version-must-not-be-expanded-first"
    elif fault == "context":
        view["context"]["horizon"] = "72h"
    elif fault == "original_task":
        task_hash = "0" * 64
    elif fault == "run":
        view["review_originals"]["run_id"] = "run-foreign"
        view[KEY]["run_id"] = "run-foreign"
        error = "perpetual_materials_view_invalid"
    else:
        row = next(row for row in view["review_originals"]["originals"]
                   if row["role"] == "cost_policy")
        row["body_json"] = canonical({"tampered": True})
        error = "perpetual_review_original_body_mismatch"
    with db.session() as session, pytest.raises(ValueError, match=error):
        _frozen_materials(session, request, _consumer_prompt(view), task_hash)
