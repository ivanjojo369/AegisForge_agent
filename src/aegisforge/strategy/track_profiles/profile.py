from __future__ import annotations

from dataclasses import dataclass

from ..budget_guard import BudgetLimits


@dataclass(frozen=True, slots=True)
class TrackProfile:
    name: str
    default_prompt: str
    budget_limits: BudgetLimits
    self_check_policy: str
    routing_bias: str
