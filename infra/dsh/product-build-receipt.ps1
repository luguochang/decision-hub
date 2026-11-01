# Pure product build receipt functions. The caller supplies repoRoot and launchId.
# Importing this file does not build, inspect credentials, publish a pointer or start services.
. (Join-Path $PSScriptRoot 'product-build-validation.ps1')
function Get-ProductBuildSha256 {
    param([string]$Path)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($Path)
    try { return [System.BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $sha.Dispose() }
}
function Get-ProductBuildSourceMap {
    param([System.Collections.IDictionary]$AdditionalSources = @{})
    $paths = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
    foreach ($path in @('package.json', 'pnpm-lock.yaml', 'pnpm-workspace.yaml',
        'apps/decision-desk/index.html', 'apps/decision-desk/package.json',
        'apps/decision-desk/tsconfig.json', 'apps/decision-desk/vite.config.ts',
        'packages/contracts_ts/package.json', 'extensions/dsh/decision-hub/package.json',
        'extensions/dsh/decision-hub/cordis.patch.yml', 'extensions/dsh/decision-hub/tsconfig.json',
        'extensions/dsh/decision-hub/vite.host.config.ts', 'infra/dsh/upstream.lock.json',
        'apps/__init__.py',
        'docs/design/decision-product-control-2026-10-17/02_DECISION_METHOD_AND_EVALUATION.md',
        'packages/kernel/research_recipe_versions/candidates/research-graph-projection.v3.json',
        'packages/kernel/research_recipe_versions/candidates/schemas/quality-projection-context.v1.json',
        'packages/kernel/research_recipe_versions/candidates/schemas/quality-decision.v1.json')) {
        [void]$paths.Add($path)
    }
    # Include all installed Python consumers/migrations, actual Pack originals,
    # and product startup controls; future V4 files join the same closed scan.
    foreach ($directory in @('apps/decision-desk/src', 'apps/decision-desk/public',
        'packages/contracts_ts/src', 'contracts/schemas', 'extensions/dsh/decision-hub/src',
        'extensions/dsh/decision-hub/scripts', 'packs', 'infra/dsh',
        'packages', 'apps/hub_api', 'apps/hub_worker', 'apps/research_mcp', 'apps/hub_mcp', 'migrations', 'tools')) {
        $root = Get-Item -LiteralPath (Join-Path $repoRoot $directory) -Force -ErrorAction Stop
        if (-not $root.PSIsContainer -or ($root.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw 'harness_install_source_directory_invalid'
        }
        if ($directory -ceq 'packages') {
            foreach ($archiveName in @('packages/kernel/research_recipe_versions', 'packages/kernel/market_component_versions')) {
                $archivePath = Join-Path $repoRoot $archiveName
                if (Test-Path -LiteralPath $archivePath) {
                    $archiveRoot = Get-Item -LiteralPath $archivePath -Force -ErrorAction Stop
                    if (-not $archiveRoot.PSIsContainer -or
                        ($archiveRoot.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
                        throw 'harness_install_source_directory_invalid'
                    }
                }
            }
        }
        $items = if ($directory -cin @('packages', 'apps/hub_api', 'apps/hub_worker', 'apps/research_mcp', 'apps/hub_mcp', 'migrations', 'tools')) {
            # Match the installed Python source closure; dependency stores and
            # bytecode are not product Python originals. FileMap checks every
            # selected file and its parent directories for links.
            @(Get-ChildItem -LiteralPath $root.FullName -Recurse -File -Filter '*.py' -Force -ErrorAction Stop)
        } else {
            @(Get-ChildItem -LiteralPath $root.FullName -Recurse -Force -ErrorAction Stop)
        }
        foreach ($item in $items) {
            if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                throw 'harness_install_linked_source'
            }
            if (-not $item.PSIsContainer) {
                $relative = $item.FullName.Substring($repoRoot.Length + 1).Replace('\', '/')
                # Only versioned immutable archives enter through hash-bound
                # additions; live helpers in these namespaces remain scanned.
                $recipeArchive = $relative -cmatch '^packages/kernel/research_recipe_versions/(?:[a-f0-9]{8}|[a-f0-9]{64})/'
                $componentArchive = $relative -cmatch '^packages/kernel/market_component_versions/[0-9]{8}T[0-9]{9}Z/'
                if ($directory -ceq 'packages' -and ($recipeArchive -or $componentArchive)) {
                    continue
                }
                [void]$paths.Add($relative)
            }
        }
    }
    # Retained skill compilers also authenticate non-Python originals. Keep
    # these finite immutable resources in the same source/parent-link guard.
    $skillArchiveName = 'packages/kernel/research_skill_versions'
    $skillArchivePath = Join-Path $repoRoot $skillArchiveName
    if (Test-Path -LiteralPath $skillArchivePath) {
        $skillArchiveRoot = Get-Item -LiteralPath $skillArchivePath -Force -ErrorAction Stop
        if (-not $skillArchiveRoot.PSIsContainer -or
            ($skillArchiveRoot.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw 'harness_install_source_directory_invalid'
        }
        foreach ($item in Get-ChildItem -LiteralPath $skillArchiveRoot.FullName -Recurse -Force -ErrorAction Stop) {
            if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                throw 'harness_install_linked_source'
            }
            if (-not $item.PSIsContainer -and $item.Extension -cin @('.json', '.yaml')) {
                $relative = $item.FullName.Substring($repoRoot.Length + 1).Replace('\', '/')
                if ($relative -cnotmatch '^packages/kernel/research_skill_versions/[a-f0-9]{64}/.+\.(?:json|yaml)$') {
                    throw 'harness_install_skill_archive_path_invalid'
                }
                [void]$paths.Add($relative)
            }
        }
    }
    foreach ($name in $AdditionalSources.Keys) { [void]$paths.Add($name) }
    $result = Get-ProductBuildFileMap -Paths @($paths)
    foreach ($name in $AdditionalSources.Keys) {
        if ($AdditionalSources[$name] -cnotmatch '^[a-f0-9]{64}$' -or
            $result[$name] -cne $AdditionalSources[$name]) {
            throw 'harness_install_retained_source_changed'
        }
    }
    return $result
}

function Get-ProductBuildFileMap {
    param([string[]]$Paths)
    $result = [System.Collections.Generic.SortedDictionary[string,string]]::new([System.StringComparer]::Ordinal)
    foreach ($name in $Paths) {
        $item = Get-Item -LiteralPath (Join-Path $repoRoot $name) -Force -ErrorAction Stop
        if ($item.PSIsContainer -or ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw 'harness_install_file_invalid'
        }
        for ($parent = $item.Directory; $parent.FullName -ne $repoRoot; $parent = $parent.Parent) {
            if ($null -eq $parent -or ($parent.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
                throw 'harness_install_linked_source'
            }
        }
        $result.Add($name, (Get-ProductBuildSha256 -Path $item.FullName))
    }
    return $result
}

# Invoked only by the explicit build option, after old-release retention and
# before SourceBefore. Dot-importing these functions never installs a recipe.
function Install-ProductSpotResearchV3 {
    $producer = @'
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
from packages.kernel.decision_hub_kernel.application import research_graph_recipe_v3 as recipe

if recipe._ROOT.resolve() != root:
    raise ValueError("product_build_spot_v3_loaded_root_invalid")
manifest = recipe.graph_recipe_manifest_v3()
identity_ref = "extensions/dsh/decision-hub/src/generated-build-identity.ts"
identity_raw = recipe._read_resource(identity_ref)
matches = re.findall(rb'PLUGIN_BUILD_HASH\s*=\s*"([a-f0-9]{64})"', identity_raw)
if len(matches) != 1:
    raise ValueError("product_build_spot_v3_plugin_identity_invalid")
plugin_hash = matches[0].decode("ascii")
pointer = recipe.GraphRecipeInstalledPointerV3(
    registration=manifest.registration,
    dependency_hash=recipe.observation_sha(manifest.model_dump(mode="json")),
    plugin_build_hash=plugin_hash,
)
raw = (recipe.observation_json(pointer.model_dump(mode="json")) + "\n").encode("utf-8")
current = root
parts = recipe.INSTALLED_POINTER_REF.split("/")
for index, part in enumerate(parts):
    current = current / part
    try:
        status = current.lstat()
    except FileNotFoundError:
        if index != len(parts) - 1:
            raise
        break
    if current.is_symlink() or getattr(status, "st_file_attributes", 0) & 0x400:
        raise ValueError("product_build_spot_v3_pointer_linked")
    if (index < len(parts) - 1) != current.is_dir():
        raise ValueError("product_build_spot_v3_pointer_path_invalid")
destination = root / recipe.INSTALLED_POINTER_REF
before = recipe._read_resource(recipe.INSTALLED_POINTER_REF) if destination.exists() else None
changed = before != raw
temporary = None
try:
    if changed:
        temporary = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".tmp")
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if (recipe.graph_recipe_manifest_v3() != manifest
            or recipe._read_resource(identity_ref) != identity_raw):
            raise ValueError("product_build_spot_v3_sources_changed")
        current_raw = recipe._read_resource(recipe.INSTALLED_POINTER_REF) if destination.exists() else None
        if current_raw != before:
            raise ValueError("product_build_spot_v3_pointer_changed")
        if before is None:
            # Windows rename rejects a concurrent first publisher; replace is
            # used only after retention has preserved an existing installation.
            os.rename(temporary, destination)
        else:
            os.replace(temporary, destination)
        temporary = None
    if (recipe._read_resource(recipe.INSTALLED_POINTER_REF) != raw
        or recipe.graph_recipe_manifest_v3() != manifest
        or recipe._read_resource(identity_ref) != identity_raw):
        raise ValueError("product_build_spot_v3_installation_changed")
finally:
    if temporary is not None and temporary.exists():
        temporary.unlink()
print(json.dumps({
    "schema_version": "product-build-spot-v3-installation.v1",
    "installed_pointer": {"path": recipe.INSTALLED_POINTER_REF, "sha256": hashlib.sha256(raw).hexdigest()},
    "plugin_build_hash": plugin_hash,
    "dependency_hash": pointer.dependency_hash,
    "registration": manifest.registration.model_dump(mode="json"),
    "source_count": len(manifest.source_files), "schema_count": len(manifest.schema_files),
    "previous_pointer_sha256": hashlib.sha256(before).hexdigest() if before is not None else None,
    "pointer_writes": int(changed),
}, sort_keys=True))
'@
    $output = $producer | & (Join-Path $repoRoot '.venv\Scripts\python.exe') -B - $repoRoot
    if ($LASTEXITCODE -ne 0) { throw 'product_build_spot_v3_installation_failed' }
    $proof = ($output -join "`n") | ConvertFrom-Json -ErrorAction Stop
    if ($proof.schema_version -cne 'product-build-spot-v3-installation.v1' -or
        $proof.installed_pointer.path -cne 'infra/dsh/research-graph-recipe-installed.v3.json' -or
        $proof.installed_pointer.sha256 -cnotmatch '^[a-f0-9]{64}$' -or
        (Get-ProductBuildSha256 -Path (Join-Path $repoRoot $proof.installed_pointer.path)) -cne
            $proof.installed_pointer.sha256) {
        throw 'product_build_spot_v3_installation_proof_invalid'
    }
    return $proof
}

function Assert-ProductSpotResearchV3Receipt {
    param([Parameter(Mandatory = $true)][string]$ReceiptPath,
        [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ReceiptSha256)
    $verifier = @'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
from packages.kernel.decision_hub_kernel.application import research_graph_recipe_v3 as recipe

if recipe._ROOT.resolve() != root:
    raise ValueError("product_start_spot_v3_loaded_root_invalid")
receipt_path = Path(sys.argv[2]).resolve()
expected = sys.argv[3]
if hashlib.sha256(receipt_path.read_bytes()).hexdigest() != expected:
    raise ValueError("product_start_spot_v3_receipt_changed")
recipe.configure_graph_recipe_v3_from_validated_release(receipt_path=receipt_path, receipt_sha256=expected)
if hashlib.sha256(receipt_path.read_bytes()).hexdigest() != expected:
    raise ValueError("product_start_spot_v3_receipt_changed")
print(json.dumps({"validated_receipt_sha256": expected, "recipe_version": recipe.RECIPE_VERSION_V3}))
'@
    $output = $verifier | & (Join-Path $repoRoot '.venv\Scripts\python.exe') -B - $repoRoot $ReceiptPath $ReceiptSha256
    if ($LASTEXITCODE -ne 0) { throw 'product_start_spot_v3_validated_installation_required' }
    return (($output -join "`n") | ConvertFrom-Json -ErrorAction Stop)
}

function New-ProductSourceBeforeDelta {
    param([System.Collections.IDictionary]$SourceBefore,
        [System.Collections.IDictionary]$SourceAfter)
    if ($SourceBefore.Count -ne $SourceAfter.Count -or
        @(Compare-Object @($SourceBefore.Keys) @($SourceAfter.Keys) -CaseSensitive).Count) {
        throw 'harness_install_source_inventory_changed'
    }
    $overrides = [System.Collections.Generic.SortedDictionary[string,string]]::new([System.StringComparer]::Ordinal)
    foreach ($name in $SourceBefore.Keys) {
        if ($SourceBefore[$name] -cne $SourceAfter[$name]) { $overrides[$name] = $SourceBefore[$name] }
    }
    # This is a lossless reference to the complete source map in the same receipt.
    # Restore before by overlaying overrides onto the complete resolved map.
    return [ordered]@{ schema_version = 'product-source-before-delta.v2'
        base = 'effective_sources'; source_count = $SourceBefore.Count; overrides = $overrides }
}

function Get-ProductBuildArtifactMap {
    param([System.Collections.IDictionary]$SourceMap)
    $publicSources = [ordered]@{}
    foreach ($name in $SourceMap.Keys) {
        if ($name.StartsWith('apps/decision-desk/public/', [System.StringComparison]::Ordinal)) {
            $publicSources[$name] = $SourceMap[$name]
        }
    }
    # The current Vite manifest binds emitted chunks, lazy imports, CSS and
    # assets; public copies and all Host lib files join the same actual map.
    # Historical hashed UI outputs stay on disk, outside this build's closure.
    $output = ($publicSources | ConvertTo-Json -Depth 4 -Compress) |
        & (Join-Path $repoRoot '.venv/Scripts/python.exe') -X utf8 -B `
            (Join-Path $repoRoot 'infra/dsh/product-artifact-manifest.py') --repository-root $repoRoot
    if ($LASTEXITCODE -ne 0) { throw 'harness_install_artifact_manifest_invalid' }
    $map = ($output -join "`n") | ConvertFrom-Json -AsHashtable -ErrorAction Stop
    if ($map -isnot [System.Collections.IDictionary] -or $map.Count -eq 0) {
        throw 'harness_install_artifact_manifest_invalid'
    }
    # Independent actual hashing retains FileMap's repository/link checks.
    $actual = Get-ProductBuildFileMap -Paths @($map.Keys)
    foreach ($name in $map.Keys) {
        if ($map[$name] -cnotmatch '^[a-f0-9]{64}$' -or $map[$name] -cne $actual[$name]) {
            throw 'harness_install_artifact_changed'
        }
    }
    return $actual
}

function Get-ProductReceiptSizeEstimate {
    param($SourceMap, $PriorArtifactMap, [string]$PluginHash, $Registry, $Retention,
        $SpotInstallation, $ComponentArchives)
    # Empty artifacts yield a strict known-byte lower bound. A prior map is
    # informational only: it can include historical outputs absent this build.
    $sourceProjection = New-ProductHistoricalSourceMap -SourceMap $SourceMap -RepositoryRoot $repoRoot
    $preview = [ordered]@{ schema = 'product-validated-builds.v2'; repository_root = $repoRoot
        sources = $sourceProjection.sources; historical_source_map = $sourceProjection.historical_source_map
        artifacts = $PriorArtifactMap; plugin_build_hash = $PluginHash
        evidence = [ordered]@{ observed_at = [datetime]::UtcNow.ToString('o'); launch_id = $launchId
            origin = 'standard_product_build_only'; ui_build_count = 1; host_build_count = 1
            source_before = (New-ProductSourceBeforeDelta -SourceBefore $SourceMap -SourceAfter $SourceMap)
            source_stable_except_standard_generated_identity = $true
            native_execution_qualified = $false; eligible_for_deployment = $false } }
    $preview.evidence['retained_recipe_archives_v1'] = @{ registry = $Registry }
    $preview.evidence['retention_preparation'] = $Retention
    if ($null -ne $SpotInstallation) {
        $preview.evidence['research_graph_recipe_v3'] = @{ installed_pointer = $SpotInstallation.installed_pointer }
    }
    if (@($ComponentArchives).Count) {
        $preview.evidence['retained_market_components_v1'] = @{ archives = @($ComponentArchives) }
    }
    return [System.Text.UTF8Encoding]::new($false).GetByteCount(
        ($preview | ConvertTo-Json -Depth 100 -Compress) + "`n")
}

function Assert-ProductReceiptKnownSize {
    param([long]$Utf8Bytes)
    if ($Utf8Bytes -gt 2097152) { throw 'product_build_receipt_known_minimum_oversized' }
}

function ConvertTo-ProductBuildReceiptBytes {
    param($Receipt)
    # The final actual map remains subject to the fixed guard's byte budget.
    $raw = [System.Text.UTF8Encoding]::new($false).GetBytes(($Receipt | ConvertTo-Json -Depth 100 -Compress) + "`n")
    if ($raw.Length -gt 2097152) { throw 'harness_install_receipt_oversized' }
    return ,$raw
}

function New-ProductBuildReceipt {
    param($SourceBefore, [string]$OutputPath, [string]$Origin = 'standard_product_start',
        [System.Collections.IDictionary]$AdditionalSources = @{},
        $RetainedRecipeRegistry = $null, $RetentionEvidence = $null,
        $SpotResearchV3Installation = $null, $MarketComponentArchives = @())
    $sourceAfter = Get-ProductBuildSourceMap -AdditionalSources $AdditionalSources
    if ($SourceBefore.Count -ne $sourceAfter.Count -or
        @(Compare-Object @($SourceBefore.Keys) @($sourceAfter.Keys) -CaseSensitive).Count) {
        throw 'harness_install_source_inventory_changed'
    }
    foreach ($name in $SourceBefore.Keys) {
        if ($name -cne 'extensions/dsh/decision-hub/src/generated-build-identity.ts' -and
            $SourceBefore[$name] -cne $sourceAfter[$name]) {
            throw 'harness_install_source_changed_during_build'
        }
    }
    $artifacts = Get-ProductBuildArtifactMap -SourceMap $sourceAfter
    $identity = Get-Content -LiteralPath (Join-Path $repoRoot 'extensions/dsh/decision-hub/src/generated-build-identity.ts') -Raw
    $match = [regex]::Match($identity, 'PLUGIN_BUILD_HASH\s*=\s*"([a-f0-9]{64})"')
    if (-not $match.Success) { throw 'harness_install_generated_identity_invalid' }
    $sourceProjection = New-ProductHistoricalSourceMap -SourceMap $sourceAfter -RepositoryRoot $repoRoot
    $receipt = [ordered]@{ schema = 'product-validated-builds.v2'; repository_root = $repoRoot
        sources = $sourceProjection.sources; historical_source_map = $sourceProjection.historical_source_map
        artifacts = $artifacts; plugin_build_hash = $match.Groups[1].Value
        evidence = [ordered]@{ observed_at = [datetime]::UtcNow.ToString('o'); launch_id = $launchId
            origin = $Origin; ui_build_count = 1; host_build_count = 1
            source_before = (New-ProductSourceBeforeDelta -SourceBefore $SourceBefore -SourceAfter $sourceAfter)
            source_stable_except_standard_generated_identity = $true
            native_execution_qualified = $false; eligible_for_deployment = $false } }
    if ($null -ne $RetainedRecipeRegistry) {
        if ($RetainedRecipeRegistry.path -cnotmatch
                '^packages/kernel/research_recipe_versions/registries/([a-f0-9]{64})\.json$' -or
            $RetainedRecipeRegistry.sha256 -cne $Matches[1] -or
            $sourceAfter[$RetainedRecipeRegistry.path] -cne $RetainedRecipeRegistry.sha256) {
            throw 'harness_install_retained_registry_unbound'
        }
        $receipt.evidence['retained_recipe_archives_v1'] = @{ registry = $RetainedRecipeRegistry }
        if ($null -ne $RetentionEvidence) {
            $receipt.evidence['retention_preparation'] = $RetentionEvidence
        }
    }
    if ($null -ne $SpotResearchV3Installation) {
        $pointer = $SpotResearchV3Installation.installed_pointer
        if ($pointer.path -cne 'infra/dsh/research-graph-recipe-installed.v3.json' -or
            $pointer.sha256 -cnotmatch '^[a-f0-9]{64}$' -or
            $sourceAfter[$pointer.path] -cne $pointer.sha256 -or
            $SpotResearchV3Installation.plugin_build_hash -cne $receipt.plugin_build_hash -or
            $SourceBefore['extensions/dsh/decision-hub/src/generated-build-identity.ts'] -cne
                $sourceAfter['extensions/dsh/decision-hub/src/generated-build-identity.ts']) {
            throw 'product_build_spot_v3_installation_changed'
        }
        $receipt.evidence['research_graph_recipe_v3'] = @{ installed_pointer = $pointer }
    }
    if (@($MarketComponentArchives).Count) {
        foreach ($archive in $MarketComponentArchives) {
            if ($archive.index_ref -cnotmatch '^packages/kernel/market_component_versions/[^/]+/index\.json$' -or
                $archive.index_sha256 -cnotmatch '^[a-f0-9]{64}$' -or
                $sourceAfter[$archive.index_ref] -cne $archive.index_sha256) {
                throw 'product_build_market_component_archive_unbound'
            }
        }
        $receipt.evidence['retained_market_components_v1'] = @{ archives = @($MarketComponentArchives) }
    }
    # Preserve every proof entry while avoiding whitespace growth at the fixed
    # receipt-size boundary. Consumers authenticate bytes and parse JSON.
    $raw = ConvertTo-ProductBuildReceiptBytes -Receipt $receipt
    $temporary = $OutputPath + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    try {
        $stream = [System.IO.File]::Open($temporary, [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        try { $stream.Write($raw, 0, $raw.Length); $stream.Flush($true) }
        finally { $stream.Dispose() }
        $checked = Test-ProductValidatedBuilds -ReceiptPath $temporary -RepositoryRoot $repoRoot
        if ($null -ne $SpotResearchV3Installation) {
            # Authenticate the entire V3 source/schema closure through the actual
            # reader, after the unchanged standard product guard has passed.
            [void](Assert-ProductSpotResearchV3Receipt -ReceiptPath $temporary `
                -ReceiptSha256 (Get-ProductBuildSha256 -Path $temporary))
        }
        # Move never overwrites an existing launch receipt; it publishes only
        # completely flushed and validated bytes on the same filesystem.
        [System.IO.File]::Move($temporary, $OutputPath)
        return Test-ProductValidatedBuilds -ReceiptPath $OutputPath -RepositoryRoot $repoRoot
    } finally {
        if ([System.IO.File]::Exists($temporary)) { [System.IO.File]::Delete($temporary) }
    }
}
