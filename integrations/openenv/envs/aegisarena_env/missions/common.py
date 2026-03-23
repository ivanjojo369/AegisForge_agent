from __future__ import annotations

import uuid
from typing import Any

from config import AegisArenaEnvConfig


def make_mission_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def build_base_mission(
    *,
    mission_id: str,
    mission_type: str,
    mission_summary: str,
    visible_context: list[dict[str, Any]] | None = None,
    hidden_truth: dict[str, Any] | None = None,
    available_tools: list[str] | None = None,
    max_steps: int,
    budget_default: int,
    heldout_mode: bool,
) -> dict[str, Any]:
    return {
        "mission_id": mission_id,
        "mission_type": mission_type,
        "mission_summary": mission_summary,
        "visible_context": visible_context or [],
        "hidden_truth": hidden_truth or {},
        "available_tools": available_tools or [],
        "max_steps": max_steps,
        "budget_default": budget_default,
        "heldout_mode": heldout_mode,
    }


def default_tools() -> list[str]:
    return [
        "inspect_context",
        "query_tool",
        "propose_plan",
        "take_action",
        "submit_final",
    ]


def merge_tools(*tool_groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()

    for group in tool_groups:
        for tool in group:
            if tool not in seen:
                merged.append(tool)
                seen.add(tool)

    return merged


def make_step_budget(
    config: AegisArenaEnvConfig,
    *,
    max_steps: int | None = None,
    budget_default: int | None = None,
) -> tuple[int, int]:
    return (
        max_steps if max_steps is not None else config.max_steps_default,
        budget_default if budget_default is not None else config.budget_default,
    )


def heldout_suffix(heldout_mode: bool) -> str:
    return "heldout" if heldout_mode else "standard"


def build_context_item(item_type: str, value: Any, *, tag: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "type": item_type,
        "value": value,
    }
    if tag is not None:
        item["tag"] = tag
    return item


def build_hidden_truth(
    *,
    success_label: str | None = None,
    expected_answer: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    truth: dict[str, Any] = {}
    if success_label is not None:
        truth["success_label"] = success_label
    if expected_answer is not None:
        truth["expected_answer"] = expected_answer
    if extra:
        truth.update(extra)
    return truth


def clamp_budget(value: int) -> int:
    return max(value, 1)


def mission_metadata_block(
    *,
    sprint_focus: int,
    mission_family: str,
    difficulty: str,
    heldout_mode: bool,
) -> dict[str, Any]:
    return {
        "sprint_focus": sprint_focus,
        "mission_family": mission_family,
        "difficulty": difficulty,
        "heldout_mode": heldout_mode,
    }
