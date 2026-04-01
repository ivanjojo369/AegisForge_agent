
    [CmdletBinding()]
    param(
      [string]$RunDir = ""
    )

    Set-StrictMode -Version Latest
    $ErrorActionPreference = "Stop"

    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    Set-Location $RepoRoot

    if ([string]::IsNullOrWhiteSpace($RunDir)) {
      $latest = Get-ChildItem ".\artifacts\officeqa\run" -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
      if (-not $latest) { throw "No OfficeQA run artifact found." }
      $RunDir = $latest.FullName
    }

    $SubmissionDir = Join-Path $RepoRoot "artifacts\officeqa\submission"
    New-Item -ItemType Directory -Force -Path $SubmissionDir | Out-Null

    Copy-Item (Join-Path $RunDir "summary.json") (Join-Path $SubmissionDir "summary.json") -Force
    Copy-Item (Join-Path $RunDir "source_payload.json") (Join-Path $SubmissionDir "source_payload.json") -Force

    $manifest = [ordered]@{
      track = "officeqa"
      scenario = "taxwiztrap"
      source_run = $RunDir
      prepared_at = (Get-Date).ToString("s")
    }
    $manifest | ConvertTo-Json -Depth 10 | Set-Content -Path (Join-Path $SubmissionDir "package_manifest.json") -Encoding UTF8

    Write-Host "OFFICEQA PURPLE SUBMISSION PREPARE OK"
