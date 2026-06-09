from __future__ import annotations

import json
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _fixtures_dir() -> Path:
    return _repo_root() / "tests" / "fixtures" / "skillsbench"


sys.path.insert(0, str(_repo_root() / "src"))


def _load_fixture(name: str) -> dict:
    return json.loads((_fixtures_dir() / name).read_text(encoding="utf-8"))


def test_forensics_detects_emitted_refs_dropped_by_official_result() -> None:
    from aegisforge.telemetry.skillsbench_forensics import build_forensic_run

    official = _load_fixture("sample_gateway_zero_result.json")
    contract = _load_fixture("sample_aegisforge_final_contract.json")
    execution = _load_fixture("sample_workspace_execution.json")

    run = build_forensic_run(
        official_results=official,
        final_contracts=[contract],
        workspace_executions=[execution],
        source="pytest-fixture",
        run_id="pytest-run",
    )

    assert run.official_shape == "nested_shard"
    assert run.records, run.as_dict()

    record = next(item for item in run.records if item.effective_task_id == "dialogue-parser")
    codes = set(record.anomaly_codes())

    assert record.artifact_survival().status == "emitted_refs_dropped"
    assert "artifact_refs_dropped" in codes
    assert "filesystem_outputs_not_scored" in codes
    assert "official_result_zeroed" in codes
    assert record.official_artifact_ref_count == 0
    assert record.emitted_artifact_count >= 2
    assert record.ok_workspace_write_count >= 1
