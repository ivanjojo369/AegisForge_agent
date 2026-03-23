from src.aegisforge.orchestration.contracts import AdapterResult
from src.aegisforge.orchestration.recovery import RecoveryPolicy

def test_recovery_policy_retries_transient_failures():
    policy = RecoveryPolicy(max_retries=2)
    decision = policy.decide(
        attempt=1,
        result=AdapterResult(
            ok=False,
            response_text="",
            error_code="transient",
            error_message="Temporary backend issue.",
        ),
    )
    assert decision.retry is True
    assert decision.revised_tool_mode == "minimal"
