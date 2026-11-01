[CmdletBinding()]
param(
    [string]$DevelopmentRoot = 'data/development/default',
    [int]$ApiPort = 18047, [int]$DshPort = 18097,
    [int]$McpPort = 18037, [int]$AcquisitionPort = 18038, [int]$DeskPort = 5173,
    [string]$CredentialFile = '',
    [ValidatePattern('^[a-f0-9]{32}$')][string]$WatchdogIntentId = '',
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$')][string]$ResearchModel = 'gpt-6.1-sol',
    [switch]$BuildPlugin, [switch]$DisableRiskMonitoring
)
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'development_powershell7_required' }
$arguments = @('-NoProfile', '-NonInteractive', '-File', (Join-Path $PSScriptRoot 'run-product.ps1'),
    '-DevelopmentMode', '-DevelopmentRoot', $DevelopmentRoot,
    '-ApiPort', [string]$ApiPort, '-DshPort', [string]$DshPort,
    '-McpPort', [string]$McpPort, '-AcquisitionPort', [string]$AcquisitionPort,
    '-DeskPort', [string]$DeskPort, '-ResearchModel', $ResearchModel)
if ($CredentialFile) { $arguments += @('-CredentialFile', $CredentialFile) }
if ($WatchdogIntentId) { $arguments += @('-WatchdogIntentId', $WatchdogIntentId) }
if ($BuildPlugin) { $arguments += '-BuildPlugin' }
if ($DisableRiskMonitoring) { $arguments += '-DisableRiskMonitoring' }
& (Join-Path $PSHOME 'pwsh.exe') @arguments
exit $LASTEXITCODE
