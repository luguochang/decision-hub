[CmdletBinding()]
param([string]$Role = 'realtime-worker', [string]$Output = 'data/product-local/remediation/W2/live-recovery.json')
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
. (Join-Path $repoRoot 'infra\dsh\product-processes.ps1')
$statePath = Join-Path $repoRoot 'data\product-local\processes.json'
$entries = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
$target = $entries | Where-Object name -eq $Role
$api = $entries | Where-Object name -eq 'hub-api'
if (-not $target -or $Role -eq 'hub-api') { throw 'Select one existing worker role, not the API.' }
if (-not (Test-ProductProcessIdentity $target)) { throw 'Worker identity unavailable before probe.' }
if (-not (Test-ProductProcessIdentity $api)) { throw 'API identity unavailable before probe.' }
$before = $target.pid
$started = [datetime]::UtcNow
$survivors = @(Stop-ProductProcessTree $target)
if ($survivors.Count -gt 0) { throw 'Injected process stop incomplete.' }
$checks = [System.Collections.Generic.List[object]]::new()
$recovered = $null
$deadline = [datetime]::UtcNow.AddSeconds(45)
do {
    $ok = $false
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8047/v1/research/inbox?limit=1' -TimeoutSec 3
        $ok = $response.StatusCode -eq 200 -and (Test-ProductProcessIdentity $api)
    } catch {}
    $checks.Add([pscustomobject]@{ at=[datetime]::UtcNow.ToString('o'); api_unchanged_and_report_readable=$ok })
    $current = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    $candidate = $current | Where-Object name -eq $Role
    if ($candidate.pid -ne $before -and (Test-ProductProcessIdentity $candidate)) {
        $operations = Invoke-RestMethod -Uri 'http://127.0.0.1:8047/v1/operations' -TimeoutSec 3
        $heartbeat = $operations.services | Where-Object service_id -eq 'hub-realtime-worker'
        if ($Role -ne 'realtime-worker' -or ($heartbeat.status -eq 'online' -and ([datetime]$heartbeat.started_at).ToUniversalTime() -gt $started)) {
            $recovered = $candidate
            break
        }
    }
    Start-Sleep -Seconds 1
} while ([datetime]::UtcNow -lt $deadline)
$report = [pscustomobject]@{
    started_at=$started.ToString('o'); finished_at=[datetime]::UtcNow.ToString('o')
    role=$Role; previous_pid=$before; recovered_pid=$recovered.pid
    api_pid=$api.pid; all_api_checks_passed=(@($checks | Where-Object { -not $_.api_unchanged_and_report_readable }).Count -eq 0)
    recovered=($null -ne $recovered); restart_count=$recovered.restart_count; checks=$checks.ToArray()
}
$outputPath = Join-Path $repoRoot $Output
New-Item -ItemType Directory -Path (Split-Path $outputPath) -Force | Out-Null
Write-ProductJson -Path $outputPath -Value $report
$report | Select-Object role, recovered, all_api_checks_passed, restart_count | ConvertTo-Json -Compress
if (-not $report.recovered -or -not $report.all_api_checks_passed) { throw 'Live worker isolation probe failed; inspect evidence.' }
