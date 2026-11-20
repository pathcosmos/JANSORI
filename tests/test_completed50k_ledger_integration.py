"""Real immutable16k prefix plus synthetic50k tail, no CUDA or source writes.

Opt in with DENSE_BUDGET_LINEAGE_FIXTURE pointing to the activated50k run.
This proves collector integration only, never actual final training/restore.
All fabricated completion artifacts are confined to pytest's temporary folder.
"""
from collections import Counter
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import pytest

from common.data_contracts import identity, sha256_file
from scripts.audit_budget_completed_ledgers import audit_completed

SOURCE = os.environ.get('DENSE_BUDGET_LINEAGE_FIXTURE')
pytestmark = pytest.mark.skipif(not SOURCE, reason='Explicit immutable16k fixture required')


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value)+'\n')


@pytest.fixture(scope='module')
def synthetic_completed(tmp_path_factory):
    source=Path(SOURCE).resolve(strict=True)
    main=tmp_path_factory.mktemp('synthetic-completed50k')
    def read(name): return json.loads((source/name).read_text())
    c,config,receipt,proof,child=[read(n) for n in
        ['contract.json','main.json','parent_handoff.json','parent_certificate.json',
         'training/training_contract.json']]
    assert c['target_steps']==50000 and receipt['parent_metadata']['step']==16000
    c=deepcopy(c);config=deepcopy(config);receipt=deepcopy(receipt)
    c['main_run']=str(main);c['retention_branches'][-1]['run']=str(main)
    config['out_dir']=str(main/'training')
    receipt['protection_root']=str(main)
    receipt['parent_checkpoint']=str(main/'parent_checkpoint/ckpt_0016000')
    write(main/'main.json',config);write(main/'parent_handoff.json',receipt)
    write(main/'training/training_contract.json',child)
    # Preserve raw certificate bytes: its byte SHA belongs to the original ID.
    shutil.copyfile(source/'parent_certificate.json',main/'parent_certificate.json')
    c['configurations']={'main.json':sha256_file(main/'main.json')}
    c['evidence'].pop(str(source/'parent_handoff.json'),None)
    c['evidence'][str(main/'parent_handoff.json')]=sha256_file(main/'parent_handoff.json')
    write(main/'contract.json',c)
    shutil.copytree(source/'lineage',main/'lineage')
    shutil.copytree(source/'probe',main/'probe')
    probe=json.loads((main/'probe/probe.json').read_text())
    prompts=[json.loads(s) for s in Path(c['prompts']).read_text().splitlines() if s.strip()]
    generation=[dict(id=p['id'],prompt=p['text'],category=p['category'],generated_ids=[1]) for p in prompts]
    generation_steps=[s for s in c['generation_steps'] if s>16000]
    generations=[dict(step=s,sha256=identity(generation)) for s in generation_steps]
    for rank in range(8):
        write(main/'ledgers'/f'synthetic-rank{rank}.json',dict(rank=rank,success=True,
            contract_id=child['id'],optimizer_initializations=1,
            steps=[dict(step=s,source_sequences={child['source_order'][0]:64})
                   for s in range(16001,50001)],generations=generations))
    rows=[dict(step=s,loss=2.,gnorm=.2,lr=.0003,tok_s=140000.,tokens_b=s*2097152/1e9)
          for s in range(16010,50001,10)]
    categories=Counter()
    for g in probe['groups']:categories[g['category']]+=g['count']*4096
    for s in range(16250,50001,250):
        values=lambda n:dict(valid_tokens=n,nll_sum=n*2.,holdout_loss=2.)
        row=dict(step=s,contract_id=child['id'],**values(probe['target_tokens']),
            by_source={g['source']:values(g['count']*4096) for g in probe['groups']},
            by_category={k:values(n) for k,n in categories.items()})
        if s in generation_steps:row['fixed_generations']=generation
        rows.append(row)
    (main/'training/log.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in sorted(rows,key=lambda r:r['step'])))
    checkpoint=main/'training/ckpt_0050000'
    write(checkpoint/'meta.json',dict(step=50000,config=config,training_contract=child,
        world_size=8,tokens_per_step=2097152,sampler_version=2))
    certificate=deepcopy(proof)
    certificate.update(step=50000,checkpoint=str(checkpoint),training_contract_id=child['id'])
    for row in certificate['files']:
        if row['path']=='meta.json':
            row.update(bytes=(checkpoint/'meta.json').stat().st_size,sha256=sha256_file(checkpoint/'meta.json'))
    certificate['bytes']=sum(r['bytes'] for r in certificate['files'])
    write(main/'checkpoint_certificates/ckpt_0050000.json',certificate)
    write(main/'report.json',dict(status='main_target_completed_review_pending',
        trained_tokens=104857600000,final_checkpoint=str(checkpoint)))
    write(main/'.done',dict(report_sha256=sha256_file(main/'report.json'),
        contract_sha256=sha256_file(main/'contract.json')))
    assert not (main/'parent_checkpoint').exists()
    assert not list(main.rglob('*.distcp'))
    return main


def test_complete_metadata_collector_with_no_checkpoint_payloads(synthetic_completed,monkeypatch):
    main=synthetic_completed
    original=Path.open
    def guarded(path,*args,**kwargs):
        if 'parent_checkpoint' in path.parts or path.suffix=='.distcp':
            raise AssertionError('Ancestor or tensor payload opened')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',guarded)
    result=audit_completed(main)
    assert result['counts']['committed_tokens']==104857600000
    assert result['counts']['committed_step_rank_pairs']==400000
    assert result['counts']['successful_target_ranks']==list(range(8))
    assert result['trace']['child']['evaluation_points']==136
    assert result['trace']['child']['train_points']==3400
    assert result['ledger_audit_verified'] and result['final_metadata_verified']
    assert not any(result[k] for k in ['source_exit_verified','full_payload_sha_verified',
        'actual8gpu_verified','retirement_authorized','goal_complete'])


@pytest.mark.parametrize('fault',['rank_success','final_generation','prefix_bytes'])
def test_collector_rejects_corrupt_synthetic_completion(synthetic_completed,fault):
    main=synthetic_completed
    name={'rank_success':'ledgers/synthetic-rank7.json',
          'final_generation':'training/log.jsonl','prefix_bytes':'lineage/restored_rank0.json'}[fault]
    path=main/name;raw=path.read_bytes()
    try:
        if fault=='rank_success':
            row=json.loads(raw);row['success']=False;write(path,row)
        elif fault=='final_generation':
            rows=[json.loads(r) for r in raw.splitlines()]
            for row in rows:
                if row['step']==50000 and 'fixed_generations' in row:
                    row['fixed_generations'][0]['generated_ids']=[2]
            path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        else:path.write_bytes(raw+b' ')
        with pytest.raises(ValueError):audit_completed(main)
    finally:path.write_bytes(raw)
