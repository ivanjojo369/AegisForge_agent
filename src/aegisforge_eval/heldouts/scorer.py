from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass(slots=True)
class HeldoutScore:
    case_id: str
    success: bool
    correctness_hint: float
    robustness_hint: float
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "success": self.success,
            "correctness_hint": self.correctness_hint,
            "robustness_hint": self.robustness_hint,
            "notes": list(self.notes),
        }

class HeldoutScorer:
    """Very lightweight internal scorer for held-out experiments."""

    def score(
        self,
        *,
        case_id: str,
        status: str,
        warnings: list[str] | None = None,
        fallback_used: bool = False,
    ) -> HeldoutScore:
        warnings = list(warnings or [])
        success = status == "completed" and not fallback_used
        correctness = 1.0 if success else 0.55
        robustness = 1.0

        notes: list[str] = []
        if warnings:
            robustness -= min(0.3, len(warnings) * 0.05)
            notes.append(f"Warnings observed: {len(warnings)}.")
        if fallback_used:
            correctness -= 0.2
            robustness -= 0.15
            notes.append("Fallback path was used during evaluation.")

        return HeldoutScore(
            case_id=case_id,
            success=success,
            correctness_hint=max(0.0, round(correctness, 3)),
            robustness_hint=max(0.0, round(robustness, 3)),
            notes=notes,
        )
