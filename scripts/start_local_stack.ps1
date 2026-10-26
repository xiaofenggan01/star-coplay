#Requires -Version 5.1
<#
Local StarCoPlay stack controller.

Usage:
  powershell -ExecutionPolicy Bypass -File scripts\start_local_stack.ps1
  powershell -ExecutionPolicy Bypass -File scripts\start_local_stack.ps1 start -RuntimeMode Hosted
  powershell -ExecutionPolicy Bypass -File scripts\start_local_stack.ps1 start -RuntimeMode Hosted -HostedAdmissionMode Allowlist -HostedUserIds <uuid-a>,<uuid-b>
  powershell -ExecutionPolicy Bypass -File scripts\start_local_stack.ps1 start
  powershell -ExecutionPolicy Bypass -File scripts\start_local_stack.ps1 stop
  powershell -ExecutionPolicy Bypass -File scripts\start_local_stack.ps1 status
  powershell -ExecutionPolicy Bypass -File scripts\start_local_stack.ps1 restart
  powershell -ExecutionPolicy Bypass -File scripts\start_local_stack.ps1 logs -Service gateway

Services:
  - NewAPI local relay stack, from deploy\newapi
  - Supabase local stack, from platform\supabase
  - Next.js platform web, from platform\web
  - platform/backend WS gateway + HTTP API

The backend starts agent subprocesses when the game Mod opens a WS session.
#>

[CmdletBinding()]
param(
    [ValidateSet("start", "stop", "status", "restart", "logs")]
    [string] $Command = "start",

    [ValidateSet("all", "web", "backend", "gateway", "supabase", "newapi")]
    [string] $Service = "all",

    [ValidateSet("Legacy", "Hosted")]
    [string] $RuntimeMode = "Legacy",

    [ValidateSet("AllAuthenticated", "Allowlist")]
    [string] $HostedAdmissionMode = "AllAuthenticated",

    [Alias("HostedUserId")]
    [string[]] $HostedUserIds = @(),

    [switch] $Visible,
    [switch] $Follow,
    [switch] $KeepSupabase,
    [switch] $KeepNewApi
)

$ErrorActionPreference = "Stop"

# Codex and some Windows launchers can provide both `Path` and `PATH` in the
# process environment. Windows PowerShell 5.1's Start-Process rejects that
# environment block as a duplicate-key dictionary, so retain the canonical
# `Path` entry before starting any child process.
$pathKeys = @(
    [Environment]::GetEnvironmentVariables().Keys |
        Where-Object { [string]::Equals([string] $_, "Path", [StringComparison]::OrdinalIgnoreCase) }
)
if ($pathKeys.Count -gt 1) {
    $pathValue = [Environment]::GetEnvironmentVariable("Path", "Process")
    foreach ($key in $pathKeys) {
        if (-not [string]::Equals([string] $key, "Path", [StringComparison]::Ordinal)) {
            [Environment]::SetEnvironmentVariable([string] $key, $null, "Process")
        }
    }
    [Environment]::SetEnvironmentVariable("Path", $pathValue, "Process")
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$PlatformDir = Join-Path $RepoRoot "platform"
$WebDir = Join-Path $PlatformDir "web"
$BackendDir = Join-Path $PlatformDir "backend"
$NewApiDir = Join-Path $RepoRoot "deploy\newapi"
$NewApiEnvPath = Join-Path $NewApiDir ".env"
$NewApiEnvExamplePath = Join-Path $NewApiDir ".env.example"
$LocalDir = Join-Path $RepoRoot ".local"
$RunDir = Join-Path $LocalDir "run"
$LogDir = Join-Path $LocalDir "logs"
$StatePath = Join-Path $RunDir "starcoplay-local-stack.json"
$HostedConfigPath = Join-Path $LocalDir "hosted-runtime.env"

$WebPidPath = Join-Path $RunDir "platform-web.pid"
$GatewayPidPath = Join-Path $RunDir "platform-backend.pid"
$SupabaseStartPidPath = Join-Path $RunDir "supabase-start.pid"

$WebLogPath = Join-Path $LogDir "platform-web.log"
$WebErrLogPath = Join-Path $LogDir "platform-web.err.log"
$GatewayLogPath = Join-Path $LogDir "platform-backend.log"
$GatewayErrLogPath = Join-Path $LogDir "platform-backend.err.log"
$NewApiLogPath = Join-Path $LogDir "newapi.log"
$NewApiErrLogPath = Join-Path $LogDir "newapi.err.log"
$NewApiStopLogPath = Join-Path $LogDir "newapi-stop.log"
$NewApiStopErrLogPath = Join-Path $LogDir "newapi-stop.err.log"
$SupabaseMigrationLogPath = Join-Path $LogDir "supabase-migration.log"
$SupabaseMigrationErrLogPath = Join-Path $LogDir "supabase-migration.err.log"
$SupabaseLogPath = Join-Path $LogDir "supabase.log"
$SupabaseErrLogPath = Join-Path $LogDir "supabase.err.log"
$SupabaseStopLogPath = Join-Path $LogDir "supabase-stop.log"
$SupabaseStopErrLogPath = Join-Path $LogDir "supabase-stop.err.log"

function Ensure-Dirs {
    $auditDir = Join-Path $LogDir "audit"
    New-Item -ItemType Directory -Force -Path $RunDir, $LogDir, $auditDir | Out-Null
}

function Get-NowIso {
    return (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}

function Write-State {
    param([hashtable] $Extra = @{})

    $state = @{
        repo_root = $RepoRoot
        updated_at = Get-NowIso
        web_pid = Read-PidFile $WebPidPath
        gateway_pid = Read-PidFile $GatewayPidPath
        supabase_start_pid = Read-PidFile $SupabaseStartPidPath
        logs = @{
            web = $WebLogPath
            web_error = $WebErrLogPath
            backend = $GatewayLogPath
            backend_error = $GatewayErrLogPath
            newapi = $NewApiLogPath
            newapi_error = $NewApiErrLogPath
            supabase = $SupabaseLogPath
            supabase_error = $SupabaseErrLogPath
        }
        urls = @{
            platform_web = "http://127.0.0.1:3000"
            newapi = "http://127.0.0.1:13000"
            supabase_studio = "http://127.0.0.1:54323"
            ws_gateway = "ws://127.0.0.1:8765/ws"
            gateway_http = "http://127.0.0.1:8766"
        }
    }
    foreach ($key in $Extra.Keys) {
        $state[$key] = $Extra[$key]
    }
    $state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $StatePath -Encoding UTF8
}

function Read-PidFile {
    param([string] $Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    $raw = (Get-Content -LiteralPath $Path -ErrorAction SilentlyContinue | Select-Object -First 1)
    $pidValue = 0
    if ([int]::TryParse([string]$raw, [ref]$pidValue)) {
        return $pidValue
    }
    return $null
}

function Test-PidRunning {
    param([Nullable[int]] $PidValue)
    if ($null -eq $PidValue -or $PidValue -le 0) {
        return $false
    }
    try {
        $proc = Get-Process -Id $PidValue -ErrorAction Stop
        return $null -ne $proc
    }
    catch {
        return $false
    }
}

function Format-Pid {
    param([Nullable[int]] $PidValue)
    if ($null -eq $PidValue -or $PidValue -le 0) {
        return "-"
    }
    return [string] $PidValue
}

function Get-EnvFileValue {
    param(
        [string] $Path,
        [string] $Key
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        return ""
    }
    foreach ($line in Get-Content -LiteralPath $Path -ErrorAction SilentlyContinue) {
        $trimmed = ([string] $line).Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }
        $name, $value = $trimmed.Split("=", 2)
        if ($name.Trim() -eq $Key) {
            return $value.Trim().Trim("'").Trim('"')
        }
    }
    return ""
}

function Set-EnvFileValue {
    param(
        [string] $Path,
        [string] $Key,
        [string] $Value
    )
    $lines = @()
    $updated = $false
    if (Test-Path -LiteralPath $Path) {
        foreach ($line in Get-Content -LiteralPath $Path -ErrorAction SilentlyContinue) {
            $text = [string] $line
            $trimmed = $text.Trim()
            if ($trimmed -and -not $trimmed.StartsWith("#") -and $trimmed.Contains("=")) {
                $name = $trimmed.Split("=", 2)[0].Trim()
                if ($name -eq $Key) {
                    $lines += "$Key=$Value"
                    $updated = $true
                    continue
                }
            }
            $lines += $text
        }
    }
    if (-not $updated) {
        $lines += "$Key=$Value"
    }
    Set-Content -LiteralPath $Path -Encoding UTF8 -Value $lines
}

function New-RandomSecret {
    param([int] $ByteCount = 48)

    $bytes = New-Object byte[] $ByteCount
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }
    return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

function Resolve-ClaudeBinary {
    $configured = Get-EnvFileValue -Path $HostedConfigPath -Key "STARCOPLAY_CLAUDE_BINARY"
    if ($configured) {
        if (-not (Test-Path -LiteralPath $configured -PathType Leaf)) {
            throw "Configured Claude Code binary does not exist: $configured"
        }
        return (Resolve-Path -LiteralPath $configured).Path
    }

    $command = Get-Command "claude.cmd" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $command -or -not $command.Source) {
        throw "Claude Code executable was not found. Install Claude Code or set STARCOPLAY_CLAUDE_BINARY in $HostedConfigPath"
    }
    return $command.Source
}

function Resolve-ClaudeCliPath {
    param([switch] $Discover)

    $configured = Get-EnvFileValue -Path $HostedConfigPath -Key "STARCOPLAY_CLAUDE_CLI_PATH"
    if ($configured) {
        if (-not (Test-Path -LiteralPath $configured -PathType Leaf)) {
            throw "Configured Claude Code CLI does not exist: $configured"
        }
        return (Resolve-Path -LiteralPath $configured).Path
    }
    if (-not $Discover) {
        return ""
    }

    $npm = Get-Command "npm.cmd" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $npm -or -not $npm.Source) {
        return ""
    }
    $globalRoot = (& $npm.Source root -g 2>$null | Select-Object -Last 1)
    if (-not $globalRoot) {
        return ""
    }
    $candidate = Join-Path ([string] $globalRoot).Trim() "@anthropic-ai\claude-code\cli.js"
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        return ""
    }
    return (Resolve-Path -LiteralPath $candidate).Path
}

