from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aegisforge.cognitive import prepare_cognitive_context


def test_ncp_preserves_router_adapter_profile():
    output = prepare_cognitive_context(
        task_text="Route the cybersecurity task to the correct adapter and keep benchmark scope.",
        metadata={
            "domain": "cybersecurity",
            "scenario_id": "StaticShipScam",
            "category": "cybersecurity",
            "selected_opponent": "cybergym",
            "adapter": "openenv",
        },
        classification={"track_guess": "cybersecurity", "task_type": "adversarial_evaluation"},
        route={"adapter_name": "openenv", "tool_mode": "controlled_benchmark", "prompt_profile": "cybersecurity"},
    )

    assert output.state.adapter_profile.adapter == "openenv"
    assert output.state.adapter_profile.domain == "cybersecurity"
    assert output.state.task_theory.selected_opponent == "cybergym"
    assert output.artifacts.attention_frame.routing_hints["adapter"] == "openenv"


def test_ncp_accepts_upstream_track_alias_metadata_without_replacing_it():
    output = prepare_cognitive_context(
        task_text="Preserve upstream identity while using local Sprint 4 metadata.",
        metadata={
            "domain": "business_process",
            "scenario_id": "SaleForceOneSpy",
            "selected_opponent": "crmarena",
            "benchmark": "AgentX-AgentBeats Phase 2 Sprint 4",
        },
        route={"adapter_name": "crmarena", "prompt_profile": "business_process"},
    )

    assert output.state.task_theory.domain == "business_process"
    assert output.state.task_theory.selected_opponent == "crmarena"
    assert output.state.adapter_profile.adapter == "crmarena"
