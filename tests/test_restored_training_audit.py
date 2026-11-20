"""Real9500 prefix files + synthetic ten-update suffix/checkpoint/GPU report.

No production writes or CUDA. Full new auditor admission is exercised using
explicitly labelled synthetic proof fixtures; it is not actual10000/57k proof.
"""
import json
import os
from pathlib import Path
import unittest
from common.data_contracts import sha256_file,artifact_path,identity
from scripts.audit_restored_training import audit_run
import test_extended_handoff as fixture_module

@unittest.skipUnless(os.environ.get('DENSE_EXTENSION_LINEAGE_FIXTURE'),'Explicit prefix fixture required')
class RestoredAuditTests(unittest.TestCase):
    def setUp(self):
        f=fixture_module.ExtendedHandoffIntegrationTests();f.setUp();self.addCleanup(f.doCleanups);self.f=f;self.root=f.root
        for name in ('training','ledgers','checkpoint_certificates'):(self.root/name).mkdir()
        f.write(self.root/'main.json',f.config);f.contract['configurations']={'main.json':sha256_file(self.root/'main.json')}
        f.write(self.root/'contract.json',f.contract);f.write(self.root/'training/training_contract.json',f.candidate)
        source=f.candidate['source_order'][0]
        for rank in range(8):
            f.write(self.root/'ledgers'/f'fixture-rank{rank}.json',dict(rank=rank,success=False,contract_id=f.candidate['id'],
                optimizer_initializations=1,steps=[dict(step=s,source_sequences={source:64}) for s in range(9501,9511)],generations=[]))
        f.write(self.root/'training/log.jsonl',dict(step=9510,loss=2.,gnorm=.2,lr=.0003,tok_s=140000.,tokens_b=9510*2097152/1e9))
        cp=self.root/'training/ckpt_0009510';cp.mkdir();self.cp=cp
        meta=dict(step=9510,config=f.config,world_size=8,tokens_per_step=2097152,sampler_version=2,training_contract=f.candidate)
        f.write(cp/'meta.json',meta)
        for name in ('.complete','.metadata','__0_0.distcp',*[f'rng_rank{r}.pt' for r in range(8)]):(cp/name).write_bytes(b'synthetic checkpoint fixture')
        files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in sorted(cp.iterdir())]
        proof=dict(status='passed_main_checkpoint_storage',checkpoint=artifact_path(cp),step=9510,
                   training_contract_id=f.candidate['id'],files=files,bytes=sum(r['bytes'] for r in files))
        f.write(self.root/'checkpoint_certificates/ckpt_0009510.json',proof)

    def test_three_exact_ids_count_prefix_once_and_no_final_claim(self):
        report=audit_run(self.root,9510)
        self.assertEqual(report['counts']['committed_tokens'],9510*2097152)
        self.assertEqual(report['counts']['committed_step_rank_pairs'],9510*8)
        self.assertEqual(len(report['counts']['segments']),3)
        self.assertEqual(report['child_counts']['committed_tokens'],10*2097152)
        self.assertFalse(report['goal_complete']);self.assertEqual(report['target_steps'],57000)
        self.assertEqual(sum(report['category_tokens'].values()),9510*2097152)

    def test_missing_rank_and_relabelled_contract_rejected(self):
        p=self.root/'ledgers/fixture-rank7.json';v=json.loads(p.read_text())
        v['contract_id']=self.f.meta['training_contract']['id'];self.f.write(p,v)
        with self.assertRaises(ValueError):audit_run(self.root,9510)
        p.unlink()
        with self.assertRaises(ValueError):audit_run(self.root,9510)

    def test_nonfinite_trace_rejected(self):
        p=self.root/'training/log.jsonl';v=json.loads(p.read_text());v['loss']=float('nan');self.f.write(p,v)
        with self.assertRaisesRegex(ValueError,'finite'):audit_run(self.root,9510)

    def test_same_size_rng_damage_rejected(self):
        p=self.cp/'rng_rank7.pt';p.write_bytes(b'X'*p.stat().st_size)
        with self.assertRaisesRegex(ValueError,'RNG content'):audit_run(self.root,9510)

    def test_cannot_claim_final_or_ignore_parent_gap(self):
        with self.assertRaises(ValueError):audit_run(self.root,57000)
        p=self.root/'lineage/current_rank0.json';rows=json.loads(p.read_text());rows[0]['record']['steps'].pop();self.f.write(p,rows)
        with self.assertRaises(ValueError):audit_run(self.root,9510)

    def test_synthetic_full57000_requires_exact_tokens_and_all_successful_ranks(self):
        from collections import Counter
        from train.train import lr_at
        f=self.f;target=57000;source=f.candidate['source_order'][0]
        generation=[dict(id=p['id'],prompt=p['text'],category=p['category'],generated_ids=[1]) for p in f.prompts]
        generations=[dict(step=s,sha256=identity(generation)) for s in f.contract['generation_steps'] if 9500<s<=target]
        for rank in range(8):
            f.write(self.root/'ledgers'/f'fixture-rank{rank}.json',dict(rank=rank,success=True,contract_id=f.candidate['id'],
                optimizer_initializations=1,steps=[dict(step=s,source_sequences={source:64}) for s in range(9501,target+1)],generations=generations))
        rows=[dict(step=s,loss=2.,gnorm=.2,lr=lr_at(s-1,f.config),tok_s=140000.,tokens_b=s*2097152/1e9) for s in range(9510,target+1,10)]
        for step in range(9750,target+1,250):
            by_source={g['source']:dict(valid_tokens=g['count']*4096,nll_sum=g['count']*4096*2.,holdout_loss=2.) for g in f.probe['groups']}
            cats=Counter()
            for g in f.probe['groups']:cats[g['category']]+=g['count']*4096
            row=dict(step=step,contract_id=f.candidate['id'],valid_tokens=f.probe['target_tokens'],nll_sum=f.probe['target_tokens']*2.,holdout_loss=2.,
                     by_source=by_source,by_category={k:dict(valid_tokens=n,nll_sum=n*2.,holdout_loss=2.) for k,n in cats.items()})
            if step in f.contract['generation_steps']:row['fixed_generations']=generation
            rows.append(row)
        (self.root/'training/log.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in sorted(rows,key=lambda r:r['step'])))
        cp=self.cp.with_name('ckpt_0057000');self.cp.rename(cp)
        meta=json.loads((cp/'meta.json').read_text());meta['step']=target;f.write(cp/'meta.json',meta)
        files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in sorted(cp.iterdir())]
        f.write(self.root/'checkpoint_certificates/ckpt_0057000.json',dict(status='passed_main_checkpoint_storage',checkpoint=artifact_path(cp),
            step=target,training_contract_id=f.candidate['id'],files=files,bytes=sum(r['bytes'] for r in files)))
        f.write(self.root/'report.json',dict(status='main_target_completed_review_pending',final_checkpoint=artifact_path(cp),trained_tokens=119537664000))
        f.write(self.root/'.done',dict(report_sha256=sha256_file(self.root/'report.json'),contract_sha256=sha256_file(self.root/'contract.json')))
        result=audit_run(self.root,target)
        self.assertEqual(result['counts']['committed_tokens'],119537664000)
        self.assertEqual(result['counts']['committed_step_rank_pairs'],456000)
        self.assertEqual(result['counts']['successful_target_ranks'],list(range(8)))
        self.assertFalse(result['goal_complete'])
        rank=self.root/'ledgers/fixture-rank7.json';v=json.loads(rank.read_text());v['success']=False;f.write(rank,v)
        with self.assertRaisesRegex(ValueError,'successful receipts'):audit_run(self.root,target)
