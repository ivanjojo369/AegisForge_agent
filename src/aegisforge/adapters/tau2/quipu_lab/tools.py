from __future__ import annotations

from typing import Any

from .schemas import ToolSpec


DEFAULT_TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="lookup_lab_note",
        description="Retrieve a compact note or memo from the quipu_lab knowledge base.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
        },
        safe=True,
        tags=["retrieval", "notes"],
    ),
    ToolSpec(
        name="list_available_assets",
        description="List named assets currently available to the task context.",
        input_schema={
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
            },
        },
        safe=True,
        tags=["inventory", "assets"],
    ),
    ToolSpec(
        name="draft_structured_plan",
        description="Produce a structured step plan from a short task description.",
        input_schema={
            "type": "object",
            "properties": {
                "goal": {"type": "string"},
                "max_steps": {"type": "integer"},
            },
            "required": ["goal"],
        },
        safe=True,
        tags=["planning"],
    ),
]


def get_default_tools() -> list[ToolSpec]:
    """Return the default tool inventory."""
    return list(DEFAULT_TOOL_SPECS)


def get_tool_index() -> dict[str, ToolSpec]:
    """Return tools indexed by name."""
    return {tool.name: tool for tool in get_default_tools()}


def find_tool(name: str) -> ToolSpec | None:
    """Find a tool by name."""
    return get_tool_index().get(name)


def get_tool_catalog() -> dict[str, Any]:
    """Return a serializable catalog of default tools."""
    return {
        "domain": "quipu_lab",
        "tools": [tool.to_dict() for tool in get_default_tools()],
    }


def execute_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Execute a deterministic mock tool.

    This is intentionally simple and safe. It provides a stable result shape
    for demos, smoke tests, and adapter wiring.
    """
    args = arguments or {}
    tool = find_tool(name)

    if tool is None:
        return {
            "ok": False,
            "tool": name,
            "error": f"unknown tool: {name}",
            "arguments": args,
        }

    if name == "lookup_lab_note":
        query = str(args.get("query", "")).strip() or "general quipu_lab guidance"
        return {
            "ok": True,
            "tool": name,
            "query": query,
            "result": (
                f"Mock note for '{query}': prioritize clarity, reproducibility, "
                "and explicit tool usage."
            ),
        }

    if name == "list_available_assets":
        kind = str(args.get("kind", "general"))
        return {
            "ok": True,
            "tool": name,
            "kind": kind,
            "assets": [
                {"name": "quipu_lab_policy_excerpt", "type": "json"},
                {"name": "quipu_lab_seed", "type": "json"},
                {"name": "quipu_lab_tools", "type": "json"},
            ],
        }

    if name == "draft_structured_plan":
        goal = str(args.get("goal", "")).strip() or "complete the quipu_lab task"
        max_steps_raw = args.get("max_steps", 3)
        try:
            max_steps = max(1, min(int(max_steps_raw), 8))
        except (TypeError, ValueError):
            max_steps = 3

        steps = [
            {"step": i + 1, "action": f"Subtask {i + 1} for: {goal}"}
            for i in range(max_steps)
        ]
        return {
            "ok": True,
            "tool": name,
            "goal": goal,
            "steps": steps,
        }

    return {
        "ok": False,
        "tool": name,
        "error": "tool exists but has no handler",
        "arguments": args,
    }
