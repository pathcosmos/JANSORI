"""Synthetic absolute35k coverage and real fixture files; no CUDA claims."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common.data_contracts import resolve,identity,sha256_file,artifact_path
from scripts.audit_main_training import aggregate,audit_trace
from scripts.audit_horizon_training import aggregate_suffix,combine_counts,audit_range_trace,audit_run
from train.final_resume_probe import file_stats
from train import train as trainer


def ledger(rank,steps,cid,*,success=False,generation_steps=()):
    return dict(rank=rank,success=success,contract_id=cid,optimizer_initializations=1,
        steps=[dict(step=s,source_sequences={'ko':64}) for s in steps],
        generations=[dict(step=s,sha256='a'*64) for s in generation_steps])


def child_contract():
    return dict(id='child',world_size=8,config=dict(max_steps=35000,seq_len=4096,global_tokens=2097152),
        source_order=['ko'],monitored_evaluation={'generation_steps':[250,35000]})


class HorizonCountTests(unittest.TestCase):
    def test_full35000_coverage_without_relabelling_parent_contract(self):
        parent=[('original',ledger(r,range(1,501),'parent',generation_steps=[250])) for r in range(8)]
        pc=aggregate(parent,world=8,through_step=500,target_steps=57000,seq_len=4096,global_tokens=2097152,
            sources=['ko'],generation_steps=[250,57000],contract_id='parent')
        child=[('new',ledger(r,range(501,35001),'child',success=True,generation_steps=[35000])) for r in range(8)]
        cc=aggregate_suffix(child,parent_step=500,through_step=35000,contract=child_contract())
        merged=combine_counts(pc,cc,parent_step=500,through_step=35000)
        self.assertEqual(merged['committed_tokens'],73400320000)
        self.assertEqual(merged['committed_step_rank_pairs'],280000)
        self.assertEqual(merged['successful_target_ranks'],list(range(8)))
        self.assertEqual(set(merged['generation_sha256_by_step']),{'250','35000'})
        self.assertEqual(parent[0][1]['contract_id'],'parent')
        self.assertEqual(child[0][1]['steps'][0]['step'],501)

    def test_replay_counts_once_and_requires_same_source_choices(self):
        rows=[('first',ledger(r,range(501,511),'child')) for r in range(8)]
        rows += [('resume',ledger(r,range(510,521),'child')) for r in range(8)]
        counts=aggregate_suffix(rows,parent_step=500,through_step=520,contract=child_contract())
        self.assertEqual(counts['committed_tokens'],20*2097152)
        self.assertEqual(counts['replayed_tokens_through_prefix'],2097152)
        rows[-1][1]['steps'][0]['source_sequences']={'ko':63}
        with self.assertRaisesRegex(ValueError,'geometry'):aggregate_suffix(rows,parent_step=500,through_step=520,contract=child_contract())

    def test_parent_overlap_missing_rank_wrong_identity_and_gap_rejected(self):
        rows=[('child',ledger(r,range(501,511),'child')) for r in range(8)]
        cases=[]
        bad=deepcopy(rows);bad[0][1]['steps'][0]['step']=500;cases.append(bad)
        cases.append(rows[:-1])
        bad=deepcopy(rows);bad[0][1]['contract_id']='parent';cases.append(bad)
        bad=deepcopy(rows);bad[0][1]['steps'].pop(3);cases.append(bad)
        for bad in cases:
            with self.subTest(case=len(bad)),self.assertRaises(ValueError):
                aggregate_suffix(bad,parent_step=500,through_step=510,contract=child_contract())

    def test_final_success_requires_each_rank(self):
        rows=[('child',ledger(r,[35000],'child',success=r!=7,generation_steps=[35000])) for r in range(8)]
        counts=aggregate_suffix(rows,parent_step=34999,through_step=35000,contract=child_contract())
        self.assertEqual(counts['successful_target_ranks'],list(range(7)))


class HorizonAuditIntegrationTests(unittest.TestCase):
    """Real file/schema integration. Worker admission and checkpoint payload are synthetic."""
    def setUp(self):
        t=tempfile.TemporaryDirectory(prefix='horizon-audit-fixture-',dir=resolve('runs'))
        self.addCleanup(t.cleanup);self.root=Path(t.name)
        for name in ['training','lineage','ledgers','checkpoint_certificates','probe']: (self.root/name).mkdir()
        def write(name,value):
            p=self.root/name;p.write_text(json.dumps(value)+'\n');return p
        self.write=write
        self.config=dict(trainer.DEFAULT,model='3b',micro_bsz=4,max_steps=35000,lr=.0003,warmup=1000,
            compile=False,keep_ckpts=35001,out_dir=str(self.root/'training'),mix=str(self.root/'mix.json'))
        self.old_config=dict(self.config,max_steps=57000)
        self.child=child_contract();self.child['config']=deepcopy(self.config)
        self.parent=deepcopy(self.child);self.parent.update(id='parent',config=deepcopy(self.old_config))
        self.parent['monitored_evaluation']['generation_steps']=[250,57000]
        prompts=[dict(id='p',text='q',category='ko')];self.prompts=prompts
        self.generation=[dict(id='p',prompt='q',category='ko',generated_ids=[1])]
        self.digest=identity(self.generation)
        probe=dict(groups=[dict(source='ko',category='ko',count=1)],target_tokens=4096)
        self.probe=probe;write('probe/probe.json',probe);write('prompts.jsonl',prompts[0]);write('mix.json',{'sources':{'ko':1}})
        self.child['monitored_evaluation'].update(probe_sha256=sha256_file(self.root/'probe/probe.json'),
            prompts_sha256=sha256_file(self.root/'prompts.jsonl'))
        write('training/training_contract.json',self.child);write('main.json',self.config)
        self.parent_records=[]
        for rank in range(8):
            row=ledger(rank,range(1,501),'parent',generation_steps=[250]);row['generations'][0]['sha256']=self.digest
            self.parent_records.append(('old',row));write(f'lineage/rank{rank}.json',[dict(attempt='old',record=row)])
            write(f'ledgers/new-rank{rank}.json',ledger(rank,range(501,1001),'child'))
        pc=aggregate(self.parent_records,world=8,through_step=500,target_steps=57000,seq_len=4096,
            global_tokens=2097152,sources=['ko'],generation_steps=[250,57000],contract_id='parent')
        pt=self.trace(0,500,'parent');ct=self.trace(500,1000,'child')
        self.write_lines('lineage/trace.jsonl',pt);self.write_lines('training/log.jsonl',ct)
        trace=audit_trace(pt,config=self.old_config,through_step=500,contract_id='parent',probe=probe,prompts=prompts,generation_hashes={'250':self.digest})
        write('lineage/audit.json',dict(counts=pc,trace=trace))
        manifest=dict(files={p.name:sha256_file(p) for p in (self.root/'lineage').iterdir()})
        write('lineage/manifest.json',manifest)
        parent_meta=dict(step=500,world_size=8,tokens_per_step=2097152,config=self.old_config,training_contract=self.parent)
        pp=self.root/'parent_checkpoint/ckpt_0000500';self.parent_proof=self.make_checkpoint(pp,parent_meta)
        write('parent_certificate.json',self.parent_proof)
        self.receipt=dict(parent_metadata=parent_meta,parent_checkpoint=str(pp),parent_stats=file_stats(pp),
            execution_binding=dict(parent_ledger_manifest_sha256=sha256_file(self.root/'lineage/manifest.json'),
                parent_certificate_sha256=sha256_file(self.root/'parent_certificate.json')))
        write('parent_handoff.json',self.receipt)
        meta=dict(step=1000,world_size=8,tokens_per_step=2097152,config=self.config,training_contract=self.child)
        cp=self.root/'training/ckpt_0001000';self.proof=self.make_checkpoint(cp,meta)
        write('checkpoint_certificates/ckpt_0001000.json',self.proof)
        self.contract=dict(purpose='main_training',main_training_authorized=True,code={},
            evidence={str(self.root/'parent_handoff.json'):sha256_file(self.root/'parent_handoff.json')},
            configurations={'main.json':sha256_file(self.root/'main.json')},prompts=str(self.root/'prompts.jsonl'),generation_steps=[250,35000])
        write('contract.json',self.contract)
        mock=patch('scripts.audit_horizon_training.load_handoff',return_value=(self.receipt,self.child,self.parent_proof))
        mock.start();self.addCleanup(mock.stop)

    def make_checkpoint(self,path,meta):
        path.mkdir(parents=True)
        (path/'meta.json').write_text(json.dumps(meta))
        for name in ['.complete','.metadata','__0_0.distcp',*[f'rng_rank{r}.pt' for r in range(8)]]:(path/name).write_text('fixture-'+name)
        files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in sorted(path.iterdir())]
        return dict(status='passed_main_checkpoint_storage',checkpoint=str(path),step=meta['step'],
            training_contract_id=meta['training_contract']['id'],files=files,bytes=sum(r['bytes'] for r in files))

    def trace(self,start,end,cid):
        rows=[dict(step=s,loss=2.,gnorm=1.,lr=.0003,tok_s=100.,tokens_b=s*2097152/1e9) for s in range(start+10,end+1,10)]
        for s in range(start+250,end+1,250):
            v=dict(valid_tokens=4096,nll_sum=8192.,holdout_loss=2.)
            row=dict(step=s,contract_id=cid,by_source={'ko':v},by_category={'ko':v},**v)
            if s==250:row['fixed_generations']=deepcopy(self.generation)
            rows.append(row)
        return rows

    def write_lines(self,name,rows):(self.root/name).write_text(''.join(json.dumps(r)+'\n' for r in rows))
    def test_file_audit_recomputes_both_identities_and_counts(self):
        report=audit_run(self.root,1000)
        self.assertEqual(report['counts']['committed_tokens'],2097152000)
        self.assertEqual(report['counts']['committed_step_rank_pairs'],8000)
        self.assertEqual(report['counts']['parent_contract_id'],'parent')
        self.assertEqual(report['trace']['parent']['evaluation_points'],2)
        self.assertEqual(report['trace']['child']['evaluation_points'],2)
        self.assertFalse(report['goal_complete'])

    def test_child_cannot_supply_missing_parent_records(self):
        p=self.root/'lineage/rank0.json';v=json.loads(p.read_text());v[0]['record']['steps'].pop();p.write_text(json.dumps(v))
        with self.assertRaisesRegex(ValueError,'coverage'):audit_run(self.root,1000)

    def test_child_trace_overlap_identity_missing_eval_and_nonfinite_fail(self):
        rows=self.trace(500,1000,'child')
        variants=[rows+self.trace(0,10,'parent'),rows[:-1]]
        bad=deepcopy(rows);bad[-1]['contract_id']='parent';variants.append(bad)
        bad=deepcopy(rows);bad[0]['loss']=float('nan');variants.append(bad)
        for rows in variants:
            self.write_lines('training/log.jsonl',rows)
            with self.assertRaises(ValueError):audit_run(self.root,1000)

    def test_rng_damage_and_metadata_contract_change_fail(self):
        p=self.root/'training/ckpt_0001000/rng_rank7.pt';p.write_bytes(b'x'*p.stat().st_size)
        with self.assertRaisesRegex(ValueError,'RNG content'):audit_run(self.root,1000)

    def test_changed_lineage_manifest_and_protected_parent_refused(self):
        self.write('lineage/manifest.json',{'files':{}})
        with self.assertRaisesRegex(ValueError,'manifest/certificate'):audit_run(self.root,1000)

    def test_original_prefix_trace_is_rechecked(self):
        self.write_lines('lineage/trace.jsonl',self.trace(0,500,'parent')[1:])
        with self.assertRaisesRegex(ValueError,'Missing train'):audit_run(self.root,1000)

    def test_full_final_audit_requires_bound_marker_and_all_successful_ranks(self):
        # A full synthetic280,000-rank/update history, not actual training.
        for rank in range(8):
            row=ledger(rank,range(501,35001),'child',success=True,generation_steps=[35000])
            row['generations'][0]['sha256']=self.digest
            self.write(f'ledgers/new-rank{rank}.json',row)
        rows=self.trace(500,35000,'child');rows[-1]['fixed_generations']=deepcopy(self.generation)
        self.write_lines('training/log.jsonl',rows)
        checkpoint=self.root/'training/ckpt_0035000'
        proof=self.make_checkpoint(checkpoint,dict(step=35000,world_size=8,tokens_per_step=2097152,
            config=self.config,training_contract=self.child))
        self.write('checkpoint_certificates/ckpt_0035000.json',proof)
        self.write('report.json',dict(status='main_target_completed_review_pending',
            final_checkpoint=str(checkpoint),trained_tokens=73400320000))
        marker=dict(report_sha256=sha256_file(self.root/'report.json'),contract_sha256=sha256_file(self.root/'contract.json'))
        self.write('.done',marker)
        result=audit_run(self.root,35000)
        self.assertEqual(result['counts']['committed_tokens'],73400320000)
        self.assertEqual(result['counts']['successful_target_ranks'],list(range(8)))
        self.assertFalse(result['goal_complete'])
        marker['report_sha256']='0'*64;self.write('.done',marker)
        with self.assertRaisesRegex(ValueError,'Final35k completion'):audit_run(self.root,35000)
        marker['report_sha256']=sha256_file(self.root/'report.json');self.write('.done',marker)
        row=json.loads((self.root/'ledgers/new-rank7.json').read_text());row['success']=False
        self.write('ledgers/new-rank7.json',row)
        with self.assertRaisesRegex(ValueError,'Final35k completion'):audit_run(self.root,35000)


if __name__=='__main__':unittest.main()
