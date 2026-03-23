[CmdletBinding()]
param(
    [int]$Port = 8012,
    [string]$BindHost = "127.0.0.1",
    [switch]$Reload
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$EnvRoot = Join-Path $RepoRoot "integrations\openenv\envs\aegisarena_env"
$AppFile = Join-Path $EnvRoot "server\app.py"
$RequirementsFile = Join-Path $EnvRoot "requirements.txt"

if (-not (Test-Path $EnvRoot)) {
    throw "No se encontró la ruta del entorno AegisArena: $EnvRoot"
}

if (-not (Test-Path $AppFile)) {
    throw "No se encontró server\app.py en: $AppFile"
}

if (-not (Test-Path $RequirementsFile)) {
    throw "No se encontró requirements.txt en: $RequirementsFile"
}

function Resolve-PythonExe {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRootPath
    )

    $candidates = @(
        (Join-Path $RepoRootPath ".venv\Scripts\python.exe"),
        (Join-Path $RepoRootPath "venv\Scripts\python.exe"),
        "python",
        "py"
    )

    foreach ($candidate in $candidates) {
        if ($candidate -like "*.exe") {
            if (Test-Path $candidate) {
                return $candidate
            }
        }
        else {
            $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
            if ($cmd) {
                return $candidate
            }
        }
    }

    throw "No encontré Python. Usa .venv\Scripts\python.exe o asegúrate de que 'python' esté en PATH."
}

$PythonExe = Resolve-PythonExe -RepoRootPath $RepoRoot

$ExistingPythonPath = [string]$env:PYTHONPATH
if ([string]::IsNullOrWhiteSpace($ExistingPythonPath)) {
    $env:PYTHONPATH = $EnvRoot
}
else {
    $env:PYTHONPATH = "$EnvRoot$([IO.Path]::PathSeparator)$ExistingPythonPath"
}

$env:PORT = [string]$Port

Write-Host ""
Write-Host "== AegisArena OpenEnv Local Env =="
Write-Host "Repo root : $RepoRoot"
Write-Host "Env root  : $EnvRoot"
Write-Host "Bind host : $BindHost"
Write-Host "Port      : $Port"
Write-Host "Python    : $PythonExe"
Write-Host ""

$UvicornArgs = @(
    "-m", "uvicorn",
    "server.app:app",
    "--app-dir", $EnvRoot,
    "--host", $BindHost,
    "--port", "$Port"
)

if ($Reload.IsPresent) {
    $UvicornArgs += "--reload"
}

Write-Host "Comando:"
Write-Host "$PythonExe $($UvicornArgs -join ' ')"
Write-Host ""
Write-Host "Health esperado: http://$BindHost`:$Port/health"
Write-Host ""

& $PythonExe @UvicornArgs
