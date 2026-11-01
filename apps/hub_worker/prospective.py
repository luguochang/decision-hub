"""Dedicated comparison worker, explicitly bound to one preregistered trial."""

from pathlib import Path

from packages.evals.prospective_labels import ProspectiveLabelService
from packages.evals.prospective_pairs import ProspectivePairService
from packages.evals.prospective_trials import ProspectiveTrialService
from packages.kernel.decision_hub_kernel.application.evolution import EvolutionAssetService
from packages.orchestration.langgraph.evolution_executor import CandidateArtifactStore
from packages.provider_adapters.market.okx import OKXPublicMarketAdapter
from packages.runtime_adapters.candidate_runtime import CandidateConfigurationRuntime
from packages.runtime_adapters.langgraph_agent.runtime import LangGraphAgentRuntime


class ProspectiveWorker:
    def __init__(self, pairs, trial_id, baseline_runtime, candidate_runtime, labels=None):
        self.pairs, self.trial_id = pairs, trial_id
        self.baseline_runtime, self.candidate_runtime = baseline_runtime, candidate_runtime
        self.labels = labels

    async def tick(self):
        recovered = self.pairs.recover_expired(self.trial_id)
        pair_id = self.pairs.pending_pair(self.trial_id)
        if pair_id is None:
            pair_id = self.pairs.admit_next(self.trial_id)
        if pair_id is None:
            return {"status": "no_input", "recovered_arms": recovered}
        view = await self.pairs.execute(pair_id, baseline_runtime=self.baseline_runtime,
                                        candidate_runtime=self.candidate_runtime)
        return {"pair_id": pair_id, "status": view["status"], "recovered_arms": recovered}


def build_prospective_worker(database, artifact_root: Path, trial_id: str):
    if not trial_id:
        raise ValueError("prospective_trial_id_required")
    trials = ProspectiveTrialService(database, artifact_root=artifact_root)
    view = trials.get(trial_id)
    base = LangGraphAgentRuntime()
    store, assets = CandidateArtifactStore(artifact_root), EvolutionAssetService(database)

    def runtime(arm):
        candidate = assets.get_candidate(view.definition[arm]["candidate_id"])
        if candidate is None:
            raise ValueError("trial_candidate_unavailable")
        bound = (CandidateConfigurationRuntime(base, store.load_candidate(candidate))
                 if candidate.content_ref else base)
        from packages.runtime_adapters.execution_recipe import assert_recipe

        assert_recipe(bound, view.definition[arm]["recipe"])
        return bound

    pairs = ProspectivePairService(database, trials, data_roots=[artifact_root.parent])
    labels = ProspectiveLabelService(
        database, pairs, OKXPublicMarketAdapter(), artifact_root.parent,
    )
    return ProspectiveWorker(pairs, trial_id, runtime("baseline"), runtime("candidate"), labels)
