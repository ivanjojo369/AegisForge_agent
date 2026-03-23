from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class StateResetManager:
    """Track reset actions required between episodes.

    This class keeps the reset intent visible even if the repository is not
    yet using a full clean-state sandbox for every local run.
    """

    reset_actions: list[str] = field(default_factory=lambda: [
        "clear in-memory task cache",
        "reset transient adapter state",
        "drop previous episode artifacts from working memory",
    ])

    def describe(self) -> list[str]:
        return list(self.reset_actions)

    def build_reset_report(self) -> dict[str, Any]:
        return {
            "status": "ready",
            "actions": self.describe(),
        }
