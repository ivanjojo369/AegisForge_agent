from __future__ import annotations

"""Lightweight τ²-style track evaluation for AegisForge.

This module provides a small, local validation layer for τ²-shaped payloads that
have been absorbed into the AegisForge evaluation stack. It does not attempt to
replicate the full upstream τ² benchmark. Instead, it checks whether a payload
contains the minimum structural signals that matter for Purple-facing local
validation: a domain, a task definition, multi-turn context, and any declared
tool or task expectations.

Within AegisForge, this file serves two purposes:
1. it validates that τ²-style requests remain structurally coherent after being
   routed through the repository's adapter and runtime layers; and
2. it provides a reusable evaluation pattern for other benchmark-inspired
   capability blocks that may later be integrated into the same Purple agent.
"""

from collections.abc import Mapping
from typing import Any

from ..schemas import TrackResult

DESCRIPTION = (
    "Performs lightweight structural validation for τ²-style payloads inside "
    "the AegisForge evaluation stack, checking domain framing, task shape, "
    "multi-turn context, and declared tool or task expectations."
)


def _extract_task(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized task mapping from either nested or flat payloads.

    The τ²-style fixtures used inside AegisForge may arrive either as a nested
    ``task`` object or as a flatter request shape. This helper keeps the track
    tolerant to both so the validation layer remains reusable across adapters,
    tests, and future domain integrations.
    """

    task = payload.get("task")
    if isinstance(task, Mapping):
        return dict(task)

    direct_task_keys = {
        "task_id",
        "title",
        "user_goal",
        "conversation_context",
        "required_tools",
        "success_criteria",
        "constraints",
        "metadata",
    }
    if any(key in payload for key in direct_task_keys):
        return {
            "task_id": payload.get("task_id"),
            "title": payload.get("title"),
            "user_goal": payload.get("user_goal"),
            "conversation_context": payload.get("conversation_context", []),
            "required_tools": payload.get("required_tools", []),
            "success_criteria": payload.get("success_criteria", []),
            "constraints": payload.get("constraints", []),
            "metadata": payload.get("metadata", {}),
        }

    return {}



def _extract_domain(payload: dict[str, Any], task: dict[str, Any]) -> str | None:
    """Resolve the domain name from payload-level or task-level metadata."""

    metadata = task.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    domain = (
        payload.get("domain")
        or payload.get("domain_name")
        or payload.get("track")
        or metadata.get("domain")
    )
    if domain is None:
        return None
    return str(domain)



def _extract_turns(payload: dict[str, Any], task: dict[str, Any]) -> list[Any]:
    """Return multi-turn context from either explicit turns or task context."""

    turns = payload.get("turns")
    if isinstance(turns, list) and turns:
        return turns

    conversation_context = task.get("conversation_context", [])
    if isinstance(conversation_context, list):
        return conversation_context

    return []



def _extract_tools(payload: dict[str, Any], task: dict[str, Any]) -> list[Any]:
    """Return declared tools from either the payload or the task definition."""

    tools = payload.get("tools")
    if isinstance(tools, list) and tools:
        return tools

    required_tools = task.get("required_tools", [])
    if isinstance(required_tools, list):
        return required_tools

    return []



def evaluate(payload: Mapping[str, Any] | None = None) -> TrackResult:
    """Evaluate whether a payload is structurally ready for local τ² validation.

    This function is intentionally lightweight. Its role is not to replace the
    upstream benchmark or claim score comparability on its own. Instead, it
    checks whether a τ²-style request has been integrated into AegisForge with
    enough structure to support local Purple-oriented validation and adapter
    testing.
    """

    payload = dict(payload or {})
    adapter = str(payload.get("adapter", "tau2"))
    task = _extract_task(payload)
    domain = _extract_domain(payload, task)
    turns = _extract_turns(payload, task)
    tools = _extract_tools(payload, task)

    success_criteria = task.get("success_criteria", [])
    if not isinstance(success_criteria, list):
        success_criteria = []

    constraints = task.get("constraints", [])
    if not isinstance(constraints, list):
        constraints = []

    details = {
        "description": DESCRIPTION,
        "adapter": adapter,
        "domain": domain,
        "turn_count": len(turns),
        "tool_count": len(tools),
        "has_task": bool(task),
        "has_user_goal": bool(task.get("user_goal")),
        "success_criteria_count": len(success_criteria),
        "constraint_count": len(constraints),
    }

    if adapter != "tau2":
        return TrackResult(
            track="tau2",
            status="skip",
            summary="payload targets a different adapter; τ² structural validation skipped",
            score=0.0,
            details=details,
        )

    missing: list[str] = []
    if not domain:
        missing.append("domain")
    if not task:
        missing.append("task")
    if task and not task.get("user_goal"):
        missing.append("user_goal")
    if not turns:
        missing.append("turns_or_conversation_context")

    if missing:
        details["missing"] = missing
        return TrackResult(
            track="tau2",
            status="warn",
            summary=f"τ² payload is incomplete for local validation: missing {', '.join(missing)}",
            score=0.5,
            details=details,
        )

    score = 0.70
    if tools:
        score += 0.10
    if len(turns) >= 2:
        score += 0.05
    if success_criteria:
        score += 0.05
    if constraints:
        score += 0.05

    if domain == "quipu_lab":
        summary = (
            "τ² payload includes a quipu_lab-compatible domain/task structure "
            "suitable for local Purple validation inside AegisForge"
        )
    else:
        summary = (
            "τ² payload includes a domain and multi-turn task structure suitable "
            "for local validation inside AegisForge"
        )

    return TrackResult(
        track="tau2",
        status="pass",
        summary=summary,
        score=min(score, 0.95),
        details=details,
    )
