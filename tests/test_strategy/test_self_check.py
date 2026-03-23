from src.aegisforge.strategy.planner import ExecutionPlan, PlanStep
from src.aegisforge.strategy.self_check import SelfCheck


def test_self_check_flags_missing_artifact_structure():
    plan = ExecutionPlan(
        goal="Return a JSON artifact",
        steps=[PlanStep(name="finalize", description="Finish response")],
        tool_intent="minimal",
        risk_level="medium",
        estimated_budget=1000,
        requires_self_check=True,
    )
    result = SelfCheck().validate_response(
        task_text="Return a JSON object with the answer.",
        response="This is not JSON",
        plan=plan,
        metadata={"artifact_required": True},
    )
    assert result.passed is False
    assert any(issue.code == "artifact_missing" for issue in result.issues)
"""
This test verifies that the SelfCheck component correctly identifies when a response fails to meet the expected artifact structure. By providing a response that is not in JSON format when a JSON artifact is required, the test checks that the self-check validation fails and that an appropriate issue with the code "artifact_missing" is included in the results. This ensures that the self-check mechanism is effectively enforcing the requirements for artifact generation as specified in the execution plan.
"""
