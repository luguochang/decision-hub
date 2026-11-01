[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$stateFile = Join-Path $repoRoot 'data\product-local\processes.json'
$supervisorFile = Join-Path $repoRoot 'data\product-local\supervisor.json'
. (Join-Path $PSScriptRoot 'product-processes.ps1')

# Intentional stop disables retries before touching any worker.
if (Test-Path -LiteralPath (Join-Path $repoRoot 'data\product-local')) {
    Set-Content -LiteralPath (Join-Path $repoRoot 'data\product-local\stop.request') -Value 'stop' -Encoding utf8
}
if (Test-Path -LiteralPath $supervisorFile) {
    $supervisor = Get-Content -LiteralPath $supervisorFile -Raw | ConvertFrom-Json
    $remaining = @(Stop-ProductProcessTree -Entry $supervisor)
    if ($remaining.Count -gt 0) { throw 'Supervisor did not stop; process state preserved.' }
    $supervisor.state = 'stopped'
    Write-ProductJson -Path $supervisorFile -Value $supervisor
}

if (-not (Test-Path -LiteralPath $stateFile)) {
    Write-Host 'No managed local product process state was found.'
    exit 0
}

# Windows PowerShell 5.1 returns a JSON array as one Object[] pipeline item.
# Wrapping that result in @() creates a nested array, so `$entry.pid` becomes
# every PID at once and the CIM filter matches nothing.  foreach enumerates
# both a single object and an array correctly without the extra wrapper.
$entries = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
$failures = [System.Collections.Generic.List[string]]::new()
foreach ($entry in $entries) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($entry.pid)" -ErrorAction SilentlyContinue
    if (-not $entry.started_ticks) {
        if (-not $process) { continue }
        # Legacy state has no creation time. Verify command identity once;
        # every new launcher entry uses the stronger two-part identity.
        if ($process.CommandLine -notlike "*$($entry.marker)*") {
            $failures.Add("unverified legacy identity:$($entry.name)")
            continue
        }
        $entry | Add-Member -NotePropertyName started_ticks -NotePropertyValue (Get-ProductProcessStamp -ProcessId $entry.pid)
    }
    $survivors = @(Stop-ProductProcessTree -Entry $entry)
    if ($survivors.Count -gt 0) {
        $failures.Add("$($entry.name):$($survivors -join ',')")
    }
}

if ($failures.Count -gt 0) {
    throw "Managed product processes are still alive: $($failures -join '; '). Process state was preserved for retry."
}

Remove-Item -LiteralPath $stateFile -Force
Write-Host 'Managed Decision Hub product processes stopped; data and logs were preserved.'
