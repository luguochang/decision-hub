"""Register frozen comparison definitions or read them without executing a model."""

import argparse
import json
from pathlib import Path

from packages.evals.prospective_pairs import ProspectivePairService
from packages.evals.prospective_trials import ProspectiveTrialService, TrialRequest
from packages.kernel.decision_hub_kernel.application.evolution import EvolutionAssetService
from packages.kernel.decision_hub_kernel.persistence.db import Database, ProspectivePairRecord
from packages.orchestration.langgraph.evolution_executor import CandidateArtifactStore
from packages.runtime_adapters.candidate_runtime import CandidateConfigurationRuntime
from packages.runtime_adapters.langgraph_agent.runtime import LangGraphAgentRuntime


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--register", type=Path,
                        help="TrialRequest JSON; defaults to read-only list")
    action.add_argument("--pairs", help="Read the first 100 frozen pairs for this trial")
    parser.add_argument("--offset", type=int, default=0, help="Offset for read-only pair pages")
    args = parser.parse_args()
    if args.offset < 0 or (args.offset and not args.pairs):
        parser.error("--offset requires --pairs and must be nonnegative")
    path = args.database.resolve()
    if not path.is_file():
        parser.error("database must already exist; this command does not initialize a database")
    mode = "rw" if args.register else "ro"
    database = Database(f"sqlite+pysqlite:///file:{path.as_posix()}?mode={mode}&uri=true")
    service = ProspectiveTrialService(database, artifact_root=args.artifact_root)
    if args.register:
        request = TrialRequest.model_validate_json(args.register.read_bytes())
        assets = EvolutionAssetService(database)
        store = CandidateArtifactStore(args.artifact_root)
        base = LangGraphAgentRuntime()

        def bound_runtime(candidate_id):
            candidate = assets.get_candidate(candidate_id)
            if candidate is None:
                raise ValueError("trial_candidate_unavailable")
            if candidate.content_ref:
                return CandidateConfigurationRuntime(base, store.load_candidate(candidate))
            return base

        result = service.register(request, bound_runtime(request.baseline_candidate_id),
                                  bound_runtime(request.candidate_id))
        output = {"registered": result.model_dump(mode="json"), "model_calls": 0,
                  "execution_started": False, "promotion_performed": False}
    elif args.pairs:
        service.get(args.pairs)
        pairs = ProspectivePairService(database, service)
        with database.session() as session:
            identities = [row[0] for row in session.query(ProspectivePairRecord.pair_id).filter_by(
                trial_id=args.pairs,
            ).order_by(ProspectivePairRecord.source_recorded_at,
                       ProspectivePairRecord.pair_id).offset(args.offset).limit(101)]
        output = {"pairs": [pairs.get(identity) for identity in identities[:100]],
                  "read_only": True, "has_more": len(identities) > 100,
                  "next_offset": args.offset + 100 if len(identities) > 100 else None}
    else:
        output = {"trials": [view.model_dump(mode="json") for view in service.list()],
                  "read_only": True}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
