import json
import shutil
from datetime import timedelta

import pytest
import yaml

from apps.hub_worker.research import ResearchRequestFactory
from packages.kernel.decision_hub_kernel.application.research_pack import ROLE
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
from packages.query_views.research import ResearchQueryService
from tests.evolution.test_research_worker import NOW, PACK_ROOT, _admitted_run
from tests.research.query_basis_helpers import save_control_request


def test_saved_request_budget_and_pack_version_survive_current_configuration_changes(tmp_path):
    db, event, run = _admitted_run(tmp_path)
    request = ResearchRequestFactory(db, pack_root=PACK_ROOT, clock=lambda: NOW).build(event, run)
    # Request overrides (e.g. historical review timeouts) take precedence over the
    # declared Pack budget. Today's Pack must not supply either value.
    request = request.model_copy(update={"execution_budget": request.execution_budget.model_copy(
        update={"per_model_step_timeout_seconds": 240, "max_tool_calls": 17}
    )})
    save_control_request(db, request, NOW)
    initial = ResearchQueryService(db, pack_root=PACK_ROOT)
    expected = initial.observability_view(run).versions
    root = tmp_path / "pack"
    shutil.copytree(PACK_ROOT, root)
    path = root / "pack.yaml"
    body = yaml.safe_load(path.read_text(encoding="utf-8"))
    body["version"] = "9.0.0"
    body["execution_budget"]["max_tool_calls"] = 77
    body["gate_policy_ref"] = "new-gate.v9"
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    query = ResearchQueryService(db, pack_root=root)
    assert query.get(run).run.budget == request.execution_budget
    assert query.list()[0].budget == request.execution_budget
    assert query.get(run).run.budget_basis == "saved_request"
    versions = query.observability_view(run).versions
    assert versions == expected
    assert versions.domain_pack_version == "1.0.0"
    assert versions.pack_basis == "saved_pack"
    assert versions.role_profile_ref == request.role_profile_ref
    assert versions.pack_receipt_ref
    assert versions.capability_versions == [] and versions.source_registry_ref is None
    # A frozen manifest path identifies a declaration. It does not attest that
    # the referenced policy was loaded or that its rules ran for this Run.
    assert versions.gate_policy_ref == expected.gate_policy_ref
    assert set(versions.unverified_fields) == {
        "gate_policy_ref", "capability_versions", "source_registry_ref",
    }
    assert query.inbox().items[0].domain_pack_ref == request.domain_pack_ref


@pytest.mark.parametrize("damage", ["missing", "hash", "late", "domain"])
def test_missing_or_invalid_historical_configuration_never_uses_current_pack(tmp_path, damage):
    db, event, run = _admitted_run(tmp_path)
    if damage != "missing":
        factory = ResearchRequestFactory(db, pack_root=PACK_ROOT, clock=lambda: NOW)
        request = factory.build(event, run)
        if damage == "domain":
            request = request.model_copy(update={"domain_pack_ref": "other.v1"})
        save_control_request(db, request, NOW)
        with db.session() as session:
            row = session.query(RunRoleOutputRecord).filter_by(run_id=run, role=ROLE).one()
            if damage == "hash":
                body = json.loads(row.payload_json)
                body["definition"]["manifest"]["version"] = "9.0.0"
                row.payload_json = json.dumps(body)
            elif damage == "late":
                row.created_at = NOW + timedelta(seconds=1)
    query = ResearchQueryService(db, pack_root=PACK_ROOT)
    view = query.get(run).run
    versions = query.observability_view(run).versions
    assert versions.domain_pack_version is None
    assert versions.gate_policy_ref is None
    assert versions.pack_receipt_ref is None
    assert versions.pack_basis == ("unavailable" if damage == "missing" else "invalid")
    assert versions.capability_versions == [] and versions.source_registry_ref is None
    if damage == "missing":
        assert view.budget is None and view.profile_ref is None
        assert view.budget_basis == "unavailable"
        assert versions.domain_pack_ref is None
    else:
        # Corrupt Pack must not erase a separately attested original request.
        assert view.budget == request.execution_budget
        assert versions.domain_pack_ref == request.domain_pack_ref
