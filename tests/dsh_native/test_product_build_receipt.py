"""Pure receipt/fixture checks. Never execute product builds, startup or migration."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.dsh_native.test_validated_product_builds import _receipt, _write

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "infra/dsh/product-build-receipt.ps1"
GUARD = ROOT / "infra/dsh/validated-product-builds.ps1"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="PowerShell pure receipt checks")


def quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def run(script: str):
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


def fixture_repo(tmp_path):
    repo, _ = _receipt(tmp_path)
    identity_path = repo / "extensions/dsh/decision-hub/src/generated-build-identity.ts"
    inputs = json.loads(
        identity_path.read_text().split("PLUGIN_BUILD_INPUTS = ")[1].split(" as const;")[0]
    )
    # The older guard fixture predates the current final-review closure.
    for name in (
        "client/index.js",
        "final-review-gate.ts",
        "reviewer-tool.ts",
        "frozen-review-prompt.ts",
    ):
        relative = f"extensions/dsh/decision-hub/src/{name}"
        _write(repo, relative, f"fixture:{relative}")
        if relative not in inputs:
            inputs.append(relative)
    digest = hashlib.sha256()
    for name in inputs:
        body = (repo / name).read_bytes()
        digest.update(f"{name}\0{len(body)}:".encode())
        digest.update(body + b"\0")
    build_hash = digest.hexdigest()
    identity_path.write_text(
        f'export const PLUGIN_BUILD_HASH = "{build_hash}" as const;\n'
        f"export const PLUGIN_BUILD_INPUTS = {json.dumps(inputs)} as const;\n",
        encoding="utf-8",
    )
    _write(repo, "extensions/dsh/decision-hub/lib/index.js", f'const buildHash = "{build_hash}";')
    for name in (
        "apps/__init__.py",
        "apps/hub_api/entry.py",
        "apps/hub_worker/entry.py",
        "apps/research_mcp/entry.py",
        "apps/hub_mcp/entry.py",
        "packages/kernel/current.py",
        "migrations/versions/0074_fixture.py",
        "tools/component_fixture.py",
        "packs/crypto_macro/recipe.yml",
    ):
        _write(repo, name, f"fixture:{name}")
    for name in (
        "research-graph-projection.v3.json",
        "schemas/quality-projection-context.v1.json",
        "schemas/quality-decision.v1.json",
    ):
        _write(repo, f"packages/kernel/research_recipe_versions/candidates/{name}", "{}\n")
    _write(repo, "infra/dsh/product-build-receipt.ps1", HELPER.read_text())
    _write(repo, "infra/dsh/validated-product-builds.ps1", GUARD.read_text())
    _write(repo, "artifacts/excluded.py", "not installed")
    _write(repo, "packages/kernel/__pycache__/excluded.pyc", "not Python source")
    return repo


def prelude(repo):
    return (
        "$ErrorActionPreference='Stop'; "
        f"$repoRoot={quote(repo)}; $launchId='isolated-fixture'; "
        f". {quote(HELPER)}; . {quote(GUARD)}; "
    )


def test_pure_helper_import_has_no_filesystem_or_process_side_effects(tmp_path):
    result = run(f"Set-Location {quote(tmp_path)}; . {quote(HELPER)}; 'imported'")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not list(tmp_path.iterdir())


def test_complete_inventory_and_receipt_pass_the_unchanged_official_guard(tmp_path):
    repo = fixture_repo(tmp_path)
    output = repo / "artifacts/receipt.json"
    result = run(
        prelude(repo) + "$before=Get-ProductBuildSourceMap; "
        f"New-ProductBuildReceipt -SourceBefore $before -OutputPath {quote(output)} "
        "-Origin 'standard_product_build_only' | ConvertTo-Json -Compress"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    checked = json.loads(result.stdout)
    receipt = json.loads(output.read_text())
    expected = {
        "apps/__init__.py",
        "apps/hub_api/entry.py",
        "apps/hub_worker/entry.py",
        "apps/research_mcp/entry.py",
        "apps/hub_mcp/entry.py",
        "packages/kernel/current.py",
        "migrations/versions/0074_fixture.py",
        "tools/component_fixture.py",
        "packs/crypto_macro/recipe.yml",
        "infra/dsh/product-build-receipt.ps1",
        "infra/dsh/validated-product-builds.ps1",
        "packages/kernel/research_recipe_versions/candidates/research-graph-projection.v3.json",
        "packages/kernel/research_recipe_versions/candidates/schemas/quality-projection-context.v1.json",
        "packages/kernel/research_recipe_versions/candidates/schemas/quality-decision.v1.json",
    }
    assert expected <= set(receipt["sources"])
    assert "artifacts/excluded.py" not in receipt["sources"]
    assert "packages/kernel/__pycache__/excluded.pyc" not in receipt["sources"]
    assert receipt["evidence"]["origin"] == "standard_product_build_only"
    before = receipt["evidence"]["source_before"]
    assert before["schema_version"] == "product-source-before-delta.v1"
    assert before["base"] == "sources"
    assert before["source_count"] == len(receipt["sources"])
    assert {**receipt["sources"], **before["overrides"]} == receipt["sources"]
    assert not receipt["evidence"]["eligible_for_deployment"]
    assert checked["source_count"] == len(receipt["sources"])
    assert checked["artifact_count"] == len(receipt["artifacts"])


def test_source_before_delta_preserves_actual_prior_generated_identity(tmp_path):
    repo = fixture_repo(tmp_path)
    output = repo / "artifacts/delta-receipt.json"
    identity = repo / "extensions/dsh/decision-hub/src/generated-build-identity.ts"
    previous_hash = hashlib.sha256(identity.read_bytes()).hexdigest()
    result = run(
        prelude(repo) + "$before=Get-ProductBuildSourceMap; "
        f"[IO.File]::AppendAllText({quote(identity)}, [Environment]::NewLine); "
        f"New-ProductBuildReceipt -SourceBefore $before -OutputPath {quote(output)} "
        "-Origin 'standard_product_build_only' | Out-Null; $before | ConvertTo-Json -Compress"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    original = json.loads(result.stdout)
    receipt = json.loads(output.read_text())
    before = receipt["evidence"]["source_before"]
    name = identity.relative_to(repo).as_posix()
    assert before["overrides"] == {name: previous_hash}
    assert {**receipt["sources"], **before["overrides"]} == original
    assert before["source_count"] == len(original)


@pytest.mark.parametrize("defect", ["changed", "added"])
def test_changed_installed_source_or_inventory_rejects_receipt(tmp_path, defect):
    repo = fixture_repo(tmp_path)
    output = repo / "artifacts/rejected.json"
    target = repo / ("packages/kernel/current.py" if defect == "changed" else "apps/hub_api/new.py")
    result = run(
        prelude(repo) + "$before=Get-ProductBuildSourceMap; "
        f"[IO.File]::WriteAllText({quote(target)}, 'changed'); "
        f"New-ProductBuildReceipt -SourceBefore $before -OutputPath {quote(output)}"
    )
    assert result.returncode != 0
    code = "source_changed_during_build" if defect == "changed" else "source_inventory_changed"
    assert code in result.stdout + result.stderr
    assert not output.exists()


def test_entrypoints_parse_and_use_shared_receipt_functions_and_explicit_risk_rollback():
    launcher = ROOT / "infra/dsh/run-product.ps1"
    builder = ROOT / "infra/dsh/build-product.ps1"
    script = "$ErrorActionPreference='Stop'; "
    for path in (launcher, builder, HELPER):
        script += (
            "$tokens=$null; $errors=$null; "
            f"[void][System.Management.Automation.Language.Parser]::ParseFile({quote(path)}, "
            "[ref]$tokens, [ref]$errors); if($errors){throw ($errors | Out-String)}; "
        )
    result = run(script + "'parsed'")
    assert result.returncode == 0, result.stdout + result.stderr
    launch_source = launcher.read_text()
    build_source = builder.read_text()
    for text in (launch_source, build_source):
        assert ". (Join-Path $PSScriptRoot 'product-build-receipt.ps1')" in text
        assert "function Get-ProductBuildSourceMap" not in text
    assert "[switch]$DisableRiskMonitoring" in launch_source
    assert (
        "DECISION_HUB_RISK_ENABLED = $(if ($DisableRiskMonitoring) { '0' } else { '1' })"
        in launch_source
    )
    assert "Save-ProductInstalledReleasePointer" not in build_source
    assert "standard_product_build_only" in build_source
    assert hashlib.sha256(GUARD.read_bytes()).hexdigest() == (
        "b7dda65ac008da102396e2436ec775e90b453c52105a015b804f07c3eca62d6a"
    )


def test_explicit_retained_json_is_hashed_and_bound_to_receipt_without_guard_changes(tmp_path):
    repo = fixture_repo(tmp_path)
    payload = '{"schema_version":"research-retained-recipe-registry.v1","registrations":[]}\n'
    sha = hashlib.sha256(payload.encode()).hexdigest()
    name = f"packages/kernel/research_recipe_versions/registries/{sha}.json"
    _write(repo, name, payload)
    (repo / name).write_bytes(payload.encode())
    output = repo / "artifacts/retained-receipt.json"
    result = run(
        prelude(repo) + f"$extras=@{{'{name}'='{sha}'}}; "
        f"$registry=@{{path='{name}';sha256='{sha}'}}; "
        "$before=Get-ProductBuildSourceMap -AdditionalSources $extras; "
        f"New-ProductBuildReceipt -SourceBefore $before -OutputPath {quote(output)} "
        "-AdditionalSources $extras -RetainedRecipeRegistry $registry | ConvertTo-Json -Compress"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(output.read_text())
    assert receipt["sources"][name] == sha
    assert receipt["evidence"]["retained_recipe_archives_v1"]["registry"] == {
        "path": name,
        "sha256": sha,
    }


def test_spot_opt_in_rejects_missing_receipt_before_startup_and_sets_all_child_environment():
    launcher = ROOT / "infra/dsh/run-product.ps1"
    script = (
        "$ErrorActionPreference='Stop'; $tokens=$null; $errors=$null; "
        f"$ast=[System.Management.Automation.Language.Parser]::ParseFile({quote(launcher)},"
        "[ref]$tokens,[ref]$errors); if($errors){throw ($errors|Out-String)}; "
        "$opt=@($ast.FindAll({param($node) "
        "$node -is [System.Management.Automation.Language.IfStatementAst] -and "
        "$node.Clauses[0].Item1.Extent.Text -ceq '$EnableSpotResearchV3' -and "
        r"$node.Extent.Text -match 'product_start_spot_v3_validated_receipt_required|"
        r"\$capabilities ='},$true)); "
        "if($opt.Count -ne 2){throw 'feature_branch_missing'}; "
        "$EnableSpotResearchV3=$true; $validatedBuilds=$null; $rejected=$false; "
        "try{Invoke-Expression $opt[0].Extent.Text}catch{"
        "$rejected=$_.Exception.Message -ceq 'product_start_spot_v3_validated_receipt_required'}; "
        "if(-not $rejected){throw 'unvalidated_start_admitted'}; "
        "$commonAst=$ast.Find({param($node) "
        "$node -is [System.Management.Automation.Language.AssignmentStatementAst] -and "
        "$node.Left.Extent.Text -ceq '$common'},$true); "
        "$base='official.macro,market.cross_asset'; $DshPort=3097; "
        "foreach($enabled in @($false,$true)){ $EnableSpotResearchV3=$enabled; "
        "$capabilities=$base; Invoke-Expression $opt[1].Extent.Text; "
        "Invoke-Expression $commonAst.Extent.Text; "
        "$flag=if($enabled){'1'}else{'0'}; "
        "if($common.DECISION_HUB_SPOT_RESEARCH_ENABLED -cne $flag -or "
        "$common.DECISION_HUB_DSH_WEB_URL -cne 'http://127.0.0.1:3097' -or "
        "$common.DECISION_HUB_RESEARCH_CAPABILITIES -cne $capabilities -or "
        "(($capabilities.Split(',') -ccontains 'spot_market_facts') -ne $enabled)){"
        "throw 'child_feature_environment_invalid'}}; 'bounded-preflight-pass'"
    )
    result = run(script)
    assert result.returncode == 0, result.stdout + result.stderr
    source = launcher.read_text()
    assert source.index("if ($EnableSpotResearchV3)") < source.index("$credentialFile =")
    builder = (ROOT / "infra/dsh/build-product.ps1").read_text()
    assert (
        builder.index("$stage.retention_preparation =")
        < builder.index("$identityOutput = & node")
        < builder.index("$spotInstallation = Install-ProductSpotResearchV3")
        < builder.index("$sourceBefore = Get-ProductBuildSourceMap")
        < builder.index("$stage.ui_build_attempts = 1")
        < builder.index("$stage.host_build_attempts = 1")
    )


@pytest.mark.parametrize("defect", ["pointer_hash", "plugin_hash"])
def test_spot_pointer_evidence_cannot_escape_actual_source_and_plugin_binding(tmp_path, defect):
    repo = fixture_repo(tmp_path)
    pointer_name = "infra/dsh/research-graph-recipe-installed.v3.json"
    _write(repo, pointer_name, "fixture-pointer-not-a-production-installation")
    output = repo / "artifacts/rejected-spot.json"
    pointer_hash = hashlib.sha256((repo / pointer_name).read_bytes()).hexdigest()
    plugin_identity_path = repo / "extensions/dsh/decision-hub/src/generated-build-identity.ts"
    plugin_hash = plugin_identity_path.read_text().split(
        'PLUGIN_BUILD_HASH = "'
    )[1].split('"')[0]
    if defect == "pointer_hash":
        pointer_hash = "0" * 64
    else:
        plugin_hash = "0" * 64
    result = run(
        prelude(repo)
        + "$before=Get-ProductBuildSourceMap; "
        + f"$spot=@{{installed_pointer=@{{path='{pointer_name}';sha256='{pointer_hash}'}}; "
        + f"plugin_build_hash='{plugin_hash}'}}; "
        + f"New-ProductBuildReceipt -SourceBefore $before -OutputPath {quote(output)} "
        + "-SpotResearchV3Installation $spot"
    )
    assert result.returncode != 0
    assert "product_build_spot_v3_installation_changed" in result.stdout + result.stderr
    assert not output.exists()


def retention_publisher():
    spec = importlib.util.spec_from_file_location(
        "product_retention_publisher", ROOT / "infra/dsh/prepare-product-retention.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_retention_publish_links_complete_file_and_preserves_existing_identity(
    tmp_path, monkeypatch
):
    module = retention_publisher()
    target = tmp_path / "resources/original.json"
    raw = b'{"old":true}\n'
    link = module.os.link
    observed = []

    def observed_link(source, destination):
        observed.append(Path(source).read_bytes())
        assert not Path(destination).exists()
        return link(source, destination)

    monkeypatch.setattr(module.os, "link", observed_link)
    module.publish(target, raw)
    module.publish(target, raw)
    with pytest.raises(ValueError, match="retention_immutable_destination_changed"):
        module.publish(target, b"changed")
    assert observed == [raw]
    assert target.read_bytes() == raw
    assert list(target.parent.iterdir()) == [target]


def test_retention_publish_link_failure_leaves_no_partial_destination(tmp_path, monkeypatch):
    module = retention_publisher()
    target = tmp_path / "resources/original.json"

    def interrupted_link(source, destination):
        assert Path(source).read_bytes() == b"complete old resource"
        assert not Path(destination).exists()
        raise OSError("simulated publication failure")

    monkeypatch.setattr(module.os, "link", interrupted_link)
    with pytest.raises(OSError, match="simulated publication failure"):
        module.publish(target, b"complete old resource")
    assert not target.exists()
    assert list(target.parent.iterdir()) == []


@pytest.mark.parametrize("bound", (True, False))
def test_component_archive_evidence_requires_actual_managed_source_binding(tmp_path, bound):
    repo = fixture_repo(tmp_path)
    index_ref = "packages/kernel/market_component_versions/release-fixture/index.json"
    _write(repo, index_ref, '{"fixture":"metadata-only-not-execution"}\n')
    expected = hashlib.sha256((repo / index_ref).read_bytes()).hexdigest()
    output = repo / "artifacts/component-receipt.json"
    extras = f"@{{'{index_ref}'='{expected}'}}" if bound else "@{}"
    result = run(
        prelude(repo)
        + f"$extra={extras}; $before=Get-ProductBuildSourceMap -AdditionalSources $extra; "
        + f"New-ProductBuildReceipt -SourceBefore $before -OutputPath {quote(output)} "
        + "-AdditionalSources $extra -MarketComponentArchives "
        + f"@(@{{index_ref='{index_ref}';index_sha256='{expected}'}}) "
        + "| ConvertTo-Json -Compress"
    )
    if not bound:
        assert result.returncode != 0
        assert "product_build_market_component_archive_unbound" in result.stdout + result.stderr
        assert not output.exists()
        return
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(output.read_text())
    assert receipt["sources"][index_ref] == expected
    assert receipt["evidence"]["retained_market_components_v1"]["archives"] == [
        {"index_ref": index_ref, "index_sha256": expected}
    ]
    assert receipt["evidence"]["native_execution_qualified"] is False
