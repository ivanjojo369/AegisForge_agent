"""Archive dashboard: real persisted two-generation chain, read-only API and security.

All demonstrations are synthetic; no models, Docker or external services.
"""
from __future__ import annotations

import importlib.util
from hashlib import sha256
from pathlib import Path
import sys

import pytest
from starlette.testclient import TestClient

from aegisforge.nexus.mission_control.mission_archive_history import NexusMissionArchiveHistory
from aegisforge.nexus.mission_control.mission_checkpoint_monitor import NexusMissionCheckpointMonitor
from aegisforge.nexus.mission_control.mission_closure_hook import NexusMissionClosureHook
from aegisforge.nexus.mission_control.mission_closure_host import NexusMissionClosureHost
from aegisforge.nexus.mission_control.mission_history_view import NexusMissionHistoryView, NexusMissionHistoryViewError
from aegisforge.nexus.mission_control.mission_http_api import create_mission_http_app, NexusMissionHTTPAPIError
from aegisforge.nexus.mission_control.mission_monitor_views import NexusMissionMonitorViews, MonitorLabSource
from aegisforge.nexus.lab.scenario_catalog import NexusScenarioCatalog, ScenarioCase, ScenarioExpectation
from aegisforge.nexus.lab.evaluation_runner import NexusEvaluationRunner, EvaluationObservation
from aegisforge.nexus.lab.scorecard import NexusScorecard
from aegisforge.nexus.mission_control.mission_persistence import NexusMissionPersistence
from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_service import NexusMissionService
from aegisforge.nexus.mission_control.mission_state import NexusMissionState, MissionStatePolicy, MissionTransitionRule
from aegisforge.nexus.proof.proof_graph import NexusProofGraph

TOKEN = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
VIEWER = 'Wd7kj3N5T2xp0G9uZ4A6bv1R8YqC5Mf2LsEe3Hh'
M1, M2 = 'mission.alpha', 'mission.beta'
ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT/'scripts/nexus/serve_mission_http_api.py'


def digest(value):
    return sha256(value.encode()).hexdigest()


@pytest.fixture
def sample(tmp_path):
    folder = tmp_path/'checkpoint'
    folder.mkdir()
    actor='actor.local'
    registry=NexusMissionRegistry()
    graphs={}
    labs={}
    for mid, product in [(M1, 'nexus'), (M2, 'aegisgrid')]:
        registry.register(mid,product=product,owner_id='owner.synthetic',
                          objective_sha256=digest(mid),actor_id=actor)
        registry.transition(mid,'ready',actor_id=actor)
        registry.transition(mid,'running',actor_id=actor)
        registry.link_reference(mid,kind='evidence',reference_sha256=digest('evidence:'+mid),actor_id=actor)
        graph=NexusProofGraph(mid)
        n=graph.add_node('mission',mid,status='observed')
        r=graph.add_node('run','run.'+mid,status='completed',stage='execution')
        graph.add_edge('contains',n,r)
        graphs[mid]=graph
        catalog=NexusScenarioCatalog()
        sid='sample.'+product
        catalog.register(ScenarioCase(sid,product=product,category='functional',
             title='Synthetic validation',description='Inert test fixture',inputs={'fixed':True},
             expectations=(ScenarioExpectation('result.ok','equals',True),)))
        case=catalog.get(sid)
        runner=NexusEvaluationRunner(catalog,trusted_sources=('fixture',))
        observation=EvaluationObservation(sid,case.case_sha256,source_id='fixture',
             evidence_ref='synthetic/'+mid,payload={'result':{'ok':mid==M1}},duration_ms=1)
        ev=runner.evaluate(runner.prepare(),(observation,))
        engine=NexusScorecard()
        labs[mid]=MonitorLabSource(engine,engine.build(mid,(ev,)),(ev,))
    store=NexusMissionPersistence(folder)
    store.save(registry)
    auth=lambda a,action,mission,anchor,req: a==actor
    service=NexusMissionService(registry,authorizer=auth)
    views=NexusMissionMonitorViews(graphs=graphs,labs=labs)
    hook=NexusMissionClosureHook(service,store,views,
         capture_authorizer=lambda a,action,ids,ctx: a==actor and action=='mission_sources.capture_closed_missions')
    history=NexusMissionArchiveHistory(store, authorizer=lambda a,action,ctx: a==actor)
    host=NexusMissionClosureHost(hook,history)
    for mid,product,rotate in [(M1,'nexus',False),(M2,'aegisgrid',True)]:
        state=NexusMissionState(MissionStatePolicy(mid,product,(
            MissionTransitionRule('reported_complete',required_reference_kinds=('evidence',)),)))
        result=host.close(mid,'reported_complete',actor_id=actor,state=state,
             expected_registry_sha256=registry.snapshot()['registry_sha256'],
             rotate_prior_archive=rotate)
        assert result['outcome']=='completed'
    assert store.load().receipt.generation==3
    monitor=NexusMissionCheckpointMonitor(store,store.load())
    history_view=NexusMissionHistoryView(store,monitor)
    def policy(a,action,mid,anchor,metadata):
        if a=='actor.admin': return action in {'list','read','history','preview','inspect'}
        if a=='actor.viewer': return mid in {'*',M1} and action in {'list','read','history'}
        return False
    restarted_service=NexusMissionService(monitor._registry,authorizer=policy)
    app=create_mission_http_app(restarted_service,token_by_actor={'actor.admin':TOKEN,'actor.viewer':VIEWER},
                                checkpoint_monitor=monitor,history_view=history_view)
    return locals()


