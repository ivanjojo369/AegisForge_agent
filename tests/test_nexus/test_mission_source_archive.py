"""Checkpoint-bound read-only source archive, no permission restoration."""
from __future__ import annotations
import importlib.util
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys

import pytest
from starlette.testclient import TestClient

from aegisforge.nexus.mission_control.mission_source_archive import (
    NexusMissionSourceArchive, NexusMissionSourceArchiveError,
    SOURCE_ARCHIVE_SCHEMA,
)
from aegisforge.nexus.mission_control.mission_checkpoint_monitor import NexusMissionCheckpointMonitor
from aegisforge.nexus.mission_control.mission_persistence import NexusMissionPersistence
from aegisforge.nexus.mission_control.mission_service import NexusMissionService
from aegisforge.nexus.mission_control.mission_http_api import create_mission_http_app, NexusMissionHTTPAPIError
from aegisforge.nexus.mission_control.mission_monitor_views import NexusMissionMonitorViews

TOKEN = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
VIEWER = "Wd7kj3N5T2xp0G9uZ4A6bv1R8YqC5Mf2LsEe3Hh"
SERVER = Path(__file__).resolve().parents[2] / "scripts/nexus/serve_mission_http_api.py"
DEMO = Path(__file__).resolve().parents[2] / "scripts/nexus/demo_create_persisted_sources.py"


def load_script(path):
    spec=importlib.util.spec_from_file_location(path.stem+"_sources_test",path)
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


@pytest.fixture()
def sample(tmp_path):
    info = load_script(DEMO).create_demo()
    root = tmp_path / 'checked'
    root.mkdir()
    shutil.copy2(Path(info['checkpoint_dir'])/'mission_registry.json', root/'mission_registry.json')
    shutil.copy2(Path(info['checkpoint_dir'])/'mission_sources.json', root/'mission_sources.json')
    store = NexusMissionPersistence(root)
    monitor = NexusMissionCheckpointMonitor(store, store.load())
    archive = NexusMissionSourceArchive(root/'mission_sources.json', monitor)
    return root, store, monitor, archive


def app_for(sample, *, include_archive=True):
    root, store, monitor, archive = sample
    def permission(actor, action, mid, anchor, metadata):
        if actor=='actor.admin': return action in {'list','read','history','preview','inspect'}
        if actor=='actor.viewer': return action in {'list','read','history'} and mid in {'*','mission.nexus.demo'}
        return False
    service=NexusMissionService(monitor._registry,authorizer=permission)
    return create_mission_http_app(service, token_by_actor={'actor.admin':TOKEN,'actor.viewer':VIEWER},
           checkpoint_monitor=monitor, source_archive=archive if include_archive else None)


def client(app):
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 49000),
                      raise_server_exceptions=False)


def get(c,url,token=TOKEN):
    return c.get(url, headers={'Authorization':'Bearer '+token})


@pytest.mark.parametrize('mid', ['mission.nexus.demo','mission.grid.demo','mission.forge.demo','mission.colossus.demo'])
def test_all_products_have_archived_graph_and_lab(sample,mid):
    with client(app_for(sample)) as c:
        for kind in ('graph','laboratory'):
            r=get(c,f'/api/v1/missions/{mid}/{kind}')
            assert r.status_code==200
            d=r.json()['data']
            assert d['available'] is True and d['mission_id']==mid
            assert d['archived_metadata_only'] is True
            assert d['authenticity_verified'] is False


def test_archived_graph_links(sample):
    with client(app_for(sample)) as c:
        d=get(c,'/api/v1/missions/mission.grid.demo/graph').json()['data']
        assert d['node_count']==2 and d['edge_count']==1
        assert {e['relation'] for e in d['edges']} == {'contains'}


def test_laboratory_is_historical_not_live_verified(sample):
    with client(app_for(sample)) as c:
        d=get(c,'/api/v1/missions/mission.nexus.demo/laboratory').json()['data']
        assert d['aggregate']['passed']==1
        assert d['verified_at_capture'] is True
        assert d['verified_against_sources'] is False


def test_trust_is_historical_and_non_authorizing(sample):
    with client(app_for(sample)) as c:
        d=get(c,'/api/v1/missions/mission.nexus.demo/trust').json()['data']
        assert d['historical_only'] is True
        assert d['count']==1
        assert d['counts']['pending']==1
        assert d['authorization_granted'] is False
        assert d['effects_executed'] is False
        assert not any('token' in r for r in d['requests'])


