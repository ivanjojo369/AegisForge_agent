"""Safe, local, synthetic three-source Mission Control integration example.

Run at repo root with PYTHONPATH=src:
  python scripts/nexus/demo_mission_read_adapters.py

No Nebius, Docker, Twitch, network, repository reads, or mutations to external
state. All demo state is in memory. No generated claims of real mission success.
"""
from __future__ import annotations
import json
from hashlib import sha256

from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_state import (
    MissionStatePolicy, MissionTransitionRule, NexusMissionState,
)
from aegisforge.nexus.mission_control.mission_coordinator import (
    MissionWorkflowStep, MissionCoordinationBlueprint, NexusMissionCoordinator,
)
from aegisforge.nexus.mission_control.mission_bridge import NexusMissionBridge
from aegisforge.nexus.mission_control.mission_read_adapters import NexusMissionReadAdapters
from aegisforge.nexus.lab.scenario_catalog import (
    NexusScenarioCatalog, ScenarioCase, ScenarioExpectation,
)
from aegisforge.nexus.proof.proof_graph import NexusProofGraph


def run_demo() -> dict:
    mission = "mission.demo"
    registry = NexusMissionRegistry()
    registry.register(mission, product="colossus", owner_id="team.demo",
                      objective_sha256=sha256(b"synthetic demonstration only").hexdigest(),
                      actor_id="team.demo", at_utc="2026-10-27T12:00:00Z")
    for i, component in enumerate(("lab", "mission_control", "proof"), 1):
        registry.assign_component(mission, component=component, actor_id="team.demo",
                                  at_utc=f"2026-10-27T12:00:{i:02d}Z")

    scenarios = NexusScenarioCatalog()
    scenarios.register(ScenarioCase(
        "scenario.synthetic", product="aegisgrid", category="functional",
        title="Safe synthetic energy case", description="Example data only",
        inputs={"sample": 1},
        expectations=(ScenarioExpectation("ready", "equals", True),),
    ))
    graph = NexusProofGraph(mission)
    graph.add_node("run", "run.synthetic", status="observed")

    policy = MissionStatePolicy(mission, "colossus", (MissionTransitionRule("ready"),))
    blueprint = MissionCoordinationBlueprint(mission, "colossus", "ready", (
        MissionWorkflowStep("step.mission", "mission_control", "summarize_mission"),
        MissionWorkflowStep("step.lab", "lab", "summarize", ("step.mission",)),
        MissionWorkflowStep("step.proof", "proof", "verify_integrity", ("step.lab",)),
    ))
    coordination = NexusMissionCoordinator(blueprint, NexusMissionState(policy)).preview(registry)
    adapter = NexusMissionReadAdapters(registry, scenarios=scenarios, proof_graph=graph)
    bridge = NexusMissionBridge(adapter.catalog).plan(coordination, registry)
    receipts = {}
    for step_id in ("step.mission", "step.lab", "step.proof"):
        receipt = adapter.inspect(bridge, step_id=step_id).to_dict()
        assert adapter.verify_receipt(receipt, bridge)
        receipts[step_id] = receipt["summary"]
    return {
        "mission_id": mission,
        "bridge_status": bridge.outcome,
        "read_only": True,
        "authorization_granted": False,
        "effects_executed": False,
        "summaries": receipts,
    }


if __name__ == "__main__":
    print(json.dumps(run_demo(), indent=2, ensure_ascii=False))
