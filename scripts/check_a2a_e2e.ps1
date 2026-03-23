param(
  [string]$ImageTag = "aegisforge-agent:local",
  [int]$Port = 8001,
  [string]$BaseUrl = ""
)

if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
  $BaseUrl = "http://127.0.0.1:$Port"
}

$BaseUrl = $BaseUrl.TrimEnd('/')

Write-Host "Building docker image: $ImageTag" -ForegroundColor Cyan
docker build -t $ImageTag .
if ($LASTEXITCODE -ne 0) {
  throw "docker build failed."
}

Write-Host "Running container on $BaseUrl ..." -ForegroundColor Cyan

# Fuerza que el puerto interno y externo sean el mismo
# y pasa --port explícitamente al entrypoint/run.sh
$cid = docker run -d --rm -p "$Port`:$Port" $ImageTag --port $Port

if ($LASTEXITCODE -ne 0) {
  $nextPort = $Port + 1
  throw "docker run failed. Port $Port is probably already in use. Try -Port $nextPort (or stop the container using it)."
}

if (-not ($cid -match '^[0-9a-f]{12,}$')) {
  throw "docker run did not return a container id: $cid"
}

function Invoke-WithRetry {
  param(
    [string]$Uri,
    [int]$MaxAttempts = 10,
    [int]$DelaySeconds = 1
  )

  for ($i = 1; $i -le $MaxAttempts; $i++) {
    try {
      return Invoke-RestMethod -Uri $Uri -Method GET -TimeoutSec 10
    } catch {
      if ($i -eq $MaxAttempts) {
        throw
      }
      Start-Sleep -Seconds $DelaySeconds
    }
  }
}

try {
  Write-Host "Checking health..." -ForegroundColor Cyan
  $health = Invoke-WithRetry -Uri "$BaseUrl/health"
  $health | ConvertTo-Json -Depth 10

  Write-Host "Checking agent card..." -ForegroundColor Cyan
  $card = Invoke-WithRetry -Uri "$BaseUrl/.well-known/agent-card.json"
  $card | ConvertTo-Json -Depth 10

  Write-Host "E2E OK: container stays up + health reachable + agent card reachable." -ForegroundColor Green
}
finally {
  Write-Host "Stopping container..." -ForegroundColor Cyan
  if ($cid -and ($cid -match '^[0-9a-f]{12,}$')) {
    docker stop $cid | Out-Null
  } else {
    Write-Host "No valid container id to stop." -ForegroundColor Yellow
  }
}
