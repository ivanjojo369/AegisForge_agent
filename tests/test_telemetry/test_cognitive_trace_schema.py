from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aegisforge.cognitive import prepare_cognitive_context


def test_cognitive_trace_events_are_serializable_and_phase_labeled():
    output = prepare_cognitive_context(
        task_text="Trace every major NCP phase.",
        metadata={"domain": "software_testing", "scenario_id": "CodeReviewRuse"},
    )

    state_dict = output.state.to_dict()
    trace = state_dict["trace"]

    assert trace
    assert all("phase" in event for event in trace)
    assert all("message" in event for event in trace)
    assert all("severity" in event for event in trace)
    assert any(event["phase"] in {"attention", "working_memory", "evidence", "uncertainty"} for event in trace)


def test_controller_steps_are_serializable():
    output = prepare_cognitive_context(
        task_text="Prepare controller output with step telemetry.",
        metadata={"domain": "coding", "scenario_id": "DevContainerDoom"},
    )

    payload = output.to_dict()
    assert payload["steps"]
    assert all("phase" in step and "status" in step for step in payload["steps"])
    assert payload["metadata"]["controller_status"] == output.status
