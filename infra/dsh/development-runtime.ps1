# Development owns only one source-local instance. No release receipt is produced.
function Get-DevelopmentLifetime {
    param($Entry)
    $state = 'unknown'
    if ($Entry -and ($Entry.pid -is [int] -or $Entry.pid -is [long] -or $Entry.pid -is [uint32]) -and
        $Entry.pid -gt 0 -and [string]$Entry.started_ticks -match '^[0-9]+$') {
        try {
            $process = Get-Process -Id $Entry.pid -ErrorAction Stop
            if (-not $process) { $state = 'absent' }
            elseif ($process.StartTime.ToUniversalTime().Ticks.ToString() -ceq [string]$Entry.started_ticks) {
                $state = 'alive'
            } else { $state = 'reused' }
        } catch {
            if ($_.FullyQualifiedErrorId -like 'NoProcessFoundForGivenId,*') { $state = 'absent' }
        }
        if ($state -eq 'unknown') {
            try {
                $native = Get-CimInstance Win32_Process -Filter "ProcessId=$($Entry.pid)" `
                    -Property ProcessId, CreationDate -ErrorAction Stop
                # CIM birth timestamps can disprove identity, never establish
                # exact ownership (their serialized precision is coarser).
                if ($native -and $native.CreationDate -is [datetime] -and
                    [Math]::Abs([decimal]$native.CreationDate.ToUniversalTime().Ticks -
                        [decimal]$Entry.started_ticks) -gt 10000) { $state = 'reused' }
            } catch { } # Inaccessible/matching birth metadata remains unknown.
        }
    }
    [pscustomobject]@{pid=$Entry.pid;started_ticks=[string]$Entry.started_ticks;state=$state}
}

function Get-DevelopmentStatus {
    param([string]$RepositoryRoot, $Layout)
    $supervisor = $null; $entries = @(); $readError = $false
    try {
        $path = Join-Path $Layout.runtime 'supervisor.json'
        if (Test-Path -LiteralPath $path -ErrorAction Stop) {
            $supervisor = Get-Content -LiteralPath $path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        }
        $path = Join-Path $Layout.runtime 'processes.json'
        if (Test-Path -LiteralPath $path -ErrorAction Stop) {
            $entries = @(Get-Content -LiteralPath $path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop)
        } elseif ($supervisor) { $readError = $true }
    } catch { $readError = $true }
    $supervisorIdentity = if ($supervisor) { Get-DevelopmentLifetime $supervisor } else { $null }
    $identities = @(foreach ($entry in $entries) {
        foreach ($item in @($entry) + @($entry.children)) {
            if ($item) { Get-DevelopmentLifetime $item }
        }
    })
    $ownership = 'unverified'; $verifiedIdentities = @()
    if ($supervisorIdentity.state -eq 'alive') {
        try {
            # Existing DEV verifier binds the supervisor's actual -File and
            # -DevelopmentRoot arguments and live parent/owner lifetimes.
            $verified = @(Get-DevelopmentVerifiedProcesses -RepositoryRoot $RepositoryRoot -Layout $Layout `
                -Supervisor $supervisor -Entries $entries)
            $verifiedIdentities = @($verified) + @($verified | ForEach-Object { $_.children })
            $ownership = 'verified'
        } catch { $ownership = 'unknown' }
    }
    $ports = @($entries | Where-Object health_probe | ForEach-Object { [int]$_.health_probe.port })
    $listeners = @(); $portState = 'observed'
    try {
        $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -in $ports |
            ForEach-Object {
                $owner = [int]$_.OwningProcess
                # Port occupancy is not ownership. No listener becomes a stop target.
                [pscustomobject]@{port=[int]$_.LocalPort;pid=$owner;ownership=if ($ownership -eq 'verified' -and
                    @($verifiedIdentities | Where-Object { $_.pid -eq $owner -and
                        (Get-DevelopmentLifetime $_).state -eq 'alive' }).Count) {
                        'verified'
                    } else { 'unverified' }}
            })
    } catch { $portState = 'unknown' }
    $observed = 'absent'
    if ($readError -or $portState -eq 'unknown' -or
        $supervisorIdentity.state -eq 'unknown' -or @($identities | Where-Object state -eq 'unknown').Count) {
        $observed = 'unknown'
    }
    if (@($identities | Where-Object state -eq 'alive').Count) { $observed = 'residual' }
    if ($supervisorIdentity.state -eq 'alive') {
        $observed = if ($ownership -eq 'verified' -and -not $readError -and $portState -ne 'unknown' -and
            -not @($identities | Where-Object state -eq 'unknown').Count) { 'running' } else { 'unknown' }
    }
    [pscustomobject]@{schema='development-status.v1';launch_id=$supervisor.launch_id
        recorded_state=$supervisor.state;observed_state=$observed;observed_at=[datetime]::UtcNow.ToString('o')
        supervisor_identity=$supervisorIdentity;ownership=$ownership;children=$identities
        port_inventory=$portState;listeners=$listeners;state_read_error=$readError}
}

