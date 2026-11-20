"""Actual prefix fixture, synthetic10000 parent, lineage re-audit mocked explicitly.

No GPU, signals or production writes. Exercises new local diagnostic binding.
"""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from common.data_contracts import resolve,sha256_file,identity
from scripts.seal_horizon_handoff import write_once,json_bytes
from scripts.prepare_extended_probe import prepare
from train.final_resume_probe import file_stats
import test_extended_handoff as fixture_module

@unittest.skipUnless(os.environ.get('DENSE_EXTENSION_LINEAGE_FIXTURE'),'Explicit prefix fixture required')
class ExtendedProbePreparationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture_module.ExtendedHandoffIntegrationTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        f=self.fixture;self.root=f.root;self.source=f.source
        self.source.mkdir();self.addCleanup(lambda:shutil.rmtree(self.source))
        for name in ('probe','expected_tokens.json'):
            origin=resolve('runs/3b_full_35k_v1')/name
            if origin.is_dir():shutil.copytree(origin,self.source/name)
            else:shutil.copyfile(origin,self.source/name)
        # Synthetic boundary-counter envelope, not actual10000 acceptance.
        f.meta['step']=10000;old=f.parent;f.parent=old.with_name('ckpt_0010000');old.rename(f.parent)
        f.write(f.parent/'meta.json',f.meta)
        f.proof['step']=10000
        f.proof['files']=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in sorted(f.parent.iterdir())]
        f.proof['bytes']=sum(r['bytes'] for r in f.proof['files']);f.write(f.root/'parent_certificate.json',f.proof)
        f.binding.update(parent_step=10000,parent_metadata_id=identity(f.meta),parent_certificate_id=identity(f.proof),
                         parent_certificate_sha256=sha256_file(f.root/'parent_certificate.json'))
        f.receipt.update(parent_metadata=f.meta,parent_checkpoint=str(f.parent),parent_stats=file_stats(f.parent),execution_binding=f.binding)
        f.write(f.root/'parent_handoff.json',f.receipt)
        manifest={'source_run':str(self.source)}
        mocked=patch('train.extended_handoff.verify_lineage',return_value=(manifest,{'synthetic_lineage_test_only':True}))
        mocked.start();self.addCleanup(mocked.stop)
        self.capture_path=self.root/'capture.json';f.write(self.capture_path,f.receipt['source_capture'])
        self.continuous=self.root.parent/(self.root.name+'-continuous');self.split=self.root.parent/(self.root.name+'-split')
        self.addCleanup(lambda:shutil.rmtree(self.continuous,ignore_errors=True));self.addCleanup(lambda:shutil.rmtree(self.split,ignore_errors=True))
        self.draft={k:v for k,v in f.contract.items() if k in ('code','evidence','shared_storage','expected_checkpoint_bytes','prompts')}
        self.draft.update(purpose='extension_preparation_not_executable',intended_run=str(self.root),source_run=str(self.source),
            framework={'torch':'fixture','cuda':'fixture'},tokenizer='fixture',gpu_identity=[{'synthetic':i} for i in range(8)],
            diagnostic_roots={'continuous':str(self.continuous),'split':str(self.split)},
            tiered_retention={'temp_root':f.contract['shared_storage']['local_temp_paths'][0]})
        self.draft['evidence']={}
        self.draft_path=self.root/'draft.json';f.write(self.draft_path,self.draft)
        self.receipt=f.receipt

    def prepare(self):
        prepare(self.root,self.draft_path,self.capture_path)
        return dict(plan=str(self.root/'horizon_probe/plan.json'))

    def test_three_modes_admit_with_local_payload_and_one_nfs_parent(self):
        from train.extended_resume_probe import check_spec
        plan=json.loads(Path(self.prepare()['plan']).read_text());spec=dict(plan=plan,plan_id=identity(plan))
        for mode in ('baseline','continuous','first'):
            result=check_spec(spec,mode)
            self.assertEqual(result[-2],Path(self.receipt['parent_checkpoint']))
            self.assertTrue(Path(result[3]['out_dir']).is_relative_to(Path(plan['diagnostic_temp_root'])))
        self.assertFalse((self.continuous/'parent_checkpoint').exists())
        self.assertFalse((self.split/'parent_checkpoint').exists())
        self.assertFalse((self.root/'horizon_probe/resumed.json').exists())

    def test_repeated_prepare_preserves_previous_outputs(self):
        self.prepare();before=sha256_file(self.root/'horizon_probe/plan.json')
        with self.assertRaisesRegex(ValueError,'Preserve existing'):self.prepare()
        self.assertEqual(before,sha256_file(self.root/'horizon_probe/plan.json'))

    def test_stale_draft_evidence_rejected_before_diagnostic_namespace(self):
        self.draft['evidence']={str(self.capture_path):'0'*64};self.fixture.write(self.draft_path,self.draft)
        with self.assertRaises(ValueError):self.prepare()
        self.assertFalse(self.continuous.exists())
