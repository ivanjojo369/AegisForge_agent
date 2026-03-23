from __future__ import annotations

from typing import Any

from config import AegisArenaEnvConfig, DEFAULT_CONFIG


def efficiency_penalty(step_count: int, max_steps: int, weight: float) -> float:
    if max_steps <= 0:
        return 0.0
    ratio = step_count / max_steps
    return round(-weight * ratio, 4)


def tool_overuse_penalty(query_count: int, free_queries: int, weight: float) -> float:
    extra_queries = max(query_count - free_queries, 0)
    return round(-weight * extra_queries, 4)


def invalid_action_penalty(weight: float) -> float:
    return round(-1.0 * weight, 4)


def robustness_bonus(
    *,
    budget_remaining: int,
    initial_budget: int,
    success: bool,
    weight: float,
) -> float:
    if not success or initial_budget <= 0:
        return 0.0
    ratio = budget_remaining / initial_budget
    return round(weight * ratio, 4)


def domain_correctness_reward(
    *,
    mission_type: str,
    success: bool,
) -> float:
    if not success:
        if mission_type == "game_ops":
            return -6.0
        if mission_type == "finance_ops":
            return -8.0
        if mission_type == "business_ops":
            return -8.0
        return -5.0

    if mission_type == "game_ops":
        return 15.0
    if mission_type == "finance_ops":
        return 18.0
    if mission_type == "business_ops":
        return 18.0
    return 12.0


def summarize_reward(
    *,
    mission_type: str,
    success: bool,
    step_count: int,
    max_steps: int,
    query_count: int,
    free_queries: int,
    invalid_action: bool,
    budget_remaining: int,
    initial_budget: int,
    config: AegisArenaEnvConfig | None = None,
) -> dict[str, Any]:
    cfg = config or DEFAULT_CONFIG
    weights = cfg.reward_weights

    correctness = domain_correctness_reward(
        mission_type=mission_type,
        success=success,
    ) * weights.correctness_weight

    eff_penalty = efficiency_penalty(
        step_count=step_count,
        max_steps=max_steps,
        weight=weights.efficiency_penalty_weight,
    )

    overuse_penalty = tool_overuse_penalty(
        query_count=query_count,
        free_queries=free_queries,
        weight=weights.tool_overuse_penalty_weight,
    )

    invalid_penalty = (
        invalid_action_penalty(weights.invalid_action_penalty_weight)
        if invalid_action
        else 0.0
    )

    bonus = robustness_bonus(
        budget_remaining=budget_remaining,
        initial_budget=initial_budget,
        success=success,
        weight=weights.robustness_bonus_weight,
    )

    total = round(
        correctness + eff_penalty + overuse_penalty + invalid_penalty + bonus,
        4,
    )

    return {
        "correctness": round(correctness, 4),
        "efficiency_penalty": eff_penalty,
        "tool_overuse_penalty": overuse_penalty,
        "invalid_action_penalty": invalid_penalty,
        "robustness_bonus": bonus,
        "total": total,
    }
