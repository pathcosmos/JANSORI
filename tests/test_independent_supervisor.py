"""Production-launch protocol tests. No real3B/GPU acceptance or adoption.

Actual NFS metadata and CPU subprocess/pidfd cleanup where stated; main model,
GPU acceptance and retention workers are synthetic in controller tests.
"""
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from common.data_contracts import resolve,sha256_file
from scripts.tiered_checkpoint_retention import atomic_json
from scripts import supervise_independent_training as m
from test_independent_runtime_contract import case,prepared,rows_for
from test_latest_independent_revision import add_checkpoint


def test_original_controller_lock_is_shared(tmp_path):
    main=tmp_path/'main';control=tmp_path/'control';main.mkdir();control.mkdir()
    with (main/'controller.lock').open('a') as other:
        fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            with m.exclusive_controls(main,control):pytest.fail('stole controller lock')
    with m.exclusive_controls(main,control):pass


def test_actual_self_record_matches_child_view():
    actual=m.self_record()
    code='import json; from scripts.horizon_process_handoff import process_record; print(json.dumps(process_record('+str(os.getpid())+')))'
    other=json.loads(subprocess.check_output([sys.executable,'-c',code],text=True,cwd=resolve('.')))
    from scripts.horizon_process_handoff import same_identity
    assert same_identity(actual,other)


@pytest.mark.parametrize('fault',['none','nested','gpus','memory'])
def test_exact_execution_resources_and_tmpdir(tmp_path,monkeypatch,fault):
    main=tmp_path/'main';scratch=tmp_path/'scratch';gpus=[{'index':i} for i in range(8)]
    c=dict(tiered_retention={'temp_root':str(scratch/'main')},gpu_identity=gpus)
    hw=dict(storage={'scratch_dir':str(scratch)},gpus=deepcopy(gpus),cpu={'affinity':list(range(96))},
        memory={'cache_aware_admission':{'headroom_gib':600}})
    monkeypatch.setenv(m.OWNER_ENV,'inherited-unrelated-token')
    if fault=='nested':hw['storage']['scratch_dir']=str(scratch/'main')
    if fault=='gpus':hw['gpus'].pop()
    if fault=='memory':hw['memory']['cache_aware_admission']['headroom_gib']=511
    if fault=='none':
        cpus,env=m.execution_settings(c,main,hw)
        assert cpus==list(range(64,96)) and env['TMPDIR']==str(scratch/'main') and m.OWNER_ENV not in env
    else:
        with pytest.raises((ValueError,RuntimeError)):m.execution_settings(c,main,hw)


def test_elapsed_clock_survives_retry_and_rejects_rebinding(tmp_path,monkeypatch):
    monkeypatch.setattr(m.time,'time',lambda:1000)
    assert m.elapsed_clock(tmp_path,'a')==0
    monkeypatch.setattr(m.time,'time',lambda:1100)
    assert m.elapsed_clock(tmp_path,'a')==100
    with pytest.raises(ValueError):m.elapsed_clock(tmp_path,'b')
    monkeypatch.setattr(m.time,'time',lambda:999)
    with pytest.raises(ValueError):m.elapsed_clock(tmp_path,'a')


def test_absence_check_rejects_actual_live_source_without_signals(tmp_path):
    from scripts.horizon_process_handoff import process_record
    proc=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
    try:
        row=process_record(proc.pid)
        roles=['supervisor','worker',*[f'rank{r}' for r in range(8)]]
        rows={name:dict(row,pid=80000000+i) for i,name in enumerate(roles)};rows['worker']=row
        capture=dict(status='observed_live_not_stopped',run=str(tmp_path),processes=rows)
        with pytest.raises(ValueError,match='still present'):m.require_absent(tmp_path,tmp_path,{'source_capture':capture})
        assert proc.poll() is None
    finally:proc.terminate();proc.wait(timeout=5)


