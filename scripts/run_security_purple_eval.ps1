[CmdletBinding()]
param(
    [string]$RepoRoot = "C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent",
    [string]$PythonExe = "python",
    [string]$BaseUrl = "http://127.0.0.1:8001",
    [ValidateSet("attacker","defender")]
    [string]$Mode = "defender",
    [int]$MaxTurns = 8,
    [switch]$StrictMode,
    [switch]$NormalUser,
    [switch]$HeldoutLike,
    [string]$ScenarioToml = "",
    [string]$TrackModule = "aegisforge_eval.tracks.security_arena",
    [switch]$NoVenv,
    [switch]$UseSetEnv,
    [string]$SetEnvPath = "C:\Users\PC\Documents\AGI-Prototipo\set_env.ps1",
    [string]$OpenAIBaseUrl = "",
    [string]$OpenAIApiKey = "",
    [string]$OpenAIModel = "",
    [switch]$SkipServerHealth,
    [switch]$TraceArtifacts,
    [switch]$DebugArtifacts,
    [string]$ExtraArgs = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Info([string]$Message) {
    Write-Host "[security-eval] $Message" -ForegroundColor Cyan
}

function Write-Ok([string]$Message) {
    Write-Host "[security-eval] $Message" -ForegroundColor Green
}

function Write-WarnLine([string]$Message) {
    Write-Host "[security-eval] $Message" -ForegroundColor Yellow
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

function Get-HealthyResponse([string]$Url) {
    try {
        return Invoke-RestMethod -Uri $Url -Method GET -TimeoutSec 5
    }
    catch {
        return $null
    }
}

Assert-Path $RepoRoot "Repo root"
Set-Location $RepoRoot

$SrcRoot = Join-Path $RepoRoot "src"
$TrackFile = Join-Path $SrcRoot "aegisforge_eval\tracks\security_arena.py"
$HarnessDir = Join-Path $RepoRoot "harness\AegisForge_scenarios\security_arena"
$DefaultAttackerScenario = Join-Path $HarnessDir "scenario_attacker.toml"
$DefaultDefenderScenario = Join-Path $HarnessDir "scenario_defender.toml"
$HealthUrl = "$BaseUrl/health"
$CardUrl = "$BaseUrl/.well-known/agent-card.json"

Assert-Path $SrcRoot "src directory"
Assert-Path $TrackFile "security_arena.py"
Assert-Path $HarnessDir "security harness directory"
Assert-Path $DefaultAttackerScenario "attacker scenario"
Assert-Path $DefaultDefenderScenario "defender scenario"

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

if (-not $ScenarioToml) {
    $ScenarioToml = if ($Mode -eq "attacker") { $DefaultAttackerScenario } else { $DefaultDefenderScenario }
}
Assert-Path $ScenarioToml "selected scenario toml"

$env:PYTHONPATH = "$SrcRoot;$RepoRoot"
$env:AEGISFORGE_BASE_URL = $BaseUrl
$env:AEGISFORGE_TRACK = "security"
$env:AEGISFORGE_ASSESSMENT_TRACK = "security_arena"
$env:AEGISFORGE_SECURITY_MODE = $Mode
$env:AEGISFORGE_SECURITY_HARNESS_DIR = $HarnessDir
$env:AEGISFORGE_SECURITY_ATTACKER_SCENARIO = $DefaultAttackerScenario
$env:AEGISFORGE_SECURITY_DEFENDER_SCENARIO = $DefaultDefenderScenario
$env:AEGISFORGE_SCENARIO_TOML = $ScenarioToml
$env:AEGISFORGE_MAX_TURNS = [string]$MaxTurns
$env:AEGISFORGE_STRICT_MODE = $(if ($StrictMode) { "1" } else { "0" })
$env:AEGISFORGE_NORMAL_USER = $(if ($NormalUser) { "1" } else { "0" })
$env:AEGISFORGE_HELDOUT_LIKE = $(if ($HeldoutLike) { "1" } else { "0" })
$env:AEGISFORGE_TRACE_ARTIFACTS = $(if ($TraceArtifacts) { "1" } else { "0" })
$env:AEGISFORGE_DEBUG_ARTIFACTS = $(if ($DebugArtifacts) { "1" } else { "0" })

if ($OpenAIBaseUrl) { $env:OPENAI_BASE_URL = $OpenAIBaseUrl }
if ($OpenAIApiKey) { $env:OPENAI_API_KEY = $OpenAIApiKey }
if ($OpenAIModel) { $env:OPENAI_MODEL = $OpenAIModel }

Write-Info "Eval preflight summary"
Write-Host "  RepoRoot     : $RepoRoot"
Write-Host "  Python       : $PythonResolved"
Write-Host "  BaseUrl      : $BaseUrl"
Write-Host "  Mode         : $Mode"
Write-Host "  ScenarioToml : $ScenarioToml"
Write-Host "  MaxTurns     : $MaxTurns"
Write-Host "  StrictMode   : $($env:AEGISFORGE_STRICT_MODE)"
Write-Host "  NormalUser   : $($env:AEGISFORGE_NORMAL_USER)"
Write-Host "  HeldoutLike  : $($env:AEGISFORGE_HELDOUT_LIKE)"
Write-Host "  Trace        : $($env:AEGISFORGE_TRACE_ARTIFACTS)"
Write-Host "  Debug        : $($env:AEGISFORGE_DEBUG_ARTIFACTS)"
Write-Host ""

if (-not $SkipServerHealth) {
    $health = Get-HealthyResponse $HealthUrl
    if ($null -eq $health) {
        throw "No healthy A2A server found at $HealthUrl. Start run_security_local_env.ps1 first."
    }
    Write-Ok "Server health check passed."

    $card = Get-HealthyResponse $CardUrl
    if ($null -ne $card) {
        Write-Ok "Agent card is reachable."
    }
    else {
        Write-WarnLine "Agent card endpoint did not respond, but health is up. Continuing."
    }
}

$SplitExtraArgs = @()
if ($ExtraArgs) {
    $SplitExtraArgs = $ExtraArgs -split "\s+" | Where-Object { $_ -and $_.Trim() }
}

$attempts = @(
    @{ Label = "module"; Args = @("-m", $TrackModule) + $SplitExtraArgs },
    @{ Label = "file"; Args = @($TrackFile) + $SplitExtraArgs }
)

$Succeeded = $false
foreach ($attempt in $attempts) {
    Write-Info "Trying eval entrypoint: $($attempt.Label)"
    & $PythonResolved @($attempt.Args)
    $ExitCode = $LASTEXITCODE
    if ($ExitCode -eq 0) {
        Write-Ok "Eval finished successfully using $($attempt.Label) entrypoint."
        $Succeeded = $true
        break
    }
    Write-WarnLine "Entrypoint $($attempt.Label) exited with code $ExitCode"
}

if (-not $Succeeded) {
    throw "Security eval failed for both module and file entrypoints. Check the track file and runner wiring."
}
