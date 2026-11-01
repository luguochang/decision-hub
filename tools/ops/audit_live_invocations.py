"""Audit managed live tool schemas and server-owned timing against DSH archives."""

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from packages.contracts_py.decision_hub_contracts import ResearchCapabilityInvocation
from packages.kernel.decision_hub_kernel.application.research_invocation import ROLE_PREFIX, SCHEMA
from packages.kernel.decision_hub_kernel.application.research_method import canonical_recipe
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    RunRoleOutputRecord,
    as_utc,
)
from packages.runtime_adapters.dsh_runtime.session_archive import load_dsh_session_archive
from tools.ops.audit_dsh_catalog import discover_archives

CONTROLS = {"mode", "round", "observed_at", "requested_observed_at", "cutoff_at", "deadline_at"}


def audit(database, sessions, run_id):
    with database.session() as session:
        link = session.get(DshSessionLinkRecord, run_id)
        if link is None or link.deadline_at is None:
            raise ValueError("live_link_required")
        root, deadline = link.dsh_session_id, as_utc(link.deadline_at)
        rows = (
            session.query(RunRoleOutputRecord)
            .filter(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.role.startswith(ROLE_PREFIX),
            )
            .all()
        )
        receipts = {}
        for row in rows:
            if (
                row.schema_version != SCHEMA
                or row.payload_hash != hashlib.sha256(row.payload_json.encode()).hexdigest()
            ):
                raise ValueError("timing_receipt_corrupt")
            body = json.loads(row.payload_json)
            if body["request_id"] in receipts:
                raise ValueError("duplicate_timing_identity")
            receipts[body["request_id"]] = body
    candidates, selected, errors = discover_archives(sessions, root)
    schemas, calls = [], []
    for sid in sorted(selected):
        meta, path = candidates[sid]
        archive = load_dsh_session_archive(path, expected_session_id=sid)
        for event in archive.events:
            if event.get("time", 0) < meta["createdAt"]:
                continue
            if event["type"] == "request/header":
                for tool in event["data"]["header"].get("tools", []):
                    if tool.get("name") == "decision_hub_research":
                        leaked = CONTROLS & set(tool["parameters"].get("properties", {}))
                        schemas.append({"session_id": sid, "model_time_fields": sorted(leaked)})
            if event["type"] != "tool/call":
                continue
            data = event["data"]
            if data.get("name") != "decision_hub_research":
                continue
            arguments = json.loads(data["arguments"])
            intent = ResearchCapabilityInvocation.model_validate(
                {
                    **{k: v for k, v in arguments.items() if k not in CONTROLS},
                    "schema_version": "research-capability-invocation.v1",
                    "request_id": data["callId"],
                    "research_session_id": root,
                }
            )
            digest = hashlib.sha256(
                canonical_recipe(intent.model_dump(mode="json")).encode()
            ).hexdigest()
            receipt = receipts.get(data["callId"])
            checks = {
                "timing_present": receipt is not None,
                "intent_matches": receipt is not None and receipt["intent_hash"] == digest,
                "linked_deadline_matches": receipt is not None
                and datetime.fromisoformat(receipt["deadline_at"]) == deadline,
                "identity_matches": receipt is not None
                and receipt["run_id"] == run_id
                and receipt["research_session_id"] == root,
                "issued_within_deadline": receipt is not None
                and datetime.fromisoformat(receipt["issued_at"]) <= deadline,
            }
            calls.append({"session_id": sid, "request_id": data["callId"], "checks": checks})
    checks = {
        "archive_headers_readable": errors == 0,
        "model_time_fields_removed": bool(schemas)
        and not any(s["model_time_fields"] for s in schemas),
        "calls_bound": bool(calls) and all(all(c["checks"].values()) for c in calls),
        "all_receipts_linked": set(receipts) == {c["request_id"] for c in calls},
    }
    return {
        "run_id": run_id,
        "checks": checks,
        "calls": calls,
        "schemas": schemas,
        "scope": "managed live invocation controls; not evidence correctness or Provider identity",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--sessions", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.database.is_file() or args.output.exists():
        raise ValueError("existing_database_and_new_output_required")
    db = Database(f"sqlite+pysqlite:///file:{args.database.resolve().as_posix()}?mode=ro&uri=true")
    try:
        proof = audit(db, args.sessions, args.run_id)
    finally:
        db.engine.dispose()
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(proof, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"checks": proof["checks"], "calls": len(proof["calls"])}))
    return 0 if all(proof["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
