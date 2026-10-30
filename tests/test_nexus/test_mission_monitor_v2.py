"""Colossus monitor v2: real API, sources, permission boundary; offline only."""
from __future__ import annotations
from hashlib import sha256
import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest
from starlette.testclient import TestClient

from aegisforge.nexus.mission_control.mission_http_api import create_mission_http_app
from aegisforge.nexus.mission_control.mission_monitor_views import (
    NexusMissionMonitorViews, NexusMissionMonitorError, MonitorLabSource, MonitorTrustSource,
)
from aegisforge.nexus.mission_control.mission_service import NexusMissionService
from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.proof.proof_graph import NexusProofGraph
from aegisforge.nexus.lab.scorecard import NexusScorecard

_TOKEN = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
_SECOND = "Wd7kj3N5T2xp0G9uZ4A6bv1R8YqC5Mf2LsEe3Hh"
_ROOT = Path(__file__).resolve().parents[2]
_SERVER = _ROOT / 'scripts/nexus/serve_mission_http_api.py'


def _client(app):
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 49000),
                      raise_server_exceptions=False)


def _get(client, path, token=_TOKEN):
    return client.get(path, headers={'Authorization': 'Bearer ' + token})


@pytest.fixture()
def demo_app(monkeypatch):
    spec = importlib.util.spec_from_file_location('colossus_monitor_demo_test', _SERVER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    capture = []
    monkeypatch.setattr(module.uvicorn, 'run', lambda app, **kw: capture.append((app, kw)))
    monkeypatch.setenv('AEGISFORGE_MISSION_API_ACTOR', 'actor.local')
    monkeypatch.setenv('AEGISFORGE_MISSION_API_TOKEN', _TOKEN)
    monkeypatch.setattr(sys, 'argv', [str(_SERVER), '--demo'])
    module.main()
    assert len(capture) == 1
    assert capture[0][1]['host'] == '127.0.0.1'
    assert capture[0][1]['proxy_headers'] is False
    return capture[0][0]


@pytest.fixture()
def bare_app():
    registry = NexusMissionRegistry()
    registry.register('mission.alpha', product='nexus', owner_id='owner.local',
                      objective_sha256=sha256(b'alpha').hexdigest(), actor_id='actor.admin')
    registry.register('mission.beta', product='aegisgrid', owner_id='owner.local',
                      objective_sha256=sha256(b'beta').hexdigest(), actor_id='actor.admin')
    def auth(actor, action, mid, anchor, req):
        if actor == 'actor.admin':
            return action in {'list', 'read', 'history', 'preview', 'inspect'}
        return actor == 'actor.viewer' and mid in {'*', 'mission.alpha'} and action in {'list', 'read', 'history'}
    service = NexusMissionService(registry, authorizer=auth)
    return registry, service, create_mission_http_app(
        service, token_by_actor={'actor.admin': _TOKEN, 'actor.viewer': _SECOND})


@pytest.mark.parametrize('kind', ['timeline', 'graph', 'laboratory', 'trust'])
def test_demo_monitor_views_authorized(demo_app, kind):
    with _client(demo_app) as c:
        r = _get(c, f'/api/v1/missions/mission.demo/{kind}')
        assert r.status_code == 200
        data = r.json()['data']
        assert data['kind'] == kind if kind != 'graph' else data['kind'] == 'proof_graph'
        assert data['available'] is True
        assert data['mission_id'] == 'mission.demo'
        assert data['authenticity_verified'] is False


def test_demo_graph_real_verified_structure(demo_app):
    with _client(demo_app) as c:
        d = _get(c,'/api/v1/missions/mission.demo/graph').json()['data']
        assert d['node_count'] == 3 and d['edge_count'] == 2
        assert {e['relation'] for e in d['edges']} == {'contains', 'records'}
        assert all(len(n['node_id']) == 64 for n in d['nodes'])
        assert 'prompt' not in json.dumps(d)


def test_demo_laboratory_real_verified_aggregate(demo_app):
    with _client(demo_app) as c:
        d = _get(c,'/api/v1/missions/mission.demo/laboratory').json()['data']
        assert d['aggregate'] == {'evaluations': 3, 'passed': 1, 'failed': 1, 'inconclusive': 1}
        assert d['verified_against_sources'] is True
        assert d['by_product']['aegisgrid']['failed'] == 1
        assert 'payload' not in json.dumps(d)


def test_demo_trust_state_is_never_approval(demo_app):
    with _client(demo_app) as c:
        d = _get(c,'/api/v1/missions/mission.demo/trust').json()['data']
        assert d['count'] == 1 and d['counts']['pending'] == 1
        assert d['authorization_granted'] is False
        assert d['effects_executed'] is False
        assert 'relative_path' not in json.dumps(d)
        assert 'reviewer' not in json.dumps(d)


def test_demo_timeline_uses_registry(demo_app):
    with _client(demo_app) as c:
        timeline = _get(c,'/api/v1/missions/mission.demo/timeline').json()['data']
        raw = _get(c,'/api/v1/missions/mission.demo/history').json()['data']
        assert timeline['count'] == raw['count'] == 2
        assert [e['operation'] for e in timeline['events']] == ['registered', 'component_assigned']
        assert timeline['events'][-1]['event_sha256'] == raw['events'][-1]['event_sha256']
        assert 'registration' not in json.dumps(timeline)


@pytest.mark.parametrize('kind', ['timeline','graph','laboratory','trust'])
def test_new_views_require_token(demo_app, kind):
    with _client(demo_app) as c:
        r = c.get('/api/v1/missions/mission.demo/' + kind)
        assert r.status_code == 401 and r.json()['error'] == 'authentication_required'


@pytest.mark.parametrize('kind', ['graph','laboratory','trust','timeline'])
def test_missing_mission_never_reveals_source(demo_app, kind):
    with _client(demo_app) as c:
        r = _get(c,'/api/v1/missions/mission.unknown/' + kind)
        assert r.status_code in (403, 404)
        assert 'traceback' not in r.text.lower()


@pytest.mark.parametrize('kind', ['graph','laboratory','trust'])
def test_unconfigured_views_advertise_absence(bare_app, kind):
    with _client(bare_app[2]) as c:
        r = _get(c,'/api/v1/missions/mission.alpha/' + kind)
        assert r.status_code == 200
        assert r.json()['data'] == NexusMissionMonitorViews.absent('proof_graph' if kind=='graph' else kind)


@pytest.mark.parametrize('kind', ['graph','laboratory','trust','timeline'])
def test_viewer_cannot_read_other_mission(bare_app, kind):
    with _client(bare_app[2]) as c:
        r = _get(c,'/api/v1/missions/mission.beta/' + kind, _SECOND)
        assert r.status_code == 403


def test_viewer_can_read_allowed_mission_timeline(bare_app):
    with _client(bare_app[2]) as c:
        r = _get(c,'/api/v1/missions/mission.alpha/timeline', _SECOND)
        assert r.status_code == 200 and r.json()['data']['count'] == 1


@pytest.mark.parametrize('kind', ['graph','laboratory','trust','timeline'])
def test_invalid_mission_name_rejected(bare_app, kind):
    with _client(bare_app[2]) as c:
        assert _get(c,'/api/v1/missions/BAD-NAME/' + kind).status_code == 404


@pytest.mark.parametrize('kind', ['graph','laboratory','trust','timeline'])
def test_unexpected_query_rejected(demo_app, kind):
    with _client(demo_app) as c:
        assert _get(c,'/api/v1/missions/mission.demo/' + kind + '?token=secret').status_code == 400


@pytest.mark.parametrize('kind', ['graph','laboratory','trust','timeline'])
def test_mutation_not_exposed(demo_app, kind):
    with _client(demo_app) as c:
        r=c.post('/api/v1/missions/mission.demo/' + kind, json={'approved': True},
                 headers={'Authorization': 'Bearer '+_TOKEN})
        assert r.status_code in (404,405)


def test_reading_monitors_does_not_modify_mission(demo_app):
    with _client(demo_app) as c:
        old=_get(c,'/api/v1/missions/mission.demo/history').json()['data']
        for kind in ['graph','laboratory','trust','timeline','graph']:
            assert _get(c,'/api/v1/missions/mission.demo/' + kind).status_code==200
        assert old == _get(c,'/api/v1/missions/mission.demo/history').json()['data']


def test_graph_cross_mission_source_rejected():
    graph=NexusProofGraph('mission.other')
    with pytest.raises(NexusMissionMonitorError):
        NexusMissionMonitorViews(graphs={'mission.demo':graph})


def test_graph_tamper_is_rejected(monkeypatch):
    graph=NexusProofGraph('mission.demo')
    fake=graph.snapshot()
    fake['graph_sha256']='0'*64
    monkeypatch.setattr(graph,'snapshot',lambda:fake)
    with pytest.raises(NexusMissionMonitorError):
        NexusMissionMonitorViews(graphs={'mission.demo':graph}).graph('mission.demo')


def test_graph_over_limit_is_rejected():
    graph=NexusProofGraph('mission.demo')
    for i in range(70): graph.add_node('evidence',f'case.{i}')
    with pytest.raises(NexusMissionMonitorError):
        NexusMissionMonitorViews(graphs={'mission.demo':graph}).graph('mission.demo')


def test_trust_cross_mission_source_rejected(tmp_path):
    from aegisforge.nexus.trust.approval_gate import NexusApprovalGate, ActionBinding
    gate=NexusApprovalGate(tmp_path, reviewer=lambda r:None,
                           binding_provider=lambda a:ActionBinding('a'*64,'b'*64))
    req=gate.request('mission.one','write_file','demo.txt',binding=ActionBinding('a'*64,'b'*64))
    with pytest.raises(NexusMissionMonitorError):
        NexusMissionMonitorViews(trusts={'mission.two':MonitorTrustSource(gate,(req,))})


def test_trust_missing_gate_record_fails_closed(tmp_path):
    from aegisforge.nexus.trust.approval_gate import NexusApprovalGate, ActionBinding
    a=ActionBinding('a'*64,'b'*64)
    gate_a=NexusApprovalGate(tmp_path, reviewer=lambda r:None,binding_provider=lambda _:a)
    gate_b=NexusApprovalGate(tmp_path, reviewer=lambda r:None,binding_provider=lambda _:a)
    req=gate_a.request('mission.demo','write_file','demo.txt',binding=a)
    with pytest.raises(NexusMissionMonitorError):
        NexusMissionMonitorViews(trusts={'mission.demo':MonitorTrustSource(gate_b,(req,))}).trust('mission.demo')


def test_laboratory_invalid_report_is_rejected():
    source=NexusScorecard()
    with pytest.raises(NexusMissionMonitorError):
        NexusMissionMonitorViews(labs={'mission.demo':MonitorLabSource(source, {'scorecard_id':'demo','scorecard_sha256':'0'*64},({},))}).laboratory('mission.demo')


def test_timeline_rejects_broken_chain():
    events=({'mission_id':'mission.demo','sequence':1,'previous_sha256':'0'*64,'event_sha256':'a'*64},
            {'mission_id':'mission.demo','sequence':2,'previous_sha256':'b'*64,'event_sha256':'c'*64})
    with pytest.raises(NexusMissionMonitorError):
        NexusMissionMonitorViews.timeline('mission.demo',events)


def test_demo_has_only_read_only_capabilities(demo_app):
    with _client(demo_app) as c:
        d=_get(c,'/api/v1/capabilities').json()['data']
        assert d['effects_enabled'] is False and d['read_only'] is True
        assert all('approve' not in path for path in d['routes'])


def test_no_secrets_in_assets(demo_app):
    with _client(demo_app) as c:
        js=c.get('/assets/mission_dashboard.js').text
        assert 'localStorage' not in js
        assert 'sessionStorage' not in js
        assert 'document.cookie' not in js
        assert 'innerHTML' not in js
        assert 'Bearer ${bearer}' in js
        assert '<script' not in js
        html=c.get('/dashboard')
        assert html.status_code == 200
        assert 'ProofGraph' in html.text and 'Nexus Laboratory' in html.text
        assert 'Nexus Trust' in html.text and 'Mission Timeline' in html.text
        assert 'no-store' in html.headers['cache-control']


def test_server_does_not_expose_network_or_permissive_cors(demo_app):
    with _client(demo_app) as c:
        res=_get(c,'/api/v1/missions/mission.demo/graph')
        assert 'access-control-allow-origin' not in res.headers
        assert "'none'" in res.headers['content-security-policy']
        assert res.headers['x-frame-options']=='DENY'


@pytest.mark.parametrize('kind', ['graph','laboratory','trust','timeline'])
def test_remote_ip_cannot_access(demo_app,kind):
    with TestClient(demo_app,base_url='http://127.0.0.1',client=('192.0.2.3',5000),
                    raise_server_exceptions=False) as c:
        r=_get(c,'/api/v1/missions/mission.demo/'+kind)
        assert r.status_code==403


@pytest.mark.parametrize('kind', ['graph','laboratory','trust','timeline'])
def test_wrong_token_rejected(demo_app,kind):
    with _client(demo_app) as c:
        assert _get(c,'/api/v1/missions/mission.demo/'+kind, _SECOND).status_code==401
