from src.aegisforge.adapters.security.policy_bridge import SecurityPolicyBridge
from src.aegisforge.strategy.task_classifier import TaskClassification

def test_security_policy_bridge_merges_role_and_artifact_constraints():
    classification = TaskClassification(
        track_guess="security",
        task_type="artifact_generation",
        complexity="high",
        risk="high",
        artifact_expected=True,
        multi_step=True,
        tool_use_likely=False,
        heldout_like=True,
        tags=["artifact", "heldout-like"],
        reasons=["test"],
    )
    bundle = SecurityPolicyBridge().build(
        classification,
        artifact_required=True,
        requested_format="json",
    )
    assert "security" in bundle.tags
    assert "artifact" in bundle.tags
    assert bundle.role_policy.posture == "conservative"
    assert bundle.artifact_policy.artifact_kind == "json"
