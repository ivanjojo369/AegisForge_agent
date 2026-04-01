param(
  [string]$Domain = "quipu_lab",
  [string]$TaskSplit = "base",
  [string]$SaveTo = "aegisforge_tau2_quipu_lab",
  [int]$Port = 8002,
  [string]$ImageTag = "aegisforge-agent:tau2-purple",
  [string]$BaseUrl = "",
  [string[]]$TaskIds = @(),
  [switch]$UseSmokeTasks,
  [int]$MaxTasks = 0,
  [string]$AdapterTestPath = "tests\test_adapters\test_tau2_adapter.py",
  [string]$SmokeTestPath = "tests\tests_envs\test_tau2_quipu_lab_smoke.py"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

chcp 65001 > $null
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
  $BaseUrl = "http://127.0.0.1:$Port"
}
$BaseUrl = $BaseUrl.TrimEnd('/')

$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
  throw "Expected repo virtualenv python not found: $VenvPython"
}

$ArtifactsRoot = Join-Path $RepoRoot "artifacts\tau2\simulations\$SaveTo"
$LogsDir = Join-Path $ArtifactsRoot "logs"
$EvidenceDir = Join-Path $ArtifactsRoot "evidence"
$TrajDir = Join-Path $ArtifactsRoot "trajectories"
$RunManifestPath = Join-Path $ArtifactsRoot "run_manifest.json"
$MetricsPath = Join-Path $ArtifactsRoot "metrics.json"
$TraceSummaryPath = Join-Path $EvidenceDir "task_catalog_summary.json"

$null = New-Item -ItemType Directory -Force -Path $ArtifactsRoot, $LogsDir, $EvidenceDir, $TrajDir

function Write-Section {
  param([string]$Message)
  Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Save-Json {
  param(
    [Parameter(Mandatory = $true)]$Object,
    [Parameter(Mandatory = $true)][string]$Path
  )

  $json = $Object | ConvertTo-Json -Depth 30
  Set-Content -Path $Path -Value $json -Encoding UTF8
}

function Load-Json {
  param([Parameter(Mandatory = $true)][string]$Path)
  return (Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json)
}

function Resolve-CommandPath {
  param([Parameter(Mandatory = $true)][string]$Name)

  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  if ($null -ne $cmd) {
    return $cmd.Source
  }

  throw "Required command not found in PATH: $Name"
}

function Convert-ToArgumentString {
  param([Parameter(Mandatory = $true)][string[]]$ArgumentList)

  $escaped = foreach ($arg in $ArgumentList) {
    if ($null -eq $arg) {
      '""'
      continue
    }

    $s = [string]$arg
    if ($s.Contains('"')) {
      $s = $s.Replace('"', '\"')
    }

    if ($s -match '\s') {
      '"' + $s + '"'
    } else {
      $s
    }
  }

  return ($escaped -join ' ')
}

function Invoke-LoggedCommand {
  param(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [Parameter(Mandatory = $true)][string[]]$ArgumentList,
    [Parameter(Mandatory = $true)][string]$LogPrefix,
    [Parameter(Mandatory = $true)][string]$WorkingDirectory,
    [hashtable]$Environment = @{}
  )

  $stdoutPath = Join-Path $LogsDir ("{0}.stdout.log" -f $LogPrefix)
  $stderrPath = Join-Path $LogsDir ("{0}.stderr.log" -f $LogPrefix)

  if (Test-Path $stdoutPath) { Remove-Item $stdoutPath -Force }
  if (Test-Path $stderrPath) { Remove-Item $stderrPath -Force }

  if (Test-Path $FilePath) {
    $resolvedFilePath = (Resolve-Path $FilePath).Path
  } else {
    $resolvedFilePath = Resolve-CommandPath -Name $FilePath
  }

  $argumentString = Convert-ToArgumentString -ArgumentList $ArgumentList
  Write-Host ("Running: {0} {1}" -f $resolvedFilePath, $argumentString) -ForegroundColor Yellow

  $startInfo = New-Object System.Diagnostics.ProcessStartInfo
  $startInfo.FileName = $resolvedFilePath
  $startInfo.Arguments = $argumentString
  $startInfo.WorkingDirectory = $WorkingDirectory
  $startInfo.UseShellExecute = $false
  $startInfo.RedirectStandardOutput = $true
  $startInfo.RedirectStandardError = $true
  $startInfo.CreateNoWindow = $true

  foreach ($entry in $Environment.GetEnumerator()) {
    $startInfo.EnvironmentVariables[$entry.Key] = [string]$entry.Value
  }

  $proc = New-Object System.Diagnostics.Process
  $proc.StartInfo = $startInfo
  [void]$proc.Start()
  $stdout = $proc.StandardOutput.ReadToEnd()
  $stderr = $proc.StandardError.ReadToEnd()
  $proc.WaitForExit()

  Set-Content -Path $stdoutPath -Value $stdout -Encoding UTF8
  Set-Content -Path $stderrPath -Value $stderr -Encoding UTF8

  if ($stdout) { $stdout.TrimEnd("`r", "`n") -split "`r?`n" | ForEach-Object { if ($_ -ne "") { Write-Host $_ } } }
  if ($stderr) { $stderr.TrimEnd("`r", "`n") -split "`r?`n" | ForEach-Object { if ($_ -ne "") { Write-Host $_ } } }

  if ($proc.ExitCode -ne 0) {
    throw ("Command failed with exit code {0}: {1} {2}" -f $proc.ExitCode, $resolvedFilePath, $argumentString)
  }
}

function Invoke-JsonWithRetry {
  param(
    [Parameter(Mandatory = $true)][string]$Uri,
    [int]$MaxAttempts = 10,
    [int]$DelaySeconds = 1
  )

  for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
    try {
      return Invoke-RestMethod -Uri $Uri -Method GET -TimeoutSec 10
    } catch {
      if ($attempt -eq $MaxAttempts) {
        throw
      }
      Start-Sleep -Seconds $DelaySeconds
    }
  }
}