@pytest.mark.parametrize('fault',['none','start','parent','command','environment','marker','missing'])
def test_consumption_requires_live_owned8rank(prepared,monkeypatch,fault):
    _,_,execution,a,_=prepared;directory=execution.parent/'consumption';directory.mkdir()
    rows=rows_for(a);processes={};launcher=123;start=77
    owned=SimpleNamespace(token='b'*32,proc=SimpleNamespace(pid=launcher,poll=lambda:None),
        records={launcher:{'start_ticks':start}},scan=lambda:[r['pid'] for r in rows]+[launcher])
    for r in rows:
        r.update(launcher_pid=launcher,launcher_start_ticks=start)
        processes[r['pid']]=dict(pid=r['pid'],start_ticks=r['start_ticks'],ppid=launcher,
            argv=['python','-m',m.MODULE,'--execution',str(execution)])
        atomic_json(directory/f"rank{r['rank']}.json",r)
    processes[launcher]=dict(start_ticks=start)
    proof=m.verify_consumption_rows(a,rows,owner=owned.token);atomic_json(directory/'all_ranks.json',proof)
    monkeypatch.setattr(m,'process_record',lambda pid:processes[pid])
    original=Path.read_bytes
    def read(path):
        if str(path).startswith('/proc/') and path.name=='environ':
            pid=int(path.parent.name);rank=pid-9000000
            return ('\0'.join([m.OWNER_ENV+'='+owned.token,'RANK='+str(rank),'LOCAL_RANK='+str(rank),'WORLD_SIZE='+('7' if fault=='environment' else '8')])).encode()
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',read)
    if fault=='start':processes[rows[7]['pid']]['start_ticks']+=1
    if fault=='parent':processes[rows[7]['pid']]['ppid']=999
    if fault=='command':processes[rows[7]['pid']]['argv'][-1]='/tmp/another'
    if fault=='marker':atomic_json(directory/'all_ranks.json',{'ranks':8})
    if fault=='missing':(directory/'rank7.json').unlink()
    if fault=='none':assert len(m.verify_live_consumption(execution,a,owned)['receipts'])==9
    else:
        with pytest.raises((ValueError,FileNotFoundError)):m.verify_live_consumption(execution,a,owned)


def test_queue_never_prunes_before_consumption_and_seals_before_retention(prepared,monkeypatch):
    c,_,execution,a,_=prepared;events=[];cancel=threading.Event()
    manager=SimpleNamespace(maintain=lambda:events.append('retention'))
    monkeypatch.setattr(m,'ExtendedRetention',lambda *args,**kw:manager)
    q=m.CheckpointQueue(c.root,c.contract,c.candidate['id'],cancel)
    try:
        q.admit_existing();q.tick();assert events==[]
        with pytest.raises(ValueError,match='No actual'):q.drain()
        proof={'proof':{'step':10000},'receipts':{str(execution):sha256_file(execution)}}
        q.allow_retention(proof)
        cp,cert=add_checkpoint(c,10500);cert.unlink()
        def seal(cp,out,cid):
            assert events==[];events.append('seal');assert cid==c.candidate['id'];atomic_json(out,{'synthetic':True});return {'synthetic':True}
        monkeypatch.setattr(m,'seal',seal)
        q.drain();assert events==['seal','retention'] and cp.name in q.verified
        execution.write_text(execution.read_text()+' ')
        with pytest.raises(ValueError,match='receipt changed'):q.tick()
    finally:q.close()


def test_failed_certification_never_prunes_and_preserves_payload(prepared,monkeypatch):
    c,_,_,_,_=prepared;events=[]
    monkeypatch.setattr(m,'ExtendedRetention',lambda *a,**kw:SimpleNamespace(maintain=lambda:events.append('retention')))
    q=m.CheckpointQueue(c.root,c.contract,c.candidate['id'],threading.Event())
    try:
        q.admit_existing();q.allow_retention({'proof':{'step':10000},'receipts':{}})
        cp,cert=add_checkpoint(c,10500);cert.unlink()
        def bad(*a):raise ValueError('injected full-state failure')
        monkeypatch.setattr(m,'seal',bad)
        with pytest.raises(ValueError,match='injected'):q.drain()
        assert events==[] and cp.exists() and not cert.exists()
    finally:q.close()


def test_missing_real_acceptance_prevents_hardware_or_launch(tmp_path,monkeypatch):
    control=tmp_path/'control';control.mkdir()
    calls=[];monkeypatch.setattr(m,'OwnedCommand',lambda *a:calls.append('launched'))
    with pytest.raises(FileNotFoundError):m.run(control)
    assert calls==[] and list(control.iterdir())==[]


def test_monitor_guard_failure_cleans_only_owned_actual_cpu_process(tmp_path,monkeypatch):
    execution=tmp_path/'execution.json';execution.write_text('{}');launch=tmp_path/'launch';launch.mkdir()
    owned=m.OwnedCommand([sys.executable,'-c','import time;time.sleep(30)'],launch,os.environ)
    unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
    cancel=threading.Event();queue=SimpleNamespace(tick=lambda:pytest.fail('must not mutate on guard failure'))
    def bad(*a):raise RuntimeError('injected reserve failure')
    monkeypatch.setattr(m,'runtime_guard',bad)
    try:
        with pytest.raises(RuntimeError,match='reserve'):m.monitor(owned,execution,{},tmp_path,{},tmp_path,queue,cancel)
        assert cancel.is_set() and owned.proc.poll() is not None and unrelated.poll() is None
        assert (launch/'failure.json').exists() and execution.exists()
    finally:unrelated.terminate();unrelated.wait(timeout=5)


