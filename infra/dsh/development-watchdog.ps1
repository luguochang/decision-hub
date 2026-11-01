# One optional launch handoff for the existing DEV lifecycle, never a supervisor.
function Get-DevelopmentWatchdogEventNames {
    param($Layout, [string]$IntentId)
    if ($IntentId -cnotmatch '^[a-f0-9]{32}$') { throw 'watchdog_intent_invalid' }
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $hash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData(
        [Text.Encoding]::UTF8.GetBytes($Layout.root.ToLowerInvariant()))).Substring(0,16)
    $prefix = "Local\DH-DEV-watchdog-$sid-$hash-$IntentId"
    @{gate="$prefix-gate";ready="$prefix-ready"}
}

function Write-DevelopmentWatchdogIntent {
    param($Layout, $Intent)
    $path = Join-Path $Layout.runtime 'watchdog-launch-intent.json'
    $temporary = "$path.$([guid]::NewGuid().ToString('N')).tmp"
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($Intent | ConvertTo-Json -Depth 8))
    $stream = [IO.File]::Open($temporary, [IO.FileMode]::CreateNew,
        [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) }
    finally { $stream.Dispose() }
    [IO.File]::Move($temporary, $path, $true)
}

function Read-DevelopmentWatchdogIntent {
    param($Layout)
    $path = Join-Path $Layout.runtime 'watchdog-launch-intent.json'
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $intent = Get-Content -LiteralPath $path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    if ($intent.schema -cne 'development-watchdog-intent.v1' -or $intent.root -ine $Layout.root -or
        $intent.id -cnotmatch '^[a-f0-9]{32}$' -or
        $intent.phase -notin @('unresolved','pending','claimed','handed_off')) { throw 'watchdog_intent_invalid' }
    $identities = @()
    if ($intent.phase -ne 'unresolved') { $identities += $intent.launch_identity }
    if ($intent.phase -in @('claimed','handed_off')) { $identities += $intent.supervisor_identity }
    foreach ($identity in $identities) {
        if (-not $identity -or ($identity.pid -isnot [int] -and $identity.pid -isnot [long]) -or
            $identity.pid -le 0 -or [string]$identity.started_ticks -notmatch '^[0-9]+$') {
            throw 'watchdog_intent_identity_invalid'
        }
    }
    if ($intent.phase -eq 'handed_off' -and -not $intent.launch_id) { throw 'watchdog_intent_invalid' }
    return $intent
}

function Get-DevelopmentWatchdogIntentGuard {
    param($Intent)
    if (-not $Intent) { return 'clear' }
    if ($Intent.phase -eq 'unresolved') { return 'blocked' }
    $identities = @($Intent.launch_identity)
    if ($Intent.phase -in @('claimed','handed_off')) { $identities += $Intent.supervisor_identity }
    $states = @($identities | ForEach-Object { (Get-DevelopmentLifetime $_).state })
    if ('unknown' -in $states) { return 'blocked' }
    if ('alive' -in $states) { return 'pending' }
    # A pending/claimed launch without a durable handoff is unresolved even when
    # its known processes later exit; Resume never converts it into permission.
    if ($Intent.phase -ne 'handed_off') { return 'blocked' }
    return 'clear'
}