function Resolve-ClaudeLaunch {
    $binary = Resolve-ClaudeBinary
    $isCommandWrapper = [IO.Path]::GetExtension($binary) -ieq ".cmd"
    $cliPath = Resolve-ClaudeCliPath -Discover:$isCommandWrapper

    if ($cliPath) {
        if ($isCommandWrapper) {
            $node = Get-Command "node.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($null -eq $node -or -not $node.Source) {
                throw "Claude Code uses a JS CLI, but node.exe was not found"
            }
            $binary = $node.Source
        }
        if (-not (Test-Path -LiteralPath $binary -PathType Leaf)) {
            throw "Claude Code runtime binary does not exist: $binary"
        }
        $binary = (Resolve-Path -LiteralPath $binary).Path
    }

    return [pscustomobject]@{
        Binary = $binary
        CliPath = $cliPath
    }
}

function Assert-ClaudeLaunchCompatible {
    param([Parameter(Mandatory=$true)] $Launch)

    $arguments = @()
    if ($Launch.CliPath) {
        $arguments += $Launch.CliPath
    }
    $arguments += "--version"
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $probe = @(& $Launch.Binary @arguments 2>&1)
        $probeExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($probeExitCode -ne 0) {
        $summary = ($probe | Select-Object -Last 3) -join " | "
        throw "Claude Code launch preflight failed. Configure a compatible Node/CLI pair in $HostedConfigPath. $summary"
    }

    $contractArguments = @()
    if ($Launch.CliPath) {
        $contractArguments += $Launch.CliPath
    }
    $contractArguments += @(
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--strict-mcp-config",
        "--tools", "",
        "--permission-mode", "default",
        "--mcp-config", "__starcoplay_preflight_missing__.json",
        "--append-system-prompt-file", "__starcoplay_preflight_missing__.txt",
        "--allowedTools", "mcp__starcoplay-hosted__*",
        "--help"
    )
    $null = & $Launch.Binary @contractArguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Claude Code is incompatible with the Hosted Runtime command contract"
    }
}

