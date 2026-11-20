"""Real lineage and CPU RNG; synthetic checkpoint DCP/CUDA application."""
import json
from pathlib import Path
import shutil

import pytest
import torch

from common.data_contracts import atomic_json,sha256_file
from test_independent_checkpoint_handoff import case
from train.independent_operational_admission import PreparedOperationalAdmission
from train import monitored_extended_horizon as worker


def prepare(c,rank=0):
    c.write(c.root/'training/training_contract.json',c.candidate)
    torch.save(dict(cpu=torch.get_rng_state(),cuda=torch.get_rng_state().clone()),c.child/f'rng_rank{rank}.pt')
    cert=json.loads(c.cert.read_text())
    cert['files']=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in sorted(c.child.iterdir())]
    cert['bytes']=sum(r['bytes'] for r in cert['files']);c.write(c.cert,cert)
    c.revision['child']['certificate_sha256']=sha256_file(c.cert);c.write(c.review_path,c.revision)
    return PreparedOperationalAdmission(c.root,c.review_path,rank=rank)


def select(a,c,module):
    receipt,candidate,proof=module.load_handoff(c.root,c.contract,c.config)
    adapter=module.parent_resume(receipt,candidate,proof,c.config,c.contract)
    assert adapter.checkpoints(c.root/'training')==[c.child]
    adapter.validate(json.loads((c.child/'meta.json').read_text()),c.candidate)
    adapter.load(lambda **kw:None,checkpoint_id=c.child)
    return adapter


def consume(c,rank=0):
    rng=torch.load(c.child/f'rng_rank{rank}.pt',map_location='cpu',weights_only=True)
    torch.set_rng_state(rng['cpu']);torch.cuda.set_rng_state(rng['cuda'],torch.device('cuda',rank))


@pytest.mark.parametrize('rank',[0,7])
def test_rotation_after_full_consumption_preserves_original_numerical_worker(case,monkeypatch,rank):
    c=case;a=prepare(c,rank);records=[];cuda_calls=[]
    monkeypatch.setattr(torch.cuda,'set_rng_state',lambda state,device:cuda_calls.append((state,device)))
    original=(worker.worker,worker.load_handoff,worker.parent_resume,torch.load,torch.set_rng_state,torch.cuda.set_rng_state)
    with a.operational_hooks(records.append) as module:
        assert module.worker is original[0]
        adapter=select(a,c,module)
        # Native atomic rewrite may change inode/format, never identity/content.
        atomic_json(c.root/'training/training_contract.json',c.candidate)
        a.check();consume(c,rank)
        assert adapter.consumed and len(records)==1 and len(cuda_calls)==1
        shutil.rmtree(c.child);shutil.rmtree(c.parent)
        a.check();assert adapter.checkpoints(c.root/'training')==[]
        with pytest.raises(ValueError,match='only once'):adapter.load(lambda **kw:None,checkpoint_id=c.child)
    assert (worker.worker,worker.load_handoff,worker.parent_resume,torch.load,torch.set_rng_state,torch.cuda.set_rng_state)==original
    assert records[0]['rank']==rank and records[0]['training_contract_id']==c.candidate['id']
    assert not records[0]['actual8gpu_acceptance']


@pytest.mark.parametrize('fault',['before_rng','wrong_rank','cuda_before_cpu','wrong_device','cuda_failure','input_change','callback_failure'])
def test_incomplete_restore_or_mutation_cannot_finish(case,monkeypatch,fault):
    c=case;a=prepare(c);records=[]
    def setter(state,device):
        if fault=='cuda_failure':raise RuntimeError('injected CUDA restore failure')
    monkeypatch.setattr(torch.cuda,'set_rng_state',setter)
    original=(worker.load_handoff,worker.parent_resume,torch.load,torch.set_rng_state,torch.cuda.set_rng_state)
    def accept(row):
        if fault=='callback_failure':raise RuntimeError('injected receipt failure')
        records.append(row)
    with pytest.raises((ValueError,RuntimeError,FileNotFoundError)):
        with a.operational_hooks(accept) as module:
            adapter=select(a,c,module)
            if fault=='before_rng':shutil.rmtree(c.child);return
            if fault=='wrong_rank':torch.load(c.child/'rng_rank1.pt',weights_only=True);return
            if fault=='cuda_before_cpu':
                rng=torch.load(c.child/'rng_rank0.pt',weights_only=True)
                torch.cuda.set_rng_state(rng['cuda'],torch.device('cuda',0));return
            if fault=='wrong_device':
                rng=torch.load(c.child/'rng_rank0.pt',weights_only=True);torch.set_rng_state(rng['cpu'])
                torch.cuda.set_rng_state(rng['cuda'],torch.device('cuda',1));return
            consume(c)
            if fault=='input_change':
                altered=dict(c.candidate,id='wrong');atomic_json(c.root/'training/training_contract.json',altered)
    assert (worker.load_handoff,worker.parent_resume,torch.load,torch.set_rng_state,torch.cuda.set_rng_state)==original
    if fault not in ('input_change',):assert not records


def test_missing_full_restore_and_nested_hooks_rejected(case,monkeypatch):
    c=case;a=prepare(c)
    monkeypatch.setattr(torch.cuda,'set_rng_state',lambda *a:None)
    with pytest.raises(ValueError,match='without full'):
        with a.operational_hooks(lambda r:None):pass
    with pytest.raises(ValueError,match='Conflicting'):
        with a.operational_hooks(lambda r:None):
            with a.operational_hooks(lambda r:None):pass


@pytest.mark.parametrize('failure',[False,True])
def test_probe_branch_uses_operational_hooks_and_restores_loaders(case,monkeypatch,failure):
    from train import independent_resume_probe as probe
    import torch.distributed.checkpoint as dcp
    c=case;a=prepare(c)
    monkeypatch.setattr('train.independent_operational_admission.PreparedOperationalAdmission',lambda *args,**kw:a)
    monkeypatch.setattr(torch.cuda,'set_rng_state',lambda state,device:None)
    monkeypatch.setattr(dcp,'load',lambda *args,**kwargs:None)
    original=(torch.load,torch.set_rng_state,torch.cuda.set_rng_state,dcp.load)
    def restore(model,optimizer,source,rank,device,step):
        dcp.load({},checkpoint_id=source)
        if failure:raise RuntimeError('injected after DCP before RNG')
        consume(c,rank)
    monkeypatch.setattr(probe,'restore_state',restore)
    spec={'plan':dict(main_run=str(c.root),revision=str(c.review_path),parent_step=10000,
                      operational_consumption_required=True)}
    if failure:
        with pytest.raises(RuntimeError,match='before RNG'):
            probe._restore_own_source(spec,c.config,None,None,c.child,0,torch.device('cuda',0))
        assert not a.adapter.consumed
    else:
        row=probe._restore_own_source(spec,c.config,None,None,c.child,0,torch.device('cuda',0))
        assert row['status']=='rank_own_nfs_state_consumed' and row['rank']==0 and a.adapter.consumed
    assert (torch.load,torch.set_rng_state,torch.cuda.set_rng_state,dcp.load)==original
