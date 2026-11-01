"""Project a coherent, verified trial snapshot into the existing evaluation ledger.

This direction-only scorer deliberately remains ineligible for promotion. It does
not retrofit Brier semantics onto a trial registered with a different scoring rule.
"""

import hashlib
import json
import math
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from packages.contracts_py.decision_hub_contracts.models import (
    EvaluationDatasetManifest,
    ExperimentManifest,
    ExperimentResultView,
)
from packages.evals.input_receipts import _Archives, verify_input_receipts
from packages.evals.prospective_labels import ProspectiveLabelService, score_pair
from packages.evals.prospective_observations import (
    _require,
    verify_archived_candles,
)
from packages.evals.prospective_pairs import HORIZONS, ProspectivePairService
from packages.evals.prospective_trials import ProspectiveTrialService, TrialRequest
from packages.kernel.decision_hub_kernel.application.evolution import dataset_manifest_hash
from packages.kernel.decision_hub_kernel.persistence.db import (
    CandidateVersionRecord,
    ProspectiveLabelAttemptRecord,
    ProspectivePairRecord,
    as_utc,
)
from packages.runtime_adapters.execution_recipe import canonical_recipe, recipe_hash

SCORER_VERSION = "prospective.paired-direction.v1"


class _SnapshotDatabase:
    """Reuse the caller's read transaction across existing read-only services."""

    def __init__(self, session):
        self._session = session

    @contextmanager
    def session(self):
        yield self._session


def _input_receipts(session, pair, request, archive):
    return verify_input_receipts(session, pair["input"], archive, request=request)


