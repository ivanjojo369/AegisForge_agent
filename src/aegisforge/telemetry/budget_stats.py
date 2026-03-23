from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass(slots=True)
class BudgetSnapshot:
    llm_calls_used: int = 0
    estimated_tokens_used: int = 0
    context_chars: int = 0
    plan_steps_used: int = 0
    near_limit: bool = False
    hard_limit_hit: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "llm_calls_used": self.llm_calls_used,
            "estimated_tokens_used": self.estimated_tokens_used,
            "context_chars": self.context_chars,
            "plan_steps_used": self.plan_steps_used,
            "near_limit": self.near_limit,
            "hard_limit_hit": self.hard_limit_hit,
        }

@dataclass
class BudgetStatsCollector:
    history: list[BudgetSnapshot] = field(default_factory=list)

    def push(self, snapshot: BudgetSnapshot) -> None:
        self.history.append(snapshot)

    def latest(self) -> BudgetSnapshot | None:
        return self.history[-1] if self.history else None

    def summary(self) -> dict[str, Any]:
        if not self.history:
            return {"samples": 0, "max_llm_calls_used": 0, "max_estimated_tokens_used": 0, "ever_near_limit": False, "ever_hard_limit_hit": False}
        latest = self.latest()
        return {
            "samples": len(self.history),
            "max_llm_calls_used": max(x.llm_calls_used for x in self.history),
            "max_estimated_tokens_used": max(x.estimated_tokens_used for x in self.history),
            "max_context_chars": max(x.context_chars for x in self.history),
            "ever_near_limit": any(x.near_limit for x in self.history),
            "ever_hard_limit_hit": any(x.hard_limit_hit for x in self.history),
            "latest": latest.as_dict() if latest else {},
        }