@pytest.mark.parametrize('mid', ['mission.grid.demo','mission.forge.demo','mission.colossus.demo'])
def test_missing_trust_remains_absent(sample,mid):
    with client(app_for(sample)) as c:
        d=get(c,f'/api/v1/missions/{mid}/trust').json()['data']
        assert d['available'] is False


def test_unauthorized_mission_not_disclosed(sample):
    with client(app_for(sample)) as c:
        assert get(c,'/api/v1/missions/mission.grid.demo/graph',VIEWER).status_code==403
        assert get(c,'/api/v1/missions/mission.grid.demo/laboratory',VIEWER).status_code==403
        assert get(c,'/api/v1/missions/mission.grid.demo/trust',VIEWER).status_code==403
        d=get(c,'/api/v1/checkpoint',VIEWER).json()['data']
        assert d['source_archive']['visible_missions_with_sources']==1
        assert d['visible_mission_count']==1


def test_archive_needs_authentication(sample):
    with client(app_for(sample)) as c:
        assert c.get('/api/v1/checkpoint').status_code==401
        assert c.get('/api/v1/missions/mission.nexus.demo/graph').status_code==401


def test_checkpoint_status_has_no_private_path(sample):
    with client(app_for(sample)) as c:
        text=get(c,'/api/v1/checkpoint').text
        assert str(sample[0]) not in text
        assert 'mission.nexus.demo' not in text
        assert get(c,'/api/v1/checkpoint').json()['data']['source_archive']['configured'] is True


def test_absent_archive_not_automatically_inferred(sample):
    with client(app_for(sample,include_archive=False)) as c:
        assert get(c,'/api/v1/missions/mission.nexus.demo/graph').json()['data']['available'] is False


def app_for_already(sample):
    # Used after mutating an existing store only for API middleware tests.
    root,store,monitor,archive=sample
    service=NexusMissionService(monitor._registry, authorizer=lambda *args: True)
    # App construction should already reject stale sources.
    return create_mission_http_app(service,token_by_actor={'actor.admin':TOKEN},
                  checkpoint_monitor=monitor,source_archive=archive)


def test_archive_read_detects_changed_bytes(sample):
    root,store,monitor,archive=sample
    (root/'mission_sources.json').write_bytes((root/'mission_sources.json').read_bytes()+b' ')
    with pytest.raises(NexusMissionSourceArchiveError):archive.assert_current()
    with pytest.raises(NexusMissionSourceArchiveError):app_for_already(sample)


def test_archive_file_missing_is_fatal(sample):
    root,store,monitor,archive=sample
    (root/'mission_sources.json').unlink()
    with pytest.raises(NexusMissionSourceArchiveError):archive.assert_current()


def test_archive_file_corrupt_refuses_load(sample):
    root,store,monitor,_=sample
    (root/'mission_sources.json').write_bytes(b'{bad')
    with pytest.raises(NexusMissionSourceArchiveError):
        NexusMissionSourceArchive(root/'mission_sources.json', monitor)


def test_archive_file_newline_variation_pinned(sample):
    root,store,monitor,archive=sample
    data=(root/'mission_sources.json').read_bytes()
    (root/'mission_sources.json').write_bytes(data.replace(b'\n',b'\r\n'))
    with pytest.raises(NexusMissionSourceArchiveError):archive.assert_current()


def test_forged_nested_field_rejected_even_with_rehash(sample):
    root,store,monitor,archive=sample
    p=root/'mission_sources.json'
    data=json.loads(p.read_text())
    data['entries']['mission.nexus.demo']['trust']['permission_granted']=True
    body={k:v for k,v in data.items() if k!='archive_sha256'}
    data['archive_sha256']=sha256(json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()).hexdigest()
    p.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(NexusMissionSourceArchiveError):
        NexusMissionSourceArchive(p,monitor)


def test_cross_checkpoint_is_rejected(sample,tmp_path):
    root,store,monitor,archive=sample
    reg=store.load().registry
    reg.assign_component('mission.nexus.demo',component='mission_control',actor_id='actor.local')
    old=store.load().receipt
    store.save(reg,expected_head_sha256=old.envelope_sha256)
    fresh=NexusMissionCheckpointMonitor(store,store.load())
    with pytest.raises(NexusMissionSourceArchiveError):
        NexusMissionSourceArchive(root/'mission_sources.json',fresh)


