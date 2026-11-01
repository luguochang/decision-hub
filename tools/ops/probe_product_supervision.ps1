# Isolated process fault rehearsal. Does not touch product processes or data.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\..\infra\dsh\product-processes.ps1')
$entries = [System.Collections.Generic.List[object]]::new()
$owned = [System.Collections.Generic.List[object]]::new()
function New-ProbeChild {
    $process = Start-Process powershell.exe -ArgumentList @('-NoProfile', '-Command', 'Start-Sleep -Seconds 90') -WindowStyle Hidden -PassThru
    $identity = [pscustomobject]@{ pid=$process.Id; started_ticks=$process.StartTime.ToUniversalTime().Ticks.ToString() }
    $owned.Add($identity)
    return $identity
}
try {
    foreach ($name in @('api', 'worker')) {
        $child = New-ProbeChild
        $entries.Add([pscustomobject]@{
            name=$name; pid=$child.pid; started_ticks=$child.started_ticks
            state='running'; restart_count=0; consecutive_restarts=0
            stable_since=[datetime]::UtcNow.ToString('o'); last_error=$null
            last_failure_at=$null; next_retry_at=$null
        })
    }
    $api = $entries[0]
    $worker = $entries[1]
    $apiPid = $api.pid
    $workerPid = $worker.pid
    # Recycled-PID protection: a matching PID with a different stamp is not owned.
    Stop-ProductProcessTree -Entry ([pscustomobject]@{pid=$api.pid; started_ticks='0'}) | Out-Null
    if (-not (Test-ProductProcessIdentity $api)) { throw 'identity guard killed unrelated process' }
    Stop-ProductProcessTree -Entry $worker | Out-Null
    $now = [datetime]::UtcNow
    $restart = {
        param($entry)
        $child = New-ProbeChild
        $entry.pid = $child.pid
        $entry.started_ticks = $child.started_ticks
    }
    Invoke-ProductSupervision -Entries $entries -Restart $restart -Now $now
    if ($worker.restart_count -ne 0) { throw 'backoff was skipped' }
    Invoke-ProductSupervision -Entries $entries -Restart $restart -Now $now.AddSeconds(3)
    if ($worker.pid -eq $workerPid -or $worker.restart_count -ne 1) { throw 'worker not restarted' }
    if ($api.pid -ne $apiPid -or -not (Test-ProductProcessIdentity $api)) { throw 'api interrupted' }
    Stop-ProductProcessTree -Entry $worker | Out-Null
    $worker.state = 'retry_wait'
    $worker.next_retry_at = $now.ToString('o')
    foreach ($i in 1..5) {
        Invoke-ProductSupervision -Entries $entries -Restart { throw 'injected startup failure' } -Now $now.AddSeconds(31 * $i)
    }
    if ($worker.state -ne 'cooldown') { throw 'restart budget not bounded' }
    if (-not (Test-ProductProcessIdentity $api)) { throw 'retry failures interrupted API' }
    Write-Output '{"identity_guard":true,"isolated_restart":true,"backoff":true,"bounded_failures":true}'
} finally {
    foreach ($entry in $owned) { Stop-ProductProcessTree -Entry $entry | Out-Null }
}
