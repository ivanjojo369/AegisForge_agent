"""Tests for safe, read-only Workspace and Engineering Mission Bridge extension."""
from dataclasses import FrozenInstanceError
from hashlib import sha256
import json

import pytest

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
from aegisforge.nexus.mission_control.mission_read_adapters import (
    READ_ONLY_CATALOG, NexusMissionReadAdapterError,
)
from aegisforge.nexus.mission_control.mission_workspace_engineering_adapters import (
    WORKSPACE_ENGINEERING_READ_CATALOG,
    NexusMissionWorkspaceEngineeringReadAdapters as Extended,
)

H = 'a' * 64
MID = 'mission.integration'
SECRET = 'VERY_PRIVATE_SOURCE_AND_RATIONALE'


def flow(tmp_path, *, include_workspace=True, include_engineering=True,
         proposal=None, workspace=None, scope='.', product='colossus', steps=None):
    if workspace is None:
        (tmp_path / 'main.py').write_text('print(1)\n', encoding='utf-8')
        workspace = NexusWorkspaceManager(tmp_path)
    if proposal is None:
        proposal = CodeProposal('proposal.example', MID, H, SECRET,
            (ProposedEdit('src/main.py', H, H, SECRET, SECRET),), usage={})
    r = NexusMissionRegistry()
    r.register(MID, product=product, owner_id='team.demo', objective_sha256=H,
               actor_id='team.demo', at_utc='2026-10-28T10:00:00Z')
    all_steps = tuple(steps) if steps is not None else (
        MissionWorkflowStep('step.workspace', 'workspace', 'inventory'),
        MissionWorkflowStep('step.engineering', 'engineering', 'propose_change',
                            ('step.workspace',)),
    )
    for index, component in enumerate(sorted({s.component for s in all_steps}), 1):
        r.assign_component(MID, component=component, actor_id='team.demo',
                           at_utc=f'2026-10-28T10:00:{index:02d}Z')
    checker = NexusMissionState(MissionStatePolicy(MID, product,
                              (MissionTransitionRule('ready'),)))
    preview = NexusMissionCoordinator(MissionCoordinationBlueprint(
        MID, product, 'ready', all_steps), checker).preview(r)
    adapter = Extended(r, workspace=workspace if include_workspace else None,
                       engineering_proposal=proposal if include_engineering else None,
                       workspace_scope=scope)
    plan = NexusMissionBridge(adapter.catalog).plan(preview, r)
    return r, workspace, proposal, plan, adapter


def test_additional_contracts_and_legacy_contracts_unchanged():
    pairs = [(p.component, p.operation) for p in WORKSPACE_ENGINEERING_READ_CATALOG.ports]
    assert pairs == sorted(pairs)
    assert pairs == [('engineering','propose_change'), ('lab','summarize'),
                     ('mission_control','summarize_mission'), ('proof','verify_integrity'),
                     ('workspace','inventory')]
    for old in READ_ONLY_CATALOG.ports:
        assert old in WORKSPACE_ENGINEERING_READ_CATALOG.ports
    assert 'request_effect' not in json.dumps(WORKSPACE_ENGINEERING_READ_CATALOG.to_dict())


@pytest.mark.parametrize('step', ['step.workspace', 'step.engineering'])
def test_real_inspect_verifies_without_side_effects(tmp_path, step):
    r, ws, proposal, plan, adapter = flow(tmp_path)
    state = r.snapshot()
    source_before = (tmp_path / 'main.py').read_bytes()
    receipt = adapter.inspect(plan, step_id=step).to_dict()
    assert receipt['effects_executed'] is False
    assert receipt['authorization_granted'] is False
    assert receipt['authenticity_verified'] is False
    assert receipt['integrity_only'] is True
    assert receipt['component'] == step.split('.')[1]
    assert adapter.verify_receipt(receipt, plan)
    assert state == r.snapshot()
    assert source_before == (tmp_path / 'main.py').read_bytes()
    assert SECRET not in json.dumps(receipt)
    assert str(tmp_path) not in json.dumps(receipt)
    assert 'src/main.py' not in json.dumps(receipt)


def test_workspace_reads_inventory_metadata_only(tmp_path):
    *_, plan, adapter = flow(tmp_path)
    (tmp_path / '.env').write_text('PRIVATE_KEY=private')
    receipt = adapter.inspect(plan, step_id='step.workspace').to_dict()
    assert receipt['summary']['file_count'] == 1
    assert receipt['summary']['text_candidate_count'] == 1
    assert receipt['summary']['metadata_only'] is True
    assert 'PRIVATE_KEY' not in json.dumps(receipt)
    assert '.env' not in json.dumps(receipt)


