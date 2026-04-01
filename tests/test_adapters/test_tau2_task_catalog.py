from __future__ import annotations

import json

from aegisforge.adapters.tau2.quipu_lab.tasks import (
    build_task_catalog,
    dump_catalog_to_json,
    get_smoke_tasks,
    get_tasks,
    iter_task_ids,
    validate_catalog,
)
from aegisforge.adapters.tau2.quipu_lab.tools import get_default_tools


def test_validate_catalog_returns_no_errors() -> None:
    assert validate_catalog() == []


def test_task_ids_are_unique() -> None:
    catalog = build_task_catalog()
    task_ids = iter_task_ids(catalog)

    assert catalog
    assert len(task_ids) == len(set(task_ids))


def test_all_required_tools_exist() -> None:
    catalog = build_task_catalog()
    tool_names = {tool.name for tool in get_default_tools()}

    assert tool_names

    for task in catalog:
        assert task.required_tools
        assert len(task.required_tools) == len(set(task.required_tools))
        assert set(task.required_tools).issubset(tool_names)


def test_smoke_subset_is_subset_of_base_catalog() -> None:
    base_tasks = get_tasks("base")
    smoke_tasks = get_smoke_tasks()

    base_ids = set(iter_task_ids(base_tasks))
    smoke_ids = set(iter_task_ids(smoke_tasks))

    assert smoke_ids
    assert smoke_ids.issubset(base_ids)

    for task in smoke_tasks:
        assert task.metadata["smoke"] is True
        assert task.metadata["split"] == "base"


def test_all_tasks_have_complete_metadata() -> None:
    catalog = build_task_catalog()
    allowed_difficulties = {"easy", "medium", "hard"}
    allowed_priorities = {"low", "medium", "medium_high", "high"}

    for task in catalog:
        metadata = task.metadata

        assert metadata["domain"] == "quipu_lab"
        assert metadata["mode"] == "tau2-style"
        assert metadata["split"] == "base"
        assert metadata["difficulty"] in allowed_difficulties
        assert metadata["priority"] in allowed_priorities
        assert isinstance(metadata["smoke"], bool)

        for field_name in (
            "task_family",
            "pressure_type",
            "expected_failure_mode",
            "enemy_repo",
            "source",
        ):
            assert isinstance(metadata[field_name], str)
            assert metadata[field_name].strip()

        assert metadata["tool_count"] == len(get_default_tools())


def test_dump_catalog_to_json_roundtrip(tmp_path) -> None:
    output_path = tmp_path / "quipu_lab_catalog.json"

    dump_catalog_to_json(output_path, split="base")

    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert payload["domain"] == "quipu_lab"
    assert payload["split"] == "base"
    assert payload["task_count"] == len(get_tasks("base"))
    assert isinstance(payload["tasks"], list)
    assert len(payload["tasks"]) == payload["task_count"]

    dumped_ids = [task["task_id"] for task in payload["tasks"]]
    assert dumped_ids == iter_task_ids(get_tasks("base"))

    for task in payload["tasks"]:
        assert task["metadata"]["domain"] == "quipu_lab"
        assert task["metadata"]["mode"] == "tau2-style"
        assert task["metadata"]["split"] == "base"
        