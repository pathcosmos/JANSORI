from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest

from common.data_contracts import resolve, identity, sha256_file
from train.horizon_worker_contract import (BRIDGE_CODE, executable_candidate,
                                            validate_executable_candidate)
from train.monitored_horizon import validate_configuration, load_handoff


class HorizonWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='horizon-worker-fixture-',dir=resolve('runs'))
        self.root=Path(self.temp.name)
        self.config=dict(model='3b',vocab_size=65536,seq_len=4096,micro_bsz=4,
            global_tokens=2097152,compile=False,sampler_version=2,max_steps=35000,
            lr=.0003,warmup=1000,decay_frac=.15,min_lr_ratio=.1,eval_every=250,
            ckpt_every=500,log_every=10,save_final=True,keep_ckpts=35001,stop_after=4,
            out_dir=str(self.root/'training'))
        cfg={k:self.config[k] for k in ['model','vocab_size','seq_len','micro_bsz','global_tokens',
             'compile','sampler_version','max_steps','lr','warmup','decay_frac','min_lr_ratio']}
        cfg['max_steps']=57000
        parent=dict(config=cfg,world_size=8,files={'tokens':'bound'},mix={},source_order=['ko'],
            holdout=[],code={'native-trainer':'bound'},execution={'deterministic_algorithms':True},
            monitored_evaluation=dict(code_sha256='parent-worker',generation_steps=[250,2500,5000,10000,35000,57000]))
        parent['id']=identity(parent)
        self.meta=dict(training_contract=parent,config=cfg,world_size=8,step=2,
                       tokens_per_step=2097152,sampler_version=2)
        protected=self.root/'parent_checkpoint/ckpt_0000002';protected.mkdir(parents=True)
        (self.root/'parent_certificate.json').write_text(json.dumps(
            {'step':2,'training_contract_id':parent['id'],'fixture_only':True}))
        lineage=self.root/'lineage';lineage.mkdir()
        names=['audit.json','trace.jsonl',*[f'rank{r}.json' for r in range(8)]]
        for name in names:(lineage/name).write_text('{}\n')
        from scripts.audit_main_training import aggregate
        attempts=[]
        for rank in range(8):
            record=dict(rank=rank,success=False,contract_id=parent['id'],optimizer_initializations=0,
                steps=[dict(step=s,source_sequences={'ko':64}) for s in [1,2]],generations=[])
            attempts.append(('fixture',record))
            (lineage/f'rank{rank}.json').write_text(json.dumps([dict(attempt='fixture',record=record)]))
        counts=aggregate(attempts,world=8,through_step=2,target_steps=57000,seq_len=4096,
            global_tokens=2097152,sources=['ko'],generation_steps=parent['monitored_evaluation']['generation_steps'],contract_id=parent['id'])
        (lineage/'audit.json').write_text(json.dumps(dict(counts=counts,fixture_only=True)))
        manifest=dict(through_step=2,committed_tokens=2*2097152,
            committed_step_rank_pairs=2*8,training_contract_id=parent['id'],
            files={n:sha256_file(lineage/n) for n in names})
        (lineage/'manifest.json').write_text(json.dumps(manifest))
        self.binding=dict(parent_contract_id=parent['id'],parent_step=2,parent_metadata_id=identity(self.meta),
            parent_certificate_sha256=sha256_file(self.root/'parent_certificate.json'),
            parent_ledger_manifest_sha256=sha256_file(lineage/'manifest.json'),
            code={p:sha256_file(resolve(p)) for p in BRIDGE_CODE})
        self.receipt=dict(status='sealed_parent_handoff',execution_binding=self.binding,
            parent_metadata=self.meta,parent_checkpoint=str(protected),
            stopped_parent_processes={n:dict(pid=2147483600+i,start_ticks=1) for i,n in
                enumerate(['supervisor','worker',*[f'rank{r}' for r in range(8)]])})
        self.contract=dict(purpose='horizon_validation',main_training_authorized=True,
            generation_steps=[250,2500,5000,10000,35000],horizon_execution=self.binding,
            evidence={},code=dict(self.binding['code']))
        self.contract['code'].update({p:sha256_file(resolve(p)) for p in ['train/final_resume_probe.py','scripts/audit_main_training.py','scripts/tiered_checkpoint_retention.py']})
        self.publish_receipt()

    def tearDown(self):self.temp.cleanup()

    def publish_receipt(self):
        p=self.root/'parent_handoff.json';p.write_text(json.dumps(self.receipt))
        self.contract['evidence'][str(p)]=sha256_file(p)

    def test_bound_candidate_only_allows_new_worker_and_explicit_lineage(self):
        c=executable_candidate(self.meta,self.binding)
        validate_executable_candidate(self.meta,c,self.binding)
        for path,value in [(['config','lr'],.001),(['files','tokens'],'changed'),
                            (['monitored_evaluation','code_sha256'],'wrong'),
                            (['horizon_transition','parent_step'],6999)]:
            with self.subTest(path=path):
                bad=deepcopy(c);dest=bad
                for k in path[:-1]:dest=dest[k]
                dest[path[-1]]=value;bad.pop('id');bad['id']=identity(bad)
                with self.assertRaises(ValueError):validate_executable_candidate(self.meta,bad,self.binding)

    def test_missing_or_unbound_bridge_code_and_parent_rejected(self):
        for key,value in [('parent_step',True),('parent_contract_id','wrong'),
                          ('parent_metadata_id','wrong'),('parent_certificate_sha256','not-sha'),
                          ('code',{})]:
            with self.subTest(key=key):
                b=deepcopy(self.binding);b[key]=value
                with self.assertRaises(ValueError):executable_candidate(self.meta,b)

    def test_exact_geometry_and_diagnostic_update_limit(self):
        self.assertEqual(validate_configuration(self.config,self.contract)['target_tokens'],73400320000)
        for key,value in [('max_steps',57000),('max_steps',40000),('compile',True),('lr',.001),('eval_every',500),
                          ('keep_ckpts',3),('stop_after',5),('stop_after',None),('log_every',20)]:
            with self.subTest(key=key,value=value):
                c=dict(self.config);c[key]=value
                with self.assertRaises(ValueError):validate_configuration(c,self.contract)
        c=dict(self.contract,main_training_authorized=False)
        with self.assertRaises(ValueError):validate_configuration(self.config,c)
        c=dict(self.contract,purpose='main_training')
        with self.assertRaises(ValueError):validate_configuration(self.config,c)
        validate_configuration(dict(self.config,stop_after=None),c)

    def test_sealed_handoff_schema_and_code_bindings(self):
        receipt,candidate,proof=load_handoff(self.root,self.contract,self.config)
        self.assertEqual(receipt,self.receipt)
        self.assertEqual(candidate,executable_candidate(self.meta,self.binding))
        self.assertEqual(proof['step'],2)
        # This fixture validates admission schema, NOT payload or distributed execution.
        self.assertFalse((self.root/'training').exists())

    def test_live_parent_process_blocks_handoff(self):
        text=Path('/proc/self/stat').read_text()
        self.receipt['stopped_parent_processes']['worker']=dict(pid=os.getpid(),start_ticks=int(text[text.rfind(')')+2:].split()[19]))
        self.publish_receipt()
        with self.assertRaisesRegex(ValueError,'still present'):load_handoff(self.root,self.contract,self.config)

    def test_unprotected_parent_or_missing_rank_ledger_is_rejected(self):
        self.receipt['parent_checkpoint']=str(self.root/'lineage');self.publish_receipt()
        with self.assertRaisesRegex(ValueError,'protected'):load_handoff(self.root,self.contract,self.config)
        self.receipt['parent_checkpoint']=str(self.root/'parent_checkpoint/ckpt_0000002');self.publish_receipt()
        (self.root/'lineage/rank7.json').write_text('changed')
        with self.assertRaisesRegex(ValueError,'lineage file changed'):load_handoff(self.root,self.contract,self.config)

    def test_receipt_and_certificate_tampering_is_rejected(self):
        (self.root/'parent_handoff.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'not bound'):load_handoff(self.root,self.contract,self.config)
        self.publish_receipt();(self.root/'parent_certificate.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'certificate changed'):load_handoff(self.root,self.contract,self.config)

    def test_rehashed_manifest_cannot_hide_missing_rank_update(self):
        rank_path=self.root/'lineage/rank7.json'
        rows=json.loads(rank_path.read_text());rows[0]['record']['steps'].pop()
        rank_path.write_text(json.dumps(rows))
        manifest_path=self.root/'lineage/manifest.json'
        manifest=json.loads(manifest_path.read_text())
        manifest['files']['rank7.json']=sha256_file(rank_path)
        manifest_path.write_text(json.dumps(manifest))
        self.binding['parent_ledger_manifest_sha256']=sha256_file(manifest_path)
        self.publish_receipt()
        with self.assertRaisesRegex(ValueError,'Incomplete step/rank coverage'):
            load_handoff(self.root,self.contract,self.config)

    def test_main_requires_bound_actual_8gpu_evidence_not_cpu_report(self):
        self.contract['purpose']='main_training'
        report=self.root/'diagnostic.json'
        self.contract['horizon_validation_report']=str(report)
        report.write_text(json.dumps(dict(status='passed_cpu_tests',world_size=1)))
        self.contract['evidence'][str(report)]=sha256_file(report)
        with self.assertRaisesRegex(ValueError,'Actual8GPU'):load_handoff(self.root,self.contract,self.config)
        candidate=executable_candidate(self.meta,self.binding)
        report.write_text(json.dumps(dict(status='passed_actual_8rank_horizon_resume',world_size=8,
            parent_contract_id=self.meta['training_contract']['id'],child_contract_id=candidate['id'],
            parent_step=2,model_optimizer_rng_exact=True,next_update_exact=True,split_resume_exact=True)))
        self.contract['evidence'][str(report)]=sha256_file(report)
        with self.assertRaisesRegex(ValueError,'underlying evidence'):load_handoff(self.root,self.contract,self.config)


if __name__=='__main__':unittest.main()
