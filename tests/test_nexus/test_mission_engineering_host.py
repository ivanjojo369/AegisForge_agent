"""Synthetic tests for read-only Mission Engineering Host; no Nebius or Docker."""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from aegisforge.nexus.nebius_client import NebiusReply
from aegisforge.nexus.planner import NexusPlanner
from aegisforge.nexus.executor import NexusExecutor
from aegisforge.nexus.orchestrator import NexusOrchestrator
from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_service import NexusMissionService
from aegisforge.nexus.mission_control.mission_orchestrator_bridge import (
    NexusMissionOrchestratorBridge, verify_bridge_receipt,
)
from aegisforge.nexus.mission_control.mission_engineering_host import (
    NexusMissionEngineeringHost, NexusMissionEngineeringHostError,
    ENGINEERING_HOST_SCHEMA,
)
from aegisforge.nexus.proof.proof_graph import NexusProofGraph

ACTOR, MISSION = 'test.actor', 'mission.test'


class OfflineClient:
    def __init__(self):
        self.calls = []
        self.fail = False

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError('simulated model unavailable')
        return NebiusReply(
            content=json.dumps({
                'goal': 'Review repository',
                'steps': [
                    {'id': 's1', 'kind': 'reason', 'title': 'Plan',
                     'description': 'Review a synthetic file', 'depends_on': [],
                     'success_criteria': 'Reasoning captured', 'tool': None},
                    {'id': 's2', 'kind': 'verify', 'title': 'Check',
                     'description': 'Request independent verification',
                     'depends_on': ['s1'], 'success_criteria': 'Limitations recorded',
                     'tool': None},
                ],
                'assumptions': [], 'risks': [],
            }), model='offline-fixture', request_id='offline-sample')


def make_host(tmp_path, *, workspace_allow=True, planning_allow=False,
              mission_allow=True, client=None):
    root = tmp_path / 'repository'
    root.mkdir(exist_ok=True)
    (root / 'math.py').write_text('def add(a, b):\n    return a - b\n', encoding='utf-8')
    (root / 'extra.js').write_text('export const greet = () => "hello";\n', encoding='utf-8')
    client = client or OfflineClient()
    service = NexusMissionService(
        NexusMissionRegistry(),
        authorizer=lambda actor, action, mid, anchor, request: (
            mission_allow(actor, action, mid) if callable(mission_allow) else mission_allow),
    )
    service.create_mission(MISSION, product='nexus', owner_id='test.owner',
                           objective_sha256=sha256(b'fixture').hexdigest(),
                           actor_id=ACTOR,
                           expected_registry_sha256=service.registry_version(actor_id=ACTOR))
    orchestrator = NexusOrchestrator(NexusPlanner(client), NexusExecutor())
    bridge = NexusMissionOrchestratorBridge(
        service, orchestrator,
        planning_authorizer=(lambda *args: planning_allow) if planning_allow is not None else None,
    )
    host = NexusMissionEngineeringHost(
        bridge, workspace_root=root,
        workspace_authorizer=lambda actor, mission, root_sha, path_sha: (
            workspace_allow(actor, mission, root_sha, path_sha)
            if callable(workspace_allow) else workspace_allow),
    )
    return host, orchestrator, client, root, service


def stage(host, target='math.py', task='Fix addition'):
    return host.inspect(MISSION, actor_id=ACTOR, task=task, target_file=target)['session_id']


def bind(host, orchestrator, sid):
    return host.bind_prepared(sid, actor_id=ACTOR,
                              prepared=orchestrator.prepare('offline synthetic'))


def err(host, sid, fragment):
    with pytest.raises(NexusMissionEngineeringHostError, match=fragment):
        host.preview(sid, actor_id=ACTOR)


