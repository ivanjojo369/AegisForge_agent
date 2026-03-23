from __future__ import annotations
from dataclasses import dataclass, field
from .failure_taxonomy import FailureTaxonomy

@dataclass(slots=True)
class EpisodeSummary:
    task_id: str
    track: str
    status: str
    attempt_count: int
    warning_count: int
    fallback_used: bool = False
    failure_label: str = "none"
    tags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "track": self.track,
            "status": self.status,
            "attempt_count": self.attempt_count,
            "warning_count": self.warning_count,
            "fallback_used": self.fallback_used,
            "failure_label": self.failure_label,
            "tags": list(self.tags),
        }

class EpisodeSummaryBuilder:
    def __init__(self, taxonomy: FailureTaxonomy | None = None) -> None:
        self.taxonomy = taxonomy or FailureTaxonomy()

    def build(self, *, task_id: str, track: str, status: str, attempt_count: int, warnings: list[str] | None = None, error_code: str | None = None, error_message: str | None = None, tags: list[str] | None = None) -> EpisodeSummary:
        warnings = list(warnings or [])
        fallback_used = any("fallback" in w.lower() for w in warnings)
        failure = self.taxonomy.classify(error_code=error_code, message=error_message)
        return EpisodeSummary(task_id=task_id, track=track, status=status, attempt_count=attempt_count, warning_count=len(warnings), fallback_used=fallback_used, failure_label=failure.value, tags=list(tags or []))
