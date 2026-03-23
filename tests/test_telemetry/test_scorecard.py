from src.aegisforge.telemetry.episode_summary import EpisodeSummary
from src.aegisforge.telemetry.scorecard import ScorecardBuilder

def test_scorecard_builder_penalizes_fallback():
    summary = EpisodeSummary(
        task_id="t1",
        track="openenv",
        status="completed",
        attempt_count=1,
        warning_count=1,
        fallback_used=True,
        failure_label="none",
    )
    scorecard = ScorecardBuilder().build(summary)
    assert scorecard.correctness_hint < 1.0
    assert scorecard.robustness_hint < 1.0
