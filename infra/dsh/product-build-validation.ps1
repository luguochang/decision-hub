# Standard schema dispatch. Unknown schemas have no fallback or skip flag.
. (Join-Path $PSScriptRoot 'validated-product-builds-v2.ps1')
function Test-ProductValidatedBuilds {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$ReceiptPath,
        [Parameter(Mandatory = $true)][string]$RepositoryRoot)
    if ((Get-Item -LiteralPath $ReceiptPath -ErrorAction Stop).Length -gt 2097152) {
        throw 'harness_install_receipt_oversized'
    }
    $receipt = Get-Content -LiteralPath $ReceiptPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    # Both standard formats preserve the fixed old verifier's exact identity.
    $guard = Join-Path $PSScriptRoot 'validated-product-builds.ps1'
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($guard)
    try { $guardHash = [System.BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $sha.Dispose() }
    if ($guardHash -cne 'b7dda65ac008da102396e2436ec775e90b453c52105a015b804f07c3eca62d6a') {
        throw 'harness_install_guard_source_changed'
    }
    if ($receipt.schema -ceq 'product-validated-builds.v1') {
        . $guard
        return Test-ValidatedProductBuilds -ReceiptPath $ReceiptPath -RepositoryRoot $RepositoryRoot
    }
    if ($receipt.schema -ceq 'product-validated-builds.v2') {
        return Test-ValidatedProductBuildsV2 -ReceiptPath $ReceiptPath -RepositoryRoot $RepositoryRoot
    }
    throw 'product_validated_build_schema_unsupported'
}

function Get-ResolvedProductBuildReceipt {
    param([Parameter(Mandatory = $true)][string]$ReceiptPath,
        [Parameter(Mandatory = $true)][string]$RepositoryRoot)
    $codec = Join-Path $RepositoryRoot 'packages/kernel/decision_hub_kernel/application/product_release_provenance.py'
    $output = & (Join-Path $RepositoryRoot '.venv/Scripts/python.exe') -X utf8 -B $codec `
        --repository-root $RepositoryRoot --resolve $ReceiptPath
    if ($LASTEXITCODE -ne 0) { throw 'product_provenance_resolution_rejected' }
    return (($output -join "`n") | ConvertFrom-Json -ErrorAction Stop)
}

function New-ProductHistoricalSourceMap {
    param([Parameter(Mandatory = $true)][System.Collections.IDictionary]$SourceMap,
        [Parameter(Mandatory = $true)][string]$RepositoryRoot)
    $codec = Join-Path $RepositoryRoot 'packages/kernel/decision_hub_kernel/application/product_release_provenance.py'
    $output = ($SourceMap | ConvertTo-Json -Depth 10 -Compress) | `
        & (Join-Path $RepositoryRoot '.venv/Scripts/python.exe') -X utf8 -B $codec `
            --repository-root $RepositoryRoot --encode
    if ($LASTEXITCODE -ne 0) { throw 'product_provenance_encoding_rejected' }
    return (($output -join "`n") | ConvertFrom-Json -AsHashtable -ErrorAction Stop)
}