function Ensure-HostedConfig {
    Ensure-Dirs

    $admissionMode = if ($HostedAdmissionMode -eq "AllAuthenticated") {
        "all_authenticated"
    }
    else {
        "allowlist"
    }
    Set-EnvFileValue `
        -Path $HostedConfigPath `
        -Key "STARCOPLAY_HOSTED_RUNTIME_ADMISSION_MODE" `
        -Value $admissionMode

    $userIds = @()
    if ($admissionMode -eq "allowlist") {
        $requestedUserIds = @(
            $HostedUserIds |
                ForEach-Object { ([string] $_).Split(",") } |
                ForEach-Object { $_.Trim() } |
                Where-Object { $_ }
        )
        if ($requestedUserIds.Count -eq 0) {
            $configuredUserIds = Get-EnvFileValue `
                -Path $HostedConfigPath `
                -Key "STARCOPLAY_HOSTED_TEST_USER_IDS"
            if (-not $configuredUserIds) {
                $configuredUserIds = Get-EnvFileValue `
                    -Path $HostedConfigPath `
                    -Key "STARCOPLAY_HOSTED_TEST_USER_ID"
            }
            $requestedUserIds = @(
                ([string] $configuredUserIds).Split(",") |
                    ForEach-Object { $_.Trim() } |
                    Where-Object { $_ }
            )
        }
        foreach ($requestedUserId in $requestedUserIds) {
            $parsedUserId = [Guid]::Empty
            if (-not [Guid]::TryParse($requestedUserId, [ref] $parsedUserId)) {
                throw "HostedUserIds must contain valid user UUIDs"
            }
            $userIds += $requestedUserId
        }
        $userIds = @($userIds | Select-Object -Unique)
        if ($userIds.Count -eq 0) {
            throw "Allowlist mode requires -HostedUserIds <uuid-a>,<uuid-b> on first start"
        }
        Set-EnvFileValue `
            -Path $HostedConfigPath `
            -Key "STARCOPLAY_HOSTED_TEST_USER_IDS" `
            -Value ($userIds -join ",")
    }

    $claimsSecret = Get-EnvFileValue -Path $HostedConfigPath -Key "STARCOPLAY_RUNTIME_CONTROL_CLAIMS_SECRET"
    if (-not $claimsSecret) {
        Set-EnvFileValue -Path $HostedConfigPath -Key "STARCOPLAY_RUNTIME_CONTROL_CLAIMS_SECRET" -Value (New-RandomSecret)
    }
    $relaySecret = Get-EnvFileValue -Path $HostedConfigPath -Key "STARCOPLAY_RUNTIME_LLM_RELAY_SECRET"
    if (-not $relaySecret) {
        Set-EnvFileValue -Path $HostedConfigPath -Key "STARCOPLAY_RUNTIME_LLM_RELAY_SECRET" -Value (New-RandomSecret)
    }

    $claudeLaunch = Resolve-ClaudeLaunch
    Set-EnvFileValue -Path $HostedConfigPath -Key "STARCOPLAY_CLAUDE_BINARY" -Value $claudeLaunch.Binary
    if ($claudeLaunch.CliPath) {
        Set-EnvFileValue -Path $HostedConfigPath -Key "STARCOPLAY_CLAUDE_CLI_PATH" -Value $claudeLaunch.CliPath
    }
    return [pscustomobject]@{
        AdmissionMode = $admissionMode
        UserIds = $userIds
    }
}

function New-HostedEnvScript {
    $hostedConfig = Ensure-HostedConfig
    $claimsSecret = Get-EnvFileValue -Path $HostedConfigPath -Key "STARCOPLAY_RUNTIME_CONTROL_CLAIMS_SECRET"
    $relaySecret = Get-EnvFileValue -Path $HostedConfigPath -Key "STARCOPLAY_RUNTIME_LLM_RELAY_SECRET"
    $claudeLaunch = Resolve-ClaudeLaunch
    if ($claimsSecret.Length -lt 32 -or $relaySecret.Length -lt 32) {
        throw "Hosted Runtime secrets must each contain at least 32 characters"
    }

    $workspaceRoot = Join-Path $LocalDir "runtimes"
    $controlServices = @{
        "local-stack" = @{ all_tenants = $true }
    } | ConvertTo-Json -Depth 4 -Compress
    $controlServicesBase64 = [Convert]::ToBase64String(
        [Text.Encoding]::UTF8.GetBytes($controlServices)
    )
    $hostedValues = [ordered]@{
        STARCOPLAY_RUNTIME_MANAGER_ENABLED = "1"
        HOSTED_GAME_SESSION_ROUTE_ENABLED = "1"
        STARCOPLAY_RUNTIME_MANAGER_WORKSPACE_ROOT = $workspaceRoot
        STARCOPLAY_CLAUDE_BINARY = $claudeLaunch.Binary
        STARCOPLAY_RUNTIME_CONTROL_CLAIMS_SECRET = $claimsSecret
        STARCOPLAY_RUNTIME_LLM_RELAY_SECRET = $relaySecret
        STARCOPLAY_RUNTIME_LLM_RELAY_BASE_URL = "http://127.0.0.1:8766/internal/runtime-llm"
        STARCOPLAY_HOSTED_RUNTIME_ADMISSION_MODE = $hostedConfig.AdmissionMode
        STARCOPLAY_CC_ALLOWLIST = ($hostedConfig.UserIds -join ",")
        STARCOPLAY_LOG_DIR = $LogDir
        PYTHONUTF8 = "1"
        PYTHONIOENCODING = "utf-8"
    }
    if ($claudeLaunch.CliPath) {
        $hostedValues.STARCOPLAY_CLAUDE_CLI_PATH = $claudeLaunch.CliPath
    }
    $lines = foreach ($entry in $hostedValues.GetEnumerator()) {
        $value = ([string] $entry.Value).Replace("'", "''")
        "`$env:$($entry.Key) = '$value'"
    }
    $lines += (
        "`$env:STARCOPLAY_RUNTIME_CONTROL_SERVICES_JSON = " +
        "[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$controlServicesBase64'))"
    )
    return ($lines -join "`r`n")
}

function New-BackendEnvScript {
    param([Parameter(Mandatory=$true)] [string] $BaseEnvScript)

    if ($RuntimeMode -ne "Hosted") {
        return $BaseEnvScript
    }
    $hostedEnvScript = New-HostedEnvScript
    return "$BaseEnvScript`r`n$hostedEnvScript"
}

function Ensure-NewApiEnv {
    if (-not (Test-Path -LiteralPath $NewApiEnvPath)) {
        if (-not (Test-Path -LiteralPath $NewApiEnvExamplePath)) {
            throw "Missing NewAPI env template: $NewApiEnvExamplePath"
        }
        Copy-Item -LiteralPath $NewApiEnvExamplePath -Destination $NewApiEnvPath
        Write-Host "[newapi] created $NewApiEnvPath from .env.example"
    }

    $internalToken = Get-EnvFileValue -Path $NewApiEnvPath -Key "INTERNAL_API_TOKEN"
    if (-not $internalToken) {
        $internalToken = "starcoplay-local-internal-token"
        Set-EnvFileValue -Path $NewApiEnvPath -Key "INTERNAL_API_TOKEN" -Value $internalToken
    }

    $modelTier = Get-EnvFileValue -Path $NewApiEnvPath -Key "AI_RELAY_AGENT_MODEL_TIER"
    if (-not $modelTier) {
        Set-EnvFileValue -Path $NewApiEnvPath -Key "AI_RELAY_AGENT_MODEL_TIER" -Value "balanced"
    }

    $rootUsername = Get-EnvFileValue -Path $NewApiEnvPath -Key "NEWAPI_ROOT_USERNAME"
    if (-not $rootUsername) {
        Set-EnvFileValue -Path $NewApiEnvPath -Key "NEWAPI_ROOT_USERNAME" -Value "root"
    }

    $rootPassword = Get-EnvFileValue -Path $NewApiEnvPath -Key "NEWAPI_ROOT_PASSWORD"
    if (-not $rootPassword) {
        Set-EnvFileValue -Path $NewApiEnvPath -Key "NEWAPI_ROOT_PASSWORD" -Value "starcoplay-local-root-2026"
    }
}

