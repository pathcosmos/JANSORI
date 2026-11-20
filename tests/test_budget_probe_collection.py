"""Synthetic end-to-end evidence assembly; not a3B or CUDA acceptance test.

All launch receipts, fingerprints and comparison results below are fabricated
test inputs in private temporary namespaces. Actual CPU DCP and process tests
are separate. This suite tests the collector's evidence relationships/rejections.
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import unittest

from common.data_contracts import identity, sha256_file, atomic_json, resolve
from scripts.run_budget_probe import collect, command_for, checkpoint_targets, STAGES, MODES, GIB
from scripts.prepare_budget_probe import resume_spec
from train.budget_horizon_contract import bound_candidate
from train.horizon_probe_observer import MODES as BOUNDS
from train.final_resume_probe import file_stats
from train.train import lr_at
import test_prepare_budget_probe as preparation_fixture


@unittest.skipUnless(__import__('os').environ.get('DENSE_BUDGET_LINEAGE_FIXTURE'),'Explicit prefix fixture required')
class BudgetCollectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture=preparation_fixture.BudgetProbePreparationTests();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        prepared=self.fixture.prepare()
        self.path=Path(prepared['plan']);self.root=self.path.parent
        self.plan=json.loads(self.path.read_text());self.plan_id=identity(self.plan)
        self.receipt=json.loads((self.fixture.root/'parent_handoff.json').read_text())
        self.parent=self.receipt['parent_metadata']['training_contract']
        self.child=bound_candidate(self.receipt['parent_metadata'],self.receipt['execution_binding'])
        for mode in ('continuous','first'):
            self.fake_certificates(mode)
        resume_spec(self.path)  # Exercise real deferred-spec publication before second output.
        self.fake_certificates('resumed')
        atomic_json(self.root/'execution_started.json',dict(plan_id=self.plan_id,pid=999,time_ns=1))
        atomic_json(self.root/'execution_hardware.json',dict(gpus=self.fixture.draft['gpu_identity']))
        comparison=dict(exact=True,model_tensors=255,optimizer_tensors=765,tensors=1020,rng_ranks=8,
                        elapsed_seconds=0,synthetic_fixture_only=True)
        atomic_json(self.root/'comparisons.json',dict(plan_id=self.plan_id,
            parent_restored=comparison,continuous_resumed=comparison))
        for number,phase in enumerate(STAGES):
            launcher=15500+number*100
            process=dict(pid=launcher,start_ticks=100,launcher=True)
            rows=[process]
            if phase in MODES:rows += [dict(pid=launcher+r+1,start_ticks=200+r,launcher=False) for r in range(8)]
            command=command_for(self.path,phase);owner='a'*32
            directory=self.root/'commands'/phase
            atomic_json(directory/'result.json',dict(status='command_exited_successfully',exit_code=0,
                command=command,launcher_pid=launcher,owner=owner,processes=rows,
                remaining_owned_processes=[],elapsed_seconds=1,peak_pss_bytes=1))
            atomic_json(directory/'handle.json',dict(pid=launcher,command=command,owner=owner,process=process))
            if phase not in MODES:continue
            row=self.plan['modes'][phase];branch=resolve(row['run_dir'])
            spec=json.loads((self.root/(phase+'.json')).read_text())
            contract=self.parent if phase=='baseline' else self.child
            atomic_json(resolve(self.plan['payload_roots'][phase])/'training/training_contract.json',contract)
            config=json.loads(resolve(row['config']).read_text())
            begin,end=BOUNDS[phase]
            for rank in range(8):
                atomic_json(branch/'execution'/f'{phase}-rank{rank}.json',dict(mode=phase,rank=rank,
                    pid=launcher+rank+1,parent_pid=launcher,start_ticks=200+rank,world_size=8,
                    backend='nccl',local_rank=rank,device_name='H200-synthetic',device_total_memory=140*GIB,
                    device_uuid='synthetic-'+str(rank),framework=self.plan['framework'],
                    diagnostic_only=True,plan_id=self.plan_id,spec_id=identity(spec)))
                initial=dict(step=15500+begin,state=self.state(begin,rank))
                updates=[dict(step=15500+i,lr=lr_at(15499+i,config),state=self.state(i,rank)) for i in range(begin+1,end+1)]
                atomic_json(branch/'observations'/f'{phase}-rank{rank}.json',dict(mode=phase,rank=rank,
                    world_size=8,plan_id=self.plan_id,parent_step=15500,success=True,diagnostic_only=True,
                    main_committed_tokens_added=0,start_step=15500+begin,end_step=15500+end,
                    initial=initial,updates=updates))
                atomic_json(branch/'ledgers'/(row['attempt']+f'-rank{rank}.json'),dict(rank=rank,
                    success=True,contract_id=contract['id'],optimizer_initializations=1,generations=[],
                    steps=[dict(step=15500+i,source_sequences={self.parent['source_order'][0]:64}) for i in range(begin+1,end+1)]))

    def state(self,offset,rank):
        result=dict(device_type='cuda')
        for name,count in [('model',255),('optimizer',765),('rng',2)]:
            result[name]=dict(sha256=identity(dict(synthetic=True,part=name,offset=offset,rank=rank)),
                             tensors=count,global_elements=count,local_bytes=count*4)
        result['identity']=identity(result)
        return result

    def fake_certificates(self,mode):
        source=Path(self.receipt['parent_checkpoint'])
        for path,meta,cert in checkpoint_targets(self.plan,mode):
            shutil.copytree(source,path)
            atomic_json(path/'meta.json',meta)
            files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in sorted(path.iterdir())]
            atomic_json(cert,dict(status='certified_horizon_probe_checkpoint',cuda_rng_required=True,
                diagnostic_only=True,checkpoint=str(path),path=str(path),metadata=meta,step=meta['step'],
                training_contract_id=self.child['id'],stats=file_stats(path),files=files,
                bytes=sum(r['bytes'] for r in files)))

    def mutate(self,path,fn):
        value=json.loads(path.read_text());fn(value);atomic_json(path,value)

    def test_synthetic_evidence_relationships_collect_without_adding_main_tokens(self):
        result=collect(self.path)
        self.assertEqual(result['world_size'],8)
        self.assertEqual(result['main_committed_tokens_added'],0)
        self.assertEqual(result['source_ledger']['actual_diagnostic_tokens'],12582912)
        self.assertGreater(len(result['evidence']),100)
        self.assertFalse((self.root/'.done').exists())  # Only the controller may finish its execution.

    def test_missing_rank_execution_receipt_is_not_acceptance(self):
        (self.fixture.continuous/'execution/continuous-rank7.json').unlink()
        with self.assertRaises(FileNotFoundError):collect(self.path)
        self.assertFalse((self.root/'report.json').exists())

    def test_native_training_contract_must_match_exact_candidate(self):
        p=resolve(self.plan['payload_roots']['resumed'])/'training/training_contract.json'
        self.mutate(p,lambda v:v['config'].update(max_steps=35000))
        with self.assertRaisesRegex(ValueError,'Actual native training contract'):collect(self.path)

    def test_same_size_protected_payload_change_refused(self):
        p=Path(self.receipt['parent_checkpoint'])/'__0_0.distcp'
        p.write_bytes(b'X'*p.stat().st_size)
        with self.assertRaisesRegex(ValueError,'Protected parent changed'):collect(self.path)

    def test_resume_must_use_the_fresh_intermediate_certificate(self):
        p=self.root/'resumed.json'
        self.mutate(p,lambda v:v.update(resume_certificate_sha256='0'*64))
        with self.assertRaisesRegex(ValueError,'another intermediate checkpoint'):collect(self.path)

    def test_rehashed_changed_optimizer_observation_fails_comparison(self):
        p=self.fixture.split/'observations/resumed-rank0.json'
        def corrupt(v):
            state=v['updates'][0]['state'];state['optimizer']['sha256']='0'*64
            state.pop('identity');state['identity']=identity(state)
        self.mutate(p,corrupt)
        with self.assertRaisesRegex(ValueError,'Continuous/split state trajectory'):collect(self.path)

    def test_sampler_coverage_must_match_real_global_batch(self):
        p=self.fixture.split/'ledgers/horizon-probe-resumed-rank0.json'
        self.mutate(p,lambda v:v['steps'][0]['source_sequences'].update({self.parent['source_order'][0]:63}))
        with self.assertRaisesRegex(ValueError,'batch geometry'):collect(self.path)

    def test_launcher_handle_mismatch_preserves_failed_evidence(self):
        p=self.root/'commands/continuous/handle.json'
        self.mutate(p,lambda v:v.update(owner='b'*32))
        with self.assertRaisesRegex(ValueError,'bound controller'):collect(self.path)
        self.assertEqual(json.loads(p.read_text())['owner'],'b'*32)

    def test_partial_independent_comparison_is_not_complete(self):
        p=self.root/'comparisons.json'
        self.mutate(p,lambda v:v['continuous_resumed'].update(optimizer_tensors=764))
        with self.assertRaisesRegex(ValueError,'Full3B'):collect(self.path)

    def test_missing_certificate_or_failed_certifier_phase_refused(self):
        p=self.root/'commands/certify-first/result.json'
        self.mutate(p,lambda v:v.update(exit_code=1))
        with self.assertRaisesRegex(ValueError,'bound controller'):collect(self.path)
        self.mutate(p,lambda v:v.update(exit_code=0))
        (self.fixture.split/'certificates/first.json').unlink()
        with self.assertRaises(FileNotFoundError):collect(self.path)


if __name__=='__main__':unittest.main()
