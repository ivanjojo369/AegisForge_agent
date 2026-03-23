param(
  [string]$Domain = "quipu_lab",
  [string]$TaskSplit = "base",
  [string]$SaveTo = "aegisforge_tau2_quipu_lab",
  [int]$Port = 8002,
  [string]$ImageTag = "aegisforge-agent:tau2-purple",
  [string]$BaseUrl = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
  $BaseUrl = "http://127.0.0.1:$Port"
}
$BaseUrl = $BaseUrl.TrimEnd('/')

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ArtifactsRoot = Join-Path $RepoRoot "artifacts\tau2\simulations\$SaveTo"
$LogsDir = Join-Path $ArtifactsRoot "logs"
$EvidenceDir = Join-Path $ArtifactsRoot "evidence"
$TrajDir = Join-Path $ArtifactsRoot "trajectories"
$RunManifestPath = Join-Path $ArtifactsRoot "run_manifest.json"
$MetricsPath = Join-Path $ArtifactsRoot "metrics.json"
$TracePath = Join-Path $TrajDir "local_validation_trace.jsonl"

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

  $json = $Object | ConvertTo-Json -Depth 20
  Set-Content -Path $Path -Value $json -Encoding UTF8
}

function Resolve-CommandPath {
  param([Parameter(Mandatory = $true)][string]$Name)

  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  if ($null -ne $cmd) {
    return $cmd.Source
  }

  throw "Required command not found in PATH: $Name"
}

function Invoke-LoggedCommand {
  param(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [Parameter(Mandatory = $true)][string[]]$ArgumentList,
    [Parameter(Mandatory = $true)][string]$LogPrefix,
    [Parameter(Mandatory = $true)][string]$WorkingDirectory
  )

  $stdoutPath = Join-Path $LogsDir ("{0}.stdout.log" -f $LogPrefix)
  $stderrPath = Join-Path $LogsDir ("{0}.stderr.log" -f $LogPrefix)

  if (Test-Path $stdoutPath) { Remove-Item $stdoutPath -Force }
  if (Test-Path $stderrPath) { Remove-Item $stderrPath -Force }

  $resolvedFilePath = Resolve-CommandPath -Name $FilePath
  Write-Host ("Running: {0} {1}" -f $resolvedFilePath, ($ArgumentList -join ' ')) -ForegroundColor Yellow

  $proc = Start-Process -FilePath $resolvedFilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory -NoNewWindow -Wait -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath

  if (Test-Path $stdoutPath) {
    Get-Content $stdoutPath | ForEach-Object { Write-Host $_ }
  }
  if (Test-Path $stderrPath) {
    Get-Content $stderrPath | ForEach-Object { Write-Host $_ }
  }

  if ($proc.ExitCode -ne 0) {
    throw ("Command failed with exit code {0}: {1} {2}" -f $proc.ExitCode, $resolvedFilePath, ($ArgumentList -join ' '))
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

$startedAt = (Get-Date).ToString("o")
$cid = $null
$health = $null
$card = $null

try {
  Write-Section "Running tau2 adapter tests"
  Invoke-LoggedCommand -FilePath "python" -ArgumentList @("-m", "pytest", "tests\test_adapters\test_tau2_adapter.py", "-q") -LogPrefix "pytest_tau2_adapter" -WorkingDirectory $RepoRoot

  Write-Section "Running quipu_lab smoke tests"
  Invoke-LoggedCommand -FilePath "python" -ArgumentList @("-m", "pytest", "tests\tests_envs\test_tau2_quipu_lab_smoke.py", "-q") -LogPrefix "pytest_quipu_lab_smoke" -WorkingDirectory $RepoRoot

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

  $finishedAt = (Get-Date).ToString("o")

  $runManifest = [ordered]@{
    status = "completed"
    domain = $Domain
    task_split = $TaskSplit
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
      trajectories_path = $TrajDir
      logs_path = $LogsDir
    }
  }

  $skillsCount = 0
  if ($null -ne $card -and $null -ne $card.skills) {
    try { $skillsCount = @($card.skills).Count } catch { $skillsCount = 0 }
  }

  $metrics = [ordered]@{
    domain = $Domain
    task_split = $TaskSplit
    adapter_tests_passed = $true
    smoke_tests_passed = $true
    docker_build_passed = $true
    health_ok = ($null -ne $health -and $health.status -eq "ok")
    agent_card_ok = ($null -ne $card)
    skills_count = $skillsCount
    local_e2e_ok = $true
  }

  $traceEvent = [ordered]@{
    timestamp = $finishedAt
    event = "local_tau2_purple_validation"
    domain = $Domain
    task_split = $TaskSplit
    base_url = $BaseUrl
    health_ok = $metrics.health_ok
    agent_card_ok = $metrics.agent_card_ok
    skills_count = $skillsCount
    container_id = $cid
  } | ConvertTo-Json -Compress

  Set-Content -Path $TracePath -Value $traceEvent -Encoding UTF8
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
