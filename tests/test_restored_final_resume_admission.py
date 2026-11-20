"""Tiny file admission fixtures. Sealed horizon admission is mocked, no CUDA."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common.data_contracts import resolve, identity, atomic_json, sha256_file, artifact_path
from train.restored_final_resume_probe import check_spec, file_stats, stat_record, verify_final_audit
from scripts.prepare_restored_final_resume import prepare


class FinalHorizonAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='test_final_horizon_', dir=resolve('runs'))
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name); self.main = self.base/'main'; self.native = self.base/'native'
        self.output = self.base/'resume'; self.scratch = self.base/'temp'
        self.main.mkdir(); self.native.mkdir()
        self.config = dict(model='3b',vocab_size=65536,seq_len=4096,micro_bsz=4,global_tokens=2097152,
            max_steps=57000,lr=.0003,min_lr_ratio=.1,warmup=1000,decay_frac=.15,wd=.1,beta1=.9,beta2=.95,
            clip=1.,seed=1234,compile=False,mix=str(self.main/'mix.json'),holdout=[],
            out_dir=str(self.main/'training'),ckpt_every=500,eval_every=250,log_every=10,
            keep_ckpts=57001,save_final=True,sampler_version=2,stop_after=None)
        self.write(self.main/'mix.json', {'sources':{'ko':1}})
        input_file = self.main/'input.bin'; input_file.write_bytes(b'tiny-input')
        self.parent = dict(world_size=8, config=self.config, files={str(input_file):sha256_file(input_file)},
            mix={'sources':{'ko':1}}, source_order=['ko'])
        self.parent['id'] = identity(self.parent)
        self.write(self.main/'training/training_contract.json', self.parent)
        self.write(self.main/'main.json', self.config)
        self.mc = dict(purpose='main_training', main_training_authorized=True,new_checkpoint_pruning_authorized=True,
            configurations={'main.json':sha256_file(self.main/'main.json')}, generation_steps=[57000],
            code={'train/monitored_extended_horizon.py':sha256_file(resolve('train/monitored_extended_horizon.py'))},evidence={},
            tiered_retention={'temp_root':str(self.scratch)}, framework={'torch':'fixture','cuda':None},
            expected_checkpoint_bytes=1024,
            retention_authorization=str(self.main/'authorization.json'),
            retention_revision=dict(authorization=str(self.main/'retention_revision.json')),
            shared_storage=dict(nfs_pools=dict(final_resume=dict(paths=[str(self.output)],budget_bytes=2**30),
                final_native=dict(paths=[str(self.native)],budget_bytes=8*2**30))))
        self.write(self.main/'contract.json', self.mc)
        for name in ['authorization.json','retention_revision.json','expected_tokens.json','parent_handoff.json','lineage/manifest.json','report.json','.done']:
            self.write(self.main/name, {'fixture':name})
        self.nc = dict(purpose='final_native_main_evaluation',final_step=57000,main_run=str(self.main),
            host_rss_gib=256,durable_budget_gib=8,local_budget_gib=8,code={},inputs={})
        self.write(self.native/'contract.json', self.nc)
        mock = patch('train.extended_handoff.load_handoff',return_value=({},self.parent,{}))
        mock.start();self.addCleanup(mock.stop)
        mock=patch('scripts.run_restored_final_resume.admission',return_value=({},{}))
        mock.start();self.addCleanup(mock.stop)

    @staticmethod
    def write(path, value): atomic_json(path, value)

    def plan(self): return prepare(self.main,self.output,native_run=self.native)

    def spec(self):
        plan = self.plan(); checkpoint=self.main/'training/ckpt_0057000'
        checkpoint.mkdir()
        self.write(checkpoint/'meta.json',dict(step=57000,world_size=8,config=self.config,training_contract=self.parent))
        for name in ['.complete','.metadata','__0_0.distcp',*[f'rng_rank{r}.pt' for r in range(8)]]:
            (checkpoint/name).write_bytes(b'tiny-synthetic-checkpoint')
        files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in sorted(checkpoint.iterdir())]
        proof_path=self.main/'checkpoint_certificates/ckpt_0057000.json'
        self.write(proof_path,dict(status='passed_main_checkpoint_storage',step=57000,
            training_contract_id=self.parent['id'],checkpoint=str(checkpoint),files=files,bytes=sum(r['bytes'] for r in files)))
        self.audit=dict(status='passed_restored_committed_prefix_audit',validation_source=False,
            source_run=str(self.main),through_step=57000,target_steps=57000,
            counts=dict(committed_tokens=119537664000,committed_step_rank_pairs=456000,
                successful_target_ranks=list(range(8)),source_tokens={'ko':119537664000},
                segments=[dict(contract_id='original',start_step_exclusive=0,through_step=8000),
                    dict(contract_id='previous',start_step_exclusive=8000,through_step=10000),
                    dict(contract_id=self.parent['id'],start_step_exclusive=10000,through_step=57000)]),
            evidence={str(self.main/p):sha256_file(self.main/p) for p in ['contract.json','main.json','report.json','.done',
                'training/training_contract.json','parent_handoff.json','lineage/manifest.json','checkpoint_certificates/ckpt_0057000.json']},
            code={p:sha256_file(resolve(p)) for p in ['scripts/audit_restored_training.py','scripts/audit_main_training.py']})
        audit_path=self.output/'audit.json';self.write(audit_path,self.audit)
        return dict(plan=plan,plan_id=identity(plan),parent_checkpoint=str(checkpoint),parent_proof=str(proof_path),
            parent_proof_sha256=sha256_file(proof_path),audit=str(audit_path),audit_sha256=sha256_file(audit_path),
            parent_stats=file_stats(checkpoint),input_stats={p:stat_record(resolve(p)) for p in self.parent['files']})

    def test_preparation_and_full_file_admission_preserve_parent(self):
        before=file_stats(self.main);spec=self.spec();plan,config,parent=check_spec(spec)
        self.assertEqual(plan['parent_step'],57000);self.assertEqual(plan['updates'],2)
        self.assertEqual(parent,self.parent);self.assertEqual(config,self.config)
        after=file_stats(self.main)
        for name,stats in before.items():self.assertEqual(stats,after[name])
        self.assertFalse((self.output/'.done').exists())
        self.assertFalse(Path(plan['scratch']).exists())

    def test_old_target_and_missing_shared_reservation_are_refused(self):
        self.config['max_steps']=35000;self.write(self.main/'main.json',self.config)
        with self.assertRaisesRegex(ValueError,'Reviewed'):self.plan()
        self.config['max_steps']=57000;self.write(self.main/'main.json',self.config)
        self.mc['configurations']['main.json']=sha256_file(self.main/'main.json')
        self.mc['shared_storage']['nfs_pools']['final_resume']['budget_bytes']=16*2**20;self.write(self.main/'contract.json',self.mc)
        with self.assertRaisesRegex(ValueError,'reservation'):self.plan()
        self.assertFalse(self.output.exists())

    def test_native57k_and_existing_scratch_are_refused(self):
        self.nc['final_step']=35000;self.write(self.native/'contract.json',self.nc)
        with self.assertRaisesRegex(ValueError,'Concurrent native'):self.plan()
        self.nc['final_step']=57000;self.write(self.native/'contract.json',self.nc)
        (self.scratch/self.output.name).mkdir(parents=True)
        with self.assertRaisesRegex(ValueError,'scratch'):self.plan()
        self.assertFalse(self.output.exists())

    def test_partial_wrong_contract_or_missing_final_audit_is_refused(self):
        self.spec()
        for key,value in [('committed_tokens',2097152),('committed_step_rank_pairs',8),
                          ('successful_target_ranks',list(range(7))),('segments',[])]:
            bad=deepcopy(self.audit);bad['counts'][key]=value
            with self.assertRaisesRegex(ValueError,'final57k8rank'):verify_final_audit(bad,self.main,self.parent['id'])
        bad=deepcopy(self.audit);bad['evidence'].pop(str(self.main/'.done'))
        with self.assertRaisesRegex(ValueError,'evidence missing'):verify_final_audit(bad,self.main,self.parent['id'])
        bad=deepcopy(self.audit);bad['code']={}
        with self.assertRaisesRegex(ValueError,'implementation'):verify_final_audit(bad,self.main,self.parent['id'])

    def test_three_segments_keep_original_ids_and_exact_boundaries(self):
        self.spec()
        for key,value in [('start_step_exclusive',7999),('through_step',9500),('contract_id','original')]:
            bad=deepcopy(self.audit);bad['counts']['segments'][1][key]=value
            with self.assertRaisesRegex(ValueError,'three-segment'):verify_final_audit(bad,self.main,self.parent['id'])
        bad=deepcopy(self.audit);bad['counts']['segments'][-1]['contract_id']='previous'
        with self.assertRaisesRegex(ValueError,'final57k8rank'):verify_final_audit(bad,self.main,self.parent['id'])

    def test_payload_mutation_and_input_mutation_fail_stat_snapshot(self):
        spec=self.spec();p=Path(spec['parent_checkpoint'])/'__0_0.distcp'
        p.write_bytes(b'x'*p.stat().st_size)
        with self.assertRaisesRegex(ValueError,'Parent changed'):check_spec(spec)
        spec['parent_stats']=file_stats(Path(spec['parent_checkpoint']))
        input_path=next(iter(self.parent['files']));Path(input_path).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'Training input changed'):check_spec(spec)

    def test_missing_code_and_input_bindings_fail_even_with_new_plan_id(self):
        spec=self.spec();spec['plan']['code'].pop('train/extended_handoff.py');spec['plan_id']=identity(spec['plan'])
        with self.assertRaisesRegex(ValueError,'code bindings'):check_spec(spec)
        spec['plan']['code']['train/extended_handoff.py']=sha256_file(resolve('train/extended_handoff.py'))
        spec['plan']['inputs'].pop(artifact_path(self.main/'authorization.json'));spec['plan_id']=identity(spec['plan'])
        with self.assertRaisesRegex(ValueError,'input bindings'):check_spec(spec)

    def test_diagnostic_lr_cannot_change_final_parent_schedule(self):
        spec=self.spec();spec['plan']['diagnostic_lr']=.0003;spec['plan_id']=identity(spec['plan'])
        with self.assertRaisesRegex(ValueError,'Diagnostic LR'):check_spec(spec)
        self.assertEqual(json.loads((self.main/'main.json').read_text())['max_steps'],57000)

    def test_parent_checkpoint_must_be_exact_final_nfs_path(self):
        spec=self.spec();spec['parent_checkpoint']=str(self.scratch/'ckpt_0057000')
        with self.assertRaisesRegex(ValueError,'exact final NFS'):check_spec(spec)
        self.assertFalse(self.scratch.exists())

    def test_controller_preparation_binds_code_without_launch(self):
        from argparse import Namespace
        from scripts.run_restored_final_resume import load_plan,main
        plan=self.plan();self.assertEqual(load_plan(self.output),plan)
        main(Namespace(run_dir=str(self.output),worker=False,prepare=True,run=False))
        contract=json.loads((self.output/'contract.json').read_text())
        self.assertIn('scripts/run_restored_final_resume.py',contract['code'])
        self.assertIn('scripts/horizon_probe_process.py',contract['code'])
        self.assertEqual(contract['worker_max_seconds'],3600)
        self.assertFalse((self.output/'execution_started.json').exists())
        self.assertFalse((self.output/'.done').exists())

    def test_controller_materializes_fresh_final_spec_without_parent_mutation(self):
        from scripts.run_restored_final_resume import materialize_spec
        spec=self.spec();plan=spec['plan'];checkpoint=Path(spec['parent_checkpoint'])
        proof=json.loads(Path(spec['parent_proof']).read_text());meta=json.loads((checkpoint/'meta.json').read_text())
        audit=deepcopy(self.audit);audit['checkpoint_certificate']=spec['parent_proof']
        before=file_stats(checkpoint)
        # Fixture supplies in-memory spec; production materialization writes it once.
        with patch('scripts.run_restored_final_resume.completed_main',return_value=(checkpoint,proof,meta,audit)),patch(
                'scripts.run_restored_final_resume.fresh_inputs',return_value=spec['input_stats']) as fresh:
            result=materialize_spec(self.output,plan)
            self.assertEqual(result['parent_stats'],before);self.assertEqual(file_stats(checkpoint),before)
            fresh.assert_called_once()
            with self.assertRaisesRegex(ValueError,'Existing final'):materialize_spec(self.output,plan)

if __name__=='__main__':unittest.main()
