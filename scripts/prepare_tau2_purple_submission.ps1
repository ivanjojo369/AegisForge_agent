param(
  [string]$InputDir = ".\artifacts\tau2\simulations\aegisforge_tau2_quipu_lab",
  [string]$OutputDir = ".\artifacts\tau2\submission\aegisforge_tau2_quipu_lab",
  [string]$AgentName = "AegisForge (QuipuLoop)",
  [string]$Track = "phase2-purple",
  [string]$Capability = "tau2",
  [string]$ModelName = "aegisforge-local",
  [string]$Organization = "",
  [string]$ContactEmail = "",
  [string]$Notes = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Section {
  param([string]$Message)
  Write-Host "`n==> $Message" -ForegroundColor Cyan
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

function Save-JsonFile {
  param(
    [Parameter(Mandatory = $true)]$Object,
    [Parameter(Mandatory = $true)][string]$Path
  )

  $json = $Object | ConvertTo-Json -Depth 30
  Set-Content -LiteralPath $Path -Value $json -Encoding UTF8
}

function Copy-DirectoryContents {
  param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Destination
  )

  if (-not (Test-Path -LiteralPath $Source)) {
    return
  }

  $null = New-Item -ItemType Directory -Force -Path $Destination
  Get-ChildItem -Path $Source -Force | Copy-Item -Destination $Destination -Recurse -Force
}

$ResolvedInputDir = (Resolve-Path -LiteralPath $InputDir).Path
$RunManifestPath = Join-Path $ResolvedInputDir 'run_manifest.json'
$MetricsPath = Join-Path $ResolvedInputDir 'metrics.json'
$EvidenceDir = Join-Path $ResolvedInputDir 'evidence'
$LogsDir = Join-Path $ResolvedInputDir 'logs'
$TrajectoriesDir = Join-Path $ResolvedInputDir 'trajectories'
$HealthPath = Join-Path $EvidenceDir 'health.json'
$AgentCardPath = Join-Path $EvidenceDir 'agent_card.json'
$CatalogSummaryPath = Join-Path $EvidenceDir 'task_catalog_summary.json'

Write-Section 'Checking required run artifacts'
Assert-PathExists -Path $RunManifestPath -Label 'run manifest'
Assert-PathExists -Path $MetricsPath -Label 'metrics file'
Assert-PathExists -Path $EvidenceDir -Label 'evidence directory'
Assert-PathExists -Path $LogsDir -Label 'logs directory'
Assert-PathExists -Path $TrajectoriesDir -Label 'trajectories directory'
Assert-PathExists -Path $HealthPath -Label 'health evidence'
Assert-PathExists -Path $AgentCardPath -Label 'agent-card evidence'
Assert-PathExists -Path $CatalogSummaryPath -Label 'task catalog summary'

$runManifest = Load-JsonFile -Path $RunManifestPath
$metrics = Load-JsonFile -Path $MetricsPath
$health = Load-JsonFile -Path $HealthPath
$agentCard = Load-JsonFile -Path $AgentCardPath
$catalogSummary = Load-JsonFile -Path $CatalogSummaryPath

if ($runManifest.status -ne 'completed') { throw ("Input run is not completed. status={0}" -f $runManifest.status) }
if (-not $metrics.adapter_tests_passed) { throw 'Adapter tests are not marked as passed.' }
if (-not $metrics.smoke_tests_passed) { throw 'Smoke tests are not marked as passed.' }
if (-not $metrics.docker_build_passed) { throw 'Docker build is not marked as passed.' }
if (-not $metrics.health_ok) { throw 'health_ok is false in metrics.json.' }
if (-not $metrics.agent_card_ok) { throw 'agent_card_ok is false in metrics.json.' }
if (-not $metrics.local_e2e_ok) { throw 'local_e2e_ok is false in metrics.json.' }
if ($health.status -ne 'ok') { throw 'health.json does not report status=ok.' }

Write-Section 'Preparing submission directory'
$ResolvedOutputDir = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $OutputDir))
if (Test-Path -LiteralPath $ResolvedOutputDir) {
  Remove-Item -LiteralPath $ResolvedOutputDir -Recurse -Force
}
$null = New-Item -ItemType Directory -Force -Path $ResolvedOutputDir
$OutEvidenceDir = Join-Path $ResolvedOutputDir 'evidence'
$OutLogsDir = Join-Path $ResolvedOutputDir 'logs'
$OutTrajDir = Join-Path $ResolvedOutputDir 'trajectories'
$null = New-Item -ItemType Directory -Force -Path $OutEvidenceDir, $OutLogsDir, $OutTrajDir

