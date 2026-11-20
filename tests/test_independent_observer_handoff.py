"""Actual NFS/cursor/lock transactions and CPU process guards, no GPU adoption."""
from copy import deepcopy
import fcntl
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from common.data_contracts import resolve, sha256_file
from scripts.tiered_checkpoint_retention import atomic_json
from scripts import independent_observer_handoff as m
from scripts import restore_final_storage as storage
from scripts.horizon_process_handoff import process_record
from test_independent_checkpoint_handoff import case
from test_restore_final_storage import setup


@pytest.fixture
def handoff(setup):
    e=setup; c=e['c']; old=e['pool']/'observer';old.mkdir()
    c.contract['shared_storage']['nfs_pools']['observer']=dict(paths=[str(old)],budget_bytes=2**30)
    atomic_json(c.root/'contract.json',c.contract)
    c.revision['source_contract_sha256']=sha256_file(c.root/'contract.json');atomic_json(c.review_path,c.revision)
    e['observer'].update(cwd=str(resolve('.')),argv=['python','scripts/observe_extended_training_v2.py',
        '--source-run',str(c.root),'--run-dir',str(old)])
    atomic_json(old/'contract.json',dict(source_run=str(c.root),source_contract_sha256=sha256_file(c.root/'contract.json'),
        source_config_sha256=sha256_file(c.root/'main.json'),code={'scripts/observe_extended_training_v2.py':sha256_file(resolve('scripts/observe_extended_training_v2.py'))}))
    cert=c.root/'checkpoint_certificates/ckpt_0010500.json'
    atomic_json(old/'cursor.json',dict(certificate_hashes={cert.name:sha256_file(cert)},seen=['milestone:21'],
        last_resource_unix=1000,last_progress_unix=999,source_state='suspected_stall',worker_pid=99))
    rows=[dict(event_id='milestone:21',event='training_milestone_observed',step=10510),
        dict(event_id='certificate:'+cert.name,event='checkpoint_certified',step=10500),
        dict(event_id='evaluation:10500',event='source_evaluation_observed',step=10500)]
    for row in rows:row.update(stage='main_training_observer',source_run=str(c.root))
    (old/'events.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows));atomic_json(old/'state.json',dict(phase='running',done=10510))
    storage.prepare(c.root,e['root'],c.review_path,e['capture'],e['observer'])
    e.update(old=old,cert=cert);return e


def accepted_view(e,ref):
    storage.publish(e['root'])
    return SimpleNamespace(main=e['c'].root,contract=json.loads((e['c'].root/'contract.json').read_text()),
        binding=dict(observer_handoff=ref,code={m.OWN:sha256_file(resolve(m.OWN))}))


def test_real_snapshot_then_budget_restore_and_cursor_migration(handoff):
    e=handoff;before={n:(e['old']/n).read_bytes() for n in m.FILES}
    ref=m.freeze(e['root']);view=accepted_view(e,ref)
    result=m.seed_history(view,e['old']/'independent_v1')
    assert set(result['seen'])=={'milestone:10500','certificate:ckpt_0010500.json','evaluation:10500'}
    assert result['last_resource_unix']==1000 and result['certificate_hashes']=={'ckpt_0010500.json':sha256_file(e['cert'])}
    assert 'source_state' not in result and 'last_progress_unix' not in result and 'worker_pid' not in result
    assert {n:(e['old']/n).read_bytes() for n in m.FILES}==before
    assert not (e['old']/'independent_v1').exists()
    with pytest.raises(ValueError,match='Preserve'):m.freeze(e['root'])


def test_live_captured_process_prevents_any_freeze(handoff):
    e=handoff;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
    try:
        row=process_record(p.pid);plan=json.loads((e['root']/'plan.json').read_text());plan['observer_capture'].update(pid=row['pid'],start_ticks=row['start_ticks'])
        atomic_json(e['root']/'plan.json',plan);atomic_json(e['root']/'prepared.json',dict(plan_sha256=sha256_file(e['root']/'plan.json')))
        with pytest.raises(ValueError,match='still present'):m.freeze(e['root'])
        assert p.poll() is None and not (e['root']/'observer_handoff').exists()
    finally:p.terminate();p.wait(timeout=5)


@pytest.mark.parametrize('fault',['held_lock','binding','namespace','size','source_live'])
def test_freeze_rejects_wrong_owner_live_or_unbounded_snapshot(handoff,monkeypatch,fault):
    e=handoff;lock=None
    if fault=='held_lock':
        lock=(e['old']/'controller.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    elif fault=='binding':
        cp=e['old']/'contract.json';v=json.loads(cp.read_text());v['source_contract_sha256']='0'*64;atomic_json(cp,v)
    elif fault=='namespace':
        p=e['root']/'plan.json';v=json.loads(p.read_text());v['observer_capture']['argv'][-1]=str(e['pool'])
        atomic_json(p,v);atomic_json(e['root']/'prepared.json',dict(plan_sha256=sha256_file(p)))
    elif fault=='size':monkeypatch.setattr(m,'CAP',1)
    elif fault=='source_live':monkeypatch.setattr(m,'require_quiet',lambda *a:(_ for _ in ()).throw(ValueError('source live')))
    try:
        with pytest.raises((ValueError,RuntimeError,BlockingIOError)):m.freeze(e['root'])
        assert not (e['root']/'observer_handoff').exists()
    finally:
        if lock:lock.close()


@pytest.mark.parametrize('fault',['manifest','raw','certificate','runtime','code','output','alive'])
def test_invalid_handoff_never_seeds_new_observer(handoff,monkeypatch,fault):
    e=handoff;ref=m.freeze(e['root']);view=accepted_view(e,ref);output=e['old']/'independent_v1'
    if fault=='manifest':view.binding['observer_handoff']['sha256']='0'*64
    elif fault=='raw':(Path(ref['path']).parent/'events.jsonl').write_text('{}\n')
    elif fault=='certificate':e['cert'].write_text('{}')
    elif fault=='runtime':view.contract['final_storage_restoration']['previous_contract_sha256']='0'*64
    elif fault=='code':view.binding['code'][m.OWN]='0'*64
    elif fault=='output':output=e['old']/'elsewhere'
    elif fault=='alive':monkeypatch.setattr(m,'stopped_identities',lambda rows:False)
    with pytest.raises(ValueError):m.seed_history(view,output)
    assert not output.exists()


@pytest.mark.parametrize('bad_reference',[False,True])
def test_observer_entrypoint_consumes_seed_before_output_publication(handoff,monkeypatch,bad_reference):
    from scripts import observe_independent_training as observer
    e=handoff;ref=m.freeze(e['root']);view=accepted_view(e,ref);output=e['old']/'independent_v1'
    if bad_reference:ref['sha256']='0'*64
    monkeypatch.setattr(observer,'RuntimeView',lambda control:view)
    monkeypatch.setattr(observer,'admit_output',lambda *a:dict(max_rss_bytes=8*2**30,max_seconds=60,
        pool=dict(paths=[str(e['old'])],budget_bytes=2**30),resource_interval_seconds=600,interval_seconds=30))
    monkeypatch.setattr(observer.time,'time',lambda:1001)
    captured=[]
    def cycle(v,h,now):
        captured.append(deepcopy(h));return dict(phase='complete',test_fixture=True),h,[]
    monkeypatch.setattr(observer,'cycle',cycle)
    if bad_reference:
        with pytest.raises(ValueError):observer.observe(e['pool']/'future_control',output)
        assert not output.exists() and not captured
    else:
        report=observer.observe(e['pool']/'future_control',output)
        assert report['goal_complete'] is False and len(captured)==1
        assert 'milestone:10500' in captured[0]['seen'] and 'milestone:21' not in captured[0]['seen']
        assert json.loads((output/'cursor.json').read_text())['certificate_hashes']==captured[0]['certificate_hashes']
