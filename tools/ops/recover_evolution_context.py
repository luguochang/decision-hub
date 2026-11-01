"""Preview or enqueue a linked recovery; never reset the original failed task."""

import argparse
import json
from pathlib import Path

from packages.kernel.decision_hub_kernel.application.live_observation import EvolutionJobService
from packages.kernel.decision_hub_kernel.persistence.db import Database, EvolutionJobRecord


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--enqueue", action="store_true")
    parser.add_argument("--revision", choices=("v2", "v3"), default="v2")
    args = parser.parse_args()
    if not args.database.is_file():
        raise ValueError("existing_database_required")
    database = Database(f"sqlite+pysqlite:///{args.database.resolve().as_posix()}")
    with database.session() as session:
        source = session.get(EvolutionJobRecord, args.job_id)
        if source is None or source.status != "failed" or source.candidate_id is not None:
            raise ValueError("context_recovery_requires_failed_plan_without_candidate")
        result = {
            "source_job_id": source.job_id,
            "original_status": source.status,
            "original_attempt": source.attempt,
            "input_count": len(json.loads(source.input_refs_json)),
            "enqueued": args.enqueue,
        }
    if args.enqueue:
        job = EvolutionJobService(database).enqueue_context_recovery(
            args.job_id, revision=args.revision,
        )
        result.update(recovery_job_id=job.job_id, recovery_status=job.status)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
