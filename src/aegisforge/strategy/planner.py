from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .budget_guard import BudgetGuard, BudgetLimits
from .task_classifier import TaskClassification
from .track_profiles import TrackProfile, get_track_profile


@dataclass(slots=True)
class PlanStep:
    name: str
    description: str
    requires_tool: bool = False
    required_confidence: str = "medium"


@dataclass(slots=True)
class ExecutionPlan:
    goal: str
    steps: list[PlanStep]
    tool_intent: str
    risk_level: str
    estimated_budget: int
    requires_self_check: bool = True
    notes: list[str] = field(default_factory=list)


class TaskPlanner:
    """Create a compact execution plan before invoking adapters or tools."""

    def __init__(self, budget_guard: BudgetGuard | None = None) -> None:
        self.budget_guard = budget_guard or BudgetGuard()

    def build_plan(
        self,
        task_text: str,
        classification: TaskClassification,
        *,
        metadata: Mapping[str, Any] | None = None,
        track_profile: TrackProfile | None = None,
        budget_limits: BudgetLimits | None = None,
    ) -> ExecutionPlan:
        metadata = dict(metadata or {})
        profile = track_profile or get_track_profile(classification.track_guess)

        steps: list[PlanStep] = [
            PlanStep(
                name="understand_task",
                description="Normalize the task and extract the required output contract.",
                requires_tool=False,
                required_confidence="high",
            )
        ]

        if classification.tool_use_likely:
            steps.append(
                PlanStep(
                    name="collect_evidence",
                    description="Gather only the evidence needed to answer or act.",
                    requires_tool=True,
                    required_confidence="medium",
                )
            )

        if classification.multi_step or classification.complexity in {"medium", "high"}:
            steps.append(
                PlanStep(
                    name="synthesize",
                    description="Combine findings into a coherent, benchmark-safe decision.",
                    requires_tool=False,
                    required_confidence="high",
                )
            )

        steps.append(
            PlanStep(
                name="finalize",
                description="Prepare the final response in the required output format.",
                requires_tool=False,
                required_confidence="high",
            )
        )

        estimated_budget = self.budget_guard.estimate_plan_cost(
            step_count=len(steps),
            classification=classification,
            limits=budget_limits or profile.budget_limits,
        )

        notes = []
        if classification.heldout_like:
            notes.append("Use conservative reasoning; avoid brittle shortcuts.")
        if classification.risk in {"medium", "high"}:
            notes.append("Apply stricter self-check and policy-sensitive phrasing.")
        if metadata.get("artifact_required"):
            notes.append("Prefer structured output and explicit completeness checks.")

        return ExecutionPlan(
            goal=self._extract_goal(task_text),
            steps=steps,
            tool_intent="selective" if classification.tool_use_likely else "minimal",
            risk_level=classification.risk,
            estimated_budget=estimated_budget,
            requires_self_check=profile.self_check_policy != "minimal",
            notes=notes,
        )

    @staticmethod
    def _extract_goal(task_text: str) -> str:
        line = task_text.strip().splitlines()[0] if task_text.strip() else ""
        return line[:180] or "Resolve the current benchmark task safely and efficiently."