def build_trial_snapshot(database, trial_id, *, data_roots, now):
    """No writes, network, model execution, outcome selection or result limits."""
    now = as_utc(now)
    archive = _Archives(data_roots)
    with database.session() as session:
        if session.bind.dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN")
        snapshot_db = _SnapshotDatabase(session)
        trials = ProspectiveTrialService(snapshot_db, artifact_root=Path("."))
        trial = trials.get(trial_id)
        request = TrialRequest.model_validate(trial.definition["request"])
        _require(
            trial.registered_at < request.starts_at <= now, "trial_not_preregistered_or_not_started"
        )
        for name, identity in (("baseline", request.baseline_candidate_id),
                               ("candidate", request.candidate_id)):
            binding = trial.definition[name]
            candidate = session.get(CandidateVersionRecord, identity)
            _require(candidate and binding["candidate_id"] == identity
                     and binding["candidate_hash"] == candidate.content_hash
                     and binding["candidate_version"] == candidate.version
                     and recipe_hash(binding["recipe"]) == binding["recipe_hash"],
                     "trial_candidate_binding_changed")
            if candidate.content_ref:
                _require(hashlib.sha256(Path(candidate.content_ref).read_bytes()).hexdigest()
                         == candidate.content_hash, "trial_candidate_artifact_changed")
        pairs = ProspectivePairService(snapshot_db, trials)
        labels = ProspectiveLabelService(snapshot_db, pairs, None, Path("."))
        rows = (
            session.query(ProspectivePairRecord)
            .filter_by(trial_id=trial_id)
            .order_by(
                ProspectivePairRecord.source_recorded_at,
                ProspectivePairRecord.pair_id,
            )
            .all()
        )
        _require(bool(rows), "trial_has_no_pairs")
        _require(len(rows) <= request.maximum_pairs, "trial_pair_limit_exceeded")
        cases = []
        for row in rows:
            pair = pairs.get(row.pair_id)
            label = labels.get(row.pair_id)
            item = {
                "pair": pair,
                "label": label,
                "input_captures": [],
                "common_directional_sample": False,
                "label_attempts": [],
            }
            attempts = (
                session.query(ProspectiveLabelAttemptRecord)
                .filter_by(
                    pair_id=row.pair_id,
                )
                .order_by(ProspectiveLabelAttemptRecord.attempt)
                .all()
            )
            item["label_attempts"] = [
                {
                    "attempt": a.attempt,
                    "status": a.status,
                    "error_code": a.error_code,
                    "started_at": as_utc(a.started_at).isoformat(),
                    "finished_at": as_utc(a.finished_at).isoformat() if a.finished_at else None,
                }
                for a in attempts
            ]
            if not pair["exclusion_reason"]:
                data = pair["input"]
                cutoff = datetime.fromisoformat(data["cutoff_at"])
                deadline = datetime.fromisoformat(pair["deadline_at"])
                _require(cutoff <= datetime.fromisoformat(pair["admitted_at"]) <= now
                         and datetime.fromisoformat(pair["admitted_at"]) < deadline
                         and deadline == cutoff + timedelta(seconds=request.pair_deadline_seconds)
                         and deadline == datetime.fromisoformat(data["window_start_at"])
                         and deadline + HORIZONS[request.horizon]
                         == datetime.fromisoformat(pair["window_end_at"])
                         == datetime.fromisoformat(data["window_end_at"])
                         and all(data[k] == getattr(request, k)
                                 for k in ("market", "instrument", "horizon")),
                         "pair_input_window_or_scope_mismatch")
                _require(
                    {a["arm"] for a in pair["arms"]} == {"baseline", "candidate"},
                    "pair_arms_incomplete",
                )
                _require(
                    all(
                        a["recipe_hash"] == trial.definition[a["arm"]]["recipe_hash"]
                        for a in pair["arms"]
                    ),
                    "pair_label_recipe_mismatch",
                )
                item["input_captures"] = _input_receipts(session, pair, request, archive)
                if label and label["status"] == "completed":
                    result = label["result"]
                    _require(
                        result is not None
                        and any(
                            a.attempt == label["attempt"] and a.status == "completed"
                            for a in attempts
                        ),
                        "completed_attempt_missing",
                    )
                    evaluated = datetime.fromisoformat(result["evaluated_at"])
                    change, proofs = verify_archived_candles(
                        session,
                        context_id=f"analytical-observation:paired:{row.pair_id}:{label['attempt']}",
                        instrument=request.instrument,
                        start_at=datetime.fromisoformat(pair["deadline_at"]),
                        end_at=datetime.fromisoformat(pair["window_end_at"]),
                        evaluated_at=evaluated,
                        now=now,
                        archive=archive,
                    )
                    rescored = score_pair(
                        pair, trial, change, evaluated_at=evaluated, captures=proofs
                    )
                    _require(rescored == result, "pair_label_recalculation_mismatch")
                    item["common_directional_sample"] = all(
                        score["direction_correct"] is not None
                        for score in result["scores"].values()
                    )
            cases.append(item)
        body = {
            "schema_version": "prospective-ledger-snapshot.v1",
            "execution_scope": "directional_synthesis",
            "scorer_version": SCORER_VERSION,
            "trial": trial.model_dump(mode="json"),
            "cases": cases,
            "promotion_eligible": False,
            "limitations": [
                "direction_only_no_registered_brier",
                "event_independence_unproven",
                "linked_archives_do_not_prove_parser_semantics",
                "comparison_runtime_not_formal_dsh_baseline",
            ],
        }
        # Query time stays outside the identity: a repeat read of the same data is idempotent.
        return {
            "snapshot_hash": recipe_hash(body),
            "snapshot": body,
            "observed_at": now.isoformat(),
        }