def test_full_offline_read_only_roundtrip(tmp_path):
    host, orch, client, root, _ = make_host(tmp_path)
    before = {p.name:p.read_bytes() for p in root.iterdir()}
    view = host.inspect(MISSION, actor_id=ACTOR,
                        task='Fix addition', target_file='math.py')
    assert view['schema'] == ENGINEERING_HOST_SCHEMA
    assert view['index_complete'] and view['indexed_files'] == 2
    assert view['stage'] == 'inspected'
    assert view['repository_file_contents_uploaded'] is False
    assert view['effects_executed'] is False
    bound = bind(host, orch, view['session_id'])
    assert bound['stage'] == 'bound' and bound['bridge_phase'] == 'bound'
    for _ in range(2):
        preview = host.preview(view['session_id'], actor_id=ACTOR)
        assert verify_bridge_receipt(preview) and preview['phase'] == 'previewed'
    finished = host.finish_offline(view['session_id'], actor_id=ACTOR)
    assert verify_bridge_receipt(finished)
    assert finished['verification_status'] == 'inconclusive'
    assert finished['tools_invoked'] == 0 and not finished['effects_executed']
    proof = host.proof_snapshot(view['session_id'], actor_id=ACTOR)
    assert NexusProofGraph.verify_snapshot(proof)
    result = host.link_evidence(view['session_id'], actor_id=ACTOR)
    assert result['reference_count'] == 1
    assert host.status(view['session_id'], actor_id=ACTOR)['stage'] == 'evidence_linked'
    assert {p.name:p.read_bytes() for p in root.iterdir()} == before
    assert len(client.calls) == 1  # host never makes its own model call


def test_no_workspace_access_without_explicit_authorizer(tmp_path):
    host, orch, _, root, _ = make_host(tmp_path, workspace_allow=False)
    with pytest.raises(NexusMissionEngineeringHostError, match='authorization denied'):
        stage(host)


def test_workspace_authorizer_errors_are_controlled(tmp_path):
    def deny(*args): raise RuntimeError('sensitive error')
    host, _, _, _, _ = make_host(tmp_path, workspace_allow=deny)
    with pytest.raises(NexusMissionEngineeringHostError, match='authorization unavailable'):
        stage(host)


def test_workspace_authorizer_receives_hashes_not_raw_path(tmp_path):
    checks = []
    def permit(*args):
        checks.append(args)
        return True
    host, _, _, root, _ = make_host(tmp_path, workspace_allow=permit)
    stage(host)
    assert checks[0][:2] == (ACTOR, MISSION)
    assert len(checks[0][2]) == len(checks[0][3]) == 64
    assert checks[0][3] == sha256(b'math.py').hexdigest()


@pytest.mark.parametrize('bad', ['../x.py', '/tmp/x.py', 'C:/outside.py', '', '..\\x.py'])
def test_unsafe_paths_rejected(tmp_path, bad):
    host, _, _, _, _ = make_host(tmp_path)
    with pytest.raises(NexusMissionEngineeringHostError):
        stage(host, target=bad)


def test_secret_file_cannot_be_inspected(tmp_path):
    host, _, _, root, _ = make_host(tmp_path)
    (root / '.env').write_text('NEBIUS_API_KEY=secret')
    with pytest.raises(NexusMissionEngineeringHostError, match='inspection unavailable'):
        stage(host, target='.env')


def test_missing_file_rejected(tmp_path):
    host, _, _, _, _ = make_host(tmp_path)
    with pytest.raises(NexusMissionEngineeringHostError):
        stage(host, target='missing.py')


def test_file_changed_before_binding_rejected(tmp_path):
    host, orch, _, root, _ = make_host(tmp_path)
    sid = stage(host)
    (root / 'math.py').write_text('def add(a,b):return a+b\n')
    with pytest.raises(NexusMissionEngineeringHostError, match='repository changed'):
        bind(host, orch, sid)


def test_other_indexed_source_changed_rejected(tmp_path):
    host, orch, _, root, _ = make_host(tmp_path)
    sid = stage(host)
    (root / 'extra.js').write_text('export const changed=1;\n')
    with pytest.raises(NexusMissionEngineeringHostError, match='repository changed'):
        bind(host, orch, sid)