$CatalogScriptPath = Join-Path $ArtifactsRoot "_emit_tau2_catalog_traces.py"
$CatalogScript = @'
import json
import os
import sys
from pathlib import Path

repo_root = Path(os.environ["AEGIS_REPO_ROOT"])
traj_dir = Path(os.environ["AEGIS_TRAJ_DIR"])
evidence_path = Path(os.environ["AEGIS_SUMMARY_PATH"])
requested_ids = json.loads(os.environ.get("AEGIS_TASK_IDS_JSON", "[]"))
use_smoke = os.environ.get("AEGIS_USE_SMOKE", "0") == "1"
requested_split = os.environ.get("AEGIS_TASK_SPLIT", "base")
max_tasks = int(os.environ.get("AEGIS_MAX_TASKS", "0") or 0)

for candidate in (repo_root / "src", repo_root):
    candidate_str = str(candidate)
    if candidate.exists() and candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from aegisforge.adapters.tau2.quipu_lab.tasks import (
    build_minimal_result,
    get_smoke_tasks,
    get_task_by_id,
    get_tasks,
    task_to_trace_seed,
)

selection_mode = "fallback_sample"
if requested_ids:
    tasks = [get_task_by_id(task_id) for task_id in requested_ids]
    selection_mode = "task_ids"
elif use_smoke:
    tasks = list(get_smoke_tasks())
    selection_mode = "smoke"
else:
    tasks = list(get_tasks(requested_split))
    selection_mode = f"split:{requested_split}"

if max_tasks > 0:
    tasks = tasks[:max_tasks]

traj_dir.mkdir(parents=True, exist_ok=True)