function Get-NewApiBaseUrl {
    $port = Get-EnvFileValue -Path $NewApiEnvPath -Key "NEWAPI_HOST_PORT"
    if (-not $port) {
        $port = "13000"
    }
    return "http://127.0.0.1:$port"
}

function Invoke-JsonRequest {
    param(
        [string] $Method,
        [string] $Uri,
        [object] $Body = $null,
        [hashtable] $Headers = @{},
        [string] $SessionVariable = "",
        [object] $WebSession = $null
    )
    $args = @{
        Method = $Method
        Uri = $Uri
        Headers = $Headers
        TimeoutSec = 10
        UseBasicParsing = $true
    }
    if ($null -ne $Body) {
        $args["ContentType"] = "application/json"
        $args["Body"] = ($Body | ConvertTo-Json -Depth 12 -Compress)
    }
    if ($SessionVariable) {
        $args["SessionVariable"] = $SessionVariable
    }
    if ($null -ne $WebSession) {
        $args["WebSession"] = $WebSession
    }
    $resp = Invoke-WebRequest @args
    if (-not $resp.Content) {
        return $null
    }
    return $resp.Content | ConvertFrom-Json
}

function Test-NewApiReady {
    Ensure-NewApiEnv
    $baseUrl = Get-NewApiBaseUrl
    try {
        $resp = Invoke-WebRequest -Uri "$baseUrl/api/status" -Method GET -TimeoutSec 2 -UseBasicParsing
        return ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500)
    }
    catch {
        return $false
    }
}

function Wait-NewApiReady {
    param([int] $TimeoutSeconds = 90)
    $baseUrl = Get-NewApiBaseUrl
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-NewApiReady) {
            Write-Host "[newapi] ready: $baseUrl"
            return $true
        }
        Start-Sleep -Seconds 2
    }
    Write-Warning "[newapi] not ready within ${TimeoutSeconds}s: $baseUrl"
    return $false
}

function Start-NewApi {
    Ensure-NewApiEnv
    if (Test-NewApiReady) {
        Write-Host "[newapi] already running; reusing local NewAPI"
        return
    }

    Set-Content -LiteralPath $NewApiLogPath -Encoding UTF8 -Value "===== $(Get-NowIso) START newapi ====="
    Set-Content -LiteralPath $NewApiErrLogPath -Encoding UTF8 -Value "===== $(Get-NowIso) START newapi STDERR ====="

    $proc = Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-Command",
            "Set-Location -LiteralPath '$NewApiDir'; docker compose up -d"
        ) `
        -WorkingDirectory $NewApiDir `
        -RedirectStandardOutput $NewApiLogPath `
        -RedirectStandardError $NewApiErrLogPath `
        -Wait `
        -PassThru `
        -WindowStyle Hidden
    if ($proc.ExitCode -ne 0) {
        throw "NewAPI failed to start. See logs: $NewApiLogPath ; $NewApiErrLogPath"
    }
    if (-not (Wait-NewApiReady -TimeoutSeconds 120)) {
        throw "NewAPI did not become ready. See docker logs for star-coplay-newapi."
    }
}

function Ensure-NewApiSetup {
    Ensure-NewApiEnv
    $baseUrl = Get-NewApiBaseUrl
    $rootUsername = Get-EnvFileValue -Path $NewApiEnvPath -Key "NEWAPI_ROOT_USERNAME"
    $rootPassword = Get-EnvFileValue -Path $NewApiEnvPath -Key "NEWAPI_ROOT_PASSWORD"
    if (-not $rootUsername) {
        $rootUsername = "root"
    }
    if (-not $rootPassword) {
        $rootPassword = "starcoplay-local-root-2026"
    }

    try {
        $setupResp = Invoke-WebRequest `
            -Method "GET" `
            -Uri "$baseUrl/api/setup" `
            -TimeoutSec 10 `
            -UseBasicParsing
        $setup = $setupResp.Content | ConvertFrom-Json
        if ($setup.success -and $setup.data -and $setup.data.status) {
            return
        }

        $body = @{
            username = $rootUsername
            password = $rootPassword
            confirmPassword = $rootPassword
            SelfUseModeEnabled = $true
            DemoSiteEnabled = $false
        }
        $initResp = Invoke-WebRequest `
            -Method "POST" `
            -Uri "$baseUrl/api/setup" `
            -ContentType "application/json" `
            -Body ($body | ConvertTo-Json -Compress) `
            -TimeoutSec 10 `
            -UseBasicParsing
        $init = $initResp.Content | ConvertFrom-Json
        if (-not $init.success) {
            if ([string]$init.message -match "已经初始化|already initialized") {
                return
            }
            throw "NewAPI setup returned success=false: $($init.message)"
        }
        Write-Host "[newapi] initialized local root user '$rootUsername'"
    }
    catch {
        throw "NewAPI setup failed: $($_.Exception.Message)"
    }
}

