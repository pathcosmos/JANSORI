"""Real source contracts/shared review, synthetic authorization/acceptance."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile

import pytest
from common.data_contracts import resolve,sha256_file,atomic_json,identity
from test_independent_checkpoint_handoff import case,rebind
from test_independent_operational_admission import prepare as fresh
from train import independent_runtime_contract as gate
from train import monitored_independent_horizon as entry


@pytest.fixture
def prepared(case,monkeypatch):
    import torch
    c=case;tmp=tempfile.TemporaryDirectory(dir=resolve('runs'),prefix='runtime-fixture-');c.addCleanup(tmp.cleanup)
    pool=Path(tmp.name);root=pool/'control';root.mkdir();(pool/'diagnostic').mkdir()
    c.contract['shared_storage']['nfs_pools']['transition']=dict(paths=[str(pool)],budget_bytes=2**30)
    c.contract['tiered_retention']={'temp_root':'/tmp/test-only-runtime'}
    c.contract['framework']=dict(torch=str(torch.__version__),cuda=torch.version.cuda)
    rebind(c);admission=fresh(c)
    # All current runtime implementation pins are real; GPU acceptance and
    # authorization remain explicit synthetic fixtures, never production.
    needed=gate.REQUIRED_CODE
    monkeypatch.setattr('scripts.supervise_extended_training.require_authorization',lambda c:None)
    accepted=dict(parent_step=10000,parent_training_contract_id=c.candidate['id'])
    monkeypatch.setattr(gate,'verify_acceptance',lambda *a:accepted)
    binding=dict(main_run=str(c.root),main_contract_sha256=sha256_file(c.root/'contract.json'),
        main_config_sha256=sha256_file(c.root/'main.json'),target_steps=57000,target_tokens=119537664000,
        rank_updates=456000,world_size=8,nfs_keep=3,local_max_keep=15,
        code={p:sha256_file(resolve(p)) for p in needed},
        independent8gpu={'run':str(pool/'diagnostic'),'manifest_sha256':'fixture','verified_sha256':'fixture'})
    auth=root/'authorization.json';atomic_json(auth,dict(purpose='independent_main_runtime_authorization_v1',
        authorized=True,binding_id=identity(binding),main_run=str(c.root)))
    runtime=root/'runtime_contract.json';atomic_json(runtime,dict(purpose='independent_main_runtime_v1',binding=binding,
        authorization=dict(path=str(auth),sha256=sha256_file(auth))))
    name='independent-'+'a'*32;folder=root/'attempts'/name;folder.mkdir(parents=True)
    bound=dict(purpose='independent_main_attempt',runtime_contract_sha256=sha256_file(runtime),attempt=name)
    shared=folder/'shared_admission.json';atomic_json(shared,admission.shared_record(bound))
    attempt=dict(purpose='independent_main_attempt_v1',attempt=name,main_run=str(c.root),world_size=8,
        runtime_contract=str(runtime),runtime_contract_sha256=sha256_file(runtime),
        revision=str(c.review_path),revision_sha256=sha256_file(c.review_path),
        training_contract_id=c.candidate['id'],shared_admission=dict(path=str(shared),sha256=sha256_file(shared)),
        supervisor={'fixture':True})
    execution=folder/'execution.json';atomic_json(execution,attempt)
    return c,root,execution,attempt,admission


def test_exact_runtime_and_shared_attempt(prepared):
    c,root,path,a,_=prepared
    actual,(rc,mc,accepted),binding=gate.validate_attempt(path)
    assert actual==a and mc==c.contract and accepted['parent_training_contract_id']==c.candidate['id']
    assert binding['runtime_contract_sha256']==sha256_file(root/'runtime_contract.json')


@pytest.mark.parametrize('fault',['authorization','config','code','coverage','target','shared','revision','attempt_name','accepted_step'])
def test_runtime_mismatch_rejected_before_entry(prepared,monkeypatch,fault):
    c,root,path,a,_=prepared;runtime=root/'runtime_contract.json';rc=json.loads(runtime.read_text())
    if fault=='authorization':
        auth=root/'authorization.json';v=json.loads(auth.read_text());v['authorized']=False;atomic_json(auth,v)
        rc['authorization']['sha256']=sha256_file(auth)
    elif fault=='config':(c.root/'main.json').write_text((c.root/'main.json').read_text()+' ')
    elif fault=='code':rc['binding']['code']['train/independent_runtime_contract.py']='0'*64
    elif fault=='coverage':del rc['binding']['code']['train/independent_runtime_contract.py']
    elif fault=='target':rc['binding']['target_steps']=35000
    elif fault=='shared':Path(a['shared_admission']['path']).write_text('{}')
    elif fault=='revision':c.review_path.write_text(c.review_path.read_text()+' ')
    elif fault=='attempt_name':a['attempt']='independent-'+('b'*32)
    elif fault=='accepted_step':monkeypatch.setattr(gate,'verify_acceptance',lambda *args:dict(parent_step=10500,parent_training_contract_id=c.candidate['id']))
    if fault in ('authorization','code','coverage','target'):
        atomic_json(runtime,rc);a['runtime_contract_sha256']=sha256_file(runtime)
    atomic_json(path,a)
    with pytest.raises(ValueError):gate.validate_attempt(path)


def rows_for(a):
    shared=json.loads(Path(a['shared_admission']['path']).read_text());rows=[]
    for rank in range(8):
        row=dict(rank=rank,local_rank=rank,world_size=8,backend='nccl',device_uuid=f'synthetic-{rank}',
            pid=9000000+rank,start_ticks=1,attempt=a['attempt'],owner='b'*32,
            runtime_contract_sha256=a['runtime_contract_sha256'],shared_admission_sha256=a['shared_admission']['sha256'])
        row['consumption']=dict(status='rank_own_nfs_state_consumed',source_run=a['main_run'],rank=rank,
            checkpoint=shared['child'],step=shared['child_proof']['step'],training_contract_id=a['training_contract_id'],
            checkpoint_certificate_id=identity(shared['child_proof']),pid=row['pid'],start_ticks=row['start_ticks'],
            model_optimizer_load_completed=True,cpu_cuda_rng_applied=True,source_may_rotate_after_all8rank_consumption=True,
            actual8gpu_acceptance=False)
        rows.append(row)
    return rows


@pytest.mark.parametrize('fault',['none','missing','rank','owner','device','pid','rng','backend','attempt'])
def test_exact_all_rank_consumption_required(prepared,fault):
    _,_,_,a,_=prepared;rows=rows_for(a)
    if fault=='missing':rows.pop()
    elif fault=='rank':rows[-1]['rank']=0
    elif fault=='owner':rows[-1]['owner']='x'
    elif fault=='device':rows[-1]['device_uuid']=rows[0]['device_uuid']
    elif fault=='pid':rows[-1]['pid']=rows[0]['pid'];rows[-1]['consumption']['pid']=rows[0]['pid']
    elif fault=='rng':rows[-1]['consumption']['cpu_cuda_rng_applied']=False
    elif fault=='backend':rows[-1]['backend']='gloo'
    elif fault=='attempt':rows[-1]['attempt']='wrong'
    if fault=='none':assert gate.verify_consumption_rows(a,rows,owner='b'*32)['ranks']==8
    else:
        with pytest.raises(ValueError):gate.verify_consumption_rows(a,rows,owner='b'*32)


def test_entry_checks_before_original_worker_and_rank_environment(prepared,monkeypatch):
    _,_,path,_,_=prepared;called=[]
    from train import monitored_extended_horizon as original
    monkeypatch.setattr(original,'worker',lambda *args:called.append(True))
    monkeypatch.delenv('RANK',raising=False)
    with pytest.raises(ValueError,match='environment'):entry.worker(path)
    for k,v in dict(RANK='0',LOCAL_RANK='0',WORLD_SIZE='8',LOCAL_WORLD_SIZE='8',CUDA_VISIBLE_DEVICES='0,1,2,3,4,5,6,7',TMPDIR='/wrong').items():monkeypatch.setenv(k,v)
    with pytest.raises(ValueError,match='temporary'):entry.worker(path)
    assert not called


@pytest.mark.parametrize('missing_rank',[False,True])
def test_wrapper_uses_original_hooks_and_requires_all_consumers(prepared,monkeypatch,missing_rank):
    import torch
    import torch.distributed as dist
    from types import SimpleNamespace
    from train import monitored_extended_horizon as original
    from test_independent_operational_admission import select,consume
    c,_,path,a,_=prepared;out=path.parent/'consumption';out.mkdir()
    for row in rows_for(a)[1:]:
        if not (missing_rank and row['rank']==7):atomic_json(out/f"rank{row['rank']}.json",row)
    (path.parent/'launch').mkdir();atomic_json(path.parent/'launch/handle.json',{'fixture':True})
    launch=dict(launcher_pid=1234,launcher_start_ticks=1,owner='b'*32)
    monkeypatch.setattr(entry,'launcher_identity',lambda *args:launch)
    for k,v in dict(RANK='0',LOCAL_RANK='0',WORLD_SIZE='8',LOCAL_WORLD_SIZE='8',CUDA_VISIBLE_DEVICES='0,1,2,3,4,5,6,7',TMPDIR=c.contract['tiered_retention']['temp_root']).items():monkeypatch.setenv(k,v)
    monkeypatch.setattr(dist,'is_initialized',lambda:True);monkeypatch.setattr(dist,'get_backend',lambda:'nccl')
    monkeypatch.setattr(dist,'get_world_size',lambda:8);monkeypatch.setattr(dist,'get_rank',lambda:0)
    barriers=[];monkeypatch.setattr(dist,'barrier',lambda:barriers.append(True))
    monkeypatch.setattr(torch.cuda,'current_device',lambda:0)
    monkeypatch.setattr(torch.cuda,'get_device_properties',lambda rank:SimpleNamespace(uuid=f'synthetic-{rank}'))
    monkeypatch.setattr(torch.cuda,'set_rng_state',lambda *args:None)
    calls=[]
    def numerical(main,config,attempt):
        calls.append((main,config,attempt));select(None,c,original);consume(c)
    monkeypatch.setattr(original,'worker',numerical)
    before=(original.load_handoff,original.parent_resume,torch.load,torch.set_rng_state,torch.cuda.set_rng_state)
    if missing_rank:
        with pytest.raises(FileNotFoundError):entry.worker(path)
        assert not (out/'all_ranks.json').exists()
    else:
        entry.worker(path)
        assert json.loads((out/'all_ranks.json').read_text())['ranks']==8 and len(barriers)==2
        with pytest.raises(ValueError,match='Preserve'):entry.worker(path)
    assert calls==[(c.root,c.root/'main.json',a['attempt'])]
    assert before==(original.load_handoff,original.parent_resume,torch.load,torch.set_rng_state,torch.cuda.set_rng_state)
