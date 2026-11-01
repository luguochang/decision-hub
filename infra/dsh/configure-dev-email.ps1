[CmdletBinding(DefaultParameterSetName='Configure')]
param(
    [string]$DevelopmentRoot = 'data/development/default',
    [Parameter(ParameterSetName='Check', Mandatory=$true)][switch]$Check,
    [Parameter(ParameterSetName='Disable', Mandatory=$true)][switch]$Disable
)
$ErrorActionPreference = 'Stop'
try {
    . (Join-Path $PSScriptRoot 'development-email.ps1')
    $repository = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
    if ($Check) {
        Get-DevelopmentEmailStatus -RepositoryRoot $repository -DevelopmentRoot $DevelopmentRoot |
            ConvertTo-Json -Compress
    } elseif ($Disable) {
        Save-DevelopmentEmailSettings -RepositoryRoot $repository -DevelopmentRoot $DevelopmentRoot -Disable
        '{"configured":true,"channel":"local","validated":true}'
    } else {
        # Validate the DEV path before prompting. Passwords never enter script arguments/history.
        Assert-DevelopmentEmailPlatform
        $null = Resolve-DevelopmentLayout -RepositoryRoot $repository -DevelopmentRoot $DevelopmentRoot
        Write-Host 'SMTP with STARTTLS (default 587). Implicit SSL on port 465 is unsupported.'
        $smtpHost = Read-Host 'SMTP host'
        $portText = Read-Host 'STARTTLS port [587]'
        $port = 587
        if ($portText -and -not [int]::TryParse($portText, [ref]$port)) { throw 'invalid' }
        $sender = Read-Host 'Sender mailbox'
        $recipient = Read-Host 'One recipient mailbox'
        $username = Read-Host 'SMTP username'
        $password = Read-Host 'SMTP password or app authorization code' -AsSecureString
        try {
            Save-DevelopmentEmailSettings -RepositoryRoot $repository -DevelopmentRoot $DevelopmentRoot `
                -SmtpHost $smtpHost -Port $port -Sender $sender -Recipient $recipient `
                -Username $username -Password $password
        } finally { if ($password) { $password.Dispose() } }
        '{"configured":true,"channel":"email","validated":true}'
    }
} catch {
    # Do not echo supplied fields, ciphertext, decrypted secrets or full error objects.
    [Console]::Error.WriteLine('development_email_configuration_failed')
    exit 1
}
