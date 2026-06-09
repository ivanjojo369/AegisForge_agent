from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _fixtures_dir() -> Path:
    return _repo_root() / "tests" / "fixtures" / "skillsbench"


def _load_tool_module(name: str) -> ModuleType:
    path = _repo_root() / "tools" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader, f"cannot load tool module: {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


def _load_fixture(name: str) -> dict:
    return json.loads((_fixtures_dir() / name).read_text(encoding="utf-8"))


def test_result_shape_audit_accepts_nested_and_rejects_legacy_flat_for_validator() -> None:
    audit_mod = _load_tool_module("skillsbench_result_shape_audit.py")

    nested = audit_mod.audit_result_payload(_load_fixture("sample_nested_results.json"))
    flat = audit_mod.audit_result_payload(_load_fixture("sample_flat_legacy_results.json"))
    mixed = audit_mod.audit_result_payload(_load_fixture("sample_mixed_results.json"))

    assert nested.shape == "nested_shard"
    assert nested.validator_compatible
    assert nested.sql_leaderboard_compatible
    assert nested.row_count == 3

    assert flat.shape == "legacy_flat"
    assert not flat.validator_compatible
    assert flat.row_count == 2
    assert any("legacy" in message.lower() for message in flat.validator_errors)

    assert mixed.shape == "mixed"
    assert not mixed.validator_compatible
    assert mixed.row_count == 2


def test_result_shape_audit_counts_artifact_ref_states() -> None:
    audit_mod = _load_tool_module("skillsbench_result_shape_audit.py")
    zero = audit_mod.audit_result_payload(_load_fixture("sample_gateway_zero_result.json"))

    assert zero.shape == "nested_shard"
    assert zero.row_count == 1
    assert zero.artifact_refs_empty_count == 1
    assert zero.artifact_refs_populated_count == 0
    assert zero.eligible_count == 1
    assert zero.passed_count == 0
    assert zero.reward_sum == 0.0
