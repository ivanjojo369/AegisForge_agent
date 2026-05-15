from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aegisforge.cognitive import EvidenceRecord, new_cognitive_state


def test_scorecard_contains_sprint4_quality_fields():
    state = new_cognitive_state(
        task_text="Score this state using generality, safety, evidence and traceability.",
        metadata={"domain": "multi_agent", "scenario_id": "BidBot"},
    )
    payload = state.scorecard.to_dict()

    for key in [
        "generality",
        "evidence_grounding",
        "uncertainty_handling",
        "safety",
        "cost_efficiency",
        "traceability",
        "adapter_fit",
        "innovation",
        "aggregate",
    ]:
        assert key in payload


def test_scorecard_updates_after_evidence():
    state = new_cognitive_state(
        task_text="Ground the task with evidence.",
        metadata={"domain": "healthcare", "scenario_id": "DocuDoctor"},
    )
    before = state.scorecard.aggregate()
    state = state.observe_evidence(
        EvidenceRecord.new(
            source="tool_result",
            kind="observation",
            claim="Evidence supports the healthcare task boundary.",
            status="supported",
            confidence=0.82,
            support="The tool output supports the boundary.",
        )
    )
    after = state.scorecard.aggregate()

    assert after >= before
