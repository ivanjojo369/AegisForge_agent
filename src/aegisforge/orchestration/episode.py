from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .task_context import TaskContext


class EpisodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(slots=True)
class EpisodeState:
    """Mutable episode state tracked by the orchestration layer."""

    context: TaskContext
    status: EpisodeStatus = EpisodeStatus.PENDING
    attempt: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    final_response: str | None = None
    final_artifacts: dict[str, Any] = field(default_factory=dict)

    def add_event(self, name: str, phase: str, message: str, **payload: Any) -> None:
        self.events.append(
            {
                "name": name,
                "phase": phase,
                "message": message,
                "payload": payload,
            }
        )

    def mark_completed(self, response_text: str, artifacts: dict[str, Any] | None = None) -> None:
        self.status = EpisodeStatus.COMPLETED
        self.final_response = response_text
        self.final_artifacts = dict(artifacts or {})

    def mark_failed(self, warning: str | None = None) -> None:
        self.status = EpisodeStatus.FAILED
        if warning:
            self.warnings.append(warning)
