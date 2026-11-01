import hashlib
import json
import shutil

import pytest
import yaml

from apps.hub_worker.research import ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import DomainPackManifest
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_method import ResearchMethodService
from packages.kernel.decision_hub_kernel.application.research_policy import (
    load_research_policy,
    policy_prompt_status,
    render_research_policy,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import DshSessionPromptRecord
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_dsh_web_runtime import FakeHostClient, _request
from tests.evolution.test_research_worker import NOW, PACK_ROOT, _admitted_run


def test_loaded_policy_is_frozen_and_only_new_process_and_run_use_new_files(tmp_path):
    db, event, run = _admitted_run(tmp_path)
    root = tmp_path / "pack"
    shutil.copytree(PACK_ROOT, root)
    factory = ResearchRequestFactory(db, pack_root=root, clock=lambda: NOW)
    factory.build(event, run)
    before = factory.packs.get(run)
    old_policy = before["definition"]["research_policy"]
    path = root / "doctrine/causal-chain.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nREVIEW_MARKER_2026\n", encoding="utf-8")
    other, _ = RunService(db).create(event, "old-process", strategy_version="research.v1")
    factory.build(event, other)
    assert factory.packs.get(other)["definition"]["research_policy"] == old_policy
    restarted = ResearchRequestFactory(db, pack_root=root, clock=lambda: NOW)
    restarted.build(event, run)
    assert restarted.packs.get(run) == before
    fresh, _ = RunService(db).create(event, "new-process", strategy_version="research.v1")
    restarted.build(event, fresh)
    updated = restarted.packs.get(fresh)
    rendered_updated = render_research_policy(updated["definition"]["research_policy"], [])
    assert "REVIEW_MARKER_2026" in rendered_updated
    assert updated["definition_hash"] != before["definition_hash"]
    rendered = render_research_policy(old_policy, ["web.fetch"])
    payload = json.loads(rendered.splitlines()[2])
    assert all(r["capability_routes"] == ["web.fetch"] for r in payload["roles"])
    assert "market.crypto_derivatives" not in rendered
    assert "REVIEW_MARKER_2026" not in rendered


@pytest.mark.parametrize("failure", ["outside", "missing", "duplicate", "oversized"])
def test_invalid_policy_is_rejected_before_a_run_is_built(tmp_path, failure):
    root = tmp_path / "pack"
    shutil.copytree(PACK_ROOT, root)
    manifest = yaml.safe_load((root / "pack.yaml").read_text(encoding="utf-8"))
    if failure == "outside":
        outside = tmp_path / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        manifest["doctrine_ref"] = "../outside.md"
    elif failure == "missing":
        manifest["doctrine_ref"] = "missing.md"
    elif failure == "duplicate":
        manifest["role_profile_refs"].append(manifest["role_profile_refs"][0])
    else:
        (root / "doctrine/causal-chain.md").write_text("x" * 64_001)
    with pytest.raises(ValueError, match="research_policy_"):
        load_research_policy(root, DomainPackManifest.model_validate(manifest))


async def test_frozen_policy_reaches_each_host_prompt_and_audit_detects_missing_block(tmp_path):
    db, event, run = _admitted_run(tmp_path)
    factory = ResearchRequestFactory(db, pack_root=PACK_ROOT)
    factory.build(event, run)
    host = FakeHostClient()
    links = DshSessionLinkService(db)
    runtime = DshWebResearchRuntime(links, host, poll_interval_seconds=0.001)
    pack = factory.packs.get(run)
    request = _request(run)
    instruction = render_research_policy(
        pack["definition"]["research_policy"], request.allowed_capabilities
    )
    original = host.submit

    async def submit(submission):
        method = ResearchMethodService(db).get(run)
        expected = hashlib.sha256(instruction.encode()).hexdigest()
        assert method["definition"]["research_policy_prompt_hash"] == expected
        saved = links.get_prompt(run, submission.generation)
        assert saved.prompt.startswith(instruction + "\n\n")
        return await original(submission)

    host.submit = submit
    await runtime.execute(request)
    before = links.get_prompt(run, 1)
    await runtime.execute(request)
    assert links.get_prompt(run, 1) == before
    from tools.ops.audit_research_method import audit

    assert audit(db, run)["checks"]["frozen_policy_in_saved_prompts"]
    definition = ResearchMethodService(db).get(run)["definition"]
    assert policy_prompt_status(definition, pack["definition"], []) == "prepared"
    with db.session() as session:
        row = session.get(DshSessionPromptRecord, (run, 1))
        row.prompt = row.prompt.replace("BEGIN_FROZEN_PACK_INSTRUCTIONS", "REMOVED", 1)
    assert not audit(db, run)["checks"]["frozen_policy_in_saved_prompts"]
    from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError

    with pytest.raises(AgentExecutionError) as rejected:
        await runtime.execute(request)
    assert rejected.value.error_code == "research_method_binding_failed"
    assert len(host.submit_calls) == 2
    from packages.kernel.decision_hub_kernel.persistence.db import utcnow
    from packages.query_views.evolution_readiness import evolution_readiness

    assert evolution_readiness(db, utcnow()).research_method["research_policy_status"] == "invalid"
