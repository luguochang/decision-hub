"""Exercise the actual launcher function with harmless child environment values."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_product_child_environment_does_not_leak_to_restarts(tmp_path):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell launcher runtime required")
    repo = Path(__file__).resolve().parents[2]
    child = tmp_path / "child.ps1"
    child.write_text('''@{
    present = [Environment]::GetEnvironmentVariables('Process').Contains('DSH_ENV_TEST_ONLY')
    value = [Environment]::GetEnvironmentVariable('DSH_ENV_TEST_ONLY', 'Process')
} | ConvertTo-Json -Compress
''', encoding="utf-8")
    wrapper = tmp_path / "wrapper.ps1"
    wrapper.write_text('''param($Repo, $TestDir, $Shell)
$ErrorActionPreference = 'Stop'
. (Join-Path $Repo 'infra/dsh/product-processes.ps1')
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $Repo 'infra/dsh/run-product.ps1'), [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'launcher parse failed' }
$definition = $ast.Find({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Start-ProductProcess'
}, $true)
. ([scriptblock]::Create($definition.Extent.Text))
$repoRoot = $TestDir; $logDir = $TestDir
$stateFile = Join-Path $TestDir 'state.json'
$started = [System.Collections.Generic.List[object]]::new(); $launchSpecs = @{}
Remove-Item Env:DSH_ENV_TEST_ONLY -ErrorAction SilentlyContinue
$first = Start-ProductProcess -Name first -FilePath $Shell -Marker test `
    -ArgumentList @('-NoProfile', '-File', (Join-Path $TestDir 'child.ps1')) `
    -Environment @{ DSH_ENV_TEST_ONLY = 'child-only' }
$first.WaitForExit()
if ([Environment]::GetEnvironmentVariables('Process').Contains('DSH_ENV_TEST_ONLY')) {
    throw 'child environment leaked into launcher'
}
$second = Start-ProductProcess -Name second -FilePath $Shell -Marker test `
    -ArgumentList @('-NoProfile', '-File', (Join-Path $TestDir 'child.ps1')) -Environment @{}
$second.WaitForExit()
''', encoding="utf-8")
    result = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(wrapper), str(repo), str(tmp_path), pwsh],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    first = json.loads((tmp_path / "first.0.stdout.log").read_text(encoding="utf-8-sig"))
    second = json.loads((tmp_path / "second.0.stdout.log").read_text(encoding="utf-8-sig"))
    assert first == {"present": True, "value": "child-only"}
    assert second["present"] is False