def test_workspace_inventory_changes_are_detected(tmp_path):
    *_, plan, adapter = flow(tmp_path)
    receipt = adapter.inspect(plan, step_id='step.workspace').to_dict()
    (tmp_path / 'added.py').write_text('a=2\n', encoding='utf-8')
    assert not adapter.verify_receipt(receipt, plan)
    assert adapter.inspect(plan, step_id='step.workspace').sha256 != receipt['receipt_sha256']


def test_workspace_same_size_modification_is_not_content_proof(tmp_path):
    *_, plan, adapter = flow(tmp_path)
    receipt = adapter.inspect(plan, step_id='step.workspace').to_dict()
    p = tmp_path / 'main.py'
    # Keep the original newline bytes. On Windows, write_text() can write
    # CRLF although the Python source literal above uses LF.
    original = p.read_bytes()
    modified = original.replace(b'print(1)', b'print(2)')
    assert modified != original
    assert len(modified) == len(original)
    p.write_bytes(modified)
    # A metadata-only inventory is NOT proof of unchanged file contents.
    assert adapter.verify_receipt(receipt, plan)


@pytest.mark.parametrize('newline', [b'\n', b'\r\n'], ids=['LF', 'CRLF'])
def test_workspace_equal_byte_size_is_metadata_only_for_each_newline(tmp_path, newline):
    p = tmp_path / 'main.py'
    p.write_bytes(b'print(1)' + newline)
    workspace = NexusWorkspaceManager(tmp_path)
    *_, plan, adapter = flow(tmp_path, workspace=workspace)
    receipt = adapter.inspect(plan, step_id='step.workspace').to_dict()
    previous = p.read_bytes()
    p.write_bytes(b'print(2)' + newline)
    assert len(previous) == len(p.read_bytes())
    assert adapter.verify_receipt(receipt, plan)


def test_workspace_changed_size_invalidates_metadata_receipt(tmp_path):
    *_, plan, adapter = flow(tmp_path)
    receipt = adapter.inspect(plan, step_id='step.workspace').to_dict()
    p = tmp_path / 'main.py'
    p.write_bytes(p.read_bytes() + b'# changed size')
    assert adapter.verify_receipt(receipt, plan) is False


def test_workspace_scope_restricts_inventory(tmp_path):
    (tmp_path / 'src').mkdir()
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'src' / 'app.py').write_text('x = 1')
    (tmp_path / 'tests' / 'test_app.py').write_text('assert True')
    ws = NexusWorkspaceManager(tmp_path)
    *_, plan, adapter = flow(tmp_path, workspace=ws, scope='src')
    out = adapter.inspect(plan, step_id='step.workspace').to_dict()
    assert out['summary']['file_count'] == 1
    assert out['summary']['total_bytes'] == len('x = 1')


@pytest.mark.parametrize('scope', ['', '../outside', '/etc', 'C:\\Windows',
                                  'src/../files', 'src//files', 'src\x00data',
                                  123, 'a'*257])
def test_rejects_unapproved_scope(tmp_path, scope):
    with pytest.raises(NexusMissionReadAdapterError):
        flow(tmp_path, scope=scope)


def test_missing_workspace_rejected_on_inspect(tmp_path):
    *_, plan, adapter = flow(tmp_path, include_workspace=False)
    with pytest.raises(NexusMissionReadAdapterError, match='workspace source missing'):
        adapter.inspect(plan, step_id='step.workspace')


def test_missing_engineering_rejected_on_inspect(tmp_path):
    *_, plan, adapter = flow(tmp_path, include_engineering=False)
    with pytest.raises(NexusMissionReadAdapterError, match='engineering proposal source missing'):
        adapter.inspect(plan, step_id='step.engineering')


def test_engineering_summary_excludes_source_and_model_text(tmp_path):
    *_, plan, adapter = flow(tmp_path)
    d = adapter.inspect(plan, step_id='step.engineering').to_dict()
    assert d['summary']['edit_count'] == 1
    assert d['summary']['needs_review'] is True
    assert d['summary']['proposal_status'] == 'proposed'
    assert d['summary']['changes_applied_by_adapter'] is False
    assert SECRET not in json.dumps(d)
    assert d['summary']['context_sha256'] == H


def test_engineering_receipt_stales_on_rationale_change(tmp_path):
    *_, proposal, plan, adapter = flow(tmp_path)
    out = adapter.inspect(plan, step_id='step.engineering').to_dict()
    edited = ProposedEdit('src/main.py', H, H, 'revised rationale', SECRET)
    adapter._engineering_proposal = CodeProposal(proposal.proposal_id, MID, H,
        SECRET, (edited,), usage={})
    assert not adapter.verify_receipt(out, plan)