Copy-Item -LiteralPath $RunManifestPath -Destination (Join-Path $ResolvedOutputDir 'run_manifest.json') -Force
Copy-Item -LiteralPath $MetricsPath -Destination (Join-Path $ResolvedOutputDir 'metrics.json') -Force
Copy-DirectoryContents -Source $EvidenceDir -Destination $OutEvidenceDir
Copy-DirectoryContents -Source $LogsDir -Destination $OutLogsDir
Copy-DirectoryContents -Source $TrajectoriesDir -Destination $OutTrajDir

$trajectoryFiles = @()
if (Test-Path -LiteralPath $OutTrajDir) {
  $trajectoryFiles = @(Get-ChildItem -LiteralPath $OutTrajDir -File | Sort-Object Name | Select-Object -ExpandProperty Name)
}

$skillCount = 0
if ($null -ne $agentCard.skills) {
  try { $skillCount = @($agentCard.skills).Count } catch { $skillCount = 0 }
}

$submission = [ordered]@{
  submission_type = 'tau2-purple'
  prepared_at = (Get-Date).ToString('o')
  agent = [ordered]@{
    name = $AgentName
    model_name = $ModelName
    image_tag = $runManifest.image_tag
    track = $Track
    capability = $Capability
    organization = $Organization
    contact_email = $ContactEmail
  }
  run = [ordered]@{
    domain = $runManifest.domain
    task_split = $runManifest.task_split
    task_selection = $runManifest.task_selection
    task_count = $runManifest.task_count
    selected_task_ids = $runManifest.selected_task_ids
    save_to = $runManifest.save_to
    base_url = $runManifest.base_url
    port = $runManifest.port
    started_at = $runManifest.started_at
    finished_at = $runManifest.finished_at
    status = $runManifest.status
  }
  validation = [ordered]@{
    adapter_tests_passed = [bool]$metrics.adapter_tests_passed
    smoke_tests_passed = [bool]$metrics.smoke_tests_passed
    docker_build_passed = [bool]$metrics.docker_build_passed
    health_ok = [bool]$metrics.health_ok
    agent_card_ok = [bool]$metrics.agent_card_ok
    local_e2e_ok = [bool]$metrics.local_e2e_ok
  }
  artifacts = [ordered]@{
    metrics = 'metrics.json'
    run_manifest = 'run_manifest.json'
    evidence_dir = 'evidence'
    logs_dir = 'logs'
    trajectories_dir = 'trajectories'
    trajectory_files = $trajectoryFiles
    trajectories_count = $trajectoryFiles.Count
  }
  task_catalog = [ordered]@{
    selection_mode = $catalogSummary.selection_mode
    requested_split = $catalogSummary.requested_split
    task_count = $catalogSummary.task_count
    task_ids = $catalogSummary.task_ids
  }
  agent_card = [ordered]@{
    name = $agentCard.name
    url = $agentCard.url
    version = $agentCard.version
    preferredTransport = $agentCard.preferredTransport
    skills_count = $skillCount
  }
  notes = $Notes
}

$packageManifest = [ordered]@{
  package_status = 'prepared'
  prepared_at = (Get-Date).ToString('o')
  input_dir = $ResolvedInputDir
  output_dir = $ResolvedOutputDir
  files = [ordered]@{
    submission_json = 'submission.json'
    metrics_json = 'metrics.json'
    run_manifest_json = 'run_manifest.json'
    evidence_dir = 'evidence'
    logs_dir = 'logs'
    trajectories_dir = 'trajectories'
  }
}

Save-JsonFile -Object $submission -Path (Join-Path $ResolvedOutputDir 'submission.json')
Save-JsonFile -Object $packageManifest -Path (Join-Path $ResolvedOutputDir 'package_manifest.json')

Write-Host "`nTAU2 PURPLE SUBMISSION PREPARE OK" -ForegroundColor Green
Write-Host ("Submission package written to: {0}" -f $ResolvedOutputDir) -ForegroundColor Green
