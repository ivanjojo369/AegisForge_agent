[CmdletBinding()]
param(
    [string]$SubmissionRoot = "artifacts\openenv\submission",
    [string]$BundleDir = "",
    [switch]$RequireLatestRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$SubmissionPath = Join-Path $RepoRoot $SubmissionRoot

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

function Assert-Exists {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    if (-not (Test-Path $Path)) {
        throw "Falta $Label en: $Path"
    }
}

function Load-JsonFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    return Get-Content -Raw -Path $Path | ConvertFrom-Json -AsHashtable
}

function Assert-JsonHasKeys {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Object,
        [Parameter(Mandatory = $true)]
        [string[]]$Keys,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    foreach ($key in $Keys) {
        if (-not $Object.ContainsKey($key)) {
            throw "$Label no contiene la llave requerida: $key"
        }
    }
}

if ([string]::IsNullOrWhiteSpace($BundleDir)) {
    $Latest = Get-LatestChildDirectory -Path $SubmissionPath
    if ($null -eq $Latest) {
        throw "No se encontraron bundles en: $SubmissionPath"
    }
    $BundlePath = $Latest.FullName
}
else {
    if ([System.IO.Path]::IsPathRooted($BundleDir)) {
        $BundlePath = $BundleDir
    }
    else {
        $BundlePath = Join-Path $RepoRoot $BundleDir
    }
}

Assert-Exists -Path $BundlePath -Label "bundle dir"

$ManifestPath = Join-Path $BundlePath "manifest.json"
$DocsRoot = Join-Path $BundlePath "docs"
$SummaryPath = Join-Path $DocsRoot "SUMMARY.txt"

$EnvRoot = Join-Path $BundlePath "openenv_env"
$EnvServerRoot = Join-Path $EnvRoot "server"

$AdapterRoot = Join-Path $BundlePath "aegisforge_adapter"

$EvidenceRoot = Join-Path $BundlePath "evidence"
$HarnessRoot = Join-Path $EvidenceRoot "harness_data"
$FixturesRoot = Join-Path $EvidenceRoot "fixtures"
$LatestRunRoot = Join-Path $EvidenceRoot "latest_run"

$TestsRoot = Join-Path $BundlePath "tests"

# ===== Bundle base =====
Assert-Exists -Path $ManifestPath -Label "manifest.json"
Assert-Exists -Path $DocsRoot -Label "docs/"
Assert-Exists -Path $SummaryPath -Label "docs/SUMMARY.txt"

# ===== OpenEnv env =====
Assert-Exists -Path $EnvRoot -Label "openenv_env/"
Assert-Exists -Path $EnvServerRoot -Label "openenv_env/server/"
Assert-Exists -Path (Join-Path $EnvRoot "README.md") -Label "openenv_env/README.md"
Assert-Exists -Path (Join-Path $EnvRoot "requirements.txt") -Label "openenv_env/requirements.txt"
Assert-Exists -Path (Join-Path $EnvRoot "openenv.yaml") -Label "openenv_env/openenv.yaml"
Assert-Exists -Path (Join-Path $EnvRoot "models.py") -Label "openenv_env/models.py"
Assert-Exists -Path (Join-Path $EnvRoot "client.py") -Label "openenv_env/client.py"
Assert-Exists -Path (Join-Path $EnvServerRoot "Dockerfile") -Label "openenv_env/server/Dockerfile"
Assert-Exists -Path (Join-Path $EnvServerRoot "app.py") -Label "openenv_env/server/app.py"

# ===== Adapter =====
Assert-Exists -Path $AdapterRoot -Label "aegisforge_adapter/"
Assert-Exists -Path (Join-Path $AdapterRoot "__init__.py") -Label "aegisforge_adapter/__init__.py"
Assert-Exists -Path (Join-Path $AdapterRoot "config.py") -Label "aegisforge_adapter/config.py"
Assert-Exists -Path (Join-Path $AdapterRoot "adapter.py") -Label "aegisforge_adapter/adapter.py"
Assert-Exists -Path (Join-Path $AdapterRoot "openenv_track.py") -Label "aegisforge_adapter/openenv_track.py"

# ===== Evidence =====
Assert-Exists -Path $EvidenceRoot -Label "evidence/"
Assert-Exists -Path $HarnessRoot -Label "evidence/harness_data/"
Assert-Exists -Path $FixturesRoot -Label "evidence/fixtures/"

Assert-Exists -Path (Join-Path $HarnessRoot "env_seed.json") -Label "evidence/harness_data/env_seed.json"
Assert-Exists -Path (Join-Path $HarnessRoot "sample_actions.json") -Label "evidence/harness_data/sample_actions.json"
Assert-Exists -Path (Join-Path $HarnessRoot "expected_reset_min.json") -Label "evidence/harness_data/expected_reset_min.json"
Assert-Exists -Path (Join-Path $HarnessRoot "expected_step_min.json") -Label "evidence/harness_data/expected_step_min.json"
Assert-Exists -Path (Join-Path $HarnessRoot "expected_state_min.json") -Label "evidence/harness_data/expected_state_min.json"

Assert-Exists -Path (Join-Path $FixturesRoot "reset_response_min.json") -Label "evidence/fixtures/reset_response_min.json"
Assert-Exists -Path (Join-Path $FixturesRoot "step_response_min.json") -Label "evidence/fixtures/step_response_min.json"
Assert-Exists -Path (Join-Path $FixturesRoot "state_response_min.json") -Label "evidence/fixtures/state_response_min.json"
Assert-Exists -Path (Join-Path $FixturesRoot "sample_config.toml") -Label "evidence/fixtures/sample_config.toml"

