param(
    [ValidateSet('Start', 'Status', 'Diagnose', 'Stop', 'Restart')]
    [string]$Action = 'Status',
    [string]$BuildDirectory = '.next',
    [ValidateRange(1024, 65535)]
    [int]$Port = 8778,
    [string]$ApiOrigin = 'http://127.0.0.1:8777'
)

$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $taskRoot 'frontend'
$runtime = Join-Path $taskRoot "runs/local-services/frontend-$Port"
$stateFile = Join-Path $runtime 'service.json'

function Read-ServiceState {
    if (Test-Path -LiteralPath $stateFile) {
        return Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
    }
    return $null
}

function Get-ServiceProcess($State) {
    if (-not $State) { return $null }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($State.pid)"
    if (-not $process) { return $null }
    $started = ([datetime]$process.CreationDate).ToUniversalTime()
    $expectedStart = ([datetime]$State.started_at).ToUniversalTime()
    if ($started -ne $expectedStart -or
        $process.ExecutablePath -ne $State.node -or
        $process.CommandLine -notlike "*$($State.next_cli)*") {
        throw 'Recorded PID belongs to another process; refusing to manage it.'
    }
    return $process
}

function Get-Listener {
    return @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Test-LocalOrigin([string]$Origin) {
    $parsed = $null
    return ([uri]::TryCreate($Origin, [UriKind]::Absolute, [ref]$parsed) -and
        $parsed.IsLoopback -and $parsed.Scheme -eq 'http' -and
        $parsed.AbsolutePath -eq '/' -and -not $parsed.Query -and
        -not $parsed.Fragment -and -not $parsed.UserInfo)
}

function Get-HttpHealth([string]$Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -TimeoutSec 3 -UseBasicParsing -MaximumRedirection 0
        return [pscustomobject]@{ url = $Url; status = [int]$response.StatusCode; error = $null }
    } catch {
        return [pscustomobject]@{ url = $Url; status = $null; error = $_.Exception.Message }
    }
}

if ($Action -eq 'Diagnose') {
    $state = $null
    $process = $null
    $identityError = $null
    try {
        $state = Read-ServiceState
        $process = Get-ServiceProcess $state
    } catch {
        $identityError = $_.Exception.Message
    }
    if (-not $PSBoundParameters.ContainsKey('BuildDirectory') -and $state) {
        $BuildDirectory = $state.build_directory
    }
    $validBuildName = $BuildDirectory -match '^\.next(?:[-.][A-Za-z0-9_-]+)*$'
    $buildId = $null
    if ($validBuildName) {
        $buildIdPath = Join-Path (Join-Path $frontend $BuildDirectory) 'BUILD_ID'
        if (Test-Path -LiteralPath $buildIdPath -PathType Leaf) {
            $buildId = (Get-Content -LiteralPath $buildIdPath -Raw).Trim()
        }
    }
    $nextCli = Join-Path $frontend 'node_modules/next/dist/bin/next'
    $node = Get-Command node.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $listeners = Get-Listener
    $ownedListener = [bool]($process -and ($listeners.OwningProcess -contains $process.ProcessId))
    $validApi = Test-LocalOrigin $ApiOrigin
    $frontendHealth = Get-HttpHealth "http://127.0.0.1:$Port"
    $apiHealth = $null
    if ($validApi) { $apiHealth = Get-HttpHealth $ApiOrigin }
    $issues = @()
    if (-not $node) { $issues += 'node_missing' }
    if (-not (Test-Path -LiteralPath $nextCli -PathType Leaf)) { $issues += 'next_cli_missing' }
    if (-not $validBuildName) { $issues += 'invalid_build_directory' }
    elseif (-not $buildId) { $issues += 'build_missing' }
    if ($identityError) { $issues += 'service_identity_unverified' }
    elseif (-not $process) { $issues += 'managed_frontend_stopped' }
    if ($listeners -and -not $ownedListener) { $issues += 'port_owned_by_unmanaged_process' }
    if ($frontendHealth.status -ne 200) { $issues += 'frontend_http_unavailable' }
    if (-not $validApi) { $issues += 'invalid_api_origin' }
    elseif ($apiHealth.status -ne 200) { $issues += 'api_http_unavailable' }
    if ($process -and $buildId -and $state.build_id -ne $buildId) { $issues += 'recorded_build_id_mismatch' }
    [pscustomobject]@{
        schema = 'local-frontend-diagnostics.v1'
        checked_at = (Get-Date).ToUniversalTime().ToString('o')
        startup_attempted = $false
        frontend_ready = [bool]($ownedListener -and $frontendHealth.status -eq 200 -and -not $identityError)
        full_stack_ready = [bool]($issues.Count -eq 0)
        issues = @($issues)
        build_directory = $BuildDirectory
        build_id = $buildId
        node = $(if ($node) { $node.Source } else { $null })
        next_cli = $nextCli
        running = [bool]$process
        listening = $ownedListener
        listener_pids = @($listeners | ForEach-Object { $_.OwningProcess })
        identity_error = $identityError
        frontend_http = $frontendHealth
        api_http = $apiHealth
        service = $state
        approval_status = 'not_observable_from_project_script'
    } | ConvertTo-Json -Depth 6
    exit 0
}

