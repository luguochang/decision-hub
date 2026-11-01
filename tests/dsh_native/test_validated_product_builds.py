from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "infra/dsh/validated-product-builds.ps1"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")
PLUGIN = "extensions/dsh/decision-hub"


def _write(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _receipt(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    repo = tmp_path / "repository"
    inputs = [
        f"{PLUGIN}/src/host/auth.ts",
        f"{PLUGIN}/src/index.ts",
        f"{PLUGIN}/src/research-tool.ts",
        f"{PLUGIN}/src/query-catalog.ts",
        f"{PLUGIN}/src/synthesis-tool.ts",
        f"{PLUGIN}/src/client/index.js",
        f"{PLUGIN}/package.json",
        f"{PLUGIN}/cordis.patch.yml",
        "infra/dsh/presets/decision-research/agent.cordis.yml",
        "infra/dsh/presets/decision-research/preset.yml",
        "infra/dsh/upstream.lock.json",
    ]
    files = inputs + [
        "package.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "apps/decision-desk/index.html",
        "apps/decision-desk/package.json",
        "apps/decision-desk/tsconfig.json",
        "apps/decision-desk/vite.config.ts",
        "apps/decision-desk/src/App.tsx",
        "apps/decision-desk/public/ui/logo.svg",
        "packages/contracts_ts/package.json",
        "packages/contracts_ts/src/index.ts",
        "contracts/schemas/research.schema.yaml",
        f"{PLUGIN}/tsconfig.json",
        f"{PLUGIN}/vite.host.config.ts",
        f"{PLUGIN}/scripts/generate-build-identity.mjs",
    ]
    for name in files:
        _write(repo, name, f"fixture:{name}\n")
    digest = hashlib.sha256()
    for name in inputs:
        data = (repo / name).read_bytes()
        digest.update(f"{name}\0{len(data)}:".encode())
        digest.update(data + b"\0")
    build_hash = digest.hexdigest()
    identity = f"{PLUGIN}/src/generated-build-identity.ts"
    _write(
        repo,
        identity,
        f'export const PLUGIN_BUILD_HASH = "{build_hash}" as const;\n'
        f"export const PLUGIN_BUILD_INPUTS = {json.dumps(inputs)} as const;\n",
    )
    artifacts = {
        "apps/decision-desk/dist/index.html": '<script src="/assets/app.js"></script>',
        "apps/decision-desk/dist/assets/app.js": "console.log('fixture')",
        f"{PLUGIN}/lib/index.js": f'const buildHash = "{build_hash}";',
        f"{PLUGIN}/lib/research-tool.js": "export default {};",
        f"{PLUGIN}/lib/synthesis-tool.js": "export default {};",
        f"{PLUGIN}/lib/client.js": "window.fixture = true;",
        f"{PLUGIN}/lib/index.js.map": "{}",
    }
    for name, content in artifacts.items():
        _write(repo, name, content)
    return repo, {
        "schema": "product-validated-builds.v1",
        "repository_root": str(repo),
        "sources": {name: _sha(repo / name) for name in files + [identity]},
        "artifacts": {name: _sha(repo / name) for name in artifacts},
        "plugin_build_hash": build_hash,
        "evidence": {"frontend_build": "test fixture"},
    }


def _quoted(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _verify(repo: Path, receipt: dict[str, object]) -> subprocess.CompletedProcess[str]:
    receipt_path = repo.parent / "validated-builds.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    assert POWERSHELL is not None
    return subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "$ErrorActionPreference = 'Stop'; "
            f". {_quoted(HELPER)}; Test-ValidatedProductBuilds "
            f"-ReceiptPath {_quoted(receipt_path)} -RepositoryRoot {_quoted(repo)} "
            "| ConvertTo-Json -Compress",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell read-only preflight verification")
def test_validated_builds_allow_existing_bundle_without_building(tmp_path: Path) -> None:
    repo, receipt = _receipt(tmp_path)
    # Unrelated old UI assets do not force another build or a giant manifest.
    _write(repo, "apps/decision-desk/dist/assets/old-bundle.js", "old")
    result = _verify(repo, receipt)
    assert result.returncode == 0, result.stdout + result.stderr
    verified = json.loads(result.stdout)
    assert verified["plugin_build_hash"] == receipt["plugin_build_hash"]
    assert verified["artifact_count"] == len(cast(dict[str, str], receipt["artifacts"]))


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell read-only preflight verification")
@pytest.mark.parametrize(
    "defect",
    [
        "schema",
        "wrong_repository",
        "source_changed",
        "source_added",
        "source_omitted",
        "artifact_changed",
        "artifact_omitted",
        "path_escape",
        "plugin_identity",
        "plugin_embedded_identity",
    ],
)
def test_validated_builds_reject_stale_or_incomplete_receipt(tmp_path: Path, defect: str) -> None:
    repo, receipt = _receipt(tmp_path)
    sources = cast(dict[str, str], receipt["sources"])
    artifacts = cast(dict[str, str], receipt["artifacts"])
    if defect == "schema":
        receipt["schema"] = "product-validated-builds.v2"
    elif defect == "wrong_repository":
        receipt["repository_root"] = str(tmp_path)
    elif defect == "source_changed":
        _write(repo, "apps/decision-desk/src/App.tsx", "changed")
    elif defect == "source_added":
        _write(repo, "apps/decision-desk/src/NewPage.tsx", "new")
    elif defect == "source_omitted":
        del sources["apps/decision-desk/src/App.tsx"]
    elif defect == "artifact_changed":
        _write(repo, "apps/decision-desk/dist/assets/app.js", "changed")
    elif defect == "artifact_omitted":
        del artifacts["apps/decision-desk/dist/assets/app.js"]
    elif defect == "path_escape":
        _write(tmp_path, "outside.txt", "outside")
        sources["../outside.txt"] = _sha(tmp_path / "outside.txt")
    elif defect == "plugin_identity":
        _write(repo, f"{PLUGIN}/src/host/auth.ts", "changed")
        sources[f"{PLUGIN}/src/host/auth.ts"] = _sha(repo / f"{PLUGIN}/src/host/auth.ts")
    elif defect == "plugin_embedded_identity":
        _write(repo, f"{PLUGIN}/lib/index.js", "export const stale = true;")
        artifacts[f"{PLUGIN}/lib/index.js"] = _sha(repo / f"{PLUGIN}/lib/index.js")
    result = _verify(repo, receipt)
    assert result.returncode != 0, result.stdout
    assert "running scripts is disabled" not in result.stderr
    assert "UnauthorizedAccess" not in result.stderr
    expected_reason = {
        "schema": "unsupported schema",
        "wrong_repository": "different repository",
        "source_changed": "Validated build sources changed",
        "source_added": "omits current source",
        "source_omitted": "omits current source",
        "artifact_changed": "Validated build artifacts changed",
        "artifact_omitted": "omits required artifact",
        "path_escape": "Invalid repository-relative build path",
        "plugin_identity": "do not match its validated identity",
        "plugin_embedded_identity": "do not match its validated identity",
    }[defect]
    assert expected_reason in result.stderr, result.stderr


def test_validated_build_preflight_precedes_credentials_and_preserves_default_builds() -> None:
    launcher = (ROOT / "infra/dsh/run-product.ps1").read_text(encoding="utf-8")
    first_preflight = launcher.index("$validatedBuilds = Test-ValidatedProductBuilds")
    assert first_preflight < launcher.index("$searchEnvironment = Read-DotEnvSubset")
    assert first_preflight < launcher.index("Set-Content -LiteralPath $dshCredentialStore")
    assert first_preflight < launcher.index("New-Item -ItemType Directory")
    assert "[string]$ValidatedBuildReceipt = ''" in launcher
    normal_builds = launcher.split("if ($null -eq $validatedBuilds) {", 1)[1].split(
        "} else {", 1
    )[0]
    assert "& pnpm --dir (Join-Path $repoRoot 'apps\\decision-desk') build" in normal_builds
    assert "& pnpm --dir $pluginDir build" in normal_builds
    assert "Decision Desk build failed." in normal_builds
    assert "Decision Hub DSH plugin build failed." in normal_builds
