from src.aegisforge_eval.heldouts.cost_guard import CostGuard

def test_cost_guard_flags_limit_exceedance():
    result = CostGuard(max_llm_calls=4, max_estimated_tokens=1000).evaluate(
        llm_calls_used=5,
        estimated_tokens_used=1200,
    )
    assert result.within_limits is False
    assert len(result.warnings) == 2
