"""The admitted calculator source is reviewable without changing its code hash."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from packages.kernel.decision_hub_kernel.application import perpetual_research_binding as binding
from packages.kernel.decision_hub_kernel.application.perpetual_review_materials import (
    select_review_originals,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from packages.runtime_adapters.dsh_runtime.perpetual_profile import _root_material_view
from packages.runtime_adapters.dsh_runtime.profile import build_trusted_web_research_prompt
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_native_graph import packet_for
from tests.decision_product.test_perpetual_research_binding import (
    binding_case as binding_case,
)
from tests.decision_product.test_perpetual_research_binding import factory_for, freeze

DEPENDENCIES = (
    "decision/action_comparison.py",
    "decision/perpetual_accounting.py",
    "decision/perpetual_publication.py",
    "application/perpetual_cost_assumptions.py",
    "ports/exact_decimal_text.py",
)


def test_frozen_exact_method_sources_reach_selected_reviewer_material(
    binding_case, tmp_path, record_property
):
    factory, service = factory_for(binding_case, tmp_path)
    request = factory.build(binding_case["event_id"], binding_case["run_id"])
    with binding_case["db"].session() as session:
        original = service.read_admission_in_session(session, request.run_id)
        materials = binding.read_perpetual_research_materials_in_session(session, request)
    method = original["comparison_method"]
    bundle = method["source_bundle"]
    assert bundle["schema_version"] == "perpetual-method-source-bundle.v1"
    assert bundle["content_kind"] == "immutable_code_data"
    assert set(bundle["files"]) == set(DEPENDENCIES)
    kernel = Path(binding.__file__).resolve().parents[1]
    expected = {}
    for name in DEPENDENCIES:
        raw = (kernel / name).read_bytes()
        expected[name] = hashlib.sha256(raw).hexdigest()
        assert bundle["files"][name] == {
            "sha256": expected[name],
            "source_text": raw.decode("utf-8"),
        }
    assert method["dependencies"] == expected
    assert method["method_code_hash"] == canonical_hash(expected) == original["method_code_hash"]
    assert method["qualification"] == "research_trial"
    assert method["financial_qualified"] is False
    selected = select_review_originals(
        packet_for(request, materials)["synthesis"], materials["review_originals"]
    )
    row = next(item for item in selected["originals"] if item["role"] == "comparison_method")
    assert json.loads(row["body_json"]) == method
    assert row["original_ref"] == original["method_ref"]
    assert row["source_ref"]["hash"] == canonical_hash(method)
    assert len(binding.canonical(selected).encode()) <= 384_000
    # The existing root material consumer must accept the admitted source data.
    original_materials = binding.canonical(materials)
    prompt = build_trusted_web_research_prompt(request, perpetual_materials=materials)
    rendered = json.loads(
        prompt.split("BEGIN_FROZEN_PERPETUAL_MATERIALS_JSON\n")[1].split(
            "\nEND_FROZEN_PERPETUAL_MATERIALS_JSON"
        )[0]
    )
    view = rendered["comparison_method"]
    assert view["display_only"] is True
    assert "source_bundle" not in view
    lookup = view["source_bundle_lookup"]
    index = int(lookup["catalog_pointer"].split("/")[3])
    rendered_original = rendered["review_originals"]["originals"][index]
    assert lookup["original_ref"] == original["method_ref"]
    assert lookup["source_ref"] == rendered_original["source_ref"]
    assert json.loads(rendered_original["body_json"]) == method
    assert binding.canonical(materials) == original_materials
    assert len(binding.canonical(rendered).encode()) <= 256_000
    record_property(
        "method_review_evidence_sizes",
        json.dumps(
            dict(
                exact_source_utf8=sum(len((kernel / name).read_bytes()) for name in DEPENDENCIES),
                raw_materials_utf8=len(original_materials.encode()),
                rendered_root_materials_utf8=len(binding.canonical(rendered).encode()),
                selected_review_catalog_utf8=len(binding.canonical(selected).encode()),
                root_prompt_utf8=len(prompt.encode()),
                method_code_hash=method["method_code_hash"],
            )
        ),
    )


def test_legacy_metadata_only_admission_reopens_unchanged(binding_case, monkeypatch):
    current = binding.comparison_method_original

    def legacy_method(*args, **kwargs):
        method, _reference, code_hash = current(*args, **kwargs)
        method.pop("source_bundle", None)
        return (
            method,
            {
                "ref": "decision-method:perpetual-assumed-unit-mean-net.v1",
                "hash": canonical_hash(method),
            },
            code_hash,
        )

    with monkeypatch.context() as patch:
        patch.setattr(binding, "comparison_method_original", legacy_method)
        service, reference = freeze(binding_case)
    with binding_case["db"].session() as session:
        before = session.connection().exec_driver_sql("SELECT total_changes()").scalar_one()
        original = service.read_admission_in_session(session, binding_case["run_id"])
        assert "source_bundle" not in original["comparison_method"]
        assert binding._read(session, binding_case["run_id"], binding.ADMISSION_ROLE)[
            1
        ] == reference.model_dump(mode="json")
        assert session.connection().exec_driver_sql("SELECT total_changes()").scalar_one() == before


def test_resealed_changed_source_bundle_cannot_reopen_as_same_method(binding_case):
    service, _ = freeze(binding_case)
    with binding_case["db"].session() as session:
        row = binding._row(session, binding_case["run_id"], binding.ADMISSION_ROLE)
        original = json.loads(row.payload_json)
        original["comparison_method"]["source_bundle"]["files"][DEPENDENCIES[0]]["source_text"] += (
            "\n# substituted code\n"
        )
        row.payload_json = binding.canonical(original)
        row.payload_hash = canonical_hash(original)
    with binding_case["db"].session() as session:
        before = session.connection().exec_driver_sql("SELECT total_changes()").scalar_one()
        with pytest.raises(ValueError, match="source_or_method_changed"):
            service.read_admission_in_session(session, binding_case["run_id"])
        assert session.connection().exec_driver_sql("SELECT total_changes()").scalar_one() == before


def test_root_original_lookups_preserve_catalog_and_reject_changed_agenda(binding_case, tmp_path):
    factory, _service = factory_for(binding_case, tmp_path)
    request = factory.build(binding_case["event_id"], binding_case["run_id"])
    with binding_case["db"].session() as session:
        materials = binding.read_perpetual_research_materials_in_session(session, request)
    before = binding.canonical(materials)
    rendered = _root_material_view(materials)
    assert rendered["review_originals"] == materials["review_originals"]
    assert binding.canonical(materials) == before
    for role in ("close_window", "event_agenda"):
        lookup = rendered[role]["original_lookup"]
        index = int(lookup["catalog_pointer"].split("/")[3])
        original = rendered["review_originals"]["originals"][index]
        assert lookup["original_ref"] == original["original_ref"]
        assert lookup["source_ref"] == original["source_ref"]
        body = json.loads(original["body_json"])
        if role == "event_agenda":
            assert body["parent_ref"] == materials["event_agenda_ref"]
            body = body["agenda"]
        assert body == materials[role]
    for role in ("cost_policy", "cost_assumptions", "cost_policy_ref", "account_snapshot"):
        assert rendered[role] == materials[role]
    for changed_field in ("agenda_body", "parent_ref", "original_ref"):
        changed = deepcopy(materials)
        row = next(
            item
            for item in changed["review_originals"]["originals"]
            if item["source_kind"] == "server_material" and item["role"] == "event_agenda"
        )
        wrapped = json.loads(row["body_json"])
        if changed_field == "agenda_body":
            wrapped["agenda"]["payload_hash"] = "f" * 64
        elif changed_field == "parent_ref":
            wrapped["parent_ref"] = {"ref": "test_foreign_parent", "hash": "f" * 64}
        else:
            row["original_ref"] = {"ref": "test_foreign_parent", "hash": "f" * 64}
        row["body_json"] = binding.canonical(wrapped)
        with pytest.raises(ValueError, match="perpetual_material_prompt_original_mismatch"):
            _root_material_view(changed)


def test_root_paper_ledger_freshness_instruction_preserves_frozen_and_legacy_data():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from packages.runtime_adapters.dsh_runtime.perpetual_profile import build_perpetual_prompt

    request = SimpleNamespace(
        deadline_at=datetime(2026, 10, 20, 17, tzinfo=UTC),
        execution_budget=SimpleNamespace(max_tool_calls=40, max_subagents=4),
    )
    account = dict(source="paper_policy", as_of="2026-10-19T01:26:24.141956Z", equity="2500")
    active = dict(
        input_policy="saved_preferences_current_paper_ledger.v1",
        account_snapshot=account,
        position_context=dict(state="flat", signed_contracts="0"),
        input_binding=dict(observed_at=account["as_of"], prepared_at="2026-10-20T16:08:41.984745Z"),
    )
    legacy = dict(account_snapshot=account, position_context=dict(state="unknown"))
    for materials in (active, legacy):
        before = binding.canonical(materials)
        prompt = build_perpetual_prompt(request, "{}", "[]", materials)
        assert "saved_preferences_current_paper_ledger.v1" in prompt
        assert "creation/last change" in prompt and "prepared_at" in prompt
        assert "non-null verified balance or position" in prompt
        assert "Legacy v1/v2 declared snapshots" in prompt and "historical readback" in prompt
        assert "not exchange mark-to-market" in prompt
        rendered = prompt.split("BEGIN_FROZEN_PERPETUAL_MATERIALS_JSON\n")[1].split(
            "\nEND_FROZEN_PERPETUAL_MATERIALS_JSON"
        )[0]
        assert rendered == before == binding.canonical(materials)

