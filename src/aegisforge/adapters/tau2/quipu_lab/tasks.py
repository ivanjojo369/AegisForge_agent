from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .policy import validate_task_payload
from .schemas import QuipuLabTask
from .tools import get_default_tools


def build_sample_task() -> QuipuLabTask:
    """Return a minimal τ²-style sample task for quipu_lab."""
    return QuipuLabTask(
        task_id="quipu_lab_demo_task_001",
        title="Prepare a structured lab coordination response",
        user_goal=(
            "Review the context, identify the relevant lab assets, and produce "
            "a concise structured response with a short plan."
        ),
        conversation_context=[
            {
                "role": "user",
                "content": (
                    "I need help organizing the next step for a small research "
                    "coordination task inside quipu_lab."
                ),
            }
        ],
        required_tools=[
            "list_available_assets",
            "draft_structured_plan",
        ],
        success_criteria=[
            "references available assets consistently",
            "produces a structured plan",
            "respects task constraints",
        ],
        constraints=[
            "do not invent unavailable tools",
            "keep the answer concise and structured",
            "do not claim hidden external access",
        ],
        metadata={
            "domain": "quipu_lab",
            "mode": "tau2-style",
            "split": "base",
            "difficulty": "easy",
            "tool_count": len(get_default_tools()),
        },
    )


def task_to_trace_seed(task: QuipuLabTask) -> dict[str, Any]:
    """Return a compact seed object derived from the task."""
    return {
        "task_id": task.task_id,
        "title": task.title,
        "required_tools": list(task.required_tools),
        "constraints": list(task.constraints),
        "metadata": dict(task.metadata),
    }


def load_task_from_json(path: str | Path) -> QuipuLabTask:
    """Load and validate a task from a JSON file."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    errors = validate_task_payload(raw)
    if errors:
        joined = "; ".join(errors)
        raise ValueError(f"invalid quipu_lab task payload: {joined}")
    return QuipuLabTask.from_dict(raw)


def dump_task_to_json(task: QuipuLabTask, path: str | Path) -> None:
    """Write a task JSON file."""
    Path(path).write_text(
        json.dumps(task.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def build_minimal_result(task: QuipuLabTask) -> dict[str, Any]:
    """Return a compact deterministic mock result."""
    return {
        "task_id": task.task_id,
        "status": "ok",
        "summary": (
            "Task processed in quipu_lab τ²-style mode with a structured response "
            "and tool-aware reasoning path."
        ),
        "used_tools": list(task.required_tools),
    }