def test_file_deleted_before_preview_fails_closed(tmp_path):
    host, orch, _, root, _ = make_host(tmp_path)
    sid = stage(host)
    bind(host, orch, sid)
    (root / 'math.py').unlink()
    with pytest.raises(NexusMissionEngineeringHostError, match='inspection unavailable'):
        host.preview(sid, actor_id=ACTOR)


def test_cannot_bind_twice(tmp_path):
    host, orch, _, _, _ = make_host(tmp_path)
    sid = stage(host)
    bind(host, orch, sid)
    with pytest.raises(NexusMissionEngineeringHostError, match='already bound'):
        bind(host, orch, sid)


def test_foreign_plan_rejected(tmp_path):
    host, _, _, _, _ = make_host(tmp_path)
    sid = stage(host)
    other = NexusOrchestrator(NexusPlanner(OfflineClient()), NexusExecutor())
    with pytest.raises(NexusMissionEngineeringHostError, match='binding rejected'):
        bind(host, other, sid)


def test_unknown_session_and_actor_mismatch(tmp_path):
    host, _, _, _, _ = make_host(tmp_path)
    sid = stage(host)
    with pytest.raises(NexusMissionEngineeringHostError, match='unknown host session'):
        host.status('f'*32, actor_id=ACTOR)
    with pytest.raises(NexusMissionEngineeringHostError, match='actor mismatch'):
        host.status(sid, actor_id='other.actor')


def test_requires_plan_to_preview_or_complete(tmp_path):
    host, _, _, _, _ = make_host(tmp_path)
    sid = stage(host)
    err(host, sid, 'no active plan')
    with pytest.raises(NexusMissionEngineeringHostError, match='not ready'):
        host.finish_offline(sid, actor_id=ACTOR)


def test_no_proof_until_complete(tmp_path):
    host, orch, _, _, _ = make_host(tmp_path)
    sid = stage(host)
    bind(host, orch, sid)
    with pytest.raises(NexusMissionEngineeringHostError, match='not completed'):
        host.proof_snapshot(sid, actor_id=ACTOR)


def test_plan_consumed_once(tmp_path):
    host, orch, _, _, _ = make_host(tmp_path)
    sid = stage(host);bind(host, orch, sid)
    host.finish_offline(sid, actor_id=ACTOR)
    with pytest.raises(NexusMissionEngineeringHostError, match='not ready'):
        host.finish_offline(sid, actor_id=ACTOR)


def test_metadata_export_requires_two_opt_ins(tmp_path):
    host, _, client, _, _ = make_host(tmp_path, planning_allow=True)
    sid = stage(host)
    for a,b in [(False,False),(True,False),(False,True)]:
        with pytest.raises(NexusMissionEngineeringHostError, match='opt-in'):
            host.prepare_plan(sid, actor_id=ACTOR, allow_model_call=a, allow_metadata_export=b)
    assert client.calls == []


def test_no_planning_policy_means_no_call(tmp_path):
    host, _, client, _, _ = make_host(tmp_path, planning_allow=None)
    sid = stage(host)
    with pytest.raises(NexusMissionEngineeringHostError, match='planning unavailable'):
        host.prepare_plan(sid, actor_id=ACTOR,
                          allow_model_call=True, allow_metadata_export=True)
    assert client.calls == []
    assert host.status(sid,actor_id=ACTOR)['stage']=='model_attempted'


def test_live_optin_with_fake_model_no_source_bytes(tmp_path):
    host, _, client, root, _ = make_host(tmp_path, planning_allow=True)
    sid = stage(host)
    result = host.prepare_plan(sid, actor_id=ACTOR, allow_model_call=True,
                               allow_metadata_export=True)
    assert result['stage'] == 'bound'
    payload=json.dumps(client.calls)
    assert 'return a - b' not in payload
    assert 'math.py' in payload
    assert host.preview(sid,actor_id=ACTOR)['phase']=='previewed'
    assert host.finish_offline(sid,actor_id=ACTOR)['tools_invoked']==0


