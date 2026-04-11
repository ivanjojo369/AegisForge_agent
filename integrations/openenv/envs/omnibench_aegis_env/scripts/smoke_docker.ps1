param(
    [string]$ImageName = $(if ($env:OPENENV_DOCKER_IMAGE) { $env:OPENENV_DOCKER_IMAGE } else { 'omnibench-aegis-env:local' }),
    [string]$ContainerName = $(if ($env:OPENENV_DOCKER_CONTAINER) { $env:OPENENV_DOCKER_CONTAINER } else { 'omnibench-aegis-env-smoke' }),
    [int]$HostPort = $(if ($env:OPENENV_HOST_PORT) { [int]$env:OPENENV_HOST_PORT } else { 8000 }),
    [int]$ContainerPort = $(if ($env:OPENENV_CONTAINER_PORT) { [int]$env:OPENENV_CONTAINER_PORT } else { 8000 }),
    [string]$BuildContext,
    [string]$Dockerfile,
    [double]$Timeout = $(if ($env:OPENENV_TIMEOUT) { [double]$env:OPENENV_TIMEOUT } else { 10 }),
    [int]$WaitSeconds = 30,
    [switch]$NoBuild,
    [switch]$KeepContainer,
    [switch]$VerboseSmoke,
    [switch]$Json,
    [switch]$AllDomains,
    [string[]]$Only,
    [switch]$IncludeNonSmoke,
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'

function Resolve-ScriptRoot {
    if ($PSScriptRoot) {
        return $PSScriptRoot
    }
    return Split-Path -Parent $MyInvocation.MyCommand.Path
}

function Assert-CommandAvailable {
    param([Parameter(Mandatory = $true)][string]$CommandName)

    if (-not (Get-Command $CommandName -ErrorAction SilentlyContinue)) {
        throw "Required command not found in PATH: $CommandName"
    }
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    Write-Host "[info] $FilePath $($Arguments -join ' ')"
    & $FilePath @Arguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "Command failed with exit code ${exitCode}: $FilePath $($Arguments -join ' ')"
    }
}

function Test-ContainerExists {
    param([Parameter(Mandatory = $true)][string]$Name)

    $result = & docker ps -a --filter "name=^/$Name$" --format '{{.Names}}'
    return $LASTEXITCODE -eq 0 -and ($result -contains $Name)
}

function Remove-ContainerIfPresent {
    param([Parameter(Mandatory = $true)][string]$Name)

    if (Test-ContainerExists -Name $Name) {
        Write-Host "[info] Removing existing container: $Name"
        & docker rm -f $Name | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to remove existing container: $Name"
        }
    }
}

function Wait-ForOpenEnvHealth {
    param(
        [Parameter(Mandatory = $true)][string]$BaseUrl,
        [Parameter(Mandatory = $true)][int]$MaxSeconds
    )

    $deadline = (Get-Date).AddSeconds($MaxSeconds)
    $healthUrl = ($BaseUrl.TrimEnd('/') + '/health')

    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-RestMethod -Method Get -Uri $healthUrl -TimeoutSec 5
            if ($null -ne $response -and $response.status -eq 'ok') {
                return $true
            }
        }
        catch {
            Start-Sleep -Milliseconds 750
        }
    }

    return $false
}

$scriptRoot = Resolve-ScriptRoot
if (-not $BuildContext) {
    $BuildContext = $scriptRoot
}
if (-not $Dockerfile) {
    $Dockerfile = Join-Path $scriptRoot 'Dockerfile'
}

Assert-CommandAvailable -CommandName 'docker'
Assert-CommandAvailable -CommandName $Python

if (-not (Test-Path -LiteralPath $Dockerfile)) {
    throw "Dockerfile not found: $Dockerfile"
}

$baseUrl = "http://127.0.0.1:$HostPort"
$smokeScript = Join-Path $scriptRoot 'smoke.ps1'
if (-not (Test-Path -LiteralPath $smokeScript)) {
    throw "smoke.ps1 not found: $smokeScript"
}

$containerStarted = $false

try {
    Remove-ContainerIfPresent -Name $ContainerName

    if (-not $NoBuild) {
        Invoke-Checked -FilePath 'docker' -Arguments @(
            'build',
            '--file', $Dockerfile,
            '--tag', $ImageName,
            $BuildContext
        )
    }

    Invoke-Checked -FilePath 'docker' -Arguments @(
        'run',
        '--detach',
        '--name', $ContainerName,
        '--publish', "$HostPort`:$ContainerPort",
        $ImageName
    )
    $containerStarted = $true

    if (-not (Wait-ForOpenEnvHealth -BaseUrl $baseUrl -MaxSeconds $WaitSeconds)) {
        Write-Host '[warn] Container did not become healthy in time. Recent logs:'
        & docker logs --tail 100 $ContainerName
        throw "Timed out waiting for $baseUrl/health"
    }

    $smokeArgs = @(
        '-ExecutionPolicy', 'Bypass',
        '-File', $smokeScript,
        '-BaseUrl', $baseUrl,
        '-Timeout', "$Timeout",
        '-Python', $Python
    )

    if ($VerboseSmoke) {
        $smokeArgs += '-VerboseSmoke'
    }
    if ($Json) {
        $smokeArgs += '-Json'
    }
    if ($AllDomains) {
        $smokeArgs += '-AllDomains'
    }
    if ($IncludeNonSmoke) {
        $smokeArgs += '-IncludeNonSmoke'
    }
    if ($Only) {
        $smokeArgs += '-Only'
        $smokeArgs += $Only
    }

    Invoke-Checked -FilePath 'powershell' -Arguments $smokeArgs
    Write-Host "[ok] smoke_docker.ps1 completed successfully against $baseUrl"
}
finally {
    if ($containerStarted -and -not $KeepContainer) {
        try {
            Write-Host "[info] Stopping container: $ContainerName"
            & docker rm -f $ContainerName | Out-Null
        }
        catch {
            Write-Warning "Could not remove container ${ContainerName}: $($_.Exception.Message)"
        }
    }
    elseif ($containerStarted -and $KeepContainer) {
        Write-Host "[info] Keeping container running: $ContainerName"
    }
}
