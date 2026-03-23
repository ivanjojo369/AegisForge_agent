[CmdletBinding()]
param(
    [string]$RunRoot = "artifacts\aegisarena\run",
    [string]$OutputDir = "artifacts\aegisarena\submission",
    [string[]]$MissionTypes = @("game_ops", "finance_ops", "business_ops"),
    [bool]$RequireHeldoutForBusiness = $true,
    [bool]$CopyOptionalJson = $true
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$RunRootPath = Join-Path $RepoRoot $RunRoot
$OutputRootPath = Join-Path $RepoRoot $OutputDir

if (-not (Test-Path $RunRootPath)) {
    throw "No existe RunRoot: $RunRootPath"
}

New-Item -ItemType Directory -Force -Path $OutputRootPath | Out-Null

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

function Get-LatestSuccessfulRunForMission {
    param(
        [Parameter(Mandatory = $true)]
        [string]$MissionType,
        [Parameter(Mandatory = $true)]
        [string]$RootPath,
        [bool]$RequireHeldoutForBusiness = $true
    )

    $runDirs = Get-ChildItem -LiteralPath $RootPath -Directory | Sort-Object Name -Descending

    foreach ($dir in $runDirs) {
        $summaryPath = Join-Path $dir.FullName "summary.json"
        $trackPath = Join-Path $dir.FullName "track_payload.json"
        $statePath = Join-Path $dir.FullName "state.json"

        if (-not (Test-Path $summaryPath) -or -not (Test-Path $trackPath) -or -not (Test-Path $statePath)) {
            continue
        }

        try {
            $summary = Read-JsonFile -Path $summaryPath
            $track = Read-JsonFile -Path $trackPath
            $state = Read-JsonFile -Path $statePath
        }
        catch {
            continue
        }

        if ($summary["env_name"] -ne "aegisarena_env") { continue }
        if ($summary["mission_type"] -ne $MissionType) { continue }
        if ($summary["validation"] -ne "AEGISARENA PURPLE EVAL OK") { continue }
        if (-not $summary.ContainsKey("checks")) { continue }

        $checks = $summary["checks"]
        if (-not [bool]$checks["health_ok"]) { continue }
        if (-not [bool]$checks["reset_ok"]) { continue }
        if (-not [bool]$checks["state_ok"]) { continue }

        $requireSuccess = [bool]$summary["require_success"]
        if ($requireSuccess) {
            if (-not [bool]$state["done"]) { continue }
            if (-not [bool]$state["success"]) { continue }

            if ($checks.ContainsKey("success_path_ok") -and $null -ne $checks["success_path_ok"]) {
                if (-not [bool]$checks["success_path_ok"]) { continue }
            }
        }

        $heldoutMode = [bool]$summary["heldout_mode"]
        if ($RequireHeldoutForBusiness -and $MissionType -eq "business_ops" -and -not $heldoutMode) {
            continue
        }

        return @{
            run_dir = $dir.FullName
            summary = $summary
            track   = $track
            state   = $state
        }
    }

    throw "No encontré una corrida válida reciente para mission_type='$MissionType' en $RootPath"
}

$selectedRuns = @()
foreach ($mission in $MissionTypes) {
    $selectedRuns += ,(
        Get-LatestSuccessfulRunForMission `
            -MissionType $mission `
            -RootPath $RunRootPath `
            -RequireHeldoutForBusiness $RequireHeldoutForBusiness
    )
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$submissionDir = Join-Path $OutputRootPath $stamp
$runsDir = Join-Path $submissionDir "runs"
New-Item -ItemType Directory -Force -Path $runsDir | Out-Null

$manifestRuns = @()

foreach ($entry in $selectedRuns) {
    $summary = $entry["summary"]
    $mission = [string]$summary["mission_type"]
    $sourceRunDir = [string]$entry["run_dir"]
    $targetMissionDir = Join-Path $runsDir $mission

    New-Item -ItemType Directory -Force -Path $targetMissionDir | Out-Null

    $requiredFiles = @("summary.json", "track_payload.json", "state.json")
    $optionalFiles = @("health.json", "reset.json", "steps.json")

    foreach ($name in $requiredFiles) {
        Copy-Item -LiteralPath (Join-Path $sourceRunDir $name) -Destination (Join-Path $targetMissionDir $name) -Force
    }

    if ($CopyOptionalJson) {
        foreach ($name in $optionalFiles) {
            $source = Join-Path $sourceRunDir $name
            if (Test-Path $source) {
                Copy-Item -LiteralPath $source -Destination (Join-Path $targetMissionDir $name) -Force
            }
        }
    }

    $manifestRuns += [ordered]@{
        mission_type    = $mission
        source_run_dir  = $sourceRunDir
        heldout_mode    = [bool]$summary["heldout_mode"]
        require_success = [bool]$summary["require_success"]
        validation      = [string]$summary["validation"]
        copied_to       = $targetMissionDir
    }
}

$manifest = [ordered]@{
    package_name              = "aegisarena_purple_submission"
    created_at                = (Get-Date).ToString("o")
    repo_root                 = $RepoRoot
    env_name                  = "aegisarena_env"
    output_dir                = $submissionDir
    run_root                  = $RunRootPath
    mission_types             = $MissionTypes
    business_requires_heldout = $RequireHeldoutForBusiness
    runs                      = $manifestRuns
    validation_hint           = ".\scripts\validate_aegisarena_purple_submission.ps1"
}

$readmeLines = @(
    "AEGISARENA PURPLE SUBMISSION",
    "",
    "Package dir: $submissionDir",
    "Created at : $($manifest.created_at)",
    "Env        : aegisarena_env",
    "",
    "Included runs:"
)

foreach ($run in $manifestRuns) {
    $readmeLines += "- $($run.mission_type) | heldout=$($run.heldout_mode) | require_success=$($run.require_success)"
}

$readmeLines += ""
$readmeLines += "Siguiente paso recomendado:"
$readmeLines += ".\scripts\validate_aegisarena_purple_submission.ps1 -SubmissionDir `"$submissionDir`""

Save-JsonFile -Path (Join-Path $submissionDir "manifest.json") -Data $manifest
[System.IO.File]::WriteAllLines((Join-Path $submissionDir "README.txt"), $readmeLines, [System.Text.Encoding]::UTF8)

Write-Host "AEGISARENA PURPLE SUBMISSION PREPARED"
Write-Host "Submission dir: $submissionDir"
Write-Host "Manifest      : $(Join-Path $submissionDir 'manifest.json')"
Write-Host "README        : $(Join-Path $submissionDir 'README.txt')"

foreach ($run in $manifestRuns) {
    Write-Host "  - $($run.mission_type) => $($run.copied_to)"
}
