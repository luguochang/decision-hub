"""Copy a frozen candidate inventory only after the managed product has stopped."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORMAL = ROOT.parent / "decision-hub"
STOP_GUARD = r"""param([string]$RepositoryRoot)
$ErrorActionPreference='Stop'
. (Join-Path $RepositoryRoot 'infra/dsh/product-processes.ps1')
$runtime=Join-Path $RepositoryRoot 'data/product-local'
if(-not(Test-Path -LiteralPath (Join-Path $runtime 'stop.request') -PathType Leaf)){
 throw 'standard_stop_request_missing'
}
if(Test-Path -LiteralPath (Join-Path $runtime 'processes.json')){
 throw 'managed_process_roster_not_clear'
}
$supervisor=Get-Content -LiteralPath (Join-Path $runtime 'supervisor.json') -Raw|ConvertFrom-Json
if($supervisor.state -cne 'stopped' -or -not $supervisor.started_ticks){
 throw 'standard_supervisor_not_stopped'
}
foreach($entry in @($supervisor)+@($supervisor.children)){
 if($null -ne $entry -and (Test-ProductProcessIdentity $entry)){
  throw 'managed_original_lifetime_alive'
 }
}
$ports=@(Get-NetTCPConnection -State Listen -ErrorAction Stop|
 Where-Object LocalPort -In 8047,3097,8037,8038)
if($ports.Count){throw 'standard_product_ports_not_clear'}
[pscustomobject]@{passed=$true;state=$supervisor.state;ports=@()}|ConvertTo-Json
"""


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if ROOT.name != ".market-risk-b2-b4" or not FORMAL.is_dir():
        raise ValueError("exact_candidate_and_formal_roots_required")
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / "artifacts"):
        raise ValueError("fresh_candidate_artifacts_output_required")
    output.mkdir(parents=True)
    files = json.loads(args.inventory.read_text(encoding="utf-8-sig"))
    # Tools/tests/docs are review material, not build inputs; explicitly copy
    # those controlled roots while leaving all data/dependency/build stores alone.
    for directory in ("tools", "tests", "docs/design/market-risk-v1"):
        for path in (ROOT / directory).rglob("*"):
            if path.is_file() and path.suffix in (".py", ".md", ".json", ".mjs"):
                if not any(part in ("__pycache__", "node_modules", "data", "artifacts")
                           for part in path.relative_to(ROOT).parts):
                    files[path.relative_to(ROOT).as_posix()] = digest(path)
    # Canonical manifest is checked by codegen and accompanies generated code.
    files["contracts/generated-manifest.yaml"] = digest(ROOT / "contracts/generated-manifest.yaml")
    changed = {}
    for name, expected in files.items():
        relative = Path(name)
        source, target = ROOT / relative, FORMAL / relative
        if relative.is_absolute() or ".." in relative.parts or "\\" in name:
            raise ValueError("candidate_inventory_path_invalid")
        if not source.resolve().is_relative_to(ROOT) or not target.resolve().is_relative_to(FORMAL):
            raise ValueError("candidate_inventory_path_escape")
        if digest(source) != expected:
            raise ValueError("candidate_source_changed:" + name)
        current = digest(target) if target.is_file() else None
        if current != expected:
            changed[name] = {"before": current, "after": expected}
    receipt = {"schema_version": "market-risk-source-promotion.v1",
               "at": datetime.now(UTC).isoformat(), "apply": args.apply,
               "inventory_hash": digest(args.inventory), "checked": len(files),
               "changed": changed, "passed": False, "formal_source_writes": 0}
    if args.apply:
        guard = output / "stopped-guard.ps1"
        guard.write_text(STOP_GUARD, encoding="utf-8")
        result = subprocess.run(["pwsh", "-NoProfile", "-File", str(guard),
                                 "-RepositoryRoot", str(FORMAL)],
                                capture_output=True, text=True, check=True)
        receipt["stopped_guard"] = json.loads(result.stdout)
        # Preserve every changed prior byte before the first source write.
        for name, record in changed.items():
            target = FORMAL / name
            if record["before"] is not None:
                saved = output / "prior" / name
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(target, saved)
                if digest(saved) != record["before"]:
                    raise ValueError("prior_source_backup_changed")
        for name, record in changed.items():
            target = FORMAL / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
            if digest(target) != record["after"]:
                raise ValueError("formal_source_copy_mismatch:" + name)
            receipt["formal_source_writes"] += 1
    receipt["passed"] = True
    (output / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps({"passed": True, "apply": args.apply, "checked": len(files),
                      "changed": len(changed), "receipt": str(output / "receipt.json")}))


if __name__ == "__main__":
    main()