def test_source_archive_requires_checkpoint_monitor(sample):
    root,store,monitor,archive=sample
    service=NexusMissionService(monitor._registry,authorizer=lambda *args: True)
    with pytest.raises(NexusMissionHTTPAPIError):
        create_mission_http_app(service,token_by_actor={'actor.admin':TOKEN},source_archive=archive)


def test_archive_keeps_workflows_unbound(sample):
    with client(app_for(sample)) as c:
        resp=get(c,'/api/v1/missions/mission.nexus.demo/preview')
        assert resp.status_code==409 and resp.json()['error']=='workflow_not_configured'


def test_archive_write_is_exclusive(sample):
    root,store,monitor,archive=sample
    with pytest.raises(NexusMissionSourceArchiveError):
        NexusMissionSourceArchive.write_new(root,monitor,NexusMissionMonitorViews(),())


def test_archive_does_not_write_on_reads(sample):
    root,store,monitor,archive=sample
    before=(root/'mission_sources.json').read_bytes()
    before_checkpoint=(root/'mission_registry.json').read_bytes()
    with client(app_for(sample)) as c:
        for _ in range(2):
            for suffix in ('graph','laboratory','trust'):
                get(c,'/api/v1/missions/mission.nexus.demo/'+suffix)
    assert (root/'mission_sources.json').read_bytes()==before
    assert (root/'mission_registry.json').read_bytes()==before_checkpoint


def test_no_api_mutations(sample):
    with client(app_for(sample)) as c:
        for method in ('post','patch','put','delete'):
            r=getattr(c,method)('/api/v1/missions/mission.nexus.demo/graph',
                     headers={'Authorization':'Bearer '+TOKEN})
            assert r.status_code in (404,405)


def test_archive_json_is_bounded(sample):
    assert (sample[0]/'mission_sources.json').stat().st_size < 2_000_000
    data=json.loads((sample[0]/'mission_sources.json').read_text())
    assert data['schema']==SOURCE_ARCHIVE_SCHEMA
    assert data['integrity_only'] is True
    assert data['authenticity_verified'] is False


def test_no_approval_tokens_or_credential_data_in_archive(sample):
    content=(sample[0]/'mission_sources.json').read_text()
    assert TOKEN not in content
    assert VIEWER not in content
    assert 'secret' not in content.lower()
    assert 'payload' not in content.lower()


def test_cli_requires_checkpoint_for_archives(monkeypatch):
    mod=load_script(SERVER)
    monkeypatch.setenv('AEGISFORGE_MISSION_API_ACTOR','actor.local')
    monkeypatch.setenv('AEGISFORGE_MISSION_API_TOKEN',TOKEN)
    monkeypatch.setattr(sys,'argv',[str(SERVER),'--source-archive'])
    with pytest.raises(SystemExit):mod.main()


def test_cli_loads_sources_offline(monkeypatch,sample):
    mod=load_script(SERVER)
    seen=[]
    monkeypatch.setattr(mod.uvicorn,'run',lambda app,**kw:seen.append((app,kw)))
    monkeypatch.setenv('AEGISFORGE_MISSION_API_ACTOR','actor.local')
    monkeypatch.setenv('AEGISFORGE_MISSION_API_TOKEN',TOKEN)
    monkeypatch.setattr(sys,'argv',[str(SERVER),'--checkpoint-dir',str(sample[0]),'--source-archive'])
    mod.main()
    assert len(seen)==1
    assert seen[0][1]['host']=='127.0.0.1'
    with client(seen[0][0]) as c:
        assert get(c,'/api/v1/missions/mission.nexus.demo/graph').json()['data']['available']


def test_archive_path_name_is_fixed(sample):
    root,store,monitor,archive=sample
    with pytest.raises(NexusMissionSourceArchiveError):
        NexusMissionSourceArchive(root/'some_other.json',monitor)


@pytest.mark.parametrize('replacement', [b'',b'null',b'[]',b'{}'])
def test_bad_root_json_is_rejected(sample,replacement):
    root,store,monitor,_=sample
    (root/'mission_sources.json').write_bytes(replacement)
    with pytest.raises((NexusMissionSourceArchiveError,TypeError,KeyError)):
        NexusMissionSourceArchive(root/'mission_sources.json',monitor)
