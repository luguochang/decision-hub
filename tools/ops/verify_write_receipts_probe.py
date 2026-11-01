"""Verify an explicit isolated PC receipt probe using read-only SQLite and its journal.

No HTTP request, application startup, migration, model call or database write is made.
Browser navigation and appearance need separate CUA evidence; this script verifies
durable identities, controlled 503 faults, confirmation calls and absence of duplicates.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tools.ops.serve_write_receipts_probe import (  # noqa: E402
    DOMAIN,
    LABEL,
    OWNER,
    snapshot,
    validate_output,
)

STAGES = (
    "before", "after-feedback-error", "after-return-no-auto", "after-feedback-confirm",
    "after-decision-error", "after-reload-no-auto", "after-decision-confirm", "final",
)
NO_AUTO_PREVIOUS = {
    "after-return-no-auto": "after-feedback-error",
    "after-reload-no-auto": "after-decision-error",
}


def load_probe(seed_path: Path) -> tuple[dict, Path, list[dict]]:
    seed_path = seed_path.resolve()
    allowed = (REPO / "data/product-local/remediation").resolve()
    if not seed_path.is_relative_to(allowed) or seed_path.name != "seed.json":
        raise ValueError("seed must be an explicit probe seed beneath remediation")
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    if seed.get("isolation_label") != LABEL or any(
        seed.get(flag) is not False
        for flag in ("production_data", "proxy", "paid_provider", "worker_started")
    ):
        raise ValueError("seed does not declare the expected isolated receipt probe")
    if seed.get("owner") != OWNER or seed.get("domain_pack_ref") != DOMAIN:
        raise ValueError("probe owner/domain does not match the local contract")
    database = (seed_path.parent / "runtime/db/decision_hub.sqlite3").resolve()
    if Path(seed["database"]).resolve() != database or not database.is_file():
        raise ValueError("database must be exactly this probe's isolated runtime database")
    journal = json.loads((seed_path.parent / "requests.json").read_text(encoding="utf-8"))
    if not isinstance(journal, list):
        raise ValueError("request journal must be a list")
    return seed, database, journal


def verify(seed_path: Path, *, stage: str = "final", previous: Path | None = None,
           require_confirmed: bool = True) -> dict:
    if stage not in STAGES:
        raise ValueError("unknown receipt audit stage")
    if stage in NO_AUTO_PREVIOUS and previous is None:
        raise ValueError("a no-auto stage requires --previous pointing to its prior checks.json")
    seed, database, journal = load_probe(seed_path)
    current = snapshot(database)
    baseline = seed["baseline"]
    checks = []

    def check(name: str, passed: bool, evidence=None) -> None:
        checks.append({"name": name, "passed": bool(passed), "evidence": evidence})

    check("synthetic_control_report_not_changed", current["immutable_hashes"]
          == baseline["immutable_hashes"], current["immutable_hashes"])
    expected = {
        "feedback": 0 if stage == "before" else 1,
        "decision": 1 if stage in (
            "after-decision-error", "after-reload-no-auto", "after-decision-confirm", "final",
        ) else 0,
    }
    expected_confirmed = {
        "feedback": require_confirmed and stage not in (
            "before", "after-feedback-error", "after-return-no-auto",
        ),
        "decision": require_confirmed and stage in ("after-decision-confirm", "final"),
    }
    mutable_counts = {"workbench_feedback", "promotion_decisions"}
    check("other_tracked_counts_unchanged", all(
        count == baseline["counts"][table]
        for table, count in current["counts"].items() if table not in mutable_counts
    ), current["counts"])
    check("stage_has_exact_feedback_and_decision_counts", all(
        current["counts"][table] == baseline["counts"][table] + expected[kind]
        for kind, table in (("feedback", "workbench_feedback"),
                            ("decision", "promotion_decisions"))
    ), {"expected": expected,
        "actual": {table: current["counts"][table] for table in sorted(mutable_counts)}})
    check("no_model_execution", all(current["counts"][table] == 0 for table in (
        "run_calls", "research_tool_call_reservations", "evolution_planning_calls",
    )))
    check("reject_did_not_create_active_pointer", current["counts"]["active_pointers"] == 0)
    candidate_status = "rejected" if expected["decision"] else "candidate"
    check("seed_candidate_stage_status", len(current["candidates"]) == 1
          and current["candidates"][0]["candidate_id"] == seed["candidate"]["candidate_id"]
          and current["candidates"][0]["status"] == candidate_status, candidate_status)

    if stage in NO_AUTO_PREVIOUS:
        previous_path = previous.resolve()
        allowed = (REPO / "data/product-local/remediation").resolve()
        if not previous_path.is_relative_to(allowed) or previous_path.name != "checks.json":
            raise ValueError("previous must be an explicit remediation checks.json")
        prior = json.loads(previous_path.read_text(encoding="utf-8"))
        if prior.get("seed") != str(seed_path.resolve()) or prior.get("stage") \
                != NO_AUTO_PREVIOUS[stage] or prior.get("passed") is not True:
            raise ValueError("previous snapshot must be this probe's successful preceding stage")
        prior_writes = [entry for entry in prior["request_journal"]
                        if entry.get("method") not in {"GET", "HEAD"}]
        current_writes = [entry for entry in journal
                          if entry.get("method") not in {"GET", "HEAD"}]
        check("no_automatic_write_since_prior_stage", current_writes == prior_writes,
              {"prior_write_count": len(prior_writes), "current_write_count": len(current_writes)})
        check("no_business_state_change_since_prior_stage", current == prior["current"])

    for kind, records, id_field in (
        ("feedback", current["feedback"], "feedback_id"),
        ("decision", current["decisions"], "decision_id"),
    ):
        writes = [entry for entry in journal if entry.get("kind") == kind
                  and entry.get("method") == "POST"]
        faults = [entry for entry in writes if entry.get("committed_response_withheld")]
        if not expected[kind]:
            check(f"{kind}_not_submitted_in_this_stage", not records and not faults
                  and not any(entry.get("application_status") == 200 for entry in writes))
            continue
        one_fault = len(faults) == 1
        check(f"{kind}_one_controlled_503_after_application_200", one_fault and all(
            entry.get("application_status") == 200 and entry.get("delivered_status") == 503
            and entry.get("expected_fault") is True
            for entry in faults
        ), [{key: entry.get(key) for key in (
            "sequence", "request_id", "application_status", "delivered_status",
        )} for entry in faults])
        if not one_fault or len(records) != 1:
            check(f"{kind}_durable_receipt_identity", False, "missing unique fault/record")
            continue
        original, row = faults[0], records[0]
        receipt = original["receipt"] if kind == "feedback" else original["receipt"]["decision"]
        fields = (
            ("feedback_id", "request_id", "target_type", "target_id", "created_by",
             "verdict", "notes") if kind == "feedback" else
            ("decision_id", "request_id", "domain_pack_ref", "candidate_id", "owner",
             "decision", "reason", "resulting_generation")
        )
        check(f"{kind}_durable_receipt_identity", all(
            row[field] == receipt[field] for field in fields
        ) and row["request_id"] == original["request_id"], {
            id_field: row[id_field], "request_id": row["request_id"],
        })
        successes = [entry for entry in writes if entry.get("application_status") == 200]
        check(f"{kind}_same_identity_for_all_successful_writes", all(
            entry["request_id"] == original["request_id"]
            and entry["owner_header"] == OWNER
            and entry["payload_sha256"] == original["payload_sha256"]
            for entry in successes
        ))
        if kind == "feedback":
            check("feedback_target_is_seed_report_revision", row["target_type"]
                  == "report_revision" and row["target_id"] == seed["report"]["revision_id"])
            check("feedback_same_idempotency_header", all(
                entry["idempotency_header"] == original["request_id"] for entry in successes
            ))
        else:
            check("decision_target_is_original_safe_reject", row["domain_pack_ref"] == DOMAIN
                  and row["candidate_id"] == seed["candidate"]["candidate_id"]
                  and row["decision"] == "reject" and row["owner"] == OWNER
                  and row["resulting_generation"] == 0
                  and json.loads(row["evaluation_refs_json"]) == [])
        confirmations = [entry for entry in journal if entry.get("kind") == kind
                         and entry.get("request_id") == original["request_id"]
                         and entry.get("owner_header") == OWNER
                         and entry.get("sequence", 0) > original["sequence"]
                         and entry.get("application_status") == 200
                         and entry.get("delivered_status") == 200
                         and not entry.get("committed_response_withheld")]
        exact_confirmations = []
        for entry in confirmations:
            value = entry["receipt"] if kind == "feedback" else entry["receipt"]["decision"]
            if value == receipt:
                exact_confirmations.append(entry)
        check(f"{kind}_original_receipt_confirmed", bool(exact_confirmations)
              or not expected_confirmed[kind], [{
                  "method": entry["method"], "path": entry["path"],
                  "sequence": entry["sequence"], "request_id": entry["request_id"],
              } for entry in exact_confirmations])

    return {
        "verified_at": datetime.now(UTC).isoformat(), "isolation_label": LABEL,
        "seed": str(seed_path.resolve()), "database": str(database),
        "database_access": "mode=ro; PRAGMA query_only=ON", "http_requests_made": 0,
        "browser_navigation_verified": False,
        "fault_semantics": "real commit 200 replaced by explicit HTTP503; not TCP disconnect",
        "stage": stage, "expected_faults": expected,
        "require_confirmed": expected_confirmed, "passed": all(c["passed"] for c in checks),
        "checks": checks, "current": current, "request_journal": journal,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--allow-unconfirmed", action="store_true")
    parser.add_argument("--stage", choices=STAGES, default="final")
    parser.add_argument("--previous", type=Path)
    args = parser.parse_args()
    try:
        root = validate_output(args.output)
        result = verify(args.seed, stage=args.stage, previous=args.previous,
                        require_confirmed=not args.allow_unconfirmed)
    except (ValueError, KeyError, OSError) as error:
        parser.error(str(error))
    root.mkdir(parents=True, exist_ok=False)
    (root / "checks.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "report": str(root / "checks.json"),
                      "http_requests_made": 0}, ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