def test_successful_exit_without_consumption_cannot_finalize(tmp_path,monkeypatch):
    execution=tmp_path/'execution.json';execution.write_text('{}');launch=tmp_path/'launch';launch.mkdir()
    owned=m.OwnedCommand([sys.executable,'-c','import time;time.sleep(.1)'],launch,os.environ)
    owned.proc.wait(timeout=5);cancel=threading.Event()
    queue=SimpleNamespace(tick=lambda:None,drain=lambda:(_ for _ in ()).throw(ValueError('No actual all8rank consumption')))
    monkeypatch.setattr(m,'runtime_guard',lambda *a:0)
    with pytest.raises(ValueError,match='all8rank'):m.monitor(owned,execution,{},tmp_path,{},tmp_path,queue,cancel)
    assert not (launch/'result.json').exists() and (launch/'failure.json').exists()


@pytest.mark.parametrize('fault',['none','elapsed','memory','disk','contract','runtime','pressure_before','pressure_busy','pressure_after'])
def test_runtime_guards_and_pressure_cannot_prune_before_consumption(tmp_path,monkeypatch,fault):
    from contextlib import nullcontext
    from scripts.horizon_storage_plan import LocalHistoryCapacityError
    main=tmp_path/'main';control=tmp_path/'control';main.mkdir();control.mkdir()
    atomic_json(main/'contract.json',{'original':True});atomic_json(main/'main.json',{'max_steps':57000})
    binding=dict(elapsed_before_seconds=10,main_contract_sha256=sha256_file(main/'contract.json'),main_config_sha256=sha256_file(main/'main.json'))
    atomic_json(control/'runtime_contract.json',{'binding':binding})
    execution=control/'execution.json';atomic_json(execution,dict(runtime_contract_sha256=sha256_file(control/'runtime_contract.json')))
    digest=sha256_file(execution)
    c=dict(max_seconds=100,max_pss_gib=1,durable_budget_bytes=m.GIB,local_budget_gib=1,tiered_retention={'temp_root':str(tmp_path)})
    owned=SimpleNamespace(scan=lambda:[],peak=0);pruned=[]
    manager=SimpleNamespace(locked=lambda:nullcontext(),make_local_room=lambda:pruned.append(True))
    q=SimpleNamespace(future=None,consumption=None,manager=manager)
    monkeypatch.setattr(m,'size_tree',lambda p:0)
    monkeypatch.setattr(m.shutil,'disk_usage',lambda p:SimpleNamespace(free=(199 if fault=='disk' else 300)*m.GIB))
    monkeypatch.setattr('scripts.process_accounting.proportional_tree',lambda p:dict(processes=[{'pid':os.getpid()}],totals_bytes={'Pss':(2*m.GIB if fault=='memory' else 1000)}))
    count=[]
    def space(*a,**kw):
        count.append(True)
        if fault.startswith('pressure') and len(count)==1:raise LocalHistoryCapacityError('injected pressure')
        return {'space':'available'}
    monkeypatch.setattr(m,'enforce_shared_space',space)
    if fault=='contract':atomic_json(main/'main.json',{'max_steps':35000})
    if fault=='runtime':atomic_json(control/'runtime_contract.json',{'binding':'different'})
    if fault=='pressure_busy':q.consumption={'proved':True};q.future=SimpleNamespace(done=lambda:False)
    if fault=='pressure_after':q.consumption={'proved':True}
    if fault in ('none','pressure_after'):
        assert m.runtime_guard(main,c,control,execution,digest,1,owned,q)==1000
        assert len(pruned)==(1 if fault=='pressure_after' else 0)
    else:
        with pytest.raises((ValueError,RuntimeError)):m.runtime_guard(main,c,control,execution,digest,100 if fault=='elapsed' else 1,owned,q)
        assert not pruned


def test_monitor_success_publishes_receipt_after_consumption_and_drain(tmp_path,monkeypatch):
    execution=tmp_path/'execution.json';execution.write_text('{}');launch=tmp_path/'launch';launch.mkdir()
    consumption=tmp_path/'consumption';consumption.mkdir();atomic_json(consumption/'all_ranks.json',{'synthetic':True})
    events=[];cancel=threading.Event()
    proc=SimpleNamespace(pid=123,poll=lambda:0)
    owned=SimpleNamespace(proc=proc,token='a'*32,records={},scan=lambda:[],close=lambda:events.append('close'))
    queue=SimpleNamespace(allow_retention=lambda proof:events.append('consumed'),tick=lambda:events.append('tick'),drain=lambda:events.append('drained'))
    monkeypatch.setattr(m,'runtime_guard',lambda *a:1000)
    monkeypatch.setattr(m,'verify_live_consumption',lambda *a:{'synthetic':True})
    result=m.monitor(owned,execution,{},tmp_path,{},tmp_path,queue,cancel)
    assert events==['consumed','tick','drained','close'] and result['status']=='owned_main_exited_successfully'
    assert (launch/'result.json').exists() and not cancel.is_set()