def record_trial_snapshot(assets, snapshot, *, receipt_path):
    """Register verified, saved evidence; never relax the existing promotion gate."""
    body, digest = snapshot["snapshot"], snapshot["snapshot_hash"]
    _require(recipe_hash(body) == digest, "prospective_snapshot_hash_mismatch")
    saved = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
    _require(saved == snapshot, "prospective_snapshot_receipt_mismatch")
    request = TrialRequest.model_validate(body["trial"]["definition"]["request"])
    cases = body["cases"]
    admitted = [c for c in cases if not c["pair"]["exclusion_reason"]]
    _require(bool(admitted), "trial_has_no_admitted_inputs")
    common = [c for c in admitted if c["common_directional_sample"]]
    now = datetime.fromisoformat(snapshot["observed_at"])
    refs = [f"prospective://{body['trial']['trial_id']}/{c['pair']['pair_id']}" for c in admitted]
    metadata = dict(
        split="shadow",
        fixture_refs=refs,
        fixture_hashes={
            ref: c["pair"]["input_hash"] for ref, c in zip(refs, admitted, strict=True)
        },
        cutoff_rule="published_at <= observed_at <= received_at",
        label_rule="outcomes.available_at > received_at",
        leakage_audit="passed",
        authorization="owner:prospective-direction-audit",
        visibility="owner_only",
        source_mode="prospective",
        window_start_at=request.starts_at,
        window_end_at=request.ends_at,
        event_family_counts={"unclassified": len(admitted)},
    )
    manifest = EvaluationDatasetManifest(
        **metadata,
        dataset_id=f"paired_data_{digest[:32]}",
        manifest_hash=dataset_manifest_hash(**metadata),
        created_at=now,
    )
    experiment = ExperimentManifest(
        execution_scope=body.get("execution_scope"),
        experiment_id=f"paired_eval_{digest[:32]}",
        dataset_id=manifest.dataset_id,
        baseline_ref=request.baseline_candidate_id,
        candidate_refs=[request.candidate_id],
        status="running",
        created_at=now,
        strategy_version=request.domain_pack_ref,
        runtime_id="preregistered-paired-comparison",
        runtime_version=SCORER_VERSION,
        provider_id=None,
        model=None,
        schema_version="experiment.v1",
        random_seed=None,
        randomness_policy="provider_default",
        deadline_seconds=request.pair_deadline_seconds,
        max_cost_usd=None,
    )
    results = []
    for name, identity in (
        ("baseline", request.baseline_candidate_id),
        ("candidate", request.candidate_id),
    ):
        arms = [next(a for a in c["pair"]["arms"] if a["arm"] == name) for c in admitted]
        outcomes = [c["label"]["result"]["scores"][name]["direction_correct"] for c in common]
        latencies = sorted(a["result"]["latency_ms"] for a in arms if a["result"] is not None)
        costs = [a["result"].get("cost_usd") if a["result"] else None for a in arms]
        known_costs = all(v is not None and math.isfinite(v) and v >= 0 for v in costs)
        failures = Counter(
            c["pair"]["exclusion_reason"] for c in cases if c["pair"]["exclusion_reason"]
        )
        for c, arm in zip(admitted, arms, strict=True):
            if arm["status"] != "succeeded":
                failures[f"arm_{arm['status']}"] += 1
            elif arm["result"]["payload"]["direction"] not in {"long", "short"}:
                failures["non_directional"] += 1
            if not c["label"] or c["label"]["status"] != "completed":
                failures["label_unavailable"] += 1
        results.append(
            ExperimentResultView(
                result_id=f"{experiment.experiment_id}:{name}",
                experiment_id=experiment.experiment_id,
                candidate_id=identity,
                sample_count=len(common),
                brier_score=None,
                cost_usd=sum(costs) if known_costs else None,
                p95_latency_ms=latencies[math.ceil(len(latencies) * 0.95) - 1]
                if latencies
                else None,
                safety_violations=0,
                event_family_counts={"unclassified": len(common)},
                created_at=now,
                stage="shadow",
                failure_counts=dict(failures),
                evidence_coverage=len(common) / len(cases),
                directional_accuracy=sum(outcomes) / len(outcomes) if outcomes else None,
                raw_artifact_refs=[str(Path(receipt_path).resolve())],
                scorer_version=SCORER_VERSION,
            )
        )
    # Validate every projection before the first write. Incomplete staging stays running;
    # replaying this content-addressed snapshot resumes the existing idempotent operations.
    assets.register_dataset(manifest)
    assets.register_experiment(experiment)
    saved_results = [assets.record_result(result) for result in results]
    assets.set_experiment_status(experiment.experiment_id, "completed")
    return {
        "experiment_id": experiment.experiment_id,
        "dataset_id": manifest.dataset_id,
        "results": [r.model_dump(mode="json") for r in saved_results],
        "snapshot_hash": digest,
        "promotion_eligible": False,
    }


def save_trial_snapshot(snapshot, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Existing receipts must never silently become a different audit record.
    if path.exists():
        _require(
            json.loads(path.read_text(encoding="utf-8")) == snapshot,
            "prospective_snapshot_receipt_exists",
        )
        return
    with path.open("x", encoding="utf-8") as handle:
        handle.write(canonical_recipe(snapshot))
