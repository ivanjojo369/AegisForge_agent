from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aegisforge.cognitive import prepare_cognitive_context


def test_tau2_context_bridge_preserves_tickettwister_metadata():
    output = prepare_cognitive_context(
        task_text="Handle τ² TicketTwister task with communication/action checks.",
        metadata={
            "domain": "tau2",
            "scenario_id": "TicketTwister",
            "scenario_name": "TicketTwister",
            "selected_opponent": "tau2_agentbeats",
            "adapter": "tau2",
        },
        classification={"track_guess": "tau2", "task_type": "tool_sequence"},
        route={"adapter_name": "tau2", "tool_mode": "tau2_benchmark", "prompt_profile": "tau2"},
        payload={
            "task": {
                "task_id": "tau2-tickettwister-001",
                "required_tools": ["update_experiment_status", "allocate_resource", "communicate"],
                "success_criteria": ["emit required tool calls in expected action shape"],
            }
        },
    )

    assert output.state.task_theory.domain == "tau2"
    assert output.state.task_theory.scenario_id == "TicketTwister"
    assert output.state.adapter_profile.adapter == "tau2"
    assert "communicate" in output.state.task_theory.required_tools


def test_tau2_bridge_keeps_upstream_opponent_identity():
    output = prepare_cognitive_context(
        task_text="Preserve tau2 upstream identity while using Sprint 4 metadata.",
        metadata={"domain": "tau2", "scenario_id": "TicketTwister", "selected_opponent": "tau2_agentbeats"},
        route={"adapter_name": "tau2"},
    )

    assert output.state.task_theory.selected_opponent == "tau2_agentbeats"
    assert output.state.task_theory.scenario_family == "agentbeats_sprint4"
