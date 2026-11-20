"""Historical lineage replay plus synthetic24NCCL receipts, not GPU proof."""
import json
from pathlib import Path
import shutil

import pytest
from common.data_contracts import atomic_json,resolve,sha256_file,identity
from scripts.audit_independent_training import audit_run
from scripts.freeze_independent_prefix import freeze
from scripts.seal_independent_resume_result import seal,verify
from train.independent_resume_probe import file_stats,metadata_for,diagnostic_lr
from test_independent_training_audit import case
from test_frozen_independent_prefix import captured
from test_independent_resume_controller import RuntimeReceiptTests


@pytest.fixture
def diagnostic(case,monkeypatch):
    from scripts import run_independent_resume as controller
    f=RuntimeReceiptTests();f.setUp();f.collector_fixture();root=f.root;c=case
    try:
        audit=audit_run(c.root,10000,c.revision_path)
        atomic_json(root/'independent_training_audit.json',audit)
        cap=captured(c.root)
        frozen=freeze(root/'independent_training_audit.json',root/'frozen_prefix',cap)
        f.plan.update(run_dir=str(root),main_run=str(c.root),parent_step=10000,
            parent_config=str(c.root/'main.json'),parent_contract=str(c.root/'training/training_contract.json'),
            parent_training_contract_id=c.candidate['id'],source_capture=cap,revision=str(c.revision_path),
            frozen_prefix_required=True,operational_consumption_required=True,shared_admission_required=True,
            required_absent_paths=[str(c.parent)],
            inputs={str(c.root/'main.json'):sha256_file(c.root/'main.json')})
        from train.independent_resume_bridge import BRIDGE_FILES
        f.plan['code']={name:sha256_file(resolve(name)) for name in
            {*BRIDGE_FILES,'train/independent_operational_admission.py','train/independent_resume_probe.py'}}
        # Synthetic immutable DAC protocol receipts. Real chmod/crash coverage
        # is in test_independent_resume_isolation; live collector is mocked here.
        iso=root/'isolation';path=str(c.parent);slot=identity(path)[:16]
        stamp=dict(device=1,inode=2,uid=1100,gid=1100,mode=0)
        ip=dict(purpose='inplace_independent_resume_dac_isolation_v1',authorized=True,
            diagnostic_run=str(root),main_run=str(c.root),revision=str(c.revision_path),source_capture=cap,
            uid=1100,dependencies=[path],entries=[dict(path=path,slot=slot,payload=dict(bytes=1,root=stamp))])
        atomic_json(iso/'plan.json',ip)
        atomic_json(iso/'prepared.json',dict(plan_id=identity(ip),plan_sha256=sha256_file(iso/'plan.json')))
        ack=iso/'isolation_receipts'/(slot+'.json')
        atomic_json(ack,dict(phase='isolated',plan_id=identity(ip),slot=slot,blocked_stamp=stamp))
        proof=dict(status='verified_original_dependencies_dac_denied',plan_id=identity(ip),
            plan_sha256=sha256_file(iso/'plan.json'),uid=1100,hidden_payload_bytes=1,
            entries=[dict(slot=slot,blocked_stamp=stamp,receipt_sha256=sha256_file(ack))])
        atomic_json(iso/'isolated.json',proof)
        f.plan['dependency_isolation']=dict(mode='live_inplace_dac',root=str(iso),plan_id=identity(ip),
            evidence={str(p):sha256_file(p) for p in [iso/'plan.json',iso/'prepared.json',iso/'isolated.json',ack]})
        f.spec.update(plan_id=identity(f.plan),parent_checkpoint=str(c.cp),parent_stats=file_stats(c.cp),
            parent_proof=str(c.root/'checkpoint_certificates/ckpt_0010000.json'),
            audit=str(root/'independent_training_audit.json'),audit_sha256=sha256_file(root/'independent_training_audit.json'),
            frozen_prefix=dict(directory=str(root/'frozen_prefix'),manifest_sha256=frozen['manifest_sha256'],
                verified_sha256=sha256_file(root/'frozen_prefix/verified.json')))
        # Synthetic controller receipt; the real fresh-review/eight-consumer
        # path is covered in test_shared_operational_admission.
        shared=root/'shared_admission.json'
        atomic_json(shared,dict(purpose='shared_own_nfs_admission_v1',
            binding=dict(purpose='independent_resume_probe',plan_id=identity(f.plan)),
            production_adoption_authorized=False,root=str(c.root),revision_path=str(c.revision_path),
            child=f.spec['parent_checkpoint'],child_stats=f.spec['parent_stats']))
        f.spec['shared_admission']=dict(path=str(shared),sha256=sha256_file(shared))
        atomic_json(root/'spec.json',f.spec);atomic_json(root/'plan.json',f.plan)
        for p in (root/'runtime').glob('*.json'):
            row=json.loads(p.read_text());row.update(plan_id=identity(f.plan),spec_id=identity(f.spec))
            if row['mode']!='resumed':
                row['own_state_consumption']=dict(status='rank_own_nfs_state_consumed',source_run=str(c.root),
                    rank=row['rank'],checkpoint=str(c.cp),step=10000,training_contract_id=c.candidate['id'],
                    checkpoint_certificate_id=identity(json.loads(Path(f.spec['parent_proof']).read_text())),
                    pid=row['pid'],start_ticks=row['start_ticks'],model_optimizer_load_completed=True,
                    cpu_cuda_rng_applied=True,source_may_rotate_after_all8rank_consumption=True,actual8gpu_acceptance=False)
            atomic_json(p,row)
        source=c.candidate['source_order'][0]
        for p in (root/'ledgers').glob('*.json'):
            row=json.loads(p.read_text());row.update(parent_step=10000,plan_id=identity(f.plan))
            for s in row['steps']:s.update(step=s['step']-1000,source_sequences={source:64})
            for m in row['metrics']:m.update(step=m['step']-1000,lr=diagnostic_lr(c.config,m['step']-1001))
            atomic_json(p,row)
        for name,offset in dict(restored_parent=0,continuous_final=2,split_first=1,resumed_final=2).items():
            cp=Path(f.plan['scratch'])/name;meta=metadata_for(f.spec,offset);atomic_json(cp/'meta.json',meta)
            for name in ['.complete','.metadata',*[f'rng_rank{r}.pt' for r in range(8)],
                         *[f'__{r}_0.distcp' for r in range(8)]]:
                (cp/name).write_bytes(b'synthetic checkpoint file')
            files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in cp.iterdir()]
            atomic_json(root/'certificates'/(cp.name+'.json'),dict(path=str(cp),metadata=meta,files=files,
                bytes=sum(r['bytes'] for r in files),stats=file_stats(cp)))
        monkeypatch.setattr(controller,'check_spec',lambda s:(f.plan,c.config,c.candidate))
        report=controller.collect_result(root,f.spec)
        atomic_json(root/'report.json',report)
        atomic_json(root/'contract.json',dict(plan_sha256=sha256_file(root/'plan.json')))
        atomic_json(root/'.done',dict(report_sha256=sha256_file(root/'report.json'),contract_sha256=sha256_file(root/'contract.json')))
        monkeypatch.setattr(controller,'verify_completed',lambda *a:controller.collect_result(root,f.spec))
        yield f,c,report
    finally:f.doCleanups()


