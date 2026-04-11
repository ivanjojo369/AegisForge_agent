from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class RewardConfig:
    """
    Generic shaping config for OmniBench Aegis environments.
    Domains can use this directly or override values per task family.
    """

    progress_scale: float = 0.05
    success_bonus: float = 4.0
    efficiency_bonus_max: float = 1.0
    safety_violation_penalty: float = 2.0
    budget_overrun_penalty: float = 1.0
    consistency_bonus: float = 0.5
    min_total: float = -10.0
    max_total: float = 10.0


@dataclass
class RewardBreakdown:
    """
    Detailed reward report that is easy to log, inspect, and serialize.
    """

    base: float = 0.0
    progress: float = 0.0
    success_bonus: float = 0.0
    efficiency_bonus: float = 0.0
    safety_penalty: float = 0.0
    budget_penalty: float = 0.0
    consistency_bonus: float = 0.0
    custom: dict[str, float] = field(default_factory=dict)

    @property
    def total(self) -> float:
        return (
            float(self.base)
            + float(self.progress)
            + float(self.success_bonus)
            + float(self.efficiency_bonus)
            - float(self.safety_penalty)
            - float(self.budget_penalty)
            + float(self.consistency_bonus)
            + sum(float(v) for v in self.custom.values())
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "base": self.base,
            "progress": self.progress,
            "success_bonus": self.success_bonus,
            "efficiency_bonus": self.efficiency_bonus,
            "safety_penalty": self.safety_penalty,
            "budget_penalty": self.budget_penalty,
            "consistency_bonus": self.consistency_bonus,
            "custom": dict(self.custom),
            "total": self.total,
        }


def progress_reward(progress_delta: int | float, *, scale: float = 0.05) -> float:
    return float(progress_delta) * float(scale)


def efficiency_bonus(
    *,
    success: bool,
    step_count: int | None,
    max_steps: int | None,
    max_bonus: float = 1.0,
) -> float:
    """
    Rewards earlier completion when success=True.
    """
    if not success or step_count is None or max_steps is None or max_steps <= 0:
        return 0.0

    remaining = max(0, int(max_steps) - int(step_count))
    ratio = remaining / float(max_steps)
    return float(max_bonus) * ratio


def safety_penalty(*, safety_ok: bool, penalty: float = 2.0) -> float:
    return 0.0 if safety_ok else float(penalty)


def budget_penalty(*, budget_ok: bool, penalty: float = 1.0) -> float:
    return 0.0 if budget_ok else float(penalty)


def consistency_bonus(*, consistent: bool, bonus: float = 0.5) -> float:
    return float(bonus) if consistent else 0.0


def build_reward(
    *,
    base_reward: float = 0.0,
    progress_delta: int | float = 0,
    success: bool = False,
    step_count: int | None = None,
    max_steps: int | None = None,
    safety_ok: bool = True,
    budget_ok: bool = True,
    consistent: bool = True,
    custom: dict[str, float] | None = None,
    config: RewardConfig | None = None,
) -> RewardBreakdown:
    """
    Build a reward in a reusable, inspectable way.

    This is meant to be domain-agnostic:
    - progress_delta: how much meaningful progress happened this step
    - success: whether the episode is completed successfully
    - safety_ok: whether no safety/policy issue occurred
    - budget_ok: whether resource/query/tool budgets are still respected
    - consistent: whether the action/answer stayed coherent with the task
    """
    cfg = config or RewardConfig()

    breakdown = RewardBreakdown(
        base=float(base_reward),
        progress=progress_reward(progress_delta, scale=cfg.progress_scale),
        success_bonus=float(cfg.success_bonus) if success else 0.0,
        efficiency_bonus=efficiency_bonus(
            success=success,
            step_count=step_count,
            max_steps=max_steps,
            max_bonus=cfg.efficiency_bonus_max,
        ),
        safety_penalty=safety_penalty(
            safety_ok=safety_ok,
            penalty=cfg.safety_violation_penalty,
        ),
        budget_penalty=budget_penalty(
            budget_ok=budget_ok,
            penalty=cfg.budget_overrun_penalty,
        ),
        consistency_bonus=consistency_bonus(
            consistent=consistent,
            bonus=cfg.consistency_bonus,
        ),
        custom=dict(custom or {}),
    )

    clipped_total = clamp(breakdown.total, cfg.min_total, cfg.max_total)

    if clipped_total != breakdown.total:
        # Preserve the fact that clipping happened in a transparent way
        breakdown.custom["clipping_adjustment"] = clipped_total - breakdown.total

    return breakdown


def finalize_reward(
    *,
    base_reward: float = 0.0,
    progress_delta: int | float = 0,
    success: bool = False,
    step_count: int | None = None,
    max_steps: int | None = None,
    safety_ok: bool = True,
    budget_ok: bool = True,
    consistent: bool = True,
    custom: dict[str, float] | None = None,
    config: RewardConfig | None = None,
) -> float:
    """
    Convenience helper when a domain only wants the scalar final reward.
    """
    breakdown = build_reward(
        base_reward=base_reward,
        progress_delta=progress_delta,
        success=success,
        step_count=step_count,
        max_steps=max_steps,
        safety_ok=safety_ok,
        budget_ok=budget_ok,
        consistent=consistent,
        custom=custom,
        config=config,
    )
    return float((config or RewardConfig()).min_total if breakdown.total < (config or RewardConfig()).min_total else (
        (config or RewardConfig()).max_total if breakdown.total > (config or RewardConfig()).max_total else breakdown.total
    ))
