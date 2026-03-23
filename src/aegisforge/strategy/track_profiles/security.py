from ..budget_guard import BudgetLimits
from .profile import TrackProfile


SECURITY_PROFILE = TrackProfile(
    name="security",
    default_prompt="tracks/security.md",
    budget_limits=BudgetLimits(
        max_llm_calls=4,
        max_context_chars=16000,
        max_plan_steps=5,
        soft_token_budget=5500,
        hard_token_budget=8000,
    ),
    self_check_policy="strict",
    routing_bias="risk-first",
)
