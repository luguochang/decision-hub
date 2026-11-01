"""Export frozen live outlooks and verify labels locally; no model or external HTTP."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from packages.evals.prospective_observations import (
    export_prospective_inputs,
    load_prospective_inputs,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path, help="Verify an existing self-contained export")
    args = parser.parse_args()
    if args.verify:
        if args.database or args.data_root or args.output:
            parser.error("verify accepts only an existing dataset directory")
        manifest, cases = load_prospective_inputs(args.verify)
        print(json.dumps({"integrity_status": "verified", "cases": len(cases),
                          "manifest_hash": manifest["content_hash"],
                          "verified_labels": manifest["verified_labels"],
                          "invalid_cases": manifest["invalid_cases"],
                          "promotion_eligible": manifest["promotion_eligible"]}))
        return 0
    if not args.database or not args.data_root or not args.output:
        parser.error("export requires database, data-root and a new output directory")
    if not args.database.is_file() or not args.data_root.is_dir():
        parser.error("existing database and data root required")
    path = args.database.resolve().as_posix()
    db = Database(f"sqlite+pysqlite:///file:{path}?mode=ro&uri=true")
    manifest = export_prospective_inputs(
        db, args.data_root.resolve(), args.output.resolve(), now=datetime.now(UTC),
    )
    print(json.dumps({"output": str(args.output), "verified_labels": manifest["verified_labels"],
                      "invalid_cases": manifest["invalid_cases"],
                      "verified_inputs": manifest["verified_inputs"],
                      "usable_observations": manifest["usable_observations"],
                      "summary": manifest["summary"]}, ensure_ascii=False))
    return 1 if manifest["invalid_cases"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
