from __future__ import annotations
from dataclasses import dataclass, field
from typing import Iterable

@dataclass(slots=True)
class GeneralityReport:
    suite_coverage: float
    cross_suite_consistency: float
    overall_generality_hint: float
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "suite_coverage": self.suite_coverage,
            "cross_suite_consistency": self.cross_suite_consistency,
            "overall_generality_hint": self.overall_generality_hint,
            "notes": list(self.notes),
        }

class GeneralityAnalyzer:
    """Estimate whether performance looks broadly transferable across suites."""

    def analyze(self, suite_success_rates: dict[str, float]) -> GeneralityReport:
        if not suite_success_rates:
            return GeneralityReport(0.0, 0.0, 0.0, ["No suite data provided."])

        values = list(suite_success_rates.values())
        covered = [value for value in values if value > 0]
        suite_coverage = round(len(covered) / len(values), 4)

        spread = max(values) - min(values)
        consistency = round(max(0.0, 1.0 - spread), 4)

        overall = round((suite_coverage * 0.5) + (consistency * 0.5), 4)
        notes: list[str] = []
        if spread > 0.35:
            notes.append("Cross-suite variance is high.")
        if suite_coverage < 0.75:
            notes.append("Coverage across suites is still limited.")

        return GeneralityReport(
            suite_coverage=suite_coverage,
            cross_suite_consistency=consistency,
            overall_generality_hint=overall,
            notes=notes,
        )
