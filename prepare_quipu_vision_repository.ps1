# Quipu Vision — safe publication staging (Windows PowerShell 5.1 / PowerShell 7)
# Copies an allowlisted snapshot of the EXISTING project into a separate directory.
# Does not modify AegisForge_agent, create a GitHub repository, commit, or push.
#
# Usage from AegisForge_agent root:
#   powershell -ExecutionPolicy Bypass -File .\prepare_quipu_vision_repository.ps1
# Or:
#   .\prepare_quipu_vision_repository.ps1 -ProjectRoot 'C:\path\to\AegisForge_agent'

[CmdletBinding()]
param(
    [string]$ProjectRoot = (Get-Location).Path,
    [string]$PublishDirectory = (Join-Path $env:TEMP 'Quipu-Vision-Publication')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = (Resolve-Path -LiteralPath $ProjectRoot).Path
$source = Join-Path $root 'Quipu-Vision'
if (-not (Test-Path -LiteralPath (Join-Path $source 'src\quipu_vision\core.py') -PathType Leaf)) {
    throw "No encuentro Quipu Vision. Ejecuta desde la carpeta AegisForge_agent o especifica -ProjectRoot. Ruta: $source"
}
if (-not (Test-Path -LiteralPath (Join-Path $source 'pyproject.toml') -PathType Leaf)) {
    throw "Falta Quipu-Vision\pyproject.toml; no se preparara una publicacion incompleta."
}

$target = [System.IO.Path]::GetFullPath($PublishDirectory)
if ($target.Equals($root, [System.StringComparison]::OrdinalIgnoreCase) -or
    $target.StartsWith($root.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'La carpeta de publicacion debe estar FUERA de AegisForge_agent para no crear un repositorio Git anidado.'
}
if (Test-Path -LiteralPath $target) {
    throw "La carpeta de publicacion ya existe: $target. Revísala antes de elegir una ruta diferente. No se sobrescribio nada."
}

function Copy-ReleaseItem {
    param(
        [string]$RelativeSource,
        [string]$RelativeTarget,
        [bool]$Required = $true
    )
    $from = Join-Path $root $RelativeSource
    if (-not (Test-Path -LiteralPath $from)) {
        if ($Required) { throw "Falta contenido necesario: $RelativeSource" }
        Write-Warning "No encontrado (opcional): $RelativeSource"
        return
    }
    $to = Join-Path $target $RelativeTarget
    $parent = Split-Path -Parent $to
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    Copy-Item -LiteralPath $from -Destination $to -Recurse -Force
    Write-Host "  + $RelativeTarget"
}

# Check the most important inputs before writing anything.
$required = @(
    'Quipu-Vision\src\quipu_vision\core.py',
    'Quipu-Vision\src\quipu_vision\schemas.py',
    'Quipu-Vision\src\quipu_vision\workflow.py',
    'Quipu-Vision\src\quipu_vision\verifier.py',
    'Quipu-Vision\pyproject.toml',
    'Quipu-Vision\tests\test_workflow.py',
    'Quipu-Vision\examples\evaluation\manifest.json',
    'src\aegisforge\web\vision\index.html',
    'src\aegisforge\web\vision\app.js',
    'src\aegisforge\web\vision\styles.css'
)
$missing = @($required | Where-Object { -not (Test-Path -LiteralPath (Join-Path $root $_) -PathType Leaf) })
if ($missing.Count -gt 0) {
    throw "Faltan archivos. No se preparo la publicacion:`n$($missing -join "`n")"
}

New-Item -ItemType Directory -Force -Path $target | Out-Null
Write-Host "Preparando version publica en: $target"

Copy-ReleaseItem 'Quipu-Vision\pyproject.toml' 'pyproject.toml'
Copy-ReleaseItem 'Quipu-Vision\src\quipu_vision' 'src\quipu_vision'
Copy-ReleaseItem 'Quipu-Vision\tests\test_workflow.py' 'tests\test_workflow.py'
Copy-ReleaseItem 'Quipu-Vision\examples\demo' 'examples\demo'
Copy-ReleaseItem 'Quipu-Vision\examples\evaluation' 'examples\evaluation'
Copy-ReleaseItem 'docs\competitions\opencv' 'docs\competitions\opencv'
Copy-ReleaseItem 'src\aegisforge\web\vision' 'integrations\aegisforge\web\vision'
Copy-ReleaseItem 'src\aegisforge\plugins\examples\quipu_vision.py' 'integrations\aegisforge\quipu_vision.py'
Copy-ReleaseItem 'infra\aws\vision-lambda.yaml' 'integrations\aws\vision-lambda.yaml' $false
Copy-ReleaseItem 'Quipu-Vision\README.md' 'docs\ORIGINAL_LOCAL_README.md' $false

@'
# Quipu Vision

**Evidence-driven visual change inspection and spatial-persistence verification using OpenCV 5.**

Quipu Vision compares a reference image with an observed image, detects pixel-change regions,
and optionally processes a user-provided follow-up image. It emits structured observations,
provenance, a verification status, and recommended next actions.

> Status: local OpenCV 5 and AegisForge A2A/web integration were tested. AWS deployment
> remains unverified. This is not an object classifier or a physical-safety certification.

## Python package (standalone engine)

Requires Python 3.11+ and the dependencies specified in `pyproject.toml`.

```bash
python -m pip install -e .
python -m unittest discover -s tests -p test_workflow.py -v
python examples/evaluation/evaluate.py --json-out outputs/evaluation-results.json
```

The tests cover workflow behavior; the synthetic evaluation uses 24 labeled scenarios.
Previous results on OpenCV 5.0.0: initial-change accuracy 75.00% (TP=12, TN=6,
FP=4, FN=2); persistence accuracy 85.71% (TP=4, TN=2, FP=0, FN=1);
33/33 functional tests passed. These are **synthetic**, not real-world performance estimates.

See `docs/competitions/opencv/evaluation.md` and `docs/competitions/opencv/architecture.md`.

## AegisForge A2A + browser UI

The files under `integrations/aegisforge/` are the plugin adapter and front-end **integration
assets**, not a standalone hosted UI or a complete copy of AegisForge. The tested web
experience uses the AegisForge runtime (`a2a_server.py` and `executor.py`) from
[the host repository](https://github.com/ivanjojo369/AegisForge_agent).

After the host repository has been updated to include the tested integration, its
`/demo/vision` interface and A2A `quipu_vision` capability can be reproduced from that host.
Do not claim the hosted interface works by cloning this package alone.

## Deployment

`integrations/aws/vision-lambda.yaml` (if present) is a template prepared for the
AegisForge container image. It does not constitute AWS deployment evidence and cannot
be used directly without building/publishing a compatible image and authorized AWS access.

## Notes

- Follow-up images are supplied by the operator/client; camera capture is not automatic.
- `verified: true` means **pixel-region persistence**, not object recognition or safety.
- No `.env`, credentials, virtual environments, runtime artifacts, or private media
  should be published. Review the staged files before pushing.
- No software license is asserted here. Add a LICENSE only after choosing and
  confirming the desired terms.
'@ | Set-Content -LiteralPath (Join-Path $target 'README.md') -Encoding UTF8

@'
# Local environments / credentials
.venv/
venv/
.env
.env.*
!.env.example
*.pem
*.key
*.p12
*.pfx
.aws/

# Caches and generated files
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
outputs/
var/
logs/
.DS_Store
Thumbs.db
*.log

# Generated exports / datasets not intentionally listed
*.sqlite
*.sqlite3
'@ | Set-Content -LiteralPath (Join-Path $target '.gitignore') -Encoding UTF8

@'
# Publishing checklist

1. Confirm you are using the intended GitHub account (repository owner).
2. Create an EMPTY public repository `Quipu-Vision` in that account. Do not initialize
   it with README, .gitignore, or LICENSE (all of these are handled locally, except LICENSE).
3. Review every file staged here. In particular, make sure documentation, image files,
   and Python sources contain no passwords, private data, payment details, or keys.
4. Keep the source project inside `AegisForge_agent/Quipu-Vision/` unchanged.
   This directory is only a release snapshot, not a second development source.
5. From PowerShell in this publication directory:

```powershell
git init -b main
git add .
git status --short
git diff --cached --stat
# Inspect content before proceeding:
git diff --cached --check
# Only after reviewing:
git commit -m "Publish Quipu Vision 0.4.0 local MVP"
git remote add origin https://github.com/ivanjojo369/Quipu-Vision.git
git push -u origin main
```

If the repository owner is NOT `ivanjojo369`, replace the remote URL before pushing.
The `git push` requires you to authenticate to the correct GitHub account.

This standalone package does not include the full AegisForge server; to publish the
verified A2A web integration, push the corresponding tested changes to the original
AegisForge repository and link the exact release/commit from this README.
'@ | Set-Content -LiteralPath (Join-Path $target 'PUBLISHING.md') -Encoding UTF8

Write-Host ''
Write-Host 'LISTO: Preparacion creada. No se modifico AegisForge_agent, no se hizo git commit/push.' -ForegroundColor Green
Write-Host "Publicacion: $target"
Write-Host 'Lee PUBLISHING.md, revisa los archivos y crea el repo GitHub vacio antes de publicar.'
