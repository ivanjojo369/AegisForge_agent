"""Offline persistence and recovery tests. Never touches the user's repository."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import stat
import threading

import pytest

from aegisforge.nexus.mission_control.mission_registry import (
    NexusMissionRegistry, MissionRegistryLimits,
)
from aegisforge.nexus.mission_control.mission_service import (
    NexusMissionService,
)
from aegisforge.nexus.mission_control.mission_persistence import (
    MISSION_PERSISTENCE_SCHEMA,
    NexusMissionPersistence, NexusMissionPersistenceError,
)


def hx(s='objective'):
    return sha256(s.encode()).hexdigest()


def reg(mission_id='mission.one'):
    r = NexusMissionRegistry()
    r.register(mission_id, product='colossus', owner_id='owner.one',
               objective_sha256=hx(), actor_id='actor.one')
    return r


def changed(r):
    r.assign_component('mission.one', component='trust', actor_id='actor.one')
    return r


def store(tmp_path):
    return NexusMissionPersistence(tmp_path)


def data_of(tmp_path):
    return json.loads((tmp_path/'mission_registry.json').read_bytes())


def rewrite(tmp_path, obj, where='mission_registry.json'):
    (tmp_path/where).write_bytes((json.dumps(obj, sort_keys=True, separators=(',',':'))+'\n').encode())


def resign(obj):
    body = {k:v for k,v in obj.items() if k != 'envelope_sha256'}
    obj['envelope_sha256'] = sha256(json.dumps(body, sort_keys=True, ensure_ascii=True,
         separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    return obj


def make_two(s):
    r=reg()
    first=s.save(r)
    changed(r)
    second=s.save(r,expected_head_sha256=first.envelope_sha256)
    return r,first,second


def assert_rejected(fn, message=None):
    with pytest.raises(NexusMissionPersistenceError, match=message):
        fn()


def test_first_save_and_reload(tmp_path):
    s=store(tmp_path); r=reg()
    receipt=s.save(r)
    loaded=s.load()
    assert receipt.generation==1 and loaded.receipt==receipt
    assert loaded.registry.snapshot()==r.snapshot()
    assert not (tmp_path/'mission_registry.json.prev').exists()


def test_schema_and_flags(tmp_path):
    s=store(tmp_path);s.save(reg())
    d=data_of(tmp_path)
    assert d['schema']==MISSION_PERSISTENCE_SCHEMA
    assert d['integrity_only'] is True and d['authenticity_verified'] is False
    assert d['registry']['effects_executed'] is False


def test_first_save_reject_expected(tmp_path):
    assert_rejected(lambda:store(tmp_path).save(reg(),expected_head_sha256='0'*64),'initial')


def test_reject_missing(tmp_path):
    assert_rejected(lambda:store(tmp_path).load(), 'no saved')


def test_second_save_prev(tmp_path):
    s=store(tmp_path);r,f,t=make_two(s)
    assert f.generation==1 and t.generation==2
    assert s.load().receipt.envelope_sha256==t.envelope_sha256
    assert s.inspect_previous().receipt.envelope_sha256==f.envelope_sha256
    assert s.inspect_previous().registry.get('mission.one')['component_count']==0
    assert s.load().registry.get('mission.one')['component_count']==1


def test_third_save_rotates(tmp_path):
    s=store(tmp_path);r,f,t=make_two(s)
    r.assign_component('mission.one', component='proof', actor_id='actor.one')
    third=s.save(r,expected_head_sha256=t.envelope_sha256)
    assert third.generation==3
    assert s.inspect_previous().receipt.envelope_sha256==t.envelope_sha256


def test_idempotent_checkpoint(tmp_path):
    s=store(tmp_path);r=reg();first=s.save(r)
    again=s.save(r, expected_head_sha256=first.envelope_sha256)
    assert again==first
    assert not (tmp_path/'mission_registry.json.prev').exists()


def test_stale_writer_rejected(tmp_path):
    s=store(tmp_path);r,f,t=make_two(s)
    assert_rejected(lambda:s.save(r,expected_head_sha256=f.envelope_sha256),'stale')


def test_missing_cas_rejected(tmp_path):
    s=store(tmp_path);r=reg();s.save(r);changed(r)
    assert_rejected(lambda:s.save(r),'stale')


def test_missing_cas_even_unchanged(tmp_path):
    s=store(tmp_path);r=reg();s.save(r)
    assert_rejected(lambda:s.save(r),'stale')


def test_malformed_expected_head(tmp_path):
    s=store(tmp_path);r=reg();s.save(r)
    assert_rejected(lambda:s.save(r,expected_head_sha256='bad'),'invalid expected')


def test_banned_mission_deletion(tmp_path):
    s=store(tmp_path); r=reg()
    r.register('mission.two', product='nexus', owner_id='owner.one',
               objective_sha256=hx(),actor_id='actor.one')
    f=s.save(r)
    assert_rejected(lambda:s.save(reg(),expected_head_sha256=f.envelope_sha256),'remove missions')


def test_banned_history_rewrite(tmp_path):
    s=store(tmp_path); r=reg(); f=s.save(r)
    other=NexusMissionRegistry()
    other.register('mission.one',product='colossus',owner_id='owner.one',
                   objective_sha256=hx('other'),actor_id='actor.one')
    assert_rejected(lambda:s.save(other,expected_head_sha256=f.envelope_sha256),'rewrite')


def test_banned_truncation(tmp_path):
    s=store(tmp_path);r,f,t=make_two(s)
    assert_rejected(lambda:s.save(reg(),expected_head_sha256=t.envelope_sha256),'rewrite')


def test_extend_with_second_mission(tmp_path):
    s=store(tmp_path);r=reg();f=s.save(r)
    r.register('mission.two',product='nexus',owner_id='owner.one',objective_sha256=hx(),actor_id='actor.one')
    second=s.save(r,expected_head_sha256=f.envelope_sha256)
    assert s.load().registry.snapshot()['mission_count']==2
    assert second.generation==2


def test_reload_can_be_extended(tmp_path):
    s=store(tmp_path);r=reg();f=s.save(r)
    restored=s.load().registry
    changed(restored)
    t=s.save(restored,expected_head_sha256=f.envelope_sha256)
    assert t.generation==2


@pytest.mark.parametrize('field,value', [
    ('schema','invalid'),('authenticity_verified',True),('integrity_only',False),
    ('generation',0),('generation',True),('generation',1.2),('previous_sha256','broken'),
    ('created_at_utc','yesterday'),('registry_sha256','f'*64),
])
def test_tamper_rejected_even_with_rehash(tmp_path,field,value):
    s=store(tmp_path);s.save(reg());obj=data_of(tmp_path);obj[field]=value
    rewrite(tmp_path,resign(obj))
    assert_rejected(s.load)


def test_tamper_without_rehash(tmp_path):
    s=store(tmp_path);s.save(reg());obj=data_of(tmp_path);obj['created_at_utc']='2020-01-01T00:00:00Z'
    rewrite(tmp_path,obj)
    assert_rejected(s.load,'SHA mismatch')


def test_tamper_registry_snapshot_with_rehash(tmp_path):
    s=store(tmp_path);s.save(reg());obj=data_of(tmp_path)
    obj['registry']['missions'][0]['status']='verified'
    rewrite(tmp_path,resign(obj))
    assert_rejected(s.load)


def test_append_unknown_field(tmp_path):
    s=store(tmp_path);s.save(reg());obj=data_of(tmp_path);obj['secret']='x'
    rewrite(tmp_path,resign(obj))
    assert_rejected(s.load,'fields')


def test_malformed_json(tmp_path):
    s=store(tmp_path);s.save(reg())
    (tmp_path/'mission_registry.json').write_bytes(b'{')
    assert_rejected(s.load)


def test_non_utf8(tmp_path):
    s=store(tmp_path);s.save(reg())
    (tmp_path/'mission_registry.json').write_bytes(b'\xff')
    assert_rejected(s.load)


def test_over_budget_read(tmp_path):
    s=store(tmp_path);s.save(reg())
    tiny=NexusMissionPersistence(tmp_path,max_checkpoint_bytes=4096)
    # Source may be shorter than 4096; extend with invalid JSON beyond budget.
    (tmp_path/'mission_registry.json').write_bytes(b'x'*5000)
    assert_rejected(tiny.load,'size')


def test_over_budget_write(tmp_path):
    s=NexusMissionPersistence(tmp_path,max_checkpoint_bytes=4096)
    r=reg()
    # Add many valid registry events so the checkpoint exceeds 4096 bytes.
    for i in range(20):
        r.link_reference('mission.one', kind='evidence',reference_sha256=hx(str(i)),actor_id='actor.one')
    assert_rejected(lambda:s.save(r),'byte budget')
    assert not (tmp_path/'mission_registry.json').exists()


@pytest.mark.parametrize('filename', ['../evil.json','other/name.json','c:\\evil.json',
                                      '.hidden.json','foo.json/','x.txt','', 'x.json.prev'])
def test_invalid_filenames(tmp_path,filename):
    assert_rejected(lambda:NexusMissionPersistence(tmp_path,filename=filename),'filename')


def test_root_does_not_exist(tmp_path):
    assert_rejected(lambda:NexusMissionPersistence(tmp_path/'missing'),'root')


def test_root_must_be_directory(tmp_path):
    f=tmp_path/'rootfile';f.write_bytes(b'0')
    assert_rejected(lambda:NexusMissionPersistence(f),'root')


def test_root_symlink_rejected(tmp_path):
    link=tmp_path/'linked'
    try:link.symlink_to(tmp_path,target_is_directory=True)
    except (OSError,NotImplementedError):pytest.skip('symlinks require privilege')
    assert_rejected(lambda:NexusMissionPersistence(link),'root')


def test_file_symlink_rejected(tmp_path):
    s=store(tmp_path)
    outside=tmp_path/'outside';outside.write_bytes(b'hello')
    try:(tmp_path/'mission_registry.json').symlink_to(outside)
    except (OSError,NotImplementedError):pytest.skip('symlinks require privilege')
    assert_rejected(lambda:s.save(reg()), 'linked')
    assert outside.read_bytes()==b'hello'


def test_hardlink_rejected(tmp_path):
    s=store(tmp_path);s.save(reg());linked=tmp_path/'alias'
    try:os.link(tmp_path/'mission_registry.json',linked)
    except OSError:pytest.skip('hardlinks unsupported')
    assert_rejected(s.load,'unlinked')


def test_no_autorecover_on_bad_primary(tmp_path):
    s=store(tmp_path);r,f,t=make_two(s)
    (tmp_path/'mission_registry.json').write_bytes(b'broken')
    assert_rejected(s.load)
    previous=s.inspect_previous()
    assert previous.receipt==type(previous.receipt)(f.generation,f.envelope_sha256,f.registry_sha256,'previous')


def test_missing_primary_with_prev_recovery_required(tmp_path):
    s=store(tmp_path);r,f,t=make_two(s)
    (tmp_path/'mission_registry.json').unlink()
    assert_rejected(s.load,'explicit previous recovery')
    assert_rejected(lambda:s.save(r,expected_head_sha256=t.envelope_sha256),'interrupted')


def test_explicit_restore_from_previous(tmp_path):
    s=store(tmp_path);r,f,t=make_two(s)
    (tmp_path/'mission_registry.json').unlink()
    assert_rejected(lambda:s.restore_interrupted_commit(expected_previous_sha256=t.envelope_sha256),'head mismatch')
    receipt=s.restore_interrupted_commit(expected_previous_sha256=f.envelope_sha256)
    assert receipt.envelope_sha256==f.envelope_sha256
    assert receipt.generation==1
    assert not (tmp_path/'mission_registry.json.prev').exists()
    assert s.load().registry.get('mission.one')['component_count']==0


def test_restore_never_overwrites_primary(tmp_path):
    s=store(tmp_path);r,f,t=make_two(s)
    assert_rejected(lambda:s.restore_interrupted_commit(expected_previous_sha256=f.envelope_sha256),
                    'refusing to overwrite')
    assert s.load().receipt.envelope_sha256==t.envelope_sha256


def test_backup_lineage_tampered(tmp_path):
    s=store(tmp_path);r,f,t=make_two(s)
    obj=data_of(tmp_path);obj['previous_sha256']='f'*64
    rewrite(tmp_path,resign(obj))
    assert_rejected(s.load,'lineage mismatch')


def test_backup_generation_tampered(tmp_path):
    s=store(tmp_path);r,f,t=make_two(s)
    obj=data_of(tmp_path);obj['generation']=4
    rewrite(tmp_path,resign(obj))
    assert_rejected(s.load,'lineage mismatch')


def test_backup_registry_changed(tmp_path):
    s=store(tmp_path);r,f,t=make_two(s)
    obj=json.loads((tmp_path/'mission_registry.json.prev').read_bytes())
    obj['registry']['effects_executed']=True
    rewrite(tmp_path,resign(obj),'mission_registry.json.prev')
    assert_rejected(s.load)


def test_lock_blocks_save(tmp_path):
    s=store(tmp_path);lock=tmp_path/'mission_registry.json.lock'
    lock.write_bytes(b'foreign process')
    assert_rejected(lambda:s.save(reg()),'lock exists')
    assert lock.read_bytes()==b'foreign process'


def test_lock_released_on_rejected_save(tmp_path):
    s=store(tmp_path);r=reg();s.save(r)
    assert_rejected(lambda:s.save(r),'stale')
    assert not (tmp_path/'mission_registry.json.lock').exists()


def test_lock_released_after_atomic_failure(tmp_path,monkeypatch):
    s=store(tmp_path);r=reg();first=s.save(r);changed(r)
    calls=[];real=s._replace
    def fail_second(src,dst):
        calls.append(dst)
        if len(calls)==2: raise OSError('synthetic replace interruption')
        real(src,dst)
    monkeypatch.setattr(s,'_replace',fail_second)
    assert_rejected(lambda:s.save(r,expected_head_sha256=first.envelope_sha256),'atomic')
    assert not (tmp_path/'mission_registry.json.lock').exists()
    assert not (tmp_path/'mission_registry.json').exists()
    assert s.inspect_previous().receipt.envelope_sha256==first.envelope_sha256
    assert_rejected(s.load, 'explicit previous recovery')


def test_no_tmp_files_left_on_failure(tmp_path,monkeypatch):
    s=store(tmp_path);r=reg();first=s.save(r);changed(r)
    monkeypatch.setattr(s,'_replace',lambda *_: (_ for _ in ()).throw(OSError('fail')))
    assert_rejected(lambda:s.save(r,expected_head_sha256=first.envelope_sha256))
    assert not list(tmp_path.glob('.mission_registry.json.*.tmp'))
    assert s.load().receipt.envelope_sha256==first.envelope_sha256


def test_atomically_updates_without_partial_data(tmp_path):
    s=store(tmp_path);r,f,t=make_two(s)
    assert s.load().registry.snapshot()==r.snapshot()
    assert not list(tmp_path.glob('.mission_registry.json.*.tmp'))


def test_two_store_instances_stale_write(tmp_path):
    a=store(tmp_path);b=store(tmp_path);r=reg();f=a.save(r);changed(r)
    latest=a.save(r,expected_head_sha256=f.envelope_sha256)
    r.assign_component('mission.one',component='proof',actor_id='actor.one')
    assert_rejected(lambda:b.save(r,expected_head_sha256=f.envelope_sha256),'stale')
    assert b.load().receipt.envelope_sha256==latest.envelope_sha256


def test_save_never_executes_effects(tmp_path):
    s=store(tmp_path);s.save(reg())
    raw=(tmp_path/'mission_registry.json').read_text()
    for name in ['print(1)', 'workspace_root', 'access_token', 'my_password']:
        assert name not in raw
    assert s.load().registry.snapshot()['effects_executed'] is False


def test_service_export_authorized(tmp_path):
    r=reg();seen=[]
    def authorizer(actor,action,mission,anchor,request):
        seen.append((actor,action,mission,request));return action=='export_snapshot'
    service=NexusMissionService(r,authorizer=authorizer)
    s=store(tmp_path)
    receipt=s.checkpoint_service(service,actor_id='actor.one')
    assert seen[0][1]=='export_snapshot'
    assert receipt.registry_sha256==r.snapshot()['registry_sha256']


def test_service_export_denied(tmp_path):
    r=reg();service=NexusMissionService(r,authorizer=lambda *_: False)
    s=store(tmp_path)
    assert_rejected(lambda:s.checkpoint_service(service,actor_id='actor.one'),'denied')
    assert not (tmp_path/'mission_registry.json').exists()


def test_service_export_fail_closed(tmp_path):
    r=reg()
    def boom(*_):raise RuntimeError('host unavailable')
    service=NexusMissionService(r,authorizer=boom)
    assert_rejected(lambda:store(tmp_path).checkpoint_service(service,actor_id='actor.one'),'denied')


def test_service_export_requires_service(tmp_path):
    assert_rejected(lambda:store(tmp_path).checkpoint_service(reg(), actor_id='actor.one'))


def test_save_snapshot_revalidates(tmp_path):
    r=reg();snap=r.snapshot();snap['authenticity_verified']=True
    assert_rejected(lambda:store(tmp_path).save_snapshot(snap))


def test_snapshot_limits_enforced(tmp_path):
    s=store(tmp_path);r=reg();s.save(r)
    tight=NexusMissionPersistence(tmp_path,registry_limits=MissionRegistryLimits(max_missions=1,max_events_per_mission=1))
    changed(r)
    assert_rejected(lambda:tight.save(r,expected_head_sha256=s.load().receipt.envelope_sha256))


def test_string_flags_rejected_even_with_rehash(tmp_path):
    s=store(tmp_path);s.save(reg());d=data_of(tmp_path)
    d['integrity_only']=1
    rewrite(tmp_path,resign(d))
    assert_rejected(s.load)


def test_unexpected_previous_backup_rejected(tmp_path):
    s=store(tmp_path);s.save(reg())
    (tmp_path/'mission_registry.json.prev').write_bytes(b'corrupted')
    assert_rejected(s.load)


def test_no_plaintext_objective_is_saved(tmp_path):
    s=store(tmp_path);s.save(reg())
    assert b'objective description: private' not in (tmp_path/'mission_registry.json').read_bytes()


def test_restore_without_backup(tmp_path):
    s=store(tmp_path)
    assert_rejected(lambda:s.restore_interrupted_commit(expected_previous_sha256='0'*64))


def test_previous_inspection_does_not_modify_file(tmp_path):
    s=store(tmp_path);r,f,t=make_two(s)
    before=(tmp_path/'mission_registry.json.prev').read_bytes()
    s.inspect_previous()
    assert (tmp_path/'mission_registry.json.prev').read_bytes()==before


def test_read_stable_same_instance(tmp_path):
    s=store(tmp_path);r=reg();s.save(r)
    seen=[]
    def worker():
        for _ in range(10): seen.append(s.load().receipt.generation)
    ts=[threading.Thread(target=worker) for _ in range(3)]
    for t in ts:t.start()
    for t in ts:t.join()
    assert seen==[1]*30


def test_loaded_registry_requires_new_host_authorizer(tmp_path):
    s=store(tmp_path);s.save(reg())
    loaded=s.load().registry
    authorized=NexusMissionService(loaded,authorizer=lambda actor,action,*_: actor=='operator.ok')
    assert authorized.get_mission('mission.one',actor_id='operator.ok')['status']=='draft'
    with pytest.raises(Exception):
        authorized.get_mission('mission.one',actor_id='actor.one')


def test_checkpoint_service_after_authorized_update(tmp_path):
    r=NexusMissionRegistry()
    service=NexusMissionService(r,authorizer=lambda *_: True)
    s=store(tmp_path)
    initial=s.checkpoint_service(service,actor_id='actor.one')
    anchor=service.registry_version(actor_id='actor.one')
    service.create_mission('mission.one',product='nexus',owner_id='owner.one',
                           objective_sha256=hx(),actor_id='actor.one',
                           expected_registry_sha256=anchor)
    final=s.checkpoint_service(service,actor_id='actor.one',
                               expected_head_sha256=initial.envelope_sha256)
    assert final.generation==2
    assert s.load().registry.get('mission.one')['product']=='nexus'


def test_corrupt_existing_blocks_new_checkpoint(tmp_path):
    s=store(tmp_path);r=reg();first=s.save(r)
    (tmp_path/'mission_registry.json').write_bytes(b'not valid json')
    changed(r)
    assert_rejected(lambda:s.save(r,expected_head_sha256=first.envelope_sha256))
    assert (tmp_path/'mission_registry.json').read_bytes()==b'not valid json'
