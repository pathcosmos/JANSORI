"""One fresh content review, eight exact consumers; no actual CUDA claim."""
import json
import shutil
from pathlib import Path

import pytest

from common.data_contracts import atomic_json,sha256_file
from test_independent_checkpoint_handoff import case
from test_independent_operational_admission import prepare
from train.independent_operational_admission import PreparedOperationalAdmission


def publish(c):
    a=prepare(c)
    binding={'purpose':'diagnostic','plan_id':'exact-test-attempt'}
    path=c.review_path.parent/'shared.json'
    atomic_json(path,a.shared_record(binding))
    return a,path,sha256_file(path),binding


def test_eight_ranks_reuse_one_fresh_payload_review(case,monkeypatch):
    import train.independent_checkpoint_handoff as handoff
    import scripts.tiered_checkpoint_retention as retention
    calls=[];original=handoff.inventory
    def inventory(*args,**kw):
        if kw.get('hashes'):calls.append(str(args[0]))
        return original(*args,**kw)
    monkeypatch.setattr(handoff,'inventory',inventory)
    monkeypatch.setattr(retention,'inventory',inventory)
    c=case;a,path,digest,binding=publish(c)
    assert calls==[str(c.child)]
    shutil.rmtree(c.parent)
    # Native rank0 may atomically rewrite identical JSON without invalidating
    # other rank admissions. Changed content must still fail.
    atomic_json(c.root/'training/training_contract.json',c.candidate)
    for rank in range(8):
        b=PreparedOperationalAdmission.from_shared(path,digest,rank=rank,binding=binding)
        assert b.rank==rank and b.candidate==a.candidate and b.child_stats==a.child_stats
        assert not b.review['actual8gpu_independent_resume_verified']
        assert b.handoff(c.root,c.contract,c.config)==(a.receipt,a.candidate,a.proof)
    assert calls==[str(c.child)]


@pytest.mark.parametrize('fault',['digest','binding','payload','missing_child','contract','code',
    'missing_coverage','bridge_code','newer_complete','local_source','certificate','claimed_authorization'])
def test_stale_or_substituted_shared_receipt_rejected(case,fault,tmp_path):
    c=case;a,path,digest,binding=publish(c)
    data=json.loads(path.read_text())
    if fault=='digest':path.write_text(path.read_text()+' ')
    elif fault=='binding':binding={'purpose':'diagnostic','plan_id':'another-attempt'}
    elif fault=='payload':
        p=c.child/'__0_0.distcp';p.write_bytes(b'x'*p.stat().st_size)
    elif fault=='missing_child':shutil.rmtree(c.child)
    elif fault=='contract':atomic_json(c.root/'training/training_contract.json',dict(c.candidate,id='wrong'))
    elif fault=='code':data['operational_code']['train/independent_operational_admission.py']='0'*64
    elif fault=='missing_coverage':data['source_sha256'].pop(str(c.root/'main.json'))
    elif fault=='bridge_code':data['bridge_code']['train/independent_resume_bridge.py']='0'*64
    elif fault=='newer_complete':
        newer=c.child.with_name('ckpt_0010500');newer.mkdir();(newer/'.complete').touch()
    elif fault=='local_source':
        local=tmp_path/c.child.name;shutil.copytree(c.child,local);data['child']=str(local)
    elif fault=='certificate':c.cert.write_text(c.cert.read_text()+' ')
    elif fault=='claimed_authorization':data['production_adoption_authorized']=True
    if fault in {'code','missing_coverage','bridge_code','local_source','claimed_authorization'}:
        # Even a caller rebinding the digest cannot omit required semantic
        # coverage. This does not pretend to protect a compromised controller.
        atomic_json(path,data);digest=sha256_file(path)
    with pytest.raises((ValueError,FileNotFoundError)):
        PreparedOperationalAdmission.from_shared(path,digest,rank=0,binding=binding)


def test_probe_shared_path_restores_without_another_fresh_review(case,monkeypatch):
    import torch
    import torch.distributed.checkpoint as dcp
    from train import independent_resume_probe as probe
    from common.data_contracts import identity
    from test_independent_operational_admission import consume
    c=case;a=prepare(c);root=c.review_path.parent
    plan=dict(main_run=str(c.root),revision=str(c.review_path),run_dir=str(root),parent_step=10000,
        operational_consumption_required=True,shared_admission_required=True)
    path=root/'shared_admission.json'
    atomic_json(path,a.shared_record(dict(purpose='independent_resume_probe',plan_id=identity(plan))))
    spec=dict(plan=plan,parent_checkpoint=str(c.child),parent_stats=a.child_stats,
        shared_admission=dict(path=str(path),sha256=sha256_file(path)))
    def forbidden(*args,**kw):raise AssertionError('Unexpected repeated full admission')
    monkeypatch.setattr(PreparedOperationalAdmission,'__init__',forbidden)
    monkeypatch.setattr(torch.cuda,'set_rng_state',lambda *args:None)
    monkeypatch.setattr(dcp,'load',lambda *args,**kwargs:None)
    def restore(model,optimizer,source,rank,device,step):
        dcp.load({},checkpoint_id=source);consume(c,rank)
    monkeypatch.setattr(probe,'restore_state',restore)
    row=probe._restore_own_source(spec,c.config,None,None,c.child,0,torch.device('cuda',0))
    assert row['model_optimizer_load_completed'] and row['cpu_cuda_rng_applied']
    # Historical check uses archived bytes, never current source availability.
    raw=path.read_bytes();path.unlink()
    assert probe.check_shared_binding(spec,read_bytes=lambda p:raw)['child']==str(c.child)
    with pytest.raises(ValueError,match='digest'):
        probe.check_shared_binding(spec,read_bytes=lambda p:raw+b' ')


def test_controller_publishes_shared_receipt_before_spec_and_preserves_reentry(case,monkeypatch):
    from scripts import run_independent_resume as controller
    from train.independent_resume_probe import check_shared_binding
    c=case;prepare(c);root=c.review_path.parent/'diagnostic';root.mkdir()
    plan=dict(main_run=str(c.root),revision=str(c.review_path),run_dir=str(root),parent_step=10000,
        operational_consumption_required=True,shared_admission_required=True,source_capture={})
    monkeypatch.setattr(controller,'stopped_source',lambda p:(c.root,c.contract))
    monkeypatch.setattr(controller,'audit_run',lambda *args:{'fixture':'prefix audit checked separately'})
    monkeypatch.setattr(controller,'fresh_inputs',lambda *args:{})
    monkeypatch.setattr(controller,'check_spec',check_shared_binding)
    def freeze(audit,out,capture):
        out.mkdir();atomic_json(out/'verified.json',{'fixture':True});return {'manifest_sha256':'fixture'}
    monkeypatch.setattr('scripts.freeze_independent_prefix.freeze',freeze)
    spec=controller.materialize_spec(root,plan)
    check_shared_binding(spec)
    assert json.loads((root/'spec.json').read_text())==spec
    before=(root/'shared_admission.json').read_bytes()
    with pytest.raises(ValueError,match='preserved'):controller.materialize_spec(root,plan)
    assert (root/'shared_admission.json').read_bytes()==before
