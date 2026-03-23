[CmdletBinding()]
param(
    [string]$EnvName = "demo_env",
    [string]$RunArtifactsRoot = "artifacts\openenv\run",
    [string]$SubmissionRoot = "artifacts\openenv\submission",
    [switch]$IncludeLatestRunArtifacts,
    [switch]$ZipBundle
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$RunArtifactsPath = Join-Path $RepoRoot $RunArtifactsRoot
$SubmissionPath = Join-Path $RepoRoot $SubmissionRoot

New-Item -ItemType Directory -Force -Path $SubmissionPath | Out-Null

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$BundleDir = Join-Path $SubmissionPath $Stamp
New-Item -ItemType Directory -Force -Path $BundleDir | Out-Null

$EnvsRoot = Join-Path $RepoRoot "integrations\openenv\envs"
$EnvRoot = Join-Path $RepoRoot "integrations\openenv\envs\$EnvName"
$ServerRoot = Join-Path $EnvRoot "server"

$AdapterRoot = Join-Path $RepoRoot "src\aegisforge\adapters\openenv"
$TrackFile = Join-Path $RepoRoot "src\aegisforge_eval\tracks\openenv.py"

$HarnessRoot = Join-Path $RepoRoot "harness\AegisForge_scenarios\data\openenv\$EnvName"
$FixturesRoot = Join-Path $RepoRoot "tests\fixtures\openenv\$EnvName"
$SmokeTestFile = Join-Path $RepoRoot "tests\tests_envs\test_openenv_demo_env_smoke.py"
$AdapterTestFile = Join-Path $RepoRoot "tests\test_adapters\test_openenv_adapter.py"

function Ensure-Exists {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [string]$Label = ""
    )

    if (-not (Test-Path $Path)) {
        if ([string]::IsNullOrWhiteSpace($Label)) {
            throw "No se encontró la ruta requerida: $Path"
        }
        throw "No se encontró $Label en: $Path"
    }
}

function Save-JsonUtf8 {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [object]$Data
    )

    $json = $Data | ConvertTo-Json -Depth 30
    [System.IO.File]::WriteAllText($Path, $json, [System.Text.Encoding]::UTF8)
}

function Get-LatestChildDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path $Path)) {
        return $null
    }

    return Get-ChildItem -Path $Path -Directory |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
}

function To-RelativeRepoPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$AbsolutePath
    )

    $normalizedRepo = [System.IO.Path]::GetFullPath($RepoRoot)
    $normalizedPath = [System.IO.Path]::GetFullPath($AbsolutePath)

    if ($normalizedPath.StartsWith($normalizedRepo, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $normalizedPath.Substring($normalizedRepo.Length).TrimStart('\')
    }

    return $normalizedPath
}

function Copy-FileExact {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,
        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    Ensure-Exists -Path $Source -Label "archivo requerido"

    $destParent = Split-Path -Parent $Destination
    if (-not (Test-Path $destParent)) {
        New-Item -ItemType Directory -Force -Path $destParent | Out-Null
    }

    Copy-Item -Path $Source -Destination $Destination -Force
}

function Copy-DirectoryContents {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourceDir,
        [Parameter(Mandatory = $true)]
        [string]$DestinationDir
    )

    Ensure-Exists -Path $SourceDir -Label "directorio requerido"

    New-Item -ItemType Directory -Force -Path $DestinationDir | Out-Null

    $children = Get-ChildItem -Path $SourceDir -Force
    foreach ($child in $children) {
        Copy-Item -Path $child.FullName -Destination $DestinationDir -Recurse -Force
    }
}

Ensure-Exists -Path $EnvRoot -Label "OpenEnv env root"
Ensure-Exists -Path $ServerRoot -Label "OpenEnv env server root"
Ensure-Exists -Path $AdapterRoot -Label "OpenEnv adapter root"
Ensure-Exists -Path $TrackFile -Label "OpenEnv track file"

$BundleEnvRoot = Join-Path $BundleDir "openenv_env"
$BundleEnvServerRoot = Join-Path $BundleEnvRoot "server"
$BundleAdapterRoot = Join-Path $BundleDir "aegisforge_adapter"
$BundleEvidenceRoot = Join-Path $BundleDir "evidence"
$BundleHarnessRoot = Join-Path $BundleEvidenceRoot "harness_data"
$BundleFixturesRoot = Join-Path $BundleEvidenceRoot "fixtures"
$BundleLatestRunRoot = Join-Path $BundleEvidenceRoot "latest_run"
$BundleTestsRoot = Join-Path $BundleDir "tests"
$BundleDocsRoot = Join-Path $BundleDir "docs"

New-Item -ItemType Directory -Force -Path $BundleEnvRoot | Out-Null
New-Item -ItemType Directory -Force -Path $BundleEnvServerRoot | Out-Null
New-Item -ItemType Directory -Force -Path $BundleAdapterRoot | Out-Null
New-Item -ItemType Directory -Force -Path $BundleEvidenceRoot | Out-Null
New-Item -ItemType Directory -Force -Path $BundleHarnessRoot | Out-Null
New-Item -ItemType Directory -Force -Path $BundleFixturesRoot | Out-Null
New-Item -ItemType Directory -Force -Path $BundleTestsRoot | Out-Null
New-Item -ItemType Directory -Force -Path $BundleDocsRoot | Out-Null

$included = [ordered]@{
    env = @()
    adapter = @()
    harness = @()
    fixtures = @()
    tests = @()
    run_artifacts = @()
    docs = @()
}

# ===== OpenEnv env =====