function Write-DevelopmentStoppedReceipt {
    param([string]$RuntimeDirectory, $Supervisor, $Targets)
    $remaining = @($Targets | ForEach-Object { Get-DevelopmentLifetime $_ } |
        Where-Object state -in @('alive', 'unknown'))
    if ($remaining.Count) {
        foreach ($identity in $remaining) {
            Write-Warning "Development stop unconfirmed: pid=$($identity.pid) state=$($identity.state)"
        }
        throw 'development_stop_unconfirmed_state_preserved'
    }
    $receipt = [pscustomobject]@{schema='development-stop.v1';launch_id=$Supervisor.launch_id
        state='stopped';observed_at=[datetime]::UtcNow.ToString('o');identities=@($Targets | ForEach-Object {
            [pscustomobject]@{pid=$_.pid;started_ticks=[string]$_.started_ticks}
        })}
    $supervisorFile = Join-Path $RuntimeDirectory 'supervisor.json'
    if (Test-Path -LiteralPath $supervisorFile -ErrorAction Stop) {
        Assert-DevelopmentSameLaunch -SupervisorFile $supervisorFile -Supervisor $Supervisor
    }
    Write-ProductJson -Path (Join-Path $RuntimeDirectory 'stop-receipt.json') -Value $receipt
    $Supervisor | Add-Member -NotePropertyName state -NotePropertyValue 'stopped' -Force
    Write-ProductJson -Path (Join-Path $RuntimeDirectory 'supervisor.json') -Value $Supervisor
}

function Write-DevelopmentCleanupReceipt {
    param([string]$RuntimeDirectory, $Supervisor, $Entries)
    $targets = @($Entries) + @($Entries | ForEach-Object { $_.children })
    $remaining = @($targets | ForEach-Object { Get-DevelopmentLifetime $_ } |
        Where-Object state -in @('alive','unknown'))
    if ($remaining.Count) { throw 'development_cleanup_unconfirmed_state_preserved' }
    # Executing finally proves child absence, never the supervisor's own exit.
    Write-ProductJson -Path (Join-Path $RuntimeDirectory 'cleanup-receipt.json') -Value @{
        schema='development-cleanup.v1';launch_id=$Supervisor.launch_id;state='children_stopped'
        observed_at=[datetime]::UtcNow.ToString('o');supervisor_pid=$Supervisor.pid
        supervisor_started_ticks=[string]$Supervisor.started_ticks
    }
    $Supervisor.state = 'exiting'
    Write-ProductJson -Path (Join-Path $RuntimeDirectory 'supervisor.json') -Value $Supervisor
}

function Get-DevelopmentTerminalInventory {
    param($Supervisor, $Entries)
    $Supervisor
    foreach ($entry in $Entries) {
        $entry
        foreach ($child in $entry.children) {
            if (-not $child) { continue }
            $lifetime = Get-DevelopmentLifetime $child
            # A readable lifetime born before this supervisor cannot be its
            # descendant. Saved children never grant ownership or kill rights.
            # Everything else (including inaccessible/dead-parent hints) stays
            # in the absence guard until disproven or observed absent/reused.
            if ($lifetime.state -eq 'alive' -and
                [string]$Supervisor.started_ticks -match '^[0-9]+$' -and
                [decimal]$child.started_ticks -lt [decimal]$Supervisor.started_ticks) { continue }
            $child
        }
    }
}

function Assert-DevelopmentSameLaunch {
    param([string]$SupervisorFile, $Supervisor)
    $latest = Get-Content -LiteralPath $SupervisorFile -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    if ($latest.pid -ne $Supervisor.pid -or $latest.started_ticks -cne [string]$Supervisor.started_ticks -or
        $latest.launch_id -cne $Supervisor.launch_id) { throw 'development_launch_changed_state_preserved' }
}

