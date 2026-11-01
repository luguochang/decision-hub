# Shared ownership checks: PIDs alone are never sufficient to stop a process.
function Get-ProductProcessStamp {
    param([int]$ProcessId)
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($process) { return $process.StartTime.ToUniversalTime().Ticks.ToString() }
    return $null
}

function Test-ProductProcessIdentity {
    param($Entry)
    if (-not $Entry -or -not $Entry.started_ticks) { return $false }
    return (Get-ProductProcessStamp -ProcessId $Entry.pid) -eq [string]$Entry.started_ticks
}

function Get-ProductProcessDescendants {
    param($Entry)
    if (-not (Test-ProductProcessIdentity $Entry)) { return @() }
    try {
        $all = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    } catch {
        # CIM can be denied while local process ownership remains readable.
        # Parent is available in PowerShell 7; older runtimes retain the exact
        # recorded children below rather than replacing them with an empty list.
        $all = @(foreach ($process in Get-Process -ErrorAction SilentlyContinue) {
            try {
                $created = $process.StartTime.ToUniversalTime()
                $parent = $process.Parent
                if ($parent) {
                    [pscustomobject]@{
                        ProcessId = $process.Id
                        ParentProcessId = $parent.Id
                        CreationDate = $created
                        started_ticks = $created.Ticks.ToString()
                    }
                }
            } catch { continue }
        })
    }
    $descendants = [System.Collections.Generic.List[object]]::new()
    $seen = @{}
    function Add-ProductDescendants {
        param($ParentEntry)
        if (-not (Test-ProductProcessIdentity $ParentEntry)) { return }
        foreach ($child in $all | Where-Object ParentProcessId -eq $ParentEntry.pid) {
            # A wrapper which exits during capture may have a recycled PID.
            if ($child.CreationDate.ToUniversalTime().Ticks -lt [long]$ParentEntry.started_ticks) { continue }
            $stamp = Get-ProductProcessStamp -ProcessId $child.ProcessId
            # The fallback inventory binds Parent to this exact process lifetime.
            if ($child.started_ticks -and $stamp -ne [string]$child.started_ticks) { continue }
            $identity = "$($child.ProcessId):$stamp"
            if ($stamp -and -not $seen.ContainsKey($identity)) {
                $seen[$identity] = $true
                $childEntry = [pscustomobject]@{ pid = $child.ProcessId; started_ticks = $stamp }
                $descendants.Add($childEntry)
                Add-ProductDescendants -ParentEntry $childEntry
            }
        }
    }
    $seen["$($Entry.pid):$($Entry.started_ticks)"] = $true
    Add-ProductDescendants -ParentEntry $Entry
    # A wrapper may already have exited or a process may be inaccessible to
    # discovery. Exact saved identities remain owned, including orphaned leaves.
    foreach ($child in $Entry.children) {
        $identity = "$($child.pid):$($child.started_ticks)"
        if (-not $seen.ContainsKey($identity) -and (Test-ProductProcessIdentity $child)) {
            $seen[$identity] = $true
            $descendants.Add($child)
        }
    }
    return @($descendants.ToArray())
}

