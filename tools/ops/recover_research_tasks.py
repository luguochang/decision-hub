"""Recover full historical graph inputs from verified initial checkpoints.

Dry run by default. Imports use today's recorded_at and retain the original
checkpoint timestamp and hash; no execution or original row is rewritten.
"""

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from packages.contracts_py.decision_hub_contracts import ResearchSessionRequest
from packages.kernel.decision_hub_kernel.application.research_method import canonical_recipe
from packages.kernel.decision_hub_kernel.application.research_task import (
    ROLE,
    SCHEMA,
    read_task_request,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionPromptRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
    utcnow,
)


def recover(database, checkpoints, run_ids, *, apply=False):
    serializer = JsonPlusSerializer()
    connection = sqlite3.connect(Path(checkpoints).resolve().as_uri() + "?mode=ro", uri=True)
    candidates = []
    output = []
    try:
        with database.session() as session:
            for run_id in dict.fromkeys(run_ids):
                run = session.get(RunRecord, run_id)
                if (run is None
                    or run.status not in {"failed", "cancelled", "completed", "degraded"}):
                    raise ValueError("terminal_run_required")
                if read_task_request(session, run):
                    output.append({"run_id": run_id, "status": "already_saved"})
                    continue
                row = connection.execute(
                    "SELECT checkpoint_id,parent_checkpoint_id,type,checkpoint,metadata "
                    "FROM checkpoints WHERE thread_id=? AND checkpoint_ns='' "
                    "ORDER BY checkpoint_id LIMIT 1", (run_id,),
                ).fetchone()
                if row is None:
                    output.append({"run_id": run_id, "status": "checkpoint_missing"})
                    continue
                checkpoint_id, parent, kind, raw, metadata_raw = row
                checkpoint = serializer.loads_typed((kind, raw))
                metadata = json.loads(metadata_raw)
                initial = checkpoint.get("channel_values", {}).get("__start__", {})
                request_raw = initial.get("request")
                request = ResearchSessionRequest.model_validate(request_raw)
                original_at = as_utc(datetime.fromisoformat(checkpoint["ts"]))
                prompt = session.get(DshSessionPromptRecord, (run_id, 1))
                if (
                    parent is not None or metadata.get("source") != "input"
                    or metadata.get("step") != -1 or initial.get("rounds")
                    or checkpoint["id"] != checkpoint_id
                    or request.run_id != run_id or request.event_id != run.event_id
                    or request.current_round != 1 or not run.snapshot_id
                    or request.trigger_snapshot_id != run.snapshot_id
                    or original_at < as_utc(run.created_at)
                    or original_at > as_utc(run.finished_at or run.updated_at)
                    or (prompt and original_at > as_utc(prompt.created_at))
                ):
                    raise ValueError(f"initial_checkpoint_identity_or_time_invalid:{run_id}")
                # Require the graph's first normalized state to confirm the same
                # request, not a later partial state or an arbitrary input blob.
                normalized = connection.execute(
                    "SELECT type,checkpoint FROM checkpoints WHERE thread_id=? "
                    "AND checkpoint_ns='' AND parent_checkpoint_id=? "
                    "ORDER BY checkpoint_id LIMIT 1",
                    (run_id, checkpoint_id),
                ).fetchone()
                if normalized is None:
                    raise ValueError(f"initial_checkpoint_readback_missing:{run_id}")
                readback = serializer.loads_typed(normalized).get("channel_values", {})
                if ResearchSessionRequest.model_validate(readback.get("request")) != request:
                    raise ValueError(f"initial_checkpoint_readback_mismatch:{run_id}")
                payload = request.model_dump(mode="json")
                body = {
                    "schema_version": SCHEMA, "run_id": run_id, "request": payload,
                    "request_hash": hashlib.sha256(canonical_recipe(payload).encode()).hexdigest(),
                    "source": {
                        "kind": "initial_checkpoint", "checkpoint_id": checkpoint_id,
                        "original_recorded_at": original_at.isoformat(),
                        "payload_hash": hashlib.sha256(raw).hexdigest(),
                        "original_request_hash": hashlib.sha256(
                            canonical_recipe(request_raw).encode()
                        ).hexdigest(),
                        "archive_ref": str(Path(checkpoints).resolve()),
                    },
                }
                candidates.append(body)
                output.append({
                    "run_id": run_id, "status": "recoverable", "checkpoint_id": checkpoint_id,
                    "original_recorded_at": original_at.isoformat(),
                    "requirements": [item.requirement_id for item in request.evidence_requirements],
                    "request_hash": body["request_hash"],
                })
        if apply:
            with database.session() as session:
                if session.bind.dialect.name == "sqlite":
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                for body in candidates:
                    run = session.get(RunRecord, body["run_id"])
                    if read_task_request(session, run):
                        raise ValueError("concurrent_task_receipt_created")
                    encoded = canonical_recipe(body)
                    session.add(RunRoleOutputRecord(
                        output_id="task_" + hashlib.sha256(run.run_id.encode()).hexdigest()[:32],
                        run_id=run.run_id, role=ROLE, schema_version=SCHEMA,
                        payload_json=encoded,
                        payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
                        created_at=utcnow(),
                    ))
        return {"apply": apply, "appended": len(candidates) if apply else 0, "items": output}
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--checkpoints", type=Path, required=True)
    parser.add_argument("--run-id", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.database.is_file() or not args.checkpoints.is_file() or args.output.exists():
        parser.error("existing databases and new output path required")
    path = args.database.resolve().as_posix()
    uri = f"sqlite+pysqlite:///{path}" if args.apply else (
        f"sqlite+pysqlite:///file:{path}?mode=ro&uri=true"
    )
    db = Database(uri)
    try:
        result = recover(db, args.checkpoints, args.run_id, apply=args.apply)
    finally:
        db.engine.dispose()
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"appended": result["appended"], "runs": len(result["items"])}))


if __name__ == "__main__":
    main()
