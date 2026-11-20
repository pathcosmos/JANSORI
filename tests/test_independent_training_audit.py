"""Real immutable9500 prefix + synthetic500step suffix; never GPU acceptance."""
from collections import Counter
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil

import pytest

from common.data_contracts import resolve,artifact_path,sha256_file,identity
from scripts.audit_independent_training import audit_run


@pytest.fixture
def case(monkeypatch):
    if not os.environ.get('DENSE_EXTENSION_LINEAGE_FIXTURE'):
        pytest.skip('Explicit immutable lineage fixture required')
    from test_restored_training_audit import RestoredAuditTests
    f=RestoredAuditTests();f.setUp();c=f.f;target=10000;source=c.candidate['source_order'][0]
    monkeypatch.setattr('train.independent_checkpoint_handoff.enforce_shared_space',lambda *a:{'fixture_only':True})
    try:
        gens=[dict(id=p['id'],prompt=p['text'],category=p['category'],generated_ids=[1]) for p in c.prompts]
        generations=[dict(step=s,sha256=identity(gens)) for s in c.contract['generation_steps'] if 9500<s<=target]
        for rank in range(8):
            c.write(c.root/'ledgers'/f'fixture-rank{rank}.json',dict(rank=rank,success=False,contract_id=c.candidate['id'],
                optimizer_initializations=1,steps=[dict(step=s,source_sequences={source:64}) for s in range(9501,target+1)],generations=generations))
        rows=[dict(step=s,loss=2.,gnorm=.2,lr=.0003,tok_s=140000.,tokens_b=s*2097152/1e9) for s in range(9510,target+1,10)]
        for step in [9750,10000]:
            by_source={g['source']:dict(valid_tokens=g['count']*4096,nll_sum=g['count']*4096*2.,holdout_loss=2.) for g in c.probe['groups']}
            cats=Counter()
            for g in c.probe['groups']:cats[g['category']]+=g['count']*4096
            row=dict(step=step,contract_id=c.candidate['id'],valid_tokens=c.probe['target_tokens'],nll_sum=c.probe['target_tokens']*2.,holdout_loss=2.,
                by_source=by_source,by_category={k:dict(valid_tokens=n,nll_sum=n*2.,holdout_loss=2.) for k,n in cats.items()})
            if step in c.contract['generation_steps']:row['fixed_generations']=gens
            rows.append(row)
        (c.root/'training/log.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in sorted(rows,key=lambda r:r['step'])))
        cp=f.cp.with_name('ckpt_0010000');f.cp.rename(cp);c.cp=cp
        meta=json.loads((cp/'meta.json').read_text());meta['step']=target;c.write(cp/'meta.json',meta)
        for rank in range(1,8):(cp/f'__{rank}_0.distcp').write_bytes(b'synthetic payload')
        files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in sorted(cp.iterdir())]
        cert=c.root/'checkpoint_certificates/ckpt_0010000.json';c.write(cert,dict(status='passed_main_checkpoint_storage',checkpoint=artifact_path(cp),step=target,
            training_contract_id=c.candidate['id'],files=files,bytes=sum(r['bytes'] for r in files)))
        c.revision=dict(purpose='independent_nfs_resume_input_review_v1',source_run=str(c.root),production_adoption_authorized=False,
            source_contract_sha256=sha256_file(c.root/'contract.json'),child=dict(step=target,checkpoint=str(cp),certificate_sha256=sha256_file(cert)),
            local_proof_backups={},code={'train/independent_checkpoint_handoff.py':sha256_file(resolve('train/independent_checkpoint_handoff.py'))})
        c.revision_path=c.root/'independent_review/revision.json';c.revision_path.parent.mkdir();c.write(c.revision_path,c.revision)
        yield c
    finally:f.doCleanups()


def test_absent_parent_still_recomputes_three_original_segments(case):
    c=case;shutil.rmtree(c.parent)
    r=audit_run(c.root,10000,c.revision_path)
    assert r['counts']['committed_tokens']==10000*2097152 and r['counts']['committed_step_rank_pairs']==80000
    assert len(r['counts']['segments'])==3 and r['child_counts']['committed_tokens']==500*2097152
    assert r['status']=='prepared_independent_prefix_audit_not_runtime_accepted'
    assert not r['production_runtime_verified'] and not r['goal_complete'] and not r['parent_payload_read']


def test_local_proof_loss_uses_bound_nfs_backup(case):
    c=case;local=Path(c.contract['shared_storage']['local_temp_paths'][0]);local.mkdir(parents=True)
    p=local/'proof.json';c.write(p,{'synthetic':'proof'})
    backup=c.revision_path.parent/'backup.json';shutil.copyfile(p,backup)
    c.report['evidence'][str(p)]=sha256_file(p);c.write(c.root/'validation.json',c.report);c.rebind();c.write(c.root/'contract.json',c.contract)
    c.revision['source_contract_sha256']=sha256_file(c.root/'contract.json')
    c.revision['local_proof_backups']={str(p):dict(backup=str(backup),sha256=sha256_file(p))};c.write(c.revision_path,c.revision)
    shutil.rmtree(local);shutil.rmtree(c.parent)
    r=audit_run(c.root,10000,c.revision_path);assert artifact_path(backup) in r['evidence']
    backup.write_text('{}')
    with pytest.raises(ValueError):audit_run(c.root,10000,c.revision_path)


@pytest.mark.parametrize('fault',['rank','ledger_identity','payload','step','trace','lineage'])
def test_incomplete_or_relabelled_state_cannot_pass(case,fault):
    c=case;step=10000
    if fault=='rank':(c.root/'ledgers/fixture-rank7.json').unlink()
    if fault=='ledger_identity':
        p=c.root/'ledgers/fixture-rank7.json';r=json.loads(p.read_text());r['contract_id']=c.meta['training_contract']['id'];c.write(p,r)
    if fault=='payload':(c.cp/'__0_0.distcp').write_bytes(b'corrupt')
    if fault=='step':step=10500
    if fault=='trace':
        p=c.root/'training/log.jsonl';p.write_text(p.read_text().replace('"loss": 2.0','"loss": NaN',1))
    if fault=='lineage':
        p=c.root/'lineage/current_rank0.json';r=json.loads(p.read_text());r[0]['record']['steps'].pop();c.write(p,r)
    with pytest.raises(ValueError):audit_run(c.root,step,c.revision_path)
