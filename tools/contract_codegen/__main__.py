from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "contracts" / "generated-manifest.yaml"
PYTHON_GENERATED = {
    ROOT / "contracts" / "schemas" / "workbench_assets.schema.yaml": (
        ROOT
        / "packages"
        / "contracts_py"
        / "decision_hub_contracts"
        / "generated"
        / "workbench_assets.py"
    ),
    ROOT / "contracts" / "schemas" / "run_inspector.schema.yaml": (
        ROOT
        / "packages"
        / "contracts_py"
        / "decision_hub_contracts"
        / "generated"
        / "run_inspector.py"
    ),
}
TS_GENERATED = ROOT / "packages" / "contracts_ts" / "src" / "generated" / "r2.ts"


def _schema_digests() -> dict[str, str]:
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT / "contracts").rglob("*.schema.yaml"))
    }


def _generate_python(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "datamodel_code_generator",
            "--input",
            str(source),
            "--input-file-type",
            "jsonschema",
            "--output",
            str(output),
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--target-python-version",
            "3.12",
            "--use-standard-collections",
            "--use-union-operator",
            "--enum-field-as-literal",
            "all",
            "--collapse-root-models",
            "--use-annotated",
            "--use-type-alias",
            "--disable-timestamp",
            "--formatters",
            "ruff-format",
        ],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--fix", str(output)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )


def _generate_typescript(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["node", "tools/contract_codegen/generate_ts.mjs", str(output)],
        cwd=ROOT,
        check=True,
    )


def generate() -> None:
    for source, output in PYTHON_GENERATED.items():
        _generate_python(source, output)
    _generate_typescript(TS_GENERATED)
    MANIFEST.write_text(
        yaml.safe_dump(
            {"manifest_version": "contracts.v1", "schemas": _schema_digests()},
            sort_keys=True,
        )
    )


def _check_generated() -> list[str]:
    stale: list[str] = []
    # Keep candidates below the repository so formatters resolve the same
    # project configuration used by the committed generated files.
    with tempfile.TemporaryDirectory(dir=ROOT) as raw_temp:
        temp = Path(raw_temp)
        for source, recorded in PYTHON_GENERATED.items():
            candidate = temp / recorded.name
            _generate_python(source, candidate)
            if not recorded.exists() or recorded.read_bytes() != candidate.read_bytes():
                stale.append(str(recorded.relative_to(ROOT)))
        ts_candidate = temp / "r2.ts"
        _generate_typescript(ts_candidate)
        if not TS_GENERATED.exists() or TS_GENERATED.read_bytes() != ts_candidate.read_bytes():
            stale.append(str(TS_GENERATED.relative_to(ROOT)))
    return stale


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
    current = _schema_digests()
    if MANIFEST.exists():
        recorded = yaml.safe_load(MANIFEST.read_text()).get("schemas", {})
        stale = [path for path, digest in current.items() if recorded.get(path) != digest]
        missing = [path for path in recorded if path not in current]
        if stale or missing:
            print(
                f"generated contract manifest stale; regenerate: stale={stale}, missing={missing}"
            )
            return 1
    stale_generated = _check_generated()
    if stale_generated:
        print(f"generated contract code stale; regenerate: {stale_generated}")
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
        generate()
        print(MANIFEST)
        return 0
    print(
        "compatibility: additive schema compatibility is enforced by fixtures in the next R0 slice"
    )
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
