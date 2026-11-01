# Adopt the isolated workspace through DSH's authenticated official controller.
# Browser credentials remain in memory; registry files are never synthesized.
function Register-DevelopmentWorkspace {
    param([string]$RepositoryRoot, $Layout, [int]$DshPort, [string]$AuthenticatedUrl)
    $authUri = $null
    if (-not [Uri]::TryCreate($AuthenticatedUrl, [UriKind]::Absolute, [ref]$authUri) -or
        $authUri.Scheme -cne 'http' -or $authUri.Host -cne '127.0.0.1' -or $authUri.Port -ne $DshPort -or
        $DshPort -lt 1024 -or $DshPort -gt 65535 -or $DshPort -in @(3097,8047,8037,8038) -or
        $authUri.AbsolutePath -cne '/' -or $authUri.UserInfo -or $authUri.Fragment -or
        $authUri.Query -cnotmatch '^\?token=[A-Za-z0-9_-]+$') {
        throw 'development_workspace_authentication_url_invalid'
    }
    $checked = Resolve-DevelopmentLayout -RepositoryRoot $RepositoryRoot -DevelopmentRoot $Layout.root
    $workspace = Join-Path $checked.home 'Crypto Macro Trader'
    if (-not [IO.Directory]::Exists($workspace)) { throw 'development_workspace_directory_required' }
    $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    try {
        Invoke-WebRequest -UseBasicParsing -Method Get -Uri $AuthenticatedUrl -WebSession $session `
            -TimeoutSec 2 -NoProxy -ErrorAction Stop | Out-Null
    } catch { throw 'development_workspace_authentication_failed' }
    $rpcId = 'decision-hub-workspace-register'
    $request = @{type='client-request'; rpcId=$rpcId; method='workspace/create'
        payload=@{args=@{request=@{path=$workspace}}}} | ConvertTo-Json -Depth 6 -Compress
    try {
        $response = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$DshPort/api/workspace/create" `
            -WebSession $session -ContentType 'application/json' -Body ([Text.Encoding]::UTF8.GetBytes($request)) `
            -TimeoutSec 2 -NoProxy -ErrorAction Stop
        $value = $response.result.value
        if ($response.type -cne 'server-response' -or $response.rpcId -cne $rpcId -or
            $response.result.ok -isnot [bool] -or -not $response.result.ok -or $value.created -isnot [bool] -or
            $value.workspace.workspaceId -isnot [string] -or [string]::IsNullOrWhiteSpace($value.workspace.workspaceId) -or
            $value.workspace.path -isnot [string] -or -not [IO.Path]::IsPathRooted($value.workspace.path) -or
            [IO.Path]::GetFullPath($value.workspace.path) -ine $workspace) {
            throw 'invalid workspace registration'
        }
    } catch { throw 'development_workspace_registration_failed' }
    Write-Host ("DEVELOPMENT_WORKSPACE_CREATED=" + $value.created.ToString().ToLowerInvariant())
}
