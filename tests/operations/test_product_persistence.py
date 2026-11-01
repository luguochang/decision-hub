"""Actual PowerShell AST/functions with mock processes, storage and clock.

Never launch or stop an OS process, contact HTTP, or open a product database.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = r'''param($Repo, $Scenario, $TestDir, $HelperPath, $LauncherPath)
$ErrorActionPreference = 'Stop'
if (-not $HelperPath) { $HelperPath=Join-Path $Repo 'infra/dsh/product-processes.ps1' }
if (-not $LauncherPath) { $LauncherPath=Join-Path $Repo 'infra/dsh/run-product.ps1' }
. $HelperPath
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $LauncherPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'launcher parse failed' }
$definition = $ast.Find({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Start-ProductProcess'
}, $true)
. ([scriptblock]::Create($definition.Extent.Text))
$script:ProcessTable = @{}; $script:Stopped = @(); $script:SpawnCount = 0
$script:Writes = 0; $script:FailWrites = 1; $script:RefuseStop = 0
$script:Saved = $null; $script:WriteFailure = 'io'
$script:FailBirthStamp=$false;$script:BirthStampReads=0
$created = ([datetime]'2026-10-01T00:00:00Z').ToUniversalTime()
function Register-Process([int]$Id, [int]$ParentId, [int]$Age) {
    $process = [pscustomobject]@{ Id=$Id; StartTime=$created.AddSeconds($Age)
        Parent=if ($ParentId) { [pscustomobject]@{Id=$ParentId} } else {$null} }
    $script:ProcessTable[$Id] = $process
    return $process
}
function Get-Process {
    [CmdletBinding()]param([int]$Id)
    if ($PSBoundParameters.ContainsKey('Id')) { return $script:ProcessTable[$Id] }
    return $script:ProcessTable.Values
}
function Get-CimInstance { [CmdletBinding()]param([string]$ClassName); throw `
    'mock CIM unavailable' }
function Stop-Process {
    [CmdletBinding()]param([int]$Id,[switch]$Force)
    $script:Stopped += $Id
    if ($Id -ne $script:RefuseStop) { $script:ProcessTable.Remove($Id) }
}
function Start-Process {
    [CmdletBinding()]param($FilePath,$WorkingDirectory,$ArgumentList,$RedirectStandardOutput,
        $RedirectStandardError,$WindowStyle,[switch]$PassThru)
    $script:SpawnCount += 1
    $id = 1100 + 2 * $script:SpawnCount
    $process = Register-Process $id 0 (10 + $script:SpawnCount)
    Register-Process ($id+1) $id (11 + $script:SpawnCount) | Out-Null
    if ($script:FailBirthStamp) {
        $returned=[pscustomobject]@{Id=$id;birth_start=$process.StartTime}
        $returned | Add-Member ScriptProperty StartTime {
            $script:BirthStampReads+=1
            if ($script:BirthStampReads -eq 1) { throw 'injected birth stamp access failure' }
            return $this.birth_start
        }
        $script:ReturnedBirth=$returned
        return $returned
    }
    return $process
}
function Write-ProductJson {
    param([string]$Path,$Value)
    $script:Writes += 1
    if ($script:Writes -le $script:FailWrites) {
        if ($script:WriteFailure -eq 'unknown') { throw `
            [InvalidOperationException]::new('injected code error') }
        throw [System.IO.IOException]::new('injected state storage failure')
    }
    $script:Saved = $Value | ConvertTo-Json -Depth 6
}
function Start-Sleep { param([int]$Milliseconds,[int]$Seconds) }
function Entry([string]$Name,[int]$Id) {
    $process = Register-Process $Id 0 0
    return [pscustomobject]@{ name=$Name;pid=$Id;started_ticks=$process.StartTime.Ticks.ToString()
        children=@();marker='mock';stdout='mock';stderr='mock';state='running'
        restart_count=0;consecutive_restarts=0;stable_since=$created.ToString('o')
        last_error=$null;last_failure_at=$null;next_retry_at=$null }
}
$api = Entry api 1001; $worker = Entry worker 1002
$started = [System.Collections.Generic.List[object]]::new(); $started.Add($api); `
    $started.Add($worker)
$checkpointState = [pscustomobject]@{ degraded=$false;failure_count=0;next_retry_at=$null
    last_error=$null;last_failure_at=$null }
$script:productPendingBirths = [System.Collections.Generic.List[object]]::new()
$repoRoot=$TestDir;$logDir=$TestDir;$launchSpecs=@{}
$stateFile=Join-Path $TestDir 'state.json';$supervisorFile=Join-Path $TestDir 'supervisor.json'
$stopRequest=Join-Path $TestDir 'stop.request'
switch ($Scenario) {
    {$_ -in @('runtime_ast_failure', 'runtime_ast_unknown')} {
        if ($Scenario -eq 'runtime_ast_unknown') { $script:WriteFailure='unknown' }
        $script:Turn=0;$script:Continued=$false;$script:Preserved=$false
        function Start-Sleep { param([int]$Milliseconds,[int]$Seconds)
            if ($Seconds) { $script:Turn += 1 }
        }
        function Test-Path { param($LiteralPath)
            if ($script:Turn -ge 2) {
                $script:Continued=$true
                $script:Preserved=(Test-ProductProcessIdentity $api) -and
                    (Test-ProductProcessIdentity $worker) -and $script:Stopped.Count -eq 0
                return $true
            }
            return $false
        }
        $supervisorEntry=[pscustomobject]@{state='running'}
        $ownership=[pscustomobject]@{};$ownership | Add-Member ScriptMethod Dispose {}
        $loop=$ast.Find({param($node) $node -is `
            [System.Management.Automation.Language.WhileStatementAst] -and
            $node.Extent.Text -match 'Invoke-ProductSupervision'},$true)
        $outer=$ast.Find({param($node) $node -is `
            [System.Management.Automation.Language.TryStatementAst] -and
            $node.Body.Extent.Text -match 'Keep report access'},$true)
        $code='try {'+$loop.Extent.Text+'} '+$outer.CatchClauses[0].Extent.Text+
            ' finally '+$outer.Finally.Extent.Text
        try { . ([scriptblock]::Create($code)) } catch { $caught=$_.Exception.Message }
        if ($Scenario -eq 'runtime_ast_unknown') {
            if ($caught -notmatch 'injected code error' -or $script:Continued) { throw `
                'unknown runtime fault was swallowed' }
        } elseif (-not $script:Continued -or -not $script:Preserved) {
            throw 'runtime storage failure shut down already-owned roles before intentional stop'
        }
        if (Test-ProductProcessIdentity $api) { throw `
            'intentional stop no longer cleans up owned API' }
    }
    'birth_failure_preserves_previous' {
        $before=$worker | ConvertTo-Json -Depth 6
        try { Start-ProductProcess -Name worker -FilePath mock -ArgumentList @() -Environment `
            @{} -Marker mock -ExistingEntry $worker | Out-Null } catch {}
        if (($worker|ConvertTo-Json -Depth 6) -ne $before) { throw `
            'birth persistence failure erased the previous identity' }
        if ($script:Stopped -notcontains 1102 -or $script:Stopped -notcontains 1103) { throw `
            'unregistered newborn tree remained alive' }
        if (-not(Test-ProductProcessIdentity $api) -or -not(Test-ProductProcessIdentity `
            $worker)) { throw 'newborn failure killed older roles' }
        if ($script:productPendingBirths.Count) { throw `
            'verified exited newborn still blocked registration' }
    }
    'checkpoint_backoff_latest_then_resume' {
        $now=$created.AddHours(1)
        if (Invoke-ProductStateCheckpoint -Path $stateFile -Value $started.ToArray() -State `
            $checkpointState -Now $now) { throw 'failed checkpoint claimed saved' }
        if (-not $checkpointState.degraded) { throw 'failed checkpoint not marked degraded' }
        $script:ProcessTable.Remove(1002)
        $script:Restarts=0
        $restart={param($entry)$script:Restarts+=1; $entry.pid=1200
            $new=Register-Process 1200 0 30;$entry.started_ticks=$new.StartTime.Ticks.ToString() }
        Invoke-ProductSupervision -Entries $started -Restart $restart -CanRestart { -not `
            $checkpointState.degraded } -Now $now
        if ($script:Restarts -or $script:Stopped.Count) { throw `
            'checkpoint outage admitted a restart or stopped a retained role' }
        if (Invoke-ProductStateCheckpoint -Path $stateFile -Value $started.ToArray() -State `
            $checkpointState -Now $now.AddSeconds(1)) { throw 'backoff skipped' }
        if ($script:Writes -ne 1) { throw 'storage retried during backoff' }
        $api.children=@([pscustomobject]@{pid=1300;started_ticks='latest-stamp'})
        if (-not(Invoke-ProductStateCheckpoint -Path $stateFile -Value $started.ToArray() -State `
            $checkpointState -Now $now.AddSeconds(2))) { throw 'storage recovery not saved' }
        if ($script:Saved -notmatch 'latest-stamp') { throw 'recovery saved stale identities' }
        if ($checkpointState.degraded) { throw 'recovered checkpoint remained blocked' }
        $worker.state='retry_wait';$worker.next_retry_at=$null
        Invoke-ProductSupervision -Entries $started -Restart $restart -CanRestart { -not `
            $checkpointState.degraded } -Now $now.AddSeconds(3)
        if ($script:Restarts -ne 1) { throw 'saved checkpoint did not resume role recovery once' }
    }
    'unknown_checkpoint_error' {
        $script:WriteFailure='unknown';$thrown=$false
        try { Invoke-ProductStateCheckpoint -Path $stateFile -Value @() -State $checkpointState `
            | Out-Null } catch {
            $thrown=$_.Exception.Message -match 'injected code error'
        }
        if (-not $thrown -or $checkpointState.degraded) { throw `
            'unknown code failure swallowed as storage outage' }
    }
    'birth_survivor_blocks_duplicate' {
        $script:RefuseStop=1103
        try { Start-ProductProcess -Name worker -FilePath mock -ArgumentList @() -Environment `
            @{} -Marker mock -ExistingEntry $worker | Out-Null } catch {}
        if ($script:productPendingBirths.Count -ne 1 -or -not $script:ProcessTable[1103]) { `
            throw 'unconfirmed newborn child identity lost' }
        try { Start-ProductProcess -Name worker -FilePath mock -ArgumentList @() -Environment `
            @{} -Marker mock -ExistingEntry $worker | Out-Null } catch {}
        if ($script:SpawnCount -ne 1) { throw `
            'unconfirmed newborn admitted a duplicate Start-Process' }
        $script:RefuseStop=0
        if (-not(Resolve-ProductPendingBirths)) { throw `
            'confirmed newborn exit did not release registration block' }
        if ($script:productPendingBirths.Count -or $script:ProcessTable[1103]) { throw `
            'newborn survivor still remained' }
        if (-not(Test-ProductProcessIdentity $api) -or -not(Test-ProductProcessIdentity `
            $worker)) { throw 'survivor cleanup damaged older identities' }
    }
    'startup_registration_strict' {
        $thrown=$false
        try { Start-ProductProcess -Name new-role -FilePath mock -ArgumentList @() -Environment `
            @{} -Marker mock | Out-Null } catch { $thrown=$true }
        if (-not $thrown -or $started.Count -ne 2) { throw `
            'startup storage failure silently admitted a role' }
        if ($script:ProcessTable[1102] -or $script:ProcessTable[1103]) { throw `
            'startup newborn was not safely stopped' }
    }
    'runtime_restart_birth_failure' {
        $script:ProcessTable.Remove(1002);$worker.state='retry_wait'
        $other=Entry other 1003;$script:ProcessTable.Remove(1003);$other.state='retry_wait'; `
            $started.Add($other)
        $priorStamp=$worker.started_ticks
        $restart={param($entry) Start-ProductProcess -Name $entry.name -FilePath mock `
            -ArgumentList @() -Environment @{} -Marker mock -ExistingEntry $entry}
        $now=[datetime]::UtcNow
        Invoke-ProductSupervision -Entries $started -Restart $restart -Now $now -CanRestart { `
            -not $checkpointState.degraded -and $script:productPendingBirths.Count -eq 0 }
        if ($script:SpawnCount -ne 1 -or -not $checkpointState.degraded) { throw `
            'birth checkpoint did not immediately block later roles' }
        if ($worker.pid -ne 1002 -or $worker.started_ticks -ne $priorStamp) { throw `
            'failed restart erased previous identity' }
        if ($script:ProcessTable[1102] -or $script:ProcessTable[1103] -or `
            -not(Test-ProductProcessIdentity $api)) { throw `
            'failed restart did not isolate newborn cleanup' }
        Invoke-ProductSupervision -Entries $started -Restart $restart -Now $now.AddSeconds(30) `
            -CanRestart { -not $checkpointState.degraded -and $script:productPendingBirths.Count `
            -eq 0 }
        if ($script:SpawnCount -ne 1) { throw `
            'restart repeated before ownership checkpoint recovered' }
        if (-not(Invoke-ProductStateCheckpoint -Path $stateFile -Value $started.ToArray() -State `
            $checkpointState -Now $now.AddSeconds(64))) { throw `
            'ownership checkpoint did not recover' }
        if ($script:Saved -notmatch '1002' -or $script:Saved -match '1102') { throw `
            'recovery checkpoint lost prior identity or claimed dead newborn' }
        Invoke-ProductSupervision -Entries $started -Restart $restart -Now $now.AddSeconds(65) `
            -CanRestart { -not $checkpointState.degraded -and $script:productPendingBirths.Count `
            -eq 0 }
        if ($script:SpawnCount -ne 3) { throw `
            'roles did not resume after successful ownership persistence' }
        if ($script:Stopped -contains 1001) { throw 'API interrupted during restart recovery' }
    }
    'checkpoint_bounded_delays' {
        $script:FailWrites=99;$now=$created.AddHours(1)
        foreach ($delay in @(2,4,8,16,32,60,60)) {
            if (Invoke-ProductStateCheckpoint -Path $stateFile -Value @() -State `
                $checkpointState -Now $now) { throw 'storage failure claimed success' }
            $next=([datetime]$checkpointState.next_retry_at).ToUniversalTime()
            if (($next-$now).TotalSeconds -ne $delay) { throw `
                'checkpoint retry delay unbounded or wrong' }
            $now=$next
        }
        if ($script:Writes -ne 7 -or $checkpointState.failure_count -ne 6) { throw `
            'checkpoint retry budget grew without bound' }
    }
    'storage_error_categories' {
        $helperAst=[System.Management.Automation.Language.Parser]::ParseFile($HelperPath, `
            [ref]$tokens,[ref]$parseErrors)
        $writer=$helperAst.Find({param($node) $node -is `
            [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq `
            'Write-ProductJson'},$true)
        . ([scriptblock]::Create($writer.Extent.Text))
        function Set-Content {
            [CmdletBinding()]param($LiteralPath,$Value,$Encoding)
            if ($script:WriteFailure -eq 'io') { throw `
                [System.IO.IOException]::new('injected writer IO') }
            if ($script:WriteFailure -eq 'access') { throw `
                [UnauthorizedAccessException]::new('injected writer access') }
            throw [InvalidOperationException]::new('injected writer code error')
        }
        function Move-Item { [CmdletBinding()]param($LiteralPath,$Destination,[switch]$Force); `
            throw 'Move must not run after failed temp write' }
        foreach ($mode in @('io','access','unknown')) {
            $script:WriteFailure=$mode
            $checkpointState.degraded=$false;$checkpointState.failure_count=0; `
                $checkpointState.next_retry_at=$null
            $thrown=$false
            try { $saved=Invoke-ProductStateCheckpoint -Path $stateFile -Value @() -State `
                $checkpointState } catch { $thrown=$true }
            if ($mode -eq 'unknown') {
                if (-not $thrown -or $checkpointState.degraded) { throw `
                    'writer code error treated as IO' }
            } elseif ($thrown -or $saved -or -not $checkpointState.degraded) { throw `
                'writer storage category not isolated' }
        }
    }
    'birth_success_publishes_once' {
        $script:FailWrites=0
        Remove-Item Env:DSH_PERSISTENCE_TEST -ErrorAction SilentlyContinue
        Start-ProductProcess -Name worker -FilePath mock -ArgumentList @() -Environment `
            @{DSH_PERSISTENCE_TEST='child-only'} -Marker mock -ExistingEntry $worker | Out-Null
        if ($worker.pid -ne 1102 -or $worker.children.pid -notcontains 1103) { throw `
            'successful new identity or child not published' }
        $saved=$script:Saved|ConvertFrom-Json
        if (@($saved|Where-Object name -eq worker).Count -ne 1 -or $saved.Count -ne 2) { throw `
            'success duplicated role registration' }
        if ($script:productPendingBirths.Count -or $script:Stopped.Count) { throw `
            'successful birth kept provisional ownership or stopped a role' }
        if ([Environment]::GetEnvironmentVariable('DSH_PERSISTENCE_TEST','Process')) { throw `
            'child environment leaked after registration' }
        if (-not(Test-ProductProcessIdentity $api)) { throw `
            'successful role replacement affected API' }
    }
    {$_ -in @('postspawn_stamp_failure', 'postspawn_restore_failure')} {
        $script:FailWrites=0
        if ($Scenario -eq 'postspawn_stamp_failure') { $script:FailBirthStamp=$true }
        else {
            function Remove-Item { [CmdletBinding()]param($LiteralPath)
                throw 'injected postspawn environment restore failure'
            }
        }
        $script:ProcessTable.Remove(1002);$worker.state='retry_wait'
        $restart={param($entry)
            Start-ProductProcess -Name $entry.name -FilePath mock -ArgumentList @() `
                -Environment @{DSH_UNKNOWN_BIRTH_TEST='mock'} -Marker mock -ExistingEntry $entry
        }
        $now=[datetime]::UtcNow
        Invoke-ProductSupervision -Entries $started -Restart $restart -Now $now `
            -CanRestart { -not $checkpointState.degraded -and `
                $script:productPendingBirths.Count -eq 0 }
        if ($script:productPendingBirths.Count -ne 1 -or $script:SpawnCount -ne 1) {
            throw 'postspawn metadata/restore failure lost newborn registration'
        }
        $birth=$script:productPendingBirths[0]
        if (-not $script:productBirthProcesses[$birth]) { throw `
            'actual returned Process not retained' }
        if ($worker.pid -ne 1002) { throw 'unproven birth replaced prior generation' }
        if ($script:Stopped.Count) { throw 'unknown newborn was stopped by unverified PID' }
        Invoke-ProductSupervision -Entries $started -Restart $restart -Now $now.AddSeconds(30) `
            -CanRestart { -not $checkpointState.degraded -and `
                $script:productPendingBirths.Count -eq 0 }
        if ($script:SpawnCount -ne 1) { throw 'unknown newborn admitted duplicate restart' }
        if (-not(Resolve-ProductPendingBirths)) { throw `
            'recovered birth identity was not resolved' }
        if ($script:ProcessTable[1102] -or $script:ProcessTable[1103]) {
            throw 'verified recovered birth tree not cleaned up'
        }
        if (-not(Test-ProductProcessIdentity $api)) { throw 'unknown birth affected existing API' }
    }
    'postspawn_shutdown_unknown' {
        $script:FailWrites=0;$script:FailBirthStamp=$true
        try { Start-ProductProcess -Name worker -FilePath mock -ArgumentList @() -Environment `
            @{} -Marker mock -ExistingEntry $worker | Out-Null } catch {}
        $birth=$script:productPendingBirths[0]
        $returned=$script:productBirthProcesses[$birth]
        $returned | Add-Member ScriptProperty StartTime { throw `
            'identity still inaccessible' } -Force
        $script:productPendingBirthRetryAt=[datetime]::UtcNow.AddMinutes(1)
        $script:Disposed=$false
        $ownership=[pscustomobject]@{};$ownership | Add-Member ScriptMethod Dispose {
            $script:Disposed=$true
        }
        $outer=$ast.Find({param($node) $node -is `
            [System.Management.Automation.Language.TryStatementAst] -and
            $node.Body.Extent.Text -match 'Keep report access'},$true)
        try { . ([scriptblock]::Create('try {} finally '+$outer.Finally.Extent.Text)) }
        catch { $caught=$_.Exception.Message }
        if ($caught -ne 'product_process_cleanup_unconfirmed') { throw `
            'unknown birth shutdown silently reported complete' }
        if (-not $script:Disposed -or $script:ProcessTable[1001]) { throw `
            'strict stop failed to release ownership or stop registered API' }
        if ($script:Stopped -contains 1102 -or $script:Stopped -contains 1103) { throw `
            'shutdown guessed an unknown newborn identity' }
        if ($script:productPendingBirths.Count -ne 1 -or
            $script:productBirthProcesses[$birth] -ne $returned) { throw `
            'unknown shutdown erased actual Process reference' }
    }
    default { throw 'unknown scenario' }
}
[pscustomobject]@{scenario=$Scenario;mock_only=$true;writes=$script:Writes;starts=$script:SpawnCount
    stopped=@($script:Stopped);pending_births=$script:productPendingBirths.Count} | `
        ConvertTo-Json -Compress
'''


@pytest.mark.parametrize("scenario", [
    "runtime_ast_failure", "runtime_ast_unknown", "birth_failure_preserves_previous",
    "checkpoint_backoff_latest_then_resume", "unknown_checkpoint_error",
    "birth_survivor_blocks_duplicate", "startup_registration_strict",
    "runtime_restart_birth_failure", "checkpoint_bounded_delays",
    "storage_error_categories", "birth_success_publishes_once",
    "postspawn_stamp_failure", "postspawn_restore_failure",
    "postspawn_shutdown_unknown",
])
def test_product_state_persistence_boundaries(tmp_path: Path, scenario: str) -> None:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("PowerShell runtime required")
    repo = Path(__file__).resolve().parents[2]
    wrapper = tmp_path / "persistence.ps1"
    wrapper.write_text(SCRIPT, encoding="utf-8")
    result = subprocess.run(
        [shell, "-NoProfile", "-File", str(wrapper), str(repo), scenario, str(tmp_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["scenario"] == scenario
    assert report["mock_only"] is True
