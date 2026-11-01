[CmdletBinding()]
param([string]$DevelopmentRoot = 'data/development/default', [switch]$Install)
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'development_powershell7_required' }
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
. (Join-Path $PSScriptRoot 'development-runtime.ps1')
$layout = Resolve-DevelopmentLayout -RepositoryRoot $repoRoot -DevelopmentRoot $DevelopmentRoot
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$hash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData(
    [Text.Encoding]::UTF8.GetBytes($layout.root.ToLowerInvariant()))).Substring(0,16)
$name = "DecisionHub-DEV-watchdog-$hash"
$executable = Join-Path $PSHOME 'pwsh.exe'
$arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -File "' + (Join-Path $PSScriptRoot 'ensure-dev-running.ps1') +
    '" -DevelopmentRoot "' + $layout.root + '"'
function XmlEscape([string]$Value) { [Security.SecurityElement]::Escape($Value) }
$xml = @"
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Recover the current user's official DEV instance; bounded retries, respects watchdog.pause.</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>$(XmlEscape $sid)</UserId></LogonTrigger>
    <TimeTrigger><Repetition><Interval>PT1M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition><StartBoundary>$([datetime]::Now.AddMinutes(1).ToString('yyyy-MM-ddTHH:mm:ss'))</StartBoundary><Enabled>true</Enabled></TimeTrigger>
  </Triggers>
  <Principals><Principal id="Author"><UserId>$(XmlEscape $sid)</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>true</StartWhenAvailable><ExecutionTimeLimit>PT0S</ExecutionTimeLimit><Enabled>true</Enabled></Settings>
  <Actions Context="Author"><Exec><Command>$(XmlEscape $executable)</Command><Arguments>$(XmlEscape $arguments)</Arguments><WorkingDirectory>$(XmlEscape $repoRoot)</WorkingDirectory></Exec></Actions>
</Task>
"@
if ($Install) {
    # No password, elevation, service account, forced replacement or immediate start.
    Register-ScheduledTask -TaskName $name -Xml $xml -ErrorAction Stop | Out-Null
}
[ordered]@{schema='development-watchdog-task.v1';installed=[bool]$Install;task_name=$name
    principal=@{user_id=$sid;run_level='LeastPrivilege';logon_type='InteractiveToken'}
    executable=$executable;arguments=$arguments;working_directory=$repoRoot
    triggers=@('logon','periodic_1_minute');multiple_instances='IgnoreNew'
    pause_path=(Join-Path $layout.runtime 'watchdog.pause');xml=$xml
    availability='current logged-on user only; no wake from sleep'} | ConvertTo-Json -Depth 5
