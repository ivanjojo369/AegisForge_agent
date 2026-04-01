from __future__ import annotations

from aegisforge.adapters.tau2.quipu_lab.policy import (
    get_default_policy_rules,
    get_policy_excerpt,
)
from aegisforge.adapters.tau2.quipu_lab.tasks import (
    build_minimal_result,
    build_sample_task,
    build_task_catalog,
    dump_task_to_json,
    get_smoke_tasks,
    get_task_by_id,
    get_tasks,
    iter_task_ids,
    load_task_from_json,
    task_to_trace_seed,
    validate_catalog,
)
from aegisforge.adapters.tau2.quipu_lab.tools import (
    execute_tool,
    get_default_tools,
)

EXPECTED_SMOKE_TASK_IDS = {
    "quipu_lab_mcu_chained_craft_constraints",
    "quipu_lab_officeqa_year_distractor_exact_extraction",
    "quipu_lab_tau2_airline_policy_clarification",
    "quipu_lab_car_ambiguous_vehicle_preference",
}

def test_quipu_lab_catalog_builds_multiple_tasks() -> None:
    catalog = build_task_catalog()
    base_tasks = get_tasks("base")
    task_ids = iter_task_ids(catalog)
    base_task_ids = iter_task_ids(base_tasks)

    assert catalog
    assert len(task_ids) == len(set(task_ids))
    assert len(base_tasks) == len(catalog)
    assert set(base_task_ids) == set(task_ids)

    sample_task = build_sample_task()
    assert sample_task.task_id in task_ids
    assert sample_task.metadata["domain"] == "quipu_lab"
    assert sample_task.metadata["mode"] == "tau2-style"

    for task in catalog:
        assert task.metadata["domain"] == "quipu_lab"
        assert task.metadata["mode"] == "tau2-style"
        assert task.metadata["split"] == "base"
        assert len(task.conversation_context) >= 2
        assert len(task.success_criteria) >= 1
        assert len(task.constraints) >= 1


def test_quipu_lab_get_tasks_and_get_task_by_id_are_consistent() -> None:
    base_tasks = get_tasks("base")
    smoke_tasks = get_smoke_tasks()

    base_ids = set(iter_task_ids(base_tasks))
    smoke_ids = set(iter_task_ids(smoke_tasks))

    assert smoke_ids == EXPECTED_SMOKE_TASK_IDS
    assert len(smoke_tasks) == len(EXPECTED_SMOKE_TASK_IDS)
    assert len(smoke_ids) == len(smoke_tasks)
    assert smoke_ids.issubset(base_ids)

    selected = get_task_by_id("quipu_lab_tau2_airline_policy_clarification")
    assert selected.task_id == "quipu_lab_tau2_airline_policy_clarification"
    assert selected.metadata["enemy_repo"] == "RDI-Foundation/tau2-agentbeats"
    assert selected.metadata["pressure_type"] == "clarify_before_action"

    smoke_selected = get_task_by_id("quipu_lab_car_ambiguous_vehicle_preference")
    assert smoke_selected.task_id in smoke_ids
    assert smoke_selected.metadata["smoke"] is True

def test_quipu_lab_catalog_validation_passes() -> None:
    assert validate_catalog() == []


def test_quipu_lab_policy_excerpt_has_rules() -> None:
    excerpt = get_policy_excerpt()

    assert excerpt["domain"] == "quipu_lab"
    assert excerpt["version"] == "0.1.0"
    assert isinstance(excerpt["rules"], list)
    assert len(excerpt["rules"]) >= 1

    rules = get_default_policy_rules()
    assert len(rules) >= 1
    assert rules[0].rule_id.startswith("qlab-")


def test_quipu_lab_tool_catalog_executes_known_and_unknown_tools() -> None:
    tools = get_default_tools()
    tool_names = {tool.name for tool in tools}

    assert {"list_available_assets", "lookup_lab_note", "draft_structured_plan"}.issubset(
        tool_names
    )

    asset_result = execute_tool("list_available_assets", {"kind": "general"})
    assert asset_result["ok"] is True
    assert asset_result["tool"] == "list_available_assets"
    assert len(asset_result["assets"]) >= 1

    unknown = execute_tool("totally_unknown_tool", {"foo": "bar"})
    assert unknown["ok"] is False
    assert unknown["tool"] == "totally_unknown_tool"
    assert "unknown tool" in unknown["error"]


def test_quipu_lab_dump_and_load_roundtrip(tmp_path) -> None:
    task = get_task_by_id("quipu_lab_officeqa_year_distractor_exact_extraction")
    output_path = tmp_path / "quipu_lab_task.json"

    dump_task_to_json(task, output_path)
    loaded = load_task_from_json(output_path)

    assert loaded.task_id == task.task_id
    assert loaded.title == task.title
    assert loaded.required_tools == task.required_tools
    assert loaded.metadata["domain"] == "quipu_lab"


def test_quipu_lab_trace_seed_and_minimal_result_are_structured() -> None:
    task = get_task_by_id("quipu_lab_mcu_chained_craft_constraints")
    trace_seed = task_to_trace_seed(task)
    result = build_minimal_result(task)

    assert trace_seed["task_id"] == task.task_id
    assert trace_seed["required_tools"] == task.required_tools
    assert trace_seed["metadata"]["domain"] == "quipu_lab"

    assert result["task_id"] == task.task_id
    assert result["status"] == "ok"
    assert "structured response" in result["summary"]
    assert result["used_tools"] == task.required_tools
    assert result["metadata"]["split"] == "base"


def test_quipu_lab_smoke_flow_end_to_end() -> None:
    task = get_task_by_id("quipu_lab_tau2_airline_policy_clarification")

    tool_results = []
    for tool_name in task.required_tools:
        if tool_name == "lookup_lab_note":
            args = {"query": task.title}
        elif tool_name == "draft_structured_plan":
            args = {"goal": task.user_goal, "max_steps": 3}
        else:
            args = {"kind": "general"}
        tool_results.append(execute_tool(tool_name, args))

    final_result = build_minimal_result(task)

    assert task.metadata["mode"] == "tau2-style"
    assert len(tool_results) == len(task.required_tools)
    assert all(result["ok"] is True for result in tool_results)
    assert final_result["status"] == "ok"
    assert final_result["used_tools"] == task.required_tools
