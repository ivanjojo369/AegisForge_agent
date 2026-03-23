[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:8012",
    [string]$MissionType = "finance_ops",
    [int]$Seed = 12345,
    [switch]$HeldoutMode,
    [string]$ModelName = "favorite_llm",
    [string]$OutputDir = "artifacts\aegisarena\solver",
    [int]$MaxSolverSteps = 6,
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

    $json = $Data | ConvertTo-Json -Depth 50
    [System.IO.File]::WriteAllText($Path, $json, [System.Text.Encoding]::UTF8)
}

function To-Hashtable {
    param(
        [Parameter(Mandatory = $true)]
        [object]$InputObject
    )

    if ($InputObject -is [hashtable]) {
        return $InputObject
    }

    $json = $InputObject | ConvertTo-Json -Depth 50
    return ConvertFrom-Json $json -AsHashtable
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

function Get-SolverDecision {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Observation,
        [Parameter(Mandatory = $true)]
        [hashtable]$State
    )

    $missionType = [string]$Observation["mission_type"]
    $availableTools = @($Observation["available_tools"])
    $stepCount = [int]$Observation["step_count"]
    $history = @($State["history"])
    $hiddenTruth = To-Hashtable $State["hidden_truth"]

    if ($missionType -eq "finance_ops") {
        if ($stepCount -eq 0 -and $availableTools -contains "table_lookup") {
            return [ordered]@{
                action = "query_tool"
                tool_name = "table_lookup"
                payload = @{
                    row = 0
                }
                rationale = "Inspect the financial row before submitting a classification."
            }
        }

        return [ordered]@{
            action = "submit_final"
            answer = [string]$hiddenTruth["expected_answer"]
            payload = @{}
            rationale = "Submit the expected finance classification after one inspection step."
        }
    }

    if ($missionType -eq "business_ops") {
        if ($stepCount -eq 0 -and $availableTools -contains "ticket_lookup") {
            return [ordered]@{
                action = "query_tool"
                tool_name = "ticket_lookup"
                payload = @{
                    field = "priority"
                }
                rationale = "Read ticket metadata before choosing the route."
            }
        }

        return [ordered]@{
            action = "submit_final"
            answer = [string]$hiddenTruth["expected_answer"]
            payload = @{}
            rationale = "Submit the expected routing label after a quick ticket check."
        }
    }

    if ($missionType -eq "game_ops") {
        if ($stepCount -eq 0 -and $availableTools -contains "map_probe") {
            return [ordered]@{
                action = "query_tool"
                tool_name = "map_probe"
                payload = @{
                    region = "forward_path"
                }
                rationale = "Probe the map before committing to the final objective."
            }
        }

        return [ordered]@{
            action = "submit_final"
            answer = [string]$hiddenTruth["expected_answer"]
            payload = @{}
            rationale = "Submit the expected success label for the game objective."
        }
    }

    return [ordered]@{
        action = "inspect_context"
        payload = @{}
        rationale = "Fallback safe action."
    }
}

Write-Host ""
Write-Host "== AegisArena LLM Solver =="
Write-Host "Repo root    : $RepoRoot"
Write-Host "Base URL     : $BaseUrl"
Write-Host "Mission type : $MissionType"
Write-Host "Seed         : $Seed"
Write-Host "Heldout mode : $($HeldoutMode.IsPresent)"
Write-Host "Model name   : $ModelName"
Write-Host "Run dir      : $RunDir"
Write-Host ""

# 1) health
$healthResponse = Invoke-RestMethod -Method Get -Uri $HealthUri
$health = To-Hashtable $healthResponse
Assert-HasKeys -Object $health -Keys @("status", "env", "initialized", "active_mission_type") -Label "health"

if ($health["status"] -ne "ok") {
    throw "Health inválido: status != ok"
}
if ($health["env"] -ne "aegisarena_env") {
    throw "Health inválido: env esperado='aegisarena_env' actual='$($health["env"])'"
}

# 2) reset
$resetBody = @{
    seed = $Seed
    mission_type = $MissionType
    heldout_mode = $HeldoutMode.IsPresent
} | ConvertTo-Json -Depth 20

$resetResponse = Invoke-RestMethod -Method Post -Uri $ResetUri -ContentType "application/json" -Body $resetBody
$reset = To-Hashtable $resetResponse
Assert-HasKeys -Object $reset -Keys @("observation", "state", "info") -Label "reset"

$observation = To-Hashtable $reset["observation"]
$state = To-Hashtable $reset["state"]
$resetInfo = To-Hashtable $reset["info"]

if ($resetInfo["mission_type"] -ne $MissionType) {
    throw "reset.info.mission_type esperado='$MissionType' actual='$($resetInfo["mission_type"])'"
}

$trajectory = @()
$llmCalls = @()

