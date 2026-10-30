"""Offline integration tests for checkpoint-bound, explicit source captures."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_persistence import NexusMissionPersistence
from aegisforge.nexus.mission_control.mission_checkpoint_monitor import NexusMissionCheckpointMonitor
from aegisforge.nexus.mission_control.mission_monitor_views import NexusMissionMonitorViews
from aegisforge.nexus.mission_control.mission_source_archive import NexusMissionSourceArchive, NexusMissionSourceArchiveError
from aegisforge.nexus.mission_control.mission_source_capture import (
    MISSION_SOURCE_CAPTURE_SCHEMA, NexusMissionSourceCapture,
    NexusMissionSourceCaptureError,
)
from aegisforge.nexus.proof.proof_graph import NexusProofGraph

ID = 'mission.capture.demo'
SECOND = 'mission.capture.secondary'
ACTOR = 'actor.capture'


def digest(text):
    return sha256(text.encode()).hexdigest()


def register(registry, mid, product='nexus', state='reported_complete'):
    registry.register(mid, product=product, owner_id='owner.demo',
                      objective_sha256=digest(mid), actor_id=ACTOR)
    if state == 'draft':
        return
    if state == 'cancelled':
        registry.transition(mid, 'cancelled', actor_id=ACTOR)
        return
    registry.transition(mid, 'ready', actor_id=ACTOR)
    if state == 'ready':
        return
    registry.transition(mid, 'running', actor_id=ACTOR)
    if state == 'running':
        return
    if state == 'reported_complete':
        registry.link_reference(mid, kind='evidence', reference_sha256=digest('source:' + mid), actor_id=ACTOR)
        registry.transition(mid, 'reported_complete', actor_id=ACTOR)
    elif state == 'failed':
        registry.transition(mid, 'failed', actor_id=ACTOR)
    else:
        raise ValueError(state)


def graph(mid):
    g = NexusProofGraph(mid)
    a = g.add_node('mission',mid,status='observed')
    b = g.add_node('run','run.demo',status='completed',stage='execution')
    g.add_edge('contains',a,b)
    return g


@pytest.fixture
def scenario(tmp_path):
    root=tmp_path/'private'
    root.mkdir()
    registry=NexusMissionRegistry()
    register(registry,ID)
    store=NexusMissionPersistence(root)
    store.save(registry)
    views=NexusMissionMonitorViews(graphs={ID:graph(ID)})
    calls=[]
    def grant(actor,action,ids,anchor):
        calls.append((actor,action,ids,anchor))
        return True
    capture=NexusMissionSourceCapture(store,views,authorizer=grant)
    return root,registry,store,views,capture,calls


def prepare(scenario):
    return scenario[4].prepare([ID], actor_id=ACTOR)


def commit(scenario):
    return scenario[4].commit(prepare(scenario),actor_id=ACTOR)


def monitor(scenario):
    s=scenario[2]
    return NexusMissionCheckpointMonitor(s,s.load())


def archive(scenario):
    return NexusMissionSourceArchive(scenario[0]/'mission_sources.json',monitor(scenario))


def test_prepare_no_disk_write_or_authorizer(scenario):
    p=prepare(scenario)
    assert p['schema']==MISSION_SOURCE_CAPTURE_SCHEMA
    assert p['host_authorization_required'] is True
    assert p['authorization_granted'] is False
    assert p['trust_permissions_restored'] is False
    assert p['effects_executed'] is False
    assert len(p['plan_sha256'])==64
    assert not (scenario[0]/'mission_sources.json').exists()
    assert not scenario[5]


def test_commit_creates_loadable_archive(scenario):
    report=commit(scenario)
    a=archive(scenario)
    assert a.summary('graph',ID)['node_count']==2
    assert NexusMissionSourceCapture.verify_capture_report(report,monitor(scenario),a)
    assert report['source_counts']=={'graph':1,'laboratory':0,'trust':0}
    assert report['trust_permissions_restored'] is False
    assert report['authenticity_verified'] is False
    assert report['effects_executed'] is False


def test_exact_host_authorization_context(scenario):
    p=prepare(scenario)
    scenario[4].commit(p,actor_id=ACTOR)
    a,act,ids,ctx=scenario[5][0]
    assert a==ACTOR and act=='mission_sources.capture_closed_missions'
    assert ids==(ID,)
    assert ctx['envelope_sha256']==p['checkpoint_envelope_sha256']
    assert ctx['archive_sha256']==p['archive_sha256']


def test_report_unchanged_verifies(scenario):
    result=commit(scenario)
    assert NexusMissionSourceCapture.verify_capture_report(result,monitor(scenario),archive(scenario))


@pytest.mark.parametrize('field,wrong', [
    ('effects_executed', True), ('authenticity_verified',True),
    ('trust_permissions_restored',True), ('checkpoint_generation',999),
    ('mission_ids',['mission.other']),('archive_sha256','f'*64),
    ('host_authorization_checked',False),('source_counts',{'graph':100,'laboratory':0,'trust':0}),
    ('capture_report_sha256','0'*64),
])
def test_altered_report_rejected(scenario,field,wrong):
    report=commit(scenario)
    report[field]=wrong
    assert not NexusMissionSourceCapture.verify_capture_report(report,monitor(scenario),archive(scenario))


def test_modified_report_with_rehashed_json_rejected_structural(scenario):
    import aegisforge.nexus.mission_control.mission_source_capture as cap
    report=commit(scenario)
    report['effects_executed']=True
    report['capture_report_sha256']=cap._hash({k:v for k,v in report.items() if k!='capture_report_sha256'})
    assert not NexusMissionSourceCapture.verify_capture_report(report,monitor(scenario),archive(scenario))


def test_extra_report_field_rejected(scenario):
    report=commit(scenario)
    report['secret']='dont persist'
    assert not NexusMissionSourceCapture.verify_capture_report(report,monitor(scenario),archive(scenario))


def test_no_authorizer_denied(scenario):
    c=NexusMissionSourceCapture(scenario[2],scenario[3])
    p=c.prepare([ID],actor_id=ACTOR)
    with pytest.raises(NexusMissionSourceCaptureError,match='authorizer'):
        c.commit(p,actor_id=ACTOR)
    assert not (scenario[0]/'mission_sources.json').exists()


@pytest.mark.parametrize('answer', [False, None, 1, 'approved', [], {}])
def test_nonliteral_true_authorizer_denied(scenario,answer):
    c=NexusMissionSourceCapture(scenario[2],scenario[3],authorizer=lambda *args:answer)
    p=c.prepare([ID],actor_id=ACTOR)
    with pytest.raises(NexusMissionSourceCaptureError,match='denied'):
        c.commit(p,actor_id=ACTOR)
    assert not (scenario[0]/'mission_sources.json').exists()


def test_authorizer_throws_fail_closed(scenario):
    def crash(*args):
        raise RuntimeError('internal secret that should not escape')
    c=NexusMissionSourceCapture(scenario[2],scenario[3],authorizer=crash)
    p=c.prepare([ID],actor_id=ACTOR)
    with pytest.raises(NexusMissionSourceCaptureError,match='authorization failed'):
        c.commit(p,actor_id=ACTOR)
    assert not (scenario[0]/'mission_sources.json').exists()


def test_actor_mismatch_consumes_plan(scenario):
    p=prepare(scenario)
    with pytest.raises(NexusMissionSourceCaptureError,match='different capture operator'):
        scenario[4].commit(p,actor_id='actor.other')
    with pytest.raises(NexusMissionSourceCaptureError,match='already used'):
        scenario[4].commit(p,actor_id=ACTOR)


def test_plan_reuse_rejected(scenario):
    p=prepare(scenario)
    scenario[4].commit(p,actor_id=ACTOR)
    with pytest.raises(NexusMissionSourceCaptureError):
        scenario[4].commit(p,actor_id=ACTOR)


def test_plan_altered_rejected(scenario):
    p=prepare(scenario)
    p['source_counts']['graph']=999
    with pytest.raises(NexusMissionSourceCaptureError):
        scenario[4].commit(p,actor_id=ACTOR)


def test_plan_does_not_expose_raw_nodes(scenario):
    p=prepare(scenario)
    assert 'nodes' not in json.dumps(p)
    assert 'owner.demo' not in json.dumps(p)


def test_missing_or_incomplete_mission_rejected(scenario):
    with pytest.raises(NexusMissionSourceCaptureError):
        scenario[4].prepare(['mission.not.found'],actor_id=ACTOR)


@pytest.mark.parametrize('state', ['draft','ready','running'])
def test_nonclosed_mission_denied(tmp_path,state):
    reg=NexusMissionRegistry()
    register(reg,ID,state=state)
    store=NexusMissionPersistence(tmp_path)
    store.save(reg)
    c=NexusMissionSourceCapture(store,NexusMissionMonitorViews(graphs={ID:graph(ID)}), authorizer=lambda *args:True)
    with pytest.raises(NexusMissionSourceCaptureError,match='closed'):
        c.prepare([ID],actor_id=ACTOR)


@pytest.mark.parametrize('state', ['cancelled','failed','reported_complete'])
def test_closing_states_accepted(tmp_path,state):
    reg=NexusMissionRegistry()
    register(reg,ID,state=state)
    store=NexusMissionPersistence(tmp_path)
    store.save(reg)
    c=NexusMissionSourceCapture(store,NexusMissionMonitorViews(graphs={ID:graph(ID)}),authorizer=lambda *args:True)
    result=c.commit(c.prepare([ID],actor_id=ACTOR),actor_id=ACTOR)
    assert result['source_counts']['graph']==1


def test_no_sources_rejected(scenario):
    c=NexusMissionSourceCapture(scenario[2],NexusMissionMonitorViews(),authorizer=lambda *args:True)
    with pytest.raises(NexusMissionSourceCaptureError,match='no verified display sources'):
        c.prepare([ID],actor_id=ACTOR)


def test_source_changes_before_authorization_rejected(scenario):
    p=prepare(scenario)
    scenario[3]._graphs[ID].add_node('evidence','later',status='observed')
    with pytest.raises(NexusMissionSourceCaptureError,match='sources changed'):
        scenario[4].commit(p,actor_id=ACTOR)
    assert not scenario[5]


def test_source_changes_inside_authorizer_rejected(scenario):
    def mutate(*args):
        scenario[3]._graphs[ID].add_node('evidence','late',status='observed')
        return True
    c=NexusMissionSourceCapture(scenario[2],scenario[3],authorizer=mutate)
    p=c.prepare([ID],actor_id=ACTOR)
    with pytest.raises(NexusMissionSourceCaptureError,match='during host authorization'):
        c.commit(p,actor_id=ACTOR)
    assert not (scenario[0]/'mission_sources.json').exists()


def test_store_changed_after_prepare_rejected(scenario):
    p=prepare(scenario)
    register(scenario[1], 'mission.new.event', state='draft')
    old=scenario[2].load().receipt.envelope_sha256
    scenario[2].save(scenario[1],expected_head_sha256=old)
    with pytest.raises(NexusMissionSourceCaptureError):
        scenario[4].commit(p,actor_id=ACTOR)


def test_archive_existing_rejected(scenario):
    commit(scenario)
    with pytest.raises(NexusMissionSourceCaptureError,match='already exists'):
        scenario[4].prepare([ID],actor_id=ACTOR)


def test_archive_changed_after_commit_is_detected(scenario):
    report=commit(scenario)
    a=archive(scenario)
    path=scenario[0]/'mission_sources.json'
    path.write_bytes(b'changed')
    assert not NexusMissionSourceCapture.verify_capture_report(report,monitor(scenario),a)


def test_checkpoint_changed_after_commit_is_detected(scenario):
    report=commit(scenario)
    a=archive(scenario)
    register(scenario[1], 'mission.new.event', state='draft')
    old=scenario[2].load().receipt.envelope_sha256
    scenario[2].save(scenario[1],expected_head_sha256=old)
    assert not NexusMissionSourceCapture.verify_capture_report(report,monitor(scenario),a)


def test_two_missions_captured(tmp_path):
    reg=NexusMissionRegistry()
    register(reg,ID)
    register(reg,SECOND,product='aegisgrid',state='failed')
    store=NexusMissionPersistence(tmp_path)
    store.save(reg)
    views=NexusMissionMonitorViews(graphs={ID:graph(ID),SECOND:graph(SECOND)})
    c=NexusMissionSourceCapture(store,views,authorizer=lambda *args:True)
    plan=c.prepare([SECOND,ID],actor_id=ACTOR)
    assert plan['mission_ids']==[ID,SECOND]
    report=c.commit(plan,actor_id=ACTOR)
    a=NexusMissionSourceArchive(tmp_path/'mission_sources.json',NexusMissionCheckpointMonitor(store,store.load()))
    assert a.summary('graph',SECOND) is not None
    assert report['source_counts']['graph']==2


def test_duplicate_ids_rejected(scenario):
    with pytest.raises(NexusMissionSourceCaptureError,match='duplicate'):
        scenario[4].prepare([ID,ID],actor_id=ACTOR)


@pytest.mark.parametrize('bad',[[],(),None,'mission.capture.demo',["../foo"], [1], ['A_B'], [ID]*129])
def test_invalid_selections_rejected(scenario,bad):
    with pytest.raises(NexusMissionSourceCaptureError):
        scenario[4].prepare(bad,actor_id=ACTOR)


@pytest.mark.parametrize('bad',['', 'Actor.Admin', 'a', 'x y', None])
def test_invalid_actors_rejected(scenario,bad):
    with pytest.raises(NexusMissionSourceCaptureError):
        scenario[4].prepare([ID],actor_id=bad)


def test_plan_limit(scenario):
    c=NexusMissionSourceCapture(scenario[2],scenario[3],authorizer=lambda *args:True,max_plans=1)
    c.prepare([ID],actor_id=ACTOR)
    with pytest.raises(NexusMissionSourceCaptureError,match='budget'):
        c.prepare([ID],actor_id='actor.other')


def test_duplicate_plan_blocked(scenario):
    prepare(scenario)
    with pytest.raises(NexusMissionSourceCaptureError,match='already prepared'):
        prepare(scenario)


def test_preexisting_archive_not_overwritten(scenario):
    p=prepare(scenario)
    path=scenario[0]/'mission_sources.json'
    path.write_bytes(b'preexisting')
    with pytest.raises(NexusMissionSourceCaptureError):
        scenario[4].commit(p,actor_id=ACTOR)
    assert path.read_bytes()==b'preexisting'


def test_lockfile_blocks_without_mutating(scenario):
    p=prepare(scenario)
    path=scenario[0]/'.mission_sources.capture.lock'
    path.write_text('host owns this lock',encoding='utf-8')
    with pytest.raises(NexusMissionSourceCaptureError,match='atomic'):
        scenario[4].commit(p,actor_id=ACTOR)
    assert path.read_text()=='host owns this lock'
    assert not (scenario[0]/'mission_sources.json').exists()


def test_publish_failure_cleans_temp(scenario,monkeypatch):
    import aegisforge.nexus.mission_control.mission_source_capture as src
    p=prepare(scenario)
    def fail(*args,**kwargs):
        raise OSError('simulated error')
    monkeypatch.setattr(src.os,'replace',fail)
    with pytest.raises(NexusMissionSourceCaptureError,match='atomic'):
        scenario[4].commit(p,actor_id=ACTOR)
    assert not list(scenario[0].glob('.mission_sources.*.tmp'))
    assert not (scenario[0]/'.mission_sources.capture.lock').exists()
    assert not (scenario[0]/'mission_sources.json').exists()


def test_concurrent_attempt_same_plan_one_writer(scenario):
    p=prepare(scenario)
    def run(_):
        try:
            scenario[4].commit(deepcopy(p),actor_id=ACTOR)
            return 'ok'
        except NexusMissionSourceCaptureError:
            return 'blocked'
    with ThreadPoolExecutor(max_workers=2) as executor:
        result=list(executor.map(run,range(2)))
    assert sorted(result)==['blocked','ok']


def test_hardlink_existing_destination_is_not_overwritten(scenario):
    path=scenario[0]/'mission_sources.json'
    path.write_text('protected',encoding='utf-8')
    with pytest.raises(NexusMissionSourceCaptureError):
        prepare(scenario)
    assert path.read_text()=='protected'


def test_no_secret_in_disk_output(scenario):
    commit(scenario)
    raw=(scenario[0]/'mission_sources.json').read_text()
    assert 'owner.demo' not in raw
    assert 'actor.capture' not in raw
    assert 'token' not in raw
    assert 'approved' not in raw or 'authenticity_verified' in raw


def test_checkpoint_generation_pinned(scenario):
    p=prepare(scenario)
    assert p['checkpoint_generation']==1
    result=scenario[4].commit(p,actor_id=ACTOR)
    assert result['checkpoint_generation']==1


def test_nonexistent_checkpoint_fails(tmp_path):
    store=NexusMissionPersistence(tmp_path)
    c=NexusMissionSourceCapture(store,NexusMissionMonitorViews(),authorizer=lambda *args:True)
    with pytest.raises(NexusMissionSourceCaptureError,match='checkpoint'):
        c.prepare([ID],actor_id=ACTOR)


def test_host_metadata_mutation_does_not_change_plan(scenario):
    def grant(actor,operation,ids,metadata):
        metadata['generation']=999
        metadata['source_counts']['graph']=100
        return True
    c=NexusMissionSourceCapture(scenario[2],scenario[3],authorizer=grant)
    report=c.commit(c.prepare([ID],actor_id=ACTOR),actor_id=ACTOR)
    assert report['checkpoint_generation']==1
    assert report['source_counts']['graph']==1


def test_archive_reader_summary_after_restart(scenario):
    commit(scenario)
    store=NexusMissionPersistence(scenario[0])
    mon=NexusMissionCheckpointMonitor(store,store.load())
    loaded=NexusMissionSourceArchive(scenario[0]/'mission_sources.json',mon)
    assert loaded.summary('graph',ID)['archived_metadata_only'] is True
    assert loaded.summary('trust',ID) is None


def test_new_instance_does_not_recover_old_plan(scenario):
    p=prepare(scenario)
    new=NexusMissionSourceCapture(scenario[2],scenario[3],authorizer=lambda *args:True)
    with pytest.raises(NexusMissionSourceCaptureError):
        new.commit(p,actor_id=ACTOR)


def test_report_invalid_types_return_false(scenario):
    commit(scenario)
    assert not NexusMissionSourceCapture.verify_capture_report(None,monitor(scenario),archive(scenario))
    assert not NexusMissionSourceCapture.verify_capture_report({},monitor(scenario),archive(scenario))


def test_archives_are_integrity_only(scenario):
    commit(scenario)
    d=archive(scenario).summary('graph',ID)
    assert d['integrity_only'] is True
    assert d['authenticity_verified'] is False
    assert d['archived_metadata_only'] is True


@pytest.mark.parametrize('field,wrong', [
    ('mission_ids', ['mission.unrelated']),
    ('source_counts', {'graph': 0, 'laboratory': 1, 'trust': 0}),
])
def test_rehashed_semantically_false_report_rejected(scenario, field, wrong):
    import aegisforge.nexus.mission_control.mission_source_capture as src
    report=commit(scenario)
    report[field]=wrong
    report['capture_report_sha256']=src._hash({k:v for k,v in report.items()
                                              if k!='capture_report_sha256'})
    assert not NexusMissionSourceCapture.verify_capture_report(report,monitor(scenario),archive(scenario))


def test_demo_captures_graph_lab_trust_after_close():
    from importlib.util import module_from_spec, spec_from_file_location
    path=Path(__file__).resolve().parents[2]/'scripts/nexus/demo_mission_source_capture.py'
    spec=spec_from_file_location('capture_demo_test',path)
    module=module_from_spec(spec)
    spec.loader.exec_module(module)
    data=module.demo()
    assert data['archive_verified'] is True
    assert data['graph_archived'] is True
    assert data['laboratory_archived'] is True
    assert data['trust_archived_historical_only'] is True
    assert data['mission_status']=='reported_complete'
    assert data['effects_executed'] is False
    assert data['authorization_restored'] is False
    assert data['external_services_called'] is False
