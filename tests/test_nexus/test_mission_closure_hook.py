"""Windows-portable, no-network integration tests for supervised mission closure."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json

import pytest

from aegisforge.nexus.mission_control.mission_closure_hook import (
    NexusMissionClosureHook, NexusMissionClosureError,
    MISSION_CLOSURE_HOOK_SCHEMA,
)
from aegisforge.nexus.mission_control.mission_service import NexusMissionService
from aegisforge.nexus.mission_control.mission_state import (
    NexusMissionState, MissionStatePolicy, MissionTransitionRule,
)
from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_persistence import NexusMissionPersistence
from aegisforge.nexus.mission_control.mission_monitor_views import NexusMissionMonitorViews
from aegisforge.nexus.mission_control.mission_source_archive import NexusMissionSourceArchive
from aegisforge.nexus.mission_control.mission_checkpoint_monitor import NexusMissionCheckpointMonitor
from aegisforge.nexus.proof.proof_graph import NexusProofGraph

ACTOR = 'actor.synthetic'
MID = 'mission.closure.synthetic'


def dh(s):
    return sha256(s.encode()).hexdigest()


@pytest.fixture
def env(tmp_path):
    root = tmp_path / 'private'
    root.mkdir()
    registry = NexusMissionRegistry()
    registry.register(MID, product='nexus', owner_id='owner.synthetic',
                      objective_sha256=dh('purpose'), actor_id=ACTOR)
    registry.transition(MID, 'ready', actor_id=ACTOR)
    registry.transition(MID, 'running', actor_id=ACTOR)
    registry.link_reference(MID, kind='evidence', reference_sha256=dh('evidence'), actor_id=ACTOR)
    auth_calls = []
    def service_auth(actor, action, mission, anchor, req):
        auth_calls.append((actor, action, mission, deepcopy(req)))
        return actor == ACTOR
    service = NexusMissionService(registry, authorizer=service_auth)
    store = NexusMissionPersistence(root)
    store.save(registry)
    graph = NexusProofGraph(MID)
    first = graph.add_node('mission', MID, status='observed')
    second = graph.add_node('run', 'run.synthetic', status='completed', stage='execution')
    graph.add_edge('contains', first, second)
    views = NexusMissionMonitorViews(graphs={MID: graph})
    capture_calls = []
    def capture_auth(actor, action, ids, meta):
        capture_calls.append((actor, action, ids, deepcopy(meta)))
        return actor == ACTOR and ids == (MID,) and action == 'mission_sources.capture_closed_missions'
    state = NexusMissionState(MissionStatePolicy(
        MID, 'nexus', (MissionTransitionRule('reported_complete',
                  required_reference_kinds=('evidence',)),)))
    hook = NexusMissionClosureHook(service, store, views, capture_authorizer=capture_auth)
    return {'registry': registry, 'service': service, 'store': store, 'views': views,
            'state': state, 'hook': hook, 'root': root, 'service_calls': auth_calls,
            'capture_calls': capture_calls, 'capture_auth': capture_auth}


def plan(e):
    return e['hook'].prepare(MID, 'reported_complete', actor_id=ACTOR, state=e['state'])


def commit(e, p=None):
    return e['hook'].commit(p if p is not None else plan(e), actor_id=ACTOR, state=e['state'])


def archive(e):
    monitor = NexusMissionCheckpointMonitor(e['store'], e['store'].load())
    return NexusMissionSourceArchive(e['root'] / 'mission_sources.json', monitor)


def test_prepare_is_read_only(env):
    before = env['registry'].snapshot()
    p = plan(env)
    assert p['schema'] == MISSION_CLOSURE_HOOK_SCHEMA
    assert p['host_authorization_required'] is True
    assert p['authorization_granted'] is False
    assert p['effects_executed'] is False
    assert env['registry'].snapshot() == before
    assert not (env['root'] / 'mission_sources.json').exists()
    assert not env['capture_calls']


def test_full_closure_and_archive(env):
    p = plan(env)
    out = commit(env, p)
    assert out['outcome'] == 'completed'
    assert out['transition_recorded'] and out['checkpoint_saved'] and out['archive_saved']
    assert out['checkpoint_generation'] == 2
    assert env['registry'].get(MID)['status'] == 'reported_complete'
    assert env['store'].load().registry.get(MID)['status'] == 'reported_complete'
    assert archive(env).summary('graph', MID)['node_count'] == 2
    assert out['trust_permissions_restored'] is False
    assert out['effects_executed'] is False
    assert NexusMissionClosureHook.verify_receipt(out)
    assert env['capture_calls'][0][2] == (MID,)


def test_plan_is_bound_to_actor_and_single_use(env):
    p = plan(env)
    with pytest.raises(NexusMissionClosureError):
        env['hook'].commit(p, actor_id='actor.other', state=env['state'])
    with pytest.raises(NexusMissionClosureError):
        commit(env, p)
    assert env['registry'].get(MID)['status'] == 'running'


def test_plan_tamper_rejected(env):
    p = plan(env)
    p['target_status'] = 'verified'
    with pytest.raises(NexusMissionClosureError):
        commit(env, p)
    assert env['registry'].get(MID)['status'] == 'running'


def test_service_denial_leaves_checkpoint_and_registry(env):
    def deny(actor, action, mid, anchor, req):
        return action != 'transition' and actor == ACTOR
    env['service']._authorizer = deny
    out = commit(env)
    assert out['outcome'] == 'transition_failed'
    assert NexusMissionClosureHook.verify_receipt(out)
    assert env['registry'].get(MID)['status'] == 'running'
    assert env['store'].load().receipt.generation == 1


def test_capture_denial_preserves_closed_checkpoint(env):
    env['hook']._capture_authorizer = lambda *_: False
    out = commit(env)
    assert out['outcome'] == 'archive_failed'
    assert out['checkpoint_saved'] is True
    assert out['archive_saved'] is False
    assert NexusMissionClosureHook.verify_receipt(out)
    assert not (env['root'] / 'mission_sources.json').exists()
    assert env['registry'].get(MID)['status'] == 'reported_complete'
    assert env['store'].load().receipt.generation == 2


def test_archive_resume_requires_new_explicit_authorization(env):
    env['hook']._capture_authorizer = lambda *_: False
    out = commit(env)
    assert out['outcome'] == 'archive_failed'
    env['hook']._capture_authorizer = env['capture_auth']
    result = env['hook'].resume_archive(MID, actor_id=ACTOR)
    assert result['outcome'] == 'archive_resumed'
    assert archive(env).summary('graph', MID)['node_count'] == 2
    with pytest.raises(NexusMissionClosureError):
        env['hook'].resume_archive(MID, actor_id=ACTOR)


def test_checkpoint_denial_is_partial(env, monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError('synthetic disk outage')
    monkeypatch.setattr(env['store'], 'checkpoint_service', fail)
    out = commit(env)
    assert out['outcome'] == 'checkpoint_failed'
    assert out['transition_recorded'] is True
    assert out['checkpoint_saved'] is False
    assert env['registry'].get(MID)['status'] == 'reported_complete'
    assert env['store'].load().receipt.generation == 1
    assert NexusMissionClosureHook.verify_receipt(out)


def test_no_silent_resume_after_failed_checkpoint(env, monkeypatch):
    monkeypatch.setattr(env['store'], 'checkpoint_service', lambda *_a, **_k: (_ for _ in ()).throw(OSError('fail')))
    assert commit(env)['outcome'] == 'checkpoint_failed'
    with pytest.raises(NexusMissionClosureError):
        env['hook'].resume_archive(MID, actor_id=ACTOR)


def test_checkpoint_stale_blocks_before_transition(env):
    p = plan(env)
    env['registry'].assign_component(MID, component='proof', actor_id=ACTOR)
    with pytest.raises(NexusMissionClosureError):
        commit(env, p)
    assert env['registry'].get(MID)['status'] == 'running'


def test_missing_sources_deny(env):
    env['hook']._views = NexusMissionMonitorViews()
    with pytest.raises(NexusMissionClosureError):
        plan(env)


def test_wrong_graph_mission_deny(env):
    other = NexusProofGraph('mission.unrelated')
    with pytest.raises(ValueError):
        NexusMissionMonitorViews(graphs={MID: other})


def test_existing_archive_blocks_duplicate_transition(env):
    assert commit(env)['outcome'] == 'completed'
    with pytest.raises(NexusMissionClosureError):
        plan(env)


def test_source_mutation_deny(env):
    p = plan(env)
    g = env['views']._graphs[MID]
    g.add_node('evidence', 'evidence.synthetic', status='observed', stage='verification')
    with pytest.raises(NexusMissionClosureError):
        commit(env, p)
    assert env['registry'].get(MID)['status'] == 'running'


def test_checkpoint_drift_deny(env):
    p = plan(env)
    env['store'].save(env['registry'], expected_head_sha256=env['store'].load().receipt.envelope_sha256)
    # Idempotent save leaves head intact, so check a *different* valid generation
    env['registry'].assign_component(MID, component='trust', actor_id=ACTOR)
    env['store'].save(env['registry'], expected_head_sha256=env['store'].load().receipt.envelope_sha256)
    with pytest.raises(NexusMissionClosureError):
        commit(env, p)


@pytest.mark.parametrize('field,value', [
    ('effects_executed', True), ('trust_permissions_restored', True),
    ('authorization_granted_by_report', True), ('authenticity_verified', True),
    ('outcome', 'transition_failed'), ('checkpoint_saved', False),
    ('checkpoint_generation', 9), ('archive_sha256', '0'*64),
    ('receipt_sha256', '0'*64), ('mission_id', 'mission.other')
])
def test_receipt_tampering_rejected(env, field, value):
    receipt = commit(env)
    receipt[field] = value
    assert not NexusMissionClosureHook.verify_receipt(receipt)


def test_rehash_does_not_allow_unsupported_permission(env):
    import aegisforge.nexus.mission_control.mission_closure_hook as h
    report = commit(env)
    report['effects_executed'] = True
    report['receipt_sha256'] = h._digest({k:v for k,v in report.items() if k!='receipt_sha256'})
    assert not NexusMissionClosureHook.verify_receipt(report)


def test_wrong_actor_prepare_rejected(env):
    with pytest.raises(NexusMissionClosureError):
        env['hook'].prepare(MID, 'reported_complete', actor_id='bad', state=env['state'])


def test_wrong_target_rejected(env):
    with pytest.raises(NexusMissionClosureError):
        env['hook'].prepare(MID, 'running', actor_id=ACTOR, state=env['state'])


def test_missing_authorizer_rejected(env):
    with pytest.raises(NexusMissionClosureError):
        NexusMissionClosureHook(env['service'], env['store'], env['views'], capture_authorizer=None)


def test_two_prepare_same_state_rejected(env):
    plan(env)
    with pytest.raises(NexusMissionClosureError):
        plan(env)


def test_case_sensitive_paths_and_lf_files_windows_portable(env):
    # No symlinks or platform-specific newline translations in test fixtures.
    path = env['root'] / 'synthetic.txt'
    path.write_bytes(b'hello\n')
    assert path.read_bytes() == b'hello\n'
    assert commit(env)['outcome'] == 'completed'


def test_uses_service_policy_auth_not_raw_registry_transition(env):
    commit(env)
    actions = [r[1] for r in env['service_calls']]
    assert 'transition' in actions
    assert 'export_snapshot' in actions
    assert env['capture_calls']


def test_resume_wrong_actor_denied(env):
    env['hook']._capture_authorizer = lambda *_: False
    assert commit(env)['outcome'] == 'archive_failed'
    with pytest.raises(NexusMissionClosureError):
        env['hook'].resume_archive(MID, actor_id='bad')


def test_resume_archive_after_simulated_restart(env):
    env['hook']._capture_authorizer = lambda *_: False
    assert commit(env)['outcome'] == 'archive_failed'
    fresh_store = NexusMissionPersistence(env['root'])
    loaded = fresh_store.load()
    fresh_service = NexusMissionService(loaded.registry,
        authorizer=lambda actor, action, mid, anchor, req: actor == ACTOR)
    fresh_hook = NexusMissionClosureHook(fresh_service, fresh_store, env['views'],
                                         capture_authorizer=env['capture_auth'])
    receipt = fresh_hook.resume_archive(MID, actor_id=ACTOR)
    assert receipt['outcome'] == 'archive_resumed'
    assert archive(env).summary('graph', MID)['available'] is True


def test_archive_failure_after_publish_does_not_republish(env, monkeypatch):
    import aegisforge.nexus.mission_control.mission_closure_hook as mod
    original = mod.NexusMissionSourceCapture.commit
    def after_publish(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise OSError('synthetic host interruption after publish')
    monkeypatch.setattr(mod.NexusMissionSourceCapture, 'commit', after_publish)
    receipt = commit(env)
    assert receipt['outcome'] == 'archive_failed'
    assert (env['root'] / 'mission_sources.json').exists()
    with pytest.raises(NexusMissionClosureError):
        env['hook'].resume_archive(MID, actor_id=ACTOR)


def test_alter_archive_before_closure_denied(env):
    (env['root'] / 'mission_sources.json').write_bytes(b'{}\n')
    with pytest.raises(NexusMissionClosureError):
        plan(env)
    assert env['registry'].get(MID)['status'] == 'running'


def test_prepare_with_corrupt_checkpoint_denied(env):
    env['store']._primary.write_bytes(b'corrupt\n')
    with pytest.raises(NexusMissionClosureError):
        plan(env)
    assert env['registry'].get(MID)['status'] == 'running'


def test_missing_primary_with_previous_denied(env):
    env['store']._primary.rename(env['store']._previous)
    with pytest.raises(NexusMissionClosureError):
        plan(env)


def test_initial_checkpoint_absent_can_be_created(env):
    env['store']._primary.unlink()
    receipt = commit(env)
    assert receipt['outcome'] == 'completed'
    assert receipt['checkpoint_generation'] == 1


def test_no_second_commit_after_completion(env):
    p = plan(env)
    assert commit(env,p)['outcome'] == 'completed'
    with pytest.raises(NexusMissionClosureError):
        commit(env,p)


def test_source_wrong_mission_in_host_view_denied(env):
    # Source constructors forbid incorrect mission mappings themselves.
    with pytest.raises(Exception):
        NexusMissionMonitorViews(graphs={MID: NexusProofGraph('mission.other')})


def test_prepare_failed_does_not_require_host_capture_authorization(env):
    # Prepared plans may be inspected or discarded safely.
    plan(env)
    assert env['capture_calls'] == []
    assert env['registry'].get(MID)['status'] == 'running'


def test_report_excludes_secrets_and_raw_source(env):
    out = commit(env)
    text = json.dumps(out)
    assert 'token' not in text
    assert 'private' not in text
    assert 'owner.synthetic' not in text
    assert 'actor.synthetic' not in text


def test_resume_requires_matching_checkpoint_even_if_registry_still_closed(env):
    env['hook']._capture_authorizer = lambda *_: False
    assert commit(env)['outcome'] == 'archive_failed'
    env['registry'].assign_component(MID, component='trust', actor_id=ACTOR) if False else None
    # Registry is already closed, so mutate *another* registered mission instead.
    env['registry'].register('mission.separate', product='nexus', owner_id='owner.synthetic',
                             objective_sha256=dh('other'), actor_id=ACTOR)
    with pytest.raises(NexusMissionClosureError):
        env['hook'].resume_archive(MID, actor_id=ACTOR)


def test_stage_receipt_is_not_claimed_complete_on_checkpoint_failure(env,monkeypatch):
    monkeypatch.setattr(env['store'], 'checkpoint_service',
                       lambda *a, **k: (_ for _ in ()).throw(OSError('synthetic crash')))
    out = commit(env)
    assert out['requires_operator_reconciliation'] is True
    assert out['archive_saved'] is False
    assert out['checkpoint_envelope_sha256'] is None


def test_invalid_receipt_shape_even_after_rehash(env):
    import aegisforge.nexus.mission_control.mission_closure_hook as mod
    out = commit(env)
    out['checkpoint_saved'] = 'yes'
    out['receipt_sha256'] = mod._digest({k:v for k,v in out.items() if k!='receipt_sha256'})
    assert not NexusMissionClosureHook.verify_receipt(out)


def test_archive_success_is_a_separate_host_decision(env):
    commit(env)
    assert any(row[1] == 'transition' for row in env['service_calls'])
    assert any(row[1] == 'export_snapshot' for row in env['service_calls'])
    assert len(env['capture_calls']) == 1
    assert env['capture_calls'][0][3]['generation'] == 2


def test_prepare_blocked_without_reference(env):
    # Host state policy demands actual registered evaluation evidence.
    strict = NexusMissionState(MissionStatePolicy(MID, 'nexus', (
        MissionTransitionRule('reported_complete',
                              required_reference_kinds=('scorecard',)),)))
    with pytest.raises(NexusMissionClosureError):
        env['hook'].prepare(MID,'reported_complete',actor_id=ACTOR,state=strict)


def test_operator_can_reconcile_checkpoint_then_resume_capture(env, monkeypatch):
    original = env['store'].checkpoint_service
    monkeypatch.setattr(env['store'], 'checkpoint_service',
                        lambda *args, **kwargs: (_ for _ in ()).throw(OSError('transient')))
    assert commit(env)['outcome'] == 'checkpoint_failed'
    monkeypatch.setattr(env['store'], 'checkpoint_service', original)
    # Explicit operator inspection, authorized snapshot export, and checkpoint.
    env['store'].checkpoint_service(env['service'],actor_id=ACTOR,
        expected_head_sha256=env['store'].load().receipt.envelope_sha256)
    result=env['hook'].resume_archive(MID,actor_id=ACTOR)
    assert result['outcome'] == 'archive_resumed'
    assert archive(env).summary('graph',MID)['available']


def test_resumption_receipt_verifiable(env):
    env['hook']._capture_authorizer=lambda *_:False
    assert commit(env)['outcome']=='archive_failed'
    env['hook']._capture_authorizer=env['capture_auth']
    receipt=env['hook'].resume_archive(MID,actor_id=ACTOR)
    assert NexusMissionClosureHook.verify_resumption_receipt(receipt)
    receipt['effects_executed']=True
    assert not NexusMissionClosureHook.verify_resumption_receipt(receipt)


@pytest.mark.parametrize('terminal', ['failed','cancelled'])
def test_other_terminal_states_can_be_archived(env,terminal):
    # These are failure/cancellation records, not evidence of task success.
    policy=NexusMissionState(MissionStatePolicy(MID,'nexus',(
        MissionTransitionRule(terminal),)))
    plan=env['hook'].prepare(MID,terminal,actor_id=ACTOR,state=policy)
    result=env['hook'].commit(plan,actor_id=ACTOR,state=policy)
    assert result['outcome']=='completed'
    assert env['store'].load().registry.get(MID)['status']==terminal
    assert NexusMissionClosureHook.verify_receipt(result)


def test_service_transition_receives_specific_status(env):
    commit(env)
    request=[x[3] for x in env['service_calls'] if x[1]=='transition'][0]
    assert request['target_status']=='reported_complete'
    assert request['decision_sha256'] is None


def test_host_denied_checkpoint_export_after_transition(env):
    before=[0]
    original=env['service']._authorizer
    def auth(actor,action,mission,anchor,req):
        if action=='transition':
            before[0]=1
        if action=='export_snapshot' and before[0]:
            return False
        return original(actor,action,mission,anchor,req)
    p=plan(env)
    env['service']._authorizer=auth
    result=commit(env,p)
    assert result['outcome']=='checkpoint_failed'
    assert result['transition_recorded'] is True
    assert NexusMissionClosureHook.verify_receipt(result)
