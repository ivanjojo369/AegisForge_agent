from __future__ import annotations

from dataclasses import dataclass

from .task_classifier import TaskClassification


@dataclass(slots=True)
class BudgetLimits:
    max_llm_calls: int = 4
    max_context_chars: int = 18000
    max_plan_steps: int = 6
    soft_token_budget: int = 6000
    hard_token_budget: int = 9000


@dataclass(slots=True)
class BudgetState:
    llm_calls_used: int = 0
    context_chars: int = 0
    plan_steps_used: int = 0
    estimated_tokens_used: int = 0
    near_limit: bool = False
    hard_limit_hit: bool = False
    compress_now: bool = False


@dataclass(slots=True)
class BudgetStepUsage:
    llm_calls: int = 0
    additional_context_chars: int = 0
    additional_plan_steps: int = 0
    additional_tokens: int = 0


class BudgetGuard:
    """Track and enforce budget constraints for an episode."""

    def init_budget(self, *, initial_context: str = "", limits: BudgetLimits | None = None) -> BudgetState:
        limits = limits or BudgetLimits()
        context_chars = len(initial_context or "")
        state = BudgetState(context_chars=context_chars)
        state.compress_now = context_chars > int(limits.max_context_chars * 0.8)
        state.near_limit = state.compress_now
        return state

    def estimate_plan_cost(
        self,
        *,
        step_count: int,
        classification: TaskClassification,
        limits: BudgetLimits,
    ) -> int:
        base = step_count * 1000
        if classification.tool_use_likely:
            base += 1200
        if classification.complexity == "high":
            base += 1800
        if classification.risk == "high":
            base += 800
        return min(base, limits.hard_token_budget)

    def can_afford_step(self, state: BudgetState, step_usage: BudgetStepUsage, limits: BudgetLimits | None = None) -> bool:
        limits = limits or BudgetLimits()
        projected_llm_calls = state.llm_calls_used + step_usage.llm_calls
        projected_context = state.context_chars + step_usage.additional_context_chars
        projected_steps = state.plan_steps_used + step_usage.additional_plan_steps
        projected_tokens = state.estimated_tokens_used + step_usage.additional_tokens

        return all(
            [
                projected_llm_calls <= limits.max_llm_calls,
                projected_context <= limits.max_context_chars,
                projected_steps <= limits.max_plan_steps,
                projected_tokens <= limits.hard_token_budget,
            ]
        )

    def update_budget(
        self,
        state: BudgetState,
        usage: BudgetStepUsage,
        *,
        limits: BudgetLimits | None = None,
    ) -> BudgetState:
        limits = limits or BudgetLimits()

        state.llm_calls_used += usage.llm_calls
        state.context_chars += usage.additional_context_chars
        state.plan_steps_used += usage.additional_plan_steps
        state.estimated_tokens_used += usage.additional_tokens

        state.near_limit = (
            state.llm_calls_used >= max(limits.max_llm_calls - 1, 1)
            or state.context_chars >= int(limits.max_context_chars * 0.85)
            or state.estimated_tokens_used >= int(limits.soft_token_budget * 0.9)
        )
        state.hard_limit_hit = (
            state.llm_calls_used > limits.max_llm_calls
            or state.context_chars > limits.max_context_chars
            or state.plan_steps_used > limits.max_plan_steps
            or state.estimated_tokens_used > limits.hard_token_budget
        )
        state.compress_now = (
            state.context_chars >= int(limits.max_context_chars * 0.8)
            or state.estimated_tokens_used >= int(limits.soft_token_budget * 0.9)
        )
        return state

    def should_compress_context(self, state: BudgetState) -> bool:
        return state.compress_now

    def should_abort_or_finalize(self, state: BudgetState) -> bool:
        return state.hard_limit_hit or state.near_limit
