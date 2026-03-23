from __future__ import annotations

import json
from pathlib import Path

from aegisforge.adapters.tau2.quipu_lab import (
    build_minimal_result,
    build_sample_task,
    execute_tool,
    get_default_policy_rules,
    get_default_tools,
    get_policy_excerpt,
    load_task_from_json,
)


def _fixture_path(*parts: str) -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures" / "tau2" / "quipu_lab" / Path(*parts)


def test_quipu_lab_sample_task_builds() -> None:
    task = build_sample_task()

    assert task.task_id == "quipu_lab_demo_task_001"
    assert task.metadata["domain"] == "quipu_lab"
    assert task.metadata["mode"] == "tau2-style"
    assert isinstance(task.required_tools, list)
    assert "draft_structured_plan" in task.required_tools


def test_quipu_lab_policy_excerpt_has_rules() -> None:
    excerpt = get_policy_excerpt()

    assert excerpt["domain"] == "quipu_lab"
    assert excerpt["version"] == "0.1.0"
    assert isinstance(excerpt["rules"], list)
    assert len(excerpt["rules"]) >= 1

    rules = get_default_policy_rules()
    assert len(rules) >= 1
    assert rules[0].rule_id.startswith("qlab-")


def test_quipu_lab_tool_catalog_executes_known_tool() -> None:
    tools = get_default_tools()
    tool_names = [tool.name for tool in tools]

    assert "list_available_assets" in tool_names

    result = execute_tool("list_available_assets", {"kind": "general"})
    assert result["ok"] is True
    assert result["tool"] == "list_available_assets"
    assert "assets" in result
    assert len(result["assets"]) >= 1


def test_quipu_lab_tool_execution_rejects_unknown_tool() -> None:
    result = execute_tool("totally_unknown_tool", {"foo": "bar"})

    assert result["ok"] is False
    assert result["tool"] == "totally_unknown_tool"
    assert "unknown tool" in result["error"]


def test_quipu_lab_fixture_task_loads() -> None:
    task = load_task_from_json(_fixture_path("task_min.json"))

    assert task.task_id == "quipu_lab_test_task_001"
    assert task.title == "Validate minimal quipu_lab tau2-style task"
    assert task.metadata["domain"] == "quipu_lab"
    assert task.required_tools == ["list_available_assets"]


def test_quipu_lab_minimal_result_matches_expected_shape() -> None:
    task = load_task_from_json(_fixture_path("task_min.json"))
    expected = json.loads(_fixture_path("expected_result_min.json").read_text(encoding="utf-8"))

    result = build_minimal_result(task)

    assert result["task_id"] == expected["task_id"]
    assert result["status"] == expected["status"]
    assert result["summary"] == expected["summary"]
    assert result["used_tools"] == expected["used_tools"]


def test_quipu_lab_smoke_flow_end_to_end() -> None:
    task = load_task_from_json(_fixture_path("task_min.json"))
    tool_result = execute_tool("list_available_assets", {"kind": "general"})
    final_result = build_minimal_result(task)

    assert task.metadata["mode"] == "tau2-style"
    assert tool_result["ok"] is True
    assert final_result["status"] == "ok"
    assert "structured response" in final_result["summary"]
    assert final_result["used_tools"] == ["list_available_assets"]
    