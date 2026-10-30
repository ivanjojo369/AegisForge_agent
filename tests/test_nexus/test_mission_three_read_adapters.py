"""Synthetic offline integration tests for Trust, ProofGraph and Laboratory read adapters."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
from hashlib import sha256
import json

import pytest

from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_state import (
    MissionStatePolicy, MissionTransitionRule, NexusMissionState)
from aegisforge.nexus.mission_control.mission_coordinator import (
    MissionWorkflowStep, MissionCoordinationBlueprint, NexusMissionCoordinator)
from aegisforge.nexus.mission_control.mission_bridge import (
    NexusMissionBridge, verify_bridge_plan)
from aegisforge.nexus.mission_control.mission_read_adapters import NexusMissionReadAdapterError
from aegisforge.nexus.mission_control.mission_trust_adapters import (
    NexusMissionTrustReadAdapters, TRUST_READ_CATALOG)
from aegisforge.nexus.mission_control.mission_proof_adapters import (
    NexusMissionProofReadAdapters, PROOF_READ_CATALOG)
from aegisforge.nexus.mission_control.mission_lab_adapters import (
    NexusMissionLabReadAdapters, LAB_READ_CATALOG)
from aegisforge.nexus.proof.provenance import NexusProvenanceLedger
from aegisforge.nexus.trust.approval_gate import NexusApprovalGate, ActionBinding
from aegisforge.nexus.lab.scenario_catalog import (
    NexusScenarioCatalog, ScenarioCase, ScenarioExpectation)
from aegisforge.nexus.lab.evaluation_runner import (
    NexusEvaluationRunner, EvaluationObservation)
from aegisforge.nexus.lab.scorecard import NexusScorecard

MID = 'mission.colossus_review'
H = sha256(b'constant test data').hexdigest()


def seeded(tmp_path):
    registry = NexusMissionRegistry()
    registry.register(MID, product='colossus', owner_id='team.synthetic',
                      objective_sha256=H, actor_id='team.synthetic',
                      at_utc='2026-10-28T11:00:00Z')
    for i, comp in enumerate(('mission_control', 'trust', 'proof', 'lab'), 1):
        registry.assign_component(MID, component=comp, actor_id='team.synthetic',
                                  at_utc=f'2026-10-28T11:00:{i:02d}Z')

    gate = NexusApprovalGate(tmp_path, reviewer=lambda _: None,
                             binding_provider=lambda _: ActionBinding(H, H))
    req = gate.request(MID, 'write_file', 'synthetic.py', binding=ActionBinding(H,H))
    ledger = NexusProvenanceLedger(MID)
    ledger.record('artifact', sha256(b'synthetic').hexdigest(),
                  origin_type='tool', origin_id_sha256=H, capture_method='reported')

    catalog = NexusScenarioCatalog()
    catalog.register(ScenarioCase('case.synthetic', product='nexus', category='functional',
                   title='Synthetic', description='Synthetic in-memory evaluation',
                   inputs={'value': 1},
                   expectations=(ScenarioExpectation('ok', 'equals', True),)))
    runner = NexusEvaluationRunner(catalog, trusted_sources=('host_checks',))
    case = catalog.get('case.synthetic')
    observation = EvaluationObservation('case.synthetic', case.case_sha256,
        source_id='host_checks', evidence_ref='fixture/evidence', duration_ms=10,
        payload={'ok': True})
    evaluation = runner.evaluate(runner.prepare(), (observation,))
    engine = NexusScorecard()
    report = engine.build('colossus.summary', (evaluation,))
    data = dict(registry=registry, gate=gate, request=req, request_id=req.request_id,
                provenance=ledger, scorecard_engine=engine,
                scorecard_report=report, scorecard_evaluations=[evaluation])
    return data


def build_flow(data, cls=NexusMissionLabReadAdapters, *, operations=None, **override):
    registry = data['registry']
    kwargs = {
        'approval_gate': data['gate'],
        'approval_request': data['request'],
        'provenance': data['provenance'],
        'scorecard_engine': data['scorecard_engine'],
        'scorecard_report': data['scorecard_report'],
        'scorecard_evaluations': data['scorecard_evaluations'],
    }
    if cls is NexusMissionTrustReadAdapters:
        kwargs = {k:kwargs[k] for k in ('approval_gate','approval_request')}
    elif cls is NexusMissionProofReadAdapters:
        kwargs = {k:kwargs[k] for k in ('approval_gate','approval_request','provenance')}
    kwargs.update(override)
    adapter = cls(registry, **kwargs)
    if operations is None:
        operations = (
            MissionWorkflowStep('step.mission', 'mission_control', 'summarize_mission'),
            MissionWorkflowStep('step.trust', 'trust', 'review_action', ('step.mission',)),
            MissionWorkflowStep('step.proof', 'proof', 'collect_evidence', ('step.trust',)),
            MissionWorkflowStep('step.lab', 'lab', 'evaluate', ('step.proof',)),
        )
    state = NexusMissionState(MissionStatePolicy(MID, 'colossus',
                            (MissionTransitionRule('ready'),)))
    preview = NexusMissionCoordinator(MissionCoordinationBlueprint(
        MID, 'colossus', 'ready', tuple(operations)), state).preview(registry)
    plan = NexusMissionBridge(adapter.catalog).plan(preview, registry)
    return adapter, plan


def test_catalog_growth():
    assert len(TRUST_READ_CATALOG.ports) == 6
    assert len(PROOF_READ_CATALOG.ports) == 7
    assert len(LAB_READ_CATALOG.ports) == 8
    for cat in (TRUST_READ_CATALOG, PROOF_READ_CATALOG, LAB_READ_CATALOG):
        keys = [(p.component, p.operation) for p in cat.ports]
        assert keys == sorted(set(keys))
        assert all(p.to_dict()['metadata_only'] is True for p in cat.ports)
        assert all(p.operation != 'request_effect' for p in cat.ports)


@pytest.mark.parametrize('step', ['step.mission', 'step.trust', 'step.proof', 'step.lab'])
def test_real_sources_and_verify(tmp_path, step):
    data = seeded(tmp_path)
    adapter, plan = build_flow(data)
    before = data['registry'].snapshot(), data['provenance'].snapshot()
    row = adapter.inspect(plan, step_id=step).to_dict()
    assert adapter.verify_receipt(row, plan)
    assert row['authorization_granted'] is False
    assert row['effects_executed'] is False
    assert row['authenticity_verified'] is False
    assert row['integrity_only'] is True
    assert row['adapter_id'].startswith('local.')
    assert (data['registry'].snapshot(), data['provenance'].snapshot()) == before


def test_trust_pending_is_not_approval(tmp_path):
    data = seeded(tmp_path)
    adapter, plan = build_flow(data)
    row = adapter.inspect(plan, step_id='step.trust').to_dict()
    assert row['summary']['gate_status'] == 'pending'
    assert row['summary']['approved_for_execution_by_adapter'] is False
    assert row['summary']['gate_status_only'] is True
    assert row['summary']['request_sha256'] is not None
    assert 'relative_path' not in json.dumps(row)
    assert data['gate'].status(data['request_id']).status == 'pending'


def test_trust_denied_is_not_authorization(tmp_path):
    data = seeded(tmp_path)
    adapter, plan = build_flow(data)
    pending = adapter.inspect(plan, step_id='step.trust').to_dict()
    data['gate'].deny(data['request_id'])
    assert not adapter.verify_receipt(pending, plan)
    denied = adapter.inspect(plan, step_id='step.trust').to_dict()
    assert denied['summary']['gate_status'] == 'denied'
    assert denied['summary']['approved_for_execution_by_adapter'] is False


def test_trust_missing_gate(tmp_path):
    data = seeded(tmp_path)
    adapter, plan = build_flow(data, approval_gate=None)
    with pytest.raises(NexusMissionReadAdapterError, match='Trust source missing'):
        adapter.inspect(plan, step_id='step.trust')


def test_trust_wrong_request_id_constructor(tmp_path):
    data = seeded(tmp_path)
    with pytest.raises(NexusMissionReadAdapterError):
        build_flow(data, approval_request='not-an-id')


def test_proof_chain_and_no_evidence_write(tmp_path):
    data = seeded(tmp_path)
    adapter, plan = build_flow(data)
    count = len(data['provenance'].records())
    row = adapter.inspect(plan, step_id='step.proof').to_dict()
    assert row['summary']['record_count'] == 1
    assert row['summary']['chain_consistent'] is True
    assert row['summary']['evidence_created_by_adapter'] is False
    assert len(data['provenance'].records()) == count
    assert 'records' not in row['summary']


def test_proof_detects_ledger_growth(tmp_path):
    data = seeded(tmp_path)
    adapter, plan = build_flow(data)
    old = adapter.inspect(plan, step_id='step.proof').to_dict()
    data['provenance'].record('artifact', sha256(b'new').hexdigest(),
        origin_type='tool', origin_id_sha256=H, capture_method='reported')
    assert adapter.verify_receipt(old, plan) is False
    assert adapter.inspect(plan, step_id='step.proof').to_dict()['summary']['record_count'] == 2


def test_proof_rejects_cross_mission(tmp_path):
    data = seeded(tmp_path)
    wrong = NexusProvenanceLedger('mission.other')
    adapter, plan = build_flow(data, provenance=wrong)
    with pytest.raises(NexusMissionReadAdapterError, match='another mission'):
        adapter.inspect(plan, step_id='step.proof')


def test_proof_missing_ledger(tmp_path):
    data = seeded(tmp_path)
    adapter, plan = build_flow(data, provenance=None)
    with pytest.raises(NexusMissionReadAdapterError):
        adapter.inspect(plan, step_id='step.proof')


def test_lab_verified_source_summary(tmp_path):
    data = seeded(tmp_path)
    adapter, plan = build_flow(data)
    row = adapter.inspect(plan, step_id='step.lab').to_dict()
    assert row['summary']['scorecard_status'] == 'descriptive_complete'
    assert row['summary']['report_count'] == 1
    assert row['summary']['evaluations'] == 1
    assert row['summary']['passed'] == 1
    assert row['summary']['verified_against_sources'] is True
    assert row['summary']['scenarios_executed_by_adapter'] is False
    assert 'evidence_ref' not in json.dumps(row)
    assert 'scenario_id' not in json.dumps(row)


def test_lab_detects_forged_scorecard_even_with_new_sha(tmp_path):
    data = seeded(tmp_path)
    adapter, plan = build_flow(data)
    altered = deepcopy(data['scorecard_report'])
    altered['aggregate']['passed'] = 300
    body = {k:v for k,v in altered.items() if k != 'scorecard_sha256'}
    altered['scorecard_sha256'] = sha256(json.dumps(body, sort_keys=True,
            separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
    data['scorecard_report'].clear()
    data['scorecard_report'].update(altered)
    with pytest.raises(NexusMissionReadAdapterError, match='inconsistent'):
        adapter.inspect(plan, step_id='step.lab')


def test_lab_rejects_mismatch_source(tmp_path):
    data = seeded(tmp_path)
    adapter, plan = build_flow(data)
    data['scorecard_evaluations'][0]['report_sha256'] = H
    with pytest.raises(NexusMissionReadAdapterError, match='inconsistent'):
        adapter.inspect(plan, step_id='step.lab')


def test_lab_missing_engine(tmp_path):
    data = seeded(tmp_path)
    adapter, plan = build_flow(data, scorecard_engine=None)
    with pytest.raises(NexusMissionReadAdapterError, match='sources missing'):
        adapter.inspect(plan, step_id='step.lab')


@pytest.mark.parametrize('step', ['step.trust', 'step.proof', 'step.lab'])
def test_tampered_receipt_fails(tmp_path, step):
    data = seeded(tmp_path)
    adapter, plan = build_flow(data)
    receipt = adapter.inspect(plan, step_id=step).to_dict()
    receipt['summary']['passed' if step == 'step.lab' else 'verified_against_sources'] = (999 if step == 'step.lab' else True)
    assert not adapter.verify_receipt(receipt, plan)


def test_stale_bridge_fails_before_read(tmp_path):
    data = seeded(tmp_path)
    adapter, plan = build_flow(data)
    data['registry'].assign_component(MID, component='engineering',
        actor_id='team.synthetic', at_utc='2026-10-28T11:00:09Z')
    with pytest.raises(NexusMissionReadAdapterError, match='stale'):
        adapter.inspect(plan, step_id='step.trust')


def test_reject_effect_request_in_plan(tmp_path):
    data = seeded(tmp_path)
    operations = (
        MissionWorkflowStep('step.mission', 'mission_control', 'summarize_mission'),
        MissionWorkflowStep('step.review', 'trust', 'review_action', ('step.mission',)),
        MissionWorkflowStep('step.effect', 'engineering', 'request_effect',
                            ('step.review',), H),
    )
    adapter, plan = build_flow(data, operations=operations)
    assert plan.outcome == 'blocked'  # no request_effect adapter supplied
    with pytest.raises(NexusMissionReadAdapterError):
        adapter.inspect(plan, step_id='step.review')


def test_bad_port_does_not_bind(tmp_path):
    data = seeded(tmp_path)
    operations = (MissionWorkflowStep('step.unknown', 'lab', 'summarize'),
                  MissionWorkflowStep('step.review', 'trust', 'review_transition'))
    adapter, plan = build_flow(data, operations=operations)
    assert plan.outcome == 'blocked'
    with pytest.raises(NexusMissionReadAdapterError):
        adapter.inspect(plan, step_id='step.unknown')


def test_receipt_immutable_and_detached(tmp_path):
    data = seeded(tmp_path)
    adapter, plan = build_flow(data)
    receipt = adapter.inspect(plan, step_id='step.lab')
    with pytest.raises(FrozenInstanceError):
        receipt._json = b'false'
    altered = receipt.to_dict()
    altered['summary']['passed'] = 999
    assert receipt.to_dict()['summary']['passed'] == 1


def test_successful_inspection_does_not_transition_mission(tmp_path):
    data = seeded(tmp_path)
    adapter, plan = build_flow(data)
    snapshot = data['registry'].snapshot()
    for step in ('step.mission', 'step.trust', 'step.proof', 'step.lab'):
        adapter.inspect(plan, step_id=step)
    assert data['registry'].snapshot() == snapshot


def test_bridge_is_host_review_not_permission(tmp_path):
    data = seeded(tmp_path)
    adapter, plan = build_flow(data)
    assert plan.outcome == 'host_review_required'
    assert verify_bridge_plan(plan.to_dict(), registry=data['registry'], catalog=adapter.catalog)
    assert all(row['metadata_only'] is True for row in plan.to_dict()['handoffs'])
    assert not plan.to_dict()['authorization_granted']


@pytest.mark.parametrize('cls,op', [
    (NexusMissionTrustReadAdapters, 'review_action'),
    (NexusMissionProofReadAdapters, 'collect_evidence'),
    (NexusMissionLabReadAdapters, 'evaluate'),
])
def test_individual_stage_connects(tmp_path, cls, op):
    data = seeded(tmp_path)
    comp = 'trust' if op == 'review_action' else ('proof' if op == 'collect_evidence' else 'lab')
    adapter, plan = build_flow(data, cls=cls,
        operations=(MissionWorkflowStep('step.single', comp, op),))
    receipt = adapter.inspect(plan, step_id='step.single').to_dict()
    assert receipt['component'] == comp
    assert adapter.verify_receipt(receipt, plan)


def test_trust_rejects_request_from_different_mission(tmp_path):
    data = seeded(tmp_path)
    other = data['gate'].request('mission.other', 'write_file', 'synthetic.py',
                                 binding=ActionBinding(H, H))
    adapter, plan = build_flow(data, approval_request=other)
    with pytest.raises(NexusMissionReadAdapterError, match='another mission'):
        adapter.inspect(plan, step_id='step.trust')


def test_trust_rejects_foreign_gate(tmp_path):
    data = seeded(tmp_path)
    from pathlib import Path
    other_dir = tmp_path / 'other'
    other_dir.mkdir()
    other_gate = NexusApprovalGate(other_dir, reviewer=lambda _: None,
                                   binding_provider=lambda _: ActionBinding(H,H))
    adapter, plan = build_flow(data, approval_gate=other_gate)
    with pytest.raises(NexusMissionReadAdapterError, match='root differs'):
        adapter.inspect(plan, step_id='step.trust')


def test_trust_wrong_same_root_request_rejected(tmp_path):
    data = seeded(tmp_path)
    another = data['gate'].request(MID, 'write_file', 'different.py',
                                    binding=ActionBinding(H, H))
    from dataclasses import replace
    altered_request = replace(data['request'], request_id=another.request_id)
    adapter, plan = build_flow(data, approval_request=altered_request)
    with pytest.raises(NexusMissionReadAdapterError, match='binding mismatch'):
        adapter.inspect(plan, step_id='step.trust')


def test_catalogs_remain_stable_under_data_changes(tmp_path):
    data = seeded(tmp_path)
    adapter, plan = build_flow(data)
    digest = adapter.catalog.sha256
    data['gate'].deny(data['request_id'])
    data['provenance'].record('artifact', sha256(b'new').hexdigest(),
        origin_type='tool', origin_id_sha256=H, capture_method='reported')
    assert adapter.catalog.sha256 == digest


def test_cross_source_receipt_rejected(tmp_path):
    data = seeded(tmp_path)
    adapter, plan = build_flow(data)
    receipt = adapter.inspect(plan, step_id='step.proof').to_dict()
    receipt['step_id'] = 'step.lab'
    assert not adapter.verify_receipt(receipt, plan)


def test_no_arbitrary_operation_dispatch(tmp_path):
    data = seeded(tmp_path)
    adapter, plan = build_flow(data)
    with pytest.raises(NexusMissionReadAdapterError):
        adapter.inspect(plan, step_id='step.nonexistent')


def test_no_missing_lab_report(tmp_path):
    data = seeded(tmp_path)
    adapter, plan = build_flow(data, scorecard_report=None)
    with pytest.raises(NexusMissionReadAdapterError, match='sources missing'):
        adapter.inspect(plan, step_id='step.lab')


def test_lab_rejects_incompatible_report_id(tmp_path):
    data = seeded(tmp_path)
    adapter, plan = build_flow(data)
    data['scorecard_report']['scorecard_id'] = 'another.report'
    with pytest.raises(NexusMissionReadAdapterError, match='inconsistent'):
        adapter.inspect(plan, step_id='step.lab')


def test_no_hidden_actions_in_catalog():
    text = json.dumps(LAB_READ_CATALOG.to_dict())
    for forbidden in ('run_tests', 'execute_tool', 'request_effect',
                      'verify_workspace_action', 'apply_change'):
        assert forbidden not in text