$EnvFileMap = [ordered]@{
    (Join-Path $EnvsRoot "README.md") = (Join-Path $BundleEnvRoot "README.md")
    (Join-Path $EnvRoot "requirements.txt") = (Join-Path $BundleEnvRoot "requirements.txt")
    (Join-Path $EnvRoot "openenv.yaml") = (Join-Path $BundleEnvRoot "openenv.yaml")
    (Join-Path $EnvRoot "models.py") = (Join-Path $BundleEnvRoot "models.py")
    (Join-Path $EnvRoot "client.py") = (Join-Path $BundleEnvRoot "client.py")
    (Join-Path $ServerRoot "Dockerfile") = (Join-Path $BundleEnvServerRoot "Dockerfile")
    (Join-Path $ServerRoot "app.py") = (Join-Path $BundleEnvServerRoot "app.py")
}

foreach ($entry in $EnvFileMap.GetEnumerator()) {
    Copy-FileExact -Source $entry.Key -Destination $entry.Value
    $included.env += To-RelativeRepoPath -AbsolutePath $entry.Key
}

# ===== Adapter =====

$AdapterFileMap = [ordered]@{
    (Join-Path $AdapterRoot "__init__.py") = (Join-Path $BundleAdapterRoot "__init__.py")
    (Join-Path $AdapterRoot "config.py") = (Join-Path $BundleAdapterRoot "config.py")
    (Join-Path $AdapterRoot "adapter.py") = (Join-Path $BundleAdapterRoot "adapter.py")
    $TrackFile = (Join-Path $BundleAdapterRoot "openenv_track.py")
}

foreach ($entry in $AdapterFileMap.GetEnumerator()) {
    Copy-FileExact -Source $entry.Key -Destination $entry.Value
    $included.adapter += To-RelativeRepoPath -AbsolutePath $entry.Key
}

# ===== Harness + fixtures =====

Copy-DirectoryContents -SourceDir $HarnessRoot -DestinationDir $BundleHarnessRoot
$included.harness += To-RelativeRepoPath -AbsolutePath $HarnessRoot

Copy-DirectoryContents -SourceDir $FixturesRoot -DestinationDir $BundleFixturesRoot
$included.fixtures += To-RelativeRepoPath -AbsolutePath $FixturesRoot

# ===== Tests =====

$TestFiles = @($SmokeTestFile, $AdapterTestFile)
foreach ($file in $TestFiles) {
    Copy-FileExact -Source $file -Destination (Join-Path $BundleTestsRoot (Split-Path -Leaf $file))
    $included.tests += To-RelativeRepoPath -AbsolutePath $file
}

# ===== Latest run artifacts =====

$latestRunRelative = $null

if ($IncludeLatestRunArtifacts.IsPresent) {
    $latestRunDir = Get-LatestChildDirectory -Path $RunArtifactsPath
    if ($null -ne $latestRunDir) {
        Copy-DirectoryContents -SourceDir $latestRunDir.FullName -DestinationDir $BundleLatestRunRoot
        $latestRunRelative = To-RelativeRepoPath -AbsolutePath $latestRunDir.FullName
        $included.run_artifacts += $latestRunRelative
    }
}

# ===== Docs =====

$summaryText = @"
AegisForge OpenEnv Purple submission bundle.

Este bundle empaqueta la capacidad OpenEnv-native dentro de AegisForge_agent,
incluyendo:
- entorno demo contenedorizable por HTTP,
- adapter OpenEnv,
- track de evaluación,
- harness data,
- fixtures,
- smoke tests,
- y opcionalmente los artefactos más recientes de ejecución local.

El objetivo actual del bundle es demostrar una capability layer reproducible
y validable de forma local, alineada con el reto OpenEnv.
"@

$summaryPath = Join-Path $BundleDocsRoot "SUMMARY.txt"
[System.IO.File]::WriteAllText($summaryPath, $summaryText, [System.Text.Encoding]::UTF8)
$included.docs += To-RelativeRepoPath -AbsolutePath $summaryPath

# ===== Manifest =====

$manifest = [ordered]@{
    bundle_type = "openenv_purple_submission"
    created_at = (Get-Date).ToString("o")
    repo_root = $RepoRoot
    env_name = $EnvName
    latest_run_artifacts_included = $IncludeLatestRunArtifacts.IsPresent
    latest_run_artifacts_path = $latestRunRelative
    bundle_dir = To-RelativeRepoPath -AbsolutePath $BundleDir
    sections = $included
    validation_targets = [ordered]@{
        env_http_api = @(
            "GET /health",
            "POST /reset",
            "POST /step",
            "GET /state"
        )
        local_evidence = @(
            "README.md",
            "requirements.txt",
            "openenv.yaml",
            "models.py",
            "client.py",
            "server/Dockerfile",
            "server/app.py",
            "adapter.py",
            "config.py",
            "openenv_track.py",
            "harness data",
            "fixtures",
            "smoke tests"
        )
    }
    status = "PREPARED"
}

$manifestPath = Join-Path $BundleDir "manifest.json"
Save-JsonUtf8 -Path $manifestPath -Data $manifest

# ===== Optional zip =====

$zipPath = $null
if ($ZipBundle.IsPresent) {
    $zipPath = "$BundleDir.zip"
    if (Test-Path $zipPath) {
        Remove-Item -Force $zipPath
    }

    Compress-Archive -Path (Join-Path $BundleDir "*") -DestinationPath $zipPath -Force
}

Write-Host ""
Write-Host "OPENENV PREPARE OK"
Write-Host "Bundle dir : $BundleDir"
Write-Host "Manifest   : $manifestPath"

if ($IncludeLatestRunArtifacts.IsPresent) {
    if ($latestRunRelative) {
        Write-Host "Run art.   : $latestRunRelative"
    }
    else {
        Write-Host "Run art.   : no se encontraron corridas previas en $RunArtifactsPath"
    }
}

if ($ZipBundle.IsPresent) {
    Write-Host "Zip        : $zipPath"
}
