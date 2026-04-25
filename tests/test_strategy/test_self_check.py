from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from aegisforge.strategy.planner import ExecutionPlan, PlanStep
from aegisforge.strategy.self_check import SelfCheck


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

    codes = {issue.code for issue in result.issues}
    assert "artifact_missing" in codes or "thin_response" in codes
    assert result.severity in {"medium", "high"}
