[CmdletBinding()]
param(
    [int]$DshPort = 3097,
    [int]$ApiPort = 8047,
    [ValidateRange(30, 180)][int]$ApiReadyTimeoutSeconds = 90,
    [ValidateRange(5, 120)][int]$ApiGracefulShutdownTimeoutSeconds = 30,
    [int]$McpPort = 8037,
    [int]$AcquisitionPort = 8038,
    [string]$DshHome = '',
    [switch]$DevelopmentMode,
    [string]$DevelopmentRoot = '',
    [ValidatePattern('^[a-f0-9]{32}$')][string]$WatchdogIntentId = '',
    [string]$CredentialFile = '',
    [int]$DeskPort = 5173,
    [switch]$BuildPlugin,
    [ValidateNotNullOrEmpty()][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$')][string]$ResearchModel = 'gpt-6.1-sol',
    [string]$ValidatedBuildReceipt = '',
    [switch]$DisableRiskMonitoring,
    [switch]$EnableSpotResearchV3,
    [switch]$EnableActionProduct,
    [switch]$EnableActionReview,
    [switch]$EnableActionExecution,
    [switch]$EnableResearchLearning,
    [switch]$EnableDurableSkillCallbacks,
    [string]$HarnessCompareConfig = '',
    [string]$HarnessCompareSha256 = '',
    [string]$HistoryPrivateConfig = '',
    [string]$HistoryPrivateConfigSha256 = '',
    [string]$HistoryQualificationPrivateConfig = '',
    [string]$HistoryQualificationPrivateConfigSha256 = '',
    [string]$HistoryOutcomePrivateConfig = '',
    [string]$HistoryOutcomePrivateConfigSha256 = ''
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$developmentLayout = $null
$watchdogHandoff = $null
$developmentCredentialFile = $CredentialFile
if ($DevelopmentMode) {
    if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'development_powershell7_required' }
    . (Join-Path $PSScriptRoot 'development-runtime.ps1')
    if ($DshHome) { throw 'development_home_override_forbidden' }
    if (-not $PSBoundParameters.ContainsKey('ApiPort')) { $ApiPort=18047 }
    if (-not $PSBoundParameters.ContainsKey('DshPort')) { $DshPort=18097 }
    if (-not $PSBoundParameters.ContainsKey('McpPort')) { $McpPort=18037 }
    if (-not $PSBoundParameters.ContainsKey('AcquisitionPort')) { $AcquisitionPort=18038 }
    if (-not $DevelopmentRoot) { $DevelopmentRoot='data/development/default' }
    Assert-DevelopmentLaunchPolicy -Ports @($ApiPort,$DshPort,$McpPort,$AcquisitionPort,$DeskPort) `
        -FeatureRequested ([bool]($ValidatedBuildReceipt -or $EnableSpotResearchV3 -or $EnableActionProduct -or
            $EnableActionReview -or $EnableActionExecution -or $EnableResearchLearning -or
            $EnableDurableSkillCallbacks -or $HarnessCompareConfig -or $HarnessCompareSha256 -or
            $HistoryPrivateConfig -or $HistoryPrivateConfigSha256 -or $HistoryQualificationPrivateConfig -or
            $HistoryQualificationPrivateConfigSha256 -or $HistoryOutcomePrivateConfig -or $HistoryOutcomePrivateConfigSha256))
    $developmentLayout = Resolve-DevelopmentLayout -RepositoryRoot $repoRoot -DevelopmentRoot $DevelopmentRoot
    if ($WatchdogIntentId) {
        . (Join-Path $PSScriptRoot 'product-processes.ps1')
        . (Join-Path $PSScriptRoot 'development-watchdog.ps1')
        $watchdogHandoff = Enter-DevelopmentWatchdogLaunch -Layout $developmentLayout -IntentId $WatchdogIntentId
    }
    Assert-DevelopmentInstanceEnvironment -Layout $developmentLayout
    Assert-DevelopmentNoSharedRuntime -RepositoryRoot $repoRoot -Layout $developmentLayout
} elseif ($DevelopmentRoot -or $CredentialFile -or $BuildPlugin -or $WatchdogIntentId -or $PSBoundParameters.ContainsKey('DeskPort') -or
    $env:DECISION_HUB_DEVELOPMENT_ROOT -or $env:DECISION_HUB_DEVELOPMENT_MODE -eq '1') {
    throw 'development_parameters_require_explicit_mode'
}
if ($EnableActionProduct -and -not $EnableSpotResearchV3) {
    throw 'action_product_spot_research_required'
}
if ($EnableActionExecution -and -not $EnableActionProduct) {
    throw 'EnableActionExecution requires EnableActionProduct.'
}
if ($EnableActionReview -and -not $EnableActionProduct) {
    throw 'action_review_requires_action_product'
}
if (($EnableResearchLearning -or $EnableDurableSkillCallbacks) -and
    (-not $EnableSpotResearchV3 -or -not $EnableActionProduct)) {
    throw 'research_skill_features_require_spot_action_product'
}
if ($EnableResearchLearning -and -not $EnableActionExecution) {
    throw 'research_learning_action_execution_required'
}
# Flags are explicit and shared by API, both research workers and Evolution.
# No separate process or schema mutation is started by either flag.
# Optional private manual-comparison configuration is inherited by the same
# API/Worker/MCP processes. It is a content identity, never an owner credential.
if ([bool]$HarnessCompareConfig -ne [bool]$HarnessCompareSha256) { throw 'harness_frozen_private_config_required' }
if ($HarnessCompareConfig) {
    if ($HarnessCompareSha256 -cnotmatch '^[a-f0-9]{64}$') { throw 'harness_frozen_private_config_hash_invalid' }
    $compareConfigPath = (Resolve-Path -LiteralPath $HarnessCompareConfig -ErrorAction Stop).Path
    if ((Get-FileHash -LiteralPath $compareConfigPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $HarnessCompareSha256) { throw 'harness_frozen_private_config_changed' }
    $env:DECISION_HUB_HARNESS_COMPARE_CONFIG = $compareConfigPath
    $env:DECISION_HUB_HARNESS_COMPARE_SHA256 = $HarnessCompareSha256
} elseif ($env:DECISION_HUB_HARNESS_COMPARE_CONFIG -or $env:DECISION_HUB_HARNESS_COMPARE_SHA256) {
    throw 'harness_frozen_private_config_explicit_launch_required'
}
# Fail before credential writes, state changes, or process startup. Reuse is
# explicit and requires every current build input and saved artifact to match.
$validatedBuilds = $null
$harnessReceiptPath = $null
$harnessReceiptHash = $null
# Use the same native hashing primitive as the official verifier, including
# Windows PowerShell hosts without the optional Get-FileHash script module.
if (-not $DevelopmentMode) { . (Join-Path $PSScriptRoot 'product-build-receipt.ps1') }
if ([bool]$HistoryPrivateConfig -ne [bool]$HistoryPrivateConfigSha256) {
    throw 'history_private_explicit_config_and_hash_required'
}
if ($HistoryPrivateConfig) {
    if ($HistoryPrivateConfigSha256 -cnotmatch '^[a-f0-9]{64}$') {
        throw 'history_private_launch_config_hash_invalid'
    }
    $historyConfigPath = (Resolve-Path -LiteralPath $HistoryPrivateConfig -ErrorAction Stop).Path
    if ((Get-Item -LiteralPath $historyConfigPath).PSIsContainer -or
        (Get-ProductBuildSha256 -Path $historyConfigPath) -cne $HistoryPrivateConfigSha256) {
        throw 'history_private_launch_config_changed'
    }
    $env:DECISION_HUB_HISTORY_PRIVATE_CONFIG = $historyConfigPath
    $env:DECISION_HUB_HISTORY_PRIVATE_CONFIG_SHA256 = $HistoryPrivateConfigSha256
} elseif ($env:DECISION_HUB_HISTORY_PRIVATE_CONFIG -or $env:DECISION_HUB_HISTORY_PRIVATE_CONFIG_SHA256) {
    throw 'history_private_explicit_launch_required'
}
# Qualification and evaluation are explicit, content-pinned server policy.
# Validate before processes, credentials, or database changes. Never inherit
# an unrequested policy from the launching shell.
function Set-ProductPrivateHistoryPolicy44 {
    param([string]$ConfigPath, [string]$ConfigSha256, [string]$EnvironmentName)
    if ([bool]$ConfigPath -ne [bool]$ConfigSha256) {
        throw 'history_extension_explicit_config_and_hash_required'
    }
    if ($ConfigPath) {
        if (-not $HistoryPrivateConfig -or -not [System.IO.Path]::IsPathRooted($ConfigPath) -or
            $ConfigSha256 -cnotmatch '^[a-f0-9]{64}$') {
            throw 'history_extension_parent_absolute_path_and_sha256_required'
        }
        $policyPath44 = (Resolve-Path -LiteralPath $ConfigPath -ErrorAction Stop).Path
        if ((Get-Item -LiteralPath $policyPath44).PSIsContainer -or
            (Get-ProductBuildSha256 -Path $policyPath44) -cne $ConfigSha256) {
            throw 'history_extension_launch_config_changed'
        }
        [Environment]::SetEnvironmentVariable($EnvironmentName, $policyPath44, 'Process')
        [Environment]::SetEnvironmentVariable($EnvironmentName + '_SHA256', $ConfigSha256, 'Process')
    } elseif ([Environment]::GetEnvironmentVariable($EnvironmentName, 'Process') -or
              [Environment]::GetEnvironmentVariable($EnvironmentName + '_SHA256', 'Process')) {
        throw 'history_extension_explicit_launch_required'
    }
}
if ($HistoryOutcomePrivateConfig -and -not $HistoryQualificationPrivateConfig) {
    throw 'history_outcome41_explicit_qualification_required'
}
Set-ProductPrivateHistoryPolicy44 -ConfigPath $HistoryQualificationPrivateConfig `
    -ConfigSha256 $HistoryQualificationPrivateConfigSha256 `
    -EnvironmentName 'DECISION_HUB_HISTORY_QUALIFICATION_PRIVATE_CONFIG'
Set-ProductPrivateHistoryPolicy44 -ConfigPath $HistoryOutcomePrivateConfig `
    -ConfigSha256 $HistoryOutcomePrivateConfigSha256 `
    -EnvironmentName 'DECISION_HUB_HISTORY_OUTCOME_PRIVATE_CONFIG'
$buildGuard = Join-Path $PSScriptRoot 'validated-product-builds.ps1'
if (-not $DevelopmentMode) {
if ((Get-ProductBuildSha256 -Path $buildGuard) -cne
    'b7dda65ac008da102396e2436ec775e90b453c52105a015b804f07c3eca62d6a') {
    throw 'harness_install_guard_source_changed'
}
. $buildGuard
}
# The fixed pointer is only a locator for already validated installation
# bytes. It grants no recipe, owner, or deployment authority of its own.
$installedReleasePointerRelativePath = 'data/product-local/installed-release-receipt.json'
$usingInstalledReleasePointer = $false
$installedReleaseReceiptOrigin = 'standard_self_build'
function Resolve-ProductInstalledReleaseFile {
    param([string]$RelativePath, [switch]$AllowMissing)
    if ([string]::IsNullOrWhiteSpace($RelativePath) -or
        $RelativePath -match '[\\\x00-\x1f<>:"|?*]' -or
        [System.IO.Path]::IsPathRooted($RelativePath) -or
        @($RelativePath.Split('/') | Where-Object { $_ -in @('', '.', '..') -or $_ -match '[. ]$' }).Count) {
        throw 'installed_release_pointer_path_invalid'
    }
    $root = [System.IO.Path]::GetFullPath($repoRoot).TrimEnd('\', '/')
    $path = [System.IO.Path]::GetFullPath((Join-Path $root $RelativePath))
    if (-not $path.StartsWith($root + [System.IO.Path]::DirectorySeparatorChar,
            [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'installed_release_pointer_path_invalid'
    }
    $parts = $RelativePath.Split('/')
    $current = $root
    foreach ($index in 0..($parts.Count - 1)) {
        $current = Join-Path $current $parts[$index]
        try { $attributes = [System.IO.File]::GetAttributes($current) }
        catch [System.IO.FileNotFoundException] {
            if ($AllowMissing) { return $path }
            throw 'installed_release_pointer_file_missing'
        }
        catch [System.IO.DirectoryNotFoundException] {
            if ($AllowMissing) { return $path }
            throw 'installed_release_pointer_file_missing'
        }
        if ($attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw 'installed_release_pointer_linked_path'
        }
        $isDirectory = [bool]($attributes -band [System.IO.FileAttributes]::Directory)
        if (($index -lt $parts.Count - 1) -ne $isDirectory) {
            throw 'installed_release_pointer_file_invalid'
        }
    }
    return $path
}

function Assert-ProductInstalledReceiptRegistry {
    param([string]$ReceiptPath)
    $receipt = Get-ResolvedProductBuildReceipt -ReceiptPath $ReceiptPath -RepositoryRoot $repoRoot
    if ('retained_recipe_archives_v1' -cnotin @($receipt.evidence.PSObject.Properties.Name)) { return }
    $archives = $receipt.evidence.retained_recipe_archives_v1
    if ($archives -isnot [pscustomobject] -or
        @($archives.PSObject.Properties.Name).Count -ne 1 -or
        'registry' -cnotin @($archives.PSObject.Properties.Name)) {
        throw 'installed_release_pointer_registry_invalid'
    }
    $reference = $archives.registry
    if ($reference -isnot [pscustomobject] -or
        @($reference.PSObject.Properties.Name).Count -ne 2 -or
        'path' -cnotin @($reference.PSObject.Properties.Name) -or
        'sha256' -cnotin @($reference.PSObject.Properties.Name) -or
        $reference.path -isnot [string] -or $reference.sha256 -isnot [string] -or
        $reference.path -cnotmatch '^packages/kernel/research_recipe_versions/registries/([a-f0-9]{64})\.json$' -or
        $reference.sha256 -cne $Matches[1]) {
        throw 'installed_release_pointer_registry_invalid'
    }
    $registered = $receipt.sources.PSObject.Properties[$reference.path]
    if ($null -eq $registered -or $registered.Value -cne $reference.sha256) {
        throw 'installed_release_pointer_registry_unguarded'
    }
    # The official full guard hashes this registry and every installed source
    # and artifact. The existing API/Worker loader additionally checks archive
    # index/resource membership before opening the database; do not bypass it.
}

function Read-ProductInstalledReleasePointer {
    try {
        $pointerPath = Resolve-ProductInstalledReleaseFile -RelativePath $installedReleasePointerRelativePath -AllowMissing
        try { [void][System.IO.File]::GetAttributes($pointerPath) }
        catch [System.IO.FileNotFoundException] { return $null }
        catch [System.IO.DirectoryNotFoundException] { return $null }
        $pointerPath = Resolve-ProductInstalledReleaseFile -RelativePath $installedReleasePointerRelativePath
        if ((Get-Item -LiteralPath $pointerPath -Force).Length -gt 8192) { throw 'installed_release_pointer_oversized' }
        $pointerHash = Get-ProductBuildSha256 -Path $pointerPath
        $pointer = [System.Text.UTF8Encoding]::new($false, $true).GetString(
            [System.IO.File]::ReadAllBytes($pointerPath)) | ConvertFrom-Json
        $keys = @('schema_version', 'repository_root', 'receipt_path', 'receipt_sha256')
        if ($pointer -isnot [pscustomobject] -or @($pointer.PSObject.Properties.Name).Count -ne $keys.Count -or
            @(Compare-Object @($pointer.PSObject.Properties.Name) $keys -CaseSensitive).Count -or
            $pointer.schema_version -cne 'product-installed-release-pointer.v1' -or
            $pointer.repository_root -isnot [string] -or $pointer.repository_root -cne $repoRoot -or
            $pointer.receipt_path -isnot [string] -or $pointer.receipt_sha256 -isnot [string] -or
            $pointer.receipt_sha256 -cnotmatch '^[a-f0-9]{64}$') {
            throw 'installed_release_pointer_invalid'
        }
        $receiptPath = Resolve-ProductInstalledReleaseFile -RelativePath $pointer.receipt_path
        if ((Get-Item -LiteralPath $receiptPath -Force).Length -gt 2097152 -or
            (Get-ProductBuildSha256 -Path $receiptPath) -cne $pointer.receipt_sha256) {
            throw 'installed_release_pointer_receipt_changed'
        }
        $checked = Test-ProductValidatedBuilds -ReceiptPath $receiptPath -RepositoryRoot $repoRoot
        Assert-ProductInstalledReceiptRegistry -ReceiptPath $receiptPath
        [void](Resolve-ProductInstalledReleaseFile -RelativePath $pointer.receipt_path)
        [void](Resolve-ProductInstalledReleaseFile -RelativePath $installedReleasePointerRelativePath)
        if ((Get-ProductBuildSha256 -Path $receiptPath) -cne $pointer.receipt_sha256 -or
            (Get-ProductBuildSha256 -Path $pointerPath) -cne $pointerHash) {
            throw 'installed_release_pointer_changed_during_validation'
        }
        return [pscustomobject]@{ receipt_path = $receiptPath; receipt_sha256 = $pointer.receipt_sha256; validated_builds = $checked }
    } catch {
        # No fallback, scan for a newer receipt, or automatic source upgrade.
        throw 'installed_release_pointer_invalid_or_changed: complete reviewed maintenance, then run pwsh -File infra/dsh/run-product.ps1 -ValidatedBuildReceipt <validated-receipt>'
    }
}

function Save-ProductInstalledReleasePointer {
    param([string]$ReceiptPath, [string]$ReceiptSha256)
    $temporary = $null
    try {
        $root = [System.IO.Path]::GetFullPath($repoRoot).TrimEnd('\', '/')
        $full = [System.IO.Path]::GetFullPath($ReceiptPath)
        if (-not $full.StartsWith($root + [System.IO.Path]::DirectorySeparatorChar,
                [System.StringComparison]::OrdinalIgnoreCase) -or $ReceiptSha256 -cnotmatch '^[a-f0-9]{64}$') {
            throw 'installed_release_pointer_receipt_invalid'
        }
        $relative = $full.Substring($root.Length + 1).Replace('\', '/')
        $full = Resolve-ProductInstalledReleaseFile -RelativePath $relative
        if ((Get-ProductBuildSha256 -Path $full) -cne $ReceiptSha256) { throw 'installed_release_pointer_receipt_changed' }
        Assert-ProductInstalledReceiptRegistry -ReceiptPath $full
        $pointerPath = Resolve-ProductInstalledReleaseFile -RelativePath $installedReleasePointerRelativePath -AllowMissing
        [void][System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($pointerPath))
        $pointerPath = Resolve-ProductInstalledReleaseFile -RelativePath $installedReleasePointerRelativePath -AllowMissing
        $pointer = [ordered]@{ schema_version = 'product-installed-release-pointer.v1'; repository_root = $repoRoot
            receipt_path = $relative; receipt_sha256 = $ReceiptSha256 }
        $raw = [System.Text.UTF8Encoding]::new($false).GetBytes(($pointer | ConvertTo-Json -Depth 4 -Compress) + "`n")
        if ($raw.Length -gt 8192) { throw 'installed_release_pointer_oversized' }
        $temporary = $pointerPath + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
        $stream = [System.IO.File]::Open($temporary, [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        try { $stream.Write($raw, 0, $raw.Length); $stream.Flush($true) }
        finally { $stream.Dispose() }
        if ((Get-ProductBuildSha256 -Path $full) -cne $ReceiptSha256) { throw 'installed_release_pointer_receipt_changed' }
        if ([System.IO.File]::Exists($pointerPath)) {
            [System.IO.File]::Replace($temporary, $pointerPath, [System.Management.Automation.Language.NullString]::Value)
        } else {
            # Concurrent first publishers cannot silently overwrite each other.
            [System.IO.File]::Move($temporary, $pointerPath)
        }
        $temporary = $null
        if ((Get-ProductBuildSha256 -Path $full) -cne $ReceiptSha256) { throw 'installed_release_pointer_receipt_changed' }
    } catch {
        throw 'installed_release_pointer_publish_failed'
    } finally {
        if ($temporary -and [System.IO.File]::Exists($temporary)) { [System.IO.File]::Delete($temporary) }
    }
}
if ($DevelopmentMode) {
    # A development session has no installed release locator or receipt authority.
    $installedReleaseReceiptOrigin = 'development_unvalidated'
} elseif (-not [string]::IsNullOrWhiteSpace($ValidatedBuildReceipt)) {
    $installedReleaseReceiptOrigin = 'explicit_validated_receipt'
    # Maintenance reuse is strict: a supplied receipt never causes a fallback
    # build. Each Worker also revalidates these exact installation bytes.
    $harnessReceiptPath = (Resolve-Path -LiteralPath $ValidatedBuildReceipt -ErrorAction Stop).Path
    $harnessReceiptHash = (Get-ProductBuildSha256 -Path $harnessReceiptPath)
    $validatedBuilds = Test-ProductValidatedBuilds -ReceiptPath $harnessReceiptPath -RepositoryRoot $repoRoot
    if ((Get-ProductBuildSha256 -Path $harnessReceiptPath) -cne $harnessReceiptHash) {
        throw 'harness_install_receipt_changed'
    }
} else {
    $installedRelease = Read-ProductInstalledReleasePointer
    if ($null -ne $installedRelease) {
        $harnessReceiptPath = $installedRelease.receipt_path
        $harnessReceiptHash = $installedRelease.receipt_sha256
        $validatedBuilds = $installedRelease.validated_builds
        $usingInstalledReleasePointer = $true
        $installedReleaseReceiptOrigin = 'installed_release_pointer'
    }
}

if ($EnableSpotResearchV3) {
    # Startup cannot install or infer a candidate. It requires the actual
    # standard build guard and the current V3 reader to agree before writes.
    if ($null -eq $validatedBuilds) { throw 'product_start_spot_v3_validated_receipt_required' }
    [void](Assert-ProductSpotResearchV3Receipt -ReceiptPath $harnessReceiptPath `
        -ReceiptSha256 $harnessReceiptHash)
}

# Only installed files feed this receipt. It grants no candidate approval,
# native execution qualification or deployment pointer permission.
$dataDir = Join-Path $repoRoot 'data\core-live-20260913'
$defaultDshHome = Join-Path $repoRoot 'data\dsh-live'
$dshHome = if ([string]::IsNullOrWhiteSpace($DshHome)) {
    $defaultDshHome
} else {
    [System.IO.Path]::GetFullPath((Join-Path $repoRoot $DshHome))
}
$workspaceDir = Join-Path $dshHome 'Crypto Macro Trader'
$runtimeDir = Join-Path $repoRoot 'data\product-local'
if ($DevelopmentMode) {
    $dataDir = $developmentLayout.data
    $dshHome = $developmentLayout.home
    $workspaceDir = Join-Path $dshHome 'Crypto Macro Trader'
    $runtimeDir = $developmentLayout.runtime
}
$deskOrigin = if ($DevelopmentMode) { "http://localhost:$DeskPort" } else { "http://127.0.0.1:$ApiPort" }
$deskHealthUrl = "http://127.0.0.1:$DeskPort/"
$logDir = Join-Path $runtimeDir 'logs'
$uvCacheDir = Join-Path $runtimeDir 'uv-cache'
$stateFile = Join-Path $runtimeDir 'processes.json'
$supervisorFile = Join-Path $runtimeDir 'supervisor.json'
$stopRequest = Join-Path $runtimeDir 'stop.request'
. (Join-Path $PSScriptRoot 'product-processes.ps1')
$credentialFile = Join-Path $dshHome '.env'
if ($DevelopmentMode) {
    $credentialFile = if ($developmentCredentialFile) {
        if ([IO.Path]::IsPathRooted($developmentCredentialFile)) { [IO.Path]::GetFullPath($developmentCredentialFile) }
        else { [IO.Path]::GetFullPath((Join-Path $repoRoot $developmentCredentialFile)) }
    }
        else { Join-Path $repoRoot 'data/dsh-live/.env' }
}
$sourceCommit = '0a53fb55bea101816fa226bb964ae2bed71c343b'
$sourceVersion = '0.1.2-alpha.2'
$pluginBuildHash = $null
$sourceAcquisitionRoot = Join-Path $dataDir 'source-acquisition'
$capabilities = 'official.macro,market.cross_asset,market.crypto_derivatives,market.crypto_crowding,web.fetch,browser.readback,document.readback,data.historical'
if ($EnableSpotResearchV3) { $capabilities = "$capabilities,spot_market_facts" }

if (-not (Test-Path -LiteralPath $credentialFile)) {
    throw "Missing live model configuration: $credentialFile"
}
if ($DevelopmentMode -and ((Get-Item -LiteralPath $credentialFile -Force).PSIsContainer -or
    ((Get-Item -LiteralPath $credentialFile -Force).Attributes -band [IO.FileAttributes]::ReparsePoint))) {
    throw 'development_credential_file_invalid'
}

function Read-DotEnvSubset {
    param([string]$Path, [string[]]$AllowedNames)
    $allowed = [System.Collections.Generic.HashSet[string]]::new(
        $AllowedNames,
        [System.StringComparer]::Ordinal
    )
    $result = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { continue }
        $name = $Matches[1]
        if (-not $allowed.Contains($name)) { continue }
        $value = $Matches[2].Trim()
        if ($value.Length -ge 2 -and (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        )) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $result[$name] = $value
    }
    return $result
}

$searchEnvironment = Read-DotEnvSubset -Path $credentialFile -AllowedNames @(
    'DECISION_HUB_MODEL_HTTP_PROXY',
    'DECISION_HUB_PUBLIC_HTTP_PROXY',
    'DECISION_HUB_SEARCH_PROVIDER',
    'DECISION_HUB_SEARCH_CANARY_STATUS',
    'DECISION_HUB_SEARCH_API_KEY',
    'DECISION_HUB_SEARCH_BASE_URL',
    'DECISION_HUB_SEARCH_MODEL',
    'DECISION_HUB_SEARCH_ALLOW_LOCATOR_ONLY',
    'DECISION_HUB_MODEL',
    'OPENAI_API_KEY',
    'OPENAI_BASE_URL'
)

# The release model is the single authority for both research and search calls.
# Private environment files may provide credentials and routes, but they must
# not silently select an older or different model after release admission.
foreach ($modelKey in @('DECISION_HUB_MODEL', 'DECISION_HUB_SEARCH_MODEL')) {
    $searchEnvironment[$modelKey] = $ResearchModel
}

$publicSourceEnvironment = @{}
if ($searchEnvironment.ContainsKey('DECISION_HUB_PUBLIC_HTTP_PROXY')) {
    $publicSourceEnvironment['DECISION_HUB_PUBLIC_HTTP_PROXY'] = $searchEnvironment['DECISION_HUB_PUBLIC_HTTP_PROXY']
}
if ($DevelopmentMode) {
    # Acquire this instance before credentials/settings writes as well as builds.
    New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
    try {
        $ownership = [IO.File]::Open((Join-Path $runtimeDir 'supervisor.lock'),
            [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch { throw 'development_instance_running' }
    $developmentModelSettingsSource = Join-Path (Split-Path $credentialFile) 'settings.yaml'
    if (-not (Test-Path -LiteralPath $developmentModelSettingsSource)) { $developmentModelSettingsSource = '' }
    Initialize-DevelopmentHome -HomePath $dshHome -CredentialEnvironment $searchEnvironment -Model $ResearchModel `
        -ModelSettingsSource $developmentModelSettingsSource
}

# Import only static Windows proxy settings into the DSH process. No registry
# mutation, PAC execution, global dispatcher or other service environment change.
$modelTransportEnvironment = @{
    DECISION_HUB_MODEL_TRANSPORT_ENABLE = '1'
    DECISION_HUB_MODEL_TRANSPORT_UPSTREAM_ROOT = (Join-Path $repoRoot '.cache\dsh-upstream\source')
    DECISION_HUB_MODEL_OS_PROXY_READ = 'unavailable'
}
try {
    $modelOsProxy = Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction Stop
    $modelTransportEnvironment['DECISION_HUB_MODEL_OS_PROXY_READ'] = 'static_registry'
    $modelTransportEnvironment['DECISION_HUB_MODEL_OS_PROXY_ENABLE'] = if ($modelOsProxy.ProxyEnable -eq 1) { '1' } else { '0' }
    $modelTransportEnvironment['DECISION_HUB_MODEL_OS_PROXY_SERVER'] = [string]$modelOsProxy.ProxyServer
    $modelTransportEnvironment['DECISION_HUB_MODEL_OS_PROXY_BYPASS'] = [string]$modelOsProxy.ProxyOverride
} catch { # Explicitly record unavailable settings; no automatic network probe.
}

# The upstream ``llm-pi-ai`` provider uses the DSH credentials service when a
# provider declares ``apiKeyEnv``.  It does not fall back to the child process
# environment in that mode.  Bootstrap the local, gitignored credential store
# from the selected .env so a clean launcher can authenticate without asking
# the user to edit DSH internals.  The value is never logged or printed.
$modelApiKey = [string]$searchEnvironment['OPENAI_API_KEY']
if (-not [string]::IsNullOrWhiteSpace($modelApiKey)) {
    $dshCredentialStore = Join-Path $dshHome '.credentials.yaml'
    if (Test-Path -LiteralPath $dshCredentialStore) {
        $credentialYaml = Get-Content -LiteralPath $dshCredentialStore -Raw
        if ($credentialYaml -match '(?ms)^  llm-pi-ai/codexai:\r?\n\s+kind:\s+api-key\r?\n\s+key:') {
            # Match only this provider record. Do not replace other stored
            # API keys; a delegate also preserves literal '$' in a key.
            $credentialYaml = [regex]::Replace($credentialYaml,
                '(?m)(^  llm-pi-ai/codexai:\r?\n    kind: api-key\r?\n    key: )[^\r\n]*',
                [System.Text.RegularExpressions.MatchEvaluator]{ param($match) $match.Groups[1].Value + $modelApiKey })
        } else {
            $credentialYaml = $credentialYaml.TrimEnd() + "`r`n  llm-pi-ai/codexai:`r`n    kind: api-key`r`n    key: $modelApiKey`r`n"
        }
    } else {
        $credentialYaml = "version: 1`r`nrecords:`r`n  llm-pi-ai/codexai:`r`n    kind: api-key`r`n    key: $modelApiKey`r`n"
    }
    Set-Content -LiteralPath $dshCredentialStore -Value $credentialYaml -Encoding utf8
    if ($DevelopmentMode) { Protect-DevelopmentPrivateFile -Path $dshCredentialStore }
}
if (-not $searchEnvironment.ContainsKey('DECISION_HUB_SEARCH_PROVIDER')) {
    $searchEnvironment['DECISION_HUB_SEARCH_PROVIDER'] = 'openai_responses'
}
if ($searchEnvironment['DECISION_HUB_SEARCH_CANARY_STATUS'] -eq 'passed') {
    # Locator compatibility does not bypass the live canary gate. Every
    # search result still requires fetch before becoming factual Evidence.
    $capabilities = "$capabilities,web.search"
}

New-Item -ItemType Directory -Force -Path $workspaceDir, $logDir, $uvCacheDir | Out-Null

# The upstream DSH process resolves role presets from DSH_HOME.  Keep the
# product preset in the selected recovery/home directory so a clean launch
# cannot hang while submitting the decision-research session.
$presetSource = Join-Path $repoRoot 'infra\dsh\presets\decision-research'
$presetTarget = Join-Path $dshHome '.agent-presets\decision-research'
New-Item -ItemType Directory -Force -Path $presetTarget | Out-Null
foreach ($presetFile in @('agent.cordis.yml', 'preset.yml')) {
    $sourceFile = Join-Path $presetSource $presetFile
    if (-not (Test-Path -LiteralPath $sourceFile)) {
        throw "Missing DSH decision-research preset: $sourceFile"
    }
    Copy-Item -LiteralPath $sourceFile -Destination (Join-Path $presetTarget $presetFile) -Force
}

try {
    $existingListeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop)
} catch {
    throw 'Product listener inventory is unavailable; startup cannot prove port ownership.'
}
$listenerPorts = @($DshPort, $ApiPort, $McpPort, $AcquisitionPort)
if ($DevelopmentMode) { $listenerPorts += $DeskPort }
foreach ($port in $listenerPorts) {
    if (@($existingListeners | Where-Object LocalPort -eq $port).Count -ne 0) {
        throw "Port $port is already in use. Run infra/dsh/stop-product.ps1 or stop the existing product process first."
    }
}

# Exclusive launcher ownership also covers startup before ports are listening.
if (-not $DevelopmentMode) {
    $ownership = [System.IO.File]::Open((Join-Path $runtimeDir 'supervisor.lock'),
        [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
}
$launchId = [datetime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
$logDir = Join-Path $logDir $launchId
$modelTransportEnvironment['DECISION_HUB_MODEL_TRANSPORT_DIAGNOSTICS'] = (Join-Path $logDir 'model-transport.channels.jsonl')
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
Remove-Item -LiteralPath $stopRequest -Force -ErrorAction SilentlyContinue
$supervisorEntry = [pscustomobject]@{
    pid = $PID; started_ticks = (Get-ProductProcessStamp -ProcessId $PID)
    launch_id = $launchId; state = 'starting'; log_dir = $logDir
    research_model = $ResearchModel
    decision_hub_model = [string]$searchEnvironment['DECISION_HUB_MODEL']
    search_model = [string]$searchEnvironment['DECISION_HUB_SEARCH_MODEL']
}
if ($DevelopmentMode) {
    $supervisorEntry | Add-Member -NotePropertyName development_root -NotePropertyValue $developmentLayout.root
}
Write-ProductJson -Path $supervisorFile -Value $supervisorEntry
if ($watchdogHandoff) {
    Complete-DevelopmentWatchdogLaunch -Layout $developmentLayout -Handoff $watchdogHandoff -Supervisor $supervisorEntry
    $watchdogHandoff = $null
}

$pluginDir = Join-Path $repoRoot 'extensions\dsh\decision-hub'
if ($DevelopmentMode) {
    $node = (Get-Command node -ErrorAction Stop).Source
    $pnpm = (Get-Command pnpm -ErrorAction Stop).Source
    # Recheck immediately before shared source identity or lib can be rewritten.
    # The current instance owns this supervisor now; only formal liveness matters.
    Assert-DevelopmentNoSharedRuntime -RepositoryRoot $repoRoot `
        -Layout $developmentLayout -OwnsInstanceLock
    Initialize-DevelopmentPlugin -RepositoryRoot $repoRoot -RuntimeDirectory $runtimeDir `
        -Node $node -Pnpm $pnpm -BuildPlugin:$BuildPlugin
    Initialize-DevelopmentProfile -RepositoryRoot $repoRoot -HomePath $dshHome -Node $node
} elseif ($null -eq $validatedBuilds) {
    $buildSourceBefore = Get-ProductBuildSourceMap
    Write-Host 'Building the current Decision Desk bundle...'
    & pnpm --dir (Join-Path $repoRoot 'apps\decision-desk') build
    if ($LASTEXITCODE -ne 0) {
        throw 'Decision Desk build failed.'
    }
    Write-Host 'Building the current DSH plugin bundle...'
    & pnpm --dir $pluginDir build
    if ($LASTEXITCODE -ne 0) {
        throw 'Decision Hub DSH plugin build failed.'
    }
    $validatedBuilds = New-ProductBuildReceipt -SourceBefore $buildSourceBefore `
        -OutputPath (Join-Path $logDir 'product-validated-builds.json')
    $harnessReceiptPath = $validatedBuilds.receipt_path
    $harnessReceiptHash = (Get-ProductBuildSha256 -Path $harnessReceiptPath)
} else {
    # Recheck immediately before launching, after the normal startup preflight.
    # A changed file must fail rather than silently trigger another build.
    try {
        $validatedBuilds = Test-ProductValidatedBuilds -ReceiptPath $harnessReceiptPath -RepositoryRoot $repoRoot
        if ((Get-ProductBuildSha256 -Path $harnessReceiptPath) -cne $harnessReceiptHash) {
            throw 'harness_install_receipt_changed'
        }
    } catch {
        if ($usingInstalledReleasePointer) {
            throw 'installed_release_pointer_invalid_or_changed: complete reviewed maintenance, then run pwsh -File infra/dsh/run-product.ps1 -ValidatedBuildReceipt <validated-receipt>'
        }
        throw
    }
    Write-Host "Reusing validated Decision Desk and DSH plugin builds ($($validatedBuilds.source_count) sources, $($validatedBuilds.artifact_count) artifacts)."
}
# Both ordinary build and strict maintenance reuse converge on one real receipt.
if ($DevelopmentMode) {
    $harnessReceiptPath = ''; $harnessReceiptHash = ''
    $harnessWorkerArguments = @()
    Write-Host 'DEVELOPMENT_SESSION=unvalidated; no release receipt or execution qualification'
} else {
    Save-ProductInstalledReleasePointer -ReceiptPath $harnessReceiptPath -ReceiptSha256 $harnessReceiptHash
    Write-Host "VALIDATED_BUILD_RECEIPT_SOURCE=$installedReleaseReceiptOrigin"
    $harnessWorkerArguments = @('--harness-release-baseline', $harnessReceiptPath,
        '--harness-install-sha256', $harnessReceiptHash)
}
$identityFile = Join-Path $pluginDir 'src\generated-build-identity.ts'
$identityText = Get-Content -LiteralPath $identityFile -Raw
$identityMatch = [regex]::Match($identityText, 'PLUGIN_BUILD_HASH\s*=\s*"([a-f0-9]{64})"')
if (-not $identityMatch.Success) {
    throw "Could not read the generated plugin build hash from $identityFile"
}
$pluginBuildHash = $identityMatch.Groups[1].Value

$uv = (Get-Command uv -ErrorAction Stop).Source
$node = (Get-Command node -ErrorAction Stop).Source
$started = [System.Collections.Generic.List[object]]::new()
$launchSpecs = @{} # Contains secrets; never serialize this in process state or logs.
$initialFailures = [System.Collections.Generic.List[string]]::new()
$checkpointState = [pscustomobject]@{
    degraded = $false; failure_count = 0; next_retry_at = $null
    last_error = $null; last_failure_at = $null
}
$script:productPendingBirths = [System.Collections.Generic.List[object]]::new()
$script:productBirthProcesses = [System.Collections.Generic.Dictionary[object, object]]::new()
$script:productPendingBirthRetryAt = $null
$managedHealthCheck = if ($DevelopmentMode) {
    { param($entry) Test-DevelopmentServiceHealth -Entry $entry }
} else { $null }

function Start-ProductProcess {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$ArgumentList,
        [hashtable]$Environment,
        [string]$Marker,
        $HealthProbe = $null,
        $ExistingEntry = $null
    )
    if ($checkpointState.degraded -or ($script:productPendingBirths -and $script:productPendingBirths.Count)) {
        throw 'product_process_registration_paused'
    }
    if ($null -eq $script:productPendingBirths) {
        $script:productPendingBirths = [System.Collections.Generic.List[object]]::new()
    }
    if ($null -eq $script:productBirthProcesses) {
        $script:productBirthProcesses = [System.Collections.Generic.Dictionary[object, object]]::new()
    }
    $generation = if ($ExistingEntry) { $ExistingEntry.restart_count } else { 0 }
    $stdout = Join-Path $logDir "$Name.$generation.stdout.log"
    $stderr = Join-Path $logDir "$Name.$generation.stderr.log"
    $launchSpecs[$Name] = @{
        Name=$Name; FilePath=$FilePath; ArgumentList=$ArgumentList
        Environment=$Environment; Marker=$Marker; HealthProbe=$HealthProbe
    }
    $entry = if ($ExistingEntry) {
        # Keep the prior generation intact until the new one is registered.
        $properties = @{}
        foreach ($property in $ExistingEntry.PSObject.Properties) { $properties[$property.Name] = $property.Value }
        [pscustomobject]$properties
    } else { [pscustomobject]@{
        name = $Name; pid = $null; marker = $Marker
        stdout = $stdout; stderr = $stderr; started_ticks = $null; children = @()
        restart_count = 0; consecutive_restarts = 0; state = 'registration_identity_pending'
        stable_since = [datetime]::UtcNow.ToString('o')
        last_error = $null; last_failure_at = $null; next_retry_at = $null
        health_probe = $HealthProbe; health_failure_count = 0; health_status = 'starting'
        last_health_failure_at = $null; cleanup_pending = $false
    } }
    $entry.pid = $null; $entry.started_ticks = $null; $entry.children = @()
    if ($DevelopmentMode) {
        $entry | Add-Member -NotePropertyName development_root -NotePropertyValue $developmentLayout.root -Force
        $entry | Add-Member -NotePropertyName development_owner_ticks -NotePropertyValue ([string]$supervisorEntry.started_ticks) -Force
    }
    $entry | Add-Member -NotePropertyName health_probe -NotePropertyValue $HealthProbe -Force
    $entry | Add-Member -NotePropertyName health_failure_count -NotePropertyValue 0 -Force
    $entry | Add-Member -NotePropertyName health_status -NotePropertyValue 'starting' -Force
    $entry | Add-Member -NotePropertyName cleanup_pending -NotePropertyValue $false -Force
    $entry.state = 'registration_identity_pending'
    # Windows PowerShell lacks Start-Process -Environment. Temporarily apply
    # this child environment, use native stdout/stderr redirection, then
    # restore the launcher process environment immediately.
    $previousEnvironment = @{}
    try {
        foreach ($key in $Environment.Keys) {
            $previousEnvironment[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
            [Environment]::SetEnvironmentVariable($key, [string]$Environment[$key], 'Process')
        }
        $process = Start-Process -FilePath $FilePath -WorkingDirectory $repoRoot `
            -ArgumentList $ArgumentList -RedirectStandardOutput $stdout `
            -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
        if ($null -ne $process) {
            # Register before environment restoration or reading Id/StartTime.
            # The actual returned Process stays private; never serialize it.
            $script:productPendingBirths.Add($entry)
            $script:productBirthProcesses[$entry] = $process
        }
    } finally {
        foreach ($key in $Environment.Keys) {
            if ($null -eq $previousEnvironment[$key]) {
                # PowerShell/.NET string binding can turn null into an empty
                # value. Remove the variable explicitly when it did not exist.
                Remove-Item -LiteralPath "Env:$key" -ErrorAction SilentlyContinue
            } else {
                [Environment]::SetEnvironmentVariable($key, $previousEnvironment[$key], 'Process')
            }
        }
    }
    if ($null -eq $process) {
        throw "Could not start product process: $Name"
    }
    $entry.pid = $process.Id
    $entry.started_ticks = $process.StartTime.ToUniversalTime().Ticks.ToString()
    $entry.stdout = $stdout
    $entry.stderr = $stderr
    $entry.children = @()
    $entry.children = @(Get-ProductProcessDescendants $entry)
    if (-not (Test-ProductProcessIdentity $entry) -and $entry.children.Count -eq 0) {
        throw 'product_newborn_lifetime_unverified'
    }
    $entry.state = 'running'
    $snapshot = @($started.ToArray() | Where-Object name -ne $Name) + @($entry)
    try {
        Write-ProductJson -Path $stateFile -Value $snapshot
    } catch {
        if (-not (Test-ProductStateStorageError $_)) { throw }
        Set-ProductCheckpointFailure -State $checkpointState -Path $stateFile -ErrorRecord $_
        $entry.state = 'registration_pending'
        $remaining = @(Stop-ProductProcessTree -Entry $entry)
        if ($remaining.Count -eq 0) {
            $script:productPendingBirths.Remove($entry) | Out-Null
            $script:productBirthProcesses.Remove($entry) | Out-Null
        }
        # Unconfirmed newborn identities remain in memory and prevent another
        # launch. The supervisor retries their cleanup and durable registration.
        throw
    }
    if ($ExistingEntry) {
        foreach ($property in $entry.PSObject.Properties) {
            $ExistingEntry | Add-Member -NotePropertyName $property.Name `
                -NotePropertyValue $property.Value -Force
        }
    } else { $started.Add($entry) }
    $script:productPendingBirths.Remove($entry) | Out-Null
    $script:productBirthProcesses.Remove($entry) | Out-Null
    return $process
}

function Wait-HttpReady {
    param([string]$Url, [hashtable]$Headers = @{}, [int]$Seconds = 30, [switch]$Optional)
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        try {
            if ($DevelopmentMode) {
                $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -Headers $Headers -TimeoutSec 2 -NoProxy
            } else {
                $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -Headers $Headers -TimeoutSec 2
            }
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                return $response
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    } while ((Get-Date) -lt $deadline)
    if ($Optional) {
        $initialFailures.Add($Url)
        Write-Warning "Service is not ready yet: $Url; report API remains available."
        return $null
    }
    throw "Service did not become ready: $Url"
}

function Wait-TcpReady {
    param([int]$Port, [int]$Seconds = 30, [switch]$Optional)
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        $client = [System.Net.Sockets.TcpClient]::new()
        try {
            $task = $client.ConnectAsync('127.0.0.1', $Port)
            if ($task.Wait(750) -and $client.Connected) {
                return
            }
        } catch {
        } finally {
            $client.Dispose()
        }
        Start-Sleep -Milliseconds 400
    } while ((Get-Date) -lt $deadline)
    if ($Optional) {
        $initialFailures.Add("tcp:$Port")
        Write-Warning "Service port $Port is unavailable; report API remains available."
        return
    }
    throw "Service did not open TCP port $Port"
}

function Wait-ResearchWorkerReady {
    # Default contract: service_id -eq 'hub-research-worker'. The same helper
    # is also used for the realtime heartbeat below.
    param(
        [System.Diagnostics.Process]$Process,
        [string]$Url,
        [int]$Seconds = 20,
        [string]$ServiceId = 'hub-research-worker'
    )
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        if ($Process.HasExited) {
            $initialFailures.Add($ServiceId)
            Write-Warning "Worker exited during startup: $ServiceId; isolated recovery will retry."
            return
        }
        try {
            if ($DevelopmentMode) { $overview = Invoke-RestMethod -Uri $Url -TimeoutSec 2 -NoProxy }
            else { $overview = Invoke-RestMethod -Uri $Url -TimeoutSec 2 }
            $worker = $overview.services | Where-Object service_id -eq $ServiceId | Select-Object -First 1
            if ($worker -and $worker.status -eq 'online') {
                return
            }
        } catch {
            # The API may still be writing the first durable heartbeat.
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    $initialFailures.Add($ServiceId)
    Write-Warning "Worker has not published an online heartbeat: $ServiceId; report API remains available."
}

try {
    # The custom route must declare the selected model before the first DSH
    # session. This is local registration only; it does not pass a canary.
    $modelSettingsArguments = @('--settings', (Join-Path $dshHome 'settings.yaml'), '--model', $ResearchModel)
    # Development reads the selected .env; only its own settings may change.
    if (-not $DevelopmentMode) { $modelSettingsArguments += @('--env', $credentialFile) }
    & $node (Join-Path $repoRoot 'infra\dsh\research-model-settings.mjs') @modelSettingsArguments --apply
    if ($LASTEXITCODE -ne 0) { throw 'research_model_selection_failed' }
    $developmentEnvironmentMode = if ($DevelopmentMode) { '1' } else { '0' }
    $common = @{
        DECISION_HUB_DEVELOPMENT_MODE = $developmentEnvironmentMode
        # Internal receipt identity, pinned after the full normal build guard.
        # Every Python child receives the same launch value, including realtime.
        DECISION_HUB_RETAINED_RECIPE_RECEIPT = $harnessReceiptPath
        DECISION_HUB_RETAINED_RECIPE_RECEIPT_SHA256 = $harnessReceiptHash
        DECISION_HUB_DSH_HOME = $dshHome
        DECISION_HUB_DATA_DIR = $dataDir
        UV_CACHE_DIR = $uvCacheDir
        # Empty inherited values are invalid Pydantic booleans. Set the live
        # product policy for every Python child instead of relying on the
        # caller's shell environment.
        DECISION_HUB_SOURCES_ENABLED = '1'
        DECISION_HUB_MARKET_ENABLED = '1'
        # Controlled product policy: retain history when monitoring is disabled.
        DECISION_HUB_RISK_ENABLED = $(if ($DisableRiskMonitoring) { '0' } else { '1' })
        # Ingest the official BLS calendar in the realtime worker. This is a
        # scheduled-event feed, not a forecast feed; consensus/actual values
        # remain separate inputs and are never inferred from the calendar.
        DECISION_HUB_CALENDAR_DISCOVERY_ENABLED = '1'
        DECISION_HUB_EVENT_WINDOW_LIVE_ENABLED = '1'
        # Document correction uses deterministic rules until an audited model
        # configuration is selected; expose this actual mode in Operations.
        DECISION_HUB_DOCUMENT_CORRECTION_LLM_ENABLED = '0'
        # The API and Operations view must describe the same live research
        # runtime as the worker; otherwise the UI reports the unused native
        # analyzer fallback as ``fake`` while DSH is serving real Runs.
        DECISION_HUB_OPERATIONS_RESEARCH_RUNTIME = 'dsh-web'
        DECISION_HUB_OPERATIONS_MODEL = $ResearchModel
        DECISION_HUB_LLM_API_MODE = 'responses'
        DECISION_HUB_SOURCE_ACQUISITION_ROOT = $sourceAcquisitionRoot
        DECISION_HUB_SOURCE_ACQUISITION_URL = "http://127.0.0.1:$AcquisitionPort"
        DECISION_HUB_SOURCE_ACQUISITION_KEY = 'decision-hub-local-source-key'
        # Research claim polling is one second, but its durable heartbeat is
        # intentionally slower so long DSH work remains visibly online.
        DECISION_HUB_HEARTBEAT_INTERVAL = '10'
        DECISION_HUB_DSH_SOURCE_COMMIT = $sourceCommit
        DECISION_HUB_DSH_SOURCE_VERSION = $sourceVersion
        DECISION_HUB_DSH_PLUGIN_BUILD_HASH = $pluginBuildHash
        # Every Python role receives the same explicit opt-in and route. An
        # inherited feature flag cannot silently enable V3 on a normal launch.
        DECISION_HUB_SPOT_RESEARCH_ENABLED = $(if ($EnableSpotResearchV3) { '1' } else { '0' })
        DECISION_HUB_ACTION_PRODUCT_ENABLED = $(if ($EnableActionProduct) { '1' } else { '0' })
        DECISION_HUB_ACTION_REVIEW_ENABLED = $(if ($EnableActionReview) { '1' } else { '0' })
    DECISION_HUB_ACTION_EXECUTION_ENABLED = $(if ($EnableActionExecution) { '1' } else { '0' })
    DECISION_HUB_RESEARCH_LEARNING_ENABLED = $(if ($EnableResearchLearning) { '1' } else { '0' })
    DECISION_HUB_DURABLE_SKILL_CALLBACKS_ENABLED = $(if ($EnableDurableSkillCallbacks) { '1' } else { '0' })
    DECISION_HUB_RESEARCH_SKILLS_ENABLED = $(if ($EnableResearchLearning -or $EnableDurableSkillCallbacks -or $env:DECISION_HUB_RESEARCH_SKILLS_ENABLED -eq '1') { '1' } else { '0' })
        DECISION_HUB_RESEARCH_CAPABILITIES = $capabilities
        DECISION_HUB_DSH_WEB_URL = "http://127.0.0.1:$DshPort"
    }
    if ($DevelopmentMode) {
        $common['DECISION_HUB_RESEARCH_SKILLS_ENABLED'] = '0'
        $common['DECISION_HUB_DEVELOPMENT_ROOT'] = $developmentLayout.root
        $common['DECISION_HUB_CHECKPOINT_PATH'] = Join-Path $dataDir 'checkpoints/decision_graph.sqlite3'
        $common['DECISION_HUB_DOCUMENT_BUNDLE_ROOT'] = Join-Path $dataDir 'document-bundles'
        $common['DECISION_HUB_PUBLICATION_DATA_DIR'] = $developmentLayout.publication
        . (Join-Path $PSScriptRoot 'development-email.ps1')
        $developmentEmailEnvironment = Get-DevelopmentEmailEnvironment -RepositoryRoot $repoRoot -DevelopmentRoot $DevelopmentRoot
        foreach ($key in $developmentEmailEnvironment.Keys) { $common[$key] = $developmentEmailEnvironment[$key] }
        $developmentEmailEnvironment.Clear()
    }
    . (Join-Path $PSScriptRoot 'decision-settings-session.ps1')
    if ($DevelopmentMode) {
        . (Join-Path $PSScriptRoot 'development-settings-session.ps1')
        $settingsSession = New-DevelopmentSettingsSession -RepositoryRoot $repoRoot -Layout $developmentLayout `
            -Origin $deskOrigin -OwnerId 'owner'
    } else {
        $settingsSession = New-ProductSettingsSession -RuntimeDirectory $runtimeDir `
            -Origin $deskOrigin -OwnerId 'owner'
    }
    $hubApiProcess = Start-ProductProcess -Name 'hub-api' -FilePath $uv `
        -ArgumentList @('run', 'python', '-m', 'apps.hub_api.main') `
        -Environment ($common + @{
            HUB_API_HOST = '127.0.0.1'
            DECISION_HUB_SETTINGS_PRIVATE_CONFIG = $settingsSession.ConfigPath
            DECISION_HUB_SETTINGS_PRIVATE_SHA256 = $settingsSession.ConfigSha256
            HUB_API_PORT = [string]$ApiPort
            HUB_API_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS = [string]$ApiGracefulShutdownTimeoutSeconds
            DECISION_HUB_PROVIDER_CALLBACK_DIAGNOSTICS_PATH = (Join-Path $logDir 'provider-input.phases.jsonl')
            DECISION_HUB_API_URL = "http://127.0.0.1:$ApiPort"
            DECISION_HUB_DSH_WORKSPACE = $workspaceDir
            DECISION_HUB_DSH_HOST_SECRET = 'decision-hub-local-host-key'
            DECISION_HUB_DSH_CALLBACK_SECRET = 'core-live-callback'
        }) -Marker 'apps.hub_api.main' -HealthProbe ([pscustomobject]@{
            port = $ApiPort
            url = "http://127.0.0.1:$ApiPort/health/live"
            startup_grace_seconds = 30
        })
    Write-Host "Waiting for Hub API readiness (bounded ${ApiReadyTimeoutSeconds}s startup window)."
    Wait-HttpReady -Url "http://127.0.0.1:$ApiPort/health/ready" -Seconds $ApiReadyTimeoutSeconds | Out-Null
    if ($DevelopmentMode) {
        $deskProcess = Start-ProductProcess -Name 'decision-desk-dev' -FilePath $pnpm `
            -ArgumentList @('--dir', (Join-Path $repoRoot 'apps/decision-desk'), 'exec', 'vite',
                '--host', '127.0.0.1', '--port', [string]$DeskPort, '--strictPort') `
            -Environment @{ VITE_API_PROXY_TARGET="http://127.0.0.1:$ApiPort" } `
            -Marker 'vite' -HealthProbe ([pscustomobject]@{
                port=$DeskPort; url=$deskHealthUrl; startup_grace_seconds=30
            })
        Wait-HttpReady -Url $deskHealthUrl | Out-Null
    }

    # Documents use the same durable database queue as the API. Keep parsing,
    # correction and claim extraction alive after the upload request returns;
    # the API background task is only a low-latency kick, not the durable owner.
    $documentsWorkerProcess = Start-ProductProcess -Name 'documents-worker' -FilePath $uv `
        -ArgumentList @('run', 'python', '-m', 'apps.hub_worker.main', '--role', 'documents', '--interval', '1') `
        -Environment ($common + @{
            DECISION_HUB_INSTANCE_ID = 'local-product-documents'
        }) -Marker 'apps.hub_worker.main --role documents'
    Wait-ResearchWorkerReady -Process $documentsWorkerProcess `
        -Url "http://127.0.0.1:$ApiPort/v1/operations" `
        -ServiceId 'hub-documents'

    $acquisitionWorkerDir = Join-Path $repoRoot 'apps\source_acquisition_worker'
    $pnpm = (Get-Command pnpm -ErrorAction Stop).Source
    $acquisitionProcess = Start-ProductProcess -Name 'source-acquisition-worker' -FilePath $pnpm `
        -ArgumentList @('--dir', $acquisitionWorkerDir, 'server') `
        -Environment @{
            SOURCE_ACQUISITION_PORT = [string]$AcquisitionPort
            SOURCE_ACQUISITION_ROOT = $sourceAcquisitionRoot
            SOURCE_HUB_URL = "http://127.0.0.1:$ApiPort"
            SOURCE_ACQUISITION_KEY = 'decision-hub-local-source-key'
            SOURCE_ALLOWED_DOMAINS = 'federalreserve.gov,ecb.europa.eu,boj.or.jp,fred.stlouisfed.org'
        } -Marker 'source_acquisition_worker' -HealthProbe ([pscustomobject]@{
            port = $AcquisitionPort
            url = "http://127.0.0.1:$AcquisitionPort/health/ready"
            startup_grace_seconds = 20
        })
    Wait-HttpReady -Url "http://127.0.0.1:$AcquisitionPort/health/ready" -Optional | Out-Null

    $researchMcpProcess = Start-ProductProcess -Name 'research-mcp' -FilePath $uv `
        -ArgumentList @('run', 'python', '-m', 'apps.research_mcp.main') `
        -Environment ($common + $searchEnvironment + @{
            DECISION_HUB_RESEARCH_MCP_PORT = [string]$McpPort
            DECISION_HUB_DSH_RESEARCH_TOOL_KEY = 'decision-hub-local-research-tool-key'
            DECISION_HUB_RESEARCH_DURABLE_PROGRESS = '1'
        }) -Marker 'apps.research_mcp.main' -HealthProbe ([pscustomobject]@{
            port = $McpPort
            url = $null
            startup_grace_seconds = 20
        })
    Wait-TcpReady -Port $McpPort -Optional

    $dshArguments = @(
            '--import', ([System.Uri](Join-Path $repoRoot 'infra\dsh\provider-transport.mjs')).AbsoluteUri,
            (Join-Path $repoRoot '.cache\dsh-upstream\source\apps\cli\lib\bin.js'),
            '--profile', 'web')
    # Official plugin installation already registers its bundle for development.
    if (-not $DevelopmentMode) {
        $dshArguments += @('--patch', (Join-Path $repoRoot 'extensions\dsh\decision-hub\cordis.patch.yml'))
    }
    $dshArguments += @(
            '--host', '127.0.0.1',
            '--port', [string]$DshPort,
            '--no-open'
        )
    $dshProcess = Start-ProductProcess -Name 'dsh-web' -FilePath $node `
        -ArgumentList $dshArguments -Environment ($common + $searchEnvironment + $modelTransportEnvironment + @{
            DSH_HOME = $dshHome
            DECISION_HUB_API_URL = "http://127.0.0.1:$ApiPort"
            DECISION_HUB_DESK_URL = $deskOrigin
            DECISION_HUB_DSH_PROVIDER = 'codexai'
            DECISION_HUB_DSH_MODEL = $ResearchModel
            DECISION_HUB_DSH_HOST_KEY = 'decision-hub-local-host-key'
            DECISION_HUB_DSH_CALLBACK_KEY = 'core-live-callback'
            DECISION_HUB_DSH_RESEARCH_TOOL_KEY = 'decision-hub-local-research-tool-key'
            DECISION_HUB_RESEARCH_TOOL_URL = "http://127.0.0.1:$McpPort/decision-hub/v1/research-capabilities/execute"
            DECISION_HUB_OWNER_ID = 'dsh-web-owner'
            DECISION_HUB_DSH_WORKSPACE_CWD = $workspaceDir
            DECISION_HUB_SKILL_DIR = (Join-Path $repoRoot 'packs\crypto_macro\skills')
            DECISION_HUB_DSH_RUNTIME_MODE = 'live'
            DECISION_HUB_DSH_CALLBACK_TIMEOUT_MS = '5000'
            DECISION_HUB_DSH_CALLBACK_ATTEMPTS = '3'
        }) -Marker '.cache\dsh-upstream\source\apps\cli\lib\bin.js' `
        -HealthProbe ([pscustomobject]@{
            port = $DshPort
            url = $null
            startup_grace_seconds = 30
        })

    $dshLog = ($started | Where-Object name -eq 'dsh-web').stdout
    $deadline = (Get-Date).AddSeconds(60)
    $dshUrl = $null
    do {
        if ($dshProcess.HasExited) {
            $initialFailures.Add('dsh-web')
            Write-Warning "DSH Web exited during startup. See $dshLog; report API remains available."
            break
        }
        if (Test-Path -LiteralPath $dshLog) {
            $line = Get-Content -LiteralPath $dshLog | Where-Object { $_ -like 'dsh web: http://127.0.0.1:*' } | Select-Object -Last 1
            if ($line) {
                $dshUrl = $line.Substring('dsh web: '.Length)
                break
            }
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    if (-not $dshUrl) {
        $initialFailures.Add('dsh-web-url')
        Write-Warning 'DSH Web has not published its authenticated URL; isolated recovery remains enabled.'
    }
    if ($DevelopmentMode) {
        . (Join-Path $PSScriptRoot 'development-workspace.ps1')
        Register-DevelopmentWorkspace -RepositoryRoot $repoRoot -Layout $developmentLayout `
            -DshPort $DshPort -AuthenticatedUrl $dshUrl
        Write-Host 'DEVELOPMENT_WORKSPACE_REGISTERED=true'
    }

    Wait-HttpReady -Url "http://127.0.0.1:$DshPort/decision-hub/v1/readiness" `
        -Headers @{ 'X-Decision-Hub-Host-Key' = 'decision-hub-local-host-key' } -Optional | Out-Null

    # Realtime owns official source/calendar polling, due outcomes and the
    # local outbox. Keep it in the same managed process set so M3/M2 are live
    # after a clean restart. Evolution has its own managed queue below.
    $realtimeWorkerProcess = Start-ProductProcess -Name 'realtime-worker' -FilePath $uv `
        -ArgumentList @('run', 'python', '-m', 'apps.hub_worker.main', '--role', 'realtime', '--interval', '10') `
        -Environment ($common + $publicSourceEnvironment + @{
            DECISION_HUB_INSTANCE_ID = 'local-product-realtime'
            DECISION_HUB_REALTIME_DIAGNOSTICS_PATH = (Join-Path $logDir 'realtime.channels.jsonl')
        }) -Marker 'apps.hub_worker.main --role realtime'

    Wait-ResearchWorkerReady -Process $realtimeWorkerProcess `
        -Url "http://127.0.0.1:$ApiPort/v1/operations" `
        -ServiceId 'hub-realtime-worker'

    $researchWorkerProcess = Start-ProductProcess -Name 'research-worker' -FilePath $uv `
        -ArgumentList (@('run', 'python', '-m', 'apps.hub_worker.main', '--role', 'research', '--interval', '1') + $harnessWorkerArguments) `
        -Environment ($common + @{
            DECISION_HUB_RESEARCH_RUNTIME = 'dsh-web'
            DECISION_HUB_RESEARCH_EXECUTION_MODE = 'live'
            DECISION_HUB_DSH_HOST_SECRET = 'decision-hub-local-host-key'
            DECISION_HUB_API_URL = "http://127.0.0.1:$ApiPort"
            DECISION_HUB_DSH_WORKSPACE = $workspaceDir
            DECISION_HUB_DSH_CALLBACK_SECRET = 'core-live-callback'
            DECISION_HUB_INSTANCE_ID = 'local-product-research'
        }) -Marker 'apps.hub_worker.main --role research'

    # Keep a second claim lane for manual/high-priority Runs. The normal
    # worker may be inside a long scheduled recheck; this lane can still claim
    # the user's request without duplicating the DSH Harness or business
    # ledger. It only admits priority=100 Runs and shares the same durable DB.
    $priorityResearchWorkerProcess = Start-ProductProcess -Name 'research-priority-worker' -FilePath $uv `
        -ArgumentList (@('run', 'python', '-m', 'apps.hub_worker.main', '--role', 'research', '--interval', '1') + $harnessWorkerArguments) `
        -Environment ($common + @{
            DECISION_HUB_RESEARCH_RUNTIME = 'dsh-web'
            DECISION_HUB_RESEARCH_EXECUTION_MODE = 'live'
            DECISION_HUB_DSH_HOST_SECRET = 'decision-hub-local-host-key'
            DECISION_HUB_API_URL = "http://127.0.0.1:$ApiPort"
            DECISION_HUB_DSH_WORKSPACE = $workspaceDir
            DECISION_HUB_DSH_CALLBACK_SECRET = 'core-live-callback'
            DECISION_HUB_RESEARCH_MIN_PRIORITY = '100'
            DECISION_HUB_RESEARCH_SERVICE_ID = 'hub-research-priority-worker'
            DECISION_HUB_INSTANCE_ID = 'local-product-research-priority'
        # The priority lane is selected through the environment, while the
        # Python command line is intentionally the same research entrypoint as
        # the normal lane. Keep a marker that the managed stop script can
        # actually find in the child command line.
        }) -Marker 'apps.hub_worker.main --role research --interval 1'

    $historicalWorkerProcess = Start-ProductProcess -Name 'historical-worker' -FilePath $uv `
        -ArgumentList @('run', 'python', '-m', 'apps.hub_worker.main', '--role', 'historical', '--interval', '3600') `
        -Environment $common -Marker 'apps.hub_worker.main --role historical'

    # Candidate generation is automatic; activation still requires owner review.
    # Invalid evaluation inputs persist a waiting candidate, never a fake score.
    $evolutionWorkerProcess = Start-ProductProcess -Name 'evolution-worker' -FilePath $uv `
        -ArgumentList @('run', 'python', '-m', 'apps.hub_worker.main', '--role', 'evolution', '--interval', '10') `
        -Environment ($common + $searchEnvironment + @{
            DECISION_HUB_LLM_ENABLED = '1'
            DECISION_HUB_TIMEOUT_SECONDS = '45'
            DECISION_HUB_TOKEN_BUDGET = '2500'
            DECISION_HUB_MAX_RETRIES = '0'
            DECISION_HUB_INSTANCE_ID = 'local-product-evolution'
        }) -Marker 'apps.hub_worker.main --role evolution'

    Wait-ResearchWorkerReady -Process $evolutionWorkerProcess `
        -Url "http://127.0.0.1:$ApiPort/v1/operations" `
        -ServiceId 'hub-evolution-worker'

    Wait-ResearchWorkerReady -Process $researchWorkerProcess `
        -Url "http://127.0.0.1:$ApiPort/v1/operations"
    Wait-ResearchWorkerReady -Process $priorityResearchWorkerProcess `
        -Url "http://127.0.0.1:$ApiPort/v1/operations" `
        -ServiceId 'hub-research-priority-worker'
    if ($historicalWorkerProcess.HasExited) {
        $initialFailures.Add('historical-worker')
        Write-Warning 'Historical worker exited; isolated recovery will retry.'
    }

    Write-ProductJson -Path $stateFile -Value $started.ToArray()
    $supervisorEntry.state = 'running'
    Write-ProductJson -Path $supervisorFile -Value $supervisorEntry
    Write-Host ''
    if (-not $DevelopmentMode) {
        Write-Host "DSH_URL=$dshUrl"
        Write-Host "DSH_TRAJECTORY_URL=$dshUrl"
    } else { Write-Host "DSH_TRAJECTORY_LOG=$dshLog" }
    Write-Host "DECISION_DESK_URL=$deskOrigin/"
    Write-Host "DECISION_DESK_LOCAL_ENTRY=$($settingsSession.EntryPath)"
    Write-Host 'USER_ENTRY=DECISION_DESK_LOCAL_ENTRY (open this private local shortcut for signed owner access)'
    Write-Host "Process state: $stateFile"
    if ($initialFailures.Count -eq 0) {
        Write-Host 'Product is ready. Keep this window open while using the Decision Desk.'
    } else {
        Write-Warning "Product started with unavailable services: $($initialFailures -join ', ')."
    }

    # Keep report access independent from research, acquisition and history.
    while (-not (Test-Path -LiteralPath $stopRequest)) {
        Start-Sleep -Seconds 2
        if (Test-Path -LiteralPath $stopRequest) { break }
        if ($checkpointState.degraded -or $script:productPendingBirths.Count) {
            if ($checkpointState.degraded -and $checkpointState.next_retry_at -and
                [datetime]::UtcNow -lt ([datetime]$checkpointState.next_retry_at).ToUniversalTime()) { continue }
            # Refresh exact live lifetimes without clearing old leaves or
            # admitting a restart before the latest ownership snapshot saves.
            Invoke-ProductSupervision -Entries $started -Restart {
                throw 'product_checkpoint_recovery_cannot_launch'
            } -CanRestart { $false } -HealthCheck $managedHealthCheck
            $birthsResolved = Resolve-ProductPendingBirths
            $snapshot = @($started.ToArray()) + @($script:productPendingBirths.ToArray() |
                Where-Object state -ne 'registration_identity_pending')
            $saved = Invoke-ProductStateCheckpoint -Path $stateFile -Value $snapshot -State $checkpointState
            if (-not $saved -or -not $birthsResolved) { continue }
        }
        Invoke-ProductSupervision -Entries $started -Restart {
            param($entry)
            $spec = $launchSpecs[$entry.name]
            Start-ProductProcess @spec -ExistingEntry $entry
        } -CanRestart { -not $checkpointState.degraded -and $script:productPendingBirths.Count -eq 0 } `
            -HealthCheck $managedHealthCheck
        $snapshot = @($started.ToArray()) + @($script:productPendingBirths.ToArray() |
            Where-Object state -ne 'registration_identity_pending')
        Invoke-ProductStateCheckpoint -Path $stateFile -Value $snapshot -State $checkpointState | Out-Null
    }
} catch {
    # Startup errors are recorded before cleanup; normal worker exits above
    # are handled per role and never enter this product-wide shutdown path.
    $supervisorEntry.state = 'startup_failed'
    Write-ProductJson -Path $supervisorFile -Value $supervisorEntry
    throw
} finally {
    try {
        if ($DevelopmentMode) { $developmentCleanupResiduals = @() }
        foreach ($entry in $started.ToArray()) {
            if ($DevelopmentMode) {
                $developmentCleanupResiduals += @(Stop-ProductProcessTree -Entry $entry)
            } else { Stop-ProductProcessTree -Entry $entry | Out-Null }
        }
        # Stop is strict: retry the original Process identity now, rather than
        # silently treating an inaccessible newborn as already gone.
        if (-not (Resolve-ProductPendingBirths -Force)) {
            throw 'product_process_cleanup_unconfirmed'
        }
        if ($DevelopmentMode) {
            Write-DevelopmentCleanupReceipt -RuntimeDirectory $runtimeDir -Supervisor $supervisorEntry `
                -Entries (@($started.ToArray()) + @($developmentCleanupResiduals))
        }
    } finally {
        $ownership.Dispose()
    }
}