function Assert-DevelopmentPlainPath {
    param([string]$RepositoryRoot, [string]$Path)
    $root = [IO.Path]::GetFullPath($RepositoryRoot).TrimEnd('\', '/')
    $full = [IO.Path]::GetFullPath($Path)
    if ($full -ne $root -and -not $full.StartsWith($root + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase)) { throw 'development_root_invalid' }
    $cursor = $root
    $parts = @('') + @($full.Substring($root.Length).TrimStart('\', '/').Split([char[]]'\/') | Where-Object { $_ })
    foreach ($part in $parts) {
        if ($part) { $cursor = Join-Path $cursor $part }
        $item = Get-Item -LiteralPath $cursor -Force -ErrorAction SilentlyContinue
        if ($item) {
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.LinkType) {
                throw 'development_path_linked'
            }
        }
    }
}

function Resolve-DevelopmentLayout {
    param([string]$RepositoryRoot, [string]$DevelopmentRoot = 'data/development/default')
    if ([string]::IsNullOrWhiteSpace($DevelopmentRoot) -or
        @($DevelopmentRoot.Split([char[]]'\/') | Where-Object { $_ -in @('.', '..') -or $_ -match '[. ]$' }).Count) {
        throw 'development_root_invalid'
    }
    $repository = [IO.Path]::GetFullPath($RepositoryRoot).TrimEnd('\', '/')
    $parent = Join-Path $repository 'data/development'
    $full = if ([IO.Path]::IsPathRooted($DevelopmentRoot)) { [IO.Path]::GetFullPath($DevelopmentRoot) }
        else { [IO.Path]::GetFullPath((Join-Path $repository $DevelopmentRoot)) }
    $full = $full.TrimEnd('\', '/')
    $name = [IO.Path]::GetFileName($full)
    if ([IO.Path]::GetDirectoryName($full) -ine $parent -or
        $name -cnotmatch '^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$' -or
        $name -match '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$') { throw 'development_root_invalid' }
    $workspaceRoot = Split-Path $repository
    $publication = Join-Path $workspaceRoot ".decision-hub-development/$name/publication"
    $layout = [pscustomobject]@{ root=$full; data=(Join-Path $full 'data')
        runtime=(Join-Path $full 'runtime'); home=(Join-Path $full 'dsh-home'); publication=$publication }
    foreach ($path in @($layout.root, $layout.data, $layout.runtime, $layout.home,
            (Join-Path $layout.home '.credentials.yaml'), (Join-Path $layout.home 'settings.yaml'),
            (Join-Path $layout.home 'cordis.patch.yml'),
            (Join-Path $layout.home 'profiles/web/package.json'),
            (Join-Path $layout.home 'profiles/web/pnpm-lock.yaml'),
            (Join-Path $layout.home 'profiles/web/pnpm-workspace.yaml'),
            (Join-Path $layout.home 'profiles/web/.npmrc'),
            (Join-Path $layout.home 'profiles/web/cordis.patch.yml'),
            (Join-Path $layout.home 'profiles/web/cordis.yml'),
            (Join-Path $layout.home '.agent-presets/decision-research/agent.cordis.yml'),
            (Join-Path $layout.home '.agent-presets/decision-research/preset.yml'))) {
        Assert-DevelopmentPlainPath -RepositoryRoot $repository -Path $path
    }
    # Publication material must live outside the source repository. Validate
    # its deterministic reserved path without creating an unused publisher.
    foreach ($path in @($publication, (Join-Path $publication 'stage'), (Join-Path $publication 'archive'))) {
        Assert-DevelopmentPlainPath -RepositoryRoot $workspaceRoot -Path $path
        if ((Test-Path -LiteralPath $path) -and -not (Get-Item -LiteralPath $path -Force).PSIsContainer) {
            throw 'development_publication_path_invalid'
        }
    }
    # Profiles intentionally contain official SDK junctions; private write trees do not.
    foreach ($directory in @($layout.data, $layout.runtime, $publication)) {
        Assert-DevelopmentPrivateTree -Directory $directory
    }
    Assert-DevelopmentHomeTree -RepositoryRoot $repository -HomePath $layout.home
    return $layout
}

function Assert-DevelopmentPrivateTree {
    param([string]$Directory)
    if (-not (Test-Path -LiteralPath $Directory)) { return }
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($Directory)
    while ($pending.Count) {
        foreach ($item in Get-ChildItem -LiteralPath $pending.Pop() -Force -ErrorAction Stop) {
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.LinkType) {
                throw 'development_path_linked'
            }
            if ($item.PSIsContainer) { $pending.Push($item.FullName) }
        }
    }
}

function Assert-DevelopmentInstanceEnvironment {
    param($Layout)
    if ($env:DECISION_HUB_PUBLICATION_DATA_DIR) { throw 'development_publication_override_forbidden' }
    $bindings = @{
        DECISION_HUB_DEVELOPMENT_ROOT = @($Layout.root, 'root')
        DECISION_HUB_DATA_DIR = @($Layout.data, 'data')
        DECISION_HUB_DSH_HOME = @($Layout.home, 'dsh_home')
        DECISION_HUB_CHECKPOINT_PATH = @((Join-Path $Layout.data 'checkpoints/decision_graph.sqlite3'), 'checkpoint')
        DECISION_HUB_DOCUMENT_BUNDLE_ROOT = @((Join-Path $Layout.data 'document-bundles'), 'document_bundle')
        DECISION_HUB_SOURCE_ACQUISITION_ROOT = @((Join-Path $Layout.data 'source-acquisition'), 'source_acquisition')
    }
    foreach ($key in $bindings.Keys) {
        $inherited = [Environment]::GetEnvironmentVariable($key, 'Process')
        if ($inherited -and (-not [IO.Path]::IsPathRooted($inherited) -or
                [IO.Path]::GetFullPath($inherited) -ine $bindings[$key][0])) {
            throw ('development_' + $bindings[$key][1] + '_override_forbidden')
        }
    }
}

function Assert-DevelopmentHomeTree {
    param([string]$RepositoryRoot, [string]$HomePath)
    if (-not (Test-Path -LiteralPath $HomePath)) { return }
    $moduleRoots = @('profiles/node_modules', 'profiles/web/node_modules',
        'profiles/web/.dsh-module-fallback/node_modules') | ForEach-Object { Join-Path $HomePath $_ }
    $dependencyRoots = @($moduleRoots) + @('node_modules', '.cache/dsh-upstream/source',
        'extensions/dsh/decision-hub' | ForEach-Object { Join-Path $RepositoryRoot $_ })
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($HomePath)
    while ($pending.Count) {
        foreach ($item in Get-ChildItem -LiteralPath $pending.Pop() -Force -ErrorAction Stop) {
            $linked = ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.LinkType
            if ($linked) {
                $dependencyEntry = @($moduleRoots | Where-Object {
                    $item.FullName.StartsWith($_ + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
                }).Count -gt 0
                if (-not $dependencyEntry) { throw 'development_path_linked' }
                if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                    foreach ($target in @($item.Target)) {
                        $fullTarget = if ([IO.Path]::IsPathRooted($target)) { [IO.Path]::GetFullPath($target) }
                            else { [IO.Path]::GetFullPath((Join-Path (Split-Path $item.FullName) $target)) }
                        if (-not @($dependencyRoots | Where-Object {
                            $fullTarget -ieq $_ -or $fullTarget.StartsWith($_ + [IO.Path]::DirectorySeparatorChar,
                                [StringComparison]::OrdinalIgnoreCase)
                        }).Count) { throw 'development_dependency_link_invalid' }
                    }
                }
                # Inspect the junction itself before any enumeration of its target.
                continue
            }
            if ($item.PSIsContainer) { $pending.Push($item.FullName) }
        }
    }
}

function Assert-DevelopmentNoSharedRuntime {
    param([string]$RepositoryRoot, $Layout, [switch]$OwnsInstanceLock)
    $checks = @(@{path=(Join-Path $RepositoryRoot 'data/product-local/supervisor.json'); error='development_shared_live_runtime'})
    if (-not $OwnsInstanceLock) {
        $checks += @{path=(Join-Path $Layout.runtime 'supervisor.json'); error='development_instance_running'}
    }
    foreach ($check in $checks) {
        if (-not (Test-Path -LiteralPath $check.path)) { continue }
        try {
            $saved = Get-Content -LiteralPath $check.path -Raw | ConvertFrom-Json
            if (($saved.pid -isnot [int] -and $saved.pid -isnot [long]) -or
                [string]$saved.started_ticks -notmatch '^[0-9]+$') { throw 'invalid state' }
            # Cached state claims a lifetime; a running label or recycled PID
            # never proves current ownership. CIM exposes non-sensitive birth
            # metadata even when the new process denies StartTime access.
            $nativeProcess = $null
            try {
                $nativeProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.pid)" `
                    -Property ProcessId, CreationDate -ErrorAction Stop
            }
            catch { } # Exact process time below still establishes identity or fails closed.
            if ($nativeProcess -and $nativeProcess.CreationDate -is [datetime]) {
                $nativeTicks = $nativeProcess.CreationDate.ToUniversalTime().Ticks
                # WMI serializes microseconds; recorded .NET ticks are 100 ns.
                # A conservative 1 ms margin never treats rounding as reuse.
                if ([Math]::Abs([decimal]$nativeTicks - [decimal]$saved.started_ticks) -gt 10000) { continue }
            }
            try { $process = Get-Process -Id $saved.pid -ErrorAction Stop }
            catch {
                if ($_.FullyQualifiedErrorId -like 'NoProcessFoundForGivenId,*') { continue }
                throw
            }
            if (-not $process) { throw 'process_identity_unavailable' }
            if ($process.StartTime.ToUniversalTime().Ticks.ToString() -ceq [string]$saved.started_ticks) {
                throw $check.error
            }
        } catch {
            if ($_.Exception.Message -eq $check.error) { throw }
            throw 'development_shared_runtime_unverified'
        }
    }
    try { $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop) }
    catch { throw 'development_listener_inventory_unavailable' }
    if (@($listeners | Where-Object LocalPort -in @(8047,3097,8037,8038)).Count) {
        throw 'development_shared_live_runtime'
    }
}

function Assert-DevelopmentLaunchPolicy {
    param([bool]$FeatureRequested, [int[]]$Ports)
    if ($FeatureRequested) { throw 'development_release_features_forbidden' }
    foreach ($entry in Get-ChildItem Env:) {
        if ($entry.Name -match '^DECISION_HUB_(HARNESS_.*(?:CONFIG|SHA256)|HISTORY_.*(?:CONFIG|SHA256))$' -and
            $entry.Value) { throw 'development_private_policy_forbidden' }
    }
    if ($Ports.Count -ne 5 -or @($Ports | Select-Object -Unique).Count -ne 5 -or
        @($Ports | Where-Object { $_ -lt 1024 -or $_ -gt 65535 -or $_ -in @(8047,3097,8037,8038) }).Count) {
        throw 'development_ports_invalid'
    }
}

function Initialize-DevelopmentHome {
    param([string]$HomePath, [hashtable]$CredentialEnvironment, [string]$Model,
        [string]$ModelSettingsSource = '')
    New-Item -ItemType Directory -Path $HomePath -Force | Out-Null
    Protect-DevelopmentPrivatePath -Path $HomePath -Directory $true
    foreach ($name in @('settings.yaml', '.credentials.yaml')) {
        $privateFile = Join-Path $HomePath $name
        if (Test-Path -LiteralPath $privateFile) { Protect-DevelopmentPrivateFile -Path $privateFile }
    }
    $settingsPath = Join-Path $HomePath 'settings.yaml'
    if (-not (Test-Path -LiteralPath $settingsPath)) {
        $baseUrl = [string]$CredentialEnvironment['OPENAI_BASE_URL']
        if ([string]::IsNullOrWhiteSpace($baseUrl)) { throw 'development_model_route_required' }
        $contextWindow = 128000
        if ($ModelSettingsSource) {
            $routeHash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData(
                [Text.Encoding]::UTF8.GetBytes($baseUrl))).ToLowerInvariant()
            $declaration = & node (Join-Path $PSScriptRoot 'development-model-settings.mjs') `
                --source $ModelSettingsSource --model $Model --base-url-sha256 $routeHash
            if ($LASTEXITCODE -ne 0) { throw 'development_capacity_lookup_failed' }
            $contextWindow = ($declaration | ConvertFrom-Json -ErrorAction Stop).contextWindow
        } else {
            Write-Warning 'development_capacity_unknown_legacy_fallback: 128000 is compatibility metadata, not verified remote capacity'
        }
        $settings = @{
            'llm-pi-ai' = @{ providers = @{ codexai = @{
                displayName='Development model'; api='openai-responses'; baseURL=$baseUrl
                apiKeyEnv='OPENAI_API_KEY'; reasoning='medium'
                models=@(@{id=$Model; name=$Model; contextWindow=$contextWindow; maxTokens=8192
                    input=@('text','image'); reasoningEfforts=@{low='low';medium='medium';high='high';xhigh='xhigh';max='max'}})
            } } }
            'agent-default-model' = @{provider='codexai'; model=$Model}
        }
        [IO.File]::WriteAllText($settingsPath, ($settings | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
    }
    Protect-DevelopmentPrivateFile -Path $settingsPath
}

function Invoke-DevelopmentModelCapacityMigration {
    param([string]$RepositoryRoot, [string]$DevelopmentRoot, [string]$SourcePath,
        [string]$ExpectedSourceSha256, [string]$ExpectedTargetSha256)
    if ($ExpectedSourceSha256 -notmatch '^[a-fA-F0-9]{64}$' -or
        $ExpectedTargetSha256 -notmatch '^[a-fA-F0-9]{64}$') { throw 'development_capacity_hash_required' }
    $layout = Resolve-DevelopmentLayout -RepositoryRoot $RepositoryRoot -DevelopmentRoot $DevelopmentRoot
    $target = Join-Path $layout.home 'settings.yaml'
    if (-not (Test-Path -LiteralPath $layout.runtime -PathType Container)) { throw 'development_capacity_runtime_required' }
    try {
        $instanceLock = [IO.File]::Open((Join-Path $layout.runtime 'supervisor.lock'),
            [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch { throw 'development_instance_running' }
    $temporary = $null
    try {
        $status = Get-DevelopmentStatus -RepositoryRoot $RepositoryRoot -Layout $layout
        if ($status.observed_state -ne 'absent') { throw 'development_capacity_instance_not_absent' }
        Protect-DevelopmentPrivatePath -Path $layout.home -Directory $true
        $temporary = Join-Path $layout.home ('.capacity-' + [guid]::NewGuid().ToString('N') + '.tmp')
        $temporaryStream = [IO.File]::Open($temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $temporaryStream.Dispose()
        Protect-DevelopmentPrivateFile -Path $temporary
        $request = @{source=[IO.Path]::GetFullPath($SourcePath);target=$target
            sourceSha256=$ExpectedSourceSha256;targetSha256=$ExpectedTargetSha256;output=$temporary} | ConvertTo-Json -Compress
        $preparedJson = $request | & node (Join-Path $PSScriptRoot 'development-model-settings.mjs') --prepare
        if ($LASTEXITCODE -ne 0) { throw 'development_capacity_prepare_failed' }
        $prepared = $preparedJson | ConvertFrom-Json -ErrorAction Stop
        $backup = Join-Path $layout.home ("settings.context-preimage-$($prepared.targetSha256).bak")
        if ($prepared.changed) {
            Protect-DevelopmentPrivateFile -Path $target
            if (-not (Test-Path -LiteralPath $backup)) {
                $backupStream = [IO.File]::Open($backup, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
                try {
                    $bytes = [IO.File]::ReadAllBytes($target)
                    $backupStream.Write($bytes, 0, $bytes.Length)
                } finally { $backupStream.Dispose() }
            }
            Assert-DevelopmentPlainPath -RepositoryRoot $RepositoryRoot -Path $backup
            Protect-DevelopmentPrivateFile -Path $backup
            if ((Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash -ine $ExpectedTargetSha256) {
                throw 'development_capacity_backup_mismatch'
            }
            # Revalidate both full paths and bytes immediately before the only target write.
            $recheck = $request | & node (Join-Path $PSScriptRoot 'development-model-settings.mjs') --prepare
            if ($LASTEXITCODE -ne 0 -or $recheck -cne $preparedJson) { throw 'development_capacity_preimage_changed' }
            if ((Get-DevelopmentStatus -RepositoryRoot $RepositoryRoot -Layout $layout).observed_state -ne 'absent') {
                throw 'development_capacity_instance_not_absent'
            }
            if ((Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash -ine $prepared.outputSha256) {
                throw 'development_capacity_prepared_output_changed'
            }
            [IO.File]::Move($temporary, $target, $true)
            if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ine $prepared.outputSha256) {
                throw 'development_capacity_readback_failed'
            }
        }
        [pscustomobject]@{changed=[bool]$prepared.changed;contextWindow=$prepared.contextWindow
            provenance=$prepared.provenance;source_sha256=$prepared.sourceSha256
            target_sha256=(Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
            backup=if ($prepared.changed) {$backup} else {$null};source_written=$false}
    } finally {
        if ($temporary -and (Test-Path -LiteralPath $temporary)) { Remove-Item -LiteralPath $temporary }
        $instanceLock.Dispose()
    }
}

function Protect-DevelopmentPrivateFile {
    param([string]$Path)
    Protect-DevelopmentPrivatePath -Path $Path -Directory $false
}

function Protect-DevelopmentPrivatePath {
    param([string]$Path, [bool]$Directory)
    if (-not (Get-Command Test-ProductSettingsOwnerOnlyAcl -ErrorAction SilentlyContinue)) {
        . (Join-Path $PSScriptRoot 'decision-settings-session.ps1')
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $ownerSection = [Security.AccessControl.AccessControlSections]::Owner
    $ownerAcl = if ($Directory) { [Security.AccessControl.DirectorySecurity]::new($Path, $ownerSection) }
        else { [Security.AccessControl.FileSecurity]::new($Path, $ownerSection) }
    if ($ownerAcl.GetOwner([Security.Principal.SecurityIdentifier]).Value -cne $identity.Value) {
        throw 'development_private_owner_mismatch'
    }
    if (Test-ProductSettingsOwnerOnlyAcl -LiteralPath $Path -Identity $identity -Directory $Directory) { return }
    $accessSection = [Security.AccessControl.AccessControlSections]::Access
    $acl = if ($Directory) { [Security.AccessControl.DirectorySecurity]::new($Path, $accessSection) }
        else { [Security.AccessControl.FileSecurity]::new($Path, $accessSection) }
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($rule in @($acl.GetAccessRules($true, $false, [Security.Principal.SecurityIdentifier]))) {
        $acl.RemoveAccessRuleSpecific($rule)
    }
    if ($Directory) {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            $identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
        [IO.FileSystemAclExtensions]::SetAccessControl([IO.DirectoryInfo]::new($Path), $acl)
    } else {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($identity, 'FullControl', 'Allow'))
        [IO.FileSystemAclExtensions]::SetAccessControl([IO.FileInfo]::new($Path), $acl)
    }
    # An existing owner has implicit WRITE_DAC, not WRITE_OWNER. Never mark
    # Owner/Group/Audit modified when repairing this instance's DACL.
    if (-not (Test-ProductSettingsOwnerOnlyAcl -LiteralPath $Path -Identity $identity -Directory $Directory)) {
        throw 'development_private_acl_unverified'
    }
}

function Initialize-DevelopmentPlugin {
    param([string]$RepositoryRoot, [string]$RuntimeDirectory, [string]$Node,
        [string]$Pnpm, [switch]$BuildPlugin)
    $plugin = Join-Path $RepositoryRoot 'extensions/dsh/decision-hub'
    $identityPath = Join-Path $plugin 'src/generated-build-identity.ts'
    $stampPath = Join-Path $RuntimeDirectory 'plugin-build.json'
    # The generator covers added sources as well as every declared TS/config input.
    & $Node (Join-Path $plugin 'scripts/generate-build-identity.mjs') > (Join-Path $RuntimeDirectory 'plugin-identity.log')
    if ($LASTEXITCODE -ne 0) { throw 'development_plugin_identity_failed' }
    $identity = (Get-FileHash -LiteralPath $identityPath -Algorithm SHA256).Hash
    $current = $false
    if (-not $BuildPlugin -and (Test-Path -LiteralPath $stampPath)) {
        try {
            $stamp = Get-Content -LiteralPath $stampPath -Raw | ConvertFrom-Json
            $current = $stamp.schema -ceq 'development-plugin-build.v1' -and $stamp.identity -ceq $identity
            foreach ($artifact in $stamp.artifacts) {
                if ($artifact.path -cnotmatch '^lib/[A-Za-z0-9._/-]+$' -or $artifact.path -match '(?:^|/)\.\.(?:/|$)') {
                    $current = $false; break
                }
                $path = Join-Path $plugin $artifact.path
                if (-not (Test-Path -LiteralPath $path) -or
                    (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -cne $artifact.sha256) { $current=$false; break }
            }
            if (@($stamp.artifacts).Count -lt 5) { $current=$false }
        } catch { $current=$false }
    }
    if ($current) { return }
    & $Pnpm --dir $plugin build > (Join-Path $RuntimeDirectory 'plugin-build.log') 2>&1
    if ($LASTEXITCODE -ne 0) { throw 'development_plugin_build_failed' }
    foreach ($name in @('index.js','research-tool.js','synthesis-tool.js','reviewer-tool.js','client.js')) {
        if (-not (Test-Path -LiteralPath (Join-Path $plugin "lib/$name"))) { throw 'development_plugin_artifact_missing' }
    }
    $artifacts = @(foreach ($file in Get-ChildItem -LiteralPath (Join-Path $plugin 'lib') -File -Recurse) {
        @{path=$file.FullName.Substring($plugin.Length+1).Replace('\','/'); sha256=(Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash}
    })
    @{schema='development-plugin-build.v1'; identity=$identity; artifacts=$artifacts; grants_product_receipt=$false} |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $stampPath -Encoding utf8
}

function Initialize-DevelopmentProfile {
    param([string]$RepositoryRoot, [string]$HomePath, [string]$Node)
    $manifest = Join-Path $HomePath 'profiles/web/package.json'
    [void](Resolve-DevelopmentLayout -RepositoryRoot $RepositoryRoot -DevelopmentRoot (Split-Path $HomePath))
    $pluginDir = Join-Path $RepositoryRoot 'extensions/dsh/decision-hub'
    $installed = $false
    if (Test-Path -LiteralPath $manifest) {
        $package = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
        $spec = [string]$package.dependencies.'@decision-hub/dsh-plugin'
        if ($spec.StartsWith('link:')) {
            $target = $spec.Substring(5)
            if (-not [IO.Path]::IsPathRooted($target)) { $target=Join-Path (Split-Path $manifest) $target }
            $installed = [IO.Path]::GetFullPath($target) -ieq [IO.Path]::GetFullPath($pluginDir)
        }
        $installed = $installed -and '@decision-hub/dsh-plugin' -cin @($package.dsh.profile.bundles)
    }
    if ($installed) { return }
    $previous = $env:DSH_HOME
    try {
        $env:DSH_HOME = $HomePath
        & $Node (Join-Path $RepositoryRoot '.cache/dsh-upstream/source/apps/cli/lib/bin.js') `
            plugin --profile web add $pluginDir > (Join-Path $HomePath 'profile-install.log') 2>&1
        if ($LASTEXITCODE -ne 0) { throw 'development_profile_install_failed' }
    } finally {
        if ($null -eq $previous) { Remove-Item Env:DSH_HOME -ErrorAction SilentlyContinue }
        else { $env:DSH_HOME=$previous }
    }
}

function Test-DevelopmentServiceHealth {
    param($Entry)
    $probeProperty = $Entry.PSObject.Properties['health_probe']
    if (-not $probeProperty -or -not $probeProperty.Value) { return $true }
    $probe = $probeProperty.Value
    if (-not (Test-ProductOwnedListener -Entry $Entry -Port ([int]$probe.port))) { return $false }
    if (-not $probe.url) { return $true }
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Method Get -Uri ([string]$probe.url) `
            -TimeoutSec 1 -NoProxy -ErrorAction Stop
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 300
    } catch { return $false }
}

function Get-DevelopmentVerifiedProcesses {
    param([string]$RepositoryRoot, $Layout, $Supervisor, $Entries)
    if ($Supervisor.development_root -ine $Layout.root -or
        -not $Supervisor.started_ticks -or $Supervisor.pid -isnot [long] -and $Supervisor.pid -isnot [int]) {
        throw 'development_supervisor_identity_invalid'
    }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($Supervisor.pid)" -ErrorAction Stop
    $command = [string]$process.CommandLine
    $scriptArgument = [regex]::Match($command, '(?i)-File\s+(?:"([^"]+)"|([^\s"]+))')
    $scriptValue = if ($scriptArgument.Groups[1].Success) { $scriptArgument.Groups[1].Value } else { $scriptArgument.Groups[2].Value }
    $rootArgument = [regex]::Match($command, '(?i)-DevelopmentRoot\s+(?:"([^"]+)"|([^\s"]+))')
    if (-not $rootArgument.Success) { throw 'development_supervisor_identity_invalid' }
    $argumentValue = if ($rootArgument.Groups[1].Success) { $rootArgument.Groups[1].Value } else { $rootArgument.Groups[2].Value }
    $commandLayout = Resolve-DevelopmentLayout -RepositoryRoot $RepositoryRoot -DevelopmentRoot $argumentValue
    if (-not (Test-ProductProcessIdentity $Supervisor) -or
        $scriptValue -ine (Join-Path $RepositoryRoot 'infra/dsh/run-product.ps1') -or
        $commandLayout.root -ine $Layout.root -or $command -notmatch '(?i)-DevelopmentMode(?:\s|$)') {
        throw 'development_supervisor_identity_invalid'
    }
    $verified = @(foreach ($entry in $Entries) {
        if ($entry.pid -isnot [long] -and $entry.pid -isnot [int]) { throw 'development_process_identity_invalid' }
        if (-not (Test-ProductProcessIdentity $entry)) { continue }
        $child = Get-CimInstance Win32_Process -Filter "ProcessId=$($entry.pid)" -ErrorAction Stop
        if ($child.ParentProcessId -ne $Supervisor.pid -or
            $entry.development_owner_ticks -cne [string]$Supervisor.started_ticks -or
            $entry.development_root -ine $Layout.root) { throw 'development_process_identity_invalid' }
        # Never trust a mutable state's children list when choosing stop targets.
        $safe = [pscustomobject]@{pid=$entry.pid; started_ticks=$entry.started_ticks; children=@()}
        $safe.children = @(Get-ProductProcessDescendants $safe)
        $safe
    })
    return $verified
}
