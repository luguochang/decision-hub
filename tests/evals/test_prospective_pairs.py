"""Injected runtimes exercise durable behavior; they do not prove forecast quality."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from apps.hub_worker.prospective import ProspectiveWorker
from packages.evals.prospective_pairs import ProspectivePairService
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalOutlookRecord,
    FactVersionRecord,
    ParsedVersionRecord,
    ProspectiveArmRecord,
    ProspectivePairRecord,
    PublicationDecisionRecord,
    RawCaptureRecord,
    RunInputRefRecord,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError, AgentResult
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from tests.evals.test_prospective_trials import context as context


def add_source(c, *, identity="outlook_test", study="study_test", at=None, **updates):
    at = at or c.clock[0]
    digest, ref = RawBodyArchive(c.tmp_path / "raw-captures").put(b'{"value":100}')
    c.input_ref = ref
    with c.database.session() as session:
        session.add(RawCaptureRecord(
            capture_id=f"input-{identity}", context_id=identity, url="https://unit.test/input",
            status_code=200, body_hash=digest, body_ref=ref, byte_count=13,
            media_type="application/json", adapter_version="unit",
            started_at=at, completed_at=at, recorded_at=at,
        ))
        session.add(ParsedVersionRecord(
            parsed_version_id=identity, original_evidence_id=identity, content_hash=digest,
            parser_version="unit", capture_ids_json=json.dumps([f"input-{identity}"]),
            archive_status="captured", recorded_at=at,
        ))
        session.add(PublicationDecisionRecord(
            artifact_id=identity, run_id=identity, status="research_only", result_hash="hash",
            decisions_json="[]", published_horizons_json="[]", review_refs_json="[]",
            decided_at=at,
        ))
        session.add(FactVersionRecord(
            fact_version_id=identity, semantic_key=identity, market="crypto",
            scope_instrument="BTC-USDT-SWAP", field="close", source_id="unit-source",
            original_fact_id=identity, parsed_version_id=identity,
            payload_json='{"value":100,"field":"close"}', known_at=at, observed_at=at,
            recorded_at=at,
        ))
        session.add(RunInputRefRecord(fact_id=identity, run_id=identity, fact_version_id=identity,
                                     use_mode="adopted", as_of=at, adopted_at=at))
        values = dict(
            outlook_id=identity, run_id=identity, artifact_id=identity, study_id=study,
            market="crypto", instrument="BTC-USDT-SWAP", method_family="research",
            method_version="unit.v1", horizon="24h", action="short", subjective_probability=.9,
            probability_status="uncalibrated", data_as_of=at, window_start_at=at,
            window_end_at=at + timedelta(hours=24), result_hash="hash",
            evidence_refs_json='["source"]', fact_version_refs_json=json.dumps([identity]),
            raw_payload_json='{"prediction":"DO NOT COPY THIS"}', trigger="", invalidation="",
            cohort="prospective", eligibility="eligible", exclusion_reasons_json="[]",
            research_quality="supported", recorded_at=at,
        )
        session.add(AnalyticalOutlookRecord(**{**values, **updates}))


@pytest.fixture
def pair_context(context, monkeypatch):
    c = context
    c.trial = c.service.register(c.request, c.baseline, c.candidate)
    c.clock[0] = c.request.starts_at + timedelta(seconds=1)
    c.pairs = ProspectivePairService(
        c.database, c.service, data_roots=[c.tmp_path], clock=lambda: c.clock[0],
    )
    c.calls = []

    async def execute(request):
        c.calls.append(request)
        await asyncio.sleep(0)
        return AgentResult(
            role=request.role, runtime_id=c.baseline.runtime_id,
            runtime_version=c.baseline.runtime_version, latency_ms=1,
            payload={"direction": "long", "probability": .6, "citations": list(request.evidence)},
        )

    monkeypatch.setattr(c.baseline, "execute", execute)
    c.worker = ProspectiveWorker(c.pairs, c.trial.trial_id, c.baseline, c.candidate)
    return c


@pytest.mark.parametrize("fault", ["missing", "late", "failed_http", "corrupt", "scope"])
@pytest.mark.parametrize("after_admission", [False, True])
async def test_invalid_raw_inputs_prevent_both_model_calls(pair_context, fault, after_admission):
    c = pair_context
    add_source(c)
    identity = c.pairs.admit_next(c.trial.trial_id) if after_admission else None
    with c.database.session() as session:
        parsed = session.get(ParsedVersionRecord, "outlook_test")
        capture = session.get(RawCaptureRecord, "input-outlook_test")
        if fault == "missing":
            parsed.capture_ids_json = "[]"
        elif fault == "late":
            capture.recorded_at = c.clock[0] + timedelta(seconds=1)
        elif fault == "failed_http":
            capture.status_code = 503
        elif fault == "scope":
            session.get(FactVersionRecord, "outlook_test").market = "metal"
        else:
            (c.tmp_path / "raw-captures" / capture.body_hash[:2] / capture.body_hash).write_bytes(
                b"corrupt original input")
    if identity is None:
        identity = c.pairs.admit_next(c.trial.trial_id)
    await c.pairs.execute(identity, baseline_runtime=c.baseline, candidate_runtime=c.candidate)
    view = c.pairs.get(identity)
    assert c.calls == []
    assert all(arm["status"] in {"excluded", "failed"} for arm in view["arms"])
    assert all(arm["error_code"] for arm in view["arms"])


async def test_two_methods_share_frozen_input_and_never_repeat(pair_context):
    c = pair_context
    add_source(c)
    result = await c.worker.tick()
    assert result["status"] == "predictions_saved"
    view = c.pairs.get(result["pair_id"])
    assert len(c.calls) == 2
    assert c.calls[0].text == c.calls[1].text
    assert c.calls[0].deadline_at == c.calls[1].deadline_at > c.clock[0]
    assert "DO NOT COPY THIS" not in c.calls[0].text
    assert "subjective_probability" not in c.calls[0].text
    assert not view["promotion_eligible"] and view["label_status"] == "not_scheduled"
    assert len({a["recipe_hash"] for a in view["arms"]}) == 2
    before = view
    await c.pairs.execute(result["pair_id"], baseline_runtime=c.baseline,
                          candidate_runtime=c.candidate)
    assert c.pairs.get(result["pair_id"]) == before
    add_source(c, identity="later_success", at=c.clock[0] + timedelta(seconds=1))
    c.clock[0] += timedelta(seconds=2)
    assert (await c.worker.tick())["status"] == "no_input"
    assert len(c.calls) == 2


async def test_first_invalid_or_late_input_is_retained_not_replaced(pair_context):
    c = pair_context
    add_source(c, eligibility="excluded")
    add_source(c, identity="later_valid", at=c.clock[0] + timedelta(seconds=1))
    c.clock[0] += timedelta(seconds=2)
    first = await c.worker.tick()
    assert first["status"] == "excluded"
    assert c.pairs.get(first["pair_id"])["exclusion_reason"] == "source_outlook_excluded"
    assert (await c.worker.tick())["status"] == "no_input"
    add_source(c, identity="late", study="late-study", at=c.clock[0])
    c.clock[0] += timedelta(minutes=5)
    late = await c.worker.tick()
    assert c.pairs.get(late["pair_id"])["exclusion_reason"] == "input_deadline_missed"
    assert c.calls == []


async def test_v2_accepts_facts_when_original_method_abstains_without_weakening_data_checks(
    pair_context,
):
    c = pair_context
    add_source(c, eligibility="excluded", action="no_trade",
               exclusion_reasons_json='["no_directional_hypothesis"]')
    assert (await c.worker.tick())["status"] == "predictions_saved"
    add_source(c, identity="invalid", study="other", eligibility="excluded", action="no_trade",
               exclusion_reasons_json='["no_directional_hypothesis","input_after_freeze"]')
    assert (await c.worker.tick())["status"] == "excluded"
    assert len(c.calls) == 2


async def test_v1_registration_keeps_original_abstention_exclusion(pair_context):
    c = pair_context
    c.clock[0] = c.request.starts_at - timedelta(seconds=1)
    trial = c.service.register(c.request.model_copy(update={
        "request_id": "v1-request", "input_rule": "first-recorded-outlook-facts.v1",
    }), c.baseline, c.candidate)
    c.clock[0] = c.request.starts_at + timedelta(seconds=1)
    add_source(c, eligibility="excluded", action="no_trade",
               exclusion_reasons_json='["no_directional_hypothesis"]')
    worker = ProspectiveWorker(c.pairs, trial.trial_id, c.baseline, c.candidate)
    assert (await worker.tick())["status"] == "excluded"
    assert c.calls == []


def test_rejects_past_future_retrospective_and_other_scope(pair_context):
    c = pair_context
    for identity, fields in [
        ("past", {"at": c.request.starts_at - timedelta(seconds=1)}),
        ("future", {"at": c.clock[0] + timedelta(seconds=1)}),
        ("retro", {"cohort": "retrospective"}),
        ("gold", {"instrument": "XAU-USD"}),
        ("other-horizon", {"horizon": "30m"}),
    ]:
        add_source(c, identity=identity, study=identity, **fields)
    assert c.pairs.admit_next(c.trial.trial_id) is None


async def test_after_cutoff_fact_is_excluded(pair_context):
    c = pair_context
    add_source(c)
    with c.database.session() as session:
        session.get(FactVersionRecord, "outlook_test").recorded_at += timedelta(seconds=1)
    result = await c.worker.tick()
    assert c.pairs.get(result["pair_id"])["exclusion_reason"] == "source_fact_after_cutoff"
    assert c.calls == []


async def test_nonfinite_fact_is_recorded_as_rejection_without_stalling_queue(pair_context):
    c = pair_context
    add_source(c)
    with c.database.session() as session:
        session.get(FactVersionRecord, "outlook_test").payload_json = '{"value":NaN}'
    result = await c.worker.tick()
    assert c.pairs.get(result["pair_id"])["exclusion_reason"] == "source_input_invalid"
    assert (await c.worker.tick())["status"] == "no_input"
    assert c.calls == []


async def test_parallel_workers_claim_each_arm_once(pair_context):
    c = pair_context
    add_source(c)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: c.pairs.admit_next(c.trial.trial_id), range(2)))
    assert results.count(None) == 1
    pair_id = next(r for r in results if r)
    await asyncio.gather(*(c.pairs.execute(pair_id, baseline_runtime=c.baseline,
                                         candidate_runtime=c.candidate) for _ in range(3)))
    assert len(c.calls) == 2
    assert c.pairs.get(pair_id)["status"] == "predictions_saved"


async def test_restart_recovers_unstarted_arm_and_retains_unknown_call(pair_context):
    c = pair_context
    add_source(c)
    pair_id = c.pairs.admit_next(c.trial.trial_id)
    with c.database.session() as session:
        arm = session.get(ProspectiveArmRecord, (pair_id, "baseline"))
        arm.status, arm.owner, arm.started_at = "running", "lost-process", c.clock[0]
    # Another worker may run only the arm whose call was never claimed.
    result = await c.worker.tick()
    assert result["status"] == "running" and len(c.calls) == 1
    c.clock[0] += timedelta(minutes=3)
    assert c.pairs.recover_expired(c.trial.trial_id) == 1
    view = c.pairs.get(pair_id)
    assert view["status"] == "incomplete"
    assert {a["status"] for a in view["arms"]} == {"succeeded", "unknown"}
    assert (await c.worker.tick())["status"] == "no_input"
    assert len(c.calls) == 1


async def test_provider_failure_keeps_successful_other_arm(pair_context, monkeypatch):
    c = pair_context
    original = c.baseline.execute

    async def one_fails(request):
        if any("Examine supplied counter-evidence" in i for i in request.instructions):
            raise AgentExecutionError("provider_auth_failed", "secret must not be saved")
        return await original(request)

    monkeypatch.setattr(c.baseline, "execute", one_fails)
    add_source(c)
    result = await c.worker.tick()
    view = c.pairs.get(result["pair_id"])
    assert view["status"] == "incomplete"
    assert {a["status"] for a in view["arms"]} == {"succeeded", "failed"}
    assert "secret" not in json.dumps(view)


async def test_late_output_never_counts_as_predictions(pair_context, monkeypatch):
    c = pair_context
    add_source(c)
    pair_id = c.pairs.admit_next(c.trial.trial_id)
    original = c.baseline.execute

    async def late(request):
        response = await original(request)
        c.clock[0] += timedelta(seconds=121)
        return response

    monkeypatch.setattr(c.baseline, "execute", late)
    view = await c.pairs.execute(pair_id, baseline_runtime=c.baseline,
                                 candidate_runtime=c.candidate)
    assert view["status"] == "incomplete"
    assert all(a["status"] == "late" for a in view["arms"])


async def test_recipe_drift_blocks_both_before_calls(pair_context):
    c = pair_context
    add_source(c)
    c.baseline.system_prompt = "Changed since registration"
    result = await c.worker.tick()
    assert result["status"] == "incomplete"
    assert c.calls == []


async def test_input_and_result_corruption_are_detected(pair_context):
    c = pair_context
    add_source(c)
    pair_id = c.pairs.admit_next(c.trial.trial_id)
    with c.database.session() as session:
        session.get(ProspectivePairRecord, pair_id).input_json = '{}'
    with pytest.raises(ValueError, match="pair_input_corrupt"):
        await c.pairs.execute(pair_id, baseline_runtime=c.baseline, candidate_runtime=c.candidate)
    assert c.calls == []


async def test_cancelled_calls_remain_unknown_without_resending(pair_context, monkeypatch):
    c = pair_context
    entered = asyncio.Event()
    count = []

    async def blocked(request):
        count.append(request)
        if len(count) == 2:
            entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(c.baseline, "execute", blocked)
    add_source(c)
    pair_id = c.pairs.admit_next(c.trial.trial_id)
    task = asyncio.create_task(c.pairs.execute(pair_id, baseline_runtime=c.baseline,
                                             candidate_runtime=c.candidate))
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert {a["status"] for a in c.pairs.get(pair_id)["arms"]} == {"unknown"}
    await c.pairs.execute(pair_id, baseline_runtime=c.baseline, candidate_runtime=c.candidate)
    assert len(count) == 2


async def test_result_hash_and_read_only_summary(pair_context):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from apps.hub_api.asset_routes import asset_router

    c = pair_context
    add_source(c)
    result = await c.worker.tick()
    app = FastAPI()
    app.include_router(asset_router(c.database, c.tmp_path, clock=lambda: c.clock[0]))
    response = TestClient(app).get("/v1/evolution/readiness")
    assert response.status_code == 200
    assert response.json()["paired_execution"] == {
        "registered_trials": 1, "pairs": 1, "predictions_saved": 1,
        "waiting": 0, "incomplete": 0, "excluded": 0, "outcome_scoring_available": True,
        "labels_completed": 0, "labels_waiting": 1, "labels_retry": 0,
    }
    assert "DO NOT COPY THIS" not in response.text
    with c.database.session() as session:
        session.get(ProspectiveArmRecord, (result["pair_id"], "baseline")).result_json = '{}'
    with pytest.raises(ValueError, match="pair_result_corrupt"):
        c.pairs.get(result["pair_id"])


async def test_main_worker_role_runs_and_records_heartbeat(pair_context, monkeypatch):
    from apps.hub_worker.main import _run_loop
    from packages.kernel.decision_hub_kernel.application.live_observation import (
        ServiceHeartbeatService,
    )

    c = pair_context
    monkeypatch.setattr("apps.hub_worker.prospective.build_prospective_worker",
                        lambda *args: c.worker)
    assert await _run_loop(database=c.database, registry=None, settings=None,
                           role="prospective", interval=1, once=True) == 0
    views = ServiceHeartbeatService(c.database).list_views()
    assert len(views) == 1 and views[0].status == "online"
    assert views[0].service_id == f"hub-prospective:{c.trial.trial_id}"
    assert c.calls == []


def test_sample_capacity_cannot_be_bypassed(pair_context):
    c = pair_context
    add_source(c)
    with c.database.session() as session:
        for i in range(c.request.maximum_pairs):
            session.add(ProspectivePairRecord(
                pair_id=f"existing-{i}", trial_id=c.trial.trial_id, study_id=f"study-{i}",
                outlook_id=f"outlook-{i}", input_json="{}", input_hash="unused",
                source_recorded_at=c.clock[0], admitted_at=c.clock[0],
                deadline_at=c.clock[0], window_end_at=c.clock[0],
                exclusion_reason="input_deadline_missed",
            ))
    assert c.pairs.admit_next(c.trial.trial_id) is None
    assert c.calls == []


async def test_pair_cli_reads_verified_receipts_without_new_model_calls(
    pair_context, monkeypatch, capsys,
):
    from tools.ops import prospective_trials as cli

    c = pair_context
    add_source(c)
    await c.worker.tick()
    monkeypatch.setattr(cli, "LangGraphAgentRuntime", lambda: pytest.fail("must not build model"))
    monkeypatch.setattr("sys.argv", ["trials", "--database", str(c.database.sqlite_path),
                                    "--artifact-root", str(c.store.root),
                                    "--pairs", c.trial.trial_id])
    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["read_only"] and result["next_offset"] is None
    assert result["pairs"][0]["status"] == "predictions_saved"
    assert len(c.calls) == 2
