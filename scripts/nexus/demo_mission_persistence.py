"""Demonstrate local checkpoint/reload with synthetic data; never uses live repo.

Run from repository root with PYTHONPATH=src:
    python scripts/nexus/demo_mission_persistence.py
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_service import NexusMissionService
from aegisforge.nexus.mission_control.mission_persistence import NexusMissionPersistence


def main() -> None:
    # This demonstration authorizer is ONLY for a temporary synthetic fixture.
    # Production authorization must authenticate identities and scopes.
    def demo_authorizer(actor, action, mission_id, anchor, request):
        return actor == "operator.demo" and action in {
            "list", "create", "assign_component", "export_snapshot", "read"}

    with TemporaryDirectory(prefix="colossus-persistence-demo-") as temp:
        storage = NexusMissionPersistence(Path(temp))
        registry = NexusMissionRegistry()
        service = NexusMissionService(registry, authorizer=demo_authorizer)
        actor = "operator.demo"

        before = storage.checkpoint_service(service, actor_id=actor)
        version = service.registry_version(actor_id=actor)
        service.create_mission(
            "mission.demo", product="colossus", owner_id="owner.demo",
            objective_sha256=sha256(b"synthetic objective").hexdigest(),
            actor_id=actor, expected_registry_sha256=version)
        first = storage.checkpoint_service(
            service, actor_id=actor,
            expected_head_sha256=before.envelope_sha256)

        # Simulate process restart: reconstruct ONLY the registry from disk,
        # then create a NEW service using a NEW host-validated authorizer.
        loaded = storage.load()
        restarted = NexusMissionService(loaded.registry, authorizer=demo_authorizer)
        mission = restarted.get_mission("mission.demo", actor_id=actor)
        print(json.dumps({
            "demo": "colossus.mission.persistence.offline",
            "checkpoint_generation": first.generation,
            "reloaded_generation": loaded.receipt.generation,
            "registry_sha256_matches": (
                loaded.receipt.registry_sha256 == loaded.registry.snapshot()["registry_sha256"]
            ),
            "mission_id": mission["mission_id"],
            "mission_status": mission["status"],
            "workflow_bindings_restored": False,
            "effects_executed": False,
            "external_services_called": False,
        }, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
