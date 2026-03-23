from ..budget_guard import BudgetLimits
from .profile import TrackProfile


TAU2_PROFILE = TrackProfile(
    name="tau2",
    default_prompt="tracks/tau2.md",
    budget_limits=BudgetLimits(
        max_llm_calls=4,
        max_context_chars=17000,
        max_plan_steps=6,
        soft_token_budget=6000,
        hard_token_budget=8500,
    ),
    self_check_policy="standard",
    routing_bias="action-consistency",
)