# ===== Tests =====
Assert-Exists -Path $TestsRoot -Label "tests/"
Assert-Exists -Path (Join-Path $TestsRoot "test_openenv_demo_env_smoke.py") -Label "tests/test_openenv_demo_env_smoke.py"
Assert-Exists -Path (Join-Path $TestsRoot "test_openenv_adapter.py") -Label "tests/test_openenv_adapter.py"

# ===== Manifest content =====
$Manifest = Load-JsonFile -Path $ManifestPath
Assert-JsonHasKeys -Object $Manifest -Keys @(
    "bundle_type",
    "created_at",
    "env_name",
    "latest_run_artifacts_included",
    "bundle_dir",
    "sections",
    "validation_targets",
    "status"
) -Label "manifest.json"

if ($Manifest["bundle_type"] -ne "openenv_purple_submission") {
    throw "manifest.bundle_type inválido: $($Manifest["bundle_type"])"
}

if ($Manifest["status"] -ne "PREPARED") {
    throw "manifest.status inválido: $($Manifest["status"])"
}

# ===== Source sanity =====
$expectedEnvName = [string]$Manifest["env_name"]

$OpenEnvYamlPath = Join-Path $EnvRoot "openenv.yaml"
$OpenEnvYamlText = Get-Content -Raw -Path $OpenEnvYamlPath

if ($OpenEnvYamlText -notmatch "name:\s*$([regex]::Escape($expectedEnvName))") {
    throw "openenv.yaml no parece declarar name: $expectedEnvName"
}
if ($OpenEnvYamlText -notmatch "port:\s*8011") {
    throw "openenv.yaml no parece declarar port: 8011"
}

$AppPyPath = Join-Path $EnvServerRoot "app.py"
$AppPyText = Get-Content -Raw -Path $AppPyPath

foreach ($needle in @("/health", "/reset", "/step", "/state")) {
    if ($AppPyText -notmatch [regex]::Escape($needle)) {
        throw "server/app.py no contiene el endpoint esperado: $needle"
    }
}

$DockerfilePath = Join-Path $EnvServerRoot "Dockerfile"
$DockerfileText = Get-Content -Raw -Path $DockerfilePath

if ($DockerfileText -notmatch "EXPOSE\s+8011") {
    throw "Dockerfile no expone el puerto 8011"
}
if ($DockerfileText -notmatch "uvicorn") {
    throw "Dockerfile no parece arrancar uvicorn"
}

$AdapterPyPath = Join-Path $AdapterRoot "adapter.py"
$AdapterPyText = Get-Content -Raw -Path $AdapterPyPath

if ($AdapterPyText -notmatch "class\s+OpenEnvAdapter") {
    throw "adapter.py no contiene class OpenEnvAdapter"
}

foreach ($needle in @("def health", "def reset", "def step", "def state")) {
    if ($AdapterPyText -notmatch $needle) {
        throw "adapter.py no contiene el método esperado: $needle"
    }
}

# ===== Latest run =====
$LatestRunStatus = "not_required"

if ($RequireLatestRun.IsPresent) {
    Assert-Exists -Path $LatestRunRoot -Label "evidence/latest_run/"
    Assert-Exists -Path (Join-Path $LatestRunRoot "summary.json") -Label "evidence/latest_run/summary.json"
    Assert-Exists -Path (Join-Path $LatestRunRoot "track_payload.json") -Label "evidence/latest_run/track_payload.json"
    $LatestRunStatus = "required_and_present"
}
elseif (Test-Path $LatestRunRoot) {
    $LatestRunStatus = "present"
}

if (Test-Path $LatestRunRoot) {
    $LatestSummaryPath = Join-Path $LatestRunRoot "summary.json"
    if (Test-Path $LatestSummaryPath) {
        $LatestSummary = Load-JsonFile -Path $LatestSummaryPath
        Assert-JsonHasKeys -Object $LatestSummary -Keys @(
            "adapter",
            "env_name",
            "base_url",
            "checks",
            "validation"
        ) -Label "latest_run/summary.json"

        if ($LatestSummary["adapter"] -ne "openenv") {
            throw "latest_run/summary.json adapter inválido: $($LatestSummary["adapter"])"
        }
    }

    $TrackPayloadPath = Join-Path $LatestRunRoot "track_payload.json"
    if (Test-Path $TrackPayloadPath) {
        $TrackPayload = Load-JsonFile -Path $TrackPayloadPath
        Assert-JsonHasKeys -Object $TrackPayload -Keys @(
            "adapter",
            "base_url",
            "env_name",
            "action_plan",
            "require_success",
            "live_check"
        ) -Label "latest_run/track_payload.json"

        if ($TrackPayload["adapter"] -ne "openenv") {
            throw "latest_run/track_payload.json adapter inválido: $($TrackPayload["adapter"])"
        }
    }
}

$Result = [ordered]@{
    validation = "VALIDATION OK"
    bundle_dir = $BundlePath
    manifest = $ManifestPath
    checks = [ordered]@{
        bundle_structure = $true
        env_files = $true
        adapter_files = $true
        evidence_files = $true
        test_files = $true
        manifest_content = $true
        source_sanity = $true
        latest_run = $LatestRunStatus
    }
}

$ValidationPath = Join-Path $BundlePath "validation_result.json"
$ResultJson = $Result | ConvertTo-Json -Depth 20
[System.IO.File]::WriteAllText($ValidationPath, $ResultJson, [System.Text.Encoding]::UTF8)

Write-Host ""
Write-Host "VALIDATION OK"
Write-Host "Bundle      : $BundlePath"
Write-Host "Manifest    : $ManifestPath"
Write-Host "Validation  : $ValidationPath"
Write-Host "Latest run  : $LatestRunStatus"