summary = {
    "selection_mode": selection_mode,
    "requested_split": requested_split,
    "task_count": len(tasks),
    "task_ids": [task.task_id for task in tasks],
    "trajectory_files": [],
}

for task in tasks:
    result = build_minimal_result(task)
    event_rows = [
        {
            "event_type": "task_loaded",
            "task_id": task.task_id,
            "title": task.title,
            "metadata": dict(task.metadata),
            "trace_seed": task_to_trace_seed(task),
        },
        {
            "event_type": "result_built",
            "task_id": task.task_id,
            "result": result,
        },
    ]
    out_name = f"{task.task_id}.jsonl"
    out_path = traj_dir / out_name
    with out_path.open("w", encoding="utf-8") as handle:
        for row in event_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary["trajectory_files"].append(out_name)

with evidence_path.open("w", encoding="utf-8") as handle:
    json.dump(summary, handle, ensure_ascii=False, indent=2)
'@
Set-Content -LiteralPath $CatalogScriptPath -Value $CatalogScript -Encoding UTF8

$startedAt = (Get-Date).ToString("o")
$cid = $null
$health = $null
$card = $null
$catalogSummary = $null

try {
  Write-Section "Running tau2 adapter tests"
  Invoke-LoggedCommand -FilePath $VenvPython -ArgumentList @("-m", "pytest", $AdapterTestPath, "-q") -LogPrefix "pytest_tau2_adapter" -WorkingDirectory $RepoRoot

  Write-Section "Running quipu_lab smoke tests"
  Invoke-LoggedCommand -FilePath $VenvPython -ArgumentList @("-m", "pytest", $SmokeTestPath, "-q") -LogPrefix "pytest_quipu_lab_smoke" -WorkingDirectory $RepoRoot

  Write-Section "Building docker image"
  Invoke-LoggedCommand -FilePath "docker" -ArgumentList @("build", "-t", $ImageTag, ".") -LogPrefix "docker_build" -WorkingDirectory $RepoRoot

  Write-Section ("Running container on {0}" -f $BaseUrl)
  $dockerRunOutput = (& (Resolve-CommandPath -Name "docker") run -d --rm -p "${Port}:${Port}" $ImageTag --port $Port) 2>&1
  $dockerRunText = ($dockerRunOutput | Out-String).Trim()
  Set-Content -Path (Join-Path $LogsDir "docker_run.log") -Value $dockerRunText -Encoding UTF8

  if ($LASTEXITCODE -ne 0) {
    $nextPort = $Port + 1
    throw ("docker run failed. Port {0} is probably already in use. Try -Port {1} (or stop the container using it)." -f $Port, $nextPort)
  }

  $cid = $dockerRunText
  if (-not ($cid -match '^[0-9a-f]{12,}$')) {
    throw ("docker run did not return a valid container id: {0}" -f $cid)
  }

  Write-Section "Checking health"
  $health = Invoke-JsonWithRetry -Uri "$BaseUrl/health"
  $health | ConvertTo-Json -Depth 20 | Write-Host
  Save-Json -Object $health -Path (Join-Path $EvidenceDir "health.json")

  Write-Section "Checking agent card"
  $card = Invoke-JsonWithRetry -Uri "$BaseUrl/.well-known/agent-card.json"
  $card | ConvertTo-Json -Depth 20 | Write-Host
  Save-Json -Object $card -Path (Join-Path $EvidenceDir "agent_card.json")

  Write-Section "Generating local multi-task trajectory evidence"
  $envMap = @{
    AEGIS_REPO_ROOT = $RepoRoot
    AEGIS_TRAJ_DIR = $TrajDir
    AEGIS_SUMMARY_PATH = $TraceSummaryPath
    AEGIS_TASK_IDS_JSON = (ConvertTo-Json $TaskIds -Compress)
    AEGIS_USE_SMOKE = $(if ($UseSmokeTasks.IsPresent) { "1" } else { "0" })
    AEGIS_TASK_SPLIT = $TaskSplit
    AEGIS_MAX_TASKS = [string]$MaxTasks
    PYTHONPATH = "$RepoRoot\src;$RepoRoot"
  }
  Invoke-LoggedCommand -FilePath $VenvPython -ArgumentList @($CatalogScriptPath) -LogPrefix "emit_tau2_catalog_traces" -WorkingDirectory $RepoRoot -Environment $envMap
  $catalogSummary = Load-Json -Path $TraceSummaryPath

  $finishedAt = (Get-Date).ToString("o")

  $skillsCount = 0
  if ($null -ne $card -and $null -ne $card.skills) {
    try { $skillsCount = @($card.skills).Count } catch { $skillsCount = 0 }
  }

  $taskIdsOut = @()
  $trajectoryFiles = @()
  $taskCount = 0
  $selectionMode = "split:$TaskSplit"
  if ($null -ne $catalogSummary) {
    try { $taskIdsOut = @($catalogSummary.task_ids) } catch { $taskIdsOut = @() }
    try { $trajectoryFiles = @($catalogSummary.trajectory_files) } catch { $trajectoryFiles = @() }
    try { $taskCount = [int]$catalogSummary.task_count } catch { $taskCount = $taskIdsOut.Count }
    if ($catalogSummary.selection_mode) { $selectionMode = [string]$catalogSummary.selection_mode }
  }

  $runManifest = [ordered]@{
    status = "completed"
    domain = $Domain
    task_split = $TaskSplit
    task_selection = $selectionMode
    task_count = $taskCount
    selected_task_ids = $taskIdsOut
    save_to = $SaveTo
    image_tag = $ImageTag
    base_url = $BaseUrl
    port = $Port
    started_at = $startedAt
    finished_at = $finishedAt
    tests = [ordered]@{
      tau2_adapter = "passed"
      quipu_lab_smoke = "passed"
    }
    docker = [ordered]@{
      build = "passed"
      container_run = "passed"
      container_id = $cid
    }
    evidence = [ordered]@{
      health_path = (Join-Path $EvidenceDir "health.json")
      agent_card_path = (Join-Path $EvidenceDir "agent_card.json")
      task_catalog_summary_path = $TraceSummaryPath
      trajectories_path = $TrajDir
      trajectory_files = $trajectoryFiles
      logs_path = $LogsDir
    }
  }

  $healthOk = ($null -ne $health -and $health.status -eq "ok")
  $agentCardOk = (
    $null -ne $card -and
    -not [string]::IsNullOrWhiteSpace([string]$card.name) -and
    -not [string]::IsNullOrWhiteSpace([string]$card.url)
  )
  $localE2EOk = (
    $healthOk -and
    $agentCardOk -and
    $taskCount -ge 1 -and
    $trajectoryFiles.Count -eq $taskCount
  )

  $metrics = [ordered]@{
    domain = $Domain
    task_split = $TaskSplit
    task_selection = $selectionMode
    task_count = $taskCount
    selected_task_ids = $taskIdsOut
    adapter_tests_passed = $true
    smoke_tests_passed = $true
    docker_build_passed = $true
    health_ok = $healthOk
    agent_card_ok = $agentCardOk
    skills_count = $skillsCount
    trajectories_count = $trajectoryFiles.Count
    local_e2e_ok = $localE2EOk
  }

  Save-Json -Object $runManifest -Path $RunManifestPath
  Save-Json -Object $metrics -Path $MetricsPath

  Write-Host "`nTAU2 PURPLE EVAL OK" -ForegroundColor Green
  Write-Host ("Artifacts written to: {0}" -f $ArtifactsRoot) -ForegroundColor Green
}
finally {
  if ($null -ne $cid -and ($cid -match '^[0-9a-f]{12,}$')) {
    Write-Section "Stopping container"
    & (Resolve-CommandPath -Name "docker") stop $cid | Out-Null
  }
}
