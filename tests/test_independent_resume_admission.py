"""File/process/resource gates with synthetic lineage, not production8GPU proof."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from common.data_contracts import resolve,artifact_path,atomic_json,sha256_file,identity
from scripts.prepare_independent_resume import prepare
from scripts.run_independent_resume import admission,stopped_source,load_plan
from train.independent_resume_probe import check_spec,file_stats,stat_record


@pytest.fixture
def fixture(monkeypatch):
    with tempfile.TemporaryDirectory(dir=resolve('runs')) as nf,tempfile.TemporaryDirectory() as lf:
        base=Path(nf);main=base/'main';prior=base/'prior';transition=base/'transition';root=transition/'diagnostic'
        for p in (main,prior,transition):p.mkdir()
        temp=Path(lf)/'temp';temp.mkdir()
        config=dict(model='3b',vocab_size=65536,seq_len=4096,micro_bsz=4,global_tokens=2097152,
            max_steps=57000,lr=.0003,min_lr_ratio=.1,warmup=1000,decay_frac=.15,wd=.1,beta1=.9,beta2=.95,
            clip=1.,seed=1234,compile=False,mix=str(main/'mix.json'),holdout=[],out_dir=str(main/'training'),
            ckpt_every=500,eval_every=250,log_every=10,keep_ckpts=57001,save_final=True,sampler_version=2,stop_after=None)
        atomic_json(main/'mix.json',dict(sources={'ko':1}));token=main/'input.bin';token.write_bytes(b'fixture')
        parent=dict(id='child',source_order=['ko'],mix={'sources':{'ko':1}},files={str(token):sha256_file(token)})
        atomic_json(main/'main.json',config);atomic_json(main/'training/training_contract.json',parent)
        receipt=dict(source_run=str(prior),parent_checkpoint=str(main/'parent_checkpoint/ckpt_0010000'),
            parent_metadata={'training_contract':{'id':'prior'}},original_contract={'id':'original'},execution_binding={})
        atomic_json(main/'parent_handoff.json',receipt)
        atomic_json(main/'lineage/manifest.json',{'synthetic':True})
        atomic_json(prior/'parent_handoff.json',dict(parent_checkpoint=str(prior/'parent_checkpoint/ckpt_0008000')))
        mc=dict(purpose='main_training',main_training_authorized=True,generation_steps=[57000],configurations={'main.json':sha256_file(main/'main.json')},
            tiered_retention=dict(temp_root=str(temp)),max_pss_gib=384,gpu_identity=['H200']*8,
            framework={'torch':'fixture','cuda':None},expected_checkpoint_bytes=1024,
            code={'train/monitored_extended_horizon.py':sha256_file(resolve('train/monitored_extended_horizon.py'))},evidence={},
            retention_branches=[dict(first_step=500,last_step=8000,policy={'local_root':str(temp/'history1')}),
                dict(first_step=8500,last_step=10000,policy={'local_root':str(temp/'history2')})],
            shared_storage=dict(nfs_pools={'transition':dict(paths=[str(transition)],budget_bytes=2**30)},local_temp_paths=[str(temp)]))
        atomic_json(main/'contract.json',mc)
        checkpoint=main/'training/ckpt_0011000';checkpoint.mkdir()
        atomic_json(checkpoint/'meta.json',dict(step=11000,world_size=8,tokens_per_step=2097152,config=config,training_contract=parent))
        for name in ['.complete','.metadata',*[f'__{r}_0.distcp' for r in range(8)],*[f'rng_rank{r}.pt' for r in range(8)]]:
            (checkpoint/name).write_bytes(b'tiny-synthetic')
        files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in sorted(checkpoint.iterdir())]
        cert=main/'checkpoint_certificates/ckpt_0011000.json'
        atomic_json(cert,dict(status='passed_main_checkpoint_storage',step=11000,training_contract_id='child',
            checkpoint=str(checkpoint),files=files,bytes=sum(x['bytes'] for x in files)))
        revision=base/'revision.json';atomic_json(revision,dict(child=dict(step=11000,checkpoint=str(checkpoint),
            certificate_sha256=sha256_file(cert)),local_proof_backups={str(temp/'old-proof.json'):{}}))
        capture=base/'capture.json';atomic_json(capture,dict(status='observed_live_not_stopped',run=str(main),
            processes={name:dict(pid=90000000+i,start_ticks=1) for i,name in enumerate(['supervisor','worker',*[f'rank{r}' for r in range(8)]])}))
        hw=dict(gpus=['H200']*8,memory={'cache_aware_admission':{'headroom_gib':1000}},
            storage={'scratch_parent':{'device':temp.stat().st_dev}})
        usage={'local_temporary_used_bytes':700*2**30}
        monkeypatch.setattr('scripts.resource_admission.inspect',lambda:hw)
        monkeypatch.setattr('scripts.extended_horizon_storage.enforce_shared_space',lambda *a:usage)
        monkeypatch.setattr('train.independent_checkpoint_handoff.review_handoff',lambda *a:{'training_contract_id':'child'})
        monkeypatch.setattr('train.extended_horizon_worker_contract.bound_candidate',lambda *a:parent)
        yield dict(base=base,main=main,root=root,temp=temp,mc=mc,hw=hw,usage=usage,config=config,parent=parent,
            checkpoint=checkpoint,cert=cert,revision=revision,capture=capture)


def build(f,**kwargs):
    plan=prepare(f['main'],f['root'],f['revision'],f['capture'],**kwargs)
    audit=f['root']/'audit.json';step=11000
    atomic_json(audit,dict(status='prepared_independent_prefix_audit_not_runtime_accepted',source_run=str(f['main']),
        through_step=step,target_steps=57000,validation_source=False,checkpoint_certificate=str(f['cert']),
        code={p:sha256_file(resolve(p)) for p in ['scripts/audit_independent_training.py','train/independent_checkpoint_handoff.py',
            'scripts/audit_extended_horizon.py','scripts/audit_horizon_training.py','scripts/audit_main_training.py','scripts/extended_horizon_lineage.py']},
        evidence={str(p):sha256_file(p) for p in [f['main']/'contract.json',f['main']/'main.json',f['main']/'training/training_contract.json',
            f['main']/'parent_handoff.json',f['main']/'lineage/manifest.json',f['revision'],f['cert'],f['checkpoint']/'meta.json']},
        counts=dict(committed_tokens=step*2097152,committed_step_rank_pairs=step*8,source_tokens={'ko':step*2097152},
            segments=[dict(contract_id='original',start_step_exclusive=0,through_step=8000),
                dict(contract_id='prior',start_step_exclusive=8000,through_step=10000),
                dict(contract_id='child',start_step_exclusive=10000,through_step=step)])))
    spec=dict(plan=plan,plan_id=identity(plan),parent_checkpoint=str(f['checkpoint']),parent_proof=str(f['cert']),
        parent_proof_sha256=sha256_file(f['cert']),audit=str(audit),audit_sha256=sha256_file(audit),
        parent_stats=file_stats(f['checkpoint']),input_stats={p:stat_record(resolve(p)) for p in f['parent']['files']})
    shared=f['root']/'shared_admission.json'
    atomic_json(shared,dict(purpose='shared_own_nfs_admission_v1',
        binding=dict(purpose='independent_resume_probe',plan_id=identity(plan)),
        production_adoption_authorized=False,root=str(f['main']),revision_path=str(f['revision']),
        child=str(f['checkpoint']),child_stats=spec['parent_stats']))
    spec['shared_admission']=dict(path=str(shared),sha256=sha256_file(shared))
    return spec


@pytest.mark.parametrize('mutation',['none','missing_pin','wrong_diagnostic','readable_parent','changed_ack'])
def test_real_dac_prepare_spec_and_controller_gate(fixture,mutation):
    from scripts import independent_resume_isolation as iso
    from scripts import extended_horizon_storage as storage
    from scripts.independent_resume_dependency_access import storage_view
    f=fixture
    rev=json.loads(f['revision'].read_text())
    proof=f['temp']/'old_proofs/training_contract.json'
    proof.parent.mkdir();proof.write_text('old proof')
    rev['local_proof_backups']={str(proof):{}}
    atomic_json(f['revision'],rev)
    parent=f['main']/'parent_checkpoint/ckpt_0010000'
    parent.mkdir(parents=True);(parent/'payload').write_bytes(b'protected model optimizer')
    tx=f['root'].parent/'isolation'
    iso.build_plan(f['main'],tx,f['root'],f['revision'],f['capture'],authorized=True)
    iso.enter(tx)
    try:
        spec=build(f,isolation_root=tx);plan=spec['plan']
        assert check_spec(spec)[2]==f['parent']
        assert load_plan(f['root'])==plan
        assert stopped_source(plan)[0]==f['main']
        assert admission(f['root'],plan)[1]['combined_memory_gib']==256
        original=storage.size_tree
        with storage_view(plan):
            assert storage.size_tree(parent)==len(b'protected model optimizer')
        assert storage.size_tree is original
        if mutation=='none':
            iso.restore(tx)
            with pytest.raises(ValueError):check_spec(spec)
            with pytest.raises(ValueError):admission(f['root'],plan)
            return
        if mutation=='missing_pin':
            plan['code'].pop('scripts/independent_resume_dependency_access.py')
        elif mutation=='wrong_diagnostic':
            plan['run_dir']=str(f['root'].parent/'other')
        elif mutation=='readable_parent':
            os.chmod(parent,0o755)
        else:
            ack=next((tx/'isolation_receipts').glob('*.json'))
            saved=ack.read_bytes();ack.write_text('{}')
        try:
            with pytest.raises(ValueError):stopped_source(plan)
            with pytest.raises(ValueError):admission(f['root'],plan)
            assert storage.size_tree is original
        finally:
            if mutation=='changed_ack':ack.write_bytes(saved)
    finally:
        # Fixture-only repair after deliberate tampering; production refuses
        # changed permissions and preserves the failed transaction for review.
        if mutation=='readable_parent':
            os.chmod(parent,0o755);os.chmod(proof.parent,0o755)
        elif not (tx/'restored.json').exists():iso.restore(tx)


def test_positive_file_admission_and_unavailable_local_parent_gate(fixture):
    f=fixture;spec=build(f);assert check_spec(spec)[2]==f['parent'];assert load_plan(f['root'])==spec['plan']
    assert not Path(spec['plan']['scratch']).exists() and not (f['root']/'.done').exists()
    local=f['temp']/'history1/checkpoints/ckpt_0008000';local.mkdir(parents=True)
    with pytest.raises(ValueError,match='unavailable'):check_spec(spec)


def test_actual_live_process_refused_without_signalling(fixture):
    f=fixture;spec=build(f);proc=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
    try:
        ticks=int(Path(f'/proc/{proc.pid}/stat').read_text().rsplit(')',1)[1].split()[19])
        plan=deepcopy(spec['plan']);plan['source_capture']['processes']['supervisor']=dict(pid=proc.pid,start_ticks=ticks)
        with pytest.raises(ValueError,match='still present'):stopped_source(plan)
        assert proc.poll() is None
    finally:proc.terminate();proc.wait(timeout=5)


def test_changed_ancestry_capture_and_revision_refused(fixture):
    f=fixture;spec=build(f)
    bad=deepcopy(spec);bad['plan']['source_capture']['processes']['worker']['start_ticks']=2;bad['plan_id']=identity(bad['plan'])
    with pytest.raises(ValueError,match='capture bindings'):check_spec(bad)
    bad=deepcopy(spec);prior=f['base']/'prior/parent_handoff.json';bad['plan']['inputs'].pop(artifact_path(prior));bad['plan_id']=identity(bad['plan'])
    with pytest.raises(ValueError,match='ancestry'):check_spec(bad)
    revision=json.loads(f['revision'].read_text());revision['child']['certificate_sha256']='0'*64;atomic_json(f['revision'],revision)
    bad=deepcopy(spec);bad['plan']['inputs'][artifact_path(f['revision'])]=sha256_file(f['revision']);bad['plan_id']=identity(bad['plan'])
    with pytest.raises(ValueError,match='revision certificate'):check_spec(bad)


def test_wrong_segments_source_mutation_and_resource_caps(fixture):
    f=fixture;spec=build(f);audit=Path(spec['audit']);data=json.loads(audit.read_text())
    data['counts']['segments'][1]['through_step']=9500;atomic_json(audit,data);spec['audit_sha256']=sha256_file(audit)
    with pytest.raises(ValueError,match='three-segment'):check_spec(spec)
    data['counts']['segments'][1]['through_step']=10000;atomic_json(audit,data);spec['audit_sha256']=sha256_file(audit)
    payload=f['checkpoint']/'__0_0.distcp';payload.write_bytes(b'different')
    with pytest.raises(ValueError):check_spec(spec)
    _,budget=admission(f['root'],spec['plan']);assert budget['projected_temporary_bytes']==900*2**30
    assert budget['main_process_absence_verified'] and budget['combined_memory_gib']==256
    unproven=deepcopy(spec['plan']);unproven.pop('source_capture')
    _,budget=admission(f['root'],unproven);assert budget['combined_memory_gib']==640
    f['hw']['memory']['cache_aware_admission']['headroom_gib']=639
    with pytest.raises(RuntimeError,match='memory'):admission(f['root'],unproven)
    f['hw']['memory']['cache_aware_admission']['headroom_gib']=1000
    f['usage']['local_temporary_used_bytes']=900*2**30
    with pytest.raises(RuntimeError,match='temporary'):admission(f['root'],spec['plan'])
    f['usage']['local_temporary_used_bytes']=700*2**30;f['hw']['memory']['cache_aware_admission']['headroom_gib']=255
    with pytest.raises(RuntimeError,match='memory'):admission(f['root'],spec['plan'])


def test_existing_namespace_and_unreserved_scope_refused(fixture):
    f=fixture;spec=build(f)
    with pytest.raises(ValueError,match='existing'):prepare(f['main'],f['root'],f['revision'],f['capture'])
    with pytest.raises(ValueError,match='transition reservation'):admission(f['base']/'other',spec['plan'])
    bad=deepcopy(spec);bad['plan']['max_pss_gib']=512;bad['plan_id']=identity(bad['plan'])
    with pytest.raises(ValueError,match='resource'):check_spec(bad)


def test_summary_without_underlying_audit_evidence_refused(fixture):
    spec=build(fixture);p=Path(spec['audit']);data=json.loads(p.read_text());data['code']={}
    atomic_json(p,data);spec['audit_sha256']=sha256_file(p)
    with pytest.raises(ValueError,match='evidence incomplete'):check_spec(spec)
