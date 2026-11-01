"""Fast file-only preflight checks; never invoke a real product build or database."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "infra/dsh/assessment-manifest-preflight.py"
BUILDER = ROOT / "infra/dsh/build-product.ps1"
GUARD = ROOT / "infra/dsh/validated-product-builds.ps1"
PWSH = shutil.which("pwsh")
SOURCE = "packages/evals/assessment_basis.py"
MANIFEST = "f" * 64
RECEIPT_HELPER = r"""
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'helper-imported'), 'loaded')
function Get-ProductBuildSha256 {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Get-ProductBuildSourceMap {
    param($AdditionalSources)
    [IO.File]::AppendAllText((Join-Path $repoRoot 'source-map-calls'), 'called' + "`n")
    if (Test-Path -LiteralPath (Join-Path $repoRoot 'forbid-source-map')) {
        throw 'fixture_expensive_inventory_reached'
    }
    $path = Join-Path $repoRoot 'packages/evals/assessment_basis.py'
    return @{'packages/evals/assessment_basis.py' = (Get-ProductBuildSha256 -Path $path)}
}
function Get-ProductReceiptSizeEstimate { return 1 }
function Assert-ProductReceiptKnownSize { param($Utf8Bytes) }
"""
RETENTION_TEMPLATE = """
import json, sys
from pathlib import Path
args = sys.argv
root = Path(args[args.index('--repository-root') + 1])
out = Path(args[args.index('--output-directory') + 1])
out.mkdir(parents=True)
(root / 'retention-reached').write_text('reached')
(root / 'packages/evals/assessment_basis.py').write_text(FINAL_SOURCE)
(out / 'retained-recipe-preparation.json').write_text(json.dumps({
    'passed': True, 'prior_receipt': {
        'sha256': args[args.index('--prior-receipt-sha256') + 1]},
    'additional_sources': {}, 'retained_recipe_archives_v1': {'registry': {}}}))
"""


def write(root: Path, name: str, body: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def source_repo(tmp_path: Path, body: str | None = None) -> tuple[Path, str]:
    for name in ("packages/__init__.py", "packages/evals/__init__.py"):
        write(tmp_path, name, "")
    source = write(
        tmp_path,
        SOURCE,
        body or f"def loaded_assessment_manifest_hash():\n    return {MANIFEST!r}\n",
    )
    return tmp_path, hashlib.sha256(source.read_bytes()).hexdigest()


def invoke(
    repo: Path,
    sha: str,
    phase: str = "standalone",
    *,
    script: Path = CLI,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(script),
            "--repository-root",
            str(repo),
            "--expected-source-sha256",
            sha,
            "--phase",
            phase,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )


@pytest.mark.parametrize("phase", ("standalone", "early", "final_source_before"))
def test_real_fresh_import_reports_source_and_explicit_non_release_scope(
    tmp_path: Path,
    phase: str,
) -> None:
    repo, sha = source_repo(tmp_path)
    before = sorted(path.relative_to(repo) for path in repo.rglob("*"))
    result = invoke(repo, sha, phase)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout)
    assert proof["schema_version"] == "assessment-manifest-load-preflight.v1"
    assert proof["scope"] == "assessment_basis_module_load"
    assert proof["phase"] == phase
    assert proof["source_basis"] == (
        "closed_source_before" if phase == "final_source_before" else "current_working_tree"
    )
    assert proof["source"] == {"path": SOURCE, "sha256": sha}
    assert proof["passed"] is True
    assert proof["loaded_manifest_hash"] == MANIFEST
    assert proof["error_code"] is None
    for field in ("grants_product_receipt", "native_execution_qualified", "whole_product_accepted"):
        assert proof[field] is False
    assert sorted(path.relative_to(repo) for path in repo.rglob("*")) == before


@pytest.mark.parametrize(
    ("body", "error"),
    [
        (
            "raise ValueError('assessment_basis_immutable_binding_limit')\n",
            "assessment_basis_immutable_binding_limit",
        ),
        (
            "raise RuntimeError('private/path must not leak')\n",
            "assessment_manifest_preflight_failed",
        ),
        (
            "def loaded_assessment_manifest_hash():\n    return 'invalid'\n",
            "assessment_manifest_preflight_invalid",
        ),
        (
            "from pathlib import Path\n"
            "def loaded_assessment_manifest_hash():\n"
            "    p = Path(__file__)\n    p.write_text(p.read_text() + '# changed\\n')\n"
            f"    return {MANIFEST!r}\n",
            "assessment_manifest_preflight_source_changed",
        ),
    ],
)
def test_loader_failures_are_small_json_and_strict_exit_one(
    tmp_path: Path,
    body: str,
    error: str,
) -> None:
    repo, sha = source_repo(tmp_path, body)
    result = invoke(repo, sha)
    assert result.returncode == 1, result.stdout + result.stderr
    proof = json.loads(result.stdout)
    assert proof["passed"] is False
    assert proof["loaded_manifest_hash"] is None
    assert proof["error_code"] == error
    assert "private/path" not in result.stdout + result.stderr
    assert len(result.stdout.encode()) < 2048


@pytest.mark.parametrize("sha", ("0" * 64, "bad-sha"))
def test_sha_rejection_happens_before_import(tmp_path: Path, sha: str) -> None:
    repo, _ = source_repo(tmp_path, "raise RuntimeError('import_was_reached')\n")
    result = invoke(repo, sha)
    assert result.returncode == 1, result.stdout + result.stderr
    assert json.loads(result.stdout)["error_code"] == "assessment_manifest_preflight_source_changed"


def test_import_output_cannot_corrupt_json(tmp_path: Path) -> None:
    repo, sha = source_repo(
        tmp_path,
        f"print('fixture import output')\ndef loaded_assessment_manifest_hash():\n"
        f"    return {MANIFEST!r}\n",
    )
    result = invoke(repo, sha)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["loaded_manifest_hash"] == MANIFEST


def test_invalid_phase_is_usage_exit_two(tmp_path: Path) -> None:
    repo, sha = source_repo(tmp_path)
    result = invoke(repo, sha, "unknown")
    assert result.returncode == 2
    assert not result.stdout


def quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def build_fixture(
    tmp_path: Path,
    *,
    initial_failure: bool,
    final_failure: bool = True,
) -> Path:
    body = "raise ValueError('assessment_basis_immutable_binding_limit')\n"
    repo, _ = source_repo(tmp_path, body if initial_failure else None)
    controls = repo / "infra/dsh"
    controls.mkdir(parents=True)
    shutil.copyfile(BUILDER, controls / BUILDER.name)
    shutil.copyfile(GUARD, controls / GUARD.name)
    if CLI.exists():
        shutil.copyfile(CLI, controls / CLI.name)
    write(repo, "prior.json", "{}\n")
    for name in ("Scripts/python.exe", "pyvenv.cfg"):
        destination = repo / ".venv" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / ".venv" / name, destination)
    write(repo, "infra/dsh/product-build-receipt.ps1", RECEIPT_HELPER)
    if initial_failure:
        write(repo, "forbid-source-map", "must not reach source inventory")
    retained_body = (
        body
        if final_failure
        else ("def loaded_assessment_manifest_hash():\n    return '" + "e" * 64 + "'\n")
    )
    write(
        repo,
        "infra/dsh/prepare-product-retention.py",
        RETENTION_TEMPLATE.replace("FINAL_SOURCE", repr(retained_body)),
    )
    write(
        repo,
        "extensions/dsh/decision-hub/src/generated-build-identity.ts",
        'export const PLUGIN_BUILD_HASH = "' + "e" * 64 + '" as const;\n',
    )
    return repo


def run_fixture_builder(
    repo: Path,
    shell: str,
    *,
    spot: bool = False,
) -> subprocess.CompletedProcess[str]:
    prior = repo / "prior.json"
    sha = hashlib.sha256(prior.read_bytes()).hexdigest()
    script = (
        "$ErrorActionPreference='Stop'; "
        "function pnpm { [IO.File]::WriteAllText("
        f"{quote(repo / 'build-reached')},'pnpm'); throw 'fixture_build_forbidden' }}; "
        "function node { [IO.File]::WriteAllText("
        f"{quote(repo / 'identity-reached')},'node'); throw 'fixture_identity_forbidden' }}; "
        f"& {quote(repo / 'infra/dsh/build-product.ps1')} "
        f"-OutputDirectory 'artifacts/probe' -PriorValidatedBuildReceipt {quote(prior)} "
        f"-PriorValidatedBuildReceiptSha256 '{sha}' " + ("-InstallSpotResearchV3" if spot else "")
    )
    return subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 isolated builder control")
def test_early_failure_precedes_inventory_retention_identity_pointer_and_build(
    tmp_path: Path,
) -> None:
    repo = build_fixture(tmp_path, initial_failure=True)
    assert PWSH is not None
    result = run_fixture_builder(repo, PWSH, spot=True)
    assert result.returncode != 0
    stage: dict[str, Any] = json.loads((repo / "artifacts/probe/build-execution.json").read_text())
    assert stage["error"] == "product_build_assessment_manifest_early_preflight_failed"
    assert stage["assessment_manifest_early_preflight"]["passed"] is False
    assert stage["assessment_manifest_preflight"] is None
    for name in (
        "source-map-calls",
        "retention-reached",
        "identity-reached",
        "build-reached",
        "infra/dsh/research-graph-recipe-installed.v3.json",
        "artifacts/probe/product-validated-builds.json",
    ):
        assert not (repo / name).exists(), name
    assert stage["pointer_writes"] == stage["builds"] == 0
    path = repo / "artifacts/probe/assessment-manifest-early-preflight.json"
    proof = json.loads(path.read_text())
    assert proof["error_code"] == "assessment_basis_immutable_binding_limit"
    assert proof["phase"] == "early"


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 isolated builder control")
def test_final_check_uses_post_retention_source_before_and_stops_build(tmp_path: Path) -> None:
    repo = build_fixture(tmp_path, initial_failure=False)
    assert PWSH is not None
    result = run_fixture_builder(repo, PWSH)
    assert result.returncode != 0
    output = repo / "artifacts/probe"
    stage = json.loads((output / "build-execution.json").read_text())
    assert stage["error"] == "product_build_assessment_manifest_preflight_failed"
    assert stage["assessment_manifest_early_preflight"]["passed"] is True
    assert stage["assessment_manifest_preflight"]["passed"] is False
    early = json.loads((output / "assessment-manifest-early-preflight.json").read_text())
    final = json.loads((output / "assessment-manifest-preflight.json").read_text())
    assert final["phase"] == "final_source_before"
    assert final["source_basis"] == "closed_source_before"
    assert final["source"]["sha256"] == hashlib.sha256((repo / SOURCE).read_bytes()).hexdigest()
    assert early["source"]["sha256"] != final["source"]["sha256"]
    assert (repo / "retention-reached").exists()
    assert len((repo / "source-map-calls").read_text().splitlines()) == 2
    assert not (repo / "build-reached").exists()
    assert not (output / "product-validated-builds.json").exists()


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 isolated builder control")
def test_early_and_final_accept_independent_valid_manifests(tmp_path: Path) -> None:
    repo = build_fixture(tmp_path, initial_failure=False, final_failure=False)
    assert PWSH is not None
    result = run_fixture_builder(repo, PWSH)
    assert result.returncode != 0
    output = repo / "artifacts/probe"
    stage = json.loads((output / "build-execution.json").read_text())
    assert stage["assessment_manifest_early_preflight"]["passed"] is True
    assert stage["assessment_manifest_preflight"]["passed"] is True
    early = json.loads((output / "assessment-manifest-early-preflight.json").read_text())
    final = json.loads((output / "assessment-manifest-preflight.json").read_text())
    assert early["loaded_manifest_hash"] == MANIFEST
    assert final["loaded_manifest_hash"] == "e" * 64
    assert stage["error"] == "fixture_build_forbidden"
    assert stage["ui_build_attempts"] == 1
    assert (repo / "build-reached").read_text() == "pnpm"
    assert not (output / "product-validated-builds.json").exists()


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="Windows PowerShell 5 gate")
def test_legacy_powershell_rejects_before_import_or_output_creation(tmp_path: Path) -> None:
    repo = build_fixture(tmp_path, initial_failure=True)
    legacy = shutil.which("powershell.exe")
    assert legacy is not None
    result = run_fixture_builder(repo, legacy)
    assert result.returncode != 0
    assert "product_build_powershell_7_required" in result.stdout + result.stderr
    assert not (repo / "infra/dsh/helper-imported").exists()
    assert not (repo / "artifacts/probe").exists()
