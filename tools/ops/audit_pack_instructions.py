"""Read-only proof that the linked Host received the frozen Pack prompts."""

import argparse
import hashlib
import json
from pathlib import Path

from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    DshSessionPromptRecord,
)
from packages.runtime_adapters.dsh_runtime.session_archive import (
    _message_texts,
    load_dsh_session_archive,
)
from tools.ops.audit_research_method import audit as audit_method


def verify_received_prompts(prompts, events):
    received = {}
    for index, event in enumerate(events):
        if event.get("type") in {"user/message", "agent/inbox/spliced"}:
            for text in _message_texts(event.get("data")):
                received.setdefault(text, index)
    return [{"generation": generation,
             "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
             "host_received_exact_prompt": prompt in received,
             "model_activity_after_receipt": prompt in received and any(
                 e.get("type") in {"request/header", "assistant/chunk"}
                 for e in events[received[prompt] + 1:]
             )} for generation, prompt in prompts]


def audit(database, sessions, run_id):
    method = audit_method(database, run_id)
    if method.get("research_policy_status") != "verified":
        raise ValueError("frozen_pack_prompt_not_verified")
    with database.session() as session:
        link = session.get(DshSessionLinkRecord, run_id)
        if link is None:
            raise ValueError("dsh_link_missing")
        root = link.dsh_session_id
        prompts = [(r.generation, r.prompt) for r in session.query(DshSessionPromptRecord)
                   .filter_by(run_id=run_id).order_by(DshSessionPromptRecord.generation).all()]
    paths = list(sessions.glob(f"*/{root}/session.jsonl.zstd"))
    if len(paths) != 1 or not paths[0].resolve().is_relative_to(sessions.resolve()):
        raise ValueError("root_archive_not_unique_or_outside_root")
    archive = load_dsh_session_archive(paths[0], expected_session_id=root)
    receipts = verify_received_prompts(prompts, archive.events)
    passed = bool(receipts) and all(
        r["host_received_exact_prompt"] and r["model_activity_after_receipt"] for r in receipts
    ) and method["status"] == "submission_receipt_verified"
    return {"run_id": run_id, "session_id": root, "passed": passed,
            "method_checks": method["checks"], "prompts": receipts,
            "archive_sha256": archive.archive_sha256,
            "scope": "Root Host received frozen prompts and logged subsequent model activity; "
                     "does not prove instruction adherence, child-role delegation or performance."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--sessions", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.database.is_file() or args.output.exists():
        raise ValueError("existing_database_and_new_output_required")
    database = Database(f"sqlite+pysqlite:///file:{args.database.resolve().as_posix()}?mode=ro&uri=true")
    try:
        proof = audit(database, args.sessions, args.run_id)
    finally:
        database.engine.dispose()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(proof, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"passed": proof["passed"], "prompts": len(proof["prompts"])}))
    return 0 if proof["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