def client(app):
    return TestClient(app,base_url='http://127.0.0.1',client=('127.0.0.1',49000),raise_server_exceptions=False)


def get(c,path,token=TOKEN):
    return c.get(path,headers={'Authorization':'Bearer '+token})


def list_url(mid=M1):
    return f'/api/v1/missions/{mid}/archives'


def summary_url(mid=M1,generation=2,kind='graph'):
    return f'/api/v1/missions/{mid}/archives/{generation}/{kind}'


def test_two_generations_read_after_restart(sample):
    with client(sample['app']) as c:
        d=get(c,list_url()).json()['data']
        assert d['count']==1
        assert [r['generation'] for r in d['generations']]==[2]
        assert d['generations'][0]['kinds']==['graph','laboratory']
        assert d['lab_trend']==[{'generation':2,'evaluations':1,'passed':1,'failed':0,'inconclusive':0}]
        assert d['historical_only'] is True
        assert d['authorization_restored'] is False
        d=get(c,list_url(M2)).json()['data']
        assert [r['generation'] for r in d['generations']]==[3]
        assert get(c,summary_url(M2,3)).json()['data']['node_count']==2
        assert d['lab_trend'][0]['failed']==1


def test_history_view_no_global_ids(sample):
    with client(sample['app']) as c:
        text=get(c,list_url()).text
        assert M2 not in text
        assert str(sample['folder']) not in text
        assert 'mission_source_history' not in text
        text=get(c,list_url(M2)).text
        assert M1 not in text


def test_all_historical_report_fields_are_inert(sample):
    with client(sample['app']) as c:
        d=get(c,summary_url()).json()['data']
        assert d['archived_metadata_only'] is True
        assert d['historical_only'] is True
        assert d['authorization_granted'] is False
        assert d['effects_executed'] is False
        assert d['authenticity_verified'] is False
        assert d['verified_against_sources'] is False
        assert d['checkpoint_generation']==2


@pytest.mark.parametrize('url', [list_url(), summary_url(), summary_url(M2,3)])
def test_history_requires_token(sample,url):
    with client(sample['app']) as c:
        assert c.get(url).status_code==401


@pytest.mark.parametrize('url', [list_url(M2),summary_url(M2,3),summary_url(M2,3,'laboratory')])
def test_host_acl_blocks_other_mission(sample,url):
    with client(sample['app']) as c:
        r=get(c,url,VIEWER)
        assert r.status_code==403
        assert M2 not in r.text


def test_viewer_can_read_allowed_history(sample):
    with client(sample['app']) as c:
        assert get(c,list_url(),VIEWER).json()['data']['count']==1
        assert get(c,summary_url(),VIEWER).status_code==200


@pytest.mark.parametrize('endpoint', [list_url('BAD'),summary_url('BAD'), list_url('mission.unknown')])
def test_invalid_or_unknown_mission_fails_closed(sample,endpoint):
    with client(sample['app']) as c:
        assert get(c,endpoint).status_code in (403,404)


@pytest.mark.parametrize('suffix', ['0/graph','-1/graph','99999999999999999999/graph','2/unknown',
                                      '2/graph?token=bad','2/graph?foo=1'])
def test_invalid_archive_inputs_fail(sample,suffix):
    with client(sample['app']) as c:
        assert get(c,list_url()+'/'+suffix).status_code in (400,404)


def test_missing_generation_404_without_leaking(sample):
    with client(sample['app']) as c:
        r=get(c,summary_url(generation=1))
        assert r.status_code==404 and M2 not in r.text


def test_missing_kind_is_visible_absence(sample):
    with client(sample['app']) as c:
        d=get(c,summary_url(kind='trust')).json()['data']
        assert d['available'] is False
        assert d['reason']=='source_not_archived'
        assert d['historical_only'] is True


def test_absent_history_is_explicit(sample):
    svc=sample['restarted_service']
    monitor=sample['monitor']
    app=create_mission_http_app(svc,token_by_actor={'actor.admin':TOKEN},checkpoint_monitor=monitor)
    with client(app) as c:
        assert get(c,list_url()).json()['data']['reason']=='history_not_configured'
        assert get(c,summary_url()).json()['data']['reason']=='history_not_configured'


