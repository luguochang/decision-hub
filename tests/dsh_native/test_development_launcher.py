"""Controlled launcher checks: no product database, real services or model calls."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTROLS = ROOT / "infra/dsh"
HELPER = CONTROLS / "development-runtime.ps1"
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(PWSH is None, reason="PowerShell 7 required")


def quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def ps(body: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(PWSH), "-NoProfile", "-NonInteractive", "-Command", body],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=25,
        check=False, env=env,
    )


def helper(body: str) -> subprocess.CompletedProcess[str]:
    assert HELPER.is_file(), "development path/ownership helper is missing"
    return ps(f"$ErrorActionPreference='Stop'; . {quote(HELPER)}; {body}")


@pytest.mark.parametrize("name", ["default", "research-a", "test_1"])
def test_development_layout_is_source_local_and_has_no_write_side_effects(
    tmp_path: Path, name: str,
) -> None:
    result = helper(
        f"Resolve-DevelopmentLayout -RepositoryRoot {quote(tmp_path)} "
        f"-DevelopmentRoot {quote('data/development/' + name)} | ConvertTo-Json -Compress"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    layout = json.loads(result.stdout)
    base = tmp_path / "data/development" / name
    assert Path(layout["root"]) == base
    assert Path(layout["data"]) == base / "data"
    assert Path(layout["runtime"]) == base / "runtime"
    assert Path(layout["home"]) == base / "dsh-home"
    assert Path(layout["publication"]) == (
        tmp_path.parent / ".decision-hub-development" / name / "publication"
    )
    assert not (tmp_path / "data").exists()
    assert not Path(layout["publication"]).exists()


@pytest.mark.parametrize("path", [
    "data/product-local", "data/core-live-20260913", "data/dsh-live",
    "data/development/../product-local", "data/development/x/..",
    "data/development/x/nested", "data/development/CON", "data/development/a.",
])
def test_development_layout_rejects_formal_traversal_nested_and_device_paths(
    tmp_path: Path, path: str,
) -> None:
    result = helper(
        f"Resolve-DevelopmentLayout -RepositoryRoot {quote(tmp_path)} "
        f"-DevelopmentRoot {quote(path)}"
    )
    assert result.returncode != 0
    assert "development_root_invalid" in result.stderr
    assert not (tmp_path / "data").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction boundary")
def test_development_layout_rejects_linked_ancestor_and_descendant(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    home = tmp_path / "data/development/linked"
    home.parent.mkdir(parents=True)
    result = ps(
        f"New-Item -ItemType Junction -Path {quote(home)} -Target {quote(outside)} | Out-Null"
    )
    assert result.returncode == 0, result.stderr
    rejected = helper(
        f"Resolve-DevelopmentLayout -RepositoryRoot {quote(tmp_path)} "
        "-DevelopmentRoot 'data/development/linked'"
    )
    assert rejected.returncode != 0
    assert "development_path_linked" in rejected.stderr
    # A linked output child must also be rejected before credentials are touched.
    safe = tmp_path / "data/development/safe"
    safe.mkdir()
    result = ps(
        f"New-Item -ItemType Junction -Path {quote(safe / 'dsh-home')} "
        f"-Target {quote(outside)} | Out-Null"
    )
    assert result.returncode == 0, result.stderr
    rejected = helper(
        f"Resolve-DevelopmentLayout -RepositoryRoot {quote(tmp_path)} "
        "-DevelopmentRoot 'data/development/safe'"
    )
    assert rejected.returncode != 0
    assert "development_path_linked" in rejected.stderr


@pytest.mark.parametrize("args,error", [
    (["-EnableSpotResearchV3"], "development_release_features_forbidden"),
    (["-EnableActionExecution"], "development_release_features_forbidden"),
    (["-EnableResearchLearning"], "development_release_features_forbidden"),
    (["-ValidatedBuildReceipt", "not-read.json"], "development_release_features_forbidden"),
    (["-DshHome", "data/dsh-live"], "development_home_override_forbidden"),
    (["-DevelopmentRoot", "data/product-local"], "development_root_invalid"),
])
def test_rejection_precedes_credentials_receipts_and_process_start(
    args: list[str], error: str,
) -> None:
    result = subprocess.run(
        [str(PWSH), "-NoProfile", "-NonInteractive", "-File",
         str(CONTROLS / "run-product.ps1"), "-DevelopmentMode", *args],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=25, check=False,
    )
    assert result.returncode != 0
    assert error in result.stderr


def test_inherited_release_policy_and_duplicate_ports_are_rejected() -> None:
    result = helper(
        "$env:DECISION_HUB_HISTORY_PRIVATE_CONFIG='never-read'; "
        "Assert-DevelopmentLaunchPolicy -FeatureRequested $false "
        "-Ports @(18047,18097,18037,18038,5173)"
    )
    assert result.returncode != 0
    assert "development_private_policy_forbidden" in result.stderr
    result = helper(
        "Assert-DevelopmentLaunchPolicy -FeatureRequested $false "
        "-Ports @(18047,18047,18037,18038,5173)"
    )
    assert result.returncode != 0
    assert "development_ports_invalid" in result.stderr


def test_ast_is_valid_and_default_release_guards_remain() -> None:
    result = ps(
        "$errors=@(); "
        "Get-ChildItem infra/dsh -Filter '*dev*.ps1' | ForEach-Object { "
        "[void][Management.Automation.Language.Parser]::ParseFile("
        "$_.FullName,[ref]$null,[ref]$errors); "
        "if ($errors.Count) { throw $errors[0] } }; "
        "[void][Management.Automation.Language.Parser]::ParseFile("
        f"{quote(CONTROLS / 'run-product.ps1')},"
        "[ref]$null,[ref]$errors); if ($errors.Count) { throw $errors[0] }"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    launcher = (CONTROLS / "run-product.ps1").read_text(encoding="utf-8")
    assert "[switch]$DevelopmentMode" in launcher
    assert "Save-ProductInstalledReleasePointer" in launcher
    assert "harness_install_guard_source_changed" in launcher
    assert "DECISION_HUB_RETAINED_RECIPE_RECEIPT = $harnessReceiptPath" in launcher
    assert "VITE_API_PROXY_TARGET" in launcher
    assert "--strictPort" in launcher
    for name in ("run-dev.ps1", "stop-dev.ps1"):
        assert (CONTROLS / name).is_file()


def test_development_launch_reaches_local_plugin_without_importing_release_producer(
    tmp_path: Path,
) -> None:
    controls = tmp_path / "infra/dsh"
    controls.mkdir(parents=True)
    for name in (
        "run-product.ps1", "development-runtime.ps1", "product-processes.ps1",
        "decision-settings-session.ps1",
    ):
        shutil.copyfile(CONTROLS / name, controls / name)
    with (controls / "development-runtime.ps1").open("a", encoding="utf-8") as stream:
        stream.write(
            "\nfunction Initialize-DevelopmentPlugin { throw 'fixture_dev_plugin_reached' }\n"
        )
    (controls / "product-build-receipt.ps1").write_text(
        "throw 'fixture_release_producer_import_forbidden'", encoding="utf-8",
    )
    (controls / "validated-product-builds.ps1").write_text(
        "throw 'fixture_release_guard_forbidden'", encoding="utf-8",
    )
    formal = tmp_path / "data/dsh-live/.env"
    formal.parent.mkdir(parents=True)
    credentials = "OPENAI_API_KEY=fixture-only\nOPENAI_BASE_URL=http://127.0.0.1:9/v1\n"
    formal.write_text(credentials, encoding="utf-8")
    pointer = tmp_path / "data/product-local/installed-release-receipt.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text("poison pointer must never be parsed", encoding="utf-8")
    presets = controls / "presets/decision-research"
    presets.mkdir(parents=True)
    for name in ("agent.cordis.yml", "preset.yml"):
        (presets / name).write_text("[]", encoding="utf-8")
    result = ps(
        "function Get-NetTCPConnection { @() }; "
        f"& {quote(controls / 'run-product.ps1')} -DevelopmentMode "
        "-DevelopmentRoot 'data/development/fixture'"
    )
    assert result.returncode != 0
    assert "fixture_dev_plugin_reached" in result.stderr, result.stdout + result.stderr
    assert "fixture_release_" not in result.stdout + result.stderr
    assert formal.read_text(encoding="utf-8") == credentials
    assert pointer.read_text(encoding="utf-8") == "poison pointer must never be parsed"
    assert (tmp_path / "data/development/fixture/dsh-home/.credentials.yaml").is_file()
    assert not (tmp_path / "data/development/fixture/dsh-home/.env").exists()


def test_plugin_rebuilds_once_then_reuses_only_current_input_and_artifact_bytes(
    tmp_path: Path,
) -> None:
    plugin = tmp_path / "extensions/dsh/decision-hub"
    (plugin / "src").mkdir(parents=True)
    (plugin / "scripts").mkdir()
    (plugin / "src/generated-build-identity.ts").write_text("input-a", encoding="utf-8")
    (tmp_path / "runtime").mkdir()
    result = helper(
        f"$fixturePlugin={quote(plugin)}; $fixtureRuntime={quote(tmp_path / 'runtime')}; "
        "$script:buildCount=0; "
        "function FixtureNode { $global:LASTEXITCODE=0 }; "
        "function FixturePnpm { "
        "$script:buildCount++; New-Item -ItemType Directory -Force "
        "-Path (Join-Path $fixturePlugin 'lib') | Out-Null; "
        "foreach($name in @('index.js','research-tool.js','synthesis-tool.js',"
        "'reviewer-tool.js','client.js')) "
        "{ Set-Content -LiteralPath (Join-Path $fixturePlugin ('lib/'+$name)) "
        "-Value 'real fixture build bytes' }; "
        "$global:LASTEXITCODE=0 }; "
        f"$arguments=@{{RepositoryRoot={quote(tmp_path)};RuntimeDirectory=$fixtureRuntime;"
        "Node='FixtureNode';Pnpm='FixturePnpm'}; "
        "Initialize-DevelopmentPlugin @arguments; $first=$script:buildCount; "
        "Initialize-DevelopmentPlugin @arguments; $second=$script:buildCount; "
        "Set-Content -LiteralPath (Join-Path $fixturePlugin 'src/generated-build-identity.ts') "
        "-Value 'input-b'; "
        "Initialize-DevelopmentPlugin @arguments; $third=$script:buildCount; "
        "Set-Content -LiteralPath (Join-Path $fixturePlugin 'lib/client.js') -Value 'tampered'; "
        "Initialize-DevelopmentPlugin @arguments; "
        "@($first,$second,$third,$script:buildCount) | ConvertTo-Json -Compress"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == [1, 1, 2, 3]
    stamp = json.loads((tmp_path / "runtime/plugin-build.json").read_text(encoding="utf-8-sig"))
    assert stamp["grants_product_receipt"] is False


def test_existing_official_profile_relative_link_is_reused(tmp_path: Path) -> None:
    home = tmp_path / "data/development/one/dsh-home"
    manifest = home / "profiles/web/package.json"
    manifest.parent.mkdir(parents=True)
    plugin = tmp_path / "extensions/dsh/decision-hub"
    manifest.write_text(json.dumps({
        "dependencies": {
            "@decision-hub/dsh-plugin": "link:" + os.path.relpath(plugin, manifest.parent)
        },
        "dsh": {"profile": {"bundles": ["@decision-hub/dsh-plugin"]}},
    }), encoding="utf-8")
    result = helper(
        "function FixtureNode { throw 'fixture_profile_reinstall_forbidden' }; "
        f"Initialize-DevelopmentProfile -RepositoryRoot {quote(tmp_path)} "
        f"-HomePath {quote(home)} -Node FixtureNode"
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows hardlink boundary")
def test_development_sensitive_file_cannot_be_a_hardlink(tmp_path: Path) -> None:
    source = tmp_path / "formal-settings.yaml"
    source.write_text("must be preserved", encoding="utf-8")
    home = tmp_path / "data/development/one/dsh-home"
    home.mkdir(parents=True)
    os.link(source, home / "settings.yaml")
    result = helper(
        f"Resolve-DevelopmentLayout -RepositoryRoot {quote(tmp_path)} "
        "-DevelopmentRoot 'data/development/one'"
    )
    assert result.returncode != 0
    assert "development_path_linked" in result.stderr
    assert source.read_text(encoding="utf-8") == "must be preserved"


@pytest.mark.parametrize("relative", [
    "profiles/web/package.json", "profiles/web/pnpm-lock.yaml", "profiles/web/pnpm-workspace.yaml",
    "profiles/web/cordis.patch.yml", "profiles/web/cordis.yml", "cordis.patch.yml",
])
def test_development_profile_control_files_cannot_link_to_formal_home(
    tmp_path: Path, relative: str,
) -> None:
    source = tmp_path / "formal-control.json"
    source.write_text("preserve formal bytes", encoding="utf-8")
    home = tmp_path / "data/development/one/dsh-home"
    target = home / relative
    target.parent.mkdir(parents=True)
    os.link(source, target)
    result = helper(
        f"Resolve-DevelopmentLayout -RepositoryRoot {quote(tmp_path)} "
        "-DevelopmentRoot 'data/development/one'"
    )
    assert result.returncode != 0
    assert "development_path_linked" in result.stderr
    assert source.read_text(encoding="utf-8") == "preserve formal bytes"


@pytest.mark.parametrize("active", ["formal-supervisor", "formal-port", "same-root"])
def test_shared_source_build_refuses_live_formal_or_same_root_before_writes(
    tmp_path: Path, active: str,
) -> None:
    layout_root = tmp_path / "data/development/one"
    supervisor = (
        layout_root / "runtime/supervisor.json" if active == "same-root"
        else tmp_path / "data/product-local/supervisor.json"
    )
    if active != "formal-port":
        supervisor.parent.mkdir(parents=True)
        supervisor.write_text(json.dumps({"pid": 701, "started_ticks": "100"}), encoding="utf-8")
    result = helper(
        "function Get-Process { [pscustomobject]@{"
        "StartTime=[datetime]::new(100,[DateTimeKind]::Utc)} }; "
        "function Get-NetTCPConnection { "
        + ("[pscustomobject]@{LocalPort=3097}" if active == "formal-port" else "@()")
        + " }; "
        f"$layout=Resolve-DevelopmentLayout -RepositoryRoot {quote(tmp_path)} "
        "-DevelopmentRoot 'data/development/one'; "
        f"Assert-DevelopmentNoSharedRuntime -RepositoryRoot {quote(tmp_path)} -Layout $layout"
    )
    assert result.returncode != 0
    expected = (
        "development_instance_running" if active == "same-root"
        else "development_shared_live_runtime"
    )
    assert expected in result.stderr
    assert not (layout_root / "dsh-home").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows DSH home link boundary")
@pytest.mark.parametrize("relative,kind", [
    ("Crypto Macro Trader", "junction"), ("sessions/session.sqlite3", "hardlink"),
    ("profiles/web", "junction"),
])
def test_dsh_runtime_write_paths_cannot_redirect_to_formal_workspace(
    tmp_path: Path, relative: str, kind: str,
) -> None:
    home = tmp_path / "data/development/one/dsh-home"
    target = home / relative
    target.parent.mkdir(parents=True)
    formal = tmp_path / "formal"
    formal.mkdir()
    preserved = formal / "session.sqlite3"
    preserved.write_text("formal untouched", encoding="utf-8")
    if kind == "junction":
        result = ps(
            f"New-Item -ItemType Junction -Path {quote(target)} -Target {quote(formal)} | Out-Null"
        )
        assert result.returncode == 0, result.stderr
    else:
        os.link(preserved, target)
    result = helper(
        f"Resolve-DevelopmentLayout -RepositoryRoot {quote(tmp_path)} "
        "-DevelopmentRoot 'data/development/one'"
    )
    assert result.returncode != 0
    assert "development_path_linked" in result.stderr
    assert preserved.read_text(encoding="utf-8") == "formal untouched"


def test_development_origin_separates_hostwide_owner_cookie_from_formal() -> None:
    result = ps(
        "$ast=[Management.Automation.Language.Parser]::ParseFile("
        f"{quote(CONTROLS / 'run-product.ps1')},[ref]$null,[ref]$null); "
        "$assignment=$ast.Find({param($node) "
        "$node -is [Management.Automation.Language.AssignmentStatementAst] -and "
        "$node.Left.VariablePath.UserPath -eq 'deskOrigin'},$true).Extent.Text; "
        "$ApiPort=8047; $DeskPort=5173; $DevelopmentMode=$false; Invoke-Expression $assignment; "
        "$formal=$deskOrigin; $DevelopmentMode=$true; Invoke-Expression $assignment; "
        "@{formal=$formal;development=$deskOrigin} | ConvertTo-Json -Compress"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "formal": "http://127.0.0.1:8047", "development": "http://localhost:5173",
    }


@pytest.mark.parametrize("override,variable", [
    ("publication", "DECISION_HUB_PUBLICATION_DATA_DIR"),
    ("checkpoint", "DECISION_HUB_CHECKPOINT_PATH"),
    ("data", "DECISION_HUB_DATA_DIR"),
    ("dsh_home", "DECISION_HUB_DSH_HOME"),
    ("document_bundle", "DECISION_HUB_DOCUMENT_BUNDLE_ROOT"),
    ("source_acquisition", "DECISION_HUB_SOURCE_ACQUISITION_ROOT"),
])
def test_inherited_publication_and_checkpoint_paths_cannot_escape_development(
    tmp_path: Path, override: str, variable: str,
) -> None:
    result = helper(
        f"$layout=Resolve-DevelopmentLayout -RepositoryRoot {quote(tmp_path)} "
        "-DevelopmentRoot 'data/development/one'; "
        f"$env:{variable}='formal-location'; "
        "Assert-DevelopmentInstanceEnvironment -Layout $layout"
    )
    assert result.returncode != 0
    assert f"development_{override}_override_forbidden" in result.stderr
    assert not (tmp_path / "data").exists()


def test_matching_checkpoint_path_is_accepted_without_creating_files(tmp_path: Path) -> None:
    result = helper(
        f"$layout=Resolve-DevelopmentLayout -RepositoryRoot {quote(tmp_path)} "
        "-DevelopmentRoot 'data/development/one'; "
        "$env:DECISION_HUB_CHECKPOINT_PATH=Join-Path $layout.data "
        "'checkpoints/decision_graph.sqlite3'; "
        "Assert-DevelopmentInstanceEnvironment -Layout $layout"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / "data").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows external publication path links")
def test_external_publication_path_rejects_links_before_any_write(tmp_path: Path) -> None:
    repo = tmp_path / "repository"
    repo.mkdir()
    publication = tmp_path / ".decision-hub-development/one/publication"
    publication.parent.mkdir(parents=True)
    target = tmp_path / "formal-publication"
    target.mkdir()
    result = ps(
        f"New-Item -ItemType Junction -Path {quote(publication)} -Target {quote(target)} | Out-Null"
    )
    assert result.returncode == 0, result.stderr
    result = helper(
        f"Resolve-DevelopmentLayout -RepositoryRoot {quote(repo)} "
        "-DevelopmentRoot 'data/development/one'"
    )
    assert result.returncode != 0
    assert "development_path_linked" in result.stderr
    assert not (repo / "data").exists()
    assert not list(target.iterdir())


def test_formal_launcher_rejects_inherited_dev_root_before_loading_release(
    tmp_path: Path,
) -> None:
    controls = tmp_path / "infra/dsh"
    controls.mkdir(parents=True)
    shutil.copyfile(CONTROLS / "run-product.ps1", controls / "run-product.ps1")
    (controls / "product-build-receipt.ps1").write_text(
        "throw 'fixture_release_import_reached'", encoding="utf-8",
    )
    result = ps(
        "$env:DECISION_HUB_DEVELOPMENT_ROOT='unexpected-dev-root'; "
        f"& {quote(controls / 'run-product.ps1')}"
    )
    assert result.returncode != 0
    assert "development_parameters_require_explicit_mode" in result.stderr
    assert "fixture_release_import_reached" not in result.stderr


@pytest.mark.parametrize("development", [True, False])
def test_startup_probe_bypasses_proxy_only_for_explicit_development(development: bool) -> None:
    mode = "$true" if development else "$false"
    result = ps(
        "$ast=[Management.Automation.Language.Parser]::ParseFile("
        f"{quote(CONTROLS / 'run-product.ps1')},[ref]$null,[ref]$null); "
        "$function=$ast.Find({param($node) "
        "$node -is [Management.Automation.Language.FunctionDefinitionAst] -and "
        "$node.Name -eq 'Wait-HttpReady'},$true); Invoke-Expression $function.Extent.Text; "
        "function Invoke-WebRequest { param($Uri,$Headers,$TimeoutSec,[switch]$UseBasicParsing,"
        "[switch]$NoProxy) $script:direct=[bool]$NoProxy; @{StatusCode=200} }; "
        f"$DevelopmentMode={mode}; Wait-HttpReady "
        "-Url 'http://127.0.0.1:18047/health/ready' | Out-Null; "
        "@{no_proxy=$script:direct} | ConvertTo-Json -Compress"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["no_proxy"] is development


def test_development_worker_heartbeat_probe_also_bypasses_proxy() -> None:
    result = ps(
        "$ast=[Management.Automation.Language.Parser]::ParseFile("
        f"{quote(CONTROLS / 'run-product.ps1')},[ref]$null,[ref]$null); "
        "$function=$ast.Find({param($node) "
        "$node -is [Management.Automation.Language.FunctionDefinitionAst] -and "
        "$node.Name -eq 'Wait-ResearchWorkerReady'},$true); "
        "Invoke-Expression $function.Extent.Text; "
        "function Invoke-RestMethod { param($Uri,$TimeoutSec,[switch]$NoProxy) "
        "$script:direct=[bool]$NoProxy; "
        "@{services=@(@{service_id='hub-research-worker';status='online'})} }; "
        "$DevelopmentMode=$true; Wait-ResearchWorkerReady -Process (Get-Process -Id $PID) "
        "-Url 'http://127.0.0.1:18047/v1/operations'; "
        "@{no_proxy=$script:direct} | ConvertTo-Json -Compress"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["no_proxy"] is True


def test_vite_internal_probe_uses_bound_ipv4_while_owner_origin_remains_localhost() -> None:
    source = (CONTROLS / "run-product.ps1").read_text(encoding="utf-8")
    assert '$deskHealthUrl = "http://127.0.0.1:$DeskPort/"' in source
    assert "url=$deskHealthUrl;" in source
    assert "Wait-HttpReady -Url $deskHealthUrl" in source
    assert source.count("-HealthCheck $managedHealthCheck") == 2
    assert '"http://localhost:$DeskPort"' in source


@pytest.mark.parametrize("owned", [True, False])
def test_development_supervision_uses_direct_probe_and_preserves_listener_ownership(
    owned: bool,
) -> None:
    mode = "$true" if owned else "$false"
    result = helper(
        "$script:called=$false; $script:direct=$false; "
        f"function Test-ProductOwnedListener {{ return {mode} }}; "
        "function Invoke-WebRequest { param($Uri,$Method,$TimeoutSec,$ErrorAction,"
        "[switch]$UseBasicParsing,[switch]$NoProxy) "
        "$script:called=$true; $script:direct=[bool]$NoProxy; @{StatusCode=200} }; "
        "$entry=[pscustomobject]@{health_probe=[pscustomobject]@{port=5173;"
        "url='http://127.0.0.1:5173/'}}; "
        "$healthy=Test-DevelopmentServiceHealth -Entry $entry; "
        "@{healthy=$healthy;called=$script:called;direct=$script:direct} | ConvertTo-Json -Compress"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"healthy": owned, "called": owned, "direct": owned}


@pytest.mark.skipif(os.name != "nt", reason="Native ordinary-account Windows ACL regression")
@pytest.mark.parametrize("preprotected", [True, False])
def test_real_development_home_acl_initialization_is_idempotent_and_preserves_files(
    tmp_path: Path, preprotected: bool,
) -> None:
    home = tmp_path / "dsh-home"
    home.mkdir()
    settings = home / "settings.yaml"
    credentials = home / ".credentials.yaml"
    settings.write_text("{}\n", encoding="utf-8")
    credentials.write_text("fixture credentials retained\n", encoding="utf-8")
    prepare = r"""
