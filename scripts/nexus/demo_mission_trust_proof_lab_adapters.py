"""Synthetic, non-executing demo of three real Colossus read adapters.

Run from the repository root, with PYTHONPATH=src:
  python scripts/nexus/demo_mission_trust_proof_lab_adapters.py

Only a temporary directory is used to initialize a synthetic Trust gate.
No AI calls, external networks, workspace modifications or effect approvals.
"""
from __future__ import annotations

from hashlib import sha256
import json
from tempfile import TemporaryDirectory

from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_state import (
    MissionStatePolicy, MissionTransitionRule, NexusMissionState,
)
from aegisforge.nexus.mission_control.mission_coordinator import (
    MissionWorkflowStep, MissionCoordinationBlueprint, NexusMissionCoordinator,
)
from aegisforge.nexus.mission_control.mission_bridge import NexusMissionBridge
from aegisforge.nexus.mission_control.mission_lab_adapters import NexusMissionLabReadAdapters
from aegisforge.nexus.trust.approval_gate import NexusApprovalGate, ActionBinding
from aegisforge.nexus.proof.provenance import NexusProvenanceLedger
from aegisforge.nexus.lab.scenario_catalog import (
    NexusScenarioCatalog, ScenarioCase, ScenarioExpectation,
)
from aegisforge.nexus.lab.evaluation_runner import (
    NexusEvaluationRunner, EvaluationObservation,
)
from aegisforge.nexus.lab.scorecard import NexusScorecard


def main() -> dict:
    mid = 'mission.integration_demo'
    digest = sha256(b'synthetic metadata').hexdigest()
    registry = NexusMissionRegistry()
    registry.register(mid, product='colossus', owner_id='team.synthetic',
                      objective_sha256=digest, actor_id='team.synthetic',
                      at_utc='2026-10-28T11:00:00Z')
    for number, component in enumerate(('mission_control', 'trust', 'proof', 'lab'), 1):
        registry.assign_component(mid, component=component, actor_id='team.synthetic',
                                  at_utc=f'2026-10-28T11:00:{number:02d}Z')
    ledger = NexusProvenanceLedger(mid)
    ledger.record('artifact', digest, origin_type='tool',
                  origin_id_sha256=digest, capture_method='reported')
    scenarios = NexusScenarioCatalog()
    scenarios.register(ScenarioCase('case.energy', product='aegisgrid',
                       category='functional', title='Synthetic energy case',
                       description='Small non-operational sample',
                       inputs={'synthetic_value': 1},
                       expectations=(ScenarioExpectation('ok', 'equals', True),)))
    runner = NexusEvaluationRunner(scenarios, trusted_sources=('test_harness',))
    case = scenarios.get('case.energy')
    report = runner.evaluate(runner.prepare(), (
        EvaluationObservation(case.scenario_id, case.case_sha256,
                              source_id='test_harness', evidence_ref='synthetic/sample',
                              duration_ms=2, payload={'ok': True}),
    ))
    scorecard_engine = NexusScorecard()
    scorecard = scorecard_engine.build('colossus.demo_scorecard', (report,))
    steps = (
        MissionWorkflowStep('step.mission', 'mission_control', 'summarize_mission'),
        MissionWorkflowStep('step.trust', 'trust', 'review_action', ('step.mission',)),
        MissionWorkflowStep('step.proof', 'proof', 'collect_evidence', ('step.trust',)),
        MissionWorkflowStep('step.lab', 'lab', 'evaluate', ('step.proof',)),
    )
    state = NexusMissionState(MissionStatePolicy(mid, 'colossus',
        (MissionTransitionRule('ready'),)))
    coordinator = NexusMissionCoordinator(MissionCoordinationBlueprint(
        mid, 'colossus', 'ready', steps), state)
    with TemporaryDirectory(prefix='colossus-trust-demo-') as directory:
        # Never approve or consume this synthetic review request.
        gate = NexusApprovalGate(directory, reviewer=lambda request: None,
                                 binding_provider=lambda action: ActionBinding(digest, digest))
        req = gate.request(mid, 'write_file', 'nonexistent-synthetic.py',
                           binding=ActionBinding(digest, digest))
        adapter = NexusMissionLabReadAdapters(
            registry, approval_gate=gate, approval_request=req,
            provenance=ledger, scorecard_engine=scorecard_engine,
            scorecard_report=scorecard, scorecard_evaluations=(report,))
        bridge = NexusMissionBridge(adapter.catalog).plan(coordinator.preview(registry), registry)
        checks = {}
        for step in steps:
            receipt = adapter.inspect(bridge, step_id=step.step_id).to_dict()
            assert adapter.verify_receipt(receipt, bridge)
            checks[step.step_id] = receipt['summary']
        return {'mission_id': mid, 'outcome': bridge.outcome,
                'read_only': True, 'authorization_granted': False,
                'effects_executed': False, 'summaries': checks}


if __name__ == '__main__':
    print(json.dumps(main(), ensure_ascii=False, indent=2))
