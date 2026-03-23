[CmdletBinding()]
param(
    [string]$SubmissionDir = "",
    [string]$SubmissionRoot = "artifacts\aegisarena\submission",
    [string[]]$RequiredMissionTypes = @("game_ops", "finance_ops", "business_ops")
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$SubmissionRootPath = Join-Path $RepoRoot $SubmissionRoot

function Read-JsonFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json -AsHashtable
}

function Save-JsonFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [object]$Data
    )

    $json = $Data | ConvertTo-Json -Depth 50
    [System.IO.File]::WriteAllText($Path, $json, [System.Text.Encoding]::UTF8)
}

function Assert-True {
    param(
        [Parameter(Mandatory = $true)]
        [bool]$Condition,
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

function Resolve-SubmissionDir {
    param(
        [string]$SubmissionDir,
        [string]$SubmissionRootPath
    )

    if (-not [string]::IsNullOrWhiteSpace($SubmissionDir)) {
        if ([System.IO.Path]::IsPathRooted($SubmissionDir)) {
            return $SubmissionDir
        }

        return (Join-Path $RepoRoot $SubmissionDir)
    }

    Assert-True -Condition (Test-Path $SubmissionRootPath) -Message "No existe SubmissionRoot: $SubmissionRootPath"

    $latest = Get-ChildItem -LiteralPath $SubmissionRootPath -Directory |
        Sort-Object Name -Descending |
        Select-Object -First 1

    if ($null -eq $latest) {
        throw "No encontré paquetes en $SubmissionRootPath"
    }

    return $latest.FullName
}

function Get-ObservedQueryTool {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$ActionPlan
    )

    foreach ($step in $ActionPlan) {
        if ([string]$step["action"] -eq "query_tool") {
            return [string]$step["tool_name"]
        }
    }

    return $null
}

$ResolvedSubmissionDir = Resolve-SubmissionDir -SubmissionDir $SubmissionDir -SubmissionRootPath $SubmissionRootPath

$ManifestPath = Join-Path $ResolvedSubmissionDir "manifest.json"
Assert-True -Condition (Test-Path $ManifestPath) -Message "No existe manifest.json en $ResolvedSubmissionDir"

$manifest = Read-JsonFile -Path $ManifestPath

Assert-True -Condition ($manifest["env_name"] -eq "aegisarena_env") -Message "manifest.env_name debe ser 'aegisarena_env'"
Assert-True -Condition ($manifest.ContainsKey("runs")) -Message "manifest.runs no existe"

$manifestMissionTypes = @($manifest["runs"] | ForEach-Object { [string]$_["mission_type"] })

foreach ($requiredMission in $RequiredMissionTypes) {
    Assert-True -Condition ($manifestMissionTypes -contains $requiredMission) -Message "Falta la misión requerida en el bundle: $requiredMission"
}

$expectedToolByMission = @{
    game_ops     = "map_probe"
    finance_ops  = "table_lookup"
    business_ops = "ticket_lookup"
}

$validationRuns = @()

foreach ($run in $manifest["runs"]) {
    $mission = [string]$run["mission_type"]
    $missionDir = Join-Path (Join-Path $ResolvedSubmissionDir "runs") $mission

    Assert-True -Condition (Test-Path $missionDir) -Message "No existe el directorio de misión: $missionDir"

    $summaryPath = Join-Path $missionDir "summary.json"
    $trackPath   = Join-Path $missionDir "track_payload.json"
    $statePath   = Join-Path $missionDir "state.json"

    Assert-True -Condition (Test-Path $summaryPath) -Message "Falta summary.json para $mission"
    Assert-True -Condition (Test-Path $trackPath) -Message "Falta track_payload.json para $mission"
    Assert-True -Condition (Test-Path $statePath) -Message "Falta state.json para $mission"

    $summary = Read-JsonFile -Path $summaryPath
    $track   = Read-JsonFile -Path $trackPath
    $state   = Read-JsonFile -Path $statePath

    Assert-True -Condition ($summary["env_name"] -eq "aegisarena_env") -Message "$($mission): summary.env_name inválido"
    Assert-True -Condition ($summary["mission_type"] -eq $mission) -Message "$($mission): summary.mission_type inválido"
    Assert-True -Condition ($summary["validation"] -eq "AEGISARENA PURPLE EVAL OK") -Message "$($mission): summary.validation inválido"

    Assert-True -Condition ($track["env_name"] -eq "aegisarena_env") -Message "$($mission): track_payload.env_name inválido"
    Assert-True -Condition ($track["mission_type"] -eq $mission) -Message "$($mission): track_payload.mission_type inválido"

    Assert-True -Condition ($state["mission_type"] -eq $mission) -Message "$($mission): state.mission_type inválido"

    $checks = $summary["checks"]
    Assert-True -Condition ($null -ne $checks) -Message "$($mission): summary.checks no existe"

    Assert-True -Condition ([bool]$checks["health_ok"]) -Message "$($mission): checks.health_ok = false"
    Assert-True -Condition ([bool]$checks["reset_ok"]) -Message "$($mission): checks.reset_ok = false"
    Assert-True -Condition ([bool]$checks["state_ok"]) -Message "$($mission): checks.state_ok = false"

    if ([bool]$summary["require_success"]) {
        Assert-True -Condition ([bool]$state["done"]) -Message "$($mission): state.done = false con require_success"
        Assert-True -Condition ([bool]$state["success"]) -Message "$($mission): state.success = false con require_success"

        if ($checks.ContainsKey("success_path_ok") -and $null -ne $checks["success_path_ok"]) {
            Assert-True -Condition ([bool]$checks["success_path_ok"]) -Message "$($mission): success_path_ok = false"
        }
    }

    $expectedTool = $null
    if ($expectedToolByMission.ContainsKey($mission)) {
        $expectedTool = [string]$expectedToolByMission[$mission]
    }

    $actionPlan = @($summary["action_plan"])
    $observedQueryTool = Get-ObservedQueryTool -ActionPlan $actionPlan

    if ($null -ne $expectedTool -and -not [string]::IsNullOrWhiteSpace($observedQueryTool)) {
        Assert-True -Condition ($observedQueryTool -eq $expectedTool) -Message "$($mission): tool por default inválida. Esperada='$expectedTool' observada='$observedQueryTool'"
    }

    if ($mission -eq "business_ops") {
        Assert-True -Condition ([bool]$summary["heldout_mode"]) -Message "business_ops debe venir con heldout_mode=true en el paquete"
    }

    $validationRuns += [ordered]@{
        mission_type        = $mission
        heldout_mode        = [bool]$summary["heldout_mode"]
        require_success     = [bool]$summary["require_success"]
        observed_query_tool = $observedQueryTool
        expected_query_tool = $expectedTool
        final_success       = [bool]$state["success"]
        final_done          = [bool]$state["done"]
        validated           = $true
    }
}

$result = [ordered]@{
    validated_at   = (Get-Date).ToString("o")
    submission_dir = $ResolvedSubmissionDir
    env_name       = "aegisarena_env"
    required_missions = $RequiredMissionTypes
    runs           = $validationRuns
    validation     = "AEGISARENA PURPLE SUBMISSION VALID"
}

$ReportPath = Join-Path $ResolvedSubmissionDir "validation_report.json"
Save-JsonFile -Path $ReportPath -Data $result

Write-Host "AEGISARENA PURPLE SUBMISSION VALID"
Write-Host "Submission dir : $ResolvedSubmissionDir"
Write-Host "Report         : $ReportPath"

foreach ($validatedRun in $validationRuns) {
    Write-Host "  - $($validatedRun.mission_type) | heldout=$($validatedRun.heldout_mode) | tool=$($validatedRun.observed_query_tool) | success=$($validatedRun.final_success)"
}
