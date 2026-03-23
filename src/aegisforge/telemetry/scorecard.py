from __future__ import annotations
from dataclasses import dataclass, field
from .budget_stats import BudgetStatsCollector
from .episode_summary import EpisodeSummary

@dataclass(slots=True)
class Scorecard:
    task_id: str
    track: str
    status: str
    correctness_hint: float
    efficiency_hint: float
    robustness_hint: float
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "track": self.track,
            "status": self.status,
            "correctness_hint": self.correctness_hint,
            "efficiency_hint": self.efficiency_hint,
            "robustness_hint": self.robustness_hint,
            "notes": list(self.notes),
        }

class ScorecardBuilder:
    def build(self, summary: EpisodeSummary, budget_stats: BudgetStatsCollector | None = None) -> Scorecard:
        budget = budget_stats.summary() if budget_stats else {}
        correctness = 1.0 if summary.status == "completed" else 0.4
        efficiency = 1.0
        robustness = 1.0
        notes = []

        if budget.get("ever_near_limit"):
            efficiency -= 0.15
            notes.append("Episode operated near budget limits.")
        if budget.get("ever_hard_limit_hit"):
            efficiency -= 0.35
            robustness -= 0.2
            notes.append("Episode exceeded a hard budget boundary.")
        if summary.fallback_used:
            correctness -= 0.25
            robustness -= 0.2
            notes.append("Fallback path was required.")
        if summary.warning_count > 0:
            robustness -= min(0.3, summary.warning_count * 0.05)
            notes.append(f"Warnings observed: {summary.warning_count}.")
        if summary.failure_label not in {"none", ""}:
            correctness -= 0.1
            notes.append(f"Failure label observed: {summary.failure_label}.")

        return Scorecard(
            task_id=summary.task_id,
            track=summary.track,
            status=summary.status,
            correctness_hint=max(0.0, round(correctness, 3)),
            efficiency_hint=max(0.0, round(efficiency, 3)),
            robustness_hint=max(0.0, round(robustness, 3)),
            notes=notes,
        )