function Stop-ProductProcessTree {
    param($Entry)
    $discovered = if (Test-ProductProcessIdentity $Entry) {
        @(Get-ProductProcessDescendants $Entry)
    } else { @() }
    # The root can exit between identity checks. Always retain saved lifetimes;
    # each child is checked again immediately before stopping it.
    $seen = @{}
    $descendants = @(foreach ($child in @($discovered) + @($Entry.children)) {
        if (-not $child -or $child.pid -eq $Entry.pid) { continue }
        $identity = "$($child.pid):$($child.started_ticks)"
        if (-not $seen.ContainsKey($identity)) {
            $seen[$identity] = $true
            $child
        }
    })
    # Stop the verified root first so its wrapper cannot replace killed leaves.
    if (Test-ProductProcessIdentity $Entry) {
        Stop-Process -Id $Entry.pid -Force -ErrorAction SilentlyContinue
    }
    foreach ($child in $descendants) {
        if (Test-ProductProcessIdentity $child) {
            Stop-Process -Id $child.pid -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Milliseconds 200
    return @(@($Entry) + @($descendants) | Where-Object { Test-ProductProcessIdentity $_ })
}

function Write-ProductJson {
    param([string]$Path, $Value)
    $temporary = "$Path.$PID.tmp"
    $json = $Value | ConvertTo-Json -Depth 6 -ErrorAction Stop
    Set-Content -LiteralPath $temporary -Value $json -Encoding utf8 -ErrorAction Stop
    Move-Item -LiteralPath $temporary -Destination $Path -Force -ErrorAction Stop
}

function Test-ProductStateStorageError {
    param($ErrorRecord)
    $exception = $ErrorRecord.Exception
    while ($exception) {
        if ($exception -is [System.IO.IOException] -or
            $exception -is [System.UnauthorizedAccessException]) { return $true }
        $exception = $exception.InnerException
    }
    return $false
}

function Set-ProductCheckpointFailure {
    param($State, [string]$Path, $ErrorRecord, [datetime]$Now = [datetime]::UtcNow)
    $State.degraded = $true
    $State.failure_count = [Math]::Min(6, $State.failure_count + 1)
    $delay = [Math]::Min(60, [Math]::Pow(2, $State.failure_count))
    $State.next_retry_at = $Now.AddSeconds($delay).ToString('o')
    $State.last_error = 'product_state_storage_unavailable'
    $State.last_failure_at = $Now.ToString('o')
    # Do not serialize environment/launch specs or attempt another file log.
    Write-Warning "Product state storage unavailable: $Path; retry in ${delay}s; new launches paused. $($ErrorRecord.Exception.Message)"
}

function Invoke-ProductStateCheckpoint {
    param([string]$Path, $Value, $State, [datetime]$Now = [datetime]::UtcNow)
    if ($State.degraded -and $State.next_retry_at -and
        $Now -lt ([datetime]$State.next_retry_at).ToUniversalTime()) { return $false }
    try {
        Write-ProductJson -Path $Path -Value $Value
    } catch {
        # This catch covers only the state writer. Code/serialization errors
        # retain the launcher's strict exception and shutdown semantics.
        if (-not (Test-ProductStateStorageError $_)) { throw }
        Set-ProductCheckpointFailure -State $State -Path $Path -ErrorRecord $_ -Now $Now
        return $false
    }
    $State.degraded = $false
    $State.failure_count = 0
    $State.next_retry_at = $null
    return $true
}

function Resolve-ProductPendingBirths {
    param([datetime]$Now = [datetime]::UtcNow, [switch]$Force)
    if (-not $script:productPendingBirths -or $script:productPendingBirths.Count -eq 0) { return $true }
    if (-not $Force -and $script:productPendingBirthRetryAt -and $Now -lt $script:productPendingBirthRetryAt) { return $false }
    foreach ($entry in $script:productPendingBirths.ToArray()) {
        if ($entry.state -eq 'registration_identity_pending') {
            if (-not $script:productBirthProcesses.ContainsKey($entry)) {
                throw 'product_birth_process_reference_missing'
            }
            $process = $script:productBirthProcesses[$entry]
            try {
                $processId = $process.Id
                $stamp = $process.StartTime.ToUniversalTime().Ticks.ToString()
            } catch {
                # Only this original Process's identity access is unknown.
                # It is neither proof of exit nor permission to kill a PID.
                Write-Warning "Newborn identity unavailable: $($entry.name); new launches remain paused."
                continue
            }
            $entry.pid = $processId; $entry.started_ticks = $stamp
            $entry.children = @(Get-ProductProcessDescendants $entry)
            if (-not (Test-ProductProcessIdentity $entry) -and $entry.children.Count -eq 0) {
                Write-Warning "Newborn tree lifetime remains unverified: $($entry.name); new launches remain paused."
                continue
            }
            $entry.state = 'registration_pending'
        }
        $remaining = @(Stop-ProductProcessTree -Entry $entry)
        if ($remaining.Count -eq 0) {
            $script:productPendingBirths.Remove($entry) | Out-Null
            if ($script:productBirthProcesses) { $script:productBirthProcesses.Remove($entry) | Out-Null }
        }
    }
    $script:productPendingBirthRetryAt = $Now.AddSeconds(30)
    return $script:productPendingBirths.Count -eq 0
}

function Test-ProductOwnedListener {
    param($Entry, [int]$Port)
    if ($Port -le 0) { return $false }
    $owned = @{}
    foreach ($identity in @($Entry) + @($Entry.children)) {
        if ($identity -and (Test-ProductProcessIdentity $identity)) {
            $owned[[int]$identity.pid] = $true
        }
    }
    if ($owned.Count -eq 0) { return $false }
    try {
        foreach ($listener in @(Get-NetTCPConnection -State Listen -LocalPort $Port `
                -ErrorAction Stop)) {
            if ($owned.ContainsKey([int]$listener.OwningProcess)) { return $true }
        }
    } catch {
        # An unavailable listener inventory is unknown, never evidence that a
        # still-owned wrapper is serving its declared product port.
    }
    return $false
}

function Test-ProductServiceHealth {
    param($Entry)
    $probeProperty = $Entry.PSObject.Properties['health_probe']
    if (-not $probeProperty -or -not $probeProperty.Value) { return $true }
    $probe = $probeProperty.Value
    if (-not (Test-ProductOwnedListener -Entry $Entry -Port ([int]$probe.port))) {
        return $false
    }
    if (-not $probe.url) { return $true }
    try {
        # Probe a dedicated liveness route, never a report/research route. A
        # long asynchronous business operation therefore remains independent
        # from service ownership supervision.
        $response = Invoke-WebRequest -UseBasicParsing -Method Get -Uri ([string]$probe.url) `
            -TimeoutSec 1 -ErrorAction Stop
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 300
    } catch {
        return $false
    }
}

function Invoke-ProductSupervision {
    param(
        $Entries,
        [scriptblock]$Restart,
        [datetime]$Now = [datetime]::UtcNow,
        [scriptblock]$CanRestart = $null,
        [scriptblock]$HealthCheck = $null
    )
    foreach ($entry in $Entries.ToArray()) {
        if (Test-ProductProcessIdentity $entry) {
            $entry | Add-Member -NotePropertyName children -NotePropertyValue @(Get-ProductProcessDescendants $entry) -Force
            $probeProperty = $entry.PSObject.Properties['health_probe']
            $probe = if ($probeProperty) { $probeProperty.Value } else { $null }
            $healthOk = if ($HealthCheck) {
                [bool](& $HealthCheck $entry)
            } else {
                Test-ProductServiceHealth -Entry $entry
            }
            if ($probe -and -not $healthOk) {
                $graceSeconds = if ($probe.startup_grace_seconds) {
                    [int]$probe.startup_grace_seconds
                } else { 0 }
                $withinStartupGrace = $graceSeconds -gt 0 -and
                    ($Now - ([datetime]$entry.stable_since).ToUniversalTime()).TotalSeconds -lt $graceSeconds
                if ($withinStartupGrace) {
                    $entry | Add-Member -NotePropertyName health_status `
                        -NotePropertyValue 'starting' -Force
                    continue
                }
                $failureProperty = $entry.PSObject.Properties['health_failure_count']
                $failureCount = if ($failureProperty) { [int]$failureProperty.Value } else { 0 }
                $failureCount = [Math]::Min(3, $failureCount + 1)
                $entry | Add-Member -NotePropertyName health_failure_count `
                    -NotePropertyValue $failureCount -Force
                $entry | Add-Member -NotePropertyName health_status `
                    -NotePropertyValue 'unhealthy' -Force
                $entry | Add-Member -NotePropertyName last_health_failure_at `
                    -NotePropertyValue $Now.ToString('o') -Force
                # A single missed probe never kills a service. State/checkpoint
                # outages also retain the current exact tree without births.
                if ($failureCount -lt 3) { continue }
                $entry.last_error = 'managed_service_unhealthy'
                $entry.last_failure_at = $Now.ToString('o')
                if ($CanRestart -and -not (& $CanRestart)) { continue }
                $remaining = @(Stop-ProductProcessTree -Entry $entry)
                $entry | Add-Member -NotePropertyName cleanup_pending `
                    -NotePropertyValue ($remaining.Count -gt 0) -Force
                $entry.state = 'retry_wait'
                $entry.next_retry_at = $Now.AddSeconds(2).ToString('o')
                # Never launch a replacement in the same tick which stopped
                # the prior exact identity. This prevents overlapping generations.
                continue
            }
            $entry | Add-Member -NotePropertyName health_failure_count `
                -NotePropertyValue 0 -Force
            $entry | Add-Member -NotePropertyName health_status `
                -NotePropertyValue 'healthy' -Force
            if ($entry.state -ne 'running') { $entry.state = 'running' }
            # Healthy five-minute intervals replenish a bounded retry budget.
            if (($Now - ([datetime]$entry.stable_since).ToUniversalTime()).TotalSeconds -ge 300) {
                $entry.consecutive_restarts = 0
            }
            continue
        }
        # A checkpoint outage must not admit more processes or clear retained
        # leaves. Live identities above can still be refreshed in memory.
        if ($CanRestart -and -not (& $CanRestart)) { continue }
        if ($entry.state -eq 'running') {
            Stop-ProductProcessTree -Entry $entry | Out-Null
            $entry.state = 'retry_wait'
            $entry.last_error = 'managed_process_exited'
            $entry.last_failure_at = $Now.ToString('o')
            $delay = [Math]::Min(60, [Math]::Pow(2, $entry.consecutive_restarts + 1))
            $entry.next_retry_at = $Now.AddSeconds($delay).ToString('o')
        }
        if ($entry.next_retry_at -and $Now -lt ([datetime]$entry.next_retry_at).ToUniversalTime()) { continue }
        $cleanupProperty = $entry.PSObject.Properties['cleanup_pending']
        if ($cleanupProperty -and $cleanupProperty.Value) {
            $remaining = @(Stop-ProductProcessTree -Entry $entry)
            if ($remaining.Count -gt 0) {
                $entry.next_retry_at = $Now.AddSeconds(2).ToString('o')
                continue
            }
            $entry.cleanup_pending = $false
        }
        if ($entry.consecutive_restarts -ge 5) {
            $entry.state = 'cooldown'
            $entry.next_retry_at = $Now.AddMinutes(5).ToString('o')
            $entry.consecutive_restarts = 0
            continue
        }
        $entry.restart_count += 1
        $entry.consecutive_restarts += 1
        try {
            & $Restart $entry | Out-Null
            $entry.state = 'running'
            $entry.stable_since = $Now.ToString('o')
            $entry.next_retry_at = $null
            $entry | Add-Member -NotePropertyName health_failure_count `
                -NotePropertyValue 0 -Force
            $entry | Add-Member -NotePropertyName health_status `
                -NotePropertyValue 'starting' -Force
            $entry | Add-Member -NotePropertyName cleanup_pending `
                -NotePropertyValue $false -Force
        } catch {
            # An unrelated API/worker stays alive; logs retain the exact failure.
            $entry.state = 'retry_wait'
            $entry.last_error = 'managed_process_restart_failed'
            $entry.last_failure_at = $Now.ToString('o')
            $entry.next_retry_at = $Now.AddSeconds(30).ToString('o')
        }
    }
}
