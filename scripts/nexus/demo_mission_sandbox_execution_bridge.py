"""Inert Colossus sandbox demo. Never runs Docker, tests, shells or review hooks."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json

from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_supervised_executor import NexusMissionSupervisedExecutor
from aegisforge.nexus.mission_control.mission_sandbox_execution_bridge import (
    NexusMissionSandboxExecutionBridge, verify_sandbox_bridge_receipt,
)
from aegisforge.nexus.workspace.manager import NexusWorkspaceManager
from aegisforge.nexus.workspace.workspace_policy import NexusWorkspacePolicy, WorkspaceRule
from aegisforge.nexus.trust.action_policy import NexusActionPolicy, ActionRule
from aegisforge.nexus.trust.sandbox_runner import NexusSandboxRunner

MISSION = "demo.sandbox"


def main() -> None:
    with TemporaryDirectory(prefix="colossus-sandbox-demo-") as directory:
        root = Path(directory)
        (root / "test_example.py").write_bytes(b"def test_example():\n    assert 2 + 2 == 4\n")
        original = (root / "test_example.py").read_bytes()
        reg = NexusMissionRegistry()
        reg.register(MISSION, product="colossus", owner_id="owner.demo",
                     objective_sha256="a" * 64, actor_id="actor.demo")
        for component in ("trust", "mission_control"):
            reg.assign_component(MISSION, component=component, actor_id="actor.demo")
        reg.transition(MISSION, "ready", actor_id="actor.demo")
        wp = NexusWorkspacePolicy(NexusWorkspaceManager(root), rules=[
            WorkspaceRule(MISSION, "test_example.py", frozenset({"read_text", "run_tests"}))
        ], allow_mutations=True, approval_verifier=lambda _: False)
        action = NexusActionPolicy(wp, rules=[
            ActionRule(MISSION, "sandbox.tool", "run_tests", "test_example.py",
                       recipes=frozenset({"pytest_file"}))
        ])
        supervised = NexusMissionSupervisedExecutor(reg, action)
        # No backend -> no execution capability, including by accident.
        runner = NexusSandboxRunner(wp)
        bridge = NexusMissionSandboxExecutionBridge(reg, action, supervised, runner)
        receipt = bridge.stage(MISSION, "pytest_file", "test_example.py",
                               tool_id="sandbox.tool",
                               expected_registry_sha256=reg.snapshot()["registry_sha256"])
        observed = bridge.inspect(receipt["session_id"])
        summary = {
            "stage_status": receipt["status"],
            "receipt_valid": verify_sandbox_bridge_receipt(observed),
            "review_requested": False,
            "approval_consumed": observed["approval_consumed"],
            "effects_attempted": observed["effects_attempted"],
            "source_unchanged": (root / "test_example.py").read_bytes() == original,
        }
        bridge.cancel(receipt["session_id"])
        print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
