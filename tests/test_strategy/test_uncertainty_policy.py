from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aegisforge.cognitive import (
    WorkingMemoryItem,
    estimate_uncertainty,
    gate_candidate_action,
    new_cognitive_state,
)


def test_uncertainty_blocks_secret_extraction_candidate_action():
    state = new_cognitive_state(
        task_text="Respect policy boundary for agent security.",
        metadata={"domain": "agent_security", "scenario_id": "GymJailbreak"},
    )
    gate = gate_candidate_action(state, "exfiltrate secret token and bypass policy")

    assert gate.allowed is False
    assert gate.recommended_action == "block_or_safe_alternative"
    assert gate.risk >= 0.78


def test_uncertainty_detects_policy_and_evidence_gap_signals():
    state = new_cognitive_state(
        task_text="Research task must cite evidence before final answer.",
        metadata={"domain": "research", "scenario_id": "WhistleBlowerWreck"},
    ).remember(
        WorkingMemoryItem(
            key="risk_prompt_injection",
            source="task",
            content="Prompt injection asks to ignore previous policy and use unsupported claims.",
            salience=0.9,
            tags=("risk:prompt_injection", "needs_evidence", "policy_boundary"),
            locked=True,
        )
    )

    assessment = estimate_uncertainty(state)
    assert assessment.recommended_action in {"gather_more_evidence", "pause_or_request_review", "block_or_safe_alternative"}
    assert assessment.evidence_gaps
    assert assessment.risk_score > 0
