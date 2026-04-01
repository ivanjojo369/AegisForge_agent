param()

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$SubmissionRoot = Join-Path $RepoRoot "artifacts\crmarena\submission\saleforceonespy"

if (-not (Test-Path $SubmissionRoot)) {
    throw "Submission directory not found: $SubmissionRoot"
}

$Checks = [ordered]@{
    summary_ok = Test-Path (Join-Path $SubmissionRoot "summary.json")
    package_manifest_ok = Test-Path (Join-Path $SubmissionRoot "package_manifest.json")
    eval_payload_present = Test-Path (Join-Path $SubmissionRoot "eval_payload.json")
}

$Ok = $true
foreach ($Value in $Checks.Values) {
    if (-not $Value) { $Ok = $false }
}

if (-not $Ok) {
    throw ("VALIDATION FAILED`n" + ($Checks | ConvertTo-Json -Depth 8))
}

Write-Host "VALIDATION OK" -ForegroundColor Green
$Checks | ConvertTo-Json -Depth 8