@pytest.mark.parametrize('fault',['none','missing_final','pid_publication'])
def test_controller_completion_and_post_launch_failure_cleanup(tmp_path,monkeypatch,fault):
    """Actual CPU owned child/locks/file publication; admissions/GPU/model mocked."""
    from contextlib import nullcontext
    main=tmp_path/'main';control=tmp_path/'control';main.mkdir();control.mkdir()
    (main/'training').mkdir();(main/'checkpoint_certificates').mkdir()
    atomic_json(main/'contract.json',{'synthetic':True});atomic_json(control/'runtime_contract.json',{'synthetic':True})
    runtime={'binding':{'elapsed_before_seconds':0}};contract={};events=[];launched=[]
    monkeypatch.setattr(m,'runtime_inputs',lambda p:(runtime,contract,{},main))
    monkeypatch.setattr(m,'require_absent',lambda *a:None)
    monkeypatch.setattr('scripts.resource_admission.inspect',lambda:{'synthetic':True})
    monkeypatch.setattr(m,'execution_settings',lambda *a:([min(os.sched_getaffinity(0))],dict(os.environ,TMPDIR=str(tmp_path/'scratch'))))
    monkeypatch.setattr(m,'enforce_shared_space',lambda *a:{'synthetic':True})
    monkeypatch.setattr('scripts.evaluate_final_rehearsal.acquire_idle_gpu',lambda callback:nullcontext())
    execution=control/'attempts'/'synthetic'/'execution.json';execution.parent.mkdir(parents=True)
    (execution.parent/'launch').mkdir()
    attempt=dict(training_contract_id='synthetic',runtime_contract_sha256=sha256_file(control/'runtime_contract.json'))
    atomic_json(execution,attempt)
    monkeypatch.setattr(m,'prepare_attempt',lambda *a:(execution,attempt))
    q=SimpleNamespace(verified={},admit_existing=lambda:events.append('admitted'),close=lambda:events.append('queue_closed'))
    monkeypatch.setattr(m,'CheckpointQueue',lambda *a:q)
    original_owned=m.OwnedCommand
    def own(command,directory,env):
        assert command[-4:]==['--module',m.MODULE,'--execution',str(execution)]
        child=original_owned([sys.executable,'-c','import time;time.sleep(30)'],directory,env);launched.append(child);return child
    monkeypatch.setattr(m,'OwnedCommand',own)
    def monitor(child,*args,**kw):
        child.cleanup();child.close()
        if fault!='missing_final':
            cp=main/'training/ckpt_0057000';cp.mkdir()
            for name in ['.complete','.metadata','meta.json',*[f'rng_rank{i}.pt' for i in range(8)],*[f'__{i}_0.distcp' for i in range(8)]]:
                (cp/name).write_bytes(b'synthetic-not-GPU')
            atomic_json(cp/'meta.json',dict(step=57000,training_contract={'id':'synthetic'}))
            files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in cp.iterdir()]
            proof=dict(files=files,bytes=sum(r['bytes'] for r in files),step=57000,training_contract_id='synthetic')
            q.verified[cp.name]=proof;atomic_json(main/'checkpoint_certificates'/(cp.name+'.json'),proof)
            atomic_json(main/'retention_state.json',{'synthetic':True})
        return dict(elapsed_seconds=1,peak_pss_bytes=1000)
    monkeypatch.setattr(m,'monitor',monitor)
    original_write=Path.write_text
    def write(path,*a,**kw):
        if fault=='pid_publication' and path==main/'pid':raise OSError('injected pid write failure after launch')
        return original_write(path,*a,**kw)
    monkeypatch.setattr(Path,'write_text',write)
    if fault=='none':
        result=m.run(control)
        assert result['status']=='main_target_completed_review_pending'
        assert result['trained_tokens']==119537664000 and (main/'.done').is_file()
    else:
        with pytest.raises((ValueError,OSError)):m.run(control)
        assert not (main/'.done').exists() and json.loads((control/'state.json').read_text())['phase']=='error'
    assert len(launched)==1 and launched[0].proc.poll() is not None and launched[0].journal.closed
    assert events==['admitted','queue_closed']
