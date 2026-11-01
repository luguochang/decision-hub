"""Verify and export a trial; ledger writes require explicit --record in an existing database."""

import argparse
import json
from pathlib import Path

from packages.evals.prospective_assets import (
    build_trial_snapshot,
    record_trial_snapshot,
    save_trial_snapshot,
)
from packages.kernel.decision_hub_kernel.application.evolution import EvolutionAssetService
from packages.kernel.decision_hub_kernel.persistence.db import Database, utcnow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--data-root",
        type=Path,
        action="append",
        required=True,
        help="Repeat for original source and label archive roots",
    )
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--output", type=Path, required=True, help="New receipt file")
    parser.add_argument(
        "--record", action="store_true", help="Also write existing evaluation ledger"
    )
    args = parser.parse_args()
    path = args.database.resolve()
    if not path.is_file():
        parser.error("existing database required; no automatic migrations")
    mode = "rw" if args.record else "ro"
    db = Database(f"sqlite+pysqlite:///file:{path.as_posix()}?mode={mode}&uri=true")
    try:
        snapshot = build_trial_snapshot(db, args.trial_id, data_roots=args.data_root, now=utcnow())
        # A repeated execution keeps the first receipt timestamp and identity.
        if args.output.exists():
            old = json.loads(args.output.read_text(encoding="utf-8"))
            if old["snapshot"] == snapshot["snapshot"]:
                snapshot = old
        save_trial_snapshot(snapshot, args.output)
        result = (
            record_trial_snapshot(EvolutionAssetService(db), snapshot, receipt_path=args.output)
            if args.record
            else None
        )
    except (ValueError, OSError, KeyError, TypeError) as exc:
        # Do not dump model payloads, source URLs or local secret-bearing paths on failure.
        public_codes = {
            "prospective_raw_archive_missing", "raw_capture_archive_corrupt",
            "archive_hash_mismatch", "pair_label_recalculation_mismatch",
            "pair_label_binding_mismatch", "pair_frozen_input_mismatch",
            "pair_input_capture_missing_or_late", "pair_parsed_archive_missing_or_late",
            "trial_has_no_pairs", "trial_has_no_admitted_inputs",
            "prospective_snapshot_receipt_exists", "prospective_snapshot_hash_mismatch",
            "prospective_snapshot_receipt_mismatch",
            "trial_candidate_binding_changed", "trial_candidate_artifact_changed",
            "pair_input_window_or_scope_mismatch",
        }
        code = str(exc) if str(exc) in public_codes else "verification_or_projection_failed"
        print(json.dumps({"status": "failed", "error_code": code,
                          "ledger_complete": False, "staging_may_exist": args.record}))
        return 1
    print(
        json.dumps(
            {
                "status": "recorded" if result else "verified_export",
                "snapshot_hash": snapshot["snapshot_hash"],
                "case_count": len(snapshot["snapshot"]["cases"]),
                "common_directional_samples": sum(
                    c["common_directional_sample"] for c in snapshot["snapshot"]["cases"]
                ),
                "experiment_id": result["experiment_id"] if result else None,
                "promotion_eligible": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
