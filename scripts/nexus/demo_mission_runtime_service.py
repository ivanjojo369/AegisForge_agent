"""Synthetic no-network demonstration of Mission Runtime + Service.

WARNING: the inline authorizer is EXAMPLE-ONLY, not real authentication.
Use a host identity provider + permission checker in production.
"""
import json
from hashlib import sha256

from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_state import (
    MissionTransitionRule, MissionStatePolicy, NexusMissionState,
)
from aegisforge.nexus.mission_control.mission_coordinator import (
    MissionWorkflowStep, MissionCoordinationBlueprint, NexusMissionCoordinator,
)
from aegisforge.nexus.mission_control.mission_read_adapters import NexusMissionReadAdapters
from aegisforge.nexus.mission_control.mission_service import NexusMissionService
from aegisforge.nexus.mission_control.mission_runtime import verify_runtime_report


def main():
    registry = NexusMissionRegistry()
    # Demo-only permission check. Never accept claimed actor IDs like this in production.
    def demo_host_authorizer(actor, action, mission, anchor, request):
        return actor == "actor.demo" and action in {
            "list", "create", "read", "assign_component", "configure", "preview", "inspect",
        }

    service = NexusMissionService(registry, authorizer=demo_host_authorizer)
    mid = "mission.demo"
    service.create_mission(mid, product="colossus", owner_id="owner.demo",
                           objective_sha256=sha256(b"synthetic mission").hexdigest(),
                           actor_id="actor.demo",
                           expected_registry_sha256=service.registry_version(actor_id="actor.demo"))
    service.assign_component(mid, component="mission_control", actor_id="actor.demo",
                             expected_registry_sha256=service.registry_version(actor_id="actor.demo"))
    state = NexusMissionState(MissionStatePolicy(mid, "colossus", (
        MissionTransitionRule("ready"),
    )))
    blueprint = MissionCoordinationBlueprint(mid, "colossus", "ready", (
        MissionWorkflowStep("step.mission", "mission_control", "summarize_mission"),
    ))
    coordinator = NexusMissionCoordinator(blueprint, state)
    service.configure_workflow(mid, actor_id="actor.demo", coordinator=coordinator,
                               adapters=NexusMissionReadAdapters(registry),
                               expected_registry_sha256=service.registry_version(actor_id="actor.demo"))
    preview = service.preview_workflow(mid, actor_id="actor.demo").to_dict()
    report = service.inspect_workflow(mid, actor_id="actor.demo").to_dict()
    print(json.dumps({
        "mission_id": mid,
        "preview_outcome": preview["outcome"],
        "runtime_outcome": report["outcome"],
        "receipt_count": report["receipt_count"],
        "report_sha256": report["report_sha256"],
        "report_consistent": verify_runtime_report(report),
        "registry_status_after": registry.get(mid)["status"],
        "authorization_granted": report["authorization_granted"],
        "effects_executed": report["effects_executed"],
    }, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
