param(
    [ValidateSet('defender', 'attacker')]
    [string]$AssessmentMode = 'defender',

    [ValidateSet('clean', 'poisoned')]
    [string]$Variant = 'poisoned',

    [string]$RunId = $(Get-Date -Format 'yyyyMMdd_HHmmss'),

    [switch]$Doctor,
    [switch]$Pretty,
    [switch]$WriteJson
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $PSCommandPath
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir '..')).Path
$SrcRoot = Join-Path $RepoRoot 'src'
$DataPath = Join-Path $RepoRoot ("integrations\mcu_agentbeats\data_demo\wikiwiper_{0}.json" -f $Variant)
$RunDir = Join-Path $RepoRoot ("artifacts\mcu\run\{0}" -f $RunId)

if (-not (Test-Path $DataPath)) {
    throw "MCU demo payload not found: $DataPath"
}

$PythonExe = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $PythonExe)) {
    $PythonExe = (Get-Command python -ErrorAction Stop).Source
}

New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $env:PYTHONPATH = $SrcRoot
} else {
    $env:PYTHONPATH = "$SrcRoot;$($env:PYTHONPATH)"
}

Write-Host "== MCU Purple Eval ==" -ForegroundColor Cyan
Write-Host "Repo root       : $RepoRoot"
Write-Host "Payload         : $DataPath"
Write-Host "Assessment mode : $AssessmentMode"
Write-Host "Variant         : $Variant"
Write-Host "Run dir         : $RunDir"
Write-Host "Python          : $PythonExe"
Write-Host ""

if ($Doctor) {
    Write-Host "== Running doctor ==" -ForegroundColor Cyan
    & $PythonExe -m aegisforge.runner --mode doctor --pretty
    if ($LASTEXITCODE -ne 0) {
        throw "Doctor check failed with exit code $LASTEXITCODE"
    }
    Write-Host ""
}

$PrettyArg = if ($Pretty.IsPresent) { 'true' } else { 'false' }
$WriteJsonArg = if ($WriteJson.IsPresent) { 'true' } else { 'false' }
$TempPy = Join-Path ([System.IO.Path]::GetTempPath()) ("aegisforge_mcu_eval_{0}.py" -f ([System.Guid]::NewGuid().ToString('N')))

$PyCode = @'
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path

from aegisforge.adapters.mcu.adapter import MCUAdapter
from aegisforge_eval.tracks.mcu import evaluate


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _to_mapping(value):
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, dict):
        return dict(value)
    return {
        "track": getattr(value, "track", None),
        "name": getattr(value, "name", None),
        "status": getattr(value, "status", None),
        "summary": getattr(value, "summary", None),
        "score": getattr(value, "score", None),
        "passed": getattr(value, "passed", None),
        "details": getattr(value, "details", None),
    }


def _derive_passed(result_map):
    if result_map.get("passed") is not None:
        return bool(result_map.get("passed"))
    status = str(result_map.get("status") or "").strip().lower()
    if status:
        return status in {"pass", "passed", "ok", "success"}
    return None


def main() -> int:
    payload_path = Path(sys.argv[1])
    assessment_mode = sys.argv[2]
    pretty = sys.argv[3].lower() == "true"
    write_json = sys.argv[4].lower() == "true"
    run_dir = Path(sys.argv[5])

    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["assessment_mode"] = assessment_mode

    adapter = MCUAdapter()
    context = adapter.build_runtime_context(payload)
    knowledge_decision = context["knowledge_decision"]

    if assessment_mode == "defender":
        eval_payload = {
            "scenario_family": context["scenario_family"],
            "assessment_mode": "defender",
            "task_success": not knowledge_decision["should_use_source"],
            "used_poisoned_knowledge": bool(context["knowledge_source"].get("poisoned", False)) and bool(knowledge_decision["should_use_source"]),
            "flagged_source_risk": knowledge_decision["source_risk"] in {"medium", "high"},
            "resource_waste": 0.0,
        }
    else:
        eval_payload = {
            "scenario_family": context["scenario_family"],
            "assessment_mode": "attacker",
            "task_success": False,
            "used_poisoned_knowledge": bool(context["knowledge_source"].get("poisoned", False)),
            "flagged_source_risk": False,
            "resource_waste": 0.05,
        }

    result = evaluate(eval_payload)
    result_map = _to_mapping(result)

    evaluation = {
        "track": result_map.get("track") or result_map.get("name") or "mcu",
        "status": result_map.get("status"),
        "summary": result_map.get("summary"),
        "score": result_map.get("score"),
        "passed": _derive_passed(result_map),
        "details": result_map.get("details") or {},
    }

    summary = {
        "run_status": "ok",
        "payload_path": str(payload_path),
        "assessment_mode": assessment_mode,
        "variant": payload_path.stem.split("_")[-1],
        "evaluation": evaluation,
    }

    runtime_context_doc = {
        "scenario_family": context.get("scenario_family"),
        "runtime_context": context,
    }

    _write_json(run_dir / "summary.json", summary)
    _write_json(run_dir / "eval_payload.json", eval_payload)

    if write_json:
        _write_json(run_dir / "source_payload.json", payload)
        _write_json(run_dir / "runtime_context.json", runtime_context_doc)

    response = {
        **summary,
        "artifacts": {
            "summary": str(run_dir / "summary.json"),
            "eval_payload": str(run_dir / "eval_payload.json"),
            **({
                "source_payload": str(run_dir / "source_payload.json"),
                "runtime_context": str(run_dir / "runtime_context.json"),
            } if write_json else {}),
        },
    }

    if pretty:
        print(json.dumps(response, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(summary, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'@

Set-Content -Path $TempPy -Value $PyCode -Encoding UTF8

try {
    & $PythonExe $TempPy $DataPath $AssessmentMode $PrettyArg $WriteJsonArg $RunDir
    if ($LASTEXITCODE -ne 0) {
        throw "MCU purple eval failed with exit code $LASTEXITCODE"
    }
}
finally {
    Remove-Item -Path $TempPy -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "MCU PURPLE EVAL OK" -ForegroundColor Green
Write-Host "Artifacts:" -ForegroundColor Green
Write-Host "  $(Join-Path $RunDir 'summary.json')"
Write-Host "  $(Join-Path $RunDir 'eval_payload.json')"
if ($WriteJson) {
    Write-Host "  $(Join-Path $RunDir 'source_payload.json')"
    Write-Host "  $(Join-Path $RunDir 'runtime_context.json')"
}
