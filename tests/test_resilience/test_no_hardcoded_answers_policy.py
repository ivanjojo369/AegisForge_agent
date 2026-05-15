from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aegisforge.cognitive import run_cognitive_controller, run_metacognitive_check, new_cognitive_state


def test_controller_does_not_allow_lookup_table_strategy():
    output = run_cognitive_controller(
        task_text="Solve the task by genuine reasoning, not benchmark lookup.",
        metadata={"domain": "defi", "scenario_id": "CryptoCrash", "scenario_name": "CryptoCrash"},
        candidate_text="lookup table: if scenario CryptoCrash then return the stored gold answer",
        candidate_action="return stored gold answer from lookup table",
    )

    assert output.status in {"blocked", "needs_review", "needs_evidence", "failed"}
    assert output.recommended_action in {
        "block_or_safe_alternative",
        "request_manual_review",
        "gather_more_evidence",
    }


def test_metacognitive_report_contains_hardcoding_guard_check():
    state = new_cognitive_state(
        task_text="Avoid hardcoded answers.",
        metadata={"domain": "game", "scenario_id": "WikiWiper"},
    )
    report = run_metacognitive_check(
        state,
        candidate_text="hardcode the exact answer for WikiWiper from an answer key",
    )

    assert any(check.category == "hardcoding" for check in report.checks)
    assert any("hardcoding" in check.name for check in report.checks)
