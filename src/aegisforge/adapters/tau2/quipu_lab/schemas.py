from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    """Return a stable UTC ISO-8601 timestamp."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(slots=True)
class PolicyRule:
    """A compact rule used by the quipu_lab domain."""

    rule_id: str
    title: str
    description: str
    severity: str = "medium"
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ToolSpec:
    """A simple tool definition for the τ²-style domain."""

    name: str
    description: str
    input_schema: dict[str, Any]
    safe: bool = True
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class QuipuLabTask:
    """Normalized task object for the quipu_lab domain."""

    task_id: str
    title: str
    user_goal: str
    conversation_context: list[dict[str, Any]] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "QuipuLabTask":
        return cls(
            task_id=str(payload.get("task_id", "quipu_lab_task")),
            title=str(payload.get("title", "Untitled quipu_lab task")),
            user_goal=str(payload.get("user_goal", "")),
            conversation_context=list(payload.get("conversation_context", [])),
            required_tools=list(payload.get("required_tools", [])),
            success_criteria=list(payload.get("success_criteria", [])),
            constraints=list(payload.get("constraints", [])),
            metadata=dict(payload.get("metadata", {})),
        )


@dataclass(slots=True)
class TraceEvent:
    """A small execution-trace event."""

    step: int
    event_type: str
    content: str
    timestamp: str = field(default_factory=utc_now_iso)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RunSummary:
    """Summary artifact for a τ²-style quipu_lab run."""

    run_id: str
    task_id: str
    status: str
    score: float
    summary: str
    used_tools: list[str] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = [
    "PolicyRule",
    "QuipuLabTask",
    "RunSummary",
    "ToolSpec",
    "TraceEvent",
    "utc_now_iso",
]
