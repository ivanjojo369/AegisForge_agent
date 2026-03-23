[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:8011",
    [string]$EnvName = "demo_env",
    [int]$Seed = 123,
    [string]$OutputDir = "artifacts\openenv\run",
    [string]$ActionPlanJson = '[{"action":"advance","value":1},{"action":"advance","value":2}]',
    [switch]$RequireSuccess,
    [switch]$WriteJson
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$OutputPath = Join-Path $RepoRoot $OutputDir
New-Item -ItemType Directory -Force -Path $OutputPath | Out-Null

$RunStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$RunDir = Join-Path $OutputPath $RunStamp
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

$HealthUri = "$BaseUrl/health"
$ResetUri  = "$BaseUrl/reset"
$StepUri   = "$BaseUrl/step"
$StateUri  = "$BaseUrl/state"

function Save-JsonArtifact {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [object]$Data
    )

    $json = $Data | ConvertTo-Json -Depth 30
    [System.IO.File]::WriteAllText($Path, $json, [System.Text.Encoding]::UTF8)
}

function Assert-HasKeys {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Object,
        [Parameter(Mandatory = $true)]
        [string[]]$Keys,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    foreach ($key in $Keys) {
        if (-not $Object.ContainsKey($key)) {
            throw "$Label no contiene la llave requerida: $key"
        }
    }
}

function To-Hashtable {
    param(
        [Parameter(Mandatory = $true)]
        [object]$InputObject
    )

    if ($InputObject -is [hashtable]) {
        return $InputObject
    }

    $json = $InputObject | ConvertTo-Json -Depth 30
    return ConvertFrom-Json $json -AsHashtable
}

function Normalize-ActionPlan {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RawJson
    )

    $parsed = ConvertFrom-Json -InputObject $RawJson
    if ($null -eq $parsed) {
        throw "ActionPlanJson está vacío o no es válido."
    }

    $items = @($parsed)
    if ($items.Count -eq 0) {
        throw "ActionPlanJson debe contener al menos una acción."
    }

    $normalized = @()

    foreach ($item in $items) {
        $asHash = To-Hashtable $item

        if (-not $asHash.ContainsKey("action")) {
            throw "Cada acción en ActionPlanJson debe incluir 'action'."
        }

        $action = [string]$asHash["action"]
        $value = 1

        if ($asHash.ContainsKey("value")) {
            $value = [int]$asHash["value"]
        }

        $normalized += @{
            action = $action
            value  = $value
        }
    }

    return ,$normalized
}

$ActionPlan = Normalize-ActionPlan -RawJson $ActionPlanJson

Write-Host ""
Write-Host "== AegisForge OpenEnv Purple Eval =="
Write-Host "Repo root       : $RepoRoot"
Write-Host "Base URL        : $BaseUrl"
Write-Host "Env name        : $EnvName"
Write-Host "Seed            : $Seed"
Write-Host "Require success : $($RequireSuccess.IsPresent)"
Write-Host "Run dir         : $RunDir"
Write-Host "Action plan     :"
$ActionPlan | ForEach-Object {
    Write-Host "  - action=$($_.action) value=$($_.value)"
}
Write-Host ""

# 1) Health
$healthResponse = Invoke-RestMethod -Method Get -Uri $HealthUri
$health = To-Hashtable $healthResponse

Assert-HasKeys -Object $health -Keys @("status", "env", "initialized") -Label "health"

if ($health["status"] -ne "ok") {
    throw "Health inválido: status != ok"
}
if ($health["env"] -ne $EnvName) {
    throw "Health inválido: env esperado='$EnvName' actual='$($health["env"])'"
}

# 2) Reset
$resetBody = @{
    seed = $Seed
} | ConvertTo-Json

$resetResponse = Invoke-RestMethod -Method Post -Uri $ResetUri -ContentType "application/json" -Body $resetBody
$reset = To-Hashtable $resetResponse

Assert-HasKeys -Object $reset -Keys @("observation", "state", "info") -Label "reset"

$resetState = To-Hashtable $reset["state"]
$resetInfo  = To-Hashtable $reset["info"]

if ($resetInfo["env_name"] -ne $EnvName) {
    throw "reset.info.env_name esperado='$EnvName' actual='$($resetInfo["env_name"])'"
}
if ($resetState["score"] -ne 0) {
    throw "reset.state.score esperado=0 actual=$($resetState["score"])"
}
if ($resetState["step_count"] -ne 0) {
    throw "reset.state.step_count esperado=0 actual=$($resetState["step_count"])"
}

