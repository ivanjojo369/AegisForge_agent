from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(slots=True)
class TaskContext:
    """Internal normalized representation of the current task."""

    task_id: str
    raw_text: str
    track_hint: str | None = None
    artifact_required: bool = False
    heldout_mode: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskContextBuilder:
    """Build a ``TaskContext`` from A2A, harness, or benchmark payloads."""

    def from_payload(self, payload: Mapping[str, Any]) -> TaskContext:
        payload = dict(payload)
        text = self._extract_text(payload)
        return TaskContext(
            task_id=str(payload.get("task_id") or payload.get("id") or "unknown-task"),
            raw_text=text,
            track_hint=payload.get("track_hint") or payload.get("track"),
            artifact_required=bool(payload.get("artifact_required", False)),
            heldout_mode=bool(payload.get("heldout_mode", False)),
            metadata=payload,
        )

    def from_text(
        self,
        task_text: str,
        *,
        task_id: str = "adhoc-task",
        track_hint: str | None = None,
        artifact_required: bool = False,
        heldout_mode: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> TaskContext:
        return TaskContext(
            task_id=task_id,
            raw_text=task_text,
            track_hint=track_hint,
            artifact_required=artifact_required,
            heldout_mode=heldout_mode,
            metadata=dict(metadata or {}),
        )

    @staticmethod
    def _extract_text(payload: Mapping[str, Any]) -> str:
        for key in ("task_text", "text", "prompt", "input", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