function Ensure-NewApiAdminAccessToken {
    Ensure-NewApiEnv
    $existing = Get-EnvFileValue -Path $NewApiEnvPath -Key "NEWAPI_ADMIN_ACCESS_TOKEN"
    if ($existing -and $existing -notmatch "^(your-|change_me)") {
        return $existing
    }

    $baseUrl = Get-NewApiBaseUrl
    Ensure-NewApiSetup
    $rootUsername = Get-EnvFileValue -Path $NewApiEnvPath -Key "NEWAPI_ROOT_USERNAME"
    $rootPassword = Get-EnvFileValue -Path $NewApiEnvPath -Key "NEWAPI_ROOT_PASSWORD"
    if (-not $rootUsername) {
        $rootUsername = "root"
    }
    if (-not $rootPassword) {
        $rootPassword = "starcoplay-local-root-2026"
    }
    try {
        $loginResp = Invoke-WebRequest `
            -Method "POST" `
            -Uri "$baseUrl/api/user/login" `
            -ContentType "application/json" `
            -Body (@{ username = $rootUsername; password = $rootPassword } | ConvertTo-Json -Compress) `
            -SessionVariable newApiSession `
            -TimeoutSec 10 `
            -UseBasicParsing
        $login = $loginResp.Content | ConvertFrom-Json
        if (-not $login.success) {
            throw "root login returned success=false: $($login.message)"
        }
        $tokenHttpResp = Invoke-WebRequest `
            -Method "GET" `
            -Uri "$baseUrl/api/user/token" `
            -Headers @{ "New-Api-User" = "1" } `
            -WebSession $newApiSession `
            -TimeoutSec 10 `
            -UseBasicParsing
        $tokenResp = $tokenHttpResp.Content | ConvertFrom-Json
        if (-not $tokenResp.success -or -not $tokenResp.data) {
            throw "token generation returned success=false: $($tokenResp.message)"
        }
        $token = [string] $tokenResp.data
        Set-EnvFileValue -Path $NewApiEnvPath -Key "NEWAPI_ADMIN_ACCESS_TOKEN" -Value $token
        Set-EnvFileValue -Path $NewApiEnvPath -Key "NEWAPI_ADMIN_USER_ID" -Value "1"
        Write-Host "[newapi] generated root access token and saved it to deploy\newapi\.env"
        return $token
    }
    catch {
        throw @"
NewAPI admin access token is required but could not be generated automatically.
Open $baseUrl, log in with $rootUsername / <deploy\newapi\.env NEWAPI_ROOT_PASSWORD>, generate an access token, then set:
  deploy\newapi\.env -> NEWAPI_ADMIN_ACCESS_TOKEN=<token>
Original error: $($_.Exception.Message)
"@
    }
}

function Get-SupabaseStatus {
    $outPath = Join-Path $LogDir "supabase-status.log"
    $errPath = Join-Path $LogDir "supabase-status.err.log"
    Set-Content -LiteralPath $outPath -Encoding UTF8 -Value "===== $(Get-NowIso) supabase status ====="
    Set-Content -LiteralPath $errPath -Encoding UTF8 -Value "===== $(Get-NowIso) supabase status STDERR ====="
    $proc = Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-Command",
            "Set-Location -LiteralPath '$PlatformDir'; npx supabase status"
        ) `
        -WorkingDirectory $PlatformDir `
        -RedirectStandardOutput $outPath `
        -RedirectStandardError $errPath `
        -Wait `
        -PassThru `
        -WindowStyle Hidden
    if ($proc.ExitCode -ne 0) {
        return @{ ok = $false; out = $outPath; err = $errPath; values = @{} }
    }

    $values = @{}
    foreach ($line in Get-Content -LiteralPath $outPath -ErrorAction SilentlyContinue) {
        $text = [string] $line
        if ($text -match '^\s*([^:]+?)\s*:\s*(.+?)\s*$') {
            $key = $Matches[1].Trim()
            $value = $Matches[2].Trim()
            $values[$key] = $value
        }
    }
    return @{ ok = $true; out = $outPath; err = $errPath; values = $values }
}

function New-LocalEnvScript {
    $adminSecret = Get-EnvFileValue -Path (Join-Path $PlatformDir "supabase\.env") -Key "ADMIN_SECRET"
    $newApiBaseUrl = Get-NewApiBaseUrl
    $newApiAdminToken = Ensure-NewApiAdminAccessToken
    $newApiAdminUserId = Get-EnvFileValue -Path $NewApiEnvPath -Key "NEWAPI_ADMIN_USER_ID"
    $internalToken = Get-EnvFileValue -Path $NewApiEnvPath -Key "INTERNAL_API_TOKEN"
    $agentModelTier = Get-EnvFileValue -Path $NewApiEnvPath -Key "AI_RELAY_AGENT_MODEL_TIER"
    $authBypass = ([string] $env:STARCOPLAY_WS_AUTH_BYPASS).Trim()
    if (-not $authBypass) {
        $authBypass = Get-EnvFileValue -Path (Join-Path $PlatformDir "supabase\.env") -Key "STARCOPLAY_WS_AUTH_BYPASS"
    }
    $authBypassUserId = ([string] $env:STARCOPLAY_WS_AUTH_BYPASS_USER_ID).Trim()
    if (-not $authBypassUserId) {
        $authBypassUserId = Get-EnvFileValue -Path (Join-Path $PlatformDir "supabase\.env") -Key "STARCOPLAY_WS_AUTH_BYPASS_USER_ID"
    }
    $status = Get-SupabaseStatus
    if (-not $status.ok) {
        throw "npx supabase status failed. See $($status.out) and $($status.err)"
    }
    $values = $status.values
    $dbUrl = [string] $values["DB URL"]
    $apiUrl = [string] $values["API URL"]
    $anonKey = [string] $values["anon key"]
    $serviceKey = [string] $values["service_role key"]

    if (-not $dbUrl) {
        $dbUrl = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    }
    if (-not $apiUrl) {
        $apiUrl = "http://127.0.0.1:54321"
    }
    if (-not $adminSecret) {
        $adminSecret = "change-me-admin-secret"
    }
    if (-not $newApiAdminUserId) {
        $newApiAdminUserId = "1"
    }
    if (-not $internalToken) {
        $internalToken = "starcoplay-local-internal-token"
    }
    if (-not $agentModelTier) {
        $agentModelTier = "balanced"
    }

    $lines = @(
        "`$env:STARCOPLAY_LOCAL_STACK = '1'",
        "`$env:STARCOPLAY_PROTOCOL_TRANSPORT = '1'",
        "`$env:NEXT_TELEMETRY_DISABLED = '1'",
        "`$env:STARCOPLAY_PLATFORM_URL = 'http://127.0.0.1:3000'",
        "`$env:WS_GATEWAY_HOST = '127.0.0.1'",
        "`$env:WS_HTTP_HOST = '127.0.0.1'",
        "`$env:WS_HTTP_PORT = '8766'",
        "`$env:STARCOPLAY_AGENT_LOG = '$($GatewayLogPath.Replace("'", "''"))'",
        "`$env:DATABASE_URL = '$($dbUrl.Replace("'", "''"))'",
        "`$env:SUPABASE_URL = '$($apiUrl.Replace("'", "''"))'",
        "`$env:NEXT_PUBLIC_SUPABASE_URL = '$($apiUrl.Replace("'", "''"))'",
        "`$env:ADMIN_SECRET = '$($adminSecret.Replace("'", "''"))'",
        "`$env:STARCOPLAY_CONFIG_ENV = 'dev'",
        "`$env:INTERNAL_API_TOKEN = '$($internalToken.Replace("'", "''"))'",
        "`$env:NEWAPI_BASE_URL = '$($newApiBaseUrl.Replace("'", "''"))'",
        "`$env:NEWAPI_ADMIN_ACCESS_TOKEN = '$($newApiAdminToken.Replace("'", "''"))'",
        "`$env:NEWAPI_ADMIN_USER_ID = '$($newApiAdminUserId.Replace("'", "''"))'",
        "`$env:AI_RELAY_AGENT_MODEL_TIER = '$($agentModelTier.Replace("'", "''"))'",
        "`$env:PLATFORM_WS_HTTP_URL = 'http://127.0.0.1:8766'",
        "`$env:PLATFORM_BACKEND_API_URL = 'http://127.0.0.1:8766'"
    )
    if ($anonKey) {
        $lines += "`$env:NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY = '$($anonKey.Replace("'", "''"))'"
    }
    if ($serviceKey) {
        $lines += "`$env:SUPABASE_SERVICE_ROLE_KEY = '$($serviceKey.Replace("'", "''"))'"
    }
    if ($authBypass -match "^(1|true|yes)$") {
        if (-not $authBypassUserId) {
            throw "STARCOPLAY_WS_AUTH_BYPASS_USER_ID is required when STARCOPLAY_WS_AUTH_BYPASS is enabled"
        }
        $lines += "`$env:STARCOPLAY_WS_AUTH_BYPASS = '$authBypass'"
        $lines += "`$env:STARCOPLAY_WS_AUTH_BYPASS_USER_ID = '$($authBypassUserId.Replace("'", "''"))'"
    }
    return ($lines -join "`r`n")
}

