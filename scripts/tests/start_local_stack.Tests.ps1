$scriptPath = Join-Path $PSScriptRoot "..\start_local_stack.ps1"
. $scriptPath

Describe "local stack Hosted Runtime profile" {
    BeforeEach {
        $script:LocalDir = Join-Path $TestDrive "local"
        $script:RunDir = Join-Path $script:LocalDir "run"
        $script:LogDir = Join-Path $script:LocalDir "logs"
        $script:HostedConfigPath = Join-Path $script:LocalDir "hosted-runtime.env"
        $script:HostedAdmissionMode = "AllAuthenticated"
        $script:HostedUserIds = @()
        $script:RuntimeMode = "Legacy"
        New-Item -ItemType Directory -Force -Path $script:LocalDir | Out-Null

        $claude = Join-Path $TestDrive "claude.exe"
        Set-Content -LiteralPath $claude -Encoding ASCII -Value "@exit /b 0"
        Set-EnvFileValue -Path $script:HostedConfigPath -Key "STARCOPLAY_CLAUDE_BINARY" -Value $claude
    }

    It "keeps the default Legacy backend environment unchanged" {
        New-BackendEnvScript -BaseEnvScript "`$env:BASE = '1'" | Should Be "`$env:BASE = '1'"
        Test-Path -LiteralPath (Join-Path $script:LocalDir "runtimes") | Should Be $false
    }

    It "creates the Hosted audit parent required by the durable sink" {
        Ensure-Dirs
        Test-Path -LiteralPath (Join-Path $script:LogDir "audit") -PathType Container | Should Be $true
    }

    It "renders a complete Hosted profile with valid control service JSON" {
        $script:RuntimeMode = "Hosted"
        $rendered = New-BackendEnvScript -BaseEnvScript "`$env:BASE = '1'"

        $rendered | Should Match "STARCOPLAY_RUNTIME_MANAGER_ENABLED = '1'"
        $rendered | Should Match "HOSTED_GAME_SESSION_ROUTE_ENABLED = '1'"
        $rendered | Should Match "STARCOPLAY_HOSTED_RUNTIME_ADMISSION_MODE = 'all_authenticated'"
        $rendered | Should Match "STARCOPLAY_CC_ALLOWLIST = ''"
        try {
            Invoke-Expression $rendered
            $services = $env:STARCOPLAY_RUNTIME_CONTROL_SERVICES_JSON | ConvertFrom-Json
            $services.'local-stack'.all_tenants | Should Be $true
        }
        finally {
            Remove-Item Env:STARCOPLAY_RUNTIME_CONTROL_SERVICES_JSON -ErrorAction SilentlyContinue
        }
    }

    It "creates separate stable secrets and reuses them" {
        $null = Ensure-HostedConfig
        $claimsFirst = Get-EnvFileValue -Path $script:HostedConfigPath -Key "STARCOPLAY_RUNTIME_CONTROL_CLAIMS_SECRET"
        $relayFirst = Get-EnvFileValue -Path $script:HostedConfigPath -Key "STARCOPLAY_RUNTIME_LLM_RELAY_SECRET"
        $null = Ensure-HostedConfig

        $claimsFirst.Length -ge 32 | Should Be $true
        $relayFirst.Length -ge 32 | Should Be $true
        $claimsFirst | Should Not Be $relayFirst
        Get-EnvFileValue -Path $script:HostedConfigPath -Key "STARCOPLAY_RUNTIME_CONTROL_CLAIMS_SECRET" | Should Be $claimsFirst
        Get-EnvFileValue -Path $script:HostedConfigPath -Key "STARCOPLAY_RUNTIME_LLM_RELAY_SECRET" | Should Be $relayFirst
    }

    It "renders an explicit Node and Claude CLI pair when configured" {
        $script:RuntimeMode = "Hosted"
        $node = Join-Path $TestDrive "node.exe"
        $cli = Join-Path $TestDrive "cli.js"
        Set-Content -LiteralPath $node -Encoding ASCII -Value "node"
        Set-Content -LiteralPath $cli -Encoding ASCII -Value "cli"
        Set-EnvFileValue -Path $script:HostedConfigPath -Key "STARCOPLAY_CLAUDE_BINARY" -Value $node
        Set-EnvFileValue -Path $script:HostedConfigPath -Key "STARCOPLAY_CLAUDE_CLI_PATH" -Value $cli

        $rendered = New-HostedEnvScript

        $rendered | Should Match "STARCOPLAY_CLAUDE_BINARY = '$([regex]::Escape($node))'"
        $rendered | Should Match "STARCOPLAY_CLAUDE_CLI_PATH = '$([regex]::Escape($cli))'"
    }

    It "renders multiple allowlisted tenants when rollout mode is requested" {
        $script:HostedAdmissionMode = "Allowlist"
        $script:HostedUserIds = @(
            "174c1ad2-34cc-4d79-8335-6aced0718351",
            "00f223b5-9a64-4390-b4f5-d59bc4151cb0"
        )

        $rendered = New-HostedEnvScript

        $rendered | Should Match "STARCOPLAY_HOSTED_RUNTIME_ADMISSION_MODE = 'allowlist'"
        $rendered | Should Match "STARCOPLAY_CC_ALLOWLIST = '$($script:HostedUserIds -join ',')'"
    }

    It "rejects an invalid Hosted allowlist user before launch" {
        $script:HostedAdmissionMode = "Allowlist"
        $script:HostedUserIds = @("not-a-user-id")
        { Ensure-HostedConfig } | Should Throw
    }
}
