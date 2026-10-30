"""Colossus Mission Control — end-to-end, offline integration regression suite.

Install at tests/test_nexus/test_mission_integration.py.

Exercises REAL Registry -> State -> Coordinator -> Bridge -> read adapters ->
Runtime -> host-authorized Service, plus Laboratory, ProofGraph, Provenance,
Workspace and Engineering. No live network, model inference, patch application,
Docker, or external command dispatch. Temporary files only for synthetic tests.

IMPORTANT: content hashes are integrity metadata, NOT authority or authenticity.
The test authorizer is a fixture, NEVER production authentication.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_state import (
    NexusMissionState, MissionStatePolicy, MissionTransitionRule,
)
from aegisforge.nexus.mission_control.mission_coordinator import (
    NexusMissionCoordinator, MissionCoordinationBlueprint, MissionWorkflowStep,
)
from aegisforge.nexus.mission_control.mission_read_adapters import NexusMissionReadAdapters
from aegisforge.nexus.mission_control.mission_workspace_engineering_adapters import (
    NexusMissionWorkspaceEngineeringReadAdapters,
)
from aegisforge.nexus.mission_control.mission_proof_adapters import NexusMissionProofReadAdapters
from aegisforge.nexus.mission_control.mission_lab_adapters import NexusMissionLabReadAdapters
from aegisforge.nexus.mission_control.mission_runtime import (
    NexusMissionRuntime, verify_runtime_preview, verify_runtime_report,
)
from aegisforge.nexus.mission_control.mission_service import (
    NexusMissionService, NexusMissionServiceError,
)
from aegisforge.nexus.workspace.manager import NexusWorkspaceManager
from aegisforge.nexus.engineering.code_proposer import CodeProposal, ProposedEdit
from aegisforge.nexus.proof.proof_graph import NexusProofGraph
from aegisforge.nexus.proof.provenance import NexusProvenanceLedger
from aegisforge.nexus.lab.scenario_catalog import (
    NexusScenarioCatalog, ScenarioCase, ScenarioExpectation,
)
from aegisforge.nexus.lab.evaluation_runner import NexusEvaluationRunner, EvaluationObservation
from aegisforge.nexus.lab.scorecard import NexusScorecard

MID = 'mission.full_integration'
ACTOR = 'actor.integration'
OWNER = 'owner.integration'
H = sha256(b'colossus synthetic integration fixture').hexdigest()
SENSITIVE = 'PRIVATE_SYNTHETIC_STRING_DO_NOT_EXPORT'
PRODUCTS = ('nexus', 'colossus', 'aegisgrid', 'live_forge')


class FixtureAuthorizer:
    """Small explicit host *simulator*; never an authorization implementation."""

    def __init__(self, denied=(), allowed_actors=(ACTOR,)):
        self.denied = frozenset(denied)
        self.allowed_actors = frozenset(allowed_actors)
        self.calls = []

    def __call__(self, actor, action, mission, anchor, request):
        self.calls.append((actor, action, mission, anchor, deepcopy(request)))
        return actor in self.allowed_actors and action not in self.denied


def setup_service(*, product='colossus', components=(), denied=(), mission=MID):
    reg = NexusMissionRegistry()
    auth = FixtureAuthorizer(denied)
    svc = NexusMissionService(reg, authorizer=auth)
    anchor = svc.registry_version(actor_id=ACTOR)
    svc.create_mission(mission, product=product, owner_id=OWNER, objective_sha256=H,
                       actor_id=ACTOR, expected_registry_sha256=anchor)
    for component in sorted(set(components)):
        svc.assign_component(mission, component=component, actor_id=ACTOR,
                             expected_registry_sha256=svc.registry_version(actor_id=ACTOR))
    return reg, svc, auth


def step(name='step.registry', component='mission_control', operation='summarize_mission', deps=()):
    return MissionWorkflowStep(name, component, operation, tuple(deps))


def policy(mission=MID, product='colossus', target='ready', checker=None):
    return NexusMissionState(MissionStatePolicy(mission, product,
                            (MissionTransitionRule(target),)), host_checker=checker)


def bind(reg, svc, adapter, steps, *, product='colossus', mission=MID, target='ready'):
    state = policy(mission, product, target)
    blueprint = MissionCoordinationBlueprint(mission, product, target, tuple(steps))
    coordinator = NexusMissionCoordinator(blueprint, state)
    svc.configure_workflow(mission, actor_id=ACTOR, coordinator=coordinator,
                           adapters=adapter,
                           expected_registry_sha256=svc.registry_version(actor_id=ACTOR))
    return NexusMissionRuntime(reg, coordinator, adapter)


def inspect(reg, svc, *, product='colossus', steps=None, adapters=None):
    if steps is None:
        steps = (step(),)
    if adapters is None:
        adapters = NexusMissionReadAdapters(reg)
    runtime = bind(reg, svc, adapters, steps, product=product)
    return runtime, svc.inspect_workflow(MID, actor_id=ACTOR).to_dict()


def make_lab_catalog(*, product='aegisgrid'):
    cat = NexusScenarioCatalog()
    cat.register(ScenarioCase('case.integration', product=product, category='functional',
        title='Read-only test', description='Deterministic synthetic integration test',
        inputs={'value': 1},
        expectations=(ScenarioExpectation('ok', 'equals', True),)))
    return cat


def make_scorecard(*, product='aegisgrid'):
    cat = make_lab_catalog(product=product)
    runner = NexusEvaluationRunner(cat, trusted_sources=('integration_checks',))
    record = cat.get('case.integration')
    observation = EvaluationObservation('case.integration', record.case_sha256,
        source_id='integration_checks', evidence_ref='fixture/check', duration_ms=8,
        payload={'ok': True})
    evaluation = runner.evaluate(runner.prepare(), (observation,))
    engine = NexusScorecard()
    report = engine.build('colossus.integration', (evaluation,))
    return cat, engine, report, [evaluation]


def make_provenance(mission=MID):
    ledger = NexusProvenanceLedger(mission)
    ledger.record('artifact', sha256(b'synthetic byte content').hexdigest(),
                  origin_type='host', origin_id_sha256=H,
                  capture_method='direct_observation')
    return ledger


def canonical_hash(body):
    return sha256(json.dumps(body, sort_keys=True, separators=(',', ':'),
                             ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def reseal(body, key='report_sha256'):
    body[key] = canonical_hash({k: v for k, v in body.items() if k != key})
    return body


# A. Real Service / Registry lifecycle and scoped authorization.
@pytest.mark.parametrize('product', PRODUCTS)
def test_multi_product_creation_and_read(product):
    reg, svc, _ = setup_service(product=product)
    row = svc.get_mission(MID, actor_id=ACTOR)
    assert row['product'] == product
    assert row['status'] == 'draft'
    assert reg.snapshot()['mission_count'] == 1


@pytest.mark.parametrize('product', PRODUCTS)
def test_multi_product_read_only_flow(product):
    reg, svc, _ = setup_service(product=product, components=('mission_control',))
    before = reg.snapshot()
    _, report = inspect(reg, svc, product=product)
    assert verify_runtime_report(report)
    assert report['outcome'] == 'completed_read_only'
    assert report['receipt_count'] == 1
    assert reg.snapshot() == before


def test_lifecycle_create_assign_link_history_and_status():
    reg, svc, auth = setup_service(components=('mission_control',))
    svc.link_reference(MID, kind='evidence', reference_sha256=H, actor_id=ACTOR,
                       expected_registry_sha256=svc.registry_version(actor_id=ACTOR))
    assert reg.get(MID)['reference_count'] == 1
    assert len(svc.mission_history(MID, actor_id=ACTOR)) == 3
    status = svc.status(actor_id=ACTOR).to_dict()
    assert status['mission_count'] == 1
    assert status['execution_permission_granted'] is False
    assert status['network_server_started'] is False
    assert any(call[1] == 'link_reference' for call in auth.calls)


def test_service_read_is_scoped_by_mission():
    reg, svc, _ = setup_service()
    assert svc.get_mission(MID, actor_id=ACTOR)['mission_id'] == MID
    with pytest.raises((NexusMissionServiceError, ValueError, KeyError)):
        svc.get_mission('mission.unknown', actor_id='actor.other')


def test_create_rejects_stale_registry_anchor_without_mutation():
    reg, svc, _ = setup_service()
    before = reg.snapshot()
    with pytest.raises(NexusMissionServiceError, match='stale'):
        svc.create_mission('mission.additional', product='nexus', owner_id=OWNER,
            objective_sha256=H, actor_id=ACTOR, expected_registry_sha256='0'*64)
    assert reg.snapshot() == before


@pytest.mark.parametrize('denied', ['create', 'read', 'list', 'history', 'export_snapshot',
                                     'assign_component', 'link_reference', 'configure',
                                     'preview', 'inspect', 'transition'])
def test_action_specific_denial_is_enforced(denied):
    reg, svc, auth = setup_service()
    auth.denied = frozenset((denied,))
    before = reg.snapshot()
    with pytest.raises(NexusMissionServiceError, match='denied'):
        if denied == 'create':
            svc.create_mission('mission.additional', product='nexus', owner_id=OWNER,
                objective_sha256=H, actor_id=ACTOR,
                expected_registry_sha256=before['registry_sha256'])
        elif denied == 'read':
            svc.get_mission(MID, actor_id=ACTOR)
        elif denied == 'list':
            svc.list_missions(actor_id=ACTOR)
        elif denied == 'history':
            svc.mission_history(MID, actor_id=ACTOR)
        elif denied == 'export_snapshot':
            svc.export_snapshot(actor_id=ACTOR)
        elif denied == 'assign_component':
            svc.assign_component(MID, component='proof', actor_id=ACTOR,
                                 expected_registry_sha256=before['registry_sha256'])
        elif denied == 'link_reference':
            svc.link_reference(MID, kind='evidence', reference_sha256=H,
                               actor_id=ACTOR,
                               expected_registry_sha256=before['registry_sha256'])
        elif denied == 'configure':
            bind(reg, svc, NexusMissionReadAdapters(reg), (step(),))
        elif denied == 'preview':
            svc.preview_workflow(MID, actor_id=ACTOR)
        elif denied == 'inspect':
            svc.inspect_workflow(MID, actor_id=ACTOR)
        else:
            svc.transition_mission(MID, 'ready', actor_id=ACTOR,
                expected_registry_sha256=before['registry_sha256'],
                state=policy())
    assert reg.snapshot() == before


def test_service_authorizer_receives_exact_mutation_parameters():
    reg, svc, auth = setup_service()
    svc.assign_component(MID, component='proof', actor_id=ACTOR,
                         expected_registry_sha256=svc.registry_version(actor_id=ACTOR))
    assert auth.calls[-1][4] == {'component': 'proof'}
    svc.link_reference(MID, kind='evidence', reference_sha256=H, actor_id=ACTOR,
                       expected_registry_sha256=svc.registry_version(actor_id=ACTOR))
    assert auth.calls[-1][4] == {'kind': 'evidence', 'reference_sha256': H}


def test_unauthorized_actor_does_not_read():
    _, svc, _ = setup_service()
    with pytest.raises(NexusMissionServiceError, match='denied'):
        svc.get_mission(MID, actor_id='actor.untrusted')


def test_service_without_workflow_denies_preview_and_inspection():
    _, svc, _ = setup_service()
    for fn in (svc.preview_workflow, svc.inspect_workflow):
        with pytest.raises(NexusMissionServiceError, match='not configured'):
            fn(MID, actor_id=ACTOR)


def test_status_transition_is_a_registry_change_not_execution():
    reg, svc, _ = setup_service()
    old = reg.snapshot()['registry_sha256']
    row = svc.transition_mission(MID, 'ready', actor_id=ACTOR,
              expected_registry_sha256=old, state=policy())
    assert row['status'] == 'ready'
    assert reg.snapshot()['registry_sha256'] != old
    assert reg.snapshot()['effects_executed'] is False


def test_ready_to_running_requires_independent_host_checker():
    reg, svc, _ = setup_service()
    svc.transition_mission(MID, 'ready', actor_id=ACTOR,
        expected_registry_sha256=svc.registry_version(actor_id=ACTOR), state=policy())
    before = reg.snapshot()
    with pytest.raises(NexusMissionServiceError, match='not ready'):
        svc.transition_mission(MID, 'running', actor_id=ACTOR,
            expected_registry_sha256=before['registry_sha256'],
            state=policy(target='running'))
    assert reg.snapshot() == before


def test_running_status_does_not_imply_effect_execution():
    reg, svc, _ = setup_service()
    svc.transition_mission(MID, 'ready', actor_id=ACTOR,
        expected_registry_sha256=svc.registry_version(actor_id=ACTOR), state=policy())
    svc.transition_mission(MID, 'running', actor_id=ACTOR,
        expected_registry_sha256=svc.registry_version(actor_id=ACTOR),
        state=policy(target='running', checker=lambda *_: True))
    assert reg.get(MID)['status'] == 'running'
    assert reg.snapshot()['effects_executed'] is False


# B. End-to-end Service -> State -> Coordinator -> Bridge -> Runtime.
def test_end_to_end_read_report_and_state_unchanged():
    reg, svc, _ = setup_service(components=('mission_control',))
    before = reg.snapshot()
    runtime, report = inspect(reg, svc)
    preview = svc.preview_workflow(MID, actor_id=ACTOR).to_dict()
    assert verify_runtime_preview(preview)
    assert verify_runtime_report(report)
    assert report['preview'] == preview
    assert report['outcome'] == 'completed_read_only'
    assert report['completed_count'] == report['step_count'] == 1
    assert runtime.inspect().to_dict() == report
    assert before == reg.snapshot()


def test_bridge_dependency_execution_order_from_real_sources():
    reg, svc, _ = setup_service(components=('mission_control', 'lab', 'proof'))
    cat = make_lab_catalog()
    graph = NexusProofGraph(MID)
    ops = (
        step('step.proof', 'proof', 'verify_integrity', ('step.mission',)),
        step('step.lab', 'lab', 'summarize', ('step.proof',)),
        step('step.mission', 'mission_control', 'summarize_mission'),
    )
    _, report = inspect(reg, svc, steps=ops,
                        adapters=NexusMissionReadAdapters(reg, scenarios=cat, proof_graph=graph))
    assert report['outcome'] == 'completed_read_only'
    assert [r['step_id'] for r in report['receipts']] == [
        'step.mission', 'step.proof', 'step.lab']
    assert report['receipts'][1]['summary']['node_count'] >= 1
    assert report['receipts'][2]['summary']['by_product']['aegisgrid'] == 1


@pytest.mark.parametrize('component,operation', [
    ('mission_control', 'summarize_mission'),
    ('lab', 'summarize'),
    ('proof', 'verify_integrity'),
])
def test_valid_read_receipt_cannot_grant_permission(component, operation):
    reg, svc, _ = setup_service(components=(component,))
    kwargs = {'scenarios': make_lab_catalog() if component == 'lab' else None,
              'proof_graph': NexusProofGraph(MID) if component == 'proof' else None}
    _, report = inspect(reg, svc, steps=(step('step.read', component, operation),),
                        adapters=NexusMissionReadAdapters(reg, **kwargs))
    assert report['outcome'] == 'completed_read_only'
    for receipt in report['receipts']:
        assert receipt['authorization_granted'] is False
        assert receipt['effects_executed'] is False
        assert receipt['authenticity_verified'] is False
    assert report['authorization_granted'] is False
    assert report['effects_executed'] is False


def test_missing_required_component_blocks_all_source_reads():
    reg, svc, _ = setup_service()
    runtime, report = inspect(reg, svc)
    assert runtime.preview().outcome == 'blocked'
    assert report['outcome'] == 'blocked'
    assert report['receipts'] == []
    assert report['completed_count'] == 0


def test_missing_proof_source_fails_closed():
    reg, svc, _ = setup_service(components=('proof',))
    _, report = inspect(reg, svc, steps=(step('step.proof','proof','verify_integrity'),),
                        adapters=NexusMissionReadAdapters(reg))
    assert report['outcome'] == 'failed'
    assert report['receipt_count'] == 0
    assert report['reasons']
    assert verify_runtime_report(report)


def test_effect_steps_are_rejected_despite_declared_trust_dependency():
    reg, svc, _ = setup_service(components=('trust', 'engineering'))
    ops = (
        step('step.trust', 'trust', 'review_action'),
        MissionWorkflowStep('step.effect', 'engineering', 'request_effect',
                            ('step.trust',), H),
    )
    _, report = inspect(reg, svc, steps=ops)
    assert report['outcome'] == 'blocked'
    assert report['receipts'] == []
    assert report['effects_executed'] is False


def test_host_approval_is_distinct_from_read_receipt():
    reg, svc, auth = setup_service(components=('mission_control',))
    _, report = inspect(reg, svc)
    assert auth.calls[-1][1] == 'inspect'
    assert report['authorization_granted'] is False
    assert report['effects_executed'] is False


def test_read_workflow_is_deterministic_for_unchanged_sources():
    reg, svc, _ = setup_service(components=('mission_control',))
    runtime, first = inspect(reg, svc)
    assert first == runtime.inspect().to_dict()
    assert first == svc.inspect_workflow(MID, actor_id=ACTOR).to_dict()


def test_registry_change_invalidates_previous_receipt():
    reg, svc, _ = setup_service(components=('mission_control',))
    _, old = inspect(reg, svc)
    svc.link_reference(MID, kind='evidence', reference_sha256=H, actor_id=ACTOR,
        expected_registry_sha256=svc.registry_version(actor_id=ACTOR))
    _, new = inspect(reg, svc)
    assert old['registry_sha256'] != new['registry_sha256']
    assert old['report_sha256'] != new['report_sha256']


@pytest.mark.parametrize('tamper', [
    lambda d: d.update(authorization_granted=True),
    lambda d: d.update(effects_executed=True),
    lambda d: d.update(authenticity_verified=True),
    lambda d: d.update(mission_id='mission.other'),
    lambda d: d.update(completed_count=999),
    lambda d: d['receipts'][0].update(authorization_granted=True),
    lambda d: d['receipts'][0].update(step_id='step.other'),
])
def test_report_tampering_detected_even_if_rehashed(tamper):
    reg, svc, _ = setup_service(components=('mission_control',))
    _, report = inspect(reg, svc)
    bad = deepcopy(report)
    tamper(bad)
    assert not verify_runtime_report(reseal(bad))


def test_preview_tampering_detected():
    reg, svc, _ = setup_service(components=('mission_control',))
    runtime, _ = inspect(reg, svc)
    preview = runtime.preview().to_dict()
    preview['effects_executed'] = True
    assert not verify_runtime_preview(preview)


# C. Integration with REAL ProofGraph v2 / Provenance / Laboratory.
def test_provenance_v2_read_in_runtime_has_real_source_count():
    reg, svc, _ = setup_service(components=('proof',))
    ledger = make_provenance()
    graph = NexusProofGraph(MID)
    adapters = NexusMissionProofReadAdapters(reg, provenance=ledger, proof_graph=graph)
    _, report = inspect(reg, svc, steps=(step('step.provenance', 'proof', 'collect_evidence'),),
                        adapters=adapters)
    assert report['outcome'] == 'completed_read_only'
    summary = report['receipts'][0]['summary']
    assert summary['record_count'] == 1
    assert summary['chain_consistent'] is True
    assert summary['graph_present'] is True
    assert summary['metadata_integrity_verified'] is True
    assert summary['authenticity_verified'] is False
    assert summary['evidence_created_by_adapter'] is False


def test_provenance_v2_wrong_mission_fails_closed():
    reg, svc, _ = setup_service(components=('proof',))
    adapters = NexusMissionProofReadAdapters(reg, provenance=make_provenance('mission.other'))
    _, report = inspect(reg, svc, steps=(step('step.provenance', 'proof', 'collect_evidence'),),
                        adapters=adapters)
    assert report['outcome'] == 'failed'
    assert report['receipt_count'] == 0


def test_provenance_v2_source_drift_changes_receipt():
    reg, svc, _ = setup_service(components=('proof',))
    ledger = make_provenance()
    adapter = NexusMissionProofReadAdapters(reg, provenance=ledger)
    runtime, first = inspect(reg, svc,
        steps=(step('step.provenance', 'proof', 'collect_evidence'),), adapters=adapter)
    ledger.record('artifact', sha256(b'another synthetic artifact').hexdigest(),
                  origin_type='host', origin_id_sha256=H,
                  capture_method='direct_observation')
    second = runtime.inspect().to_dict()
    assert first['report_sha256'] != second['report_sha256']
    assert second['receipts'][0]['summary']['record_count'] == 2


def test_lab_scorecard_matches_original_evaluation_sources():
    reg, svc, _ = setup_service(components=('lab',))
    _, engine, scorecard, evaluations = make_scorecard()
    adapter = NexusMissionLabReadAdapters(
        reg, scorecard_engine=engine, scorecard_report=scorecard,
        scorecard_evaluations=evaluations)
    _, report = inspect(reg, svc, steps=(step('step.scorecard', 'lab', 'evaluate'),),
                        adapters=adapter)
    assert report['outcome'] == 'completed_read_only'
    summary = report['receipts'][0]['summary']
    assert summary['verified_against_sources'] is True
    assert summary['scenarios_executed_by_adapter'] is False
    assert summary['report_count'] == 1


def test_lab_scorecard_altered_report_is_rejected():
    reg, svc, _ = setup_service(components=('lab',))
    _, engine, scorecard, evaluations = make_scorecard()
    altered = deepcopy(scorecard)
    altered['aggregate']['passed'] += 1
    adapter = NexusMissionLabReadAdapters(
        reg, scorecard_engine=engine, scorecard_report=altered,
        scorecard_evaluations=evaluations)
    _, report = inspect(reg, svc, steps=(step('step.scorecard', 'lab', 'evaluate'),),
                        adapters=adapter)
    assert report['outcome'] == 'failed'
    assert report['receipts'] == []


def test_combined_provenance_and_scorecard_read_flow():
    reg, svc, _ = setup_service(components=('mission_control','proof','lab'))
    ledger = make_provenance()
    _, engine, scorecard, evaluations = make_scorecard()
    adapter = NexusMissionLabReadAdapters(
        reg, provenance=ledger, scorecard_engine=engine,
        scorecard_report=scorecard, scorecard_evaluations=evaluations)
    ops = (
        step('step.mission'),
        step('step.proof','proof','collect_evidence', ('step.mission',)),
        step('step.lab','lab','evaluate', ('step.proof',)),
    )
    _, report = inspect(reg, svc, steps=ops, adapters=adapter)
    assert report['outcome'] == 'completed_read_only'
    assert report['receipt_count'] == 3
    assert [r['component'] for r in report['receipts']] == ['mission_control','proof','lab']
    assert reg.get(MID)['status'] == 'draft'


# D. Temporary-fixture Workspace/Engineering adapter (no code exported/applied).
def make_workspace_flow(tmp_path):
    root = tmp_path / 'isolated'
    root.mkdir()
    source = root / 'app.py'
    source.write_bytes(b'print(1)\n')  # Cross-platform: byte-exact, no CRLF normalization.
    workspace = NexusWorkspaceManager(root)
    proposal = CodeProposal('proposal.integration', MID, H, SENSITIVE,
        (ProposedEdit('src/app.py', H, H, SENSITIVE, SENSITIVE),), usage={})
    reg, svc, _ = setup_service(components=('workspace', 'engineering'))
    adapter = NexusMissionWorkspaceEngineeringReadAdapters(
        reg, workspace=workspace, engineering_proposal=proposal)
    steps = (
        step('step.workspace','workspace','inventory'),
        step('step.engineering','engineering','propose_change',('step.workspace',)),
    )
    return reg, svc, adapter, steps, source


def test_workspace_and_engineering_read_no_file_changes(tmp_path):
    reg, svc, adapter, steps, source = make_workspace_flow(tmp_path)
    original = source.read_bytes()
    before = reg.snapshot()
    _, report = inspect(reg, svc, steps=steps, adapters=adapter)
    assert report['outcome'] == 'completed_read_only'
    assert [r['component'] for r in report['receipts']] == ['workspace','engineering']
    assert source.read_bytes() == original
    assert reg.snapshot() == before
    assert SENSITIVE not in json.dumps(report)
    assert str(source) not in json.dumps(report)


def test_workspace_addition_changes_inventory_receipt(tmp_path):
    reg, svc, adapter, steps, source = make_workspace_flow(tmp_path)
    runtime, before = inspect(reg, svc, steps=steps, adapters=adapter)
    (source.parent / 'added.py').write_bytes(b'new = True\n')
    after = runtime.inspect().to_dict()
    assert after['outcome'] == 'completed_read_only'
    assert after['report_sha256'] != before['report_sha256']


def test_workspace_synthetic_credentials_not_exported(tmp_path):
    reg, svc, adapter, steps, source = make_workspace_flow(tmp_path)
    (source.parent / '.env').write_bytes(b'PRIVATE_KEY=fictional-synthetic-value\n')
    _, report = inspect(reg, svc, steps=steps, adapters=adapter)
    assert 'PRIVATE_KEY' not in json.dumps(report)
    assert 'fictional-synthetic-value' not in json.dumps(report)
    assert '.env' not in json.dumps(report)


# E. Thread-safe lifecycle, repeated reports, and transport-neutral contracts.
def test_repeated_parallel_reads_do_not_mutate_registry():
    reg, svc, _ = setup_service(components=('mission_control',))
    runtime, first = inspect(reg, svc)
    before = reg.snapshot()
    with ThreadPoolExecutor(max_workers=4) as pool:
        reports = list(pool.map(lambda _: runtime.inspect().to_dict(), range(8)))
    assert all(verify_runtime_report(r) for r in reports)
    assert all(r == first for r in reports)
    assert reg.snapshot() == before


def test_repeated_parallel_authorized_service_reads():
    reg, svc, _ = setup_service(components=('mission_control',))
    bind(reg, svc, NexusMissionReadAdapters(reg), (step(),))
    with ThreadPoolExecutor(max_workers=4) as pool:
        reports = list(pool.map(
            lambda _: svc.inspect_workflow(MID, actor_id=ACTOR).to_dict(), range(8)))
    assert all(verify_runtime_report(r) for r in reports)
    assert len({r['report_sha256'] for r in reports}) == 1


def test_runtime_and_service_do_not_start_a_network_server():
    reg, svc, _ = setup_service(components=('mission_control',))
    _, report = inspect(reg, svc)
    assert report['effects_executed'] is False
    assert svc.status(actor_id=ACTOR).to_dict()['network_server_started'] is False


def test_nested_dict_is_not_a_permission_token():
    reg, svc, _ = setup_service(components=('mission_control',))
    _, report = inspect(reg, svc)
    rep = deepcopy(report)
    rep['receipts'][0]['summary']['status'] = 'verified'
    assert not verify_runtime_report(reseal(rep))


def test_source_output_excludes_raw_objective_and_sensitive_values(tmp_path):
    reg, svc, adapter, steps, _ = make_workspace_flow(tmp_path)
    _, report = inspect(reg, svc, steps=steps, adapters=adapter)
    raw = json.dumps(report)
    assert OWNER not in raw
    assert SENSITIVE not in raw
    assert 'src/app.py' not in raw
    assert 'PRIVATE_KEY' not in raw


def test_deterministic_json_roundtrip_report_and_preview():
    reg, svc, _ = setup_service(components=('mission_control',))
    runtime, report = inspect(reg, svc)
    text = json.dumps(report, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    assert verify_runtime_report(json.loads(text))
    preview = runtime.preview().to_dict()
    assert verify_runtime_preview(json.loads(json.dumps(preview)))


def test_no_undisclosed_persistence_from_read_only_inspections(tmp_path):
    reg, svc, adapter, steps, source = make_workspace_flow(tmp_path)
    before = {p.relative_to(tmp_path).as_posix(): p.read_bytes()
              for p in tmp_path.rglob('*') if p.is_file()}
    inspect(reg, svc, steps=steps, adapters=adapter)
    after = {p.relative_to(tmp_path).as_posix(): p.read_bytes()
             for p in tmp_path.rglob('*') if p.is_file()}
    assert before == after


# F. Mid-read changes, hostile callbacks and cross-mission boundaries.
def test_authorizer_exception_never_leaks_internal_details():
    reg, svc, auth = setup_service()
    def failing_auth(*_args):
        raise RuntimeError('SENSITIVE_INTERNAL_HOST_CALLBACK_ERROR')
    svc._authorizer = failing_auth  # Controlled test injection, not production practice.
    with pytest.raises(NexusMissionServiceError, match='host authorization unavailable') as caught:
        svc.get_mission(MID, actor_id=ACTOR)
    assert 'SENSITIVE_INTERNAL_HOST_CALLBACK_ERROR' not in str(caught.value)


def test_authorizer_non_boolean_truthy_value_is_denied():
    reg, svc, _ = setup_service()
    svc._authorizer = lambda *_: 1
    with pytest.raises(NexusMissionServiceError, match='denied'):
        svc.get_mission(MID, actor_id=ACTOR)


def test_authorizer_that_mutates_registry_fails_closed():
    reg, svc, _ = setup_service()
    before = reg.snapshot()
    def mutating_auth(*_args):
        reg.link_reference(MID, kind='evidence', reference_sha256=H, actor_id=ACTOR)
        return True
    svc._authorizer = mutating_auth
    with pytest.raises(NexusMissionServiceError, match='registry changed'):
        svc.get_mission(MID, actor_id=ACTOR)
    assert reg.snapshot()['registry_sha256'] != before['registry_sha256']
    # An external mutator is outside the service; the service correctly refuses
    # to treat the out-of-band change as a successful authorized request.


def test_runtime_detects_registry_change_after_first_read(monkeypatch):
    reg, svc, _ = setup_service(components=('mission_control',))
    adapter = NexusMissionReadAdapters(reg)
    runtime = bind(reg, svc, adapter, (step(),))
    original = adapter.inspect
    def mutate_after_read(plan, *, step_id):
        receipt = original(plan, step_id=step_id)
        reg.link_reference(MID, kind='evidence', reference_sha256=H, actor_id=ACTOR)
        return receipt
    monkeypatch.setattr(adapter, 'inspect', mutate_after_read)
    report = runtime.inspect().to_dict()
    assert report['outcome'] in {'failed', 'stale'}
    assert report['effects_executed'] is False
    assert verify_runtime_report(report)


def test_runtime_detects_source_drift_on_final_recheck(monkeypatch):
    reg, svc, _ = setup_service(components=('proof',))
    ledger = make_provenance()
    adapter = NexusMissionProofReadAdapters(reg, provenance=ledger)
    runtime = bind(reg, svc, adapter,
                   (step('step.proof', 'proof', 'collect_evidence'),))
    original = adapter.verify_receipt
    calls = {'n': 0}
    def mutate_after_verification(receipt, plan):
        value = original(receipt, plan)
        calls['n'] += 1
        if calls['n'] == 1 and value:
            ledger.record('check', sha256(b'new-check').hexdigest(),
                          origin_type='host', origin_id_sha256=H,
                          capture_method='reported')
        return value
    monkeypatch.setattr(adapter, 'verify_receipt', mutate_after_verification)
    report = runtime.inspect().to_dict()
    assert report['outcome'] == 'stale'
    assert report['reasons'] == ['source_changed_during_inspection']
    assert verify_runtime_report(report)


def test_runtime_fails_closed_when_adapter_raises_private_exception(monkeypatch):
    reg, svc, _ = setup_service(components=('mission_control',))
    adapter = NexusMissionReadAdapters(reg)
    runtime = bind(reg, svc, adapter, (step(),))
    def failing(*_args, **_kwargs):
        raise OSError('PRIVATE_WORKSPACE_PATH_AND_SECRET')
    monkeypatch.setattr(adapter, 'inspect', failing)
    report = runtime.inspect().to_dict()
    assert report['outcome'] == 'failed'
    assert report['reasons'] == ['read_adapter_unavailable']
    assert 'PRIVATE_WORKSPACE_PATH_AND_SECRET' not in json.dumps(report)


def test_scorecard_mutation_detected_after_success():
    reg, svc, _ = setup_service(components=('lab',))
    _, engine, scorecard, evaluations = make_scorecard()
    adapter = NexusMissionLabReadAdapters(
        reg, scorecard_engine=engine, scorecard_report=scorecard,
        scorecard_evaluations=evaluations)
    runtime, good = inspect(reg, svc,
                            steps=(step('step.scorecard','lab','evaluate'),),
                            adapters=adapter)
    assert good['outcome'] == 'completed_read_only'
    scorecard['aggregate']['passed'] = 999
    bad = runtime.inspect().to_dict()
    assert bad['outcome'] == 'failed'
    assert bad['effects_executed'] is False


def test_wrong_mission_graph_source_is_rejected():
    reg, svc, _ = setup_service(components=('proof',))
    adapter = NexusMissionReadAdapters(reg, proof_graph=NexusProofGraph('mission.other'))
    _, result = inspect(reg, svc,
                        steps=(step('step.proof','proof','verify_integrity'),),
                        adapters=adapter)
    assert result['outcome'] == 'failed'
    assert result['receipts'] == []


@pytest.mark.parametrize('product', PRODUCTS)
def test_mission_products_do_not_change_read_safety_guarantees(product):
    reg, svc, auth = setup_service(product=product, components=('mission_control',))
    runtime, report = inspect(reg, svc, product=product)
    assert report['product'] == product
    assert report['preview']['product'] == product
    assert verify_runtime_report(report)
    assert all(not r['authorization_granted'] for r in report['receipts'])
    assert all(not r['effects_executed'] for r in report['receipts'])
    assert svc.get_mission(MID, actor_id=ACTOR)['status'] == 'draft'
