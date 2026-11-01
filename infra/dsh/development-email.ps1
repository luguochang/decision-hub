# DEV-only, CurrentUser DPAPI persistence. Never serialize the decrypted environment.
. (Join-Path $PSScriptRoot 'development-runtime.ps1')
. (Join-Path $PSScriptRoot 'decision-settings-session.ps1')

function Assert-DevelopmentEmailPlatform {
    if ($PSVersionTable.PSVersion.Major -lt 7 -or -not $IsWindows) {
        throw 'development_email_windows_powershell7_required'
    }
    if (-not ('DevelopmentEmailNative' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class DevelopmentEmailNative {
    [StructLayout(LayoutKind.Sequential)]
    public struct StandardInfo {
        public long AllocationSize, EndOfFile;
        public uint NumberOfLinks;
        public byte DeletePending, Directory;
    }
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool GetFileInformationByHandleEx(
        SafeFileHandle handle, int kind, out StandardInfo info, uint size);
}
'@
    }
}

function Assert-DevelopmentEmailAcl {
    param([string]$Path, [bool]$Directory)
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().User
    if (-not (Test-ProductSettingsOwnerOnlyAcl -LiteralPath $Path -Identity $identity -Directory $Directory)) {
        throw 'development_email_private_acl_required'
    }
}

function Read-DevelopmentEmailFile {
    param([string]$RepositoryRoot, $Layout)
    $path = Join-Path $Layout.runtime 'email-settings.json'
    Assert-DevelopmentPlainPath -RepositoryRoot $RepositoryRoot -Path $path
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    Assert-DevelopmentEmailAcl -Path $Layout.runtime -Directory $true
    Assert-DevelopmentEmailAcl -Path $path -Directory $false
    $stream = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        $info = [DevelopmentEmailNative+StandardInfo]::new()
        if (-not [DevelopmentEmailNative]::GetFileInformationByHandleEx($stream.SafeFileHandle, 1,
                [ref]$info, [Runtime.InteropServices.Marshal]::SizeOf($info)) -or $info.NumberOfLinks -ne 1) {
            throw 'development_email_file_linked_or_unverified'
        }
        if ($stream.Length -gt 32768) { throw 'development_email_file_invalid' }
        $reader = [IO.StreamReader]::new($stream, [Text.UTF8Encoding]::new($false, $true))
        try { return $reader.ReadToEnd() } finally { $reader.Dispose() }
    } finally { $stream.Dispose() }
}

function Assert-DevelopmentEmailRecord {
    param([hashtable]$Record)
    if ($Record.schema -isnot [string] -or $Record.channel -isnot [string]) {
        throw 'development_email_record_invalid'
    }
    $keys = @('schema', 'channel')
    if ($Record.channel -ceq 'email') {
        $keys += @('host', 'port', 'sender', 'recipient', 'username', 'password_ciphertext', 'starttls')
    } elseif ($Record.channel -cne 'local') { throw 'development_email_record_invalid' }
    if ($Record.schema -cne 'development-email.v1' -or $Record.Count -ne $keys.Count) {
        throw 'development_email_record_invalid'
    }
    foreach ($key in $Record.Keys) {
        if ($key -cnotin $keys) { throw 'development_email_record_invalid' }
    }
    if ($Record.channel -ceq 'local') { return }
    foreach ($key in @('host', 'sender', 'recipient', 'username', 'password_ciphertext')) {
        $value = $Record[$key]
        if ($value -isnot [string] -or [string]::IsNullOrWhiteSpace($value) -or
            $value.Length -gt 16384 -or $value -match '[\x00-\x1f\x7f]') {
            throw 'development_email_record_invalid'
        }
    }
    if ($Record.port -isnot [int] -and $Record.port -isnot [long] -or
        $Record.port -lt 1 -or $Record.port -gt 65535 -or $Record.port -eq 465 -or
        $Record.starttls -isnot [bool] -or -not $Record.starttls -or
        [Uri]::CheckHostName($Record.host) -eq [UriHostNameType]::Unknown) {
        throw 'development_email_starttls_configuration_invalid'
    }
    foreach ($key in @('sender', 'recipient')) {
        try { $address = [Net.Mail.MailAddress]::new($Record[$key]) }
        catch { throw 'development_email_address_invalid' }
        if ($address.Address -cne $Record[$key] -or $address.DisplayName) {
            throw 'development_email_address_invalid'
        }
    }
}

