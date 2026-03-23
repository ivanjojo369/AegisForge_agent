$ErrorActionPreference = "Stop"

# Ir a la raíz del repo (scripts/..)
Set-Location (Join-Path $PSScriptRoot "..")

$req = Join-Path $env:TEMP "requirements.txt"
$reqDev = Join-Path $env:TEMP "requirements-dev.txt"

uv pip compile pyproject.toml -o $req
Copy-Item -Force $req .\requirements.txt

uv pip compile pyproject.toml --extra test -o $reqDev
Copy-Item -Force $reqDev .\requirements-dev.txt

Write-Host "Done:"
Write-Host " - $((Resolve-Path .\requirements.txt).Path)"
Write-Host " - $((Resolve-Path .\requirements-dev.txt).Path)"
