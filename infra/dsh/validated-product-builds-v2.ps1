# Explicit v2 verifier. The fixed v1 guard remains unchanged.
function Test-ValidatedProductBuildsV2 {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$ReceiptPath,
        [Parameter(Mandatory = $true)][string]$RepositoryRoot)
    $root = [System.IO.Path]::GetFullPath($RepositoryRoot).TrimEnd('\', '/')
    $codec = Join-Path $root 'packages/kernel/decision_hub_kernel/application/product_release_provenance.py'
    $output = & (Join-Path $root '.venv/Scripts/python.exe') -X utf8 -B $codec `
        --repository-root $root --verify $ReceiptPath
    if ($LASTEXITCODE -ne 0) { throw 'product_validated_build_v2_rejected' }
    $proof = ($output -join "`n") | ConvertFrom-Json -ErrorAction Stop
    if ($proof.receipt_schema -cne 'product-validated-builds.v2' -or
        $proof.receipt_sha256 -cnotmatch '^[a-f0-9]{64}$' -or
        $proof.verifier_source_sha256 -cnotmatch '^[a-f0-9]{64}$' -or
        $proof.source_count -le 0 -or $proof.artifact_count -le 0) {
        throw 'product_validated_build_v2_proof_invalid'
    }
    return $proof
}