function Get-DevelopmentEmailEnvironment {
    param([string]$RepositoryRoot, [string]$DevelopmentRoot = 'data/development/default')
    Assert-DevelopmentEmailPlatform
    $layout = Resolve-DevelopmentLayout -RepositoryRoot $RepositoryRoot -DevelopmentRoot $DevelopmentRoot
    $text = Read-DevelopmentEmailFile -RepositoryRoot $RepositoryRoot -Layout $layout
    if ($null -eq $text) { return @{} }
    $document = $null; $secure = $null; $pointer = [IntPtr]::Zero
    try {
        $document = [Text.Json.JsonDocument]::Parse([string]$text)
        $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        foreach ($property in $document.RootElement.EnumerateObject()) {
            if (-not $names.Add($property.Name)) { throw 'duplicate' }
        }
        $record = ConvertFrom-Json -InputObject $text -AsHashtable -ErrorAction Stop
        Assert-DevelopmentEmailRecord -Record $record
        if ($record.channel -ceq 'local') { return @{DECISION_HUB_NOTIFICATION_CHANNEL='local'} }
        $secure = ConvertTo-SecureString -String $record.password_ciphertext -ErrorAction Stop
        if ($secure.Length -eq 0) { throw 'empty' }
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        return @{
            DECISION_HUB_NOTIFICATION_CHANNEL='email'; DECISION_HUB_SMTP_HOST=$record.host
            DECISION_HUB_SMTP_PORT=[string]$record.port; DECISION_HUB_SMTP_SENDER=$record.sender
            DECISION_HUB_SMTP_RECIPIENT=$record.recipient; DECISION_HUB_SMTP_USERNAME=$record.username
            DECISION_HUB_SMTP_PASSWORD=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
            DECISION_HUB_SMTP_STARTTLS='true'
        }
    } catch { throw 'development_email_configuration_invalid_or_undecryptable' }
    finally {
        if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
        if ($secure) { $secure.Dispose() }
        if ($document) { $document.Dispose() }
    }
}

function Get-DevelopmentEmailStatus {
    param([string]$RepositoryRoot, [string]$DevelopmentRoot = 'data/development/default')
    $environment = Get-DevelopmentEmailEnvironment -RepositoryRoot $RepositoryRoot -DevelopmentRoot $DevelopmentRoot
    try {
        return [pscustomobject]@{configured=($environment.Count -gt 0)
            channel=if ($environment.Count) { $environment['DECISION_HUB_NOTIFICATION_CHANNEL'] } else { 'default' }
            validated=($environment.Count -gt 0)}
    } finally { $environment.Clear() }
}

function Save-DevelopmentEmailSettings {
    param([string]$RepositoryRoot, [string]$DevelopmentRoot = 'data/development/default',
        [string]$SmtpHost, [int]$Port = 587, [string]$Sender, [string]$Recipient,
        [string]$Username, [Security.SecureString]$Password, [switch]$Disable)
    Assert-DevelopmentEmailPlatform
    $layout = Resolve-DevelopmentLayout -RepositoryRoot $RepositoryRoot -DevelopmentRoot $DevelopmentRoot
    $record = @{schema='development-email.v1'; channel='local'}
    if (-not $Disable) {
        if ($null -eq $Password -or $Password.Length -eq 0) { throw 'development_email_password_required' }
        $record = @{schema='development-email.v1'; channel='email'; host=$SmtpHost; port=$Port
            sender=$Sender; recipient=$Recipient; username=$Username; starttls=$true
            password_ciphertext=(ConvertFrom-SecureString -SecureString $Password -ErrorAction Stop)}
    }
    Assert-DevelopmentEmailRecord -Record $record
    $json = $record | ConvertTo-Json -Compress
    if ([Text.Encoding]::UTF8.GetByteCount($json) -gt 32768) { throw 'development_email_file_invalid' }
    New-Item -ItemType Directory -Path $layout.runtime -Force | Out-Null
    Protect-DevelopmentPrivatePath -Path $layout.root -Directory $true
    Protect-DevelopmentPrivatePath -Path $layout.runtime -Directory $true
    # Validate an existing target before replacing it; never repair its ACL or follow links.
    $null = Read-DevelopmentEmailFile -RepositoryRoot $RepositoryRoot -Layout $layout
    $path = Join-Path $layout.runtime 'email-settings.json'
    $temporary = Join-Path $layout.runtime ('.email-' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        $stream = [IO.File]::Open($temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $stream.Dispose()
        Protect-DevelopmentPrivatePath -Path $temporary -Directory $false
        [IO.File]::WriteAllText($temporary, $json, [Text.UTF8Encoding]::new($false))
        $null = Resolve-DevelopmentLayout -RepositoryRoot $RepositoryRoot -DevelopmentRoot $DevelopmentRoot
        $null = Read-DevelopmentEmailFile -RepositoryRoot $RepositoryRoot -Layout $layout
        [IO.File]::Move($temporary, $path, $true)
        Assert-DevelopmentEmailAcl -Path $path -Directory $false
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
    }
}
