from src.aegisforge.strategy.role_policy import RolePolicy

def test_role_policy_becomes_conservative_for_security_risk():
    decision = RolePolicy().decide(
        track="security",
        risk="high",
        task_type="analysis",
        heldout_like=True,
    )
    assert decision.role == "security_guardian"
    assert decision.posture == "conservative"
    assert "avoid brittle shortcuts or memorized patterns" in decision.constraints
