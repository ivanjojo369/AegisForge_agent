[CmdletBinding()]
param(
    [ValidateSet("poisoned", "attack", "clean", "benign")]
    [string]$Variant = "poisoned",
    [string]$PayloadPath,
    [switch]$Pretty,
    [switch]$WriteJson
)

$ErrorActionPreference = "Stop"

function Resolve-RepoRoot {
    param([string]$ScriptRoot)
    return (Resolve-Path (Join-Path $ScriptRoot "..")).Path
}

function Resolve-PayloadPath {
    param(
        [string]$RepoRoot,
        [string]$Variant,
        [string]$PayloadPath
    )

    if ($PayloadPath) {
        if ([System.IO.Path]::IsPathRooted($PayloadPath)) {
            return (Resolve-Path $PayloadPath).Path
        }
        return (Resolve-Path (Join-Path $RepoRoot $PayloadPath)).Path
    }

    $normalized = $Variant.Trim().ToLowerInvariant()
    $fileName = if ($normalized -in @("clean", "benign")) { "clean_w2_payload.json" } else { "poisoned_w2_payload.json" }
    return (Resolve-Path (Join-Path $RepoRoot ("harness\AegisForge_scenarios\data\officeqa\taxwiztrap\payloads\" + $fileName))).Path
}

$RepoRoot = Resolve-RepoRoot -ScriptRoot $PSScriptRoot
$RunId = Get-Date -Format "yyyyMMdd_HHmmss"
$RunDir = Join-Path $RepoRoot ("artifacts\officeqa\run\" + $RunId)
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

New-Item -ItemType Directory -Path $RunDir -Force | Out-Null

$ResolvedPayloadPath = Resolve-PayloadPath -RepoRoot $RepoRoot -Variant $Variant -PayloadPath $PayloadPath
$SourcePayload = Get-Content $ResolvedPayloadPath -Raw | ConvertFrom-Json -AsHashtable
$canonicalVariant = if ($SourcePayload.variant) { [string]$SourcePayload.variant } elseif ($Variant.Trim().ToLowerInvariant() -in @("clean", "benign")) { "clean" } else { "poisoned" }

$TempPy = Join-Path $env:TEMP ("aegisforge_officeqa_eval_" + [guid]::NewGuid().ToString("N") + ".py")
@'
import json
import sys
from pathlib import Path

repo_root = Path(sys.argv[1])
payload_path = Path(sys.argv[2])
out_dir = Path(sys.argv[3])

sys.path.insert(0, str(repo_root / "src"))

from aegisforge.adapters.officeqa.adapter import OfficeQAAdapter
from aegisforge.strategy.officeqa import evaluate_document_payload
from aegisforge_eval.tracks.officeqa import evaluate as eval_officeqa

payload = json.loads(payload_path.read_text(encoding="utf-8"))
adapter = OfficeQAAdapter()
normalized = adapter.normalize(payload)
runtime_context = adapter.build_runtime_context(payload)
response = evaluate_document_payload(normalized)
evaluation = eval_officeqa(normalized, response)

passed = bool(evaluation.get("status") == "pass")
summary = {
    "track": "officeqa",
    "scenario": "taxwiztrap",
    "variant": normalized.get("variant") or payload.get("variant") or "unknown",
    "run_status": "ok",
    "payload_path": str(payload_path),
    "question": normalized.get("question", ""),
    "response_text": response.get("final_answer", ""),
    "evaluation": {
        "track": evaluation.get("track", "officeqa"),
        "status": evaluation.get("status", "unknown"),
        "summary": (
            "OfficeQA resisted TaxWizTrap-style document injection while preserving financial review quality."
            if passed else
            "OfficeQA did not fully satisfy the TaxWizTrap evaluation checks."
        ),
        "score": evaluation.get("score", 0.0),
        "passed": passed,
        "details": {
            "track": evaluation.get("track", "officeqa"),
            "scenario_family": "taxwiztrap",
            "variant": normalized.get("variant") or payload.get("variant") or "unknown",
            **dict(evaluation.get("checks") or {}),
        },
    },
}

(out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
(out_dir / "source_payload.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
(out_dir / "eval_payload.json").write_text(json.dumps(normalized, indent=2), encoding="utf-8")
(out_dir / "runtime_context.json").write_text(json.dumps(runtime_context, indent=2), encoding="utf-8")
(out_dir / "response.json").write_text(json.dumps(response, indent=2), encoding="utf-8")
'@ | Set-Content -Path $TempPy -Encoding UTF8

try {
    & $Python $TempPy $RepoRoot $ResolvedPayloadPath $RunDir
    if ($LASTEXITCODE -ne 0) {
        throw "OfficeQA purple eval failed with exit code $LASTEXITCODE"
    }
}
finally {
    if (Test-Path $TempPy) { Remove-Item $TempPy -Force -ErrorAction SilentlyContinue }
}

$SummaryPath = Join-Path $RunDir "summary.json"
$Summary = Get-Content $SummaryPath -Raw | ConvertFrom-Json -AsHashtable

Write-Host ""
Write-Host "== OfficeQA Purple Eval ==" -ForegroundColor Cyan
Write-Host ("Repo root : " + $RepoRoot)
Write-Host ("Payload   : " + $ResolvedPayloadPath)
Write-Host ("Variant   : " + $canonicalVariant)
Write-Host ("Run dir   : " + $RunDir)
Write-Host ("Python    : " + $Python)
Write-Host ""
Write-Host "OFFICEQA PURPLE EVAL OK" -ForegroundColor Green
Write-Host ("Artifacts written to: " + $RunDir) -ForegroundColor Cyan

if ($Pretty) {
    $Summary | ConvertTo-Json -Depth 20
}
