from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import nullcontext

from common.data_contracts import identity
from scripts.run_horizon_probe import (validate_runtime, require_comparison, command_for,
                                      checkpoint_targets, execute, load_plan, STAGES, GIB)
import test_prepare_horizon_probe as preparation_fixture


class RuntimeProvenanceTests(unittest.TestCase):
    """Synthetic receipts exercise rejection; no claim of CUDA execution."""
    def setUp(self):
        self.plan={'framework':{'torch':'fixture','cuda':'fixture'}}
        self.spec={'plan':self.plan,'plan_id':identity(self.plan)}
        self.launch=dict(status='command_exited_successfully',exit_code=0,remaining_owned_processes=[],
            launcher_pid=1000,processes=[dict(pid=1000,start_ticks=10,launcher=True)])
        self.rows=[]
        for rank in range(8):
            self.launch['processes'].append(dict(pid=1001+rank,start_ticks=20+rank,launcher=False))
            self.rows.append(dict(rank=rank,pid=1001+rank,start_ticks=20+rank,parent_pid=1000,
                plan_id=identity(self.plan),spec_id=identity(self.spec),mode='continuous',world_size=8,
                backend='nccl',local_rank=rank,device_name='H200-fixture',device_total_memory=140*GIB,
                framework=self.plan['framework'],diagnostic_only=True,device_uuid='fixture-'+str(rank)))

    def test_full_coverage_schema_and_missing_rank_pid_binding(self):
        validate_runtime(self.plan,self.spec,'continuous',self.launch,self.rows)
        with self.assertRaises(ValueError):validate_runtime(self.plan,self.spec,'continuous',self.launch,self.rows[:-1])
        self.launch['processes'].pop()
        with self.assertRaises(ValueError):validate_runtime(self.plan,self.spec,'continuous',self.launch,self.rows)

    def test_wrong_backend_device_spec_pid_reuse_and_duplicate_device_refused(self):
        for key,value in [('backend','gloo'),('device_name','CPU'),('start_ticks',999),
                          ('spec_id','bad'),('parent_pid',999),('local_rank',1),('world_size',1),
                          ('device_uuid','fixture-1')]:
            rows=deepcopy(self.rows);rows[0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                validate_runtime(self.plan,self.spec,'continuous',self.launch,rows)

    def test_small_or_model_only_comparison_cannot_certify3b(self):
        good=dict(exact=True,model_tensors=255,optimizer_tensors=765,tensors=1020,rng_ranks=8)
        require_comparison(good)
        for key,value in [('exact',False),('model_tensors',2),('optimizer_tensors',0),('rng_ranks',1)]:
            with self.assertRaises(ValueError):require_comparison(dict(good,**{key:value}))


class HorizonControllerTests(unittest.TestCase):
    """Real prepared small files; process/GPU execution mocked, never acceptance."""
    def setUp(self):
        self.fixture=preparation_fixture.ProbePreparationTests();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.draft['gpu_identity']=[{'fixture_gpu':i} for i in range(8)]
        temp=tempfile.TemporaryDirectory(prefix='horizon-controller-scratch-');self.addCleanup(temp.cleanup)
        self.fixture.draft['tiered_retention']={'temp_root':temp.name}
        self.fixture.write_draft();self.prepared=self.fixture.prepare()
        self.path=Path(self.prepared['plan']);self.root=self.path.parent
        self.plan,_=load_plan(self.path)
        for name,kwargs in [
            ('scripts.resource_admission.inspect',dict(return_value={'gpus':self.fixture.draft['gpu_identity'],
                'memory':{'cache_aware_admission':{'headroom_gib':1024}}})),
            ('scripts.run_horizon_probe.enforce_runtime_space',dict(return_value={'fixture':True})),
            ('scripts.run_horizon_probe.idle_gpu_lease',dict(side_effect=lambda:nullcontext())),
            ('scripts.run_horizon_probe.gpu_pids',dict(return_value=set())),
        ]:
            p=patch(name,**kwargs);p.start();self.addCleanup(p.stop)

    def test_exact_commands_and_expected_native_checkpoint_metadata(self):
        command=command_for(self.path,'continuous')
        self.assertIn('train.horizon_resume_probe',command)
        targets=checkpoint_targets(self.plan,'continuous')
        self.assertEqual(len(targets),2)
        self.assertEqual(targets[0][1]['kind'],'horizon_restored_parent')
        self.assertEqual(targets[1][1]['step'],502)
        self.assertEqual(targets[1][1]['config']['max_steps'],35000)
        self.assertEqual(targets[1][1]['training_contract']['id'],self.plan['child_contract_id'])
        self.assertEqual(checkpoint_targets(self.plan,'baseline'),[])

    def test_four_modes_and_certification_order_then_collect(self):
        called=[]
        def fake(command,directory,env,guard):
            called.append(directory.name)
            self.assertEqual(env['CUDA_VISIBLE_DEVICES'],
                '0,1,2,3,4,5,6,7' if directory.name in ('baseline','continuous','first','resumed') else '')
        with patch('scripts.run_horizon_probe.run_command',side_effect=fake), \
             patch('scripts.run_horizon_probe.check_spec'),patch('scripts.run_horizon_probe.resume_spec') as resume, \
             patch('scripts.run_horizon_probe.collect',return_value={'fixture_only':True}) as collect:
            # Fake collector writes a fixture report only; no actual pass status.
            def finish(path):
                (self.root/'report.json').write_text('{"fixture_only":true}')
                return {'fixture_only':True}
            collect.side_effect=finish
            resume.side_effect=lambda path:(self.root/'resumed.json').write_text('{"fixture_only":true}')
            self.assertEqual(execute(self.path),{'fixture_only':True})
        self.assertEqual(called,list(STAGES));resume.assert_called_once()
        with self.assertRaisesRegex(ValueError,'Prior diagnostic execution'):execute(self.path)

    def test_failed_phase_preserves_evidence_and_never_runs_later_modes(self):
        called=[]
        def fail(command,directory,*args):
            called.append(directory.name)
            (directory/'fixture.log').write_text('failure evidence')
            raise RuntimeError('fixture launcher failure')
        with patch('scripts.run_horizon_probe.run_command',side_effect=fail):
            with self.assertRaisesRegex(RuntimeError,'launcher failure'):execute(self.path)
        self.assertEqual(called,['baseline'])
        self.assertTrue((self.root/'failure.json').exists())
        self.assertFalse((self.root/'.done').exists())
        self.assertEqual((self.root/'commands/baseline/fixture.log').read_text(),'failure evidence')

    def test_guard_enforces_memory_budget(self):
        class Owned:
            fds={1000:1}
            class proc:pid=1000
        def run(command,directory,env,guard):guard(Owned(),0,385*GIB)
        with patch('scripts.run_horizon_probe.run_command',side_effect=run):
            with self.assertRaisesRegex(RuntimeError,'PSS budget'):execute(self.path)
        self.assertFalse((self.root/'.done').exists())

    def test_guard_enforces_total_elapsed_budget(self):
        class Owned:
            fds={1000:1}
            class proc:pid=1000
        def run(command,directory,env,guard):
            with patch('scripts.run_horizon_probe.time.monotonic',return_value=10**15):
                guard(Owned(),0,0)
        with patch('scripts.run_horizon_probe.run_command',side_effect=run):
            with self.assertRaisesRegex(TimeoutError,'time budget'):execute(self.path)
        self.assertFalse((self.root/'.done').exists())

    def test_guard_refuses_foreign_gpu_without_signalling_it(self):
        class Owned:
            fds={1000:1}
            class proc:pid=1000
            def scan(self):return [1000]
        def run(command,directory,env,guard):
            with patch('scripts.run_horizon_probe.gpu_pids',return_value={9999}):
                guard(Owned(),0,0)
        with patch('scripts.run_horizon_probe.run_command',side_effect=run):
            with self.assertRaisesRegex(RuntimeError,'Unexpected GPU owner'):execute(self.path)
        self.assertFalse((self.root/'.done').exists())

    def test_changed_bound_input_rejected_before_execution_record(self):
        (self.fixture.source/'capture.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'Source evidence changed'):execute(self.path)
        self.assertFalse((self.root/'execution_started.json').exists())


if __name__=='__main__':unittest.main()
