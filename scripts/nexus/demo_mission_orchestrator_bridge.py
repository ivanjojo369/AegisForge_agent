"""Standalone, purely offline demo: Mission Control ↔ original Orchestrator.

The allow-all authorizer in THIS SYNTHETIC DEMO must NEVER be used in production.
No network, tool execution, files, Docker, mission state transitions or tokens.
"""
from __future__ import annotations

from hashlib import sha256
import json

from aegisforge.nexus.nebius_client import NebiusReply
from aegisforge.nexus.planner import NexusPlanner
from aegisforge.nexus.executor import NexusExecutor
from aegisforge.nexus.orchestrator import NexusOrchestrator
from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_service import NexusMissionService
from aegisforge.nexus.mission_control.mission_orchestrator_bridge import (
    NexusMissionOrchestratorBridge, verify_bridge_receipt,
)
from aegisforge.nexus.proof.proof_graph import NexusProofGraph


class OfflineClient:
    def chat(self, messages, *, max_tokens=512, temperature=0.2):
        content = json.dumps({
            "goal": "Inspect an offline sample",
            "steps": [
                {"id": "s1", "kind": "reason", "tool": None,
                 "title": "Prepare", "description": "Prepare a synthetic observation",
                 "depends_on": [], "success_criteria": "Observation prepared"},
                {"id": "s2", "kind": "verify", "tool": None,
                 "title": "Verify", "description": "Assess the observation",
                 "depends_on": ["s1"], "success_criteria": "Assessment recorded"},
            ], "assumptions": [], "risks": [],
        })
        return NebiusReply(content=content, model="offline-fixture", request_id="fixture")


def main():
    actor, mission = "demo.actor", "mission.demo"
    # All-True is ONLY for this local fixture. Production requires real host IAM.
    service = NexusMissionService(NexusMissionRegistry(),
        authorizer=lambda actor_id, action, mission_id, registry_sha, request: True)
    service.create_mission(
        mission, product="colossus", owner_id="demo.owner",
        objective_sha256=sha256(b"offline fixture only").hexdigest(),
        actor_id=actor,
        expected_registry_sha256=service.registry_version(actor_id=actor))
    orchestrator = NexusOrchestrator(NexusPlanner(OfflineClient()), NexusExecutor())
    bridge = NexusMissionOrchestratorBridge(service, orchestrator)
    prepared = orchestrator.prepare("offline fixture only")
    anchor = service.registry_version(actor_id=actor)
    bound = bridge.bind_prepared(mission, actor_id=actor, prepared=prepared,
                                 expected_registry_sha256=anchor).to_dict()
    preview = bridge.preview(bound["session_id"], actor_id=actor).to_dict()
    outcome = bridge.run_offline(bound["session_id"], actor_id=actor).to_dict()
    proof = bridge.proof_snapshot(bound["session_id"], actor_id=actor)
    linked = bridge.link_evidence(bound["session_id"], actor_id=actor,
                                  expected_registry_sha256=anchor)
    assert all(verify_bridge_receipt(r) for r in (bound, preview, outcome))
    assert NexusProofGraph.verify_snapshot(proof)
    assert linked["status"] == "draft"
    print(json.dumps({
        "schema": "colossus.orchestrator_bridge.demo.v1",
        "mission_id": mission,
        "bound": bound["phase"], "previewed": preview["phase"],
        "completed": outcome["phase"],
        "tool_calls": outcome["tools_invoked"],
        "verification_status": outcome["verification_status"],
        "evidence_events": outcome["audit_event_count"],
        "evidence_linked": linked["reference_count"] == 1,
        "proof_graph_valid": True,
        "mission_status": linked["status"],
        "authorization_granted": False,
        "effects_executed": False,
        "external_api_calls": 0,
    }, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
