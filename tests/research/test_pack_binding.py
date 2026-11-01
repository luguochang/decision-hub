import json
import shutil

import pytest
import yaml

from apps.hub_worker.research import ResearchRequestFactory
from packages.kernel.decision_hub_kernel.application.research_pack import ROLE, ResearchPackService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
from tests.evolution.test_research_worker import NOW, PACK_ROOT, _admitted_run


def test_worker_uses_one_loaded_pack_and_new_runs_only_change_after_reload(tmp_path):
    db, event, run = _admitted_run(tmp_path)
    root = tmp_path / "pack"
    shutil.copytree(PACK_ROOT, root)
    factory = ResearchRequestFactory(db, pack_root=root, clock=lambda: NOW)
    initial = factory.build(event, run)
    receipt = factory.packs.get(run)
    manifest_path = root / "pack.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf8"))
    manifest["execution_budget"]["max_tool_calls"] = 77
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf8")
    evidence_path = root / "evidence/source_manifest.yaml"
    policy = yaml.safe_load(evidence_path.read_text(encoding="utf8"))
    policy["requirements"][0]["description"] = "A new requirement description"
    evidence_path.write_text(yaml.safe_dump(policy), encoding="utf8")
    # The old process cannot combine the new manifest with cached old evidence policy.
    another, _ = RunService(db).create(event, "old-process", strategy_version="research.v1")
    same_process = factory.build(event, another)
    assert same_process.execution_budget == initial.execution_budget
    assert same_process.evidence_requirements == initial.evidence_requirements
    # A restarted process restores the exact old Run policy and permissions.
    reloaded = ResearchRequestFactory(
        db, pack_root=root, allowed_capabilities=("replay.research", "web.fetch"), clock=lambda: NOW
    )
    recovered = reloaded.build(event, run)
    assert recovered.execution_budget == initial.execution_budget
    assert recovered.evidence_requirements == initial.evidence_requirements
    assert recovered.allowed_capabilities == initial.allowed_capabilities
    assert reloaded.packs.get(run) == receipt
    fresh, _ = RunService(db).create(event, "new-process", strategy_version="research.v1")
    changed = reloaded.build(event, fresh)
    assert changed.execution_budget.max_tool_calls == 77
    assert changed.evidence_requirements != initial.evidence_requirements
    assert changed.allowed_capabilities == ["replay.research", "web.fetch"]
    assert reloaded.packs.get(fresh)["definition_hash"] != receipt["definition_hash"]
    from tests.research.test_durable_research_gateway import _gateway

    gateway = _gateway(db, "unused-for-policy-read")
    # MCP persistence uses this Run's pinned rule, not its own current Pack copy.
    old_rules = gateway._active_requirements(event, run_id=run)
    new_rules = gateway._active_requirements(event, run_id=fresh)
    assert old_rules != new_rules
    assert list(old_rules.values()) == initial.evidence_requirements
    revoked = ResearchRequestFactory(db, pack_root=root, allowed_capabilities=("web.fetch",))
    with pytest.raises(ValueError, match="research_pack_capability_revoked") as error:
        revoked.build(event, run)
    assert error.value.error_code == "research_pack_capability_revoked"
    mode_changed = ResearchRequestFactory(db, pack_root=root, execution_mode="replay")
    with pytest.raises(ValueError, match="research_pack_execution_mode_changed"):
        mode_changed.validate_scope(event, run)


def test_corrupt_pack_is_rejected_before_building_a_new_request(tmp_path):
    db, event, run = _admitted_run(tmp_path)
    factory = ResearchRequestFactory(db, pack_root=PACK_ROOT, clock=lambda: NOW)
    factory.build(event, run)
    with db.session() as session:
        row = session.query(RunRoleOutputRecord).filter_by(run_id=run, role=ROLE).one()
        body = json.loads(row.payload_json)
        body["definition"]["manifest"]["execution_budget"]["max_tool_calls"] = 1
        row.payload_json = json.dumps(body)
    with pytest.raises(ValueError, match="research_pack_receipt_corrupt"):
        factory.build(event, run)


def test_preexisting_method_never_gains_a_backdated_pack_receipt(tmp_path):
    db, event, run = _admitted_run(tmp_path)
    db.save_role_output(run, "research_execution_method", {}, "research-execution-method.v1")
    factory = ResearchRequestFactory(db, pack_root=PACK_ROOT, clock=lambda: NOW)
    factory.build(event, run)
    assert ResearchPackService(db).get(run) is None
