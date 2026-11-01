# Prepare only the isolated instance's private files; the shared factory owns
# all signing keys, pairing tokens, config contents and rotation.
function New-DevelopmentSettingsSession {
    param([string]$RepositoryRoot, $Layout, [string]$Origin, [string]$OwnerId)
    $checked = Resolve-DevelopmentLayout -RepositoryRoot $RepositoryRoot -DevelopmentRoot $Layout.root
    $private = Join-Path $checked.runtime 'private-settings'
    Assert-DevelopmentPlainPath -RepositoryRoot $RepositoryRoot -Path $private
    New-Item -ItemType Directory -Path $private -Force | Out-Null
    Protect-DevelopmentPrivatePath -Path $private -Directory $true
    foreach ($name in @('owner-launch.json', 'Decision Hub.url')) {
        $path = Join-Path $private $name
        Assert-DevelopmentPlainPath -RepositoryRoot $RepositoryRoot -Path $path
        if (-not (Test-Path -LiteralPath $path)) {
            $stream = [IO.File]::Open($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            $stream.Dispose()
        }
        Protect-DevelopmentPrivateFile -Path $path
    }
    return New-ProductSettingsSession -RuntimeDirectory $checked.runtime -Origin $Origin -OwnerId $OwnerId
}
