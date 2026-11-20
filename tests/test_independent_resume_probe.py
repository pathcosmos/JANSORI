"""Actual tiny CPU DCP plus diagnostic schedule/scope tests; no8GPU proof."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
import torch.distributed as dist
import torch.distributed.checkpoint as dcp

from common.data_contracts import atomic_json,identity
from train.independent_resume_probe import (restore_state,save_state,restore_own_source,
    diagnostic_lr,check_spec,required_absent_paths,MODES)
from scripts.verify_independent_resume_probe import ledger_review
from scripts.verify_final_resume_probe import compare_states


@pytest.fixture
def gloo(tmp_path):
    dist.init_process_group('gloo',init_method='file://'+str(tmp_path/'rendezvous'),rank=0,world_size=1)
    torch.set_num_threads(2)
    try:yield
    finally:dist.destroy_process_group()


def model():
    m=torch.nn.Sequential(torch.nn.Linear(4,8),torch.nn.Dropout(.25),torch.nn.Linear(8,2))
    return m,torch.optim.AdamW(m.parameters(),lr=.001)


def update(m,opt):
    loss=(m(torch.randn(3,4))-torch.randn(3,2)).square().mean();loss.backward();opt.step();opt.zero_grad(set_to_none=True)
    return loss.item()


def test_actual_dcp_through_own_adapter_hook_and_split_resume(tmp_path,monkeypatch,gloo):
    torch.manual_seed(391);m,opt=model();update(m,opt);source=tmp_path/'source'
    save_state(m,opt,source,0,'cpu',dict(step=1,fixture=True))
    events=[]
    class Adapter:
        def checkpoints(self,directory):return [source]
        def validate(self,*a):events.append('validate')
        def load(self,loader,*args,**kw):
            assert kw['checkpoint_id']==source;events.append('own_dcp_load');return loader(*args,**kw)
    class Admission:
        contract={};config={}
        def __init__(self,*a):pass
        def handoff(self,*a):return {},{},{}
        def resume(self,*a):return Adapter()
        def check(self):events.append('after_load')
    monkeypatch.setattr('train.independent_resume_bridge.PreparedIndependentAdmission',Admission)
    spec={'plan':dict(main_run=str(tmp_path),revision=str(tmp_path/'revision'),parent_step=1)}
    c=dict(out_dir=str(tmp_path));a,ao=model();load=dcp.load
    restore_own_source(spec,c,a,ao,source,0,'cpu');assert dcp.load is load
    save_state(a,ao,tmp_path/'restored',0,'cpu',dict(step=1))
    assert compare_states(source,tmp_path/'restored',world=1)['exact']
    continuous=[update(a,ao),update(a,ao)];save_state(a,ao,tmp_path/'continuous',0,'cpu',dict(step=3))
    b,bo=model();restore_own_source(spec,c,b,bo,source,0,'cpu');first=update(b,bo)
    save_state(b,bo,tmp_path/'first',0,'cpu',dict(step=2))
    r,ro=model();restore_state(r,ro,tmp_path/'first',0,'cpu',2);second=update(r,ro)
    save_state(r,ro,tmp_path/'resumed',0,'cpu',dict(step=3))
    assert [first,second]==continuous and compare_states(tmp_path/'continuous',tmp_path/'resumed',world=1)['exact']
    assert events==['validate','own_dcp_load','after_load']*2
    # Actual DCP succeeds, but wrong optimizer step fails and restores global loader.
    with pytest.raises(ValueError,match='optimizer step'):
        restore_own_source({'plan':dict(spec['plan'],parent_step=2)},c,*model(),source,0,'cpu')
    assert dcp.load is load


def rows(step):
    cfg=dict(max_steps=57000,warmup=1000,lr=.0003,min_lr_ratio=.1,decay_frac=.15,seq_len=4096,global_tokens=2097152)
    result=[]
    for mode,(begin,end) in MODES.items():
        for rank in range(8):
            result.append(dict(mode=mode,rank=rank,success=True,plan_id='fixture',parent_step=step,diagnostic_only=True,
                steps=[dict(step=s+1,source_sequences={'ko':64}) for s in range(step+begin,step+end)],
                metrics=[dict(step=s+1,loss=2.+(s-step)*.01,gnorm=.1,lr=diagnostic_lr(cfg,s)) for s in range(step+begin,step+end)]))
    return cfg,result


@pytest.mark.parametrize('step',[10500,11000,48500,56500])
def test_original_schedule_and_diagnostic_replay_counting(step):
    cfg,r=rows(step);report=ledger_review(r,parent_step=step,world=8,config=cfg,source_order=['ko'],plan_id='fixture')
    assert report['main_committed_tokens_added']==0 and report['actual_diagnostic_committed_tokens']==4*2097152
    assert report['unique_diagnostic_tokens']==2*2097152
    bad=deepcopy(r);bad[-1]['metrics'][0]['lr']=diagnostic_lr(cfg,56999)
    with pytest.raises(ValueError,match='LR'):ledger_review(bad,parent_step=step,world=8,config=cfg,source_order=['ko'],plan_id='fixture')
    with pytest.raises(ValueError,match='Incomplete'):ledger_review(r[:-1],parent_step=step,world=8,config=cfg,source_order=['ko'],plan_id='fixture')


@pytest.mark.parametrize('bad',[35000.0,57000,10000,10501])
def test_unreviewed_boundary_rejected_before_any_source_or_cuda(bad):
    p=dict(purpose='isolated_independent_nfs_resume_probe',parent_step=bad,world_size=8,updates=2,main_target_modified=False,lr_policy='original_schedule_next_updates')
    with pytest.raises(ValueError,match='scope'):check_spec(dict(plan=p,plan_id=identity(p)))


def test_old_parent_and_local_proof_coverage(tmp_path):
    main=tmp_path/'main';prior=tmp_path/'prior';main.mkdir();prior.mkdir()
    atomic_json(main/'parent_handoff.json',dict(source_run=str(prior),parent_checkpoint=str(main/'parent_checkpoint/ckpt_0010000')))
    atomic_json(prior/'parent_handoff.json',dict(parent_checkpoint=str(prior/'parent_checkpoint/ckpt_0008000')))
    revision=dict(local_proof_backups={str(tmp_path/'local-proof.json'):{}})
    paths=required_absent_paths(main,{},revision)
    assert paths==sorted([str(main/'parent_checkpoint/ckpt_0010000'),str(prior/'parent_checkpoint/ckpt_0008000'),str(tmp_path/'local-proof.json')])
