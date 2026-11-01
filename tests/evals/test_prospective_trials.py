"""Isolated contract tests; injected agents are never evidence of live execution."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from packages.contracts_py.decision_hub_contracts.models import CandidateProposal, CandidateVersion
from packages.evals.prospective_trials import ProspectiveTrialService, TrialRequest
from packages.kernel.decision_hub_kernel.application.evolution import EvolutionAssetService
from packages.kernel.decision_hub_kernel.persistence.db import Database, ProspectiveTrialRecord
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError, AgentRequest
from packages.orchestration.langgraph.evolution_executor import CandidateArtifactStore
from packages.runtime_adapters.candidate_runtime import CandidateConfigurationRuntime
from packages.runtime_adapters.execution_recipe import canonical_recipe, runtime_recipe
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime
from packages.runtime_adapters.langgraph_agent.provider_config import ProviderConfig
from packages.runtime_adapters.langgraph_agent.runtime import LangGraphAgentRuntime

NOW = datetime(2026, 9, 30, 8, tzinfo=UTC)


@pytest.fixture
def context(tmp_path, monkeypatch):
    calls = []

    class NoCallsAgent:
        async def ainvoke(self, payload):
            calls.append(payload)
            raise AssertionError("Registration must never execute a model")

    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-never-record")
    monkeypatch.setattr(LangGraphAgentRuntime, "_build_agent", lambda self: NoCallsAgent())
    config = ProviderConfig(base_url="https://provider.test/v1", model="test-model")
    baseline = LangGraphAgentRuntime(config)
    proposal = CandidateProposal(
        execution_scope="directional_synthesis", execution_rules=["examine_counter_evidence"],
        proposal_id="proposal_test", candidate_type="strategy", version="candidate.v1",
        parent_version="baseline.v1", summary="Check contrary evidence",
        changes=["Check contrary evidence before proposing a direction"],
        rationale="Avoid single-source certainty", evidence_refs=["feedback_test"],
    )
    candidate = CandidateConfigurationRuntime(baseline, proposal)
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'trials.sqlite3'}")
    database.initialize()
    clock = [NOW]
    assets = EvolutionAssetService(database, clock=lambda: clock[0])
    pointer = assets.ensure_release_baseline("crypto_macro.v1")
    store = CandidateArtifactStore(tmp_path / "artifacts")
    identity, content_hash, content_ref = store.persist(
        SimpleNamespace(job_id="test_job", domain_pack_ref="crypto_macro.v1"), proposal,
    )
    assets.register_candidate(CandidateVersion(
        execution_scope="directional_synthesis",
        candidate_id=identity, candidate_type="strategy", version=proposal.version,
        parent_version=proposal.parent_version, content_hash=content_hash,
        content_ref=content_ref, status="candidate", source="test", created_at=NOW,
    ))
    service = ProspectiveTrialService(database, artifact_root=store.root, clock=lambda: clock[0])
    request = TrialRequest(
        request_id="trial_request", baseline_candidate_id=pointer.candidate_id,
        candidate_id=identity, horizon="24h", starts_at=NOW + timedelta(hours=1),
        ends_at=NOW + timedelta(days=31),
    )
    return SimpleNamespace(**locals())


def test_proposal_and_returned_recipe_are_detached(context):
    c = context
    before = runtime_recipe(c.candidate)
    c.proposal.changes.append("Mutated outside the runtime")
    c.candidate.proposal.changes.clear()
    c.candidate.execution_recipe()["proposal"]["changes"].clear()
    assert runtime_recipe(c.candidate) == before


@pytest.mark.parametrize("field,value", [
    ("model", "different-model"), ("token_budget", 800), ("api_mode", "chat"),
    ("base_url", "https://another.test/v1"),
])
def test_same_proposal_different_provider_has_distinct_identity(context, field, value):
    c = context
    config = ProviderConfig.model_validate({**c.config.model_dump(), field: value})
    other = CandidateConfigurationRuntime(LangGraphAgentRuntime(config), c.proposal)
    assert other.runtime_version != c.candidate.runtime_version


def test_prompt_and_schema_bound_and_credentials_excluded(context):
    c = context
    other = CandidateConfigurationRuntime(
        LangGraphAgentRuntime(c.config, system_prompt="Different instruction"), c.proposal,
    )
    assert other.runtime_version != c.candidate.runtime_version
    receipt = canonical_recipe(c.candidate.execution_recipe())
    assert "response_schema" in receipt
    assert "test-secret-never-record" not in receipt
    assert "provider.test" not in receipt
    secret_url = ProviderConfig(base_url="https://user:secret@provider.test/v1?token=secret")
    secret_recipe = runtime_recipe(LangGraphAgentRuntime(secret_url))
    assert not secret_recipe["live_attestable"]
    assert "secret" not in canonical_recipe(secret_recipe)


@pytest.mark.parametrize("target,field,value", [
    ("baseline", "system_prompt", "Changed after construction"),
    ("baseline", "max_attempts", 10), ("candidate", "max_attempts", 10),
])
def test_drift_fails_before_any_model_call(context, target, field, value):
    c = context
    setattr(getattr(c, target), field, value)
    request = AgentRequest(role="decision_synthesis", text="Future input", evidence=(),
                           deadline_at=datetime.now(UTC) + timedelta(seconds=20))
    with pytest.raises(AgentExecutionError, match="changed"):
        asyncio.run(c.candidate.execute(request))
    assert c.calls == []


def test_registration_durable_idempotent_and_never_promotes(context):
    c = context
    before = c.assets.active_pointer("crypto_macro.v1")
    first = c.service.register(c.request, c.baseline, c.candidate)
    c.clock[0] += timedelta(hours=2)
    second = c.service.register(c.request, c.baseline, c.candidate)
    assert second == first
    reopened = ProspectiveTrialService(Database(str(c.database.engine.url)),
                                      artifact_root=c.store.root, clock=lambda: c.clock[0])
    assert reopened.list() == [first]
    assert c.assets.active_pointer("crypto_macro.v1") == before
    assert c.calls == []
    with pytest.raises(ValueError, match="trial_request_reused"):
        c.service.register(c.request.model_copy(update={"horizon": "72h"}),
                           c.baseline, c.candidate)


def test_backdating_fake_and_unbound_runtime_rejected(context, monkeypatch):
    c = context
    past = c.request.model_copy(update={"starts_at": NOW})
    with pytest.raises(ValueError, match="before_start"):
        c.service.register(past, c.baseline, c.candidate)
    for fake in [FakeAgentRuntime()]:
        with pytest.raises(ValueError, match="attestable_live_runtime"):
            c.service.register(c.request, fake, c.candidate)
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    with pytest.raises(ValueError, match="attestable_live_runtime"):
        c.service.register(c.request, LangGraphAgentRuntime(c.config), c.candidate)
    assert c.service.list() == []
    assert c.calls == []


@pytest.mark.parametrize("offset", [-1, 3, 32 * 24])
def test_execution_rejects_earlier_future_and_closed_window(context, offset):
    c = context
    trial = c.service.register(c.request, c.baseline, c.candidate)
    c.clock[0] += timedelta(hours=2)
    with pytest.raises(ValueError, match="outside_registered_window"):
        c.service.assert_execution(trial.trial_id, baseline_runtime=c.baseline,
                                   candidate_runtime=c.candidate,
                                   received_at=NOW + timedelta(hours=offset))


def test_execution_checks_binding_artifact_and_stored_definition(context):
    c = context
    trial = c.service.register(c.request, c.baseline, c.candidate)
    c.clock[0] += timedelta(hours=2)
    kwargs = dict(baseline_runtime=c.baseline, candidate_runtime=c.candidate,
                  received_at=c.clock[0])
    assert c.service.assert_execution(trial.trial_id, **kwargs) == trial
    new_base = LangGraphAgentRuntime(c.config, system_prompt="Changed baseline")
    with pytest.raises(ValueError, match="execution_binding_changed"):
        c.service.assert_execution(trial.trial_id, **{**kwargs, "baseline_runtime": new_base})
    path = c.store.root / "candidates" / f"{c.identity}.json"
    original = path.read_bytes()
    path.write_bytes(original + b" ")
    with pytest.raises(ValueError, match="candidate_artifact_hash_mismatch"):
        c.service.assert_execution(trial.trial_id, **kwargs)
    path.write_bytes(original)
    with c.database.session() as session:
        row = session.get(ProspectiveTrialRecord, trial.trial_id)
        value = json.loads(row.definition_json)
        value["request"]["horizon"] = "72h"
        row.definition_json = json.dumps(value)
    with pytest.raises(ValueError, match="definition_corrupt"):
        c.service.list()
    assert c.calls == []


def test_candidate_artifact_domain_and_parent_rejected(context):
    c = context
    path = c.store.root / "candidates" / f"{c.identity}.json"
    value = json.loads(path.read_bytes())
    value["domain_pack_ref"] = "gold.v1"
    body = json.dumps(value).encode()
    path.write_bytes(body)
    from packages.kernel.decision_hub_kernel.persistence.db import CandidateVersionRecord

    with c.database.session() as session:
        row = session.get(CandidateVersionRecord, c.identity)
        row.content_hash = hashlib.sha256(body).hexdigest()
    with pytest.raises(ValueError, match="recipe_mismatch"):
        c.service.register(c.request, c.baseline, c.candidate)


def test_request_requires_aware_time_and_minimum_window(context):
    value = context.request.model_dump()
    for update in [{"starts_at": NOW.replace(tzinfo=None)},
                   {"ends_at": NOW + timedelta(days=1)}, {"maximum_pairs": 3}]:
        with pytest.raises(ValueError):
            TrialRequest.model_validate({**value, **update})


def test_cli_read_only_does_not_construct_runtime(context, monkeypatch, capsys):
    from tools.ops import prospective_trials as cli

    c = context
    c.service.register(c.request, c.baseline, c.candidate)
    monkeypatch.setattr(cli, "LangGraphAgentRuntime",
                        lambda: pytest.fail("read must not build model"))
    monkeypatch.setattr("sys.argv", ["trials", "--database", str(c.database.sqlite_path),
                                    "--artifact-root", str(c.store.root)])
    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["read_only"] and len(result["trials"]) == 1
    assert c.calls == []


def test_cli_registers_existing_versions_without_starting_execution(context, monkeypatch, capsys):
    from tools.ops import prospective_trials as cli

    c = context
    now = datetime.now(UTC)
    request = c.request.model_copy(update={"starts_at": now + timedelta(hours=1),
                                           "ends_at": now + timedelta(days=31)})
    path = c.tmp_path / "request.json"
    path.write_bytes(request.model_dump_json().encode())
    monkeypatch.setattr(cli, "LangGraphAgentRuntime", lambda: c.baseline)
    monkeypatch.setattr("sys.argv", ["trials", "--database", str(c.database.sqlite_path),
                                    "--artifact-root", str(c.store.root), "--register", str(path)])
    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["registered"]["definition"]["request"] == request.model_dump(mode="json")
    assert result["model_calls"] == 0 and not result["execution_started"]
    assert not result["promotion_performed"] and c.calls == []


@pytest.mark.parametrize("identity_key", ["identity", "baseline"])
def test_trial_refuses_harness_scope_before_model_dispatch(context, identity_key):
    from packages.kernel.decision_hub_kernel.persistence.db import CandidateVersionRecord

    c = context
    identity = c.identity if identity_key == "identity" else c.pointer.candidate_id
    with c.database.session() as session:
        session.get(CandidateVersionRecord, identity).execution_scope = "research_harness"
    with pytest.raises(ValueError, match="trial_execution_scope_mismatch"):
        c.service.register(c.request, c.baseline, c.candidate)
    assert not c.calls
