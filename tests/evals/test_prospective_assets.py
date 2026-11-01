import json
from datetime import timedelta

import pytest

from packages.evals.prospective_assets import (
    build_trial_snapshot,
    record_trial_snapshot,
    save_trial_snapshot,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    CandidateVersionRecord,
    ExperimentRecord,
    ExperimentResultRecord,
    ProspectiveArmRecord,
    ProspectiveLabelRecord,
)
from packages.runtime_adapters.execution_recipe import canonical_recipe, recipe_hash
from tests.evals.test_prospective_labels import labelled as labelled
from tests.evals.test_prospective_pairs import add_source
from tests.evals.test_prospective_pairs import context as context
from tests.evals.test_prospective_pairs import pair_context as pair_context


@pytest.fixture
async def auditable(labelled):
    return labelled


def snapshot(c):
    return build_trial_snapshot(
        c.database, c.trial.trial_id, data_roots=[c.tmp_path], now=c.clock[0]
    )


async def test_verified_projection_idempotent_and_never_promotes(auditable):
    c = auditable
    c.clock[0] = c.due
    await c.labels.process_due(c.trial.trial_id)
    view = snapshot(c)
    receipt = c.tmp_path / "receipt.json"
    save_trial_snapshot(view, receipt)
    result = record_trial_snapshot(c.assets, view, receipt_path=receipt)
    assert [r["sample_count"] for r in result["results"]] == [1, 1]
    assert all(r["directional_accuracy"] == 1 for r in result["results"])
    assert all(r["brier_score"] is None and r["cost_usd"] is None for r in result["results"])
    assert result == record_trial_snapshot(c.assets, view, receipt_path=receipt)
    with c.database.session() as session:
        assert session.query(ExperimentResultRecord).count() == 2
    assert not c.assets.promotion_eligible(c.identity, [result["results"][1]["result_id"]])
    c.clock[0] += timedelta(hours=1)
    assert snapshot(c)["snapshot_hash"] == view["snapshot_hash"]


async def test_abstention_pending_and_excluded_cases_not_dropped(auditable):
    c = auditable
    with c.database.session() as session:
        arm = session.get(ProspectiveArmRecord, (c.pair_id, "candidate"))
        value = json.loads(arm.result_json)
        value["payload"]["direction"] = "no_trade"
        arm.result_json, arm.result_hash = canonical_recipe(value), recipe_hash(value)
    add_source(c, identity="excluded", study="excluded-study", eligibility="excluded")
    await c.worker.tick()
    c.clock[0] = c.due
    pending = snapshot(c)
    assert len(pending["snapshot"]["cases"]) == 2
    assert not any(v["common_directional_sample"] for v in pending["snapshot"]["cases"])
    await c.labels.process_due(c.trial.trial_id)
    complete = snapshot(c)
    assert complete["snapshot_hash"] != pending["snapshot_hash"]
    receipt = c.tmp_path / "receipt.json"
    save_trial_snapshot(complete, receipt)
    result = record_trial_snapshot(c.assets, complete, receipt_path=receipt)
    assert [r["sample_count"] for r in result["results"]] == [0, 0]
    assert all(r["directional_accuracy"] is None for r in result["results"])
    assert result["results"][1]["failure_counts"]["non_directional"] == 1
    assert result["results"][0]["failure_counts"]["source_outlook_excluded"] == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("market_return_pct", -2),
        ("paired_correctness_delta", -1),
        ("quality_status", "verified"),
    ],
)
async def test_rehashed_false_labels_fail_recalculation(auditable, field, value):
    c = auditable
    c.clock[0] = c.due
    await c.labels.process_due(c.trial.trial_id)
    with c.database.session() as session:
        row = session.get(ProspectiveLabelRecord, c.pair_id)
        label = json.loads(row.result_json)
        label[field] = value
        row.result_json, row.result_hash = canonical_recipe(label), recipe_hash(label)
    with pytest.raises(ValueError, match="pair_label_recalculation_mismatch"):
        snapshot(c)
    with c.database.session() as session:
        assert session.query(ExperimentResultRecord).count() == 0


