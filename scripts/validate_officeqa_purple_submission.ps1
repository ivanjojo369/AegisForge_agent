
    [CmdletBinding()]
    param(
      [string]$SubmissionDir = ".\artifacts\officeqa\submission"
    )

    Set-StrictMode -Version Latest
    $ErrorActionPreference = "Stop"

    $required = @(
      "summary.json",
      "source_payload.json",
      "package_manifest.json"
    )

    foreach ($name in $required) {
      $path = Join-Path $SubmissionDir $name
      if (-not (Test-Path $path)) {
        throw "Missing required file: $path"
      }
    }

    Write-Host "VALIDATION OK"
