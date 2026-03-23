from src.aegisforge.telemetry.trace_schema import EpisodeTrace, TraceArtifact, TraceStep

def test_episode_trace_collects_steps_and_artifacts():
    trace = EpisodeTrace(task_id="t1", track="security", status="completed")
    trace.add_step(TraceStep(name="plan", phase="setup", message="Planned task"))
    trace.add_artifact(TraceArtifact(name="summary.json", kind="json", path="artifacts/summary.json"))

    payload = trace.as_dict()
    assert payload["task_id"] == "t1"
    assert len(payload["steps"]) == 1
    assert len(payload["artifacts"]) == 1