function Test-SupabaseReady {
    $status = Get-SupabaseStatus
    return [bool] $status.ok
}

function Invoke-SupabaseMigrationUp {
    Set-Content -LiteralPath $SupabaseMigrationLogPath -Encoding UTF8 -Value "===== $(Get-NowIso) supabase migration up ====="
    Set-Content -LiteralPath $SupabaseMigrationErrLogPath -Encoding UTF8 -Value "===== $(Get-NowIso) supabase migration up STDERR ====="
    $proc = Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-Command",
            "Set-Location -LiteralPath '$PlatformDir'; npx supabase migration up --local"
        ) `
        -WorkingDirectory $PlatformDir `
        -RedirectStandardOutput $SupabaseMigrationLogPath `
        -RedirectStandardError $SupabaseMigrationErrLogPath `
        -Wait `
        -PassThru `
        -WindowStyle Hidden
    if ($proc.ExitCode -ne 0) {
        Write-Warning "[supabase] migration up failed. See $SupabaseMigrationLogPath ; $SupabaseMigrationErrLogPath"
    }
    else {
        Write-Host "[supabase] migrations are up to date"
    }
}

function Stop-PidFile {
    param(
        [string] $Name,
        [string] $Path,
        [int] $GraceSeconds = 5
    )
    $pidValue = Read-PidFile $Path
    if ($null -eq $pidValue) {
        Write-Host "[$Name] no pid file"
        return
    }
    if (-not (Test-PidRunning $pidValue)) {
        Write-Host "[$Name] pid $pidValue is not running"
        Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
        return
    }

    Write-Host "[$Name] stopping pid $pidValue"
    Stop-ProcessTree -RootPid $pidValue -GraceSeconds $GraceSeconds

    $deadline = (Get-Date).AddSeconds($GraceSeconds)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-PidRunning $pidValue)) {
            break
        }
        Start-Sleep -Milliseconds 200
    }

    if (Test-PidRunning $pidValue) {
        Write-Warning "[$Name] pid $pidValue still running; forcing kill"
        Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
}

function Get-ChildProcessIds {
    param([int] $ParentPid)
    $children = @()
    try {
        $items = Get-CimInstance Win32_Process -Filter "ParentProcessId=$ParentPid" -ErrorAction Stop
    }
    catch {
        try {
            $items = Get-WmiObject Win32_Process -Filter "ParentProcessId=$ParentPid" -ErrorAction Stop
        }
        catch {
            $items = @()
        }
    }
    foreach ($item in $items) {
        $childPid = [int] $item.ProcessId
        $children += $childPid
        $children += Get-ChildProcessIds -ParentPid $childPid
    }
    return $children
}

