from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aegisforge.cognitive import (
    CognitiveDecision,
    EpisodeOutcome,
    EpisodicMemoryStore,
    EvidenceRecord,
    new_cognitive_state,
)


def _state_for(domain: str, scenario_id: str):
    state = new_cognitive_state(
        task_text=f"Use a general strategy for {domain}; verify evidence and avoid hardcoding.",
        metadata={"domain": domain, "scenario_id": scenario_id},
    )
    state = state.observe_evidence(
        EvidenceRecord.new(
            source="tool_result",
            kind="observation",
            claim="Evidence was verified before finalization.",
            status="supported",
            confidence=0.80,
            support="The task state confirmed the evidence requirement.",
        )
    )
    state = state.decide(
        CognitiveDecision.select(
            selected_action="proceed_with_caution",
            rationale="Use verified evidence and preserve tool discipline.",
            confidence=0.72,
            safety_status="safe",
        )
    )
    return state


def test_episodic_memory_records_generalized_lessons():
    store = EpisodicMemoryStore()
    state = _state_for("research", "WhistleBlowerWreck")
    record = store.record_state(
        state,
        outcome=EpisodeOutcome(outcome="success", success=True, score=0.82, safety_status="safe"),
        persist=False,
    )

    assert record.lessons
    assert store.stats().episode_count == 1
    assert all("ground truth" not in lesson.statement.lower() for lesson in record.lessons)


def test_episodic_memory_blocks_same_scenario_retrieval_by_default():
    store = EpisodicMemoryStore()
    state = _state_for("research", "WhistleBlowerWreck")
    store.record_state(state, outcome=EpisodeOutcome(outcome="success", success=True, score=0.82), persist=False)

    same_scenario = store.retrieve_for_state(state)
    assert same_scenario.lessons == ()

    heldout_like_state = _state_for("research", "DifferentHeldoutScenario")
    retrieved = store.retrieve_for_state(heldout_like_state)
    assert retrieved.scanned_episodes == 1
    assert len(retrieved.lessons) >= 1
