"""CPU orchestration/coverage tests, not actual final3B evaluation acceptance."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from common.data_contracts import atomic_json,sha256_file
from scripts.evaluate_restored_native import main_observation,reject_live_orphan,completed_main,verify_result,admission


class FinalMainEvaluationTest(unittest.TestCase):
    def test_contract_binds_actual_external_authorization_paths(self):
        from scripts.evaluate_restored_native import make_contract
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);main=root/'main';main.mkdir();reference=root/'reference';reference.mkdir()
            tokenizer=root/'tokenizer.json';tokenizer.write_text('{}')
            approval=root/'boundary_approval.json';revision=root/'retention_revision.json'
            atomic_json(approval,dict(authorized=True));atomic_json(revision,dict(authorized=True))
            cfg=dict(model='3b',max_steps=57000);atomic_json(main/'main.json',cfg)
            mc=dict(configurations={'main.json':sha256_file(main/'main.json')},code={},
                evidence={str(approval):sha256_file(approval)},tokenizer=str(tokenizer),framework={},
                retention_authorization=str(approval),retention_revision=dict(authorization=str(revision)))
            atomic_json(main/'contract.json',mc)
            result=dict(contract=dict(tokenizer_sha256=sha256_file(tokenizer)),
                languages={k:dict(valid_tokens=40) for k in ['ko','en']})
            atomic_json(reference/'result.json',result)
            rc=dict(code={},inputs={},evaluation={},samples='fixture',holdout_manifest='fixture')
            ref=dict(result='result.json',result_sha256=sha256_file(reference/'result.json'))
            for name,data in [('contract.json',rc),('report.json',ref),('.done',{})]:atomic_json(reference/name,data)
            with patch('scripts.supervise_extended_training.require_authorization') as auth,patch(
                    'train.extended_handoff.validate_configuration'),patch('train.extended_handoff.load_handoff'),patch(
                    'scripts.evaluate_restored_native.certified',return_value=(ref,rc)):
                c=make_contract(main,reference)
            auth.assert_called_once_with(mc)
            self.assertFalse((main/'authorization.json').exists())
            self.assertEqual(c['inputs'][str(approval)],sha256_file(approval))
            self.assertEqual(c['inputs'][str(revision)],sha256_file(revision))
            self.assertEqual(c['final_step'],57000)
            self.assertEqual(c['late_start_step'],56500)

    def fixture(self,root):
        from scripts.evaluate_restored_native import GIB
        main=root/'main';main.mkdir();output=root/'native';scratch=root/'scratch';scratch.mkdir()
        mc=dict(tiered_retention=dict(temp_root=str(scratch)),gpu_identity=['fixture']*8,max_pss_gib=384,
            shared_storage=dict(nfs_pools=dict(final_native=dict(paths=[str(output)],budget_bytes=8*GIB)),
                                local_temp_paths=[str(scratch)]))
        atomic_json(main/'contract.json',mc)
        c=dict(main_run=str(main),host_rss_gib=256,durable_budget_gib=8,local_budget_gib=8)
        hw=dict(gpus=['fixture']*8,memory=dict(cache_aware_admission=dict(headroom_gib=700)),
                storage=dict(scratch_parent=dict(device=scratch.parent.stat().st_dev)))
        return main,output,scratch,mc,c,hw

    def test_deferred_budget_and_wrong_namespace_rejected_before_setup(self):
        from argparse import Namespace
        from scripts.evaluate_restored_native import main as setup
        with tempfile.TemporaryDirectory() as tmp:
            main,output,scratch,mc,c,hw=self.fixture(Path(tmp))
            mc['shared_storage']['nfs_pools']['final_native']['budget_bytes']=16*2**20
            atomic_json(main/'contract.json',mc)
            with patch('scripts.evaluate_restored_native.make_contract',return_value=c),patch(
                    'scripts.evaluate_restored_native.resolve',side_effect=lambda p:Path(tmp) if p=='runs' else Path(p)),patch(
                    'scripts.resource_admission.inspect',return_value=hw):
                with self.assertRaisesRegex(ValueError,'must be restored'):
                    setup(Namespace(worker=False,run_dir=str(output),main_run=str(main),reference_run='unused',prepare=True))
            self.assertFalse(output.exists())
            mc['shared_storage']['nfs_pools']['final_native']['budget_bytes']=8*2**30
            mc['shared_storage']['nfs_pools']['final_native']['paths']=[str(output)+'_other']
            atomic_json(main/'contract.json',mc)
            with self.assertRaisesRegex(ValueError,'exact8GiB'):admission(c,output)

    def test_shared_pool_reuse_and_memory_temp_limits(self):
        from scripts.evaluate_restored_native import GIB
        with tempfile.TemporaryDirectory() as tmp:
            main,output,scratch,mc,c,hw=self.fixture(Path(tmp))
            with patch('scripts.resource_admission.inspect',return_value=hw),patch(
                    'scripts.extended_horizon_storage.enforce_shared_space',return_value={'local_temporary_used_bytes':1000*GIB}) as shared:
                _,actual=admission(c,output)
                self.assertEqual(actual,scratch/'final_native')
                shared.assert_called_once_with(main,mc)
                shared.return_value={'local_temporary_used_bytes':1020*GIB}
                with self.assertRaisesRegex(RuntimeError,'shared temporary'):admission(c,output)
                hw['memory']['cache_aware_admission']['headroom_gib']=600
                with self.assertRaisesRegex(RuntimeError,'future peak headroom'):admission(c,output)
                hw['memory']['cache_aware_admission']['headroom_gib']=700
                mc['shared_storage']['local_temp_paths']=[];atomic_json(main/'contract.json',mc)
                with self.assertRaisesRegex(ValueError,'scratch absent'):admission(c,output)

    def test_old35k_producer_is_not_57k_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)',
                'scripts/supervise_horizon_training.py','torch.distributed.run','--module',
                'train.monitored_horizon','--run-dir',str(root)])
            try:
                (root/'pid').write_text(str(process.pid))
                atomic_json(root/'state.json',dict(phase='running',worker_pid=process.pid))
                with self.assertRaisesRegex(RuntimeError,'handle missing'):main_observation(root)
                self.assertIsNone(process.poll())
            finally:
                process.terminate();process.wait(timeout=5)

    def test_late_trigger_requires_actual_main_handles_and_errors_are_not_restarts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'training').mkdir()
            process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)',
                'scripts/supervise_extended_training.py','torch.distributed.run',
                '--module','train.monitored_extended_horizon','--run-dir',str(root)])
            try:
                (root/'pid').write_text(str(process.pid))
                atomic_json(root/'state.json',dict(phase='running',worker_pid=process.pid))
                log=root/'training/log.jsonl'
                log.write_text(json.dumps(dict(step=56000,loss=3.))+'\n')
                self.assertFalse(main_observation(root)['ready'])
                log.write_text(json.dumps(dict(step=56500,loss=3.))+'\n{"step":')
                self.assertTrue(main_observation(root)['ready'])
                atomic_json(root/'state.json',dict(phase='error'))
                with self.assertRaisesRegex(RuntimeError,'requires recovery'):main_observation(root)
                self.assertIsNone(process.poll())
            finally:
                process.terminate();process.wait(timeout=5)
            atomic_json(root/'state.json',dict(phase='running',worker_pid=process.pid))
            with self.assertRaisesRegex(RuntimeError,'handle missing'):main_observation(root)

    def test_complete_marker_does_not_replace_full_target_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'.done').write_text('{}')
            self.assertTrue(main_observation(root)['ready'])
            with patch('scripts.audit_restored_training.audit_run',return_value=dict(validation_source=False,
                    counts=dict(committed_tokens=2097152,successful_target_ranks=list(range(8))))):
                with self.assertRaisesRegex(ValueError,'full8rank'):completed_main(root)
            with patch('scripts.audit_restored_training.audit_run',return_value=dict(validation_source=False,
                    counts=dict(committed_tokens=119537664000,successful_target_ranks=list(range(7))))):
                with self.assertRaisesRegex(ValueError,'full8rank'):completed_main(root)

    def test_orphan_worker_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)',
                                     'evaluate_restored_native.py','--worker',str(root)])
            try:
                atomic_json(root/'worker_state.json',dict(pid=process.pid))
                with self.assertRaisesRegex(RuntimeError,'still live'):reject_live_orphan(root)
                self.assertIsNone(process.poll())
            finally:
                process.terminate();process.wait(timeout=5)
            reject_live_orphan(root)

    def test_actual_tiny_native_provenance_full_targets_content_ppl_and_decode(self):
        from test_eval_runner import fixture as checkpoint_fixture
        from test_split_inputs import fixture as source_fixture
        from data.local_split_reader import prepare_reader
        from eval.prepare_holdout import local_verified_inputs
        from data.split_inputs import prepare
        from eval.run import run,records
        from tokenizers import Tokenizer
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sources,split=source_fixture(root)
            reader=prepare_reader(split,root/'reader',scratch=root/'scratch')
            checkpoint,tok,_,prompts=checkpoint_fixture(root)
            meta=json.loads((checkpoint/'meta.json').read_text());meta['step']=57000
            atomic_json(checkpoint/'meta.json',meta)
            prompts.write_text('\n'.join(json.dumps(dict(id=str(i),category='ko' if i<3 else 'en',text='안녕' if i<3 else 'hello')) for i in range(6)))
            with local_verified_inputs(sources,split,reader,cache_mib=64) as inputs:
                prepare(sources,split,root/'holdout',dict(ko=40,en=40),mode='eval',categories=dict(ko='ko',en='en'),verified_inputs=inputs)
                result=run(checkpoint,tok,root/'holdout/samples.jsonl',prompts,root/'eval.json',
                    seq_len=8,stride=4,max_new_tokens=2,holdout_manifest=root/'holdout/manifest.json',verified_inputs=inputs)
                inputs.selection.db.execute('SELECT 1')
            tokenizer=Tokenizer.from_file(str(tok))
            targets={k:sum(len(tokenizer.encode(r['text'],add_special_tokens=False).ids) for r in records(root/'holdout/samples.jsonl') if r['category']==k) for k in ['ko','en']}
            proof=dict(files=[dict(path=str(p.relative_to(checkpoint)),sha256=sha256_file(p)) for p in checkpoint.rglob('*') if p.is_file()])
            reference=copy.deepcopy(result)
            def verify(r,proof=proof):
                verify_result(r,proof,tokenizer,targets,reference,expected_step=57000,expected_device='cpu')
            verify(result)
            bad=copy.deepcopy(result);bad['languages']['ko']['valid_tokens']-=1
            with self.assertRaisesRegex(ValueError,'target coverage'):verify(bad)
            bad=copy.deepcopy(result);bad['aggregate']['mean_nll']+=.1
            with self.assertRaisesRegex(ValueError,'weighted NLL'):verify(bad)
            bad=copy.deepcopy(result);bad['languages']['en']['perplexity']+=1
            with self.assertRaisesRegex(ValueError,'PPL differs'):verify(bad)
            bad=copy.deepcopy(result);bad['generations'][0]['generated_text']='changed'
            with self.assertRaisesRegex(ValueError,'decode differs'):verify(bad)
            bad=copy.deepcopy(result);bad['generations'][0]['stopped_at_eos']=not bad['generations'][0]['stopped_at_eos']
            with self.assertRaisesRegex(ValueError,'EOS/decode'):verify(bad)
            bad=copy.deepcopy(result);bad['generations'][0]['prompt_ids']=[0]
            with self.assertRaisesRegex(ValueError,'prompt token IDs'):verify(bad)
            bad=copy.deepcopy(result);bad['checkpoint_step']=5000
            with self.assertRaisesRegex(ValueError,'step differs'):verify(bad)
            badproof=copy.deepcopy(proof);badproof['files'][0]['sha256']='0'*64
            with self.assertRaisesRegex(ValueError,'checkpoint content'):verify(result,badproof)
            bad=copy.deepcopy(result);bad['generations'].pop()
            with self.assertRaisesRegex(ValueError,'Fixed6'):verify(bad)


if __name__=='__main__':
    unittest.main()
