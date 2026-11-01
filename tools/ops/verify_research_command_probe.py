"""Read-only audit of an explicit isolated Run-command probe; no HTTP or startup.

CUA/navigation/screenshot evidence is separate. GETs here are only read from the
request journal; an untagged GET is not automatically proof of browser actions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tools.ops.serve_research_command_probe import (  # noqa: E402
    FAILURE_CODE,
    LABEL,
    OWNER,
    digest,
    snapshot,
    validate_output,
)

STAGES = (
    "before",
    "researching",
    "after-cancel-error",
    "after-cancel-return-no-auto",
    "after-cancel-confirm",
    "after-source-cancelled",
    "after-retry-held",
    "after-retry-away-no-auto",
    "after-retry-released",
    "after-retry-confirm",
    "after-child-failure",
    "final",
)
NO_AUTO = {
    "after-cancel-return-no-auto": "after-cancel-error",
    "after-retry-away-no-auto": "after-retry-held",
    "after-retry-released": "after-retry-away-no-auto",
}


def load_probe(seed_path: Path):
    seed_path = seed_path.resolve()
    allowed = (REPO / "data/product-local/remediation").resolve()
    if seed_path.name != "seed.json" or not seed_path.is_relative_to(allowed):
        raise ValueError("seed must be an explicit remediation seed.json")
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    if (
        seed.get("isolation_label") != LABEL
        or seed.get("owner") != OWNER
        or any(
            seed.get(flag) is not False
            for flag in ("production_data", "proxy", "paid_provider", "ai_success")
        )
        or seed.get("worker_role") != "actual_durable_research_failure_only"
    ):
        raise ValueError("not the expected isolated failure-only command probe")
    database = (seed_path.parent / "runtime/db/decision_hub.sqlite3").resolve()
    if Path(seed["database"]).resolve() != database or not database.is_file():
        raise ValueError("database must be exactly this seed's isolated runtime database")
    journal = json.loads((seed_path.parent / "requests.json").read_text(encoding="utf-8"))
    if not isinstance(journal, list):
        raise ValueError("invalid request journal")
    return seed, database, journal


def write_identity(entry):
    return {
        key: entry.get(key)
        for key in (
            "sequence",
            "method",
            "path",
            "received_at",
            "source_run_id",
            "request_id",
            "payload",
            "payload_sha256",
            "owner_header",
            "idempotency_header",
        )
    }


def verify(seed_path: Path, *, stage: str = "final", previous: Path | None = None):
    if stage not in STAGES:
        raise ValueError("unknown command audit stage")
    if stage in NO_AUTO and previous is None:
        raise ValueError("no-auto stage requires the successful prior checks.json")
    seed, database, journal = load_probe(seed_path)
    source_id = seed["source_run_id"]
    current = snapshot(database, source_id)
    baseline = seed["baseline"]
    runtime_events = json.loads(
        (seed_path.parent / "runtime-events.json").read_text(encoding="utf-8")
    )
    worker_events = json.loads(
        (seed_path.parent / "worker-events.json").read_text(encoding="utf-8")
    )
    worker_status = json.loads(
        (seed_path.parent / "worker-status.json").read_text(encoding="utf-8")
    )
    index = STAGES.index(stage)
    commands_count = 0 if index < 2 else 1 if index < 6 else 2
    expected_child_count = 1 if commands_count == 2 else 0
    checks = []

    def check(name, passed, evidence=None):
        checks.append({"name": name, "passed": bool(passed), "evidence": evidence})

    check(
        "original_frozen_history_hashes_unchanged",
        current["historical_hashes"] == baseline["historical_hashes"],
        current["historical_hashes"],
    )
    check(
        "no_model_dsh_report_or_notification",
        all(
            current["counts"][table] == 0
            for table in (
                "run_calls",
                "research_tool_call_reservations",
                "evolution_planning_calls",
                "dsh_session_links",
                "dsh_session_prompts",
                "artifacts",
                "forecasts",
                "report_revisions",
                "outbox",
                "research_results",
            )
        ),
        current["counts"],
    )
    check(
        "exact_durable_command_and_receipt_counts",
        len(current["commands"]) == commands_count
        and len(current["receipts"]) == commands_count
        and current["counts"]["product_commands"]
        == baseline["counts"]["product_commands"] + commands_count,
        commands_count,
    )
    source = next(row for row in current["runs"] if row["run_id"] == source_id)
    children = [row for row in current["runs"] if row["parent_run_id"] == source_id]
    check(
        "only_expected_child_created",
        len(children) == expected_child_count
        and current["counts"]["runs"] == baseline["counts"]["runs"] + expected_child_count,
        [row["run_id"] for row in children],
    )
    reference = next(row for row in current["runs"] if row["run_id"] == seed["reference_run_id"])
    original_reference = next(
        row for row in baseline["runs"] if row["run_id"] == seed["reference_run_id"]
    )
    check("read_only_reference_run_not_changed", reference == original_reference)
    check(
        "source_status_and_cancel_lease",
        (
            source["status"] in {"admitted", "running"}
            if index < 2
            else source["status"] == "cancelled"
            and source["error_code"] == "cancelled_by_owner"
            and source["finished_at"] is not None
            and source["lease_owner"] is None
            and source["lease_expires_at"] is None
        ),
        {key: source[key] for key in ("status", "error_code", "finished_at", "lease_owner")},
    )
    if stage == "researching":
        check(
            "real_worker_claim_and_runtime_entered",
            source["status"] == "running"
            and bool(source["lease_owner"])
            and bool(source["lease_expires_at"])
            and any(event["run_id"] == source_id for event in runtime_events),
        )

    successful_posts = [
        entry
        for entry in journal
        if entry["method"] == "POST" and entry.get("application_status") == 200
    ]
    receipt_views = {}
    for row in current["receipts"]:
        receipt = json.loads(row["result_json"])
        command, result = receipt["command"], receipt["result"]
        action, key = command["command"], row["request_id"]
        receipt_views[action] = receipt
        writes = [entry for entry in successful_posts if entry.get("request_id") == key]
        check(
            f"{action}_frozen_owner_target_key_hash_and_payload",
            bool(writes)
            and row["owner_id"] == receipt["owner_id"] == OWNER
            and row["kind"] == "research_command"
            and row["target_id"] == source_id
            and receipt["source_run_id"] == result["source_run_id"] == source_id
            and key == receipt["request_id"] == command["request_id"] == result["request_id"]
            and result["status"] == "accepted"
            and row["payload_hash"] == digest([OWNER, source_id, command])
            and all(
                entry["payload"] == command
                and entry["owner_header"] == OWNER
                and entry["idempotency_header"] == key
                for entry in writes
            ),
            {"request_id": key, "payload_hash": row["payload_hash"]},
        )
        record = next((item for item in current["commands"] if item["request_id"] == key), None)
        check(
            f"{action}_legacy_command_matches_receipt",
            record is not None
            and all(
                record[field] == value
                for field, value in (
                    ("run_id", source_id),
                    ("command", action),
                    ("reason", command["reason"]),
                    ("target_run_id", result["target_run_id"]),
                    ("status", "accepted"),
                )
            ),
        )
        if action == "cancel":
            faults = [entry for entry in writes if entry.get("committed_response_withheld")]
            check(
                "one_expected_cancel_commit200_delivered503",
                len(faults) == 1
                and faults[0]["application_status"] == 200
                and faults[0]["delivered_status"] == 503
                and faults[0].get("expected_fault") is True
                and faults[0]["receipt"] == result,
            )
        else:
            holds = [entry for entry in writes if entry.get("response_held")]
            check(
                "one_retry_actual_commit_response_hold",
                len(holds) == 1
                and holds[0]["receipt"] == result
                and not holds[0].get("gate_timed_out"),
                [
                    {
                        "delivered_status": entry["delivered_status"],
                        "received_at": entry["received_at"],
                        "delivered_at": entry.get("delivered_at"),
                    }
                    for entry in holds
                ],
            )
        required = (action == "cancel" and index >= 4) or (action == "retry" and index >= 9)
        gets = [
            entry
            for entry in journal
            if entry["method"] == "GET"
            and entry.get("request_id") == key
            and entry.get("source_run_id") == source_id
            and entry.get("owner_header") == OWNER
            and entry.get("delivered_status") == 200
            and entry.get("receipt") == receipt
            and not entry.get("evidence_only")
        ]
        check(
            f"{action}_exact_untagged_receipt_get",
            bool(gets) or not required,
            {
                "required": required,
                "sequences": [entry["sequence"] for entry in gets],
                "requires_separate_cua_proof": True,
            },
        )

    if children:
        child = children[0]
        receipt = receipt_views.get("retry", {})
        key = receipt.get("request_id", "")
        expected_id = "run_" + hashlib.sha256(key.encode()).hexdigest()[:32]
        check(
            "child_original_chain_and_new_key_identity",
            child["run_id"] == expected_id
            and child["run_id"] == receipt.get("result", {}).get("target_run_id")
            and child["event_id"] == source["event_id"]
            and child["study_id"] == source["study_id"]
            and child["strategy_version"] == "research.v1"
            and child["idempotency_key"] == f"research-command:{key}",
        )
        check(
            "child_does_not_inherit_library_scope_or_consent",
            not any(row["run_id"] == child["run_id"] for row in current["scopes"]),
        )
        entered = [event for event in runtime_events if event["run_id"] == child["run_id"]]
        check(
            "child_runtime_input_has_no_parent_library_content",
            all(not event["library_input_refs"] for event in entered),
            entered,
        )
        if index >= 10:
            check(
                "child_real_worker_failed_without_report",
                child["status"] == "failed"
                and child["error_code"] == FAILURE_CODE
                and child["lease_owner"] is None
                and child["lease_expires_at"] is None
                and bool(entered)
                and entered[-1]["kind"] == "runtime_failed"
                and not entered[-1].get("shutdown_release"),
                child["status"],
            )
    if index >= 5:
        check(
            "source_runtime_cooperatively_cancelled",
            any(
                event["run_id"] == source_id
                and event["kind"] == "cooperative_cancel"
                and event.get("cancelled_error_propagated") is True
                for event in runtime_events
            ),
            [event for event in runtime_events if event["run_id"] == source_id],
        )

    if stage in NO_AUTO:
        previous = previous.resolve()
        allowed = (REPO / "data/product-local/remediation").resolve()
        if not previous.is_relative_to(allowed) or previous.name != "checks.json":
            raise ValueError("previous must be an explicit remediation checks.json")
        prior = json.loads(previous.read_text(encoding="utf-8"))
        if (
            prior.get("seed") != str(seed_path.resolve())
            or prior.get("stage") != NO_AUTO[stage]
            or prior.get("passed") is not True
        ):
            raise ValueError("previous must be this probe's successful preceding stage")
        writes = [
            write_identity(entry) for entry in journal if entry["method"] not in {"GET", "HEAD"}
        ]
        old_writes = [
            write_identity(entry)
            for entry in prior["request_journal"]
            if entry["method"] not in {"GET", "HEAD"}
        ]
        check(
            "no_new_write_while_returning_or_receiving_late_response",
            writes == old_writes,
            {"previous_write_count": len(old_writes), "current_write_count": len(writes)},
        )
        check(
            "no_new_command_child_or_receipt",
            current["commands"] == prior["current"]["commands"]
            and current["receipts"] == prior["current"]["receipts"]
            and current["counts"]["runs"] == prior["current"]["counts"]["runs"],
        )

    return {
        "verified_at": datetime.now(UTC).isoformat(),
        "seed": str(seed_path.resolve()),
        "stage": stage,
        "isolation_label": LABEL,
        "database": str(database),
        "database_access": "mode=ro; PRAGMA query_only=ON",
        "http_requests_made": 0,
        "browser_navigation_verified": False,
        "late_commit_verified": False,
        "fault_semantics": "real cancel commit200 delivered explicit HTTP503; not TCP disconnect",
        "passed": all(row["passed"] for row in checks),
        "checks": checks,
        "current": current,
        "runtime_events": runtime_events,
        "worker_events": worker_events,
        "worker_status": worker_status,
        "request_journal": journal,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stage", choices=STAGES, default="final")
    parser.add_argument("--previous", type=Path)
    args = parser.parse_args()
    try:
        output = validate_output(args.output)
        result = verify(args.seed, stage=args.stage, previous=args.previous)
    except (ValueError, KeyError, OSError) as error:
        parser.error(str(error))
    output.mkdir(parents=True, exist_ok=False)
    (output / "checks.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "report": str(output / "checks.json"),
                "http_requests_made": 0,
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
