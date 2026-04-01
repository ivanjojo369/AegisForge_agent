
param()

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ArtifactsRoot = Join-Path $RepoRoot "artifacts\crmarena"
$RunRoot = Join-Path $ArtifactsRoot "run"
$SubmissionRoot = Join-Path $ArtifactsRoot "submission\saleforceonespy"

if (-not (Test-Path $RunRoot)) {
    throw "No run artifacts found at $RunRoot"
}

$LatestRun = Get-ChildItem $RunRoot -Directory | Sort-Object Name -Descending | Select-Object -First 1
if (-not $LatestRun) {
    throw "No run directories available in $RunRoot"
}

New-Item -ItemType Directory -Path $SubmissionRoot -Force | Out-Null
Copy-Item (Join-Path $LatestRun.FullName "summary.json") (Join-Path $SubmissionRoot "summary.json") -Force
if (Test-Path (Join-Path $LatestRun.FullName "eval_payload.json")) {
    Copy-Item (Join-Path $LatestRun.FullName "eval_payload.json") (Join-Path $SubmissionRoot "eval_payload.json") -Force
}

$Manifest = [ordered]@{
    track = "crmarena"
    scenario = "saleforceonespy"
    latest_run = $LatestRun.Name
    status = "prepared"
}
$Manifest | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $SubmissionRoot "package_manifest.json") -Encoding UTF8

Write-Host "CRMARENA PURPLE SUBMISSION PREPARE OK" -ForegroundColor Green
Write-Host ("Submission package written to: " + $SubmissionRoot) -ForegroundColor Cyan
