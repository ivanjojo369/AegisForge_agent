from src.aegisforge_eval.heldouts.generality import GeneralityAnalyzer

def test_generality_analyzer_combines_coverage_and_consistency():
    report = GeneralityAnalyzer().analyze(
        {"smoke": 1.0, "robustness": 0.8, "budget": 0.75, "transfer": 0.9}
    )
    assert report.suite_coverage == 1.0
    assert report.overall_generality_hint > 0.7
