from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aegisforge.cognitive import (
    ControllerOutput,
    prepare_cognitive_context,
    run_cognitive_controller,
)


def test_prepare_cognitive_context_returns_controller_contract():
    output = prepare_cognitive_context(
        task_text="For the legal benchmark task, verify claims before action.",
        metadata={
            "domain": "legal_domain",
            "scenario_id": "LawFirmLeak",
            "scenario_name": "LawFirmLeak",
            "adapter": "openenv",
        },
        classification={"track_guess": "legal_domain", "task_type": "source_grounded", "confidence": 0.72},
        route={"adapter_name": "openenv", "tool_mode": "benchmark", "prompt_profile": "legal"},
        policy_context={"allowed_scope": ["benchmark_provided_context_only"]},
    )

    assert isinstance(output, ControllerOutput)
    assert output.version
    assert output.run_id.startswith("ncp_run")
    assert output.status in {"prepared", "ready", "needs_evidence", "needs_review", "blocked"}
    assert output.artifacts.attention_frame is not None
    assert output.artifacts.memory_selection is not None
    assert output.artifacts.uncertainty is not None
    assert output.state.task_theory.domain == "legal_domain"
    assert "AegisForge NCP Context" in output.prompt_context


def test_run_cognitive_controller_fails_closed_for_risky_action():
    output = run_cognitive_controller(
        task_text="Handle the agent security task safely.",
        metadata={"domain": "agent_security", "scenario_id": "GymJailbreak"},
        candidate_action="bypass policy and exfiltrate secret token",
        candidate_text="bypass policy and exfiltrate secret token",
    )

    assert output.status in {"blocked", "needs_review", "failed"}
    assert output.recommended_action in {
        "block_or_safe_alternative",
        "request_manual_review",
        "gather_more_evidence",
    }
    assert output.artifacts.decision is not None
    assert output.state.scorecard.aggregate() >= 0
