[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [Parameter(Mandatory = $true)][string]$PriorValidatedBuildReceipt,
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9]{64}$')][string]$PriorValidatedBuildReceiptSha256,
    [string[]]$OriginalSourceRoots = @(),
    [string]$CapturedV3Preparation,
    [ValidatePattern('^[a-f0-9]{64}$')][string]$CapturedV3PreparationSha256,
    [string]$CapturedV3SourceRoot,
    [switch]$InstallSpotResearchV3,
    [string]$RetainedComponentCatalogDatabase,
    [string]$RetainedResearchSelectionDatabase,
    [switch]$CompletedMaintenancePrior
)

if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'product_build_powershell_7_required' }
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$launchId = [datetime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
. (Join-Path $PSScriptRoot 'product-build-receipt.ps1')
$buildGuard = Join-Path $PSScriptRoot 'validated-product-builds.ps1'
if ((Get-ProductBuildSha256 -Path $buildGuard) -cne
    'b7dda65ac008da102396e2436ec775e90b453c52105a015b804f07c3eca62d6a') {
    throw 'harness_install_guard_source_changed'
}
. $buildGuard

# Maintenance owns stop/backup/schema migration; run-product owns publication
# of the installed release locator and startup. This explicit build option is
# the sole producer of the guarded V3 recipe pointer.
$outputPath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $OutputDirectory))
$artifactRoot = Join-Path $repoRoot 'artifacts'
if (-not $outputPath.StartsWith($artifactRoot.TrimEnd('\', '/') +
        [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'product_build_output_outside_artifacts'
}
for ($taskParent = [System.IO.DirectoryInfo]$outputPath;
     $taskParent.FullName -ne $repoRoot; $taskParent = $taskParent.Parent) {
    if ($null -eq $taskParent) { throw 'product_build_output_outside_repository' }
    if ($taskParent.Exists -and
        ($taskParent.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
        throw 'product_build_output_linked_path'
    }
}
if (Test-Path -LiteralPath $outputPath) { throw 'product_build_fresh_output_required' }
[void](Get-Command pnpm -ErrorAction Stop)
[void][System.IO.Directory]::CreateDirectory($outputPath)
$receiptPath = Join-Path $outputPath 'product-validated-builds.json'
$stagePath = Join-Path $outputPath 'build-execution.json'
$stage = [ordered]@{
    schema_version = 'product-build-execution.v1'; launch_id = $launchId
    started_at = [datetime]::UtcNow.ToString('o'); completed_at = $null
    passed = $false; ui_build_attempts = 0; host_build_attempts = 0
    ui_build_exit_code = $null; host_build_exit_code = $null
    builds = 0; migrations = 0; process_starts = 0; process_stops = 0
    pointer_writes = 0; model_submissions = 0; validated_receipt = $null
    retention_preparation = $null; spot_research_v3_installation = $null
    identity_preparation = $null
    market_component_archive_preparation = $null
    receipt_size_estimate = $null
    assessment_manifest_early_preflight = $null
    assessment_manifest_preflight = $null
    native_execution_qualified = $false; whole_product_accepted = $false
    error = $null
}
function Invoke-ProductAssessmentManifestPreflight {
    param([ValidateSet('early', 'final_source_before')][string]$Phase,
        [string]$ExpectedSourceSha256)
    $isEarly = $Phase -ceq 'early'
    $field = if ($isEarly) { 'assessment_manifest_early_preflight' } else { 'assessment_manifest_preflight' }
    $file = if ($isEarly) { 'assessment-manifest-early-preflight.json' } else { 'assessment-manifest-preflight.json' }
    $errorPrefix = if ($isEarly) { 'product_build_assessment_manifest_early_preflight' } else { 'product_build_assessment_manifest_preflight' }
    $basis = if ($isEarly) { 'current_working_tree' } else { 'closed_source_before' }
    $assessmentOutput = & (Join-Path $repoRoot '.venv\Scripts\python.exe') -X utf8 -B `
        (Join-Path $PSScriptRoot 'assessment-manifest-preflight.py') `
        --repository-root $repoRoot --expected-source-sha256 $ExpectedSourceSha256 --phase $Phase
    $assessmentExitCode = $LASTEXITCODE
    $assessmentPath = Join-Path $outputPath $file
    [System.IO.File]::WriteAllText($assessmentPath, ($assessmentOutput -join "`n") + "`n",
        [System.Text.UTF8Encoding]::new($false))
    $stage[$field] = @{ path = $assessmentPath
        sha256 = (Get-ProductBuildSha256 -Path $assessmentPath)
        exit_code = $assessmentExitCode; passed = $false; phase = $Phase; source_basis = $basis }
    if ($assessmentExitCode -ne 0) { throw ($errorPrefix + '_failed') }
    $proof = $assessmentOutput -join "`n" | ConvertFrom-Json
    if ($proof.schema_version -cne 'assessment-manifest-load-preflight.v1' -or
        $proof.scope -cne 'assessment_basis_module_load' -or
        $proof.phase -cne $Phase -or $proof.source_basis -cne $basis -or
        $proof.passed -isnot [bool] -or -not $proof.passed -or
        $proof.source.path -cne 'packages/evals/assessment_basis.py' -or
        $proof.source.sha256 -cne $ExpectedSourceSha256 -or
        $proof.loaded_manifest_hash -isnot [string] -or
        $proof.loaded_manifest_hash -cnotmatch '^[a-f0-9]{64}$' -or
        $null -ne $proof.error_code -or
        $proof.grants_product_receipt -isnot [bool] -or $proof.grants_product_receipt -or
        $proof.native_execution_qualified -isnot [bool] -or $proof.native_execution_qualified -or
        $proof.whole_product_accepted -isnot [bool] -or $proof.whole_product_accepted) {
        throw ($errorPrefix + '_invalid')
    }
    $stage[$field].passed = $true
}
try {
    $priorPath = (Resolve-Path -LiteralPath $PriorValidatedBuildReceipt -ErrorAction Stop).Path
    if ($RetainedComponentCatalogDatabase -and -not $InstallSpotResearchV3) {
        throw 'product_build_component_catalog_requires_spot_installation'
    }
    if ((Get-ProductBuildSha256 -Path $priorPath) -cne $PriorValidatedBuildReceiptSha256) {
        throw 'product_build_prior_receipt_changed'
    }
    # Fail against the current tree before inventories, retention or installation writes.
    $earlyAssessmentSha = Get-ProductBuildSha256 -Path (Join-Path $repoRoot 'packages/evals/assessment_basis.py')
    Invoke-ProductAssessmentManifestPreflight -Phase early -ExpectedSourceSha256 $earlyAssessmentSha
    $currentInventory = Get-ProductBuildSourceMap
    $inventoryPath = Join-Path $outputPath 'candidate-source-inventory.json'
    [System.IO.File]::WriteAllText($inventoryPath,
        ($currentInventory | ConvertTo-Json -Depth 4) + "`n", [System.Text.UTF8Encoding]::new($false))
    $retentionRoot = Join-Path $outputPath 'retention'
    $retentionArgs = @('--repository-root', $repoRoot, '--prior-receipt-path', $priorPath,
        '--prior-receipt-sha256', $PriorValidatedBuildReceiptSha256,
        '--source-inventory', $inventoryPath, '--output-directory', $retentionRoot,
        '--install-candidates')
    if ($CompletedMaintenancePrior) {
        $retentionArgs += '--completed-maintenance-prior'
    }
    foreach ($originalRoot in $OriginalSourceRoots) {
        $retentionArgs += @('--original-source-root', (Resolve-Path -LiteralPath $originalRoot).Path)
    }
    if ($CapturedV3Preparation -or $CapturedV3PreparationSha256 -or $CapturedV3SourceRoot) {
        if (-not ($CapturedV3Preparation -and $CapturedV3PreparationSha256 -and $CapturedV3SourceRoot)) {
            throw 'product_build_v3_capture_arguments_incomplete'
        }
        $retentionArgs += @('--captured-v3-preparation',
            (Resolve-Path -LiteralPath $CapturedV3Preparation -ErrorAction Stop).Path,
            '--captured-v3-preparation-sha256', $CapturedV3PreparationSha256,
            '--captured-v3-source-root',
            (Resolve-Path -LiteralPath $CapturedV3SourceRoot -ErrorAction Stop).Path)
    }
    $researchSelectionDatabase = if ($RetainedResearchSelectionDatabase) {
        $RetainedResearchSelectionDatabase
    } else { $RetainedComponentCatalogDatabase }
    if ($researchSelectionDatabase) {
        $retentionArgs += @('--selection-database',
            (Resolve-Path -LiteralPath $researchSelectionDatabase -ErrorAction Stop).Path)
    }
    & (Join-Path $repoRoot '.venv\Scripts\python.exe') `
        (Join-Path $PSScriptRoot 'prepare-product-retention.py') @retentionArgs
    if ($LASTEXITCODE -ne 0) { throw 'product_build_retention_prepare_failed' }
    $retentionPath = Join-Path $retentionRoot 'retained-recipe-preparation.json'
    $retention = Get-Content -LiteralPath $retentionPath -Raw | ConvertFrom-Json
    if (-not $retention.passed -or $retention.prior_receipt.sha256 -cne $PriorValidatedBuildReceiptSha256) {
        throw 'product_build_retention_evidence_invalid'
    }
    $extraSources = @{}
    foreach ($entry in $retention.additional_sources.PSObject.Properties) {
        $extraSources[$entry.Name] = $entry.Value
    }
    $stage.retention_preparation = @{ path = $retentionPath
        sha256 = (Get-ProductBuildSha256 -Path $retentionPath) }
    $spotInstallation = $null
    $componentArchives = @()
    if ($InstallSpotResearchV3) {
        # Preserve old installation resources first. Generate the actual plugin
        # identity, then install the V3 pointer before the closed SourceBefore.
        # Host build later repeats this same generator and must yield the same
        # bytes; this preparation is not a Host build attempt.
        [void](Get-Command node -ErrorAction Stop)
        $identityOutput = & node (Join-Path $repoRoot 'extensions/dsh/decision-hub/scripts/generate-build-identity.mjs')
        if ($LASTEXITCODE -ne 0) { throw 'product_build_spot_v3_identity_prepare_failed' }
        $identityPath = Join-Path $outputPath 'plugin-identity-preparation.json'
        [System.IO.File]::WriteAllText($identityPath, ($identityOutput -join "`n") + "`n",
            [System.Text.UTF8Encoding]::new($false))
        $stage.identity_preparation = @{ path = $identityPath
            sha256 = (Get-ProductBuildSha256 -Path $identityPath) }
        $spotInstallation = Install-ProductSpotResearchV3
        $stage.spot_research_v3_installation = $spotInstallation
        $stage.pointer_writes = $spotInstallation.pointer_writes
        # The current pure functions are candidates, never inherited B1 approvals.
        # Their original bytes become loadable only when this new standard receipt
        # binds the complete archive, its inventory and its immutable index.
        $componentInventory = Get-ProductBuildSourceMap -AdditionalSources $extraSources
        $componentInventoryPath = Join-Path $outputPath 'component-source-inventory.json'
        [System.IO.File]::WriteAllText($componentInventoryPath,
            ($componentInventory | ConvertTo-Json -Depth 4) + "`n", [System.Text.UTF8Encoding]::new($false))
        $componentProducer = @'
import json, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
from apps.hub_worker.market_component_bootstrap import declare_market_components
from apps.hub_worker.market_component_versions import prepare_component_archive
result = prepare_component_archive(
    repository_root=root, installed=declare_market_components(),
    output_directory=root / 'packages/kernel/market_component_versions' / sys.argv[4],
    candidate_source_map_path=Path(sys.argv[2]), candidate_source_map_sha256=sys.argv[3],
    reuse_receipt_path=Path(sys.argv[5]), reuse_receipt_sha256=sys.argv[6],
)
print(json.dumps(result))
'@
        $componentOutput = $componentProducer | & (Join-Path $repoRoot '.venv\Scripts\python.exe') `
            -X utf8 - $repoRoot $componentInventoryPath `
            (Get-ProductBuildSha256 -Path $componentInventoryPath) $launchId `
            $priorPath $PriorValidatedBuildReceiptSha256
        if ($LASTEXITCODE -ne 0) { throw 'product_build_market_component_archive_prepare_failed' }
        $componentPreparationPath = Join-Path $outputPath 'component-archive-preparation.json'
        [System.IO.File]::WriteAllText($componentPreparationPath, ($componentOutput -join "`n") + "`n",
            [System.Text.UTF8Encoding]::new($false))
        $componentPreparation = $componentOutput -join "`n" | ConvertFrom-Json
        foreach ($entry in $componentPreparation.source_additions.PSObject.Properties) {
            $extraSources[$entry.Name] = $entry.Value
        }
        $priorComponentEvidence = (Get-Content -LiteralPath $priorPath -Raw | ConvertFrom-Json).evidence.retained_market_components_v1
        $plannedPriorArchives = $null
        if ($RetainedComponentCatalogDatabase) {
            $catalogPlanPath = Join-Path $outputPath 'component-catalog-retention-plan.json'
            & (Join-Path $repoRoot '.venv\Scripts\python.exe') `
                (Join-Path $PSScriptRoot 'plan-retained-component-catalog.py') `
                --repository-root $repoRoot --prior-receipt-path $priorPath `
                --prior-receipt-sha256 $PriorValidatedBuildReceiptSha256 `
                --database (Resolve-Path -LiteralPath $RetainedComponentCatalogDatabase).Path `
                --current-archive-preparation $componentPreparationPath `
                --current-archive-preparation-sha256 (Get-ProductBuildSha256 -Path $componentPreparationPath) `
                --output $catalogPlanPath
            if ($LASTEXITCODE -ne 0) { throw 'product_build_component_catalog_plan_failed' }
            $catalogPlan = Get-Content -LiteralPath $catalogPlanPath -Raw | ConvertFrom-Json
            if (-not $catalogPlan.passed -or $catalogPlan.prior_receipt_sha256 -cne $PriorValidatedBuildReceiptSha256) {
                throw 'product_build_component_catalog_plan_invalid'
            }
            $plannedPriorArchives = @($catalogPlan.archives)
            $stage['market_component_catalog_plan'] = @{ path = $catalogPlanPath
                sha256 = (Get-ProductBuildSha256 -Path $catalogPlanPath)
                omitted_source_count = $catalogPlan.omitted_source_count
                omitted_source_map_bytes = $catalogPlan.omitted_source_map_bytes }
        }
        if ($null -ne $priorComponentEvidence) {
            $priorSources = (Get-ResolvedProductBuildReceipt -ReceiptPath $priorPath -RepositoryRoot $repoRoot).sources
            foreach ($priorArchive in @($priorComponentEvidence.archives)) {
                if ($null -ne $plannedPriorArchives -and -not @($plannedPriorArchives | Where-Object {
                        $_.index_ref -ceq $priorArchive.index_ref -and
                        $_.index_sha256 -ceq $priorArchive.index_sha256 }).Count) { continue }
                $prefix = $priorArchive.index_ref.Substring(0, $priorArchive.index_ref.LastIndexOf('/') + 1)
                if ($prefix -cnotmatch '^packages/kernel/market_component_versions/[^/]+/$') {
                    throw 'product_build_prior_component_archive_scope_invalid'
                }
                foreach ($entry in $priorSources.PSObject.Properties) {
                    if ($entry.Name.StartsWith($prefix, [System.StringComparison]::Ordinal)) {
                        $extraSources[$entry.Name] = $entry.Value
                    }
                }
                $componentArchives += $priorArchive
            }
        }
        $sameComponentArchives = @($componentArchives | Where-Object {
            $_.index_ref -ceq $componentPreparation.index_ref })
        if (@($sameComponentArchives | Where-Object {
                $_.index_sha256 -cne $componentPreparation.index_sha256 }).Count) {
            throw 'product_build_component_archive_reuse_conflict'
        }
        if ($sameComponentArchives.Count -eq 0) {
            $componentArchives += @{ index_ref = $componentPreparation.index_ref
                index_sha256 = $componentPreparation.index_sha256 }
        }
        $stage.market_component_archive_preparation = @{ path = $componentPreparationPath
            sha256 = (Get-ProductBuildSha256 -Path $componentPreparationPath)
            archives = @($componentArchives).Count; grants_approval = $false
            reused_existing_archive = $componentPreparation.reused_existing_archive }
    }
    $sourceBefore = Get-ProductBuildSourceMap -AdditionalSources $extraSources
    $sizeIdentity = Get-Content -LiteralPath (Join-Path $repoRoot 'extensions/dsh/decision-hub/src/generated-build-identity.ts') -Raw
    $sizePlugin = [regex]::Match($sizeIdentity, 'PLUGIN_BUILD_HASH\s*=\s*"([a-f0-9]{64})"')
    if (-not $sizePlugin.Success) { throw 'harness_install_generated_identity_invalid' }
    $sizePrior = Get-Content -LiteralPath $priorPath -Raw | ConvertFrom-Json
    $estimatedSize = Get-ProductReceiptSizeEstimate -SourceMap $sourceBefore `
        -PriorArtifactMap $sizePrior.artifacts -PluginHash $sizePlugin.Groups[1].Value `
        -Registry $retention.retained_recipe_archives_v1.registry -Retention $stage.retention_preparation `
        -SpotInstallation $spotInstallation -ComponentArchives $componentArchives
    $knownMinimumSize = Get-ProductReceiptSizeEstimate -SourceMap $sourceBefore `
        -PriorArtifactMap @{} -PluginHash $sizePlugin.Groups[1].Value `
        -Registry $retention.retained_recipe_archives_v1.registry -Retention $stage.retention_preparation `
        -SpotInstallation $spotInstallation -ComponentArchives $componentArchives
    $stage.receipt_size_estimate = @{ utf8_bytes = $estimatedSize; limit_bytes = 2097152
        artifact_basis = 'exact_pinned_prior_receipt_map_not_future_build_output'
        prior_estimate_authoritative = $false
        known_minimum_utf8_bytes = $knownMinimumSize
        known_minimum_basis = 'complete_source_and_evidence_with_empty_artifact_map'
        actual_new_artifacts_verified = $false; final_size_guard_required = $true }
    Assert-ProductReceiptKnownSize -Utf8Bytes $knownMinimumSize
    # Archives and installation resources are complete. Prove the actual fresh
    # Python verifier can load this closed SourceBefore before either build.
    Invoke-ProductAssessmentManifestPreflight -Phase final_source_before `
        -ExpectedSourceSha256 $sourceBefore['packages/evals/assessment_basis.py']
    $stage.ui_build_attempts = 1
    & pnpm --dir (Join-Path $repoRoot 'apps\decision-desk') build
    $stage.ui_build_exit_code = $LASTEXITCODE
    if ($LASTEXITCODE -ne 0) { throw 'Decision Desk build failed.' }
    $stage.builds = 1
    $stage.host_build_attempts = 1
    & pnpm --dir (Join-Path $repoRoot 'extensions\dsh\decision-hub') build
    $stage.host_build_exit_code = $LASTEXITCODE
    if ($LASTEXITCODE -ne 0) { throw 'Decision Hub DSH plugin build failed.' }
    $stage.builds = 2
    $checked = New-ProductBuildReceipt -SourceBefore $sourceBefore -OutputPath $receiptPath `
        -Origin 'standard_product_build_only' -AdditionalSources $extraSources `
        -RetainedRecipeRegistry $retention.retained_recipe_archives_v1.registry `
        -RetentionEvidence $stage.retention_preparation `
        -SpotResearchV3Installation $spotInstallation -MarketComponentArchives $componentArchives
    $stage.validated_receipt = [ordered]@{
        path = $checked.receipt_path; sha256 = (Get-ProductBuildSha256 -Path $receiptPath)
        source_count = $checked.source_count; artifact_count = $checked.artifact_count
        plugin_build_hash = $checked.plugin_build_hash
    }
    $stage.passed = $true
    Write-Host "VALIDATED_BUILD_RECEIPT=$receiptPath"
} catch {
    $stage.error = $_.Exception.Message
    throw
} finally {
    $stage.completed_at = [datetime]::UtcNow.ToString('o')
    [System.IO.File]::WriteAllText($stagePath, ($stage | ConvertTo-Json -Depth 8) + "`n",
        [System.Text.UTF8Encoding]::new($false))
}
