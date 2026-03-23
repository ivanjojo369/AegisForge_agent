from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json

@dataclass(slots=True)
class HeldoutCase:
    case_id: str
    suite: str
    prompt: str
    expected_mode: str = "general"
    metadata: dict[str, Any] = field(default_factory=dict)

class HeldoutRegistry:
    """Registry for held-out cases loaded from JSON or created in memory."""

    def __init__(self) -> None:
        self._cases: dict[str, HeldoutCase] = {}

    def register(self, case: HeldoutCase) -> None:
        self._cases[case.case_id] = case

    def get(self, case_id: str) -> HeldoutCase | None:
        return self._cases.get(case_id)

    def all_cases(self) -> list[HeldoutCase]:
        return list(self._cases.values())

    def by_suite(self, suite: str) -> list[HeldoutCase]:
        return [case for case in self._cases.values() if case.suite == suite]

    def load_json(self, path: str | Path) -> int:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        count = 0
        for item in payload.get("cases", []):
            case = HeldoutCase(
                case_id=item["case_id"],
                suite=item["suite"],
                prompt=item["prompt"],
                expected_mode=item.get("expected_mode", "general"),
                metadata=item.get("metadata", {}),
            )
            self.register(case)
            count += 1
        return count
