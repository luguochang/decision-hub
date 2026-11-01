[CmdletBinding()]
param(
    [string]$DevelopmentRoot = 'data/development/default',
    [ValidateRange(1, 5)][int]$MaxAttempts = 3,
    [ValidateRange(30, 3600)][int]$BackoffSeconds = 60,
    [ValidateRange(600, 86400)][int]$StableSeconds = 600,
    [switch]$Pause, [switch]$Resume
)
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'development_powershell7_required' }
if ($Pause -and $Resume) { throw 'watchdog_pause_resume_conflict' }
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
. (Join-Path $PSScriptRoot 'development-runtime.ps1')
. (Join-Path $PSScriptRoot 'product-processes.ps1')
. (Join-Path $PSScriptRoot 'development-watchdog.ps1')
$layout = Resolve-DevelopmentLayout -RepositoryRoot $repoRoot -DevelopmentRoot $DevelopmentRoot
New-Item -ItemType Directory -Path $layout.runtime -Force | Out-Null
$control = $null
$gate = $null; $readyEvent = $null
try {
    try {
        $control = [IO.File]::Open((Join-Path $layout.runtime 'watchdog.lock'),
            [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch {
        @{schema='development-watchdog.v1';action='busy'} | ConvertTo-Json -Compress
        return
    }
    $pausePath = Join-Path $layout.runtime 'watchdog.pause'
    $statePath = Join-Path $layout.runtime 'watchdog-state.json'
    $auditPath = Join-Path $layout.runtime 'watchdog-audit.jsonl'
    $now = [datetime]::UtcNow
    $state = [pscustomobject]@{schema='development-watchdog-state.v1';attempts=0
        next_attempt_at=$null;launch_identity=$null;last_error=$null}
    if (Test-Path -LiteralPath $statePath) {
        $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json -ErrorAction Stop
        if ($state.schema -cne 'development-watchdog-state.v1' -or
            ($state.attempts -isnot [long] -and $state.attempts -isnot [int]) -or
            $state.attempts -lt 0) { throw 'watchdog_state_invalid' }
    }
    foreach ($field in @('stable_since','last_healthy_at','stable_launch_id','stable_owner_ticks')) {
        if (-not $state.PSObject.Properties[$field]) {
            $state | Add-Member -NotePropertyName $field -NotePropertyValue $null
        }
    }
    function Write-WatchdogResult {
        param([string]$Action, [string]$Detail = '')
        $record = [ordered]@{schema='development-watchdog.v1';action=$Action
            observed_at=[datetime]::UtcNow.ToString('o');root=$layout.root
            attempts=$state.attempts;detail=$Detail}
        $json = $record | ConvertTo-Json -Compress
        Add-Content -LiteralPath $auditPath -Value $json -Encoding utf8
        $json
    }
    if ($Pause) {
        Set-Content -LiteralPath $pausePath -Value $now.ToString('o') -Encoding utf8
        Write-WatchdogResult -Action 'paused'; return
    }
    if ((Test-Path -LiteralPath $pausePath) -and -not $Resume) { Write-WatchdogResult -Action 'paused'; return }
    try { $intent = Read-DevelopmentWatchdogIntent $layout }
    catch { Write-WatchdogResult -Action 'blocked_intent' -Detail 'invalid'; return }
    if (-not $intent -and $state.attempts -gt 0 -and -not $state.launch_identity) {
        Write-WatchdogResult -Action 'blocked_intent' -Detail 'legacy_creation_birth_unknown'; return
    }
    $intentGuard = Get-DevelopmentWatchdogIntentGuard $intent
    if ($intentGuard -eq 'blocked') { Write-WatchdogResult -Action 'blocked_intent' -Detail 'unresolved'; return }
    if ($Resume) {
        # Explicit operator action resets only this watchdog's state. Audit is append-only.
        if (Test-Path -LiteralPath $pausePath) { Remove-Item -LiteralPath $pausePath }
        $state.attempts = 0; $state.next_attempt_at = $null; $state.last_error = $null
        $state.stable_since = $null; $state.last_healthy_at = $null
        Write-ProductJson -Path $statePath -Value $state
        $null = Write-WatchdogResult -Action 'resumed'
    }
    if (Test-Path -LiteralPath $pausePath) { Write-WatchdogResult -Action 'paused'; return }
    $status = Get-DevelopmentStatus -RepositoryRoot $repoRoot -Layout $layout
    if ($status.observed_state -eq 'running') {
        $ready = $false
        if (@($status.listeners | Where-Object { $_.port -eq 18047 -and $_.ownership -eq 'verified' }).Count) {
            try {
                $health = Invoke-RestMethod -Uri 'http://127.0.0.1:18047/health/ready' `
                    -NoProxy -TimeoutSec 3 -ErrorAction Stop
                $ready = $health.status -ceq 'ready'
            } catch { }
        }
        if ($ready) {
            if (-not $state.stable_since -or -not $state.last_healthy_at -or
                $state.stable_launch_id -cne $status.launch_id -or
                $state.stable_owner_ticks -cne [string]$status.supervisor_identity.started_ticks -or
                ($now - ([datetime]$state.last_healthy_at).ToUniversalTime()).TotalSeconds -gt 180) {
                $state.stable_since = $now.ToString('o')
            }
            $state.last_healthy_at = $now.ToString('o')
            $state.stable_launch_id = $status.launch_id
            $state.stable_owner_ticks = [string]$status.supervisor_identity.started_ticks
            if ($state.attempts -gt 0 -and
                ($now - ([datetime]$state.stable_since).ToUniversalTime()).TotalSeconds -ge $StableSeconds) {
                $state.attempts = 0; $state.next_attempt_at = $null; $state.last_error = $null
                $null = Write-WatchdogResult -Action 'stable_reset'
            }
        } else { $state.stable_since = $null; $state.last_healthy_at = $null }
        Write-ProductJson -Path $statePath -Value $state
        Write-WatchdogResult -Action 'already_running'; return
    }
    $state.stable_since = $null; $state.last_healthy_at = $null
    if (Test-Path -LiteralPath $statePath) { Write-ProductJson -Path $statePath -Value $state }
    if ($status.observed_state -ne 'absent') {
        Write-WatchdogResult -Action 'blocked_status' -Detail $status.observed_state; return
    }
    if ($intentGuard -eq 'pending') { Write-WatchdogResult -Action 'launch_pending'; return }
    # Include all official default ports even when old snapshots have no probes.
    # Automatic recovery uses run-dev's official default port configuration.
    $ports = @(18047,18097,18037,18038,5173)
    try { $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop) }
    catch { Write-WatchdogResult -Action 'blocked_ports' -Detail 'inventory_unknown'; return }
    if (@($status.listeners).Count -or @($listeners | Where-Object LocalPort -in $ports).Count) {
        Write-WatchdogResult -Action 'blocked_ports' -Detail 'occupied'; return
    }
    if ($state.launch_identity) {
        $identity = Get-DevelopmentLifetime $state.launch_identity
        if ($identity.state -in @('alive','unknown')) {
            Write-WatchdogResult -Action 'launch_pending' -Detail $identity.state; return
        }
    }
    if ($state.attempts -ge $MaxAttempts) { Write-WatchdogResult -Action 'exhausted'; return }
    if ($state.next_attempt_at -and ([datetime]$state.next_attempt_at).ToUniversalTime() -gt $now) {
        Write-WatchdogResult -Action 'backoff'; return
    }
    # run-dev keeps its own supervisor.lock and full admission checks. Never kill
    # or take ownership of an unknown process or a port listener here.
    Assert-DevelopmentNoSharedRuntime -RepositoryRoot $repoRoot -Layout $layout
    $state.attempts++
    $state.next_attempt_at = $now.AddSeconds([Math]::Min(3600,
        $BackoffSeconds * [Math]::Pow(2, $state.attempts - 1))).ToString('o')
    $state.launch_identity = $null
    Write-ProductJson -Path $statePath -Value $state
    $logs = Join-Path $layout.runtime 'logs'
    New-Item -ItemType Directory -Path $logs -Force | Out-Null
    $stamp = $now.ToString('yyyyMMddTHHmmssfffffffZ')
    $runPath = Join-Path $PSScriptRoot 'run-dev.ps1'
    if ($intent) {
        [IO.File]::Copy((Join-Path $layout.runtime 'watchdog-launch-intent.json'),
            (Join-Path $layout.runtime "watchdog-launch-intent-$($intent.id).json"), $false)
    }
    $intent = [pscustomobject]@{schema='development-watchdog-intent.v1';id=[guid]::NewGuid().ToString('N')
        root=$layout.root;phase='unresolved';created_at=$now.ToString('o')
        launch_identity=$null;supervisor_identity=$null;launch_id=$null}
    $names = Get-DevelopmentWatchdogEventNames -Layout $layout -IntentId $intent.id
    $created = $false
    $gate = [Threading.EventWaitHandle]::new($false, [Threading.EventResetMode]::ManualReset, $names.gate, [ref]$created)
    if (-not $created) { throw 'watchdog_gate_already_exists' }
    $readyEvent = [Threading.EventWaitHandle]::new($false, [Threading.EventResetMode]::ManualReset, $names.ready, [ref]$created)
    if (-not $created) { throw 'watchdog_gate_already_exists' }
    # Durable unresolved fact is the last prerequisite before process creation.
    Write-DevelopmentWatchdogIntent -Layout $layout -Intent $intent
    $arguments = '-NoProfile -NonInteractive -File "' + $runPath + '" -DevelopmentRoot "' + $layout.root +
        '" -WatchdogIntentId ' + $intent.id
    $committed = $false
    try {
        $child = Start-Process -FilePath (Join-Path $PSHOME 'pwsh.exe') -ArgumentList $arguments `
            -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru -ErrorAction Stop `
            -RedirectStandardOutput (Join-Path $logs "watchdog-$stamp.stdout.log") `
            -RedirectStandardError (Join-Path $logs "watchdog-$stamp.stderr.log")
        $state.launch_identity = [pscustomobject]@{pid=$child.Id
            started_ticks=$child.StartTime.ToUniversalTime().Ticks.ToString()}
        $state.last_error = $null
        Write-ProductJson -Path $statePath -Value $state
        $intent.launch_identity = $state.launch_identity
        $intent.phase = 'pending'
        Write-DevelopmentWatchdogIntent -Layout $layout -Intent $intent
        $committed = $true
    } catch {
        $state.last_error = $_.Exception.Message
        try { Write-ProductJson -Path $statePath -Value $state } catch { }
        Write-WatchdogResult -Action 'launch_failed' -Detail $state.last_error
    }
    if ($committed) {
        # Release control before the child can claim it. A concurrent stop either
        # writes pause first or sees the claimed handoff as control-busy.
        $control.Dispose(); $control = $null
        [void]$gate.Set()
        $acknowledged = $readyEvent.WaitOne(10000)
        Write-WatchdogResult -Action 'launched' -Detail "gate_acknowledged=$acknowledged"
    }
} finally {
    if ($control) { $control.Dispose() }
    if ($gate) { $gate.Dispose() }
    if ($readyEvent) { $readyEvent.Dispose() }
}
