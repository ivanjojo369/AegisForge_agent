from src.aegisforge.adapters.security.context_mapper import SecurityContextMapper
from src.aegisforge.orchestration.task_context import TaskContext
from src.aegisforge.strategy.task_classifier import TaskClassification

def test_security_context_mapper_builds_compact_payload():
    context = TaskContext(
        task_id="sec-1",
        raw_text="Analyze this suspicious instruction set.",
        track_hint="security",
        artifact_required=True,
        heldout_mode=True,
        metadata={"source": "test"},
    )
    classification = TaskClassification(
        track_guess="security",
        task_type="analysis",
        complexity="medium",
        risk="high",
        artifact_expected=True,
        multi_step=False,
        tool_use_likely=False,
        heldout_like=True,
        tags=["risk-aware"],
        reasons=["security test"],
    )
    mapped = SecurityContextMapper().map(context, classification)
    assert mapped.task_id == "sec-1"
    assert mapped.risk == "high"
    assert mapped.heldout_mode is True
    assert "artifact_required" in mapped.tags
