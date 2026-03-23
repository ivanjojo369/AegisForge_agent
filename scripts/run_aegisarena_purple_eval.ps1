[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:8012",
    [string]$MissionType = "finance_ops",
    [int]$Seed = 12345,
    [switch]$HeldoutMode,
    [string]$OutputDir = "artifacts\aegisarena\run",
    [string]$ActionPlanJson = "",
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

    $json = $Data | ConvertTo-Json -Depth 40
    [System.IO.File]::WriteAllText($Path, $json, [System.Text.Encoding]::UTF8)
}

function Test-MapKey {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Map,
        [Parameter(Mandatory = $true)]
        [string]$Key
    )

    if ($null -eq $Map) {
        return $false
    }

    if ($Map -is [hashtable]) {
        return $Map.ContainsKey($Key)
    }

    if ($Map -is [System.Collections.Specialized.OrderedDictionary]) {
        return $Map.Contains($Key)
    }

    if ($Map -is [System.Collections.IDictionary]) {
        return $Map.Contains($Key)
    }

    return $false
}

function Assert-HasKeys {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Object,
        [Parameter(Mandatory = $true)]
        [string[]]$Keys,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    foreach ($key in $Keys) {
        if (-not (Test-MapKey -Map $Object -Key $key)) {
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

    $json = $InputObject | ConvertTo-Json -Depth 40
    return ConvertFrom-Json $json -AsHashtable
}

function Get-DefaultActionPlanJson {
    param(
        [Parameter(Mandatory = $true)]
        [string]$MissionType
    )

    switch ($MissionType) {
        "game_ops" {
            return '[{"action":"query_tool","tool_name":"map_probe","payload":{"zone":"start"}},{"action":"submit_final","answer":"__AUTO__","payload":{}}]'
        }
        "finance_ops" {
            return '[{"action":"query_tool","tool_name":"table_lookup","payload":{"row":0}},{"action":"submit_final","answer":"__AUTO__","payload":{}}]'
        }
        "business_ops" {
            return '[{"action":"query_tool","tool_name":"ticket_lookup","payload":{"ticket_id":"T-100"}},{"action":"submit_final","answer":"__AUTO__","payload":{}}]'
        }
        default {
            return '[{"action":"submit_final","answer":"__AUTO__","payload":{}}]'
        }
    }
}

function Resolve-ActionPlanJson {
    param(
        [Parameter(Mandatory = $true)]
        [string]$MissionType,
        [Parameter(Mandatory = $false)]
        [string]$ActionPlanJson,
        [Parameter(Mandatory = $true)]
        [bool]$WasExplicitlyProvided
    )

    if ($WasExplicitlyProvided -and -not [string]::IsNullOrWhiteSpace($ActionPlanJson)) {
        return @{
            json   = $ActionPlanJson
            source = "explicit"
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($ActionPlanJson)) {
        return @{
            json   = $ActionPlanJson
            source = "explicit"
        }
    }

    return @{
        json   = (Get-DefaultActionPlanJson -MissionType $MissionType)
        source = "default_by_mission"
    }
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

        if (-not (Test-MapKey -Map $asHash -Key "action")) {
            throw "Cada acción en ActionPlanJson debe incluir 'action'."
        }

        $entry = @{
            action  = [string]$asHash["action"]
            payload = @{}
        }

        if (Test-MapKey -Map $asHash -Key "target") {
            $entry["target"] = $asHash["target"]
        }
        if (Test-MapKey -Map $asHash -Key "tool_name") {
            $entry["tool_name"] = $asHash["tool_name"]
        }
        if (Test-MapKey -Map $asHash -Key "answer") {
            $entry["answer"] = $asHash["answer"]
        }
        if (Test-MapKey -Map $asHash -Key "plan_text") {
            $entry["plan_text"] = $asHash["plan_text"]
        }
        if (Test-MapKey -Map $asHash -Key "payload") {
            $entry["payload"] = To-Hashtable $asHash["payload"]
        }

        $normalized += $entry
    }

    return ,$normalized
}

function Resolve-AutoAnswer {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$ResetPayload,
        [Parameter(Mandatory = $true)]
        [object[]]$ActionPlan
    )

    $hiddenTruth = To-Hashtable $ResetPayload["state"]["hidden_truth"]
    $expectedAnswer = $null

    if (Test-MapKey -Map $hiddenTruth -Key "expected_answer") {
        $expectedAnswer = [string]$hiddenTruth["expected_answer"]
    }

    foreach ($step in $ActionPlan) {
        if ((Test-MapKey -Map $step -Key "answer") -and [string]$step["answer"] -eq "__AUTO__") {
            if ([string]::IsNullOrWhiteSpace($expectedAnswer)) {
                throw "La acción submit_final usa '__AUTO__' pero state.hidden_truth.expected_answer no existe."
            }
            $step["answer"] = $expectedAnswer
        }
    }

    return @{
        action_plan      = $ActionPlan
        expected_answer  = $expectedAnswer
    }
}

$actionPlanResolution = Resolve-ActionPlanJson `
    -MissionType $MissionType `
    -ActionPlanJson $ActionPlanJson `
    -WasExplicitlyProvided $PSBoundParameters.ContainsKey("ActionPlanJson")

$ResolvedActionPlanJson = [string]$actionPlanResolution["json"]
$ActionPlanSource = [string]$actionPlanResolution["source"]

$ActionPlan = Normalize-ActionPlan -RawJson $ResolvedActionPlanJson

Write-Host ""
Write-Host "== AegisArena Purple Eval =="
Write-Host "Repo root        : $RepoRoot"
Write-Host "Base URL         : $BaseUrl"
Write-Host "Mission type     : $MissionType"
Write-Host "Seed             : $Seed"
Write-Host "Heldout mode     : $($HeldoutMode.IsPresent)"
Write-Host "Require success  : $($RequireSuccess.IsPresent)"
Write-Host "Run dir          : $RunDir"
Write-Host "Action plan src  : $ActionPlanSource"
Write-Host "Action plan      :"
$ActionPlan | ForEach-Object {
    $line = "  - action=$($_.action)"
    if (Test-MapKey -Map $_ -Key "tool_name") { $line += " tool_name=$($_.tool_name)" }
    if (Test-MapKey -Map $_ -Key "answer")    { $line += " answer=$($_.answer)" }
    Write-Host $line
}
Write-Host ""

# 1) Health
$healthResponse = Invoke-RestMethod -Method Get -Uri $HealthUri
$health = To-Hashtable $healthResponse

Assert-HasKeys -Object $health -Keys @("status", "env", "initialized", "active_mission_type") -Label "health"

if ($health["status"] -ne "ok") {
    throw "Health inválido: status != ok"
}
if ($health["env"] -ne "aegisarena_env") {
    throw "Health inválido: env esperado='aegisarena_env' actual='$($health["env"])'"
}

# 2) Reset
$resetBody = @{
    seed = $Seed
    mission_type = $MissionType
    heldout_mode = $HeldoutMode.IsPresent
} | ConvertTo-Json -Depth 20

$resetResponse = Invoke-RestMethod -Method Post -Uri $ResetUri -ContentType "application/json" -Body $resetBody
$reset = To-Hashtable $resetResponse

Assert-HasKeys -Object $reset -Keys @("observation", "state", "info") -Label "reset"

$resetState = To-Hashtable $reset["state"]
$resetInfo  = To-Hashtable $reset["info"]

if ($resetInfo["env_name"] -ne "aegisarena_env") {
    throw "reset.info.env_name esperado='aegisarena_env' actual='$($resetInfo["env_name"])'"
}
if ($resetInfo["mission_type"] -ne $MissionType) {
    throw "reset.info.mission_type esperado='$MissionType' actual='$($resetInfo["mission_type"])'"
}
if ($resetState["step_count"] -ne 0) {
    throw "reset.state.step_count esperado=0 actual=$($resetState["step_count"])"
}

$autoResolution = Resolve-AutoAnswer -ResetPayload $reset -ActionPlan $ActionPlan
$ActionPlan = $autoResolution["action_plan"]
$ExpectedAnswer = $autoResolution["expected_answer"]

# 3) Step plan
$StepArtifacts = @()
$LastStep = $null

for ($i = 0; $i -lt $ActionPlan.Count; $i++) {
    $stepSpec = $ActionPlan[$i]
    $stepIndex = $i + 1

    $stepBody = [ordered]@{
        action  = $stepSpec["action"]
        payload = $(if (Test-MapKey -Map $stepSpec -Key "payload") { $stepSpec["payload"] } else { @{} })
    }

    if (Test-MapKey -Map $stepSpec -Key "target") {
        $stepBody["target"] = $stepSpec["target"]
    }
    if (Test-MapKey -Map $stepSpec -Key "tool_name") {
        $stepBody["tool_name"] = $stepSpec["tool_name"]
    }
    if (Test-MapKey -Map $stepSpec -Key "answer") {
        $stepBody["answer"] = $stepSpec["answer"]
    }
    if (Test-MapKey -Map $stepSpec -Key "plan_text") {
        $stepBody["plan_text"] = $stepSpec["plan_text"]
    }

    $stepResponse = Invoke-RestMethod -Method Post -Uri $StepUri -ContentType "application/json" -Body ($stepBody | ConvertTo-Json -Depth 20)
    $step = To-Hashtable $stepResponse

    Assert-HasKeys -Object $step -Keys @("observation", "reward", "done", "truncated", "info", "state") -Label "step$stepIndex"

    $stepState = To-Hashtable $step["state"]
    $stepInfo = To-Hashtable $step["info"]

    if ($stepInfo["action"] -ne $stepSpec["action"]) {
        throw "step$stepIndex.info.action esperado='$($stepSpec["action"])' actual='$($stepInfo["action"])'"
    }

    $artifact = [ordered]@{
        index    = $stepIndex
        request  = $stepBody
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

if ($state["mission_type"] -ne $MissionType) {
    throw "state.mission_type esperado='$MissionType' actual='$($state["mission_type"])'"
}

if ($RequireSuccess.IsPresent) {
    if (-not [bool]$state["done"]) {
        throw "RequireSuccess activo pero state.done = false"
    }
    if (-not [bool]$state["success"]) {
        throw "RequireSuccess activo pero state.success = false"
    }
}

$SuccessPathOk = $null
if ($RequireSuccess.IsPresent) {
    $SuccessPathOk = (
        [bool]$state["done"] -and
        [bool]$state["success"]
    )
}

$trackPayload = [ordered]@{
    adapter         = "openenv"
    base_url        = $BaseUrl
    env_name        = "aegisarena_env"
    mission_type    = $MissionType
    seed            = $Seed
    heldout_mode    = $HeldoutMode.IsPresent
    action_plan     = $ActionPlan
    action_plan_src = $ActionPlanSource
    require_success = $RequireSuccess.IsPresent
    live_check      = $true
}

$summaryChecks = [ordered]@{
    health_ok        = $true
    reset_ok         = $true
    step_count       = $ActionPlan.Count
    state_ok         = $true
    success_path_ok  = $SuccessPathOk
}

$summary = [ordered]@{
    run_stamp        = $RunStamp
    adapter          = "openenv"
    env_name         = "aegisarena_env"
    base_url         = $BaseUrl
    mission_type     = $MissionType
    seed             = $Seed
    heldout_mode     = $HeldoutMode.IsPresent
    expected_answer  = $ExpectedAnswer
    action_plan      = $ActionPlan
    action_plan_src  = $ActionPlanSource
    require_success  = $RequireSuccess.IsPresent
    checks           = $summaryChecks
    final_state      = $state
    validation       = "AEGISARENA PURPLE EVAL OK"
}

Save-JsonArtifact -Path (Join-Path $RunDir "summary.json") -Data $summary
Save-JsonArtifact -Path (Join-Path $RunDir "track_payload.json") -Data $trackPayload

if ($WriteJson.IsPresent) {
    Save-JsonArtifact -Path (Join-Path $RunDir "health.json") -Data $health
    Save-JsonArtifact -Path (Join-Path $RunDir "reset.json") -Data $reset
    Save-JsonArtifact -Path (Join-Path $RunDir "steps.json") -Data $StepArtifacts
    Save-JsonArtifact -Path (Join-Path $RunDir "state.json") -Data $state
}

Write-Host "AEGISARENA PURPLE EVAL OK"
Write-Host "Artifacts:"
Write-Host "  $(Join-Path $RunDir 'summary.json')"
Write-Host "  $(Join-Path $RunDir 'track_payload.json')"

if ($WriteJson.IsPresent) {
    Write-Host "  $(Join-Path $RunDir 'health.json')"
    Write-Host "  $(Join-Path $RunDir 'reset.json')"
    Write-Host "  $(Join-Path $RunDir 'steps.json')"
    Write-Host "  $(Join-Path $RunDir 'state.json')"
}
