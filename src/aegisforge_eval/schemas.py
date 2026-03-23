from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_WARN = "warn"
STATUS_SKIP = "skip"


@dataclass(slots=True)
class TrackConfig:
    """Generic configuration shared by all track evaluators."""

    name: str
    description: str = ""
    enabled: bool = True
    strict: bool = False
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TrackResult:
    """Normalized result for a single evaluation track."""

    track: str
    status: str
    summary: str
    score: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvaluationReport:
    """High-level report produced by ``aegisforge_eval.runner``."""

    agent_name: str
    run_id: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    track_results: list[TrackResult] = field(default_factory=list)
    totals: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["track_results"] = [item.to_dict() for item in self.track_results]
        return payload
