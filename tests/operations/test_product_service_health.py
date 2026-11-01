"""Mock-only service health ownership and restart tests.

The PowerShell harness never contacts product ports, starts the product, or opens its database.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = r'''param($Helper, $Scenario)
$ErrorActionPreference = 'Stop'
. $Helper
$created = ([datetime]'2026-10-01T00:00:00Z').ToUniversalTime()
$script:ProcessTable = @{}
$script:Stopped = [System.Collections.Generic.List[int]]::new()
$script:Restarts = 0
$script:HealthOk = $false
$script:RestartAllowed = $true
$script:ListenerOwner = 0
function Register-TestProcess([int]$Id, [int]$ParentId, [int]$Age) {
    $process = [pscustomobject]@{
        Id = $Id; StartTime = $created.AddSeconds($Age)
        Parent = if ($ParentId) { [pscustomobject]@{ Id = $ParentId } } else { $null }
    }
    $script:ProcessTable[$Id] = $process
    return [pscustomobject]@{
        pid = $Id; started_ticks = $process.StartTime.ToUniversalTime().Ticks.ToString()
    }
}
function Get-Process {
    [CmdletBinding()]param([int]$Id)
    if ($PSBoundParameters.ContainsKey('Id')) { return $script:ProcessTable[$Id] }
    return $script:ProcessTable.Values
}
function Get-CimInstance { [CmdletBinding()]param([string]$ClassName); throw 'mock CIM denied' }
function Get-NetTCPConnection {
    [CmdletBinding()]param($State, [int]$LocalPort)
    if ($script:ListenerOwner) { return [pscustomobject]@{ OwningProcess=$script:ListenerOwner } }
    return @()
}
function Stop-Process {
    [CmdletBinding()]param([int]$Id, [switch]$Force)
    $script:Stopped.Add($Id)
    $script:ProcessTable.Remove($Id)
}
function Start-Sleep { param([int]$Milliseconds) }
function New-Entry([string]$Name, [int]$Root, [int]$Child, $Probe) {
    $rootIdentity = Register-TestProcess $Root 0 0
    $childIdentity = if ($Child) { Register-TestProcess $Child $Root 1 } else { $null }
    return [pscustomobject]@{
        name=$Name; pid=$rootIdentity.pid; started_ticks=$rootIdentity.started_ticks
        children=if ($childIdentity) { @($childIdentity) } else { @() }
        state='running'; restart_count=0; consecutive_restarts=0
        stable_since=$created.ToString('o'); next_retry_at=$null
        last_error=$null; last_failure_at=$null
        health_probe=$Probe; health_failure_count=0; health_status='healthy'
        last_health_failure_at=$null; cleanup_pending=$false
    }
}
$probe = [pscustomobject]@{
    port=8047; url='http://127.0.0.1:8047/health/live'; startup_grace_seconds=30
}
$api = New-Entry hub-api 1001 1002 $probe
$worker = New-Entry worker 2001 0 $null
$entries = [System.Collections.Generic.List[object]]::new()
$entries.Add($api); $entries.Add($worker)
$health = { param($entry)
    if ($entry.name -eq 'hub-api') { return $script:HealthOk }
    return $true
}
$restart = { param($entry)
    $script:Restarts += 1
    $root = Register-TestProcess 3001 0 200
    $child = Register-TestProcess 3002 3001 201
    $entry.pid = $root.pid; $entry.started_ticks = $root.started_ticks
    $entry.children = @($child)
}
switch ($Scenario) {
    'owned_listener' {
        $script:ListenerOwner = 1002
        if (-not(Test-ProductOwnedListener -Entry $api -Port 8047)) {
            throw 'owned descendant listener not recognized'
        }
        $script:ListenerOwner = 9999
        if (Test-ProductOwnedListener -Entry $api -Port 8047) {
            throw 'unrelated listener claimed as owned health'
        }
    }
    'three_ticks' {
        $now = $created.AddSeconds(100)
        Invoke-ProductSupervision -Entries $entries -Restart $restart -Now $now `
            -HealthCheck $health -CanRestart { $true }
        Invoke-ProductSupervision -Entries $entries -Restart $restart -Now $now.AddSeconds(2) `
            -HealthCheck $health -CanRestart { $true }
        if ($script:Stopped.Count -or $script:Restarts -or $api.health_failure_count -ne 2) {
            throw 'one or two failed ticks killed or restarted service'
        }
        $script:HealthOk = $true
        Invoke-ProductSupervision -Entries $entries -Restart $restart -Now $now.AddSeconds(4) `
            -HealthCheck $health -CanRestart { $true }
        if ($api.health_failure_count -ne 0) {
            throw 'healthy tick did not reset consecutive count'
        }
        $script:HealthOk = $false
        foreach ($seconds in @(6,8,10)) {
            Invoke-ProductSupervision -Entries $entries -Restart $restart `
                -Now $now.AddSeconds($seconds) -HealthCheck $health -CanRestart { $true }
        }
        if ($script:Restarts -or $api.state -ne 'retry_wait' -or
            $api.last_error -ne 'managed_service_unhealthy') {
            throw 'third failure did not isolate old generation before replacement'
        }
        if (($script:Stopped | Sort-Object) -join ',' -ne '1001,1002') {
            throw 'unhealthy exact tree was not stopped'
        }
        if (-not(Test-ProductProcessIdentity $worker)) { throw 'unrelated worker was stopped' }
        Invoke-ProductSupervision -Entries $entries -Restart $restart -Now $now.AddSeconds(12) `
            -HealthCheck $health -CanRestart { $true }
        if ($script:Restarts -ne 1 -or $api.restart_count -ne 1 -or $api.pid -ne 3001) {
            throw 'replacement generation was absent or duplicated'
        }
        if ($api.health_failure_count -ne 0 -or $api.health_status -ne 'starting') {
            throw 'replacement did not enter a fresh health grace state'
        }
    }
    'checkpoint_blocks_birth' {
        $script:RestartAllowed = $false
        $now = $created.AddSeconds(100)
        foreach ($seconds in @(0,2,4,6)) {
            Invoke-ProductSupervision -Entries $entries -Restart $restart `
                -Now $now.AddSeconds($seconds) -HealthCheck $health `
                -CanRestart { $script:RestartAllowed }
        }
        if ($script:Stopped.Count -or $script:Restarts -or
            -not(Test-ProductProcessIdentity $api)) {
            throw 'checkpoint outage stopped ownership or admitted a replacement'
        }
        if ($api.last_error -ne 'managed_service_unhealthy') {
            throw 'blocked unhealthy service lost its diagnosis'
        }
    }
    default { throw 'unknown scenario' }
}
[pscustomobject]@{
    scenario=$Scenario; stopped=@($script:Stopped); restarts=$script:Restarts
    api_pid=$api.pid; worker_alive=(Test-ProductProcessIdentity $worker)
} | ConvertTo-Json -Compress
'''


@pytest.mark.parametrize("scenario", ["owned_listener", "three_ticks", "checkpoint_blocks_birth"])
def test_product_service_health_is_owned_debounced_and_restart_safe(
    tmp_path: Path, scenario: str
) -> None:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("PowerShell runtime required")
    repo = Path(__file__).resolve().parents[2]
    wrapper = tmp_path / "service-health.ps1"
    wrapper.write_text(SCRIPT, encoding="utf-8")
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-File",
            str(wrapper),
            str(repo / "infra/dsh/product-processes.ps1"),
            scenario,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["scenario"] == scenario


def test_launcher_declares_health_ownership_for_critical_listeners() -> None:
    repo = Path(__file__).resolve().parents[2]
    launcher = (repo / "infra/dsh/run-product.ps1").read_text(encoding="utf-8")

    for port in ("$ApiPort", "$AcquisitionPort", "$McpPort", "$DshPort"):
        assert f"port = {port}" in launcher
    assert 'url = "http://127.0.0.1:$ApiPort/health/live"' in launcher
    assert 'url = "http://127.0.0.1:$AcquisitionPort/health/ready"' in launcher
    assert "HUB_API_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS" in launcher
    assert launcher.index("Test-Path -LiteralPath $stopRequest") < launcher.index(
        "Invoke-ProductSupervision -Entries $started"
    )