# 3) Step plan
$StepArtifacts = @()
$LastStep = $null

for ($i = 0; $i -lt $ActionPlan.Count; $i++) {
    $stepSpec = $ActionPlan[$i]
    $stepIndex = $i + 1

    $stepBody = @{
        action = $stepSpec["action"]
        value  = $stepSpec["value"]
    } | ConvertTo-Json

    $stepResponse = Invoke-RestMethod -Method Post -Uri $StepUri -ContentType "application/json" -Body $stepBody
    $step = To-Hashtable $stepResponse

    Assert-HasKeys -Object $step -Keys @("observation", "reward", "done", "truncated", "info", "state") -Label "step$stepIndex"

    $stepState = To-Hashtable $step["state"]

    if ($stepState["last_action"] -ne $stepSpec["action"]) {
        throw "step$stepIndex.state.last_action esperado='$($stepSpec["action"])' actual='$($stepState["last_action"])'"
    }

    $artifact = [ordered]@{
        index = $stepIndex
        request = [ordered]@{
            action = $stepSpec["action"]
            value  = $stepSpec["value"]
        }
        response = $step
    }

    $StepArtifacts += $artifact
    $LastStep = $step
}

# 4) State
$stateResponse = Invoke-RestMethod -Method Get -Uri $StateUri
$state = To-Hashtable $stateResponse

Assert-HasKeys -Object $state -Keys @(
    "episode_id",
    "score",
    "step_count",
    "max_steps",
    "target_score",
    "done",
    "success",
    "last_action",
    "history"
) -Label "state"

if ($null -ne $LastStep) {
    $lastStepState = To-Hashtable $LastStep["state"]
    if ($state["last_action"] -ne $lastStepState["last_action"]) {
        throw "state.last_action no coincide con el último step"
    }
}

if ($RequireSuccess.IsPresent) {
    if (-not [bool]$state["done"]) {
        throw "RequireSuccess activo pero state.done = false"
    }
    if (-not [bool]$state["success"]) {
        throw "RequireSuccess activo pero state.success = false"
    }
    if ([int]$state["score"] -lt [int]$state["target_score"]) {
        throw "RequireSuccess activo pero state.score < state.target_score"
    }
}

$SuccessPathOk = $null
if ($RequireSuccess.IsPresent) {
    $SuccessPathOk = (
        [bool]$state["done"] -and
        [bool]$state["success"] -and
        ([int]$state["score"] -ge [int]$state["target_score"])
    )
}

$trackPayload = [ordered]@{
    adapter = "openenv"
    base_url = $BaseUrl
    env_name = $EnvName
    seed = $Seed
    action_plan = $ActionPlan
    require_success = $RequireSuccess.IsPresent
    live_check = $true
}

$summaryChecks = [ordered]@{
    health_ok = $true
    reset_ok = $true
    step_count = $ActionPlan.Count
    state_ok = $true
    success_path_ok = $SuccessPathOk
}

$summary = [ordered]@{
    run_stamp = $RunStamp
    adapter = "openenv"
    env_name = $EnvName
    base_url = $BaseUrl
    seed = $Seed
    action_plan = $ActionPlan
    require_success = $RequireSuccess.IsPresent
    checks = $summaryChecks
    final_state = $state
    validation = "OPENENV PURPLE EVAL OK"
}

Save-JsonArtifact -Path (Join-Path $RunDir "summary.json") -Data $summary
Save-JsonArtifact -Path (Join-Path $RunDir "track_payload.json") -Data $trackPayload

if ($WriteJson.IsPresent) {
    Save-JsonArtifact -Path (Join-Path $RunDir "health.json") -Data $health
    Save-JsonArtifact -Path (Join-Path $RunDir "reset.json") -Data $reset
    Save-JsonArtifact -Path (Join-Path $RunDir "steps.json") -Data $StepArtifacts
    Save-JsonArtifact -Path (Join-Path $RunDir "state.json") -Data $state
}

Write-Host "OPENENV PURPLE EVAL OK"
Write-Host "Artifacts:"
Write-Host "  $(Join-Path $RunDir 'summary.json')"
Write-Host "  $(Join-Path $RunDir 'track_payload.json')"

if ($WriteJson.IsPresent) {
    Write-Host "  $(Join-Path $RunDir 'health.json')"
    Write-Host "  $(Join-Path $RunDir 'reset.json')"
    Write-Host "  $(Join-Path $RunDir 'steps.json')"
    Write-Host "  $(Join-Path $RunDir 'state.json')"
}
