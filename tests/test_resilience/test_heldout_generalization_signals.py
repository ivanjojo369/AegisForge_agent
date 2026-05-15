from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aegisforge.cognitive import (
    EpisodeOutcome,
    EpisodicMemoryStore,
    run_metacognitive_check,
    new_cognitive_state,
)


def test_episodic_lessons_generalize_to_different_scenario_not_same_scenario():
    store = EpisodicMemoryStore()
    train_state = new_cognitive_state(
        task_text="General strategy: verify evidence and tool contract.",
        metadata={"domain": "web", "scenario_id": "SearchGlitch"},
    )
    store.record_state(train_state, outcome=EpisodeOutcome(outcome="success", success=True, score=0.8), persist=False)

    same = store.retrieve_for_state(train_state)
    assert len(same.lessons) == 0

    heldout = new_cognitive_state(
        task_text="General strategy: verify evidence and tool contract.",
        metadata={"domain": "web", "scenario_id": "DifferentHeldoutWebTask"},
    )
    retrieved = store.retrieve_for_state(heldout)
    assert len(retrieved.lessons) >= 1


def test_metacognition_rewards_pattern_level_generalization():
    state = new_cognitive_state(
        task_text="Use pattern-level reasoning and prepare for held-out variations.",
        metadata={"domain": "computer_use", "scenario_id": "LnkLifter"},
        plan={"goal": "Use evidence and adapter contracts instead of scenario lookup."},
    )
    report = run_metacognitive_check(state, candidate_text="Use a general evidence-first strategy.")
    generalization_checks = [check for check in report.checks if check.category == "generalization"]

    assert generalization_checks
    assert generalization_checks[0].status in {"pass", "warn"}
