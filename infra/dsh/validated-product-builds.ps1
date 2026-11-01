# Read-only preflight for explicitly reusing a previously validated product build.
# This helper never builds, writes credentials, or starts product processes.
function Test-ValidatedProductBuilds {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ReceiptPath,
        [Parameter(Mandatory = $true)][string]$RepositoryRoot
    )

    $taskRepoRoot = [System.IO.Path]::GetFullPath($RepositoryRoot).TrimEnd('\', '/')
    $taskReceiptPath = (Resolve-Path -LiteralPath $ReceiptPath -ErrorAction Stop).Path
    $taskReceipt = Get-Content -LiteralPath $taskReceiptPath -Raw -ErrorAction Stop | ConvertFrom-Json
    if ($null -eq $taskReceipt -or $taskReceipt -isnot [pscustomobject] -or
        $taskReceipt.schema -cne 'product-validated-builds.v1') {
        throw 'Validated build receipt has an unsupported schema.'
    }
    foreach ($taskProperty in $taskReceipt.PSObject.Properties.Name) {
        if ($taskProperty -cnotin @('schema', 'repository_root', 'sources', 'artifacts', 'plugin_build_hash', 'evidence')) {
            throw "Unexpected validated build receipt field: $taskProperty"
        }
    }
    if ($taskReceipt.repository_root -isnot [string] -or
        -not [System.IO.Path]::IsPathRooted($taskReceipt.repository_root) -or
        [System.IO.Path]::GetFullPath($taskReceipt.repository_root).TrimEnd('\', '/') -ne $taskRepoRoot) {
        throw 'Validated build receipt belongs to a different repository.'
    }
    if ($taskReceipt.plugin_build_hash -isnot [string] -or
        $taskReceipt.plugin_build_hash -cnotmatch '^[a-f0-9]{64}$') {
        throw 'Validated build receipt is missing its plugin build hash.'
    }

    function Resolve-ValidatedBuildFile {
        param([string]$RelativePath)
        # Require one canonical spelling; reject traversal, drives, UNC paths,
        # alternate data streams, and links which could escape the repository.
        if ([string]::IsNullOrWhiteSpace($RelativePath) -or
            $RelativePath -match '[\\\x00-\x1f<>:"|?*]' -or
            [System.IO.Path]::IsPathRooted($RelativePath) -or
            @($RelativePath.Split('/') | Where-Object { $_ -in @('', '.', '..') }).Count) {
            throw "Invalid repository-relative build path: $RelativePath"
        }
        $taskFilePath = [System.IO.Path]::GetFullPath((Join-Path $taskRepoRoot $RelativePath))
        if (-not $taskFilePath.StartsWith($taskRepoRoot + [System.IO.Path]::DirectorySeparatorChar,
                [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Build path escapes the repository: $RelativePath"
        }
        $taskItem = Get-Item -LiteralPath $taskFilePath -Force -ErrorAction Stop
        if ($taskItem.PSIsContainer) { throw "Build receipt path is not a file: $RelativePath" }
        if ($taskItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw "Build receipt cannot reference a linked path: $RelativePath"
        }
        for ($taskParent = $taskItem.Directory; $taskParent.FullName -ne $taskRepoRoot; $taskParent = $taskParent.Parent) {
            if ($null -eq $taskParent -or
                ($taskParent.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
                throw "Build receipt cannot reference a linked path: $RelativePath"
            }
        }
        return $taskFilePath
    }

    $taskMaps = @{}
    foreach ($taskMapName in @('sources', 'artifacts')) {
        $taskMap = $taskReceipt.$taskMapName
        if ($taskMap -isnot [pscustomobject] -or @($taskMap.PSObject.Properties).Count -eq 0) {
            throw "Validated build receipt has no $taskMapName."
        }
        $taskHashes = [System.Collections.Generic.Dictionary[string, string]]::new([System.StringComparer]::Ordinal)
        foreach ($taskEntry in $taskMap.PSObject.Properties) {
            if ($taskEntry.Value -isnot [string] -or $taskEntry.Value -cnotmatch '^[a-f0-9]{64}$') {
                throw "Invalid SHA-256 for $taskMapName path: $($taskEntry.Name)"
            }
            if ($taskMapName -eq 'artifacts' -and $taskEntry.Name -cnotmatch '^(apps/decision-desk/dist/|extensions/dsh/decision-hub/lib/)') {
                throw "Unexpected product build artifact path: $($taskEntry.Name)"
            }
            $taskFilePath = Resolve-ValidatedBuildFile $taskEntry.Name
            $taskFileSha = [System.Security.Cryptography.SHA256]::Create()
            $taskFileStream = [System.IO.File]::OpenRead($taskFilePath)
            try {
                $taskActualHash = [System.BitConverter]::ToString(
                    $taskFileSha.ComputeHash($taskFileStream)
                ).Replace('-', '').ToLowerInvariant()
            } finally {
                $taskFileStream.Dispose()
                $taskFileSha.Dispose()
            }
            if ($taskActualHash -cne $taskEntry.Value) {
                throw "Validated build $taskMapName changed: $($taskEntry.Name)"
            }
            $taskHashes.Add($taskEntry.Name, $taskEntry.Value)
        }
        $taskMaps[$taskMapName] = $taskHashes
    }

    $taskRequiredSources = @(
        'package.json', 'pnpm-lock.yaml', 'pnpm-workspace.yaml',
        'apps/decision-desk/index.html', 'apps/decision-desk/package.json',
        'apps/decision-desk/tsconfig.json', 'apps/decision-desk/vite.config.ts',
        'packages/contracts_ts/package.json', 'extensions/dsh/decision-hub/package.json',
        'extensions/dsh/decision-hub/cordis.patch.yml', 'extensions/dsh/decision-hub/tsconfig.json',
        'extensions/dsh/decision-hub/vite.host.config.ts',
        'infra/dsh/presets/decision-research/agent.cordis.yml',
        'infra/dsh/presets/decision-research/preset.yml', 'infra/dsh/upstream.lock.json'
    )
    # Enumerating current inputs also rejects a receipt which omits newly added
    # source files. Old, unrelated UI assets can remain in the published dist.
    foreach ($taskSourceDirectory in @('apps/decision-desk/src', 'apps/decision-desk/public',
            'packages/contracts_ts/src', 'contracts/schemas',
            'extensions/dsh/decision-hub/src', 'extensions/dsh/decision-hub/scripts')) {
        $taskDirectory = Get-Item -LiteralPath (Join-Path $taskRepoRoot $taskSourceDirectory) -Force -ErrorAction Stop
        if (-not $taskDirectory.PSIsContainer -or
            ($taskDirectory.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw "Invalid product source directory: $taskSourceDirectory"
        }
        foreach ($taskSourceFile in Get-ChildItem -LiteralPath $taskDirectory.FullName -Recurse -Force) {
            if ($taskSourceFile.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                throw "Linked product source input: $($taskSourceFile.FullName)"
            }
            if (-not $taskSourceFile.PSIsContainer) {
                $taskRequiredSources += $taskSourceFile.FullName.Substring($taskRepoRoot.Length + 1).Replace('\', '/')
            }
        }
    }
    foreach ($taskRequiredSource in $taskRequiredSources) {
        if (-not $taskMaps.sources.ContainsKey($taskRequiredSource)) {
            throw "Validated build receipt omits current source: $taskRequiredSource"
        }
    }

    $taskRequiredArtifacts = @('apps/decision-desk/dist/index.html',
        'extensions/dsh/decision-hub/lib/index.js', 'extensions/dsh/decision-hub/lib/research-tool.js',
        'extensions/dsh/decision-hub/lib/synthesis-tool.js', 'extensions/dsh/decision-hub/lib/client.js')
    foreach ($taskPluginFile in Get-ChildItem -LiteralPath (Join-Path $taskRepoRoot 'extensions/dsh/decision-hub/lib') -File -Recurse -Force) {
        $taskRequiredArtifacts += $taskPluginFile.FullName.Substring($taskRepoRoot.Length + 1).Replace('\', '/')
    }
    $taskHtml = Get-Content -LiteralPath (Join-Path $taskRepoRoot 'apps/decision-desk/dist/index.html') -Raw
    foreach ($taskReference in [regex]::Matches($taskHtml, '(?:src|href)\s*=\s*["'']([^"'']+)["'']')) {
        $taskReferencePath = $taskReference.Groups[1].Value
        if ($taskReferencePath -match '^(?:[a-z]+:|//|#)') { continue }
        $taskReferencePath = ($taskReferencePath -split '[?#]', 2)[0].TrimStart('/')
        if ($taskReferencePath) { $taskRequiredArtifacts += "apps/decision-desk/dist/$taskReferencePath" }
    }
    foreach ($taskRequiredArtifact in $taskRequiredArtifacts) {
        if (-not $taskMaps.artifacts.ContainsKey($taskRequiredArtifact)) {
            throw "Validated build receipt omits required artifact: $taskRequiredArtifact"
        }
    }

    $taskIdentityPath = 'extensions/dsh/decision-hub/src/generated-build-identity.ts'
    $taskIdentityText = Get-Content -LiteralPath (Resolve-ValidatedBuildFile $taskIdentityPath) -Raw
    $taskIdentityHash = [regex]::Match($taskIdentityText, 'PLUGIN_BUILD_HASH\s*=\s*"([a-f0-9]{64})"')
    $taskIdentityInputs = [regex]::Match($taskIdentityText, 'PLUGIN_BUILD_INPUTS\s*=\s*(\[[^\r\n]*\])\s+as const;')
    if (-not $taskIdentityHash.Success -or -not $taskIdentityInputs.Success -or
        $taskIdentityHash.Groups[1].Value -cne $taskReceipt.plugin_build_hash) {
        throw 'Generated plugin build identity does not match the validated receipt.'
    }
    $taskInputs = $taskIdentityInputs.Groups[1].Value | ConvertFrom-Json
    $taskExpectedInputs = @('extensions/dsh/decision-hub/src/index.ts',
        'extensions/dsh/decision-hub/src/research-tool.ts', 'extensions/dsh/decision-hub/src/query-catalog.ts',
        'extensions/dsh/decision-hub/src/synthesis-tool.ts', 'extensions/dsh/decision-hub/src/client/index.js',
        'extensions/dsh/decision-hub/src/final-review-gate.ts',
        'extensions/dsh/decision-hub/src/reviewer-tool.ts',
        'extensions/dsh/decision-hub/src/frozen-review-prompt.ts',
        'extensions/dsh/decision-hub/package.json', 'extensions/dsh/decision-hub/cordis.patch.yml',
        'infra/dsh/presets/decision-research/agent.cordis.yml',
        'infra/dsh/presets/decision-research/preset.yml', 'infra/dsh/upstream.lock.json')
    foreach ($taskHostFile in Get-ChildItem -LiteralPath (Join-Path $taskRepoRoot 'extensions/dsh/decision-hub/src/host') -File -Recurse -Force) {
        $taskExpectedInputs += $taskHostFile.FullName.Substring($taskRepoRoot.Length + 1).Replace('\', '/')
    }
    if ($taskInputs.Count -ne $taskExpectedInputs.Count -or
        @($taskInputs | Select-Object -Unique).Count -ne $taskInputs.Count -or
        @(Compare-Object $taskInputs $taskExpectedInputs -CaseSensitive).Count) {
        throw 'Generated plugin build identity omits or adds plugin inputs.'
    }
    $taskHashBytes = [System.IO.MemoryStream]::new()
    $taskSha = [System.Security.Cryptography.SHA256]::Create()
    try {
        foreach ($taskInput in $taskInputs) {
            if ($taskInput -isnot [string] -or -not $taskMaps.sources.ContainsKey($taskInput)) {
                throw 'Generated plugin identity contains an unvalidated input.'
            }
            $taskBytes = [System.IO.File]::ReadAllBytes((Resolve-ValidatedBuildFile $taskInput))
            $taskPrefix = [System.Text.Encoding]::UTF8.GetBytes($taskInput + [char]0 +
                $taskBytes.Length.ToString([System.Globalization.CultureInfo]::InvariantCulture) + ':')
            $taskHashBytes.Write($taskPrefix, 0, $taskPrefix.Length)
            $taskHashBytes.Write($taskBytes, 0, $taskBytes.Length)
            $taskHashBytes.WriteByte(0)
        }
        $taskComputedHash = [System.BitConverter]::ToString($taskSha.ComputeHash($taskHashBytes.ToArray())).Replace('-', '').ToLowerInvariant()
    } finally {
        $taskSha.Dispose()
        $taskHashBytes.Dispose()
    }
    $taskPluginEntry = Get-Content -LiteralPath (Join-Path $taskRepoRoot 'extensions/dsh/decision-hub/lib/index.js') -Raw
    if ($taskComputedHash -cne $taskReceipt.plugin_build_hash -or
        -not $taskPluginEntry.Contains($taskReceipt.plugin_build_hash)) {
        throw 'Current plugin inputs or built plugin entry do not match its validated identity.'
    }
    return [pscustomobject]@{
        receipt_path = $taskReceiptPath
        plugin_build_hash = $taskComputedHash
        source_count = $taskMaps.sources.Count
        artifact_count = $taskMaps.artifacts.Count
    }
}
