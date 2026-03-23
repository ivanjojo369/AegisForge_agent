from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(slots=True)
class AdapterRequest:
    """Normalized request sent from the orchestration layer to an adapter."""

    track: str
    task_text: str
    prompt_profile: str
    policy_profile: str
    tool_mode: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AdapterResult:
    """Result returned by an adapter after execution."""

    ok: bool
    response_text: str
    artifacts: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None


@dataclass(slots=True)
class ExecutionEvent:
    """Small event object that can be forwarded to telemetry emitters."""

    name: str
    phase: str
    message: str
    payload: Mapping[str, Any] = field(default_factory=dict)