function Stop-Frontend {
    $state = Read-ServiceState
    $process = Get-ServiceProcess $state
    if ($process) {
        Stop-Process -Id $process.ProcessId -ErrorAction Stop
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            if (-not (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue)) { break }
            Start-Sleep -Milliseconds 250
        }
        if (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue) {
            throw 'Frontend did not stop; no replacement was started.'
        }
    }
    if (Get-Listener) { throw "Port $Port is owned by another process; it was not stopped." }
}

if ($Action -eq 'Status') {
    $state = Read-ServiceState
    $process = Get-ServiceProcess $state
    $listeners = Get-Listener
    [pscustomobject]@{
        running = [bool]$process
        listening = [bool]($process -and ($listeners.OwningProcess -contains $process.ProcessId))
        service = $state
        listener_pids = @($listeners | ForEach-Object { $_.OwningProcess })
    } | ConvertTo-Json -Depth 5
    exit 0
}

if ($Action -eq 'Stop') {
    Stop-Frontend
    [pscustomobject]@{ stopped = $true; port = $Port } | ConvertTo-Json
    exit 0
}

if ($BuildDirectory -notmatch '^\.next(?:[-.][A-Za-z0-9_-]+)*$') {
    throw 'BuildDirectory must be a local .next build folder name.'
}
$build = Join-Path $frontend $BuildDirectory
$buildIdFile = Join-Path $build 'BUILD_ID'
$nextCli = Join-Path $frontend 'node_modules/next/dist/bin/next'
$node = (Get-Command node.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
if (-not (Test-LocalOrigin $ApiOrigin)) {
    throw 'ApiOrigin must be a plain HTTP loopback origin.'
}
if (-not (Test-Path -LiteralPath $buildIdFile) -or -not (Test-Path -LiteralPath $nextCli)) {
    throw 'Built frontend or local Next.js executable is missing.'
}
$buildId = (Get-Content -LiteralPath $buildIdFile -Raw).Trim()

if ($Action -eq 'Restart') { Stop-Frontend }
if (Get-Listener) { throw "Port $Port is occupied; use Status and verify its owner first." }

New-Item -ItemType Directory -Path $runtime -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$stdout = Join-Path $runtime "$stamp.stdout.log"
$stderr = Join-Path $runtime "$stamp.stderr.log"
$oldDist = $env:CAR2LEGO_NEXT_DIST
$oldApi = $env:CAR2LEGO_API_ORIGIN
try {
    $env:CAR2LEGO_NEXT_DIST = $BuildDirectory
    $env:CAR2LEGO_API_ORIGIN = $ApiOrigin
    $child = Start-Process -FilePath $node -ArgumentList @(
        "`"$nextCli`"", 'start', '--hostname', '127.0.0.1', '--port', "$Port"
    ) -WorkingDirectory $frontend -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
} finally {
    $env:CAR2LEGO_NEXT_DIST = $oldDist
    $env:CAR2LEGO_API_ORIGIN = $oldApi
}
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$($child.Id)"
if (-not $process) { throw "Frontend exited at startup. Read $stderr" }
$state = [pscustomobject]@{
    schema = 'local-frontend-service.v1'
    pid = $child.Id
    started_at = ([datetime]$process.CreationDate).ToUniversalTime().ToString('o')
    node = $node
    next_cli = $nextCli
    build_directory = $BuildDirectory
    build_id = $buildId
    url = "http://127.0.0.1:$Port"
    api_origin = $ApiOrigin
    stdout = $stdout
    stderr = $stderr
}
$state | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding utf8
for ($attempt = 0; $attempt -lt 80; $attempt++) {
    if ($child.HasExited) { throw "Frontend exited with code $($child.ExitCode). Read $stderr" }
    $listeners = Get-Listener
    if ($listeners.OwningProcess -contains $child.Id) {
        $response = Invoke-WebRequest -Uri $state.url -TimeoutSec 10 -UseBasicParsing
        if ($response.StatusCode -eq 200) {
            $state | ConvertTo-Json -Depth 4
            exit 0
        }
    }
    Start-Sleep -Milliseconds 250
}
throw "Frontend did not become ready. Read $stdout and $stderr"
