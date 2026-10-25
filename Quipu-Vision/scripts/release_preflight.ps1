# Quipu Vision 2026: read-only release preflight for the local AegisForge checkout.
# Run from PowerShell on Windows. This script does NOT git add, commit, push or deploy.
param(
    [string]$Root = 'C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent',
    [int]$Port = 8002
)
$ErrorActionPreference = 'Stop'
function Check([string]$Name, [bool]$Pass, [string]$Detail = '') {
    $prefix = if ($Pass) { 'PASS' } else { 'CHECK' }
    Write-Host "$prefix $Name $Detail" -ForegroundColor $(if ($Pass) { 'Green' } else { 'Yellow' })
}
if (-not (Test-Path (Join-Path $Root 'Dockerfile'))) { throw "No Dockerfile at $Root" }
$Vision = Join-Path $Root 'Quipu-Vision'
if (-not (Test-Path $Vision)) { throw "Missing Quipu-Vision at $Vision" }
Write-Host "AegisForge: $Root" -ForegroundColor Cyan
Write-Host 'Git root and current branch:'
& git -C $Root rev-parse --show-toplevel
& git -C $Root branch --show-current
Write-Host '\nAegisForge changes (REVIEW before staging):' -ForegroundColor Cyan
& git -C $Root status --short
Write-Host '\nTracked nested-repo entry (160000 indicates a gitlink):' -ForegroundColor Cyan
& git -C $Root ls-files --stage -- 'Quipu-Vision'
$NestedGit = Test-Path (Join-Path $Vision '.git')
Check 'Quipu-Vision contains .git' $NestedGit '(informational; if true, inspect repo ownership before staging)'
Write-Host 'Quipu-Vision resolved git root:'
& git -C $Vision rev-parse --show-toplevel
Write-Host '\nGit whitespace validation (LF/CRLF warnings on Windows may be informational):' -ForegroundColor Cyan
& git -C $Root diff --check

$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (Test-Path $Python) {
    Write-Host '\nPython/OpenCV environment:' -ForegroundColor Cyan
    & $Python -c "import cv2; print('OpenCV:', cv2.__version__)"
    Check 'OpenCV 5 in .venv' ($LASTEXITCODE -eq 0)
} else { Check 'venv Python available' $false $Python }

$TestFile = Join-Path $Vision 'tests\test_workflow.py'
if ((Test-Path $TestFile) -and (Test-Path $Python)) {
    Write-Host '\nFunctional workflow tests:' -ForegroundColor Cyan
    & $Python -m unittest discover -s (Join-Path $Vision 'tests') -p 'test_workflow.py' -v
    Check 'Python tests' ($LASTEXITCODE -eq 0)
}
$JsTests = Join-Path $Vision 'tests\web'
if ((Test-Path $JsTests) -and (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host '\nNode.js web tests:' -ForegroundColor Cyan
    $files = @(Get-ChildItem -Path $JsTests -Filter '*.test.mjs' -File | ForEach-Object { $_.FullName })
    if ($files.Count -gt 0) { & node --test $files }
    Check 'Node web tests' ($LASTEXITCODE -eq 0)
}
Write-Host '\nDocker status (no build performed):' -ForegroundColor Cyan
& docker ps --filter 'name=quipu-vision-docker' --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
$Base = "http://127.0.0.1:$Port"
$Routes = @('/health', '/vision/capabilities', '/demo/vision',
            '/demo/vision/image-quality.js', '/demo/vision/evidence-overlay.js',
            '/demo/vision/decision-trace.js', '/demo/vision/evaluation-lab.html')
foreach ($route in $Routes) {
    try {
        $response = Invoke-WebRequest -Uri ($Base + $route) -TimeoutSec 8 -UseBasicParsing -ErrorAction Stop
        Check $route ($response.StatusCode -eq 200) "HTTP $($response.StatusCode)"
    } catch {
        Check $route $false $_.Exception.Message
    }
}
Write-Host '\nPreflight completed. A passing health endpoint does NOT demonstrate image processing or AWS deployment.' -ForegroundColor Cyan