function Get-DevelopmentWatchdogStopGuard {
    param($Layout)
    try { $intent = Read-DevelopmentWatchdogIntent $Layout }
    catch { throw 'development_stop_unconfirmed_watchdog_intent' }
    if (-not $intent) {
        $legacyPath = Join-Path $Layout.runtime 'watchdog-state.json'
        if (Test-Path -LiteralPath $legacyPath) {
            try {
                $legacy = Get-Content -LiteralPath $legacyPath -Raw | ConvertFrom-Json -ErrorAction Stop
                if ($legacy.schema -cne 'development-watchdog-state.v1' -or
                    ($legacy.attempts -gt 0 -and -not $legacy.launch_identity) -or
                    ($legacy.launch_identity -and
                        (Get-DevelopmentLifetime $legacy.launch_identity).state -in @('alive','unknown'))) {
                    throw 'legacy_pending'
                }
            } catch { throw 'development_stop_unconfirmed_watchdog_legacy_pending' }
        }
        return @()
    }
    if ($intent.phase -eq 'unresolved') { throw 'development_stop_unconfirmed_watchdog_intent' }
    $identities = @($intent.launch_identity)
    if ($intent.phase -in @('claimed','handed_off')) { $identities += $intent.supervisor_identity }
    if (@($identities | Where-Object { (Get-DevelopmentLifetime $_).state -eq 'unknown' }).Count) {
        throw 'development_stop_unconfirmed_watchdog_intent'
    }
    $live = @($identities | Where-Object { (Get-DevelopmentLifetime $_).state -eq 'alive' })
    if (-not $live.Count) { return @() }
    if ($intent.phase -ne 'handed_off') { throw 'development_stop_unconfirmed_watchdog_pending' }
    try {
        $saved = Get-Content -LiteralPath (Join-Path $Layout.runtime 'supervisor.json') -Raw | ConvertFrom-Json
        if ($saved.pid -ne $intent.supervisor_identity.pid -or
            [string]$saved.started_ticks -cne [string]$intent.supervisor_identity.started_ticks -or
            $saved.launch_id -cne $intent.launch_id -or
            (Get-DevelopmentLifetime $intent.supervisor_identity).state -ne 'alive') { throw 'mismatch' }
    } catch { throw 'development_stop_unconfirmed_watchdog_pending' }
    # Only an absence guard; this saved wrapper identity never becomes a kill target.
    return @($intent.launch_identity)
}

function Enter-DevelopmentWatchdogLaunch {
    param($Layout, [string]$IntentId)
    $names = Get-DevelopmentWatchdogEventNames -Layout $Layout -IntentId $IntentId
    $gate = $null; $ready = $null; $control = $null
    try {
        $gate = [Threading.EventWaitHandle]::OpenExisting($names.gate)
        $ready = [Threading.EventWaitHandle]::OpenExisting($names.ready)
        [void]$ready.Set()
        if (-not $gate.WaitOne(15000)) { throw 'watchdog_gate_timeout' }
        # No retry/wait on either lock. Both launch and stop take control first.
        try {
            $control = [IO.File]::Open((Join-Path $Layout.runtime 'watchdog.lock'),
                [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        } catch { throw 'watchdog_handoff_control_busy' }
        if (Test-Path -LiteralPath (Join-Path $Layout.runtime 'watchdog.pause')) { throw 'watchdog_launch_paused' }
        $intent = Read-DevelopmentWatchdogIntent $Layout
        $self = Get-Process -Id $PID -ErrorAction Stop
        if (-not $intent -or $intent.id -cne $IntentId -or $intent.phase -cne 'pending' -or
            $self.Parent.Id -ne $intent.launch_identity.pid -or
            (Get-DevelopmentLifetime $intent.launch_identity).state -ne 'alive') {
            throw 'watchdog_handoff_parent_unverified'
        }
        $intent.phase = 'claimed'
        $intent.supervisor_identity = [pscustomobject]@{pid=$PID
            started_ticks=$self.StartTime.ToUniversalTime().Ticks.ToString()}
        Write-DevelopmentWatchdogIntent -Layout $Layout -Intent $intent
        $result = [pscustomobject]@{control=$control;intent=$intent}
        $control = $null
        return $result
    } finally {
        if ($control) { $control.Dispose() }
        if ($gate) { $gate.Dispose() }
        if ($ready) { $ready.Dispose() }
    }
}

function Complete-DevelopmentWatchdogLaunch {
    param($Layout, $Handoff, $Supervisor)
    try {
        if ($Supervisor.pid -ne $Handoff.intent.supervisor_identity.pid -or
            [string]$Supervisor.started_ticks -cne [string]$Handoff.intent.supervisor_identity.started_ticks) {
            throw 'watchdog_handoff_supervisor_unverified'
        }
        $Handoff.intent.phase = 'handed_off'
        $Handoff.intent.supervisor_identity = [pscustomobject]@{pid=$Supervisor.pid
            started_ticks=[string]$Supervisor.started_ticks}
        $Handoff.intent.launch_id = $Supervisor.launch_id
        Write-DevelopmentWatchdogIntent -Layout $Layout -Intent $Handoff.intent
    } finally { $Handoff.control.Dispose() }
}