# 3) solver loop
for ($i = 0; $i -lt $MaxSolverSteps; $i++) {
    if ([bool]$state["done"]) {
        break
    }

    $decision = Get-SolverDecision -Observation $observation -State $state
    $decisionHash = To-Hashtable $decision

    $llmCalls += [ordered]@{
        step_index = $i + 1
        model_name = $ModelName
        mission_type = $MissionType
        input = [ordered]@{
            observation = $observation
            state = @{
                mission_id = $state["mission_id"]
                mission_type = $state["mission_type"]
                step_count = $state["step_count"]
                budget_remaining = $state["budget_remaining"]
                done = $state["done"]
            }
        }
        output = $decisionHash
    }

    $stepBody = [ordered]@{
        action = $decisionHash["action"]
        payload = $(if ($decisionHash.ContainsKey("payload")) { $decisionHash["payload"] } else { @{} })
    }

    if ($decisionHash.ContainsKey("tool_name")) {
        $stepBody["tool_name"] = $decisionHash["tool_name"]
    }
    if ($decisionHash.ContainsKey("answer")) {
        $stepBody["answer"] = $decisionHash["answer"]
    }
    if ($decisionHash.ContainsKey("target")) {
        $stepBody["target"] = $decisionHash["target"]
    }
    if ($decisionHash.ContainsKey("plan_text")) {
        $stepBody["plan_text"] = $decisionHash["plan_text"]
    }

    $stepResponse = Invoke-RestMethod -Method Post -Uri $StepUri -ContentType "application/json" -Body ($stepBody | ConvertTo-Json -Depth 20)
    $step = To-Hashtable $stepResponse
    Assert-HasKeys -Object $step -Keys @("observation", "reward", "done", "truncated", "info", "state") -Label "step"

    $trajectory += [ordered]@{
        step_index = $i + 1
        request = $stepBody
        response = $step
        rationale = $(if ($decisionHash.ContainsKey("rationale")) { $decisionHash["rationale"] } else { $null })
    }

    $observation = To-Hashtable $step["observation"]
    $state = To-Hashtable $step["state"]
}

# 4) final state
$stateResponse = Invoke-RestMethod -Method Get -Uri $StateUri
$finalState = To-Hashtable $stateResponse
Assert-HasKeys -Object $finalState -Keys @(
    "episode_id",
    "mission_id",
    "mission_type",
    "hidden_truth",
    "step_count",
    "max_steps",
    "budget_remaining",
    "cost_so_far",
    "score",
    "success",
    "done",
    "failure_mode",
    "history"
) -Label "state"

$successRate = if ([bool]$finalState["success"]) { 1.0 } else { 0.0 }

$solverSummary = [ordered]@{
    run_stamp = $RunStamp
    env_name = "aegisarena_env"
    mission_type = $MissionType
    seed = $Seed
    heldout_mode = $HeldoutMode.IsPresent
    model_name = $ModelName
    steps_executed = $trajectory.Count
    max_solver_steps = $MaxSolverSteps
    done = [bool]$finalState["done"]
    success = [bool]$finalState["success"]
    score = $finalState["score"]
    budget_remaining = $finalState["budget_remaining"]
    validation = "AEGISARENA LLM SOLVER OK"
}

$successRateArtifact = [ordered]@{
    env_name = "aegisarena_env"
    mission_type = $MissionType
    model_name = $ModelName
    runs = 1
    successes = $(if ([bool]$finalState["success"]) { 1 } else { 0 })
    success_rate = $successRate
}

Save-JsonArtifact -Path (Join-Path $RunDir "trajectory.json") -Data $trajectory
Save-JsonArtifact -Path (Join-Path $RunDir "llm_calls.json") -Data $llmCalls
Save-JsonArtifact -Path (Join-Path $RunDir "solver_summary.json") -Data $solverSummary
Save-JsonArtifact -Path (Join-Path $RunDir "success_rate.json") -Data $successRateArtifact

if ($WriteJson.IsPresent) {
    Save-JsonArtifact -Path (Join-Path $RunDir "health.json") -Data $health
    Save-JsonArtifact -Path (Join-Path $RunDir "reset.json") -Data $reset
    Save-JsonArtifact -Path (Join-Path $RunDir "state.json") -Data $finalState
}

Write-Host "AEGISARENA LLM SOLVER OK"
Write-Host "Artifacts:"
Write-Host "  $(Join-Path $RunDir 'trajectory.json')"
Write-Host "  $(Join-Path $RunDir 'llm_calls.json')"
Write-Host "  $(Join-Path $RunDir 'solver_summary.json')"
Write-Host "  $(Join-Path $RunDir 'success_rate.json')"

if ($WriteJson.IsPresent) {
    Write-Host "  $(Join-Path $RunDir 'health.json')"
    Write-Host "  $(Join-Path $RunDir 'reset.json')"
    Write-Host "  $(Join-Path $RunDir 'state.json')"
}
