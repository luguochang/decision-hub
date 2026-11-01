# Per-launch local-owner credential handoff. No Host/service key reuse, network,
# database access or process/browser startup. Only safe locators leave this helper.
function Test-ProductSettingsOwnerOnlyAcl {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][string]$LiteralPath,
        [Parameter(Mandatory=$true)][System.Security.Principal.SecurityIdentifier]$Identity,
        [Parameter(Mandatory=$true)][bool]$Directory
    )
    # Do not request Audit. PowerShell's FileSystem provider attempts an All-section
    # transfer in Set-Acl, and merely reading SACL state needs SeSecurityPrivilege.
    $sections = [System.Security.AccessControl.AccessControlSections]::Access -bor
        [System.Security.AccessControl.AccessControlSections]::Owner
    $acl = if ($Directory) {
        [System.Security.AccessControl.DirectorySecurity]::new($LiteralPath, $sections)
    } else {
        [System.Security.AccessControl.FileSecurity]::new($LiteralPath, $sections)
    }
    if (-not $acl.AreAccessRulesProtected -or
        $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -cne $Identity.Value) { return $false }
    $rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
    if ($rules.Count -ne 1) { return $false }
    $rule = $rules[0]
    $inheritance = if ($Directory) {
        [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
            [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    } else { [System.Security.AccessControl.InheritanceFlags]::None }
    return (-not $rule.IsInherited -and $rule.IdentityReference.Value -ceq $Identity.Value -and
        $rule.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and
        $rule.FileSystemRights -eq [System.Security.AccessControl.FileSystemRights]::FullControl -and
        $rule.InheritanceFlags -eq $inheritance -and
        $rule.PropagationFlags -eq [System.Security.AccessControl.PropagationFlags]::None)
}

function New-ProductSettingsSession {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][string]$RuntimeDirectory,
        [Parameter(Mandatory=$true)][string]$Origin,
        [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9_.:-]{1,128}$')][string]$OwnerId
    )
    $uri = [Uri]$Origin
    if ($uri.Scheme -cne 'http' -or $uri.Host -notin @('127.0.0.1', 'localhost', '::1') -or
        $uri.AbsolutePath -cne '/' -or $uri.Query -or $uri.Fragment -or $uri.UserInfo -or
        $Origin.TrimEnd('/') -cne $Origin) { throw 'decision_settings_launch_origin_invalid' }
    $runtimePath = [System.IO.Path]::GetFullPath($RuntimeDirectory)
    $privatePath = Join-Path $runtimePath 'private-settings'
    if (Test-Path -LiteralPath $privatePath) {
        if ((Get-Item -LiteralPath $privatePath).Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw 'decision_settings_private_directory_linked'
        }
    } else { New-Item -ItemType Directory -Path $privatePath -Force | Out-Null }
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    if (-not (Test-ProductSettingsOwnerOnlyAcl -LiteralPath $privatePath -Identity $identity -Directory $true)) {
        $directoryAcl = [System.Security.AccessControl.DirectorySecurity]::new()
        $directoryAcl.SetAccessRuleProtection($true, $false)
        $directoryAcl.SetOwner($identity)
        $directoryAcl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
            $identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
        Set-Acl -LiteralPath $privatePath -AclObject $directoryAcl
    }
    $configPath = Join-Path $privatePath 'owner-launch.json'
    $entryPath = Join-Path $privatePath 'Decision Hub.url'
    foreach ($candidate in @($configPath, $entryPath)) {
        if ((Test-Path -LiteralPath $candidate) -and
            ((Get-Item -LiteralPath $candidate).Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw 'decision_settings_private_file_linked'
        }
    }
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $signing = New-Object byte[] 32; $rng.GetBytes($signing)
        $pairing = New-Object byte[] 32; $rng.GetBytes($pairing)
        $signingHex = ([BitConverter]::ToString($signing)).Replace('-', '').ToLowerInvariant()
        $pairingToken = ([BitConverter]::ToString($pairing)).Replace('-', '').ToLowerInvariant()
        $pairingHash = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($pairingToken)))).Replace('-', '').ToLowerInvariant()
        $config = [ordered]@{ schema_version='decision-settings-private-launch.v1'; owner_identity=$OwnerId; origin=$Origin; signing_key_hex=$signingHex; pairing_sha256=$pairingHash; lifetime_seconds=28800 }
        $utf8 = [Text.UTF8Encoding]::new($false)
        [IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json -Compress), $utf8)
        # URL fragment never travels to HTTP access logs. The PC consumes and
        # immediately removes it, then uses HttpOnly signed cookies + CSRF.
        [IO.File]::WriteAllText($entryPath, "[InternetShortcut]`r`nURL=$Origin/#/home?settings_pairing=$pairingToken`r`n", $utf8)
        foreach ($candidate in @($configPath, $entryPath)) {
            if (-not (Test-ProductSettingsOwnerOnlyAcl -LiteralPath $candidate -Identity $identity -Directory $false)) {
                $fileAcl = [System.Security.AccessControl.FileSecurity]::new()
                $fileAcl.SetAccessRuleProtection($true, $false)
                $fileAcl.SetOwner($identity)
                $fileAcl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($identity, 'FullControl', 'Allow'))
                Set-Acl -LiteralPath $candidate -AclObject $fileAcl
            }
        }
        $configHash = ([BitConverter]::ToString($sha.ComputeHash([IO.File]::ReadAllBytes($configPath)))).Replace('-', '').ToLowerInvariant()
        return [pscustomobject]@{ ConfigPath=$configPath; ConfigSha256=$configHash; EntryPath=$entryPath }
    } finally { $rng.Dispose(); $sha.Dispose() }
}
