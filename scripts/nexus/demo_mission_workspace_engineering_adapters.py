"""Synthetic, safe Colossus read-only integration demo.

Run from the repository root with PYTHONPATH=src:
  python scripts/nexus/demo_mission_workspace_engineering_adapters.py

Creates one *temporary* synthetic file outside the project root, inventories
only its file metadata, and inspects an already-prepared fake CodeProposal.
Does NOT read the user's repo, call Nebius, apply code, run tests, or authorize
anything. The temporary fixture is automatically cleaned up.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from aegisforge.nexus.workspace.manager import NexusWorkspaceManager
from aegisforge.nexus.engineering.code_proposer import CodeProposal, ProposedEdit
from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_state import (
    MissionStatePolicy, MissionTransitionRule, NexusMissionState,
)
from aegisforge.nexus.mission_control.mission_coordinator import (
    MissionCoordinationBlueprint, MissionWorkflowStep, NexusMissionCoordinator,
)
from aegisforge.nexus.mission_control.mission_bridge import NexusMissionBridge
from aegisforge.nexus.mission_control.mission_workspace_engineering_adapters import (
    NexusMissionWorkspaceEngineeringReadAdapters,
)


def run_demo() -> dict:
    mission_id = 'mission.workspace_demo'
    h = sha256(b'synthetic sample mission').hexdigest()
    with TemporaryDirectory(prefix='colossus-read-only-demo-') as directory:
        fixture = Path(directory)
        (fixture / 'demo.py').write_bytes(b'value = 42\n')
        workspace = NexusWorkspaceManager(fixture)
        proposal = CodeProposal(
            'proposal.synthetic', mission_id, h,
            'Synthetic proposal for demonstrating a non-applying preview',
            (ProposedEdit('demo.py', sha256(b'value = 42\n').hexdigest(),
                          sha256(b'value = 43\n').hexdigest(),
                          'Synthetic change only',
                          '--- demo.py\n+++ demo.py\n@@ -1 +1 @@\n-value = 42\n+value = 43'),),
            usage={},
        )
        registry = NexusMissionRegistry()
        registry.register(mission_id, product='colossus', owner_id='team.demo',
                          objective_sha256=h, actor_id='team.demo',
                          at_utc='2026-10-28T11:00:00Z')
        for seq, component in enumerate(('engineering', 'mission_control', 'workspace'), 1):
            registry.assign_component(mission_id, component=component,
                                      actor_id='team.demo',
                                      at_utc=f'2026-10-28T11:00:{seq:02d}Z')
        steps = (
            MissionWorkflowStep('step.mission', 'mission_control', 'summarize_mission'),
            MissionWorkflowStep('step.workspace', 'workspace', 'inventory',
                                ('step.mission',)),
            MissionWorkflowStep('step.engineering', 'engineering', 'propose_change',
                                ('step.workspace',)),
        )
        checker = NexusMissionState(MissionStatePolicy(
            mission_id, 'colossus', (MissionTransitionRule('ready'),)))
        coordinator = NexusMissionCoordinator(MissionCoordinationBlueprint(
            mission_id, 'colossus', 'ready', steps), checker)
        preview = coordinator.preview(registry)
        adapters = NexusMissionWorkspaceEngineeringReadAdapters(
            registry, workspace=workspace, engineering_proposal=proposal)
        bridge = NexusMissionBridge(adapters.catalog).plan(preview, registry)
        summaries = {}
        for step in steps:
            receipt = adapters.inspect(bridge, step_id=step.step_id).to_dict()
            assert adapters.verify_receipt(receipt, bridge)
            summaries[step.step_id] = receipt['summary']
        return {
            'mission_id': mission_id,
            'bridge_outcome': bridge.outcome,
            'read_only': True,
            'authorization_granted': False,
            'effects_executed': False,
            'synthetic_temporary_workspace': True,
            'summaries': summaries,
        }


if __name__ == '__main__':
    print(json.dumps(run_demo(), indent=2, ensure_ascii=False))
