"""Actual tiny NFS→local copy/SHA/retire; runtime8GPU proof is mocked."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace

import pytest
from common.data_contracts import resolve,sha256_file
from scripts.tiered_checkpoint_retention import atomic_json
from scripts import independent_parent_retention as m
from scripts.archive_retire_historical_checkpoints import archive_one,retire_one
from scripts.copy_reviewed_checkpoint_archive import snapshot
from scripts.audit_checkpoint_retention import Snapshot,checkpoint_content
from train.final_resume_probe import file_stats


@pytest.fixture
def retired(monkeypatch):
    import torch
    import torch.distributed.checkpoint as dcp
    torch.set_num_threads(2)
    with tempfile.TemporaryDirectory(dir=resolve('runs'),prefix='parent-retire-test-') as nfs, tempfile.TemporaryDirectory(prefix='parent-retire-local-') as tmp:
        base=Path(nfs);roots=[base/n for n in ['original','middle','main']]
        for p in roots:p.mkdir()
        main=roots[-1];control=base/'control';control.mkdir();local=Path(tmp)/'independent_parent_archive_v1'
        contract=dict(retention_branches=[dict(run=str(p),policy=dict(local_root=str(Path(tmp)/p.name))) for p in roots],
            tiered_retention=dict(temp_root=tmp,local_root=str(Path(tmp)/'main')),shared_storage=dict(local_temp_paths=[tmp]))
        atomic_json(main/'contract.json',contract)
        view=SimpleNamespace(main=main,control=control,contract=contract,digest='a'*64,runtime={'synthetic_runtime':True},unchanged=lambda:None)
        code={p:sha256_file(resolve(p)) for p in m.CODE}
        view.binding=dict(main_contract_sha256=sha256_file(main/'contract.json'),code=code)
        entries=[];certificates=[]
        for owner,step in zip(roots[1:],[8000,10000]):
            cp=owner/'parent_checkpoint'/f'ckpt_{step:07d}'
            dcp.save({'model':{'weight':torch.arange(16.)},'optim':{'state':{'weight':dict(step=torch.tensor(float(step)),exp_avg=torch.zeros(16),exp_avg_sq=torch.zeros(16))}}},checkpoint_id=cp)
            config=dict(model='3b',max_steps=57000,global_tokens=2097152,ckpt_every=500,out_dir=str(owner/'training'))
            meta=dict(step=step,world_size=8,tokens_per_step=2097152,config=config,training_contract={'id':f'contract-{step}'})
            atomic_json(cp/'meta.json',meta)
            for rank in range(8):torch.save(dict(cpu=torch.Generator().manual_seed(step+rank).get_state(),cuda=torch.tensor([],dtype=torch.uint8)),cp/f'rng_rank{rank}.pt')
            (cp/'.complete').touch()
            from scripts.supervise_extended_training import seal
            cert=owner/'parent_certificate.json';seal(cp,cert,meta['training_contract']['id'])
            atomic_json(owner/'parent_handoff.json',dict(parent_checkpoint=str(cp),parent_metadata=meta,parent_stats=file_stats(cp)))
            slot=hashlib.sha256(str(cp).encode()).hexdigest()[:24]
            entries.append(dict(source=str(cp),destination=str(local/slot),slot=slot,source_stats=snapshot(cp),bytes=json.loads(cert.read_text())['bytes']))
            certificates.append(cert)
        plan=dict(purpose='independent_parent_retirement_v1',authorized=True,main_run=str(main),main_contract_sha256=view.binding['main_contract_sha256'],copy_workers=4,hash_workers=8,code=code,entries=entries)
        plan_path=control/'parent_retirement_plan.json';atomic_json(plan_path,plan)
        view.binding['parent_retirement']=dict(path=str(plan_path),sha256=sha256_file(plan_path))
        execution=control/'attempts/fixture/execution.json';attempt={'main_run':str(main)};atomic_json(execution,attempt)
        atomic_json(execution.parent/'launch/handle.json',{'fixture_handle':True})
        consumption=execution.parent/'consumption'
        for name in ['supervisor_verified.json','all_ranks.json',*[f'rank{r}.json' for r in range(8)]]:atomic_json(consumption/name,{'fixture_runtime_not_actualGPU':True})
        view.consumption=lambda _:{'fixture_all8_consumption':True}
        monkeypatch.setattr('train.independent_runtime_contract.validate_attempt',lambda _:(attempt,(view.runtime,contract,{}),{}))
        # Content layout is genuinely decoded/hashed; tiny tensors are not3B.
        def tiny_content(*args,**kwargs):
            kwargs['production']=False
            return checkpoint_content(*args,**kwargs)
        monkeypatch.setattr(m,'checkpoint_content',tiny_content)
        for entry in entries:
            state=control/'parent_retirement'/entry['slot']
            archive_one(entry,state,lambda **kw:None)
            atomic_json(state/'runtime_consumption.json',dict(runtime_contract_sha256=view.digest,execution=str(execution),supervisor_ack_sha256=sha256_file(consumption/'supervisor_verified.json')))
            retire_one(entry,state,lambda **kw:None)
        yield SimpleNamespace(view=view,plan=plan,plan_path=plan_path,entries=entries,local=local,execution=execution,certificates=certificates)


def verify(c):return m.verify_retired_parents(c.view,Snapshot())


def test_actual_copy_retirement_full_content_and_optional_local_loss(retired):
    c=retired;before={str(p):sha256_file(p) for p in c.view.control.rglob('*') if p.is_file()}
    rows=verify(c)
    assert [r['step'] for r in rows]==[8000,10000]
    assert all(not r['nfs_resume_candidate'] and r['full_payload_sha_refreshed'] and r['local_content']['state_tensors']==4 for r in rows)
    assert before=={str(p):sha256_file(p) for p in c.view.control.rglob('*') if p.is_file()}
    shutil.rmtree(c.local)
    rows=verify(c)
    assert all(r['local_namespace_missing'] and not r['full_payload_sha_refreshed'] and r['local_content'] is None for r in rows)
    assert not c.local.exists()


@pytest.mark.parametrize('fault',['plan_hash','unauthorized','foreign_entry','source_reappeared','retirement_residue','unknown_nfs_residue','partial_local','payload_corrupt','backup_corrupt','ack_files','ack_runtime','missing_consumption','missing_retirement','changed_intent','changed_local_stats'])
def test_missing_or_changed_retirement_proof_cannot_hide_extra_nfs_or_damage(retired,fault):
    c=retired;e=c.entries[0];state=c.view.control/'parent_retirement'/e['slot'];dest=Path(e['destination'])
    if fault=='plan_hash':c.view.binding['parent_retirement']['sha256']='0'*64
    elif fault in ('unauthorized','foreign_entry'):
        p=deepcopy(c.plan)
        if fault=='unauthorized':p['authorized']=False
        else:p['entries'][0]['destination']=str(c.local/'foreign')
        atomic_json(c.plan_path,p);c.view.binding['parent_retirement']['sha256']=sha256_file(c.plan_path)
    elif fault=='source_reappeared':Path(e['source']).mkdir()
    elif fault=='retirement_residue':Path(e['source']).with_name('.archive-retiring-'+e['slot']).mkdir()
    elif fault=='unknown_nfs_residue':Path(e['source']).with_name('unexpected_old_checkpoint').mkdir()
    elif fault=='partial_local':shutil.rmtree(dest)
    elif fault=='payload_corrupt':next(dest.glob('*.distcp')).write_bytes(b'corrupt')
    elif fault=='backup_corrupt':next((state/'source_metadata').iterdir()).write_bytes(b'corrupt')
    elif fault=='ack_files':
        p=state/'copy_ack.json';v=json.loads(p.read_text());v['files'][0]['sha256']='0'*64;atomic_json(p,v)
    elif fault=='ack_runtime':
        p=state/'runtime_consumption.json';v=json.loads(p.read_text());v['runtime_contract_sha256']='0'*64;atomic_json(p,v)
    elif fault=='missing_consumption':c.view.consumption=lambda _:None
    elif fault=='missing_retirement':(state/'retired.json').unlink()
    elif fault=='changed_intent':atomic_json(state/'retirement_intent.json',{})
    elif fault=='changed_local_stats':
        p=state/'retired.json';v=json.loads(p.read_text());v['local_stats']={};atomic_json(p,v)
    with pytest.raises((ValueError,OSError)):verify(c)


def test_controller_rechecks_real_receipts_stat_and_absence_after_worker(retired,monkeypatch):
    from common.data_contracts import artifact_path
    from scripts.run_restored_retention_audit import verify_current
    c=retired;snap=Snapshot();rows=m.verify_retired_parents(c.view,snap)
    result=dict(branch_inventory=[dict(root=d['run'],local_root=d['policy']['local_root'],nfs={},local={}) for d in c.view.contract['retention_branches']],
        retired_parents=rows,protected_parents=[],resident_file_stats=dict(snap.checkpoints),observed_directories=dict(snap.directories),
        metadata_evidence={artifact_path(Path(p)):v[1] for p,v in snap.files.items()},training_audit={'evidence':{}})
    contract=dict(main_run=str(c.view.main),independent_runtime={'fixture_only':True},code={},inputs={})
    # Final3B/count/runtime gates have separate tests; exercise content recheck.
    monkeypatch.setattr('scripts.run_restored_retention_audit.require_result',lambda *a:None)
    monkeypatch.setattr('scripts.independent_final_source.source_view',lambda *a:c.view)
    monkeypatch.setattr('scripts.independent_final_source.verify_audit_runtime',lambda *a:None)
    verify_current(result,contract)
    bad=deepcopy(result);bad['retired_parents'][0]['copy_ack_sha256']='0'*64
    with pytest.raises(ValueError,match='digest differs'):verify_current(bad,contract)
    bad=deepcopy(result);p=c.view.control/'parent_retirement'/c.entries[0]['slot']/'retired.json'
    bad['metadata_evidence'].pop(artifact_path(p))
    with pytest.raises(ValueError,match='coverage missing'):verify_current(bad,contract)
    Path(c.entries[0]['source']).mkdir()
    with pytest.raises(ValueError,match='scope changed'):verify_current(result,contract)
