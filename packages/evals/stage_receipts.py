"""Durable evaluation bindings and verified completion; never infer old receipts."""

import hashlib
import json
from pathlib import Path

from packages.contracts_py.decision_hub_contracts.models import ExperimentResultView
from packages.evals.runner import SCORER_VERSION
from packages.kernel.decision_hub_kernel.application.evolution import EvolutionAssetService
from packages.kernel.decision_hub_kernel.persistence.db import (
    EvaluationStageReceiptRecord,
    ExperimentResultRecord,
    utcnow,
)
from packages.runtime_adapters.execution_recipe import canonical_recipe, recipe_hash, runtime_recipe
from tools.replay.run_fixture import REPLAY_SCORER_VERSION


class EvaluationStageReceipts:
    def __init__(self, database, *, clock=utcnow):
        self.database, self.clock = database, clock

    @staticmethod
    def binding(plan):
        return {
            "schema_version": "evaluation-stage-binding.v2",
            "scorer_version": SCORER_VERSION,
            "replay_scorer_version": REPLAY_SCORER_VERSION,
            "dataset": plan.dataset.model_dump(mode="json"),
            "experiment": plan.experiment.model_dump(mode="json", exclude={"status"}),
            "strategy_versions": dict(plan.strategy_versions),
            "runtimes": {key: runtime_recipe(value) for key, value in plan.runtimes.items()},
        }

    def prepare(self, plan):
        binding = self.binding(plan)
        if [str(path) for path in plan.fixture_paths] != plan.dataset.fixture_refs:
            raise ValueError("evaluation_stage_input_changed")
        for path in plan.fixture_paths:
            if hashlib.sha256(path.read_bytes()).hexdigest() != plan.dataset.fixture_hashes.get(
                str(path)
            ):
                raise ValueError("evaluation_stage_input_changed")
        with self.database.session() as session:
            row = session.get(EvaluationStageReceiptRecord, plan.experiment.experiment_id)
            if row:
                if (row.completion_hash is None) != (row.completion_json is None):
                    raise ValueError("evaluation_stage_completion_changed")
                if (
                    row.binding_hash != recipe_hash(binding)
                    or recipe_hash(json.loads(row.binding_json)) != row.binding_hash
                ):
                    raise ValueError("evaluation_stage_binding_changed")
            else:
                # A prior unreceipted result cannot acquire a new execution identity.
                if (
                    session.query(ExperimentResultRecord)
                    .filter_by(
                        experiment_id=plan.experiment.experiment_id,
                    )
                    .count()
                ):
                    raise ValueError("evaluation_stage_legacy_receipt_missing")
                session.add(
                    EvaluationStageReceiptRecord(
                        experiment_id=plan.experiment.experiment_id,
                        binding_hash=recipe_hash(binding),
                        binding_json=canonical_recipe(binding),
                        created_at=self.clock(),
                    )
                )

    def completed(self, plan):
        self.prepare(plan)
        missing = []
        with self.database.session() as session:
            row = session.get(EvaluationStageReceiptRecord, plan.experiment.experiment_id)
            if row.completion_json is None:
                return None
            body = json.loads(row.completion_json)
            if recipe_hash(body) != row.completion_hash:
                raise ValueError("evaluation_stage_completion_changed")
            results = [ExperimentResultView.model_validate(x) for x in body["results"]]
            for result in results:
                stored = session.get(ExperimentResultRecord, result.result_id)
                if stored is None:
                    missing.append(result)
                elif EvolutionAssetService._result(stored) != result:
                    raise ValueError("evaluation_stage_result_changed")
            for path, digest in body["artifacts"].items():
                if (
                    not Path(path).is_file()
                    or hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest
                ):
                    raise ValueError("evaluation_stage_artifact_changed")
        # The sealed receipt precedes ledger projection. Recover a crash between
        # these writes without executing the model or replacing an existing result.
        assets = EvolutionAssetService(self.database)
        for result in missing:
            assets.record_result(result)
        return results

    def complete(self, plan, results):
        self.prepare(plan)
        expected = {plan.experiment.baseline_ref, *plan.experiment.candidate_refs}
        if len(results) != len(expected) or {r.candidate_id for r in results} != expected:
            raise ValueError("evaluation_stage_result_set_mismatch")
        if any(r.experiment_id != plan.experiment.experiment_id for r in results):
            raise ValueError("evaluation_stage_result_experiment_mismatch")
        if any(r.scorer_version != SCORER_VERSION for r in results):
            raise ValueError("evaluation_stage_scorer_mismatch")
        artifacts = {}
        for result in results:
            if not result.raw_artifact_refs:
                raise ValueError("evaluation_stage_artifacts_missing")
            for ref in result.raw_artifact_refs:
                artifacts[ref] = hashlib.sha256(Path(ref).read_bytes()).hexdigest()
        body = {
            "schema_version": "evaluation-stage-completion.v1",
            "results": [r.model_dump(mode="json") for r in results],
            "artifacts": artifacts,
        }
        with self.database.session() as session:
            for result in results:
                stored = session.get(ExperimentResultRecord, result.result_id)
                if stored is not None and EvolutionAssetService._result(stored) != result:
                    raise ValueError("evaluation_stage_result_changed")
            row = session.get(EvaluationStageReceiptRecord, plan.experiment.experiment_id)
            digest = recipe_hash(body)
            if row.completion_json and row.completion_hash != digest:
                raise ValueError("evaluation_stage_completion_reused")
            if row.completion_json is None:
                row.completion_json, row.completion_hash = canonical_recipe(body), digest
                row.completed_at = self.clock()
