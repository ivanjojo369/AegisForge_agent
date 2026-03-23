from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass(slots=True)
class TraceStep:
    name: str
    phase: str
    message: str
    ok: bool = True
    payload: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class TraceArtifact:
    name: str
    kind: str
    path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class EpisodeTrace:
    task_id: str
    track: str
    status: str
    steps: list[TraceStep] = field(default_factory=list)
    artifacts: list[TraceArtifact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def add_step(self, step: TraceStep) -> None:
        self.steps.append(step)

    def add_artifact(self, artifact: TraceArtifact) -> None:
        self.artifacts.append(artifact)

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "track": self.track,
            "status": self.status,
            "steps": [{"name": s.name, "phase": s.phase, "message": s.message, "ok": s.ok, "payload": s.payload} for s in self.steps],
            "artifacts": [{"name": a.name, "kind": a.kind, "path": a.path, "metadata": a.metadata} for a in self.artifacts],
            "warnings": list(self.warnings),
            "tags": list(self.tags),
        }
