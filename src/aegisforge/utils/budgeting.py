from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..strategy.budget_guard import BudgetLimits, BudgetState, BudgetStepUsage


def make_budget_limits(
    *,
    max_llm_calls: int = 4,
    max_context_chars: int = 18000,
    max_plan_steps: int = 6,
    soft_token_budget: int = 6000,
    hard_token_budget: int = 9000,
) -> BudgetLimits:
    return BudgetLimits(
        max_llm_calls=max_llm_calls,
        max_context_chars=max_context_chars,
        max_plan_steps=max_plan_steps,
        soft_token_budget=soft_token_budget,
        hard_token_budget=hard_token_budget,
    )


def make_step_usage(
    *,
    llm_calls: int = 0,
    additional_context_chars: int = 0,
    additional_plan_steps: int = 0,
    additional_tokens: int = 0,
) -> BudgetStepUsage:
    return BudgetStepUsage(
        llm_calls=llm_calls,
        additional_context_chars=additional_context_chars,
        additional_plan_steps=additional_plan_steps,
        additional_tokens=additional_tokens,
    )


def state_as_dict(state: BudgetState) -> dict[str, Any]:
    return asdict(state)


def limits_as_dict(limits: BudgetLimits) -> dict[str, Any]:
    return asdict(limits)


def budget_headroom(state: BudgetState, limits: BudgetLimits) -> dict[str, int]:
    return {
        "llm_calls_remaining": max(0, limits.max_llm_calls - state.llm_calls_used),
        "context_chars_remaining": max(0, limits.max_context_chars - state.context_chars),
        "plan_steps_remaining": max(0, limits.max_plan_steps - state.plan_steps_used),
        "soft_tokens_remaining": max(0, limits.soft_token_budget - state.estimated_tokens_used),
        "hard_tokens_remaining": max(0, limits.hard_token_budget - state.estimated_tokens_used),
    }


def is_budget_tight(state: BudgetState) -> bool:
    return bool(state.near_limit or state.hard_limit_hit or state.compress_now)