def test_sealed_runtime_survives_original_source_and_local_probe_loss(diagnostic):
    f,c,report=diagnostic;result=seal(f.root)
    assert result['diagnostic_counts']==report['counts']
    assert not result['actual_new_gpu_execution'] and not result['current_payloads_verified']
    assert not result['isolation']['current_denial_verified']
    (c.root/'training/log.jsonl').write_text('advanced\n')
    shutil.rmtree(c.cp);shutil.rmtree(c.parent);shutil.rmtree(Path(f.plan['scratch']))
    shutil.rmtree(f.root/'runtime');shutil.rmtree(f.root/'ledgers');shutil.rmtree(f.root/'isolation')
    (f.root/'shared_admission.json').unlink()
    assert verify(f.root)==result


@pytest.mark.parametrize('fault',['rank','backend','owner','comparison','isolation','blob'])
def test_changed_historical_receipts_rejected(diagnostic,fault):
    f,_,_=diagnostic;seal(f.root);out=f.root/'accepted_evidence'
    manifest=json.loads((out/'manifest.json').read_text())
    if fault=='blob':next((out/'blobs').iterdir()).write_bytes(b'corrupt')
    else:
        if fault in ('rank','backend','owner'):p=f.root/'runtime/resumed-rank7.json'
        elif fault=='comparison':p=f.root/'roundtrip_comparison.json'
        else:p=f.root/'isolation/isolated.json'
        row=manifest['files'][str(p)];dest=out/'blobs'/row['blob'];value=json.loads(dest.read_text())
        if fault=='rank':value['rank']=6
        if fault=='backend':value['backend']='gloo'
        if fault=='owner':value['owner']='z'*32
        if fault=='comparison':value['exact']=False
        if fault=='isolation':value['hidden_payload_bytes']=0
        atomic_json(dest,value);row.update(bytes=dest.stat().st_size,sha256=sha256_file(dest))
        # Local blob hash repairs cannot bypass the original report/DAC pins.
        atomic_json(out/'manifest.json',manifest)
    with pytest.raises(ValueError):verify(f.root)


def test_failed_live_revalidation_cannot_publish_acceptance(diagnostic,monkeypatch):
    f,_,_=diagnostic
    def fail(*a):raise ValueError('injected live comparison failure')
    monkeypatch.setattr('scripts.run_independent_resume.verify_completed',fail)
    with pytest.raises(ValueError,match='comparison'):seal(f.root)
    assert not (f.root/'accepted_evidence').exists()


def test_completed_controller_reentry_uses_historical_acceptance(diagnostic,monkeypatch):
    from types import SimpleNamespace
    from scripts import run_independent_resume as controller
    f,c,_=diagnostic
    monkeypatch.setattr(controller,'load_plan',lambda root:f.plan)
    monkeypatch.setattr(controller,'make_contract',lambda root:json.loads((root/'contract.json').read_text()))
    monkeypatch.setattr(controller,'check_bindings',lambda c:None)
    args=SimpleNamespace(run_dir=str(f.root),worker=False,prepare=False,run=True)
    accepted=controller.main(args)
    shutil.rmtree(c.cp);shutil.rmtree(f.root/'runtime')
    def fail(*a):raise AssertionError('Historical reentry must not inspect live tensors/DAC')
    monkeypatch.setattr(controller,'verify_completed',fail)
    assert controller.main(args)==accepted
