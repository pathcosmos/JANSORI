"""Actual small CPU ownership/hash checks; synthetic8GPU provenance admission."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from common.data_contracts import resolve,atomic_json,sha256_file,identity
from scripts.run_final_horizon_resume import (GIB,admission,fresh_inputs,verify_runtime,
    supervise_command,owned_processes,reject_previous_execution,materialize_spec,require_comparison)


class FinalHorizonControllerTests(unittest.TestCase):
    def test_real_cpu_failed_process_preserves_partial_and_unrelated_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            other=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
            try:
                code="from pathlib import Path;import sys;Path(sys.argv[1]).write_text('partial');sys.exit(7)"
                with self.assertRaisesRegex(RuntimeError,'exited 7'):
                    supervise_command([sys.executable,'-c',code,str(root/'partial')],root=root,
                        env=os.environ.copy(),guard=lambda *a:None,interval=.05)
                self.assertEqual((root/'partial').read_text(),'partial')
                self.assertTrue((root/'supervision/failure.json').exists())
                self.assertIsNone(other.poll());self.assertFalse((root/'.done').exists())
            finally:other.terminate();other.wait(timeout=5)

    def test_actual_budget_stop_cleans_separate_session_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            code="""import subprocess,sys,time
from pathlib import Path
p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
Path(sys.argv[1]).write_text(str(p.pid));time.sleep(30)
"""
            def guard(pid,elapsed,peak):
                if elapsed>.3:raise RuntimeError('fixture budget exceeded')
            with self.assertRaisesRegex(RuntimeError,'fixture budget'):
                supervise_command([sys.executable,'-c',code,str(root/'child.pid')],root=root,
                    env=os.environ.copy(),guard=guard,interval=.05)
            child=int((root/'child.pid').read_text());stat=Path(f'/proc/{child}/stat')
            self.assertTrue(not stat.exists() or stat.read_text().rsplit(')',1)[1].split()[0]=='Z')
            self.assertTrue((root/'supervision/failure.json').exists())

    def test_live_exact_diagnostic_is_not_restarted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)',
                'train.final_horizon_resume_probe','--spec',str(root/'spec.json')])
            try:
                self.assertIn(process.pid,owned_processes(root))
                with self.assertRaisesRegex(RuntimeError,'actual diagnostic process'):reject_previous_execution(root)
                self.assertIsNone(process.poll())
            finally:process.terminate();process.wait(timeout=5)

    def test_exact_shared_storage_budget_and_memory_caps(self):
        with tempfile.TemporaryDirectory(dir=resolve('runs')) as nf, tempfile.TemporaryDirectory() as lf:
            base=Path(nf);main=base/'main';main.mkdir();root=base/'resume';root.mkdir();native=base/'native';native.mkdir()
            temp=Path(lf)/'temp';temp.mkdir();scratch=temp/root.name
            plan=dict(main_run=str(main),native_run=str(native),scratch=str(scratch),durable_budget_gib=1,
                local_budget_gib=192,max_pss_gib=256,reserved_main_temp_gib=1024)
            mc=dict(tiered_retention=dict(temp_root=str(temp)),max_pss_gib=384,gpu_identity=['H200']*8,
                space_reservations={'nfs':{str(root):GIB,str(native):8*GIB}})
            atomic_json(main/'contract.json',mc)
            atomic_json(native/'contract.json',dict(main_run=str(main),final_step=35000,
                host_rss_gib=256,durable_budget_gib=8,local_budget_gib=8))
            hw=dict(gpus=['H200']*8,memory=dict(cache_aware_admission=dict(headroom_gib=1000)),
                storage=dict(scratch_parent=dict(device=temp.parent.stat().st_dev)))
            with patch('scripts.resource_admission.inspect',return_value=hw),patch(
                    'scripts.horizon_storage_plan.enforce_runtime_space',return_value={'local_temporary_used_bytes':800*GIB}) as shared:
                _,budget=admission(root,plan)
                self.assertEqual(budget['projected_temporary_bytes'],1000*GIB)
                self.assertEqual(budget['combined_memory_gib'],896)
                shared.assert_called_once_with(main,mc)
                shared.return_value={'local_temporary_used_bytes':900*GIB}
                with self.assertRaisesRegex(RuntimeError,'shared temporary'):admission(root,plan)
                hw['memory']['cache_aware_admission']['headroom_gib']=895
                with self.assertRaisesRegex(RuntimeError,'memory'):admission(root,plan)
                hw['memory']['cache_aware_admission']['headroom_gib']=1000
                mc['space_reservations']['nfs'].pop(str(root));atomic_json(main/'contract.json',mc)
                with self.assertRaisesRegex(ValueError,'reservation'):admission(root,plan)

    def test_real_small_input_fresh_hash_rebuilds_horizon_identity(self):
        from train.parallel_contract import training_contract
        from train.runtime import RESUME_KEYS
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'probe').mkdir();token=root/'tokens.bin';token.write_bytes(b'01234567')
            atomic_json(root/'mix.json',dict(sources={'ko':{'files':[str(token)]}}))
            atomic_json(root/'probe/probe.json',{'tiny':'probe'});(root/'prompts.jsonl').write_text('{}\n')
            config={k:1 for k in RESUME_KEYS};config.update(max_steps=35000,mix=str(root/'mix.json'),holdout=[])
            atomic_json(root/'main.json',config)
            mc=dict(prompts=str(root/'prompts.jsonl'),generation_steps=[35000],horizon_execution={'fixture':'binding'})
            atomic_json(root/'contract.json',mc)
            parent=training_contract(config,8,[],workers=2);parent.pop('id')
            parent.update(monitored_evaluation=dict(probe_sha256=sha256_file(root/'probe/probe.json'),
                prompts_sha256=sha256_file(root/'prompts.jsonl'),generation_steps=[35000],
                code_sha256=sha256_file(resolve('train/monitored_horizon.py')),
                source_hooks_sha256=sha256_file(resolve('train/rehearsal.py'))),horizon_transition=mc['horizon_execution'])
            parent['id']=identity(parent);atomic_json(root/'parent.json',parent)
            plan=dict(main_run=str(root),parent_contract=str(root/'parent.json'),parent_config=str(root/'main.json'))
            stats=fresh_inputs(plan,root/'hash.json');self.assertEqual(set(stats),{str(token)})
            bad=deepcopy(parent);bad['horizon_transition']={'wrong':'identity'};bad.pop('id');bad['id']=identity(bad)
            atomic_json(root/'parent.json',bad)
            with self.assertRaisesRegex(ValueError,'Fresh35k'):fresh_inputs(plan,root/'hash.json')
            atomic_json(root/'parent.json',parent);token.write_bytes(b'changed!')
            with self.assertRaisesRegex(ValueError,'Fresh training input'):fresh_inputs(plan,root/'hash.json')

    def test_failed_final_audit_cannot_materialize_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch('scripts.run_final_horizon_resume.completed_main',side_effect=ValueError('Missing final coverage')):
                with self.assertRaisesRegex(ValueError,'Missing final'):materialize_spec(root,{'main_run':'fixture'})
            self.assertEqual(list(root.iterdir()),[])
        good=dict(exact=True,tensors=1020,model_tensors=255,optimizer_tensors=765,rng_ranks=8)
        require_comparison(good)
        for key in good:
            bad=dict(good);bad[key]=False if key=='exact' else 0
            with self.assertRaisesRegex(ValueError,'incomplete'):require_comparison(bad)

    def test_worker_requires_own_identity_and_audits_before_gpu_lock(self):
        import torch
        from scripts.run_final_horizon_resume import worker
        from scripts.horizon_probe_process import OWNER_ENV
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);owner='a'*32
            plan=dict(framework={'torch':str(torch.__version__),'cuda':torch.version.cuda},scratch=str(root/'scratch'))
            atomic_json(root/'contract.json',dict(code={},inputs={}))
            atomic_json(root/'execution_started.json',{'plan_id':identity(plan)})
            ticks=int(Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(')',1)[1].split()[19])
            handle=dict(pid=os.getpid(),process={'start_ticks':ticks},owner=owner)
            atomic_json(root/'supervision/handle.json',handle)
            with patch.dict(os.environ,{OWNER_ENV:owner}),patch('scripts.run_final_horizon_resume.load_plan',return_value=plan),patch(
                    'scripts.run_final_horizon_resume.admission',return_value=({},{})),patch(
                    'scripts.run_final_horizon_resume.materialize_spec',side_effect=ValueError('Final audit failed')) as materialize,patch(
                    'scripts.run_final_horizon_resume.acquire_idle_gpu') as gpu:
                with self.assertRaisesRegex(ValueError,'Final audit failed'):worker(root)
                materialize.assert_called_once();gpu.assert_not_called()
                self.assertFalse((root/'scratch').exists())
                atomic_json(root/'supervision/handle.json',dict(handle,pid=handle['pid']+1))
                with self.assertRaisesRegex(ValueError,'execution identity'):worker(root)
                self.assertEqual(materialize.call_count,1)



class RuntimeReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.plan=dict(framework={'torch':'fixture','cuda':'fixture'});self.spec={'plan':self.plan,'plan_id':identity(self.plan)}
        self.owner='a'*32;self.events=[dict(event='process_bound',pid=100,start_ticks=1000)]
        script=str(resolve('scripts/run_final_horizon_resume.py'))
        atomic_json(self.root/'supervision/handle.json',dict(pid=100,owner=self.owner,
            process={'pid':100,'start_ticks':1000},command=['python',script,'--run-dir',str(self.root),'--worker']))
        for i,mode in enumerate(['continuous','first','resumed']):
            pid=200+i;stamp=2000+i;self.events.append(dict(event='process_bound',pid=pid,start_ticks=stamp))
            cmd=[sys.executable,'-m','torch.distributed.run','--standalone','--nproc_per_node=8','--module',
                 'train.final_horizon_resume_probe','--spec',str(self.root/'spec.json'),'--mode',mode]
            process=dict(pid=pid,start_ticks=stamp,argv=cmd,ppid=100)
            atomic_json(self.root/'attempts'/(mode+'.handle.json'),dict(process=process,command=cmd,owner=self.owner))
            atomic_json(self.root/'attempts'/(mode+'.result.json'),dict(process=process,exit_code=0,owner=self.owner))
            for rank in range(8):
                pid=300+i*8+rank;stamp=3000+i*8+rank;self.events.append(dict(event='process_bound',pid=pid,start_ticks=stamp))
                atomic_json(self.root/'runtime'/f'{mode}-rank{rank}.json',dict(owner=self.owner,plan_id=identity(self.plan),
                    spec_id=identity(self.spec),mode=mode,rank=rank,local_rank=rank,world_size=8,backend='nccl',
                    framework=self.plan['framework'],pid=pid,start_ticks=stamp,parent_pid=200+i,
                    device_name='H200',device_uuid=f'fixture-device-{rank}',device_total_memory=140*GIB,
                    diagnostic_only=True,actual8gpu_acceptance=False))
        self.events_path=self.root/'supervision/events.jsonl'
        self.events_path.write_text(''.join(json.dumps(r)+'\n' for r in self.events))

    def test_complete_synthetic_receipts_and_missing_rank(self):
        self.assertEqual(len(verify_runtime(self.root,self.spec)),32)
        (self.root/'runtime/resumed-rank7.json').unlink()
        with self.assertRaisesRegex(ValueError,'24rank'):verify_runtime(self.root,self.spec)

    def test_wrong_backend_owner_or_unpinned_identity_rejected(self):
        p=self.root/'runtime/resumed-rank7.json';original=json.loads(p.read_text())
        for key,value in [('backend','gloo'),('owner','b'*32),('parent_pid',999),('start_ticks',1),('spec_id','wrong')]:
            atomic_json(p,dict(original,**{key:value}))
            with self.assertRaisesRegex(ValueError,'provenance'):verify_runtime(self.root,self.spec)
        atomic_json(p,original);self.events_path.write_text(''.join(json.dumps(r)+'\n' for r in self.events[:-1]))
        with self.assertRaisesRegex(ValueError,'provenance'):verify_runtime(self.root,self.spec)

    def test_nonzero_launcher_and_different_devices_rejected(self):
        p=self.root/'attempts/first.result.json';original=json.loads(p.read_text());atomic_json(p,dict(original,exit_code=1))
        with self.assertRaisesRegex(ValueError,'launcher'):verify_runtime(self.root,self.spec)
        atomic_json(p,original);p=self.root/'runtime/resumed-rank7.json';row=json.loads(p.read_text());row['device_uuid']='different'
        atomic_json(p,row)
        with self.assertRaisesRegex(ValueError,'Device identity'):verify_runtime(self.root,self.spec)

    def collector_fixture(self):
        from train.final_horizon_resume_probe import metadata_for,file_stats,diagnostic_lr
        from scripts.verify_final_resume_probe import MODES
        root=self.root;scratch=root/'scratch';scratch.mkdir();parent=root/'parent';parent.mkdir()
        (parent/'payload').write_text('preserved')
        self.plan.update(scratch=str(scratch),parent_step=35000,world_size=8,parent_training_contract_id='child',
            parent_config='fixture-config',inputs={'fixture-config':'a'*64},lr_policy='freeze_last_parent_update')
        self.spec.update(plan_id=identity(self.plan),parent_checkpoint=str(parent),parent_stats=file_stats(parent),
            parent_proof=str(root/'parent-proof.json'))
        atomic_json(root/'spec.json',self.spec)
        for p in (root/'runtime').glob('*.json'):
            row=json.loads(p.read_text());row.update(plan_id=identity(self.plan),spec_id=identity(self.spec));atomic_json(p,row)
        config=dict(max_steps=35000,warmup=1000,lr=.0003,min_lr_ratio=.1,decay_frac=.15,seq_len=4096,global_tokens=2097152)
        for mode,(begin,end) in MODES.items():
            for rank in range(8):
                steps=list(range(35000+begin+1,35000+end+1))
                atomic_json(root/'ledgers'/f'{mode}-rank{rank}.json',dict(mode=mode,rank=rank,success=True,
                    plan_id=identity(self.plan),parent_step=35000,diagnostic_only=True,
                    steps=[dict(step=n,source_sequences={'ko':64}) for n in steps],
                    metrics=[dict(step=n,loss=2.,gnorm=1.,lr=diagnostic_lr(config)) for n in steps]))
        for name,offset in [('restored_parent',0),('continuous_final',2),('split_first',1),('resumed_final',2)]:
            cp=scratch/name;cp.mkdir();meta=metadata_for(self.spec,offset);atomic_json(cp/'meta.json',meta)
            (cp/'payload').write_text('synthetic-no-GPU')
            files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in sorted(cp.iterdir())]
            atomic_json(root/'certificates'/(name+'.json'),dict(path=str(cp),metadata=meta,files=files,
                stats=file_stats(cp),bytes=sum(r['bytes'] for r in files)))
        for name in ['roundtrip_comparison','resume_comparison']:
            atomic_json(root/(name+'.json'),dict(exact=True,tensors=1020,model_tensors=255,optimizer_tensors=765,rng_ranks=8))
        for name in ['final_training_audit','fresh_input_hash','execution_timing','parent-proof']:
            atomic_json(root/(name+'.json'),{'fixture':True})
        return config

    def test_collector_combines24rank_evidence_excluding_diagnostic_tokens(self):
        from scripts.run_final_horizon_resume import collect_result
        config=self.collector_fixture()
        with patch('scripts.run_final_horizon_resume.check_spec',return_value=(self.plan,config,{'source_order':['ko']})):
            result=collect_result(self.root,self.spec)
            self.assertEqual(result['parent_step'],35000)
            self.assertEqual(result['counts']['main_committed_tokens_added'],0)
            self.assertEqual(result['counts']['actual_diagnostic_committed_tokens'],8388608)
            self.assertGreater(len(result['evidence']),60)
            path=self.root/'ledgers/resumed-rank7.json';row=json.loads(path.read_text());row['success']=False
            atomic_json(path,row)
            with self.assertRaisesRegex(ValueError,'completion identity'):collect_result(self.root,self.spec)

    def test_collector_rejects_changed_snapshot_and_checkpoint_residue(self):
        from scripts.run_final_horizon_resume import collect_result
        config=self.collector_fixture()
        with patch('scripts.run_final_horizon_resume.check_spec',return_value=(self.plan,config,{'source_order':['ko']})):
            original=verify_runtime
            def mutate(root,spec,**kwargs):
                result=original(root,spec,**kwargs)
                path=root/'ledgers/first-rank0.json';path.write_text(path.read_text()+' ')
                return result
            with patch('scripts.run_final_horizon_resume.verify_runtime',side_effect=mutate):
                with self.assertRaisesRegex(ValueError,'snapshot changed'):collect_result(self.root,self.spec)
            (self.root/'scratch/resumed_final/foreign').write_text('unmanaged')
            with self.assertRaisesRegex(ValueError,'inventory'):collect_result(self.root,self.spec)

if __name__=='__main__':unittest.main()
