"""Offline, no-effect demonstration of Colossus supervised action preflight."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_supervised_executor import (
    NexusMissionSupervisedExecutor, verify_supervised_plan, verify_supervised_report,
)
from aegisforge.nexus.workspace.manager import NexusWorkspaceManager
from aegisforge.nexus.workspace.workspace_policy import NexusWorkspacePolicy, WorkspaceRule
from aegisforge.nexus.trust.action_policy import NexusActionPolicy, ActionRule, ActionIntent


def main() -> None:
    with TemporaryDirectory(prefix="colossus-supervised-") as temp:
        root = Path(temp) / "synthetic_repo"
        root.mkdir()
        sample = root / "sample.py"
        sample.write_bytes(b"print('synthetic')\n")
        baseline = sample.read_bytes()
        mission_id = "mission.supervised.demo"
        registry = NexusMissionRegistry()
        registry.register(mission_id, product="colossus", owner_id="owner.example",
                          objective_sha256="a" * 64, actor_id="actor.example")
        registry.assign_component(mission_id, component="mission_control", actor_id="actor.example")
        registry.assign_component(mission_id, component="trust", actor_id="actor.example")
        registry.transition(mission_id, "ready", actor_id="actor.example")

        workspace_policy = NexusWorkspacePolicy(
            NexusWorkspaceManager(root),
            rules=[WorkspaceRule(mission_id, "sample.py",
                                 frozenset({"read_text", "apply_patch"}))],
            allow_mutations=True,
            approval_verifier=lambda _: False,  # Demo host explicitly NEVER approves.
        )
        action_policy = NexusActionPolicy(workspace_policy, rules=[
            ActionRule(mission_id, "reader", "read_text", "sample.py"),
            ActionRule(mission_id, "patcher", "apply_patch", "sample.py"),
        ])
        supervisor = NexusMissionSupervisedExecutor(registry, action_policy)
        proposals = (
            ActionIntent(mission_id, "reader", "read_text", "sample.py", "b" * 64, {}),
            ActionIntent(mission_id, "patcher", "apply_patch", "sample.py", "b" * 64, {}),
        )
        plan = supervisor.prepare(mission_id, proposals,
            expected_registry_sha256=registry.snapshot()["registry_sha256"])
        receipt = supervisor.finalize(plan.session_id)
        assert verify_supervised_plan(plan.to_dict())
        assert verify_supervised_report(receipt.to_dict(), plan=plan.to_dict())
        assert sample.read_bytes() == baseline
        print(json.dumps({
            "mission_id": mission_id,
            "plan_valid": True,
            "report_valid": True,
            "outcome": receipt.outcome,
            "action_count": receipt.to_dict()["action_count"],
            "read_only_actions": sum(a["policy_status"] == "allow_read_only"
                                     for a in receipt.to_dict()["actions"]),
            "review_required_actions": sum(a["policy_status"] == "review_required"
                                           for a in receipt.to_dict()["actions"]),
            "authorization_granted": False,
            "approval_consumed": False,
            "effects_executed": False,
            "workspace_unchanged": True,
        }, indent=2))


if __name__ == "__main__":
    main()