@pytest.mark.parametrize("which", ["input", "label"])
async def test_raw_bytes_must_still_exist_and_match(auditable, which):
    c = auditable
    c.clock[0] = c.due
    await c.labels.process_due(c.trial.trial_id)
    ref = (
        c.input_ref
        if which == "input"
        else c.labels.get(c.pair_id)["result"]["captures"][0]["body_ref"]
    )
    digest = ref.rsplit("/", 1)[1]
    (c.tmp_path / "raw-captures" / digest[:2] / digest).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="raw_capture_archive_corrupt"):
        snapshot(c)


async def test_retry_attempt_and_unknown_arm_are_retained(auditable):
    c = auditable
    with c.database.session() as session:
        arm = session.get(ProspectiveArmRecord, (c.pair_id, "candidate"))
        arm.status, arm.result_json, arm.result_hash = "unknown", None, None
    c.clock[0], c.market.failure = c.due, True
    await c.labels.process_due(c.trial.trial_id)
    c.market.failure = False
    c.clock[0] += timedelta(minutes=2)
    await c.labels.process_due(c.trial.trial_id)
    view = snapshot(c)
    case = view["snapshot"]["cases"][0]
    assert [a["status"] for a in case["label_attempts"]] == ["failed", "completed"]
    assert not case["common_directional_sample"]
    receipt = c.tmp_path / "receipt.json"
    save_trial_snapshot(view, receipt)
    result = record_trial_snapshot(c.assets, view, receipt_path=receipt)
    assert result["results"][1]["failure_counts"]["arm_unknown"] == 1


async def test_missing_archive_and_receipt_overwrite_are_rejected(auditable):
    c = auditable
    view = snapshot(c)
    receipt = c.tmp_path / "receipt.json"
    save_trial_snapshot(view, receipt)
    with pytest.raises(ValueError, match="prospective_snapshot_receipt_exists"):
        save_trial_snapshot({**view, "observed_at": "changed"}, receipt)
    with pytest.raises(ValueError, match="prospective_raw_archive_missing"):
        build_trial_snapshot(
            c.database, c.trial.trial_id, data_roots=[c.tmp_path / "missing"], now=c.clock[0]
        )


async def test_cli_defaults_to_read_only_export(auditable, monkeypatch, capsys):
    from tools.ops.project_prospective_assets import main

    c = auditable
    monkeypatch.setattr("tools.ops.project_prospective_assets.utcnow", lambda: c.clock[0])
    args = [
        "project",
        "--database",
        str(c.tmp_path / "trials.sqlite3"),
        "--data-root",
        str(c.tmp_path),
        "--trial-id",
        c.trial.trial_id,
        "--output",
        str(c.tmp_path / "cli.json"),
    ]
    monkeypatch.setattr("sys.argv", args)
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["status"] == "verified_export"
    with c.database.session() as session:
        assert session.query(ExperimentResultRecord).count() == 0
    monkeypatch.setattr("sys.argv", [*args, "--record"])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["status"] == "recorded"


async def test_trial_candidate_identity_cannot_drift(auditable):
    c = auditable
    with c.database.session() as session:
        session.get(CandidateVersionRecord, c.identity).version = "changed.v2"
    with pytest.raises(ValueError, match="trial_candidate_binding_changed"):
        snapshot(c)


async def test_interrupted_projection_resumes_without_duplicate_results(auditable, monkeypatch):
    c = auditable
    view = snapshot(c)
    receipt = c.tmp_path / "resume.json"
    save_trial_snapshot(view, receipt)
    original = c.assets.record_result

    def interrupted(result):
        if result.candidate_id == c.identity:
            raise RuntimeError("simulated interruption")
        return original(result)

    monkeypatch.setattr(c.assets, "record_result", interrupted)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        record_trial_snapshot(c.assets, view, receipt_path=receipt)
    with c.database.session() as session:
        assert session.query(ExperimentResultRecord).count() == 1
        assert session.query(ExperimentRecord).one().status == "running"
    monkeypatch.setattr(c.assets, "record_result", original)
    record_trial_snapshot(c.assets, view, receipt_path=receipt)
    with c.database.session() as session:
        assert session.query(ExperimentResultRecord).count() == 2
        assert session.query(ExperimentRecord).one().status == "completed"