def test_proposal_cross_mission_rejected(tmp_path):
    bad = CodeProposal('proposal.x', 'mission.other', H, 'summary', (), usage={})
    *_, plan, adapter = flow(tmp_path, proposal=bad)
    with pytest.raises(NexusMissionReadAdapterError, match='another mission'):
        adapter.inspect(plan, step_id='step.engineering')


def test_proposal_status_must_be_proposed(tmp_path):
    bad = CodeProposal('proposal.x', MID, H, 'summary', (), usage={}, status='approved')
    *_, plan, adapter = flow(tmp_path, proposal=bad)
    with pytest.raises(NexusMissionReadAdapterError, match='not in proposed'):
        adapter.inspect(plan, step_id='step.engineering')


def test_invalid_context_digest_rejected(tmp_path):
    bad = CodeProposal('proposal.x', MID, '0', 'summary', (), usage={})
    *_, plan, adapter = flow(tmp_path, proposal=bad)
    with pytest.raises(NexusMissionReadAdapterError, match='context digest'):
        adapter.inspect(plan, step_id='step.engineering')


def test_proposal_with_mutable_usage_receipt_stale(tmp_path):
    proposal = CodeProposal('proposal.x', MID, H, 'summary', (), usage={'total_tokens': 5})
    *_, plan, adapter = flow(tmp_path, proposal=proposal)
    receipt = adapter.inspect(plan, step_id='step.engineering').to_dict()
    proposal.usage['total_tokens'] = 6
    assert not adapter.verify_receipt(receipt, plan)


def test_registry_update_invalidates_workspace_and_engineering_reads(tmp_path):
    r, _, _, plan, adapter = flow(tmp_path)
    for step in ['step.workspace','step.engineering']:
        receipt = adapter.inspect(plan, step_id=step).to_dict()
        assert adapter.verify_receipt(receipt, plan)
    r.assign_component(MID, component='trust', actor_id='team.demo',
                       at_utc='2026-10-28T10:00:10Z')
    for step in ['step.workspace','step.engineering']:
        with pytest.raises(NexusMissionReadAdapterError):
            adapter.inspect(plan, step_id=step)


def test_tampered_plan_rejected(tmp_path):
    *_, plan, adapter = flow(tmp_path)
    raw = plan.to_dict()
    raw['handoffs'][0]['component'] = 'trust'
    with pytest.raises(NexusMissionReadAdapterError):
        adapter.inspect(raw, step_id='step.workspace')


def test_missing_step_and_effectful_workflow_rejected(tmp_path):
    *_, plan, adapter = flow(tmp_path)
    with pytest.raises(NexusMissionReadAdapterError):
        adapter.inspect(plan, step_id='step.nonexistent')
    with pytest.raises(NexusMissionReadAdapterError):
        adapter.inspect(plan, step_id='../evil')


def test_wrong_catalog_rejected(tmp_path):
    r, _, _, plan, adapter = flow(tmp_path)
    # A plan from the 5-port catalog is not compatible with the original 3-port catalog.
    from aegisforge.nexus.mission_control.mission_read_adapters import NexusMissionReadAdapters
    legacy = NexusMissionReadAdapters(r)
    with pytest.raises(NexusMissionReadAdapterError):
        legacy.inspect(plan, step_id='step.workspace')


def test_receipt_is_immutable_and_returns_detached_copy(tmp_path):
    *_, plan, adapter = flow(tmp_path)
    receipt = adapter.inspect(plan, step_id='step.engineering')
    with pytest.raises(FrozenInstanceError):
        receipt._json = b'changed'
    outside = receipt.to_dict()
    outside['summary']['edit_count'] = 99
    assert receipt.to_dict()['summary']['edit_count'] == 1


def test_workspace_provider_must_be_exact_type(tmp_path):
    class FakeWorkspace:
        pass
    with pytest.raises(NexusMissionReadAdapterError):
        flow(tmp_path, workspace=FakeWorkspace())


def test_engineering_provider_must_be_exact_type(tmp_path):
    with pytest.raises(NexusMissionReadAdapterError):
        flow(tmp_path, proposal={'edits': []})


def test_empty_proposal_is_valid_reviewable_metadata(tmp_path):
    proposal = CodeProposal('proposal.x', MID, H, 'insufficient context', (), usage={})
    *_, plan, adapter = flow(tmp_path, proposal=proposal)
    d = adapter.inspect(plan, step_id='step.engineering').to_dict()
    assert d['summary']['edit_count'] == 0
    assert d['summary']['needs_review'] is False


