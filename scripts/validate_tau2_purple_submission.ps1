param(
  [string]$SubmissionDir = ".\artifacts\tau2\submission\aegisforge_tau2_quipu_lab",
  [string]$ExpectedDomain = "quipu_lab",
  [string]$ExpectedTaskSplit = "base",
  [switch]$CheckLive,
  [int]$MaxAttempts = 5,
  [int]$DelaySeconds = 1
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Section {
  param([string]$Message)
  Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Assert-Condition {
  param(
    [Parameter(Mandatory = $true)][bool]$Condition,
    [Parameter(Mandatory = $true)][string]$Message
  )
  if (-not $Condition) {
    throw $Message
  }
}

function Assert-PathExists {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Label
  )
  if (-not (Test-Path -LiteralPath $Path)) {
    throw ("Required {0} not found: {1}" -f $Label, $Path)
  }
}

function Load-JsonFile {
  param([Parameter(Mandatory = $true)][string]$Path)
  return (Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json)
}

function Invoke-JsonWithRetry {
  param(
    [Parameter(Mandatory = $true)][string]$Uri,
    [int]$Attempts = 5,
    [int]$SleepSeconds = 1
  )

  for ($i = 1; $i -le $Attempts; $i++) {
    try {
      return Invoke-RestMethod -Uri $Uri -Method GET -TimeoutSec 10
    } catch {
      if ($i -eq $Attempts) {
        throw
      }
      Start-Sleep -Seconds $SleepSeconds
    }
  }
}

Assert-PathExists -Path $SubmissionDir -Label 'submission directory'
$ResolvedSubmissionDir = (Resolve-Path -LiteralPath $SubmissionDir).Path
$SubmissionJsonPath = Join-Path $ResolvedSubmissionDir 'submission.json'
$MetricsPath = Join-Path $ResolvedSubmissionDir 'metrics.json'
$RunManifestPath = Join-Path $ResolvedSubmissionDir 'run_manifest.json'
$PackageManifestPath = Join-Path $ResolvedSubmissionDir 'package_manifest.json'
$EvidenceDir = Join-Path $ResolvedSubmissionDir 'evidence'
$LogsDir = Join-Path $ResolvedSubmissionDir 'logs'
$TrajectoriesDir = Join-Path $ResolvedSubmissionDir 'trajectories'
$HealthPath = Join-Path $EvidenceDir 'health.json'
$AgentCardPath = Join-Path $EvidenceDir 'agent_card.json'
$CatalogSummaryPath = Join-Path $EvidenceDir 'task_catalog_summary.json'

Write-Section 'Checking submission structure'
Assert-PathExists -Path $SubmissionJsonPath -Label 'submission.json'
Assert-PathExists -Path $MetricsPath -Label 'metrics.json'
Assert-PathExists -Path $RunManifestPath -Label 'run_manifest.json'
Assert-PathExists -Path $PackageManifestPath -Label 'package_manifest.json'
Assert-PathExists -Path $EvidenceDir -Label 'evidence directory'
Assert-PathExists -Path $LogsDir -Label 'logs directory'
Assert-PathExists -Path $TrajectoriesDir -Label 'trajectories directory'
Assert-PathExists -Path $HealthPath -Label 'health evidence'
Assert-PathExists -Path $AgentCardPath -Label 'agent-card evidence'
Assert-PathExists -Path $CatalogSummaryPath -Label 'task catalog summary'

$submission = Load-JsonFile -Path $SubmissionJsonPath
$metrics = Load-JsonFile -Path $MetricsPath
$runManifest = Load-JsonFile -Path $RunManifestPath
$packageManifest = Load-JsonFile -Path $PackageManifestPath
$health = Load-JsonFile -Path $HealthPath
$agentCard = Load-JsonFile -Path $AgentCardPath
$catalogSummary = Load-JsonFile -Path $CatalogSummaryPath

Write-Section 'Checking metadata consistency'
Assert-Condition -Condition ($submission.run.domain -eq $ExpectedDomain) -Message ("Unexpected domain in submission.json: {0}" -f $submission.run.domain)
Assert-Condition -Condition ($runManifest.domain -eq $ExpectedDomain) -Message ("Unexpected domain in run_manifest.json: {0}" -f $runManifest.domain)
Assert-Condition -Condition ($submission.run.task_split -eq $ExpectedTaskSplit) -Message ("Unexpected task_split in submission.json: {0}" -f $submission.run.task_split)
Assert-Condition -Condition ($runManifest.task_split -eq $ExpectedTaskSplit) -Message ("Unexpected task_split in run_manifest.json: {0}" -f $runManifest.task_split)
Assert-Condition -Condition ($runManifest.status -eq 'completed') -Message ("Run status is not completed: {0}" -f $runManifest.status)
Assert-Condition -Condition ([bool]$metrics.adapter_tests_passed) -Message 'adapter_tests_passed is false.'
Assert-Condition -Condition ([bool]$metrics.smoke_tests_passed) -Message 'smoke_tests_passed is false.'
Assert-Condition -Condition ([bool]$metrics.docker_build_passed) -Message 'docker_build_passed is false.'
Assert-Condition -Condition ([bool]$metrics.health_ok) -Message 'health_ok is false.'
Assert-Condition -Condition ([bool]$metrics.agent_card_ok) -Message 'agent_card_ok is false.'
Assert-Condition -Condition ([bool]$metrics.local_e2e_ok) -Message 'local_e2e_ok is false.'
Assert-Condition -Condition ($health.status -eq 'ok') -Message 'health.json does not report status=ok.'
Assert-Condition -Condition ($null -ne $agentCard.name) -Message 'agent_card.json is missing name.'
Assert-Condition -Condition ($null -ne $agentCard.url) -Message 'agent_card.json is missing url.'
Assert-Condition -Condition ($packageManifest.package_status -eq 'prepared') -Message 'package_manifest.json is not marked as prepared.'

Write-Section 'Checking task catalog consistency'
$trajectoryFiles = @(Get-ChildItem -LiteralPath $TrajectoriesDir -File | Sort-Object Name)
$logFiles = @(Get-ChildItem -LiteralPath $LogsDir -File | Sort-Object Name)
Assert-Condition -Condition ($trajectoryFiles.Count -ge 1) -Message 'No trajectory files found in trajectories directory.'
Assert-Condition -Condition ($logFiles.Count -ge 1) -Message 'No log files found in logs directory.'

$catalogTaskIds = @($catalogSummary.task_ids)
$runTaskIds = @($runManifest.selected_task_ids)
$submissionTaskIds = @($submission.run.selected_task_ids)
Assert-Condition -Condition ($catalogTaskIds.Count -ge 1) -Message 'task_catalog_summary.json lists no tasks.'
Assert-Condition -Condition ($runManifest.task_count -eq $catalogTaskIds.Count) -Message 'run_manifest task_count does not match task_catalog_summary.'
Assert-Condition -Condition ($submission.run.task_count -eq $catalogTaskIds.Count) -Message 'submission.run.task_count does not match task_catalog_summary.'
Assert-Condition -Condition (($runTaskIds -join '|') -eq ($catalogTaskIds -join '|')) -Message 'run_manifest selected_task_ids do not match task_catalog_summary.'
Assert-Condition -Condition (($submissionTaskIds -join '|') -eq ($catalogTaskIds -join '|')) -Message 'submission selected_task_ids do not match task_catalog_summary.'
Assert-Condition -Condition ($submission.artifacts.trajectories_count -eq $trajectoryFiles.Count) -Message 'submission.artifacts.trajectories_count does not match actual files.'

foreach ($trajectoryFile in $trajectoryFiles) {
  Assert-Condition -Condition ($trajectoryFile.Length -gt 0) -Message ("Empty trajectory file: {0}" -f $trajectoryFile.FullName)
}

if ($CheckLive.IsPresent) {
  Write-Section 'Checking live endpoints referenced by submission package'
  $baseUrl = $submission.run.base_url
  Assert-Condition -Condition (-not [string]::IsNullOrWhiteSpace($baseUrl)) -Message 'submission.run.base_url is empty.'
  $baseUrl = $baseUrl.TrimEnd('/')

  $liveHealth = Invoke-JsonWithRetry -Uri ($baseUrl + '/health') -Attempts $MaxAttempts -SleepSeconds $DelaySeconds
  Assert-Condition -Condition ($liveHealth.status -eq 'ok') -Message 'Live /health check did not return status=ok.'

  $liveCard = Invoke-JsonWithRetry -Uri ($baseUrl + '/.well-known/agent-card.json') -Attempts $MaxAttempts -SleepSeconds $DelaySeconds
  Assert-Condition -Condition ($null -ne $liveCard.name) -Message 'Live agent-card response is missing name.'
}

$summary = [ordered]@{
  package_structure_ok = $true
  domain_ok = $true
  task_split_ok = $true
  run_status_ok = $true
  task_catalog_ok = $true
  adapter_tests_ok = [bool]$metrics.adapter_tests_passed
  smoke_tests_ok = [bool]$metrics.smoke_tests_passed
  docker_build_ok = [bool]$metrics.docker_build_passed
  health_ok = [bool]$metrics.health_ok
  agent_card_ok = [bool]$metrics.agent_card_ok
  local_e2e_ok = [bool]$metrics.local_e2e_ok
  task_count = $catalogTaskIds.Count
  trajectories_count = $trajectoryFiles.Count
  logs_count = $logFiles.Count
  live_check = [bool]$CheckLive.IsPresent
}

Write-Host "`nVALIDATION OK" -ForegroundColor Green
$summary | ConvertTo-Json -Depth 10 | Write-Host
