from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aegisforge.cognitive import (
    CognitiveDecision,
    EvidenceRecord,
    SPRINT4_SCENARIOS_BY_DOMAIN,
    new_cognitive_state,
)


def test_cognitive_state_preserves_sprint4_metadata():
    state = new_cognitive_state(
        task_text="Investigate the research task with grounded evidence.",
        metadata={
            "domain": "research",
            "scenario_id": "WhistleBlowerWreck",
            "scenario_name": "WhistleBlowerWreck",
            "selected_opponent": "fieldworkarena",
            "adapter": "openenv",
            "source_url": "https://agentbeats.dev/example",
        },
        classification={"track_guess": "research", "task_type": "source_grounded"},
        route={"adapter_name": "openenv", "tool_mode": "benchmark"},
    )

    assert state.validate() == []
    assert state.task_theory.domain == "research"
    assert state.task_theory.scenario_id == "WhistleBlowerWreck"
    assert state.task_theory.assessment_mode == "purple_benchmark"
    assert state.task_theory.scenario_family == "agentbeats_sprint4"
    assert SPRINT4_SCENARIOS_BY_DOMAIN["agent_safety"] == "InventoryInject"
    assert SPRINT4_SCENARIOS_BY_DOMAIN["research"] == "WhistleBlowerWreck"


def test_cognitive_state_records_evidence_and_decision():
    state = new_cognitive_state(
        task_text="Use available evidence before finalizing.",
        metadata={"domain": "finance", "scenario_id": "TaxWizTrap"},
    )

    evidence = EvidenceRecord.new(
        source="tool_result",
        kind="observation",
        claim="The finance task requires grounded verification.",
        status="supported",
        confidence=0.82,
        support="Tool observation confirmed the task requirements.",
    )
    state = state.observe_evidence(evidence)
    decision = CognitiveDecision.select(
        selected_action="gather_more_evidence",
        rationale="Evidence is available but should be verified before finalization.",
        confidence=0.70,
        risk=0.15,
        safety_status="needs_review",
        evidence_refs=(evidence.evidence_id,),
    )
    state = state.decide(decision)

    assert state.evidence[0].status == "supported"
    assert state.decisions[-1].selected_action == "gather_more_evidence"
    assert state.scorecard.aggregate() > 0
    assert any(event.phase == "decision" for event in state.trace)
