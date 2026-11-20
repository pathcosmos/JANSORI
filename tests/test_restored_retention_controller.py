"""Synthetic final target verdict and real filesystem/process guard tests."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from common.data_contracts import resolve,atomic_json,sha256_file
from train.final_resume_probe import file_stats
from scripts.run_restored_retention_audit import (require_result,verify_current,
    owned_workers,reject_previous,validate_output,admission,GIB)


def result_fixture():
    full=dict(model_tensors=255,state_tensors=1020,rng_ranks=8,payload_files=8,full_payload_sha_refreshed=True)
    nfs={f'ckpt_{s:07d}':dict(full) for s in [56000,56500,57000]}
    rows=[dict(root='runs/old',local_root='/tmp/old',training_contract_id='old',nfs={},local={},
               certificates={f'ckpt_{s:07d}':{} for s in range(500,8001,500)},local_namespace_missing=False),
          dict(root='runs/middle',local_root='/tmp/middle',training_contract_id='middle',nfs={},local={},
               certificates={f'ckpt_{s:07d}':{} for s in range(8500,10001,500)},local_namespace_missing=False),
          dict(root='runs/new',local_root='/tmp/new',training_contract_id='child',nfs=nfs,local={},
               certificates={f'ckpt_{s:07d}':{} for s in range(10500,57001,500)},local_namespace_missing=False)]
    expected=[dict(root=root,contract_id=cid,start_step_exclusive=start,through_step=end)
              for root,cid,start,end in [('runs/old','old',0,8000),('runs/middle','middle',8000,10000),('runs/new','child',10000,57000)]]
    segments=[dict(contract_id=r['contract_id'],start_step_exclusive=r['start_step_exclusive'],through_step=r['through_step'],
                   committed_step_rank_pairs=(r['through_step']-r['start_step_exclusive'])*8,
                   committed_tokens=(r['through_step']-r['start_step_exclusive'])*2097152,
                   source_tokens={'fixture_source':(r['through_step']-r['start_step_exclusive'])*2097152},
                   persisted_attempt_tokens_through_prefix=(r['through_step']-r['start_step_exclusive'])*2097152,
                   replayed_tokens_through_prefix=0) for r in expected]
    contract=dict(main_run='runs/new',source_contract_sha256='source',code={'scripts/audit_restored_retention.py':'audit'},inputs={},
                  expected_segments=expected,expected_source_order=['fixture_source'])
    result=dict(status='passed_final_restored_retention_content',production=True,final=True,full_payload_sha_refreshed=True,
        source_run='runs/new',through_step=57000,source_contract_sha256='source',audit_code_sha256='audit',issues=[],residues=[],overlap=[],
        nfs=nfs,local={},nfs_resume_candidates=sorted(nfs),expected_nfs=sorted(nfs),expected_local=[],distinct_checkpoints=3,
        certificate_count=114,branch_inventory=rows,protected_parent_counted_as_normal_history=False,protected_parents=[dict(full),dict(full)],
        metadata_evidence={'runs/new/contract.json':'source'},code={'scripts/audit_restored_retention.py':'audit'},
        training_audit=dict(status='passed_restored_committed_prefix_audit',source_run='runs/new',target_steps=57000,
            validation_source=False,through_step=57000,counts=dict(status='passed_ledger_accounting_only',target_steps=57000,
            through_step=57000,committed_step_rank_pairs=456000,source_tokens={'fixture_source':119537664000},
            persisted_attempt_tokens_through_prefix=119537664000,replayed_tokens_through_prefix=0,
            committed_tokens=119537664000,successful_target_ranks=list(range(8)),segments=segments)))
    return result,contract


class FinalGlobalRetentionControllerTests(unittest.TestCase):
    def test_final_counts_cannot_hide_wrong_identity_boundaries_or_replay(self):
        result,contract=result_fixture()
        mutations=[lambda r:r['training_audit'].update(status='pending'),
                   lambda r:r['training_audit'].update(source_run='runs/foreign'),
                   lambda r:r['training_audit']['counts']['segments'][1].update(start_step_exclusive=7999),
                   lambda r:r['training_audit']['counts']['segments'][2].update(contract_id='middle'),
                   lambda r:r['training_audit']['counts']['segments'][0].update(committed_step_rank_pairs=63999),
                   lambda r:r['training_audit']['counts']['segments'][0]['source_tokens'].update(fixture_source=1),
                   lambda r:r['training_audit']['counts']['segments'][2].update(replayed_tokens_through_prefix=2097152),
                   lambda r:r['training_audit']['counts'].update(persisted_attempt_tokens_through_prefix=119539761152)]
        for mutate in mutations:
            bad=deepcopy(result);mutate(bad)
            with self.assertRaises(ValueError):require_result(bad,contract)
        good=deepcopy(result);counts=good['training_audit']['counts'];counts['segments'][2]['replayed_tokens_through_prefix']=2097152
        counts['segments'][2]['persisted_attempt_tokens_through_prefix']+=2097152
        counts['persisted_attempt_tokens_through_prefix']+=2097152;counts['replayed_tokens_through_prefix']=2097152
        require_result(good,contract)

    def test_actual_scope_counts_and_protected_parent_are_required(self):
        result,contract=result_fixture();require_result(result,contract)
        for key,value in [('through_step',35000),('certificate_count',113),('protected_parent_counted_as_normal_history',True),
                          ('production',False),('full_payload_sha_refreshed',False)]:
            bad=deepcopy(result);bad[key]=value
            with self.assertRaises(ValueError):require_result(bad,contract)
        bad=deepcopy(result);bad['protected_parents'][1]['rng_ranks']=7
        with self.assertRaisesRegex(ValueError,'Full3B'):require_result(bad,contract)
        bad=deepcopy(result);bad['training_audit']['counts']['committed_tokens']-=1
        with self.assertRaisesRegex(ValueError,'target audit'):require_result(bad,contract)

    def test_relabelled_branch_certificate_or_local_final_is_rejected(self):
        result,contract=result_fixture();bad=deepcopy(result)
        bad['branch_inventory'][0]['certificates']['ckpt_0057000']={}
        with self.assertRaisesRegex(ValueError,'Duplicate'):require_result(bad,contract)
        bad=deepcopy(result);bad['local']['ckpt_0057000']=bad['nfs'].pop('ckpt_0057000')
        with self.assertRaisesRegex(ValueError,'coverage'):require_result(bad,contract)
        bad=deepcopy(result);bad['branch_inventory'][2]['nfs']={}
        with self.assertRaisesRegex(ValueError,'inventory'):require_result(bad,contract)

    def test_live_worker_and_failed_attempt_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)',
                'scripts/run_restored_retention_audit.py','--run-dir',str(root),'--worker'])
            try:
                self.assertIn(p.pid,owned_workers(root))
                with self.assertRaisesRegex(RuntimeError,'orphan'):reject_previous(root)
                self.assertIsNone(p.poll())
            finally:p.terminate();p.wait(timeout=5)
            (root/'supervision').mkdir()
            with self.assertRaisesRegex(RuntimeError,'Preserve prior'):reject_previous(root)
            self.assertTrue((root/'supervision').is_dir())

    def test_exact_reserved_output_and_combined_budget_are_reused(self):
        with tempfile.TemporaryDirectory(dir=resolve('runs')) as tmp:
            base=Path(tmp);main=base/'new';main.mkdir();old=base/'old';old.mkdir()
            native=base/'native';native.mkdir();resume=base/'resume';resume.mkdir();root=base/'audit';root.mkdir()
            policy=dict(local_root='/tmp/test-new-history',temp_root='/tmp/test-new-temp')
            mc=dict(tiered_retention=policy,
                retention_branches=[dict(run=str(old),policy=dict(local_root='/tmp/test-old-history',temp_root='/tmp/test-old-temp')),dict(run=str(main),policy=policy)],
                shared_storage=dict(nfs_pools=dict(final_retention=dict(paths=[str(root)],budget_bytes=GIB),
                    final_native=dict(paths=[str(native)],budget_bytes=8*GIB),final_resume=dict(paths=[str(resume)],budget_bytes=GIB))),gpu_identity=['H200']*8,max_pss_gib=384)
            atomic_json(main/'contract.json',mc)
            atomic_json(native/'contract.json',dict(durable_budget_gib=8,host_rss_gib=256))
            atomic_json(resume/'contract.json',dict(durable_budget_gib=1,worker_pss_gib=256))
            c=dict(main_run=str(main),native_run=str(native),resume_run=str(resume),max_pss_gib=16)
            hw=dict(gpus=['H200']*8,memory=dict(cache_aware_admission=dict(headroom_gib=1000)))
            with patch('scripts.resource_admission.inspect',return_value=hw),patch(
                    'scripts.extended_horizon_storage.enforce_shared_space',return_value={'fixture_shared_budget':True}) as shared:
                _,v=admission(root,c);self.assertEqual(v['combined_memory_gib'],912)
                self.assertEqual(v['audit_additional_local_bytes'],0);shared.assert_called_once_with(main,mc)
                hw['memory']['cache_aware_admission']['headroom_gib']=911
                with self.assertRaisesRegex(RuntimeError,'memory'):admission(root,c)
            with self.assertRaisesRegex(ValueError,'reservation'):validate_output(old/'illegal-output',c)
            mc['shared_storage']['nfs_pools']['final_retention']['paths']=[str(old/'illegal-output')];atomic_json(main/'contract.json',mc)
            with self.assertRaisesRegex(ValueError,'overlaps'):validate_output(old/'illegal-output',c)

    def test_posthash_source_mutation_and_namespace_changes_are_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);main=base/'main';main.mkdir();old=base/'old';old.mkdir();middle=base/'middle';middle.mkdir();local=base/'local'
            protected=main/'parent_checkpoint/cp';protected.mkdir(parents=True);(protected/'payload').write_text('full-state')
            original=middle/'parent_checkpoint/cp';original.mkdir(parents=True);(original/'payload').write_text('old-state')
            defs=[dict(run=str(r),policy=dict(local_root=str(local/n))) for r,n in [(old,'old'),(middle,'middle'),(main,'new')]]
            mc=dict(retention_branches=defs,tiered_retention={'local_root':str(local/'new')})
            atomic_json(main/'contract.json',mc);atomic_json(main/'parent_handoff.json',dict(parent_checkpoint=str(protected)))
            atomic_json(middle/'parent_handoff.json',dict(parent_checkpoint=str(original)))
            result=dict(branch_inventory=[dict(root=str(old),local_root=str(local/'old'),nfs={},local={}),
                 dict(root=str(middle),local_root=str(local/'middle'),nfs={},local={}),
                 dict(root=str(main),local_root=str(local/'new'),nfs={},local={})],protected_parents=[{'path':str(original)},{'path':str(protected)}],
                resident_file_stats={str(original):file_stats(original),str(protected):file_stats(protected)},metadata_evidence={str(main/'contract.json'):sha256_file(main/'contract.json')},
                training_audit={'evidence':{}},observed_directories={str(local):None})
            c=dict(main_run=str(main),code={},inputs={})
            with patch('scripts.run_restored_retention_audit.require_result'):
                verify_current(result,c);local.mkdir()
                with self.assertRaisesRegex(ValueError,'namespace changed'):verify_current(result,c)
                local.rmdir();(protected/'payload').write_text('corrupted!')
                with self.assertRaisesRegex(ValueError,'checkpoint changed'):verify_current(result,c)

if __name__=='__main__':unittest.main()
