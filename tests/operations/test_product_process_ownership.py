"""Exercise managed tree cleanup with mocked processes; never stop a real PID."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = r'''param($Helper, $Scenario)
$ErrorActionPreference = 'Stop'
. $Helper
$script:ProcessTable = @{}
$script:CimInventory = @()
$script:CimDenied = $true
$script:InventoryReads = 0
$script:Stopped = [System.Collections.Generic.List[int]]::new()
$script:RefuseStop = 0
$script:RecycleOnRootStop = $false
$script:RootReads = 0
$script:ExpireRootDuringDiscovery = $false
$created = [datetime]'2026-10-01T00:00:00Z'
function Register-TestProcess([int]$Id, [int]$ParentId, [int]$Age) {
    $process = [pscustomobject]@{
        Id = $Id; StartTime = $created.AddSeconds($Age)
        Parent = if ($ParentId) { [pscustomobject]@{ Id = $ParentId } } else { $null }
    }
    $script:ProcessTable[$Id] = $process
    return [pscustomobject]@{
        pid = $Id
        started_ticks = $process.StartTime.ToUniversalTime().Ticks.ToString()
    }
}
function Get-Process {
    [CmdletBinding()]param([int]$Id)
    if ($PSBoundParameters.ContainsKey('Id')) {
        if ($Id -eq 901) {
            $script:RootReads += 1
            if ($script:ExpireRootDuringDiscovery -and $script:RootReads -eq 2) {
                $script:ProcessTable.Remove(901)
            }
        }
        return $script:ProcessTable[$Id]
    }
    $script:InventoryReads += 1
    return $script:ProcessTable.Values
}
function Get-CimInstance {
    [CmdletBinding()]param([string]$ClassName)
    if ($script:CimDenied) { throw 'test: CIM access denied' }
    return $script:CimInventory
}
function Stop-Process {
    [CmdletBinding()]param([int]$Id, [switch]$Force)
    $script:Stopped.Add($Id)
    if ($Id -ne $script:RefuseStop) { $script:ProcessTable.Remove($Id) }
    if ($Id -eq 901 -and $script:RecycleOnRootStop) {
        Register-TestProcess 902 999 20 | Out-Null
    }
}
function Start-Sleep { param([int]$Milliseconds) }
$root = Register-TestProcess 901 0 0
$root | Add-Member children @()
$expected = @()
$expectedRemaining = @()
switch ($Scenario) {
    'saved_without_parent' {
        $child = Register-TestProcess 902 0 1
        $reused = Register-TestProcess 903 0 2
        Register-TestProcess 903 999 20 | Out-Null
        $root.children = @($child, $reused)
        $root | Add-Member state 'running'
        $root | Add-Member stable_since $created.ToString('o')
        $root | Add-Member consecutive_restarts 0
        $entries = [System.Collections.Generic.List[object]]::new(); $entries.Add($root)
        Invoke-ProductSupervision -Entries $entries -Now $created.AddSeconds(5) -Restart {
            throw 'restart must not run'
        }
        if ($root.children.Count -ne 1 -or $root.children[0].pid -ne 902) {
            throw 'supervision lost a saved child or retained a recycled PID'
        }
        $expected = @(901, 902)
    }
    'parent_inventory_with_orphan' {
        $child = Register-TestProcess 902 901 1
        Register-TestProcess 903 902 2 | Out-Null
        $orphan = Register-TestProcess 904 905 3
        Register-TestProcess 906 901 -1 | Out-Null
        Register-TestProcess 907 999 5 | Out-Null
        $root.children = @($child, $orphan)
        $expected = @(901, 902, 903, 904)
    }
    'expired_root' {
        $root.children = @(Register-TestProcess 902 0 1)
        Register-TestProcess 901 999 20 | Out-Null
        $expected = @(902)
    }
    'child_recycled_before_stop' {
        Register-TestProcess 902 901 1 | Out-Null
        $script:RecycleOnRootStop = $true
        $expected = @(901)
    }
    'root_exits_during_discovery' {
        $root.children = @(Register-TestProcess 902 901 1)
        $script:ExpireRootDuringDiscovery = $true
        $expected = @(902)
    }
    'failed_child_stop' {
        Register-TestProcess 902 901 1 | Out-Null
        $script:RefuseStop = 902
        $expected = @(901, 902)
        $expectedRemaining = @(902)
    }
    'cim_available' {
        Register-TestProcess 902 901 1 | Out-Null
        $root.children = @(Register-TestProcess 904 905 3)
        $script:CimDenied = $false
        $script:CimInventory = @([pscustomobject]@{
            ProcessId = 902; ParentProcessId = 901; CreationDate = $created.AddSeconds(1)
        })
        $expected = @(901, 902, 904)
    }
    default { throw 'unknown scenario' }
}
$remaining = @(Stop-ProductProcessTree -Entry $root)
if ((@($script:Stopped | Sort-Object) -join ',') -ne (@($expected | Sort-Object) -join ',')) {
    throw "wrong stopped identities: $($script:Stopped -join ',')"
}
if ((@($remaining.pid | Sort-Object) -join ',') -ne
    (@($expectedRemaining | Sort-Object) -join ',')) {
    throw 'survivor identities were lost'
}
if ($Scenario -notin @('expired_root', 'root_exits_during_discovery') -and
    $script:Stopped[0] -ne 901) {
    throw 'root must stop before descendants'
}
if ($Scenario -eq 'cim_available' -and $script:InventoryReads -ne 0) {
    throw 'CIM success unnecessarily used fallback inventory'
}
if ($Scenario -eq 'expired_root' -and $script:InventoryReads -ne 0) {
    throw 'expired root must not discover a recycled process tree'
}
[pscustomobject]@{
    scenario = $Scenario
    stopped = @($script:Stopped)
    remaining = @($remaining.pid)
} | ConvertTo-Json -Compress
'''


@pytest.mark.parametrize("scenario", [
    "saved_without_parent",
    "parent_inventory_with_orphan",
    "expired_root",
    "child_recycled_before_stop",
    "root_exits_during_discovery",
    "failed_child_stop",
    "cim_available",
])
def test_managed_tree_preserves_verified_ownership_without_cim(tmp_path, scenario):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell runtime required")
    repo = Path(__file__).resolve().parents[2]
    script = tmp_path / "mock_processes.ps1"
    script.write_text(SCRIPT, encoding="utf-8")
    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(script),
            str(repo / "infra/dsh/product-processes.ps1"),
            scenario,
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["scenario"] == scenario
