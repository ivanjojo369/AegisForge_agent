from ..budget_guard import BudgetLimits
from .profile import TrackProfile


OPENENV_PROFILE = TrackProfile(
    name="openenv",
    default_prompt="tracks/openenv.md",
    budget_limits=BudgetLimits(
        max_llm_calls=4,
        max_context_chars=18000,
        max_plan_steps=6,
        soft_token_budget=6500,
        hard_token_budget=9000,
    ),
    self_check_policy="standard",
    routing_bias="tool-guided",
)
