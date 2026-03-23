from src.aegisforge.strategy.budget_guard import BudgetGuard, BudgetLimits, BudgetStepUsage


def test_budget_guard_detects_hard_limit_hit():
    guard = BudgetGuard()
    limits = BudgetLimits(max_llm_calls=2, hard_token_budget=100)
    state = guard.init_budget(initial_context="", limits=limits)

    guard.update_budget(
        state,
        BudgetStepUsage(llm_calls=3, additional_tokens=150),
        limits=limits,
    )
    assert state.hard_limit_hit is True
    assert guard.should_abort_or_finalize(state) is True
"""
This test verifies that the BudgetGuard correctly identifies when the hard limits for LLM calls and token usage have been exceeded. By initializing a budget with specific limits and then updating it with usage that exceeds those limits, the test checks that the hard_limit_hit flag is set to True and that the guard indicates that the agent should abort or finalize its current plan. This ensures that the budgeting mechanism is functioning as intended to prevent excessive resource usage.
"""