def test_model_exception_no_automatic_retry(tmp_path):
    client=OfflineClient();client.fail=True
    host, _, _, _, _ = make_host(tmp_path, planning_allow=True, client=client)
    sid=stage(host)
    with pytest.raises(NexusMissionEngineeringHostError, match='planning unavailable'):
        host.prepare_plan(sid,actor_id=ACTOR,allow_model_call=True,allow_metadata_export=True)
    with pytest.raises(NexusMissionEngineeringHostError,match='already bound'):
        host.prepare_plan(sid,actor_id=ACTOR,allow_model_call=True,allow_metadata_export=True)
    assert len(client.calls)==1


def test_mission_version_change_fails(tmp_path):
    host, orch, _, _, svc = make_host(tmp_path)
    sid=stage(host)
    svc.assign_component(MISSION,component='workspace',actor_id=ACTOR,
                         expected_registry_sha256=svc.registry_version(actor_id=ACTOR))
    with pytest.raises(NexusMissionEngineeringHostError,match='mission changed'):
        bind(host,orch,sid)


def test_denied_mission_access_after_inspect(tmp_path):
    deny = {'enabled':False}
    host, orch, _, _, _ = make_host(tmp_path,
        mission_allow=lambda actor,action,mid: not(deny['enabled'] and action=='read'))
    sid=stage(host)
    deny['enabled']=True
    with pytest.raises(NexusMissionEngineeringHostError,match='mission access denied'):
        bind(host,orch,sid)


def test_link_denial_is_not_retried(tmp_path):
    deny={'link':False}
    host, orch, _, _, svc=make_host(tmp_path,
        mission_allow=lambda actor,action,mid: not(deny['link'] and action=='link_reference'))
    sid=stage(host);bind(host,orch,sid)
    host.finish_offline(sid,actor_id=ACTOR)
    deny['link']=True
    with pytest.raises(NexusMissionEngineeringHostError,match='host rejected'):
        host.link_evidence(sid,actor_id=ACTOR)
    deny['link']=False
    with pytest.raises(NexusMissionEngineeringHostError,match='no evidence available'):
        host.link_evidence(sid,actor_id=ACTOR)
    assert host.status(sid,actor_id=ACTOR)['stage']=='link_unconfirmed'


def test_source_with_syntax_error_reports_partial_index(tmp_path):
    host, _, _, root, _ = make_host(tmp_path)
    (root/'math.py').write_text('def ???\n')
    result=host.inspect(MISSION,actor_id=ACTOR,task='Diagnose syntax',target_file='math.py')
    assert result['index_complete'] is False
    assert result['index_warnings_count'] > 0


def test_no_source_written_and_no_automatic_link(tmp_path):
    host, orch, _, root, svc = make_host(tmp_path)
    sid=stage(host);bind(host,orch,sid)
    host.finish_offline(sid,actor_id=ACTOR)
    assert svc.get_mission(MISSION,actor_id=ACTOR)['reference_count']==0
    assert not (root/'patch.diff').exists()


def test_report_has_no_task_or_root_path(tmp_path):
    host, orch, _, root, _ = make_host(tmp_path)
    sid=stage(host,task='Confidential problem context')
    report=json.dumps(host.status(sid,actor_id=ACTOR))
    assert 'Confidential problem context' not in report
    assert str(root) not in report
    assert 'return a - b' not in report


def test_no_workspace_authorizer_constructor_rejected(tmp_path):
    host, orch, _, root, _ = make_host(tmp_path)
    with pytest.raises(NexusMissionEngineeringHostError,match='authorizer required'):
        NexusMissionEngineeringHost(host.bridge,workspace_root=root,workspace_authorizer=None)


def test_wrong_bridge_constructor_rejected(tmp_path):
    _, _, _, root, _ = make_host(tmp_path)
    with pytest.raises(NexusMissionEngineeringHostError,match='original bridge'):
        NexusMissionEngineeringHost(object(),workspace_root=root,workspace_authorizer=lambda *a:True)
