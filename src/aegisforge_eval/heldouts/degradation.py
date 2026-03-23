from __future__ import annotations
from dataclasses import dataclass, field
from typing import Iterable

@dataclass(slots=True)
class DegradationReport:
    baseline_success_rate: float
    heldout_success_rate: float
    absolute_drop: float
    relative_drop: float
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "baseline_success_rate": self.baseline_success_rate,
            "heldout_success_rate": self.heldout_success_rate,
            "absolute_drop": self.absolute_drop,
            "relative_drop": self.relative_drop,
            "notes": list(self.notes),
        }

class DegradationAnalyzer:
    """Compare in-distribution and held-out performance at a high level."""

    def analyze(self, baseline_results: Iterable[bool], heldout_results: Iterable[bool]) -> DegradationReport:
        baseline = list(baseline_results)
        heldout = list(heldout_results)

        baseline_rate = self._success_rate(baseline)
        heldout_rate = self._success_rate(heldout)
        absolute_drop = round(baseline_rate - heldout_rate, 4)
        relative_drop = round((absolute_drop / baseline_rate), 4) if baseline_rate > 0 else 0.0

        notes: list[str] = []
        if absolute_drop > 0.2:
            notes.append("Held-out degradation exceeds the 20% attention threshold.")
        if heldout_rate == 0.0 and heldout:
            notes.append("No held-out cases succeeded.")

        return DegradationReport(
            baseline_success_rate=baseline_rate,
            heldout_success_rate=heldout_rate,
            absolute_drop=absolute_drop,
            relative_drop=relative_drop,
            notes=notes,
        )

    @staticmethod
    def _success_rate(results: list[bool]) -> float:
        if not results:
            return 0.0
        return round(sum(1 for item in results if item) / len(results), 4)