$identity=[Security.Principal.WindowsIdentity]::GetCurrent().User
$acl=[Security.AccessControl.DirectorySecurity]::new()
$acl.SetAccessRuleProtection($true,$false); $acl.SetOwner($identity)
$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
    $identity,'FullControl','ContainerInherit,ObjectInherit','None','Allow'))
[IO.FileSystemAclExtensions]::SetAccessControl([IO.DirectoryInfo]::new($homePath),$acl)
""" if preprotected else ""
    result = helper(
        f"$homePath={quote(home)}; " + prepare
        + f". {quote(CONTROLS / 'decision-settings-session.ps1')}; "
        "Initialize-DevelopmentHome -HomePath $homePath "
        "-CredentialEnvironment @{} -Model 'gpt-6.1-sol'; "
        "Initialize-DevelopmentHome -HomePath $homePath "
        "-CredentialEnvironment @{} -Model 'gpt-6.1-sol'; "
        "Protect-DevelopmentPrivateFile -Path (Join-Path $homePath '.credentials.yaml'); "
        "Protect-DevelopmentPrivateFile -Path (Join-Path $homePath '.credentials.yaml'); "
        "$identity=[Security.Principal.WindowsIdentity]::GetCurrent().User; "
        "$valid=Test-ProductSettingsOwnerOnlyAcl -LiteralPath $homePath "
        "-Identity $identity -Directory $true; "
        "foreach($name in @('settings.yaml','.credentials.yaml')) { "
        "$valid=$valid -and (Test-ProductSettingsOwnerOnlyAcl "
        "-LiteralPath (Join-Path $homePath $name) -Identity $identity -Directory $false) }; "
        "@{owner_only=$valid;initializations=2} | ConvertTo-Json -Compress"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"owner_only": True, "initializations": 2}
    assert settings.read_text(encoding="utf-8") == "{}\n"
    assert credentials.read_text(encoding="utf-8") == "fixture credentials retained\n"


@pytest.mark.skipif(os.name != "nt", reason="Native owner WRITE_DAC without WRITE_OWNER")
@pytest.mark.parametrize("directory", [True, False])
def test_acl_repair_with_inherited_modify_requires_no_write_owner(
    tmp_path: Path, directory: bool,
) -> None:
    parent = tmp_path / "modify-parent"
    parent.mkdir()
    kind = "$true" if directory else "$false"
    result = helper(
        f". {quote(CONTROLS / 'decision-settings-session.ps1')}; "
        f"$fixtureParentPath={quote(parent)}; $directory={kind}; "
        "$identity=[Security.Principal.WindowsIdentity]::GetCurrent().User; "
        "$acl=[Security.AccessControl.DirectorySecurity]::new(); "
        "$acl.SetAccessRuleProtection($true,$false); "
        "$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new("
        "$identity,'Modify','ContainerInherit,ObjectInherit','None','Allow')); "
        "[IO.FileSystemAclExtensions]::SetAccessControl("
        "[IO.DirectoryInfo]::new($fixtureParentPath),$acl); "
        "$target=Join-Path $fixtureParentPath 'private'; "
        "if($directory){[void][IO.Directory]::CreateDirectory($target)} "
        "else{[IO.File]::WriteAllText($target,'preserve fixture bytes')}; "
        "Protect-DevelopmentPrivatePath -Path $target -Directory $directory; "
        "Protect-DevelopmentPrivatePath -Path $target -Directory $directory; "
        "$valid=Test-ProductSettingsOwnerOnlyAcl -LiteralPath $target "
        "-Identity $identity -Directory $directory; "
        "$preserved=$directory -or ([IO.File]::ReadAllText($target)-ceq 'preserve fixture bytes'); "
        "@{owner_only=$valid;bytes_preserved=$preserved} | ConvertTo-Json -Compress"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"owner_only": True, "bytes_preserved": True}


@pytest.mark.skipif(os.name != "nt", reason="Native Modify-only private session lifecycle")
def test_real_private_session_with_modify_parent_is_protected_and_rotates_on_restart(
    tmp_path: Path,
) -> None:
    wrapper = CONTROLS / "development-settings-session.ps1"
    assert wrapper.is_file(), "DACL-only development session wrapper is missing"
    repository = tmp_path / "repository"
    repository.mkdir()
    result = helper(
        f". {quote(CONTROLS / 'decision-settings-session.ps1')}; . {quote(wrapper)}; "
        f"$repository={quote(repository)}; "
        "$identity=[Security.Principal.WindowsIdentity]::GetCurrent().User; "
        "$acl=[Security.AccessControl.DirectorySecurity]::new(); "
        "$acl.SetAccessRuleProtection($true,$false); "
        "$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new("
        "$identity,'Modify','ContainerInherit,ObjectInherit','None','Allow')); "
        "[IO.FileSystemAclExtensions]::SetAccessControl("
        "[IO.DirectoryInfo]::new($repository),$acl); "
        "$layout=Resolve-DevelopmentLayout -RepositoryRoot $repository "
        "-DevelopmentRoot 'data/development/one'; "
        "$first=New-DevelopmentSettingsSession -RepositoryRoot $repository -Layout $layout "
        "-Origin 'http://localhost:5173' -OwnerId owner; "
        "$hash1=(Get-FileHash -LiteralPath $first.ConfigPath "
        "-Algorithm SHA256).Hash.ToLowerInvariant(); "
        "$entry1=[IO.File]::ReadAllText($first.EntryPath); "
        "$second=New-DevelopmentSettingsSession -RepositoryRoot $repository -Layout $layout "
        "-Origin 'http://localhost:5173' -OwnerId owner; "
        "$hash2=(Get-FileHash -LiteralPath $second.ConfigPath "
        "-Algorithm SHA256).Hash.ToLowerInvariant(); "
        "$entry2=[IO.File]::ReadAllText($second.EntryPath); "
        "$private=Split-Path $second.ConfigPath; "
        "$valid=Test-ProductSettingsOwnerOnlyAcl -LiteralPath $private "
        "-Identity $identity -Directory $true; "
        "foreach($file in @($second.ConfigPath,$second.EntryPath)){ "
        "$valid=$valid -and (Test-ProductSettingsOwnerOnlyAcl -LiteralPath $file "
        "-Identity $identity -Directory $false) }; "
        "@{owner_only=$valid;hashes_match=($first.ConfigSha256 -ceq $hash1 "
        "-and $second.ConfigSha256 -ceq $hash2);tokens_rotated=($hash1 -cne $hash2 "
        "-and $entry1 -cne $entry2);nonempty=($entry1.Length -gt 0 -and $entry2.Length -gt 0)} "
        "| ConvertTo-Json -Compress"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "owner_only": True, "hashes_match": True, "tokens_rotated": True, "nonempty": True,
    }


@pytest.mark.parametrize("delta,allowed", [
    (86_400_000_000, True), (-86_400_000_000, True),
    (0, False), (5, False), (9_999, False),
])
def test_recycled_inaccessible_pid_is_stale_only_with_clear_cim_lifetime_difference(
    tmp_path: Path, delta: int, allowed: bool,
) -> None:
    supervisor = tmp_path / "data/product-local/supervisor.json"
    supervisor.parent.mkdir(parents=True)
    saved_ticks = 638_000_000_000_000_000
    supervisor.write_text(
        json.dumps({"pid": 701, "started_ticks": str(saved_ticks)}), encoding="utf-8",
    )
    result = helper(
        "function Get-Process { $entry=[pscustomobject]@{}; "
        "$entry | Add-Member ScriptProperty StartTime "
        "{ throw 'fixture_start_time_access_denied' }; "
        "$entry }; "
        "function Get-CimInstance { [pscustomobject]@{"
        f"CreationDate=[datetime]::new({saved_ticks + delta},[DateTimeKind]::Utc)}} }}; "
        "function Get-NetTCPConnection { @() }; "
        "function Stop-Process { throw 'fixture_stop_forbidden' }; "
        f"$layout=Resolve-DevelopmentLayout -RepositoryRoot {quote(tmp_path)} "
        "-DevelopmentRoot 'data/development/one'; "
        f"Assert-DevelopmentNoSharedRuntime -RepositoryRoot {quote(tmp_path)} -Layout $layout"
    )
    if allowed:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0
        assert "development_shared_runtime_unverified" in result.stderr
    assert "fixture_stop_forbidden" not in result.stdout + result.stderr
    assert json.loads(supervisor.read_text(encoding="utf-8"))["started_ticks"] == str(saved_ticks)


def test_unknown_cim_and_inaccessible_starttime_still_fail_closed(tmp_path: Path) -> None:
    supervisor = tmp_path / "data/product-local/supervisor.json"
    supervisor.parent.mkdir(parents=True)
    supervisor.write_text(json.dumps({"pid": 701, "started_ticks": "100"}), encoding="utf-8")
    result = helper(
        "function Get-CimInstance { throw 'fixture_cim_unavailable' }; "
        "function Get-Process { $entry=[pscustomobject]@{}; "
        "$entry | Add-Member ScriptProperty StartTime "
        "{ throw 'fixture_start_time_access_denied' }; "
        "$entry }; function Get-NetTCPConnection { @() }; "
        f"$layout=Resolve-DevelopmentLayout -RepositoryRoot {quote(tmp_path)} "
        "-DevelopmentRoot 'data/development/one'; "
        f"Assert-DevelopmentNoSharedRuntime -RepositoryRoot {quote(tmp_path)} -Layout $layout"
    )
    assert result.returncode != 0
    assert "development_shared_runtime_unverified" in result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Native CIM/FILETIME precision and live ownership")
def test_native_current_process_cim_precision_never_bypasses_live_instance(tmp_path: Path) -> None:
    result = helper(
        "$process=Get-Process -Id $PID; "
        "$native=Get-CimInstance Win32_Process -Filter ('ProcessId='+$PID); "
        "$exact=$process.StartTime.ToUniversalTime().Ticks; "
        "$cim=$native.CreationDate.ToUniversalTime().Ticks; "
        f"$repository={quote(tmp_path)}; "
        "$stateDirectory=Join-Path $repository 'data/product-local'; "
        "New-Item -ItemType Directory -Force -Path $stateDirectory | Out-Null; "
        "@{pid=$PID;started_ticks=$exact.ToString();state='stopped'} | ConvertTo-Json "
        "| Set-Content -LiteralPath (Join-Path $stateDirectory 'supervisor.json'); "
        "$layout=Resolve-DevelopmentLayout -RepositoryRoot $repository "
        "-DevelopmentRoot 'data/development/one'; $rejected=$false; "
        "try { Assert-DevelopmentNoSharedRuntime -RepositoryRoot $repository -Layout $layout } "
        "catch { if($_.Exception.Message -ne 'development_shared_live_runtime'){throw}; "
        "$rejected=$true }; "
        "@{difference_ticks=[Math]::Abs([decimal]$exact-[decimal]$cim);"
        "live_rejected=$rejected} | ConvertTo-Json -Compress"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout)
    assert proof["difference_ticks"] <= 10_000
    assert proof["live_rejected"] is True


@pytest.mark.parametrize("created", [True, False])
def test_official_workspace_registration_authenticates_then_uses_idempotent_rpc(
    tmp_path: Path, created: bool,
) -> None:
    workspace_helper = CONTROLS / "development-workspace.ps1"
    assert workspace_helper.is_file(), "official development workspace bootstrap is missing"
    workspace = tmp_path / "data/development/one/dsh-home/Crypto Macro Trader"
    workspace.mkdir(parents=True)
    created_value = "$true" if created else "$false"
    result = helper(
        f". {quote(workspace_helper)}; "
        "$script:calls=@(); $script:authenticatedSession=$null; "
        "function Invoke-WebRequest { param($Uri,$WebSession,$TimeoutSec,$ErrorAction,"
        "[switch]$UseBasicParsing,[switch]$NoProxy) "
        "if (-not $NoProxy -or $Uri -ne 'http://127.0.0.1:18097/?token=fixture-only-token') "
        "{ throw 'fixture_bad_auth_request' }; "
        "$script:authenticatedSession=$WebSession; $script:calls+='authenticate'; "
        "@{StatusCode=200} }; "
        "function Invoke-RestMethod { param($Uri,$Method,$WebSession,$Body,$ContentType,"
        "$TimeoutSec,$ErrorAction,[switch]$NoProxy) "
        "if (-not $NoProxy -or $Method -ne 'Post' -or $ContentType -ne 'application/json' "
        "-or $Uri -ne 'http://127.0.0.1:18097/api/workspace/create' "
        "-or -not [object]::ReferenceEquals($script:authenticatedSession,$WebSession)) "
        "{ throw 'fixture_bad_workspace_transport' }; "
        "$script:calls+='register'; $text=if($Body -is [byte[]]) "
        "{[Text.Encoding]::UTF8.GetString($Body)} else {[string]$Body}; "
        "$script:request=$text | ConvertFrom-Json; "
        "@{type='server-response';rpcId='decision-hub-workspace-register';"
        "result=@{ok=$true;value=@{workspace=@{workspaceId='workspace-fixture';"
        f"path={quote(workspace)}"
        f"}};created={created_value}"
        "}}} }; "
        f"$layout=Resolve-DevelopmentLayout -RepositoryRoot {quote(tmp_path)} "
        "-DevelopmentRoot 'data/development/one'; "
        f"Register-DevelopmentWorkspace -RepositoryRoot {quote(tmp_path)} -Layout $layout "
        "-DshPort 18097 -AuthenticatedUrl 'http://127.0.0.1:18097/?token=fixture-only-token'; "
        "@{calls=$script:calls;request=$script:request} | ConvertTo-Json -Depth 8 -Compress"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    expected_created = str(created).lower()
    assert f"DEVELOPMENT_WORKSPACE_CREATED={expected_created}" in result.stdout.splitlines()
    proof = json.loads(result.stdout.splitlines()[-1])
    assert proof["calls"] == ["authenticate", "register"]
    assert proof["request"] == {
        "type": "client-request", "rpcId": "decision-hub-workspace-register",
        "method": "workspace/create", "payload": {"args": {"request": {"path": str(workspace)}}},
    }
    assert "fixture-only-token" not in result.stdout


@pytest.mark.parametrize("failure", ["authenticate", "rpc", "workspace-path", "workspace-id"])
def test_official_workspace_bootstrap_rejects_failures_without_leaking_authentication(
    tmp_path: Path, failure: str,
) -> None:
    workspace_helper = CONTROLS / "development-workspace.ps1"
    assert workspace_helper.is_file(), "official development workspace bootstrap is missing"
    workspace = tmp_path / "data/development/one/dsh-home/Crypto Macro Trader"
    workspace.mkdir(parents=True)
    result = helper(
        f". {quote(workspace_helper)}; $script:failure={quote(failure)}; "
        "function Invoke-WebRequest { "
        "if($script:failure -eq 'authenticate') { throw 'private fixture-only-token' }; "
        "@{StatusCode=200} }; "
        "function Invoke-RestMethod { "
        "$path=" + quote(workspace) + "; $id='workspace-fixture'; "
        "if($script:failure -eq 'rpc'){return @{result=@{ok=$false}}}; "
        "if($script:failure -eq 'workspace-path'){$path='formal-other-path'}; "
        "if($script:failure -eq 'workspace-id'){$id=''}; "
        "@{type='server-response';rpcId='decision-hub-workspace-register';"
        "result=@{ok=$true;value=@{created=$true;workspace=@{workspaceId=$id;path=$path}}}} }; "
        f"$layout=Resolve-DevelopmentLayout -RepositoryRoot {quote(tmp_path)} "
        "-DevelopmentRoot 'data/development/one'; "
        f"Register-DevelopmentWorkspace -RepositoryRoot {quote(tmp_path)} -Layout $layout "
        "-DshPort 18097 -AuthenticatedUrl 'http://127.0.0.1:18097/?token=fixture-only-token'"
    )
    assert result.returncode != 0
    expected = (
        "development_workspace_authentication_failed" if failure == "authenticate"
        else "development_workspace_registration_failed"
    )
    assert expected in result.stderr
    assert "fixture-only-token" not in result.stdout + result.stderr


@pytest.mark.parametrize("url", [
    "http://external.invalid:18097/?token=fixture-only-token",
    "http://127.0.0.1:3097/?token=fixture-only-token",
    "http://127.0.0.1:18097/other?token=fixture-only-token",
])
def test_workspace_bootstrap_rejects_wrong_auth_origin_before_http(
    tmp_path: Path, url: str,
) -> None:
    workspace_helper = CONTROLS / "development-workspace.ps1"
    assert workspace_helper.is_file(), "official development workspace bootstrap is missing"
    result = helper(
        f". {quote(workspace_helper)}; "
        "function Invoke-WebRequest { throw 'fixture_http_forbidden' }; "
        f"$layout=Resolve-DevelopmentLayout -RepositoryRoot {quote(tmp_path)} "
        "-DevelopmentRoot 'data/development/one'; "
        f"Register-DevelopmentWorkspace -RepositoryRoot {quote(tmp_path)} -Layout $layout "
        f"-DshPort 18097 -AuthenticatedUrl {quote(url)}"
    )
    assert result.returncode != 0
    assert "development_workspace_authentication_url_invalid" in result.stderr
    assert "fixture_http_forbidden" not in result.stderr
    assert "fixture-only-token" not in result.stdout + result.stderr


def test_workspace_bootstrap_is_dev_only_and_precedes_host_readiness_and_research() -> None:
    source = (CONTROLS / "run-product.ps1").read_text(encoding="utf-8")
    branch = source.index(
        "if ($DevelopmentMode) {\n        . (Join-Path $PSScriptRoot 'development-workspace.ps1')"
    )
    register = source.index("Register-DevelopmentWorkspace -RepositoryRoot", branch)
    ready = source.index('Wait-HttpReady -Url "http://127.0.0.1:$DshPort/decision-hub/v1/readiness"')
    research = source.index("$researchWorkerProcess = Start-ProductProcess")
    assert branch < register < ready < research


@pytest.mark.parametrize("mutation", ["foreign-root", "foreign-parent", "missing-stamp"])
def test_stopping_rejects_forged_state_before_choosing_any_target(
    tmp_path: Path, mutation: str,
) -> None:
    # A controlled CIM inventory is necessary to describe foreign PID lifetimes
    # without ever launching, inspecting or stopping a real foreign process.
    setup = (
        f"$repository={quote(tmp_path)}; "
        "$layout=Resolve-DevelopmentLayout -RepositoryRoot $repository "
        "-DevelopmentRoot 'data/development/one'; "
        "$supervisor=[pscustomobject]@{pid=701;started_ticks='100';development_root=$layout.root}; "
        "$entry=[pscustomobject]@{pid=702;started_ticks='200';development_owner_ticks='100';"
        "development_root=$layout.root;children=@()}; "
        "$script:parent=701; $script:commandRoot='data/development/one'; "
        "function Test-ProductProcessIdentity { param($Entry) return [bool]$Entry.started_ticks }; "
        "function Get-ProductProcessDescendants { throw 'fixture_target_selection_forbidden' }; "
        "function Get-CimInstance { param($ClassName,$Filter,$ErrorAction) "
        "if($Filter -eq 'ProcessId=701') { [pscustomobject]@{CommandLine=('pwsh -File '+ "
        "(Join-Path $repository 'infra/dsh/run-product.ps1') + "
        "' -DevelopmentMode -DevelopmentRoot ' + $script:commandRoot)} } "
        "else { [pscustomobject]@{ParentProcessId=$script:parent} } }; "
    )
    change = {
        "foreign-root": "$script:commandRoot='data/development/two'; ",
        "foreign-parent": "$script:parent=999; ",
        "missing-stamp": "$supervisor.started_ticks=$null; ",
    }[mutation]
    result = helper(
        setup + change + "Get-DevelopmentVerifiedProcesses -RepositoryRoot $repository "
        "-Layout $layout -Supervisor $supervisor -Entries @($entry)"
    )
    assert result.returncode != 0
    assert "development_" in result.stderr
    assert "fixture_target_selection_forbidden" not in result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Native Windows owned-process rehearsal")
def test_stopper_stops_own_tree_and_ignores_forged_saved_children(tmp_path: Path) -> None:
    controls = tmp_path / "infra/dsh"
    controls.mkdir(parents=True)
    for name in (
        "stop-dev.ps1", "development-runtime.ps1", "product-processes.ps1",
        "development-watchdog.ps1",
    ):
        shutil.copyfile(CONTROLS / name, controls / name)
    # This fixture owns no sockets, product service, model call or database.
    launcher = controls / "run-product.ps1"
    launcher.write_text(r"""
