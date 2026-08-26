from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "contracts" / "generated-manifest.yaml"


def check() -> int:
    failures: list[str] = []
    for path in sorted((ROOT / "contracts").rglob("*.yaml")):
        if path.name in {"versions.yaml"}:
            continue
        try:
            document = yaml.safe_load(path.read_text())
            Draft202012Validator.check_schema(document)
        except Exception as exc:  # pragma: no cover - error output path
            failures.append(f"{path}: {exc}")
    if failures:
        print("\n".join(failures))
        return 1
    current = {}
    for path in sorted((ROOT / "contracts").rglob("*.schema.yaml")):
        current[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    if MANIFEST.exists():
        recorded = yaml.safe_load(MANIFEST.read_text()).get("schemas", {})
        stale = [path for path, digest in current.items() if recorded.get(path) != digest]
        missing = [path for path in recorded if path not in current]
        if stale or missing:
            print(
                f"generated contract manifest stale; regenerate: stale={stale}, missing={missing}"
            )
            return 1
    print("canonical schemas: ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["check", "generate", "compatibility"])
    args = parser.parse_args()
    if args.command == "check":
        return check()
    if args.command == "generate":
        entries = {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((ROOT / "contracts").rglob("*.schema.yaml"))
        }
        MANIFEST.write_text(
            yaml.safe_dump(
                {"manifest_version": "contracts.v1", "schemas": entries},
                sort_keys=True,
            )
        )
        print(MANIFEST)
        return 0
    print(
        "compatibility: additive schema compatibility is enforced by fixtures in the next R0 slice"
    )
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
