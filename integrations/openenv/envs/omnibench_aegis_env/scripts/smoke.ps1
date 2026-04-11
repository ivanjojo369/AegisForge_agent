param(
    [string]$BaseUrl = $(if ($env:OPENENV_BASE_URL) { $env:OPENENV_BASE_URL } else { 'http://127.0.0.1:8000' }),
    [double]$Timeout = $(if ($env:OPENENV_TIMEOUT) { [double]$env:OPENENV_TIMEOUT } else { 10 }),
    [switch]$VerboseSmoke,
    [switch]$Json,
    [switch]$AllDomains,
    [string[]]$Only,
    [switch]$IncludeNonSmoke,
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'

function Resolve-ScriptRoot {
    if ($PSScriptRoot) {
        return $PSScriptRoot
    }
    return Split-Path -Parent $MyInvocation.MyCommand.Path
}

function Invoke-OpenEnvPythonScript {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptPath,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    Write-Host "[info] Running $ScriptPath"
    & $Python $ScriptPath @Arguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "Command failed with exit code ${exitCode}: $Python $ScriptPath $($Arguments -join ' ')"
    }
}

$scriptRoot = Resolve-ScriptRoot

if ($AllDomains) {
    $targetScript = Join-Path $scriptRoot 'smoke_test_all_domains.py'
    $arguments = @('--base-url', $BaseUrl, '--timeout', "$Timeout")

    if ($VerboseSmoke) {
        $arguments += '--verbose'
    }
    if ($Json) {
        $arguments += '--json'
    }
    if ($IncludeNonSmoke) {
        $arguments += '--include-non-smoke'
    }
    if ($Only) {
        $arguments += '--only'
        $arguments += $Only
    }

    Invoke-OpenEnvPythonScript -ScriptPath $targetScript -Arguments $arguments
    Write-Host '[ok] smoke.ps1 completed multi-domain smoke successfully'
    exit 0
}

$targetScript = Join-Path $scriptRoot 'smoke_local.py'
$arguments = @('--base-url', $BaseUrl, '--timeout', "$Timeout")

if ($VerboseSmoke) {
    $arguments += '--verbose'
}
if ($Json) {
    $arguments += '--json'
}

Invoke-OpenEnvPythonScript -ScriptPath $targetScript -Arguments $arguments
Write-Host '[ok] smoke.ps1 completed local smoke successfully'
