"""Read or explicitly collect due paired market labels. No model calls or promotion."""

import argparse
import asyncio
import json
from pathlib import Path

from packages.evals.prospective_labels import ProspectiveLabelService
from packages.evals.prospective_pairs import ProspectivePairService
from packages.evals.prospective_trials import ProspectiveTrialService
from packages.kernel.decision_hub_kernel.persistence.db import Database, ProspectivePairRecord
from packages.provider_adapters.market.okx import OKXPublicMarketAdapter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument(
        "--collect", action="store_true", help="Fetch at most one due market window"
    )
    args = parser.parse_args()
    if args.offset < 0:
        parser.error("offset must be non-negative")
    path = args.database.resolve()
    if not path.is_file():
        parser.error("existing database required; migrate explicitly before running")
    mode = "rw" if args.collect else "ro"
    db = Database(f"sqlite+pysqlite:///file:{path.as_posix()}?mode={mode}&uri=true")
    trials = ProspectiveTrialService(db, artifact_root=args.data_root / "evolution")
    trials.get(args.trial_id)
    pairs = ProspectivePairService(db, trials)
    labels = ProspectiveLabelService(db, pairs, OKXPublicMarketAdapter(), args.data_root)
    completed = asyncio.run(labels.process_due(args.trial_id, limit=1)) if args.collect else 0
    with db.session() as session:
        identities = [
            row[0]
            for row in session.query(ProspectivePairRecord.pair_id)
            .filter_by(
                trial_id=args.trial_id,
            )
            .order_by(ProspectivePairRecord.source_recorded_at, ProspectivePairRecord.pair_id)
            .offset(args.offset)
            .limit(101)
        ]
    views = [labels.get(identity) for identity in identities[:100]]
    print(
        json.dumps(
            {
                "read_only": not args.collect,
                "collected": completed,
                "offset": args.offset,
                "has_more": len(identities) > 100,
                "labels": [view for view in views if view is not None],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if args.collect and any(v and v["status"] == "retry" for v in views) else 0


if __name__ == "__main__":
    raise SystemExit(main())
