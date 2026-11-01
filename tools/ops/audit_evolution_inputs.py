"""Check repository evolution samples without opening a DB or calling a model."""

import argparse
import json
from pathlib import Path

from packages.evals.fixture_audit import audit_fixture_splits, default_evolution_fixtures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    audit = audit_fixture_splits(default_evolution_fixtures())
    body = json.dumps(audit.model_dump(), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
    print(body, end="")
    return 1 if audit.issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
