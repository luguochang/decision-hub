"""Read-only reconciliation of reviewed document inputs, DSH prompt and frozen report."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from apps.hub_worker.research import _library_candidates
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionPromptRecord,
    ResearchDocumentVersionRecord,
    ResearchResultRecord,
)


def verify(database_path, api_url, run_id, *, expected_candidates, expected_included,
           expected_excluded):
    database = Database(
        f"sqlite+pysqlite:///file:{database_path.resolve().as_posix()}?mode=ro&uri=true"
    )
    try:
        candidates = {item.evidence_id: item for item in _library_candidates(database, run_id)}
        with httpx.Client(trust_env=False, timeout=30) as client:
            def get(path):
                response = client.get(api_url.rstrip("/") + path)
                response.raise_for_status()
                return response.json()

            detail = get(f"/v1/research/runs/{run_id}")
            scope = get(f"/v1/research-library/context/{run_id}")
            if not scope or not scope["allow_remote_library_content"]:
                raise ValueError("explicit_library_scope_required")
            receipts = {item["evidence_id"]: item for item in scope["context_inputs"]}
            if len(candidates) != expected_candidates or len(receipts) != expected_included:
                raise ValueError("candidate_or_input_count_mismatch")
            if (len(receipts) != len(scope["context_inputs"])
                    or not receipts.keys() <= candidates.keys()):
                raise ValueError("duplicate_or_unknown_input")
            with database.session() as session:
                versions = [session.get(ResearchDocumentVersionRecord, identity)
                            for identity in scope["selected_version_ids"]]
                for version in versions:
                    response = client.get(
                        api_url.rstrip("/")
                        + f"/v1/research-library/versions/{version.version_id}/original"
                    )
                    response.raise_for_status()
                    if hashlib.sha256(response.content).hexdigest() != version.original_file_hash:
                        raise ValueError("original_file_hash_mismatch")
                prompts = session.query(DshSessionPromptRecord).filter_by(run_id=run_id).order_by(
                    DshSessionPromptRecord.generation
                ).all()
                if not prompts:
                    raise ValueError("dsh_prompt_not_yet_persisted")
                prompt = prompts[0].prompt
                control = json.loads(prompt.split(
                    "Canonical control request (untrusted text is replaced by typed references):\n",
                    1,
                )[1].split("\nBEGIN_UNTRUSTED_RESEARCH_DATA", 1)[0])
                untrusted = json.loads(prompt.split("BEGIN_UNTRUSTED_RESEARCH_DATA\n", 1)[1]
                                       .split("\nEND_UNTRUSTED_RESEARCH_DATA", 1)[0])
                texts = {item["ref"]: item["text"] for item in untrusted
                         if item["kind"] == "evidence_excerpt"}
                controls = {item["evidence_id"]: item for item in control["input_evidence"]}
                if control["run_id"] != run_id:
                    raise ValueError("prompt_run_mismatch")
                for identity, receipt in receipts.items():
                    text = receipt["excerpt"]
                    if hashlib.sha256(text.encode()).hexdigest() != receipt["excerpt_hash"]:
                        raise ValueError("input_excerpt_hash_mismatch")
                    candidate = candidates[identity]
                    if receipt["original_file_hash"] != candidate.source_content_hash:
                        raise ValueError("input_original_hash_mismatch")
                    if not receipt["locator_refs"]:
                        raise ValueError("input_locator_missing")
                    if texts.get(controls[identity]["excerpt"]) != text:
                        raise ValueError("dsh_prompt_input_differs_from_receipt")
                initial = control["context_manifest"]
                initial_excluded = {item["evidence_ref"] for item in initial["excluded_inputs"]
                                    if item["reason"] == "context_budget"}
                if (candidates.keys() - receipts.keys()) != (initial_excluded & candidates.keys()):
                    raise ValueError("initial_budget_exclusions_mismatch")
                result = session.get(ResearchResultRecord, run_id)
                proof = {
                    "checked_at": datetime.now(UTC).isoformat(), "run_id": run_id,
                    "run_status": detail["run"]["status"],
                    "artifact_id": detail["run"]["artifact_id"],
                    "runtime_id": detail["run"]["runtime_id"],
                    "candidate_count": len(candidates), "input_count": len(receipts),
                    "initial_budget_excluded": len(initial_excluded & candidates.keys()),
                    "prompt_generation": prompts[0].generation,
                    "receipt_prompt_text_equal": True, "original_hashes_match": True,
                    "input_hashes_match": True, "input_locators_present": True,
                    "selected_version_ids": scope["selected_version_ids"],
                }
                if result is None or detail["context_manifest"] is None:
                    return {**proof, "status": "pending", "reason": "frozen_result_not_available"}
                frozen = json.loads(result.payload_json)
                if hashlib.sha256(result.payload_json.encode()).hexdigest() != result.payload_hash:
                    raise ValueError("frozen_result_hash_mismatch")
                manifest = detail["context_manifest"]
                if frozen["context_manifest"] != manifest:
                    raise ValueError("api_manifest_differs_from_frozen_result")
                effective = set(manifest["effective_inputs"]) & candidates.keys()
                excluded = {item["evidence_ref"] for item in manifest["excluded_inputs"]
                            if item["reason"] == "context_budget"} & candidates.keys()
                if (effective != receipts.keys() or effective & excluded
                        or effective | excluded != candidates.keys()
                        or len(excluded) != expected_excluded):
                    raise ValueError("final_library_budget_partition_mismatch")
                if (not proof["artifact_id"]
                        or proof["run_status"] not in {"completed", "research_only"}):
                    raise ValueError("research_did_not_commit_report")
                return {
                    **proof, "status": "passed", "final_budget_excluded": len(excluded),
                    "effective_ids": sorted(effective), "excluded_ids": sorted(excluded),
                    "frozen_report_hash": result.payload_hash,
                    "accepted_evidence_count": detail["action_summary"]["accepted_evidence_count"],
                    "total_tool_calls": detail["total_tool_calls"],
                    "final_run_failure": detail["run"]["failure"],
                }
    finally:
        database.engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:8047")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-candidates", type=int, required=True)
    parser.add_argument("--expected-included", type=int, required=True)
    parser.add_argument("--expected-excluded", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    proof = verify(args.database, args.api_url, args.run_id,
                   expected_candidates=args.expected_candidates,
                   expected_included=args.expected_included,
                   expected_excluded=args.expected_excluded)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(json.dumps(proof, ensure_ascii=False, indent=2).encode())
    summary = {k: v for k, v in proof.items() if not k.endswith("_ids")}
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if proof["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
