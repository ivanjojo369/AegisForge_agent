from src.aegisforge_eval.heldouts.degradation import DegradationAnalyzer

def test_degradation_analyzer_reports_drop():
    report = DegradationAnalyzer().analyze(
        baseline_results=[True, True, False, True],
        heldout_results=[True, False, False, False],
    )
    assert report.absolute_drop > 0
    assert report.baseline_success_rate > report.heldout_success_rate
