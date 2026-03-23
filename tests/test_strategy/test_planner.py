from src.aegisforge.strategy.planner import TaskPlanner
from src.aegisforge.strategy.task_classifier import TaskClassification


def test_planner_adds_collect_step_for_tool_tasks():
    classification = TaskClassification(
        track_guess="openenv",
        task_type="tool_use",
        complexity="medium",
        risk="low",
        artifact_expected=False,
        multi_step=True,
        tool_use_likely=True,
        heldout_like=False,
        tags=["tool-use"],
        reasons=["tool test"],
    )
    plan = TaskPlanner().build_plan(
        "Use table lookup to inspect the current task and produce a final answer.",
        classification,
    )

    step_names = [step.name for step in plan.steps]
    assert "collect_evidence" in step_names
    assert plan.tool_intent == "selective"
"""
This test verifies that the TaskPlanner correctly adds a "collect_evidence" step to the plan when the task classification indicates that tool use is likely. By providing a classification that suggests a tool use task, the test checks that the resulting plan includes the appropriate step for collecting evidence before producing a final answer. Additionally, it confirms that the tool intent for the plan is set to "selective," which is consistent with the expectation for tasks that may require tool use but are not guaranteed to need it.
"""