param([switch]$DevelopmentMode,[string]$DevelopmentRoot)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'development-runtime.ps1')
. (Join-Path $PSScriptRoot 'product-processes.ps1')
$repoRoot=(Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$layout=Resolve-DevelopmentLayout -RepositoryRoot $repoRoot -DevelopmentRoot $DevelopmentRoot
New-Item -ItemType Directory -Force -Path $layout.runtime | Out-Null
$supervisor=[pscustomobject]@{pid=$PID;started_ticks=(Get-ProductProcessStamp $PID);
    development_root=$layout.root}
Write-ProductJson -Path (Join-Path $layout.runtime 'supervisor.json') -Value $supervisor
$child=Start-Process -FilePath (Join-Path $PSHOME 'pwsh.exe') -WindowStyle Hidden -PassThru `
    -ArgumentList @('-NoProfile','-Command','Start-Sleep -Seconds 40')
$entry=[pscustomobject]@{pid=$child.Id;started_ticks=$child.StartTime.ToUniversalTime().Ticks.ToString();
    development_owner_ticks=$supervisor.started_ticks;development_root=$layout.root;
    children=@([pscustomobject]@{pid=[int]$env:FIXTURE_FOREIGN_PID;started_ticks=$env:FIXTURE_FOREIGN_STAMP})}
Write-ProductJson -Path (Join-Path $layout.runtime 'processes.json') -Value @($entry)
Start-Sleep -Seconds 40
""", encoding="utf-8")
    foreign = subprocess.Popen(
        [str(PWSH), "-NoProfile", "-Command", "Start-Sleep -Seconds 40"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    owned: subprocess.Popen[bytes] | None = None
    child_pid: int | None = None
    child_stamp: str | None = None
    try:
        stamp = ps(
            f"(Get-Process -Id {foreign.pid}).StartTime.ToUniversalTime().Ticks.ToString()"
        )
        assert stamp.returncode == 0, stamp.stderr
        env = os.environ.copy()
        env.update(FIXTURE_FOREIGN_PID=str(foreign.pid), FIXTURE_FOREIGN_STAMP=stamp.stdout.strip())
        owned = subprocess.Popen(
            [str(PWSH), "-NoProfile", "-File", str(launcher), "-DevelopmentMode",
             "-DevelopmentRoot", "data/development/one"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
        )
        state = tmp_path / "data/development/one/runtime/processes.json"
        deadline = time.monotonic() + 12
        while not state.exists() and time.monotonic() < deadline:
            assert owned.poll() is None, "fixture supervisor failed before registration"
            time.sleep(0.05)
        assert state.exists(), "fixture supervisor did not register"
        saved = json.loads(state.read_text(encoding="utf-8-sig"))
        saved = saved[0] if isinstance(saved, list) else saved
        child_pid, child_stamp = saved["pid"], saved["started_ticks"]
        result = ps(
            f"& {quote(controls / 'stop-dev.ps1')} -DevelopmentRoot 'data/development/one'"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        owned.wait(timeout=5)
        assert foreign.poll() is None, "forged saved children must never be stopped"
        check = ps(f"if (Get-Process -Id {child_pid} -ErrorAction SilentlyContinue) {{ exit 8 }}")
        assert check.returncode == 0, "owned child survived"
    finally:
        if owned is not None and owned.poll() is None:
            owned.terminate()
            owned.wait(timeout=5)
        if child_pid is not None and child_stamp:
            ps(
                f"$child=Get-Process -Id {child_pid} -ErrorAction SilentlyContinue; "
                "if ($child -and $child.StartTime.ToUniversalTime().Ticks.ToString() "
                f"-eq '{child_stamp}') "
                "{ $child.Kill() }"
            )
        if foreign.poll() is None:
            foreign.terminate()
        foreign.wait(timeout=5)
