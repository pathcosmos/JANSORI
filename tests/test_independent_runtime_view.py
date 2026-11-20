"""Actual immutable NFS/shared metadata, synthetic acceptedGPU and /proc rows."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
from common.data_contracts import resolve,sha256_file
from scripts.tiered_checkpoint_retention import atomic_json
from scripts import independent_runtime_view as m
from test_independent_runtime_contract import case,prepared,rows_for
from test_latest_independent_revision import add_checkpoint


@pytest.fixture
def runtime(prepared,monkeypatch):
    c,control,path,a,_=prepared
    sup=dict(pid=80000001,start_ticks=20,argv_sha256='supervisor',cwd=str(resolve('.')),uid=1100,state='S',
        argv=['python',str(resolve(m.SUPERVISOR)),'--control-dir',str(control),'--run'])
    a['supervisor']=deepcopy(sup);atomic_json(path,a)
    command=['python','-m','torch.distributed.run','--standalone','--nproc_per_node=8','--module',m.MODULE,'--execution',str(path)]
    handle=dict(pid=80000002,process={'pid':80000002,'start_ticks':30},owner='b'*32,command=command)
    atomic_json(path.parent/'launch/handle.json',handle)
    atomic_json(control/'current_attempt.json',dict(execution=str(path),execution_sha256=sha256_file(path),runtime_contract_sha256=sha256_file(control/'runtime_contract.json')))
    atomic_json(c.root/'state.json',dict(phase='running',worker_pid=handle['pid']))
    rows=rows_for(a)
    for r in rows:r.update(launcher_pid=handle['pid'],launcher_start_ticks=30)
    folder=path.parent/'consumption';folder.mkdir()
    for r in rows:atomic_json(folder/f"rank{r['rank']}.json",r)
    proof=m.verify_consumption_rows(a,rows,owner=handle['owner']);atomic_json(folder/'all_ranks.json',proof)
    receipt=dict(proof=proof,receipts={str(p):sha256_file(p) for p in [*[folder/f'rank{i}.json' for i in range(8)],folder/'all_ranks.json']})
    atomic_json(folder/'supervisor_verified.json',receipt)
    processes={sup['pid']:sup,handle['pid']:dict(pid=handle['pid'],start_ticks=30,ppid=sup['pid'],argv=command,argv_sha256='launcher',state='S')}
    for r in rows:
        processes[r['pid']]=dict(pid=r['pid'],start_ticks=r['start_ticks'],ppid=handle['pid'],argv=['python','-m',m.MODULE,'--execution',str(path)],argv_sha256='rank',state='S')
    monkeypatch.setattr(m,'process_record',lambda pid:deepcopy(processes[pid]))
    original=Path.read_bytes
    def read(p):
        if str(p).startswith('/proc/') and p.name=='environ':
            pid=int(p.parent.name);rank=pid-9000000
            return ('\0'.join([m.OWNER_ENV+'='+handle['owner'],'RANK='+str(rank),'LOCAL_RANK='+str(rank),'WORLD_SIZE=8'])).encode()
        return original(p)
    monkeypatch.setattr(Path,'read_bytes',read)
    view=m.RuntimeView(control)
    return SimpleNamespace(c=c,control=control,path=path,a=a,rows=rows,handle=handle,processes=processes,view=view)


def test_live_identity_and_consumption_survive_rotated_source(runtime):
    r=runtime;result=r.view.observe()
    assert result['processes']['all8_consumed_live'] and result['consumption']['proof']['step']==10000
    shutil.rmtree(r.c.child);shutil.rmtree(r.c.parent)
    result=r.view.observe()
    assert result['processes']['all8_consumed_live'] and result['completion'] is None


@pytest.mark.parametrize('fault',['supervisor_reused','launcher_reused','launcher_command','rank_reused','rank_parent','rank_command'])
def test_reused_or_wrong_process_never_reported_healthy(runtime,fault):
    r=runtime
    if fault=='supervisor_reused':r.processes[r.a['supervisor']['pid']]['start_ticks']+=1
    elif fault=='launcher_reused':r.processes[r.handle['pid']]['start_ticks']+=1
    elif fault=='launcher_command':r.processes[r.handle['pid']]['argv'][-1]='/tmp/wrong'
    elif fault=='rank_reused':r.processes[r.rows[-1]['pid']]['start_ticks']+=1
    elif fault=='rank_parent':r.processes[r.rows[-1]['pid']]['ppid']=123
    elif fault=='rank_command':r.processes[r.rows[-1]['pid']]['argv'][-1]='/tmp/wrong'
    result=r.view.observe()['processes']
    assert result['supervisor'] is None or result['launcher'] is None or not result['all8_consumed_live']


@pytest.mark.parametrize('fault',['main_config','runtime','shared','execution','ack','rank_missing','training_id','pointer'])
def test_changed_or_missing_evidence_fails_closed(runtime,fault):
    r=runtime;r.view.observe()
    if fault=='main_config':p=r.c.root/'main.json';p.write_text(p.read_text()+' ')
    elif fault=='runtime':p=r.control/'runtime_contract.json';p.write_text(p.read_text()+' ')
    elif fault=='shared':p=Path(r.a['shared_admission']['path']);p.write_text(p.read_text()+' ')
    elif fault=='execution':r.path.write_text(r.path.read_text()+' ')
    elif fault=='ack':atomic_json(r.path.parent/'consumption/supervisor_verified.json',{'fake':True})
    elif fault=='rank_missing':(r.path.parent/'consumption/rank7.json').unlink()
    elif fault=='training_id':atomic_json(r.c.root/'training/training_contract.json',{'id':'wrong'})
    elif fault=='pointer':atomic_json(r.control/'current_attempt.json',{'execution':str(r.path),'execution_sha256':'0'*64,'runtime_contract_sha256':r.view.digest})
    with pytest.raises((ValueError,FileNotFoundError)):r.view.observe()


def test_unpublished_attempt_and_pending_consumption_are_not_completion(runtime):
    r=runtime;(r.control/'current_attempt.json').unlink()
    assert r.view.observe()['phase']=='await_independent_execution'
    atomic_json(r.control/'current_attempt.json',dict(execution=str(r.path),execution_sha256=sha256_file(r.path),runtime_contract_sha256=r.view.digest))
    (r.path.parent/'consumption/supervisor_verified.json').unlink()
    observed=r.view.observe();assert observed['consumption'] is None and observed['completion'] is None


def completed(r):
    cp,cert=add_checkpoint(r.c,57000)
    result=dict(status='owned_main_exited_successfully',exit_code=0,owner=r.handle['owner'],remaining_owned_processes=[],
        processes=[r.handle['process'],*[{'pid':v['pid'],'start_ticks':v['start_ticks']} for v in r.rows]])
    atomic_json(r.path.parent/'launch/result.json',result)
    report=dict(status='main_target_completed_review_pending',trained_tokens=119537664000,final_checkpoint=str(cp),
        runtime_contract_sha256=r.view.digest,execution=str(r.path),certificate_receipts={cert.name:sha256_file(cert)})
    atomic_json(r.c.root/'report.json',report)
    atomic_json(r.c.root/'.done',dict(report_sha256=sha256_file(r.c.root/'report.json'),contract_sha256=r.view.binding['main_contract_sha256']))
    atomic_json(r.c.root/'state.json',{'phase':'complete'})


@pytest.mark.parametrize('fault',['none','live_rank','tagged_orphan','missing_rank_exit','bad_report','bad_certificate','bad_owner','marker_only'])
def test_target_completion_requires_exit_and_storage_binding(runtime,monkeypatch,fault):
    r=runtime;completed(r);monkeypatch.setattr(m,'stopped_identities',lambda rows:fault!='live_rank')
    monkeypatch.setattr(m,'tagged_pids',lambda owner:[99] if fault=='tagged_orphan' else [])
    if fault=='missing_rank_exit':
        p=r.path.parent/'launch/result.json';v=json.loads(p.read_text());v['processes'].pop();atomic_json(p,v)
    elif fault=='bad_report':
        p=r.c.root/'report.json';v=json.loads(p.read_text());v['trained_tokens']=35000*2097152;atomic_json(p,v)
    elif fault=='bad_certificate':
        p=r.c.root/'checkpoint_certificates/ckpt_0057000.json';p.write_text(p.read_text()+' ')
    elif fault=='bad_owner':
        p=r.path.parent/'launch/result.json';v=json.loads(p.read_text());v['owner']='c'*32;atomic_json(p,v)
    elif fault=='marker_only':(r.path.parent/'launch/result.json').unlink()
    if fault=='none':
        value=r.view.observe()['completion'];assert value['step']==57000 and not value['goal_complete'] and not value['payload_sha_refreshed']
    elif fault in ('live_rank','tagged_orphan'):assert r.view.observe()['completion'] is None
    else:
        with pytest.raises((ValueError,FileNotFoundError)):r.view.observe()


def test_stopped_identity_check_uses_actual_process_start_ticks():
    import subprocess,sys
    from scripts.horizon_process_handoff import process_record
    proc=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
    try:
        row=process_record(proc.pid)
        assert not m.stopped_identities([row])
        assert m.stopped_identities([dict(row,start_ticks=row['start_ticks']+1)])
        proc.terminate();proc.wait(timeout=5)
        assert m.stopped_identities([row])
    finally:
        if proc.poll() is None:proc.terminate();proc.wait(timeout=5)


def test_cached_admission_also_pins_historical_verifier_implementation(runtime,monkeypatch):
    r=runtime;path=resolve('scripts/verify_independent_resume_probe.py')
    assert str(path) in r.view.pins
    original=m.stamp
    def changed(p):
        value=original(p)
        return (*value[:-1],value[-1]+1) if Path(p)==path else value
    monkeypatch.setattr(m,'stamp',changed)
    with pytest.raises(ValueError,match='evidence changed'):r.view.observe()
