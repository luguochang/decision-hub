[CmdletBinding()]
param([string]$DevelopmentRoot = 'data/development/default',
    [ValidateRange(0, 30)][int]$GraceSeconds = 6)
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'development_powershell7_required' }
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
. (Join-Path $PSScriptRoot 'development-runtime.ps1')
. (Join-Path $PSScriptRoot 'product-processes.ps1')
. (Join-Path $PSScriptRoot 'development-watchdog.ps1')
$layout = Resolve-DevelopmentLayout -RepositoryRoot $repoRoot -DevelopmentRoot $DevelopmentRoot
New-Item -ItemType Directory -Path $layout.runtime -Force | Out-Null
try {
    $watchdogControl = [IO.File]::Open((Join-Path $layout.runtime 'watchdog.lock'),
        [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
} catch { throw 'development_stop_unconfirmed_watchdog_control_busy' }
try {
Set-Content -LiteralPath (Join-Path $layout.runtime 'watchdog.pause') -Value ([datetime]::UtcNow.ToString('o')) -Encoding utf8
Add-Content -LiteralPath (Join-Path $layout.runtime 'watchdog-audit.jsonl') -Value (
    @{schema='development-watchdog.v1';action='paused_by_stop';observed_at=[datetime]::UtcNow.ToString('o')} |
    ConvertTo-Json -Compress) -Encoding utf8
$watchdogPending = @(Get-DevelopmentWatchdogStopGuard -Layout $layout)
$stateFile = Join-Path $layout.runtime 'processes.json'
$supervisorFile = Join-Path $layout.runtime 'supervisor.json'
if (-not (Test-Path -LiteralPath $supervisorFile)) {
    $status = Get-DevelopmentStatus -RepositoryRoot $repoRoot -Layout $layout
    if ($status.observed_state -ne 'absent') { throw 'development_stop_unconfirmed_state_preserved' }
    Write-Host 'No managed development supervisor was found.'; exit 0
}
$supervisor = Get-Content -LiteralPath $supervisorFile -Raw | ConvertFrom-Json
if (-not (Test-Path -LiteralPath $stateFile -ErrorAction Stop)) {
    throw 'development_stop_unconfirmed_state_preserved'
}
$entries = @(Get-Content -LiteralPath $stateFile -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop)
$recorded = @(Get-DevelopmentTerminalInventory -Supervisor $supervisor -Entries $entries) + $watchdogPending
$lifetime = Get-DevelopmentLifetime $supervisor
if ($lifetime.state -ne 'alive') {
    $status = Get-DevelopmentStatus -RepositoryRoot $repoRoot -Layout $layout
    # A dead supervisor never licenses killing mutable saved/orphan identities.
    if ($status.observed_state -ne 'absent') { throw 'development_stop_unconfirmed_state_preserved' }
    Assert-DevelopmentSameLaunch -SupervisorFile $supervisorFile -Supervisor $supervisor
    Write-DevelopmentStoppedReceipt -RuntimeDirectory $layout.runtime -Supervisor $supervisor `
        -Targets $recorded
    exit 0
}
$verified = @(Get-DevelopmentVerifiedProcesses -RepositoryRoot $repoRoot -Layout $layout -Supervisor $supervisor -Entries $entries)
$supervisorSafe = [pscustomobject]@{pid=$supervisor.pid;started_ticks=$supervisor.started_ticks;children=@()}
# Capture actual owner descendants too, including newborns not checkpointed yet.
$supervisorSafe.children = @(Get-ProductProcessDescendants $supervisorSafe)
$targets = @($supervisorSafe) + @($supervisorSafe.children) + @($verified) + @($verified | ForEach-Object { $_.children })
# Validate before request; let the existing supervisor loop perform cleanup.
Set-Content -LiteralPath (Join-Path $layout.runtime 'stop.request') -Value 'stop' -Encoding utf8
$deadline = [datetime]::UtcNow.AddSeconds($GraceSeconds)
do {
    $remaining = @($recorded | ForEach-Object { Get-DevelopmentLifetime $_ } | Where-Object state -in @('alive','unknown'))
    if (-not $remaining.Count) { break }
    Start-Sleep -Milliseconds 100
} while ([datetime]::UtcNow -lt $deadline)
# The supervisor can checkpoint a birth/restart during the grace window.
# Re-read even when the original inventory has exited.
$entries = @(Get-Content -LiteralPath $stateFile -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop)
$recorded += @(Get-DevelopmentTerminalInventory -Supervisor $supervisor -Entries $entries)
$remaining = @($recorded | ForEach-Object { Get-DevelopmentLifetime $_ } | Where-Object state -in @('alive','unknown'))
if ($remaining.Count) {
    # Discover fresh descendants only through a still verified original owner.
    if ((Get-DevelopmentLifetime $supervisor).state -eq 'alive') {
        $fresh = @(Get-DevelopmentVerifiedProcesses -RepositoryRoot $repoRoot -Layout $layout -Supervisor $supervisor -Entries $entries)
        $supervisorSafe.children = @(Get-ProductProcessDescendants $supervisorSafe)
        $targets += @($supervisorSafe.children) + @($fresh) + @($fresh | ForEach-Object { $_.children })
    }
    # Each force action binds to a Process object and checks its exact lifetime
    # again; never act on a port, recycled PID, or unknown identity.
    foreach ($target in $targets) {
        $current = Get-DevelopmentLifetime $target
        if ($current.state -ne 'alive') { continue }
        $process = Get-Process -Id $target.pid -ErrorAction Stop
        if ($process.StartTime.ToUniversalTime().Ticks.ToString() -ceq [string]$target.started_ticks) {
            try { $process.Kill(); [void]$process.WaitForExit(2000) } catch { }
        }
    }
}
# Refuse a concurrent new launch before writing terminal state.
Assert-DevelopmentSameLaunch -SupervisorFile $supervisorFile -Supervisor $supervisor
$finalEntries = @(Get-Content -LiteralPath $stateFile -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop)
$recorded += @(Get-DevelopmentTerminalInventory -Supervisor $supervisor -Entries $finalEntries)
if ($watchdogPending.Count) {
    $pendingDeadline = [datetime]::UtcNow.AddSeconds(2)
    while (@($watchdogPending | Where-Object { (Get-DevelopmentLifetime $_).state -in @('alive','unknown') }).Count -and
        [datetime]::UtcNow -lt $pendingDeadline) { Start-Sleep -Milliseconds 50 }
}
Write-DevelopmentStoppedReceipt -RuntimeDirectory $layout.runtime -Supervisor $supervisor -Targets (@($targets) + @($recorded))
Write-Host 'Managed development processes stopped; data and logs were preserved.'
} finally { $watchdogControl.Dispose() }