function Stop-ProcessTree {
    param(
        [int] $RootPid,
        [int] $GraceSeconds = 5
    )
    $childPids = @(Get-ChildProcessIds -ParentPid $RootPid)
    if ($childPids.Count -eq 0) {
        # WMI/CIM process enumeration can be denied in managed Windows shells.
        # taskkill still follows the exact PID-owned tree and prevents orphaned
        # Node/Python children from retaining ports and redirected log handles.
        & taskkill.exe /PID $RootPid /T /F 2>$null | Out-Null
        return
    }

    $pids = $childPids + @($RootPid)
    $pids = $pids | Select-Object -Unique
    foreach ($pidValue in $pids) {
        if (Test-PidRunning $pidValue) {
            Stop-Process -Id $pidValue -ErrorAction SilentlyContinue
        }
    }
    $deadline = (Get-Date).AddSeconds($GraceSeconds)
    while ((Get-Date) -lt $deadline) {
        $stillRunning = @($pids | Where-Object { Test-PidRunning $_ })
        if ($stillRunning.Count -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 200
    }
    foreach ($pidValue in $pids) {
        if (Test-PidRunning $pidValue) {
            Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
        }
    }
}

function Start-LoggedProcess {
    param(
        [Parameter(Mandatory=$true)] [string] $Name,
        [Parameter(Mandatory=$true)] [string] $WorkDir,
        [Parameter(Mandatory=$true)] [string] $CommandLine,
        [Parameter(Mandatory=$true)] [string] $PidPath,
        [Parameter(Mandatory=$true)] [string] $LogPath,
        [Parameter(Mandatory=$true)] [string] $ErrLogPath,
        [string] $EnvScript = "",
        [switch] $Wait
    )

    $existingPid = Read-PidFile $PidPath
    if ((-not $Wait) -and (Test-PidRunning $existingPid)) {
        Write-Host "[$Name] already running pid $existingPid"
        return $existingPid
    }

    $stamp = Get-NowIso
    Set-Content -LiteralPath $LogPath -Encoding UTF8 -Value @(
        "===== $stamp START $Name =====",
        "cwd=$WorkDir",
        "cmd=$CommandLine"
    )
    Set-Content -LiteralPath $ErrLogPath -Encoding UTF8 -Value "===== $stamp START $Name STDERR ====="

    $safeWorkDir = $WorkDir.Replace("'", "''")
    $safeGatewayLogPath = $GatewayLogPath.Replace("'", "''")
    $psCommand = @"
`$ErrorActionPreference = 'Continue'
Set-Location -LiteralPath '$safeWorkDir'
$EnvScript
& {
    $CommandLine
}
if (`$null -ne `$global:LASTEXITCODE) {
    exit `$global:LASTEXITCODE
}
if (`$?) {
    exit 0
}
exit 1
"@

    $args = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-Command", $psCommand
    )

    $startInfo = @{
        FilePath = "powershell.exe"
        ArgumentList = $args
        WorkingDirectory = $WorkDir
        RedirectStandardOutput = $LogPath
        RedirectStandardError = $ErrLogPath
        PassThru = $true
    }
    if ($Visible) {
        $startInfo["WindowStyle"] = "Normal"
    }
    else {
        $startInfo["WindowStyle"] = "Hidden"
    }
    if ($Wait) {
        $startInfo["Wait"] = $true
    }

    $proc = Start-Process @startInfo
    if ($null -ne $proc) {
        Set-Content -LiteralPath $PidPath -Value $proc.Id -Encoding ASCII
        Write-Host "[$Name] started pid $($proc.Id), log=$LogPath"
        if ($Wait) {
            Write-Host "[$Name] exited code $($proc.ExitCode)"
            Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
            if ($proc.ExitCode -ne 0) {
                Write-Warning "[$Name] exited non-zero. See logs: $LogPath ; $ErrLogPath"
            }
            return $proc.ExitCode
        }
        return $proc.Id
    }
    return $null
}

function Wait-HttpReady {
    param(
        [string] $Name,
        [string] $Url,
        [int] $TimeoutSeconds = 45
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -Method GET -TimeoutSec 2 -UseBasicParsing
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500) {
                Write-Host "[$Name] ready: $Url"
                return $true
            }
        }
        catch {
            Start-Sleep -Seconds 1
        }
    }
    Write-Warning "[$Name] not ready within ${TimeoutSeconds}s: $Url"
    return $false
}

function Show-Status {
    Ensure-Dirs
    $webPid = Read-PidFile $WebPidPath
    $gatewayPid = Read-PidFile $GatewayPidPath
    $supabasePid = Read-PidFile $SupabaseStartPidPath
    $webRunning = Test-PidRunning $webPid
    $gatewayRunning = Test-PidRunning $gatewayPid
    $supabaseStartRunning = Test-PidRunning $supabasePid

    Write-Host "Repo: $RepoRoot"
    Write-Host "State: $StatePath"
    $recordedMode = "unknown"
    $recordedAdmissionMode = "n/a"
    if (Test-Path -LiteralPath $StatePath) {
        try {
            $recordedState = Get-Content -Raw -LiteralPath $StatePath | ConvertFrom-Json
            if ($recordedState.runtime_mode) {
                $recordedMode = [string] $recordedState.runtime_mode
            }
            if ($recordedState.hosted_admission_mode) {
                $recordedAdmissionMode = [string] $recordedState.hosted_admission_mode
            }
        }
        catch {
            $recordedMode = "invalid-state"
        }
    }
    Write-Host "Runtime mode: $recordedMode"
    Write-Host "Hosted admission: $recordedAdmissionMode"
    Write-Host ""
    $newApiRunning = Test-NewApiReady

    Write-Host ("newapi      : running={0} url=http://127.0.0.1:13000" -f $newApiRunning)
    Write-Host ("platform-web: pid={0} running={1} url=http://127.0.0.1:3000" -f (Format-Pid $webPid), $webRunning)
    Write-Host ("backend     : pid={0} running={1} ws=ws://127.0.0.1:8765/ws http=http://127.0.0.1:8766" -f (Format-Pid $gatewayPid), $gatewayRunning)
    Write-Host ("supabase cmd: pid={0} running={1} studio=http://127.0.0.1:54323" -f (Format-Pid $supabasePid), $supabaseStartRunning)
    Write-Host ""
    Write-Host "Logs:"
    Write-Host "  supabase : $SupabaseLogPath"
    Write-Host "  supabase!: $SupabaseErrLogPath"
    Write-Host "  web      : $WebLogPath"
    Write-Host "  web!     : $WebErrLogPath"
    Write-Host "  newapi   : $NewApiLogPath"
    Write-Host "  newapi!  : $NewApiErrLogPath"
    Write-Host "  backend  : $GatewayLogPath"
    Write-Host "  backend! : $GatewayErrLogPath"
    if (Test-Path -LiteralPath $StatePath) {
        Write-Host ""
        Write-Host "State file exists."
    }
}

function Start-Stack {
    Ensure-Dirs
    Write-Host "Repo root: $RepoRoot"
    Write-Host "Logs:      $LogDir"
    Write-Host "Run:       $RunDir"
    Write-Host ""

    if ($RuntimeMode -eq "Hosted") {
        $claudeLaunch = Resolve-ClaudeLaunch
        Assert-ClaudeLaunchCompatible -Launch $claudeLaunch
        Write-Host "[runtime] Claude Code launch preflight passed"
    }

    Start-NewApi

    if (Test-SupabaseReady) {
        Write-Host "[supabase] already running; reusing local database"
    }
    else {
        Write-Host "[supabase] starting local Supabase..."
        $supabaseExit = Start-LoggedProcess `
            -Name "supabase" `
            -WorkDir $PlatformDir `
            -CommandLine "npx supabase start" `
            -PidPath $SupabaseStartPidPath `
            -LogPath $SupabaseLogPath `
            -ErrLogPath $SupabaseErrLogPath `
            -Wait
        if ($supabaseExit -ne 0) {
            Write-State @{ start_failed = "supabase"; stopped_at = Get-NowIso }
            throw "Supabase failed to start. See logs: $SupabaseLogPath ; $SupabaseErrLogPath"
        }
    }
    Invoke-SupabaseMigrationUp

    $localEnvScript = New-LocalEnvScript
    $backendEnvScript = New-BackendEnvScript -BaseEnvScript $localEnvScript
    if ($RuntimeMode -eq "Hosted") {
        if ($HostedAdmissionMode -eq "AllAuthenticated") {
            Write-Host "[runtime] Hosted mode enabled for all authenticated tenants"
        }
        else {
            Write-Host "[runtime] Hosted mode enabled for the configured tenant allowlist"
        }
    }
    else {
        Write-Host "[runtime] Legacy mode enabled"
    }

    Start-LoggedProcess `
        -Name "platform-web" `
        -WorkDir $WebDir `
        -CommandLine "npm run dev" `
        -PidPath $WebPidPath `
        -LogPath $WebLogPath `
        -ErrLogPath $WebErrLogPath `
        -EnvScript $localEnvScript | Out-Null

    Start-LoggedProcess `
        -Name "platform-backend" `
        -WorkDir $BackendDir `
        -CommandLine "python -m app" `
        -PidPath $GatewayPidPath `
        -LogPath $GatewayLogPath `
        -ErrLogPath $GatewayErrLogPath `
        -EnvScript $backendEnvScript | Out-Null

    Write-State @{
        runtime_mode = $RuntimeMode.ToLowerInvariant()
        hosted_admission_mode = if ($RuntimeMode -eq "Hosted") {
            if ($HostedAdmissionMode -eq "AllAuthenticated") { "all_authenticated" } else { "allowlist" }
        }
        else { "n/a" }
    }
    Write-Host ""
    Wait-HttpReady -Name "platform-web" -Url "http://127.0.0.1:3000" -TimeoutSeconds 60 | Out-Null
    $adminSecret = Get-EnvFileValue -Path (Join-Path $PlatformDir "supabase\.env") -Key "ADMIN_SECRET"
    if ($adminSecret) {
        Wait-HttpReady -Name "ws-gateway-http" -Url "http://127.0.0.1:8766/api/v1/admin/gateway/status?admin_secret=$adminSecret" -TimeoutSeconds 45 | Out-Null
    }
    else {
        Write-Warning "ADMIN_SECRET not found in platform\supabase\.env; skipping gateway readiness check."
    }
    Write-Host ""
    Write-Host "Local stack started."
    Write-Host "  Platform Web:     http://127.0.0.1:3000"
    Write-Host "  NewAPI:           http://127.0.0.1:13000"
    Write-Host "  Supabase Studio:  http://127.0.0.1:54323"
    Write-Host "  WS Gateway:       ws://127.0.0.1:8765/ws"
    Write-Host "  Gateway HTTP:     http://127.0.0.1:8766"
    Write-Host "  Runtime Mode:     $RuntimeMode"
    if ($RuntimeMode -eq "Hosted") {
        Write-Host "  Tenant Admission: $(if ($HostedAdmissionMode -eq 'AllAuthenticated') { 'all_authenticated' } else { 'allowlist' })"
    }
    Write-Host ""
    Write-Host "Use this script to stop it:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\start_local_stack.ps1 stop"
}

