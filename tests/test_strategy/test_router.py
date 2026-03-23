from src.aegisforge.strategy.budget_guard import BudgetState
from src.aegisforge.strategy.router import TaskRouter
from src.aegisforge.strategy.task_classifier import TaskClassification


def test_router_switches_to_minimal_tool_mode_near_limit():
    classification = TaskClassification(
        track_guess="security",
        task_type="analysis",
        complexity="medium",
        risk="high",
        artifact_expected=False,
        multi_step=False,
        tool_use_likely=True,
        heldout_like=True,
        tags=["risk-aware"],
        reasons=["router test"],
    )
    decision = TaskRouter().decide(
        classification,
        budget_state=BudgetState(near_limit=True),
    )
    assert decision.track == "security"
    assert decision.tool_mode == "minimal"
