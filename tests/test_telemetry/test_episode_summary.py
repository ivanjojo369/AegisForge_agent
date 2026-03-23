from src.aegisforge.telemetry.episode_summary import EpisodeSummaryBuilder

def test_episode_summary_builder_detects_fallback_warning():
    builder = EpisodeSummaryBuilder()
    summary = builder.build(
        task_id="task-1",
        track="security",
        status="completed",
        attempt_count=2,
        warnings=["Completed with fallback path."],
        error_code=None,
        error_message=None,
        tags=["heldout"],
    )
    assert summary.fallback_used is True
    assert summary.warning_count == 1