function Stop-Stack {
    Ensure-Dirs
    Stop-PidFile -Name "platform-backend" -Path $GatewayPidPath -GraceSeconds 5
    Stop-PidFile -Name "platform-web" -Path $WebPidPath -GraceSeconds 5

    if ($KeepSupabase) {
        Write-Host "[supabase] kept running because -KeepSupabase was set"
    }
    else {
        Write-Host "[supabase] stopping local Supabase..."
        Add-Content -LiteralPath $SupabaseStopLogPath -Encoding UTF8 -Value ""
        Add-Content -LiteralPath $SupabaseStopLogPath -Encoding UTF8 -Value "===== $(Get-NowIso) STOP supabase ====="
        Add-Content -LiteralPath $SupabaseStopErrLogPath -Encoding UTF8 -Value ""
        Add-Content -LiteralPath $SupabaseStopErrLogPath -Encoding UTF8 -Value "===== $(Get-NowIso) STOP supabase STDERR ====="
        $stopProc = Start-Process `
            -FilePath "powershell.exe" `
            -ArgumentList @(
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-Command",
                "Set-Location -LiteralPath '$PlatformDir'; npx supabase stop >> '$SupabaseStopLogPath' 2>> '$SupabaseStopErrLogPath'"
            ) `
            -WorkingDirectory $PlatformDir `
            -Wait `
            -PassThru `
            -WindowStyle Hidden
        Write-Host "[supabase] stop exited code $($stopProc.ExitCode), logs=$SupabaseStopLogPath ; $SupabaseStopErrLogPath"
    }

    if ($KeepNewApi) {
        Write-Host "[newapi] kept running because -KeepNewApi was set"
    }
    else {
        Write-Host "[newapi] stopping local NewAPI..."
        Add-Content -LiteralPath $NewApiStopLogPath -Encoding UTF8 -Value ""
        Add-Content -LiteralPath $NewApiStopLogPath -Encoding UTF8 -Value "===== $(Get-NowIso) STOP newapi ====="
        Add-Content -LiteralPath $NewApiStopErrLogPath -Encoding UTF8 -Value ""
        Add-Content -LiteralPath $NewApiStopErrLogPath -Encoding UTF8 -Value "===== $(Get-NowIso) STOP newapi STDERR ====="
        if (Test-Path -LiteralPath $NewApiDir) {
            $stopProc = Start-Process `
                -FilePath "powershell.exe" `
                -ArgumentList @(
                    "-NoProfile",
                    "-ExecutionPolicy", "Bypass",
                    "-Command",
                    "Set-Location -LiteralPath '$NewApiDir'; docker compose down >> '$NewApiStopLogPath' 2>> '$NewApiStopErrLogPath'"
                ) `
                -WorkingDirectory $NewApiDir `
                -Wait `
                -PassThru `
                -WindowStyle Hidden
            Write-Host "[newapi] stop exited code $($stopProc.ExitCode), logs=$NewApiStopLogPath ; $NewApiStopErrLogPath"
        }
    }

    Remove-Item -LiteralPath $SupabaseStartPidPath -Force -ErrorAction SilentlyContinue
    Write-State @{ stopped_at = Get-NowIso }
    Write-Host "Local stack stopped."
}

function Show-Logs {
    Ensure-Dirs
    $paths = @()
    if ($Service -eq "all" -or $Service -eq "supabase") { $paths += $SupabaseLogPath }
    if ($Service -eq "all" -or $Service -eq "supabase") { $paths += $SupabaseErrLogPath }
    if ($Service -eq "all" -or $Service -eq "newapi") { $paths += $NewApiLogPath }
    if ($Service -eq "all" -or $Service -eq "newapi") { $paths += $NewApiErrLogPath }
    if ($Service -eq "all" -or $Service -eq "web") { $paths += $WebLogPath }
    if ($Service -eq "all" -or $Service -eq "web") { $paths += $WebErrLogPath }
    if ($Service -eq "all" -or $Service -eq "gateway" -or $Service -eq "backend") { $paths += $GatewayLogPath }
    if ($Service -eq "all" -or $Service -eq "gateway" -or $Service -eq "backend") { $paths += $GatewayErrLogPath }

    foreach ($path in $paths) {
        Write-Host ""
        Write-Host "===== $path ====="
        if (-not (Test-Path -LiteralPath $path)) {
            Write-Host "(missing)"
            continue
        }
        if ($Follow -and $paths.Count -eq 1) {
            Get-Content -LiteralPath $path -Tail 120 -Wait
        }
        else {
            Get-Content -LiteralPath $path -Tail 120
        }
    }
    if ($Follow -and $paths.Count -gt 1) {
        Write-Warning "-Follow only follows when one -Service is selected."
    }
}

if ($MyInvocation.InvocationName -ne ".") {
    switch ($Command) {
        "start" { Start-Stack }
        "stop" { Stop-Stack }
        "status" { Show-Status }
        "restart" {
            Stop-Stack
            Start-Stack
        }
        "logs" { Show-Logs }
    }
}
