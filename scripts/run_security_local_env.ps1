[CmdletBinding()]
param(
    [string]$RepoRoot = "C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent",
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8001,
    [string]$CardUrl = "",
    [string]$PythonExe = "python",
    [switch]$NoVenv,
    [switch]$SkipHealthCheck,
    [switch]$UseSetEnv,
    [string]$SetEnvPath = "C:\Users\PC\Documents\AGI-Prototipo\set_env.ps1",
    [string]$OpenAIBaseUrl = "",
    [string]$OpenAIApiKey = "",
    [string]$OpenAIModel = "",
    [switch]$DebugArtifacts,
    [switch]$TraceArtifacts
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Info([string]$Message) {
    Write-Host "[security-local] $Message" -ForegroundColor Cyan
}

function Write-Ok([string]$Message) {
    Write-Host "[security-local] $Message" -ForegroundColor Green
}

function Write-WarnLine([string]$Message) {
    Write-Host "[security-local] $Message" -ForegroundColor Yellow
}

function Assert-Path([string]$PathValue, [string]$Label) {
    if (-not (Test-Path $PathValue)) {
        throw "$Label not found: $PathValue"
    }
}

function Resolve-Python([string]$RequestedPython) {
    $cmd = Get-Command $RequestedPython -ErrorAction SilentlyContinue
    if ($null -eq $cmd) {
        throw "Python executable not found: $RequestedPython"
    }
    return $cmd.Source
}

function Invoke-HealthProbe([string]$Url) {
    try {
        $resp = Invoke-RestMethod -Uri $Url -Method GET -TimeoutSec 5
        return $resp
    }
    catch {
        return $null
    }
}

Assert-Path $RepoRoot "Repo root"
Set-Location $RepoRoot

$SrcRoot = Join-Path $RepoRoot "src"
$HarnessDir = Join-Path $RepoRoot "harness\AegisForge_scenarios\security_arena"
$AttackerScenario = Join-Path $HarnessDir "scenario_attacker.toml"
$DefenderScenario = Join-Path $HarnessDir "scenario_defender.toml"
$A2AServer = Join-Path $SrcRoot "aegisforge\a2a_server.py"
$HealthUrl = "http://$BindHost`:$Port/health"
$CardProbeUrl = "http://$BindHost`:$Port/.well-known/agent-card.json"

$env:AEGISFORGE_HOST = $BindHost

Assert-Path $SrcRoot "src directory"
Assert-Path $HarnessDir "security harness directory"
Assert-Path $AttackerScenario "attacker scenario"
Assert-Path $DefenderScenario "defender scenario"
Assert-Path $A2AServer "a2a_server.py"

if (-not $NoVenv) {
    $ActivatePath = Join-Path $RepoRoot ".venv\Scripts\Activate.ps1"
    if (Test-Path $ActivatePath) {
        Write-Info "Activating venv: $ActivatePath"
        . $ActivatePath
    }
    else {
        Write-WarnLine "No .venv activation script found. Continuing with current Python environment."
    }
}

if ($UseSetEnv) {
    if (Test-Path $SetEnvPath) {
        Write-Info "Loading shared env script: $SetEnvPath"
        . $SetEnvPath
    }
    else {
        Write-WarnLine "SetEnv script requested but not found: $SetEnvPath"
    }
}

$PythonResolved = Resolve-Python $PythonExe

$env:PYTHONPATH = "$SrcRoot;$RepoRoot"
$env:AEGISFORGE_TRACK = "security"
$env:AEGISFORGE_HOST = $BindHost
$env:AEGISFORGE_PORT = [string]$Port
$env:AEGISFORGE_SECURITY_HARNESS_DIR = $HarnessDir
$env:AEGISFORGE_SECURITY_ATTACKER_SCENARIO = $AttackerScenario
$env:AEGISFORGE_SECURITY_DEFENDER_SCENARIO = $DefenderScenario
$env:AEGISFORGE_ASSESSMENT_TRACK = "security_arena"
$env:AEGISFORGE_DEBUG_ARTIFACTS = $(if ($DebugArtifacts) { "1" } else { "0" })
$env:AEGISFORGE_TRACE_ARTIFACTS = $(if ($TraceArtifacts) { "1" } else { "0" })

if ($OpenAIBaseUrl) { $env:OPENAI_BASE_URL = $OpenAIBaseUrl }
if ($OpenAIApiKey) { $env:OPENAI_API_KEY = $OpenAIApiKey }
if ($OpenAIModel) { $env:OPENAI_MODEL = $OpenAIModel }
if ($CardUrl) { $env:AEGISFORGE_CARD_URL = $CardUrl }

Write-Info "Preflight summary"
Write-Host "  RepoRoot : $RepoRoot"
Write-Host "  Python   : $PythonResolved"
Write-Host "  Host     : $BindHost"
Write-Host "  Port     : $Port"
Write-Host "  Health   : $HealthUrl"
Write-Host "  Card     : $(if ($CardUrl) { $CardUrl } else { $CardProbeUrl })"
Write-Host "  Harness  : $HarnessDir"
Write-Host "  Trace    : $env:AEGISFORGE_TRACE_ARTIFACTS"
Write-Host "  Debug    : $env:AEGISFORGE_DEBUG_ARTIFACTS"
Write-Host ""

$existing = Invoke-HealthProbe $HealthUrl
if ($null -ne $existing) {
    Write-WarnLine "A server is already responding on $HealthUrl"
    Write-WarnLine "Stop it or choose a different -Port before launching another one."
    throw "Port already in use by a healthy server."
}

Write-Info "Starting AegisForge Security A2A server..."
Write-Info "Tip: leave this terminal open. Use a second terminal for run_security_purple_eval.ps1"
Write-Host ""

& $PythonResolved -m aegisforge.a2a_server --host $BindHost --port $Port @($(if ($CardUrl) { @("--card-url", $CardUrl) } else { @() }))
$ExitCode = $LASTEXITCODE

if ($ExitCode -ne 0) {
    throw "A2A server exited with code $ExitCode"
}

if (-not $SkipHealthCheck) {
    $resp = Invoke-HealthProbe $HealthUrl
    if ($null -eq $resp) {
        Write-WarnLine "Server process exited cleanly, but no health response was captured afterward."
    }
    else {
        Write-Ok "Health probe succeeded."
    }
}