@pytest.mark.parametrize('product', ['nexus', 'aegisgrid', 'live_forge', 'colossus'])
def test_supported_product(tmp_path, product):
    *_, plan, adapter = flow(tmp_path, product=product)
    assert adapter.inspect(plan, step_id='step.workspace').to_dict()['product'] == product


def test_input_workflow_dependency_preserved(tmp_path):
    *_, plan, adapter = flow(tmp_path)
    data = plan.to_dict()
    assert next(h for h in data['handoffs'] if h['step_id']=='step.engineering')['depends_on'] == ['step.workspace']
    assert adapter.inspect(plan, step_id='step.engineering').to_dict()['authorization_granted'] is False


def test_replay_same_sources_deterministic(tmp_path):
    *_, plan, adapter = flow(tmp_path)
    a = adapter.inspect(plan, step_id='step.workspace').to_dict()
    b = adapter.inspect(plan, step_id='step.workspace').to_dict()
    assert a == b


def test_workspace_inventory_does_not_call_read_text(tmp_path, monkeypatch):
    *_, plan, adapter = flow(tmp_path)
    monkeypatch.setattr(NexusWorkspaceManager, 'read_text', lambda *a, **kw:
                        (_ for _ in ()).throw(AssertionError('source code read')))
    adapter.inspect(plan, step_id='step.workspace')


def test_engineering_view_does_not_invoke_model(tmp_path, monkeypatch):
    from aegisforge.nexus.engineering.code_proposer import NexusCodeProposer
    monkeypatch.setattr(NexusCodeProposer, 'propose', lambda *a, **kw:
                        (_ for _ in ()).throw(AssertionError('model invoked')))
    *_, plan, adapter = flow(tmp_path)
    adapter.inspect(plan, step_id='step.engineering')


def test_original_laboratory_source_still_supported(tmp_path):
    from aegisforge.nexus.lab.scenario_catalog import NexusScenarioCatalog, ScenarioCase, ScenarioExpectation
    case = NexusScenarioCatalog()
    case.register(ScenarioCase('case.example', product='nexus', category='functional',
        title='Demo', description='A fake experiment', inputs={'sample':'PRIVATE_ABC'},
        expectations=(ScenarioExpectation('ok','exists'),)))
    steps = (MissionWorkflowStep('step.lab', 'lab', 'summarize'),)
    r, _, _, _, _ = flow(tmp_path, steps=steps)
    checker = NexusMissionState(MissionStatePolicy(MID, 'colossus',
                          (MissionTransitionRule('ready'),)))
    preview = NexusMissionCoordinator(MissionCoordinationBlueprint(
        MID, 'colossus', 'ready', steps), checker).preview(r)
    adapter = Extended(r, scenarios=case)
    plan = NexusMissionBridge(adapter.catalog).plan(preview, r)
    receipt = adapter.inspect(plan, step_id='step.lab').to_dict()
    assert receipt['summary']['count'] == 1
    assert 'PRIVATE_ABC' not in json.dumps(receipt)


def test_effectful_workflow_does_not_pass_bridge_validation(tmp_path):
    steps = (
        MissionWorkflowStep('step.review', 'trust', 'review_action'),
        MissionWorkflowStep('step.effect', 'engineering', 'request_effect',
                            ('step.review',), H),
    )
    *_, plan, adapter = flow(tmp_path, steps=steps)
    assert plan.outcome == 'blocked'
    with pytest.raises(NexusMissionReadAdapterError):
        adapter.inspect(plan, step_id='step.effect')


def test_workspace_index_not_allowed_as_effective_operation(tmp_path):
    steps = (MissionWorkflowStep('step.index', 'workspace', 'index'),)
    *_, plan, adapter = flow(tmp_path, steps=steps)
    assert plan.outcome == 'blocked'
    with pytest.raises(NexusMissionReadAdapterError):
        adapter.inspect(plan, step_id='step.index')


def test_truncated_inventory_must_be_described_as_partial(tmp_path):
    from aegisforge.nexus.workspace.manager import WorkspaceLimits
    for i in range(5):
        (tmp_path / f'file_{i}.py').write_text('x=0')
    limits = WorkspaceLimits(max_files=2)
    workspace = NexusWorkspaceManager(tmp_path, limits=limits)
    *_, plan, adapter = flow(tmp_path, workspace=workspace)
    receipt = adapter.inspect(plan, step_id='step.workspace').to_dict()
    assert receipt['summary']['partial'] is True
    assert receipt['summary']['file_count'] <= 2
