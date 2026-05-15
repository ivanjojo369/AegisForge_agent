from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aegisforge.cognitive import run_metacognitive_check, new_cognitive_state


def test_metacognition_flags_hardcoding_lookup_risk():
    state = new_cognitive_state(
        task_text="Generalize across held-out tasks; do not hardcode.",
        metadata={"domain": "finance", "scenario_id": "TaxWizTrap", "scenario_name": "TaxWizTrap"},
    )
    report = run_metacognitive_check(
        state,
        candidate_text="Use a lookup table: if scenario TaxWizTrap return the exact benchmark answer.",
    )

    hardcoding_checks = [check for check in report.checks if check.category == "hardcoding"]
    assert hardcoding_checks
    assert hardcoding_checks[0].status in {"warn", "fail", "block"}
    assert report.recommended_action in {"run_self_check", "request_manual_review", "block_or_safe_alternative", "gather_more_evidence"}


def test_metacognition_can_emit_decision_for_state():
    state = new_cognitive_state(
        task_text="Verify evidence and proceed only when safe.",
        metadata={"domain": "web", "scenario_id": "SearchGlitch"},
    )
    report = run_metacognitive_check(state, candidate_text="Proceed with caution after evidence review.")
    decision = report.to_decision(turn_index=state.turn_index)

    assert decision.decision_id
    assert decision.selected_action in {
        "proceed",
        "proceed_with_caution",
        "gather_more_evidence",
        "run_self_check",
        "request_manual_review",
        "block_or_safe_alternative",
    }
