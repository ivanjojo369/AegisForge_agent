"""Static plan quality checks; never produces authorizations or executions."""
from __future__ import annotations
from typing import Any, Mapping


def review_plan(plan: Mapping[str, Any], *, max_steps: int = 20) -> dict[str, object]:
    if not isinstance(plan, Mapping) or type(max_steps) is not int or max_steps <= 0:
        raise ValueError("invalid plan review input")
    issues: list[str] = []
    steps = plan.get("steps")
    if not isinstance(steps, (list, tuple)) or not steps:
        issues.append("steps_missing")
        steps = ()
    if len(steps) > max_steps:
        issues.append("step_count_exceeded")
    seen: set[str] = set()
    for i, step in enumerate(steps):
        if not isinstance(step, Mapping):
            issues.append(f"step_{i}_invalid")
            continue
        sid, action = step.get("id"), step.get("action")
        if not isinstance(sid, str) or not sid or sid in seen:
            issues.append(f"step_{i}_id_invalid_or_duplicate")
        else:
            seen.add(sid)
        if not isinstance(action, str) or not action.strip():
            issues.append(f"step_{i}_action_missing")
        if not step.get("acceptance_criteria"):
            issues.append(f"step_{i}_acceptance_missing")
    return {"schema": "aegisforge.nexus.intelligence.plan_review.v1",
            "structurally_acceptable": not issues, "issues": issues,
            "requires_host_approval": True, "effects_executed": False,
            "verification_completed": False}


__all__ = ["review_plan"]
