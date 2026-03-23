from src.aegisforge.strategy.artifact_policy import ArtifactPolicy

def test_artifact_policy_prefers_strict_json_when_requested():
    decision = ArtifactPolicy().decide(
        artifact_required=True,
        task_type="artifact_generation",
        track="security",
        requested_format="json",
    )
    assert decision.required is True
    assert decision.artifact_kind == "json"
    assert decision.strict_format is True