def test_history_requires_same_checkpoint(sample):
    with pytest.raises(NexusMissionHTTPAPIError):
        create_mission_http_app(sample['restarted_service'],token_by_actor={'actor.admin':TOKEN},
                 history_view=sample['history_view'])
    store=NexusMissionPersistence(sample['folder'])
    other=NexusMissionCheckpointMonitor(store,store.load())
    with pytest.raises(NexusMissionHTTPAPIError):
        create_mission_http_app(sample['restarted_service'],token_by_actor={'actor.admin':TOKEN},
                 checkpoint_monitor=other,history_view=sample['history_view'])


def test_directory_mutation_rejected_with_409(sample):
    folder=sample['folder']/'mission_source_history'
    (folder/'unexpected.txt').write_text('changed')
    with client(sample['app']) as c:
        r=get(c,list_url())
        assert r.status_code==409 and r.json()['error']=='history_changed_restart_required'


def test_history_bytes_drift_rejected_even_with_valid_internal_hash(sample):
    folder=sample['folder']/'mission_source_history'
    p=next(folder.glob('g00000000000000000002_*.json'))
    raw=p.read_bytes()
    p.write_bytes(raw+b' ')
    with client(sample['app']) as c:
        assert get(c,list_url()).status_code==409


def test_archive_removed_rejected_409(sample):
    folder=sample['folder']/'mission_source_history'
    next(folder.glob('g*.json')).unlink()
    with client(sample['app']) as c:
        assert get(c,list_url()).status_code==409


def test_checkpoint_drift_rejected_409(sample):
    p=sample['folder']/'mission_registry.json'
    p.write_bytes(b'not a valid checkpoint')
    with client(sample['app']) as c:
        r=get(c,list_url())
        assert r.status_code==409 and r.json()['error']=='checkpoint_changed_restart_required'


def test_read_only_methods_and_no_disk_mutation(sample):
    root=sample['folder']
    originals={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with client(sample['app']) as c:
        for _ in range(2):
            for url in [list_url(),list_url(M2),summary_url(),summary_url(M2,3),summary_url(kind='trust')]:
                assert get(c,url).status_code==200
        for method in ('post','patch','put','delete'):
            assert getattr(c,method)(list_url(),headers={'Authorization':'Bearer '+TOKEN}).status_code in (404,405)
    assert originals=={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_public_script_cli_enables_history_only_with_checkpoint(monkeypatch,sample):
    spec=importlib.util.spec_from_file_location('mission_history_dashboard_cli_test',SERVER)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    monkeypatch.setenv('AEGISFORGE_MISSION_API_ACTOR','actor.admin')
    monkeypatch.setenv('AEGISFORGE_MISSION_API_TOKEN',TOKEN)
    captured=[]
    monkeypatch.setattr(mod.uvicorn,'run',lambda app,**kwargs: captured.append((app,kwargs)))
    monkeypatch.setattr(sys,'argv',[str(SERVER),'--checkpoint-dir',str(sample['folder']),'--history'])
    mod.main()
    with client(captured[0][0]) as c:
        assert get(c,list_url()).json()['data']['count']==1
        assert get(c,'/api/v1/checkpoint').json()['data']['historical_generations_configured'] is True
    assert captured[0][1]['host']=='127.0.0.1'
    assert captured[0][1]['proxy_headers'] is False


def test_cli_history_requires_checkpoint(monkeypatch):
    spec=importlib.util.spec_from_file_location('mission_history_dashboard_cli_test2',SERVER)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    monkeypatch.setattr(sys,'argv',[str(SERVER),'--history'])
    with pytest.raises(SystemExit) as e:mod.main()
    assert e.value.code==2


def test_capabilities_and_dashboard_assets(sample):
    with client(sample['app']) as c:
        routes=get(c,'/api/v1/capabilities').json()['data']['routes']
        assert '/api/v1/missions/{mission_id}/archives' in routes
        html=c.get('/dashboard').text
        js=c.get('/assets/mission_dashboard.js').text
        assert 'archive-generation' in html
        assert 'archive-trend' in html
        assert 'loadArchiveGeneration' in js
        assert 'textContent' in js
        assert 'localStorage' not in js


def test_history_generation_range_ignored_out_of_checkpoint(sample):
    with pytest.raises(NexusMissionHistoryViewError):
        sample['history_view'].summary(4,M1,'graph')


def test_archives_dont_reconfigure_workflows(sample):
    with client(sample['app']) as c:
        assert get(c,summary_url()).status_code==200
        assert get(c,f'/api/v1/missions/{M1}/preview').status_code==409
        assert get(c,f'/api/v1/missions/{M1}/inspect').status_code==409
