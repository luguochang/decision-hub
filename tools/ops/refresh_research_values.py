"""Append corrected value snapshots for terminal Runs; preserve original evaluations."""

import argparse
import hashlib
import json
from pathlib import Path

from packages.kernel.decision_hub_kernel.application.research_value import (
    ResearchValueEvaluationService,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    ResearchResultRecord,
    ResearchValueEvaluationRecord,
    RunRecord,
)


def refresh(database, run_ids, *, apply=False):
    run_ids = list(dict.fromkeys(run_ids))
    with database.session() as session:
        for run_id in run_ids:
            run = session.get(RunRecord, run_id)
            if (
                run is None or run.strategy_version != "research.v1"
                or run.status not in {"failed", "cancelled", "degraded", "completed"}
            ):
                raise ValueError("terminal_research_run_required")
        rows = session.query(ResearchValueEvaluationRecord).filter(
            ResearchValueEvaluationRecord.run_id.in_(run_ids)
        ).all()
        before = {
            r.evaluation_id: hashlib.sha256(r.payload_json.encode()).hexdigest() for r in rows
        }
    if not apply:
        return {"apply": False, "run_ids": run_ids, "old_evaluations": len(before)}
    service = ResearchValueEvaluationService(database, pack_root=Path("unused"))
    values = []
    for run_id in run_ids:
        item = service.evaluate_current(run_id)
        values.append(item.model_dump(mode="json"))
    with database.session() as session:
        preserved = all(
            (row := session.get(ResearchValueEvaluationRecord, key)) is not None
            and hashlib.sha256(row.payload_json.encode()).hexdigest() == digest
            for key, digest in before.items()
        )
    if not preserved:
        raise RuntimeError("original_evaluation_changed")
    return {
        "apply": True, "old_evaluations": len(before), "old_evaluations_preserved": True,
        "appended": sum(item["evaluation_id"] not in before for item in values),
        "evaluations": values,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--run-id", action="append", default=[])
    parser.add_argument("--failed-without-result", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    path = args.database.resolve()
    if not path.is_file() or args.output.exists():
        parser.error("existing database and new output path required")
    uri = (
        f"sqlite+pysqlite:///{path.as_posix()}" if args.apply
        else f"sqlite+pysqlite:///file:{path.as_posix()}?mode=ro&uri=true"
    )
    db = Database(uri)
    run_ids = args.run_id
    if args.failed_without_result:
        with db.session() as session:
            run_ids.extend(row.run_id for row in session.query(RunRecord).outerjoin(
                ResearchResultRecord, RunRecord.run_id == ResearchResultRecord.run_id
            ).filter(
                RunRecord.strategy_version == "research.v1", RunRecord.status == "failed",
                ResearchResultRecord.run_id.is_(None),
            ).order_by(RunRecord.created_at, RunRecord.run_id))
    try:
        result = refresh(db, run_ids, apply=args.apply)
    finally:
        db.engine.dispose()
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k != "evaluations"}))


if __name__ == "__main__":
    main()
