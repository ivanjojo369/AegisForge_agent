"""Create a disposable, synthetic multi-product Mission Registry checkpoint.

Use ONLY for visual tests. It writes to a NEW temporary directory, never an
existing repository or a user-provided target; no model calls, execution,
approvals, or external services. Delete the printed directory when finished.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import tempfile

from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_persistence import NexusMissionPersistence


def main() -> None:
    base = Path(tempfile.mkdtemp(prefix="colossus_checkpoint_demo_"))
    registry = NexusMissionRegistry()
    for mission_id, product in (
        ("mission.nexus.demo", "nexus"),
        ("mission.grid.demo", "aegisgrid"),
        ("mission.forge.demo", "live_forge"),
        ("mission.colossus.demo", "colossus"),
    ):
        registry.register(mission_id, product=product, owner_id="owner.synthetic",
                          objective_sha256=sha256(mission_id.encode()).hexdigest(),
                          actor_id="actor.synthetic")
    receipt = NexusMissionPersistence(base).save(registry)
    print(json.dumps({"kind": "synthetic_disposable_checkpoint",
                      "checkpoint_dir": str(base),
                      "generation": receipt.generation,
                      "missions": registry.snapshot()["mission_count"],
                      "effects_executed": False,
                      "external_services_called": False}, sort_keys=True))


if __name__ == "__main__":
    main()
