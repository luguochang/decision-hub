[CmdletBinding()]
param([string]$DevelopmentRoot = 'data/development/default')
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'development_powershell7_required' }
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
. (Join-Path $PSScriptRoot 'development-runtime.ps1')
. (Join-Path $PSScriptRoot 'product-processes.ps1')
$layout = Resolve-DevelopmentLayout -RepositoryRoot $repoRoot -DevelopmentRoot $DevelopmentRoot
Get-DevelopmentStatus -RepositoryRoot $repoRoot -Layout $layout | ConvertTo-Json -Depth 8
