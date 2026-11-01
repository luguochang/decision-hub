"""Load the actual assessment verifier without issuing a product build receipt."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

SOURCE = "packages/evals/assessment_basis.py"
PHASES = ("standalone", "early", "final_source_before")


def preflight(root: Path, expected: str, phase: str) -> dict[str, object]:
    proof: dict[str, object] = {
        "schema_version": "assessment-manifest-load-preflight.v1",
        "passed": False,
        "scope": "assessment_basis_module_load",
        "phase": phase,
        "source_basis": (
            "closed_source_before" if phase == "final_source_before" else "current_working_tree"
        ),
        "source": {"path": SOURCE, "sha256": expected},
        "loaded_manifest_hash": None,
        "error_code": None,
        "grants_product_receipt": False,
        "native_execution_qualified": False,
        "whole_product_accepted": False,
    }
    try:
        source = root.resolve() / SOURCE
        if (
            not re.fullmatch(r"[a-f0-9]{64}", expected)
            or hashlib.sha256(source.read_bytes()).hexdigest() != expected
        ):
            raise ValueError("assessment_manifest_preflight_source_changed")
        # This command owns a fresh process; keep the repository unchanged by imports.
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(root.resolve()))
        with redirect_stdout(sys.stderr):
            module = importlib.import_module("packages.evals.assessment_basis")
            if (
                not isinstance(module.__file__, str)
                or Path(module.__file__).resolve() != source.resolve()
            ):
                raise ValueError("assessment_manifest_preflight_source_changed")
            manifest = module.loaded_assessment_manifest_hash()
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError("assessment_manifest_preflight_source_changed")
        if not isinstance(manifest, str) or not re.fullmatch(r"[a-f0-9]{64}", manifest):
            raise ValueError("assessment_manifest_preflight_invalid")
        proof["loaded_manifest_hash"] = manifest
        proof["passed"] = True
    except Exception as error:
        code = str(error)
        proof["error_code"] = (
            code
            if re.fullmatch(r"[A-Za-z0-9_]{1,128}", code)
            else "assessment_manifest_preflight_failed"
        )
    return proof


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--phase", choices=PHASES, default="standalone")
    args = parser.parse_args()
    proof = preflight(args.repository_root, args.expected_source_sha256, args.phase)
    print(json.dumps(proof, sort_keys=True))
    return 0 if proof["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
