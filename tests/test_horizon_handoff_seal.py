import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from common.data_contracts import sha256_file, resolve, artifact_path
from scripts.protect_horizon_parent import protect_parent, GIB
from scripts.seal_horizon_handoff import prepare_lineage, seal_handoff
from train.monitored_horizon import load_handoff, parent_resume
import test_horizon_worker as worker_fixture


class HandoffSealTests(unittest.TestCase):
    """Real small filesystem copies; original-run auditor is synthetic here.

    Actual original3B audit is exercised separately by the read-only preflight.
    These tests do not claim real distributed execution or a3B payload restore.
    """
    def setUp(self):
        self.fixture = worker_fixture.HorizonWorkerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root
        self.temp = tempfile.TemporaryDirectory(prefix='handoff-source-fixture-', dir=resolve('runs'))
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        (self.source/'ledgers').mkdir()
        old_lineage = self.root/'lineage'
        counts = json.loads((old_lineage/'audit.json').read_text())['counts']
        for rank in range(8):
            entry = json.loads((old_lineage/f'rank{rank}.json').read_text())[0]
            (self.source/'ledgers'/f'fixture-rank{rank}.json').write_text(json.dumps(entry['record']))
        shutil.rmtree(old_lineage)  # This test's private synthetic namespace only.
        (self.root/'parent_handoff.json').unlink()
        (self.root/'parent_certificate.json').unlink()
        parent = self.root/'parent_checkpoint/ckpt_0000002'
        parent.rmdir()  # Empty synthetic worker-schema fixture, not a checkpoint.
        checkpoint = self.source/'training/ckpt_0000002'; checkpoint.mkdir(parents=True)
        (checkpoint/'meta.json').write_text(json.dumps(self.fixture.meta))
        for name in ['.complete','.metadata','__0_0.distcp',*[f'rng_rank{r}.pt' for r in range(8)]]:
            (checkpoint/name).write_bytes(('synthetic-'+name).encode())
        (self.source/'training/log.jsonl').write_text(''.join(json.dumps(dict(step=s,tok_s=10))+'\n' for s in [1,2,3]))
        (self.source/'main.json').write_text(json.dumps(dict(out_dir=str(self.source/'training'))))
        files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in sorted(checkpoint.iterdir())]
        self.proof=dict(step=2,training_contract_id=self.fixture.meta['training_contract']['id'],
                        files=files,bytes=sum(r['bytes'] for r in files),checkpoint=str(checkpoint))
        (self.source/'checkpoint_certificates').mkdir()
        (self.source/'checkpoint_certificates/ckpt_0000002.json').write_text(json.dumps(self.proof))
        self.owner = 'c'*32
        protect_parent(checkpoint,self.root,self.proof,owner_id=self.owner,required_free_after_bytes=200*GIB)
        self.audit=dict(counts=counts,trace={'fixture_only':True},code={},through_step=2,
            source_run=str(self.source),evidence={artifact_path(p):sha256_file(p) for p in
                [self.source/'training/log.jsonl', *sorted((self.source/'ledgers').glob('*.json'))]})
        self.template = self.root/'lineage_template'
        self.mock = patch('scripts.seal_horizon_handoff.audit_run',return_value=self.audit)
        self.mock.start(); self.addCleanup(self.mock.stop)
        prepare_lineage(self.source,2,self.template)
        self.capture = dict(status='observed_live_not_stopped',run=str(self.source),
                            processes=copy.deepcopy(self.fixture.receipt['stopped_parent_processes']))
        self.capture_path = self.source/'captured.json'
        self.capture_path.write_text(json.dumps(self.capture))

    def seal(self, **kwargs):
        return seal_handoff(self.source,self.root,self.capture_path,self.template,owner_id=self.owner,**kwargs)

    def test_seal_retry_and_worker_diagnostic_admission(self):
        result = self.seal()
        self.assertEqual(result,self.seal())
        self.assertEqual(result['status'],'sealed_parent_handoff')
        self.assertFalse(result['actual8gpu_resume_verified'])
        self.assertEqual(result['unsaved_progress']['discarded_updates_at_least'],1)
        self.assertIsNone(result['unsaved_progress']['exact_discarded_updates'])
        contract = copy.deepcopy(self.fixture.contract)
        contract['horizon_execution'] = result['execution_binding']
        contract['code'].update(result['execution_binding']['code'])
        contract['evidence']={str(self.root/'parent_handoff.json'):sha256_file(self.root/'parent_handoff.json')}
        receipt,candidate,proof=load_handoff(self.root,contract,self.fixture.config)
        self.assertEqual(candidate['id'],result['candidate_contract_id'])
        hook=parent_resume(receipt,candidate,proof,self.fixture.config,contract)
        chosen=hook.checkpoints(self.root/'training')
        self.assertEqual(chosen,[Path(receipt['parent_checkpoint'])])
        hook.validate(receipt['parent_metadata'],candidate)
        broken=copy.deepcopy(receipt);broken.pop('parent_stats')
        with self.assertRaisesRegex(ValueError,'file-stat snapshot'):
            parent_resume(broken,candidate,proof,self.fixture.config,contract)

    def test_live_captured_process_rejected_before_publication(self):
        stat=Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(')',1)[1].split()
        self.capture['processes']['worker']=dict(pid=os.getpid(),start_ticks=int(stat[19]))
        self.capture_path.write_text(json.dumps(self.capture))
        with self.assertRaisesRegex(ValueError,'still present'):
            self.seal()
        self.assertFalse((self.root/'parent_handoff.json').exists())

    def test_interrupted_lineage_publication_resumes_same_transaction(self):
        def fault(point):
            if point=='lineage_copied':raise InterruptedError('synthetic interruption')
        with self.assertRaises(InterruptedError):self.seal(fault=fault)
        self.assertFalse((self.root/'parent_handoff.json').exists())
        self.assertEqual(self.seal()['status'],'sealed_parent_handoff')

    def test_foreign_residue_is_preserved(self):
        lineage=self.root/'lineage';lineage.mkdir()
        (lineage/'audit.json').write_text('foreign')
        with self.assertRaisesRegex(ValueError,'Different handoff residue'):
            self.seal()
        self.assertEqual((lineage/'audit.json').read_text(),'foreign')
        self.assertFalse((self.root/'parent_handoff.json').exists())

    def test_protected_payload_modified_after_copy_is_rejected(self):
        path=self.root/'parent_checkpoint/ckpt_0000002/__0_0.distcp'
        raw=path.read_bytes();path.write_bytes(b'X'*len(raw))
        with self.assertRaisesRegex(ValueError,'changed since full SHA'):
            self.seal()

    def test_rehashed_trace_still_must_equal_original_source(self):
        path=self.template/'trace.jsonl';path.write_text('{}\n')
        manifest=json.loads((self.template/'manifest.json').read_text())
        manifest['files']['trace.jsonl']=sha256_file(path)
        (self.template/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError,'Copied trace differs'):
            self.seal()

    def test_changed_source_after_preparation_rejected(self):
        path=self.source/'training/log.jsonl'
        path.write_text(path.read_text()+json.dumps(dict(step=4,tok_s=10))+'\n')
        with self.assertRaisesRegex(ValueError,'Source evidence changed'):
            self.seal()
        self.assertFalse((self.root/'parent_handoff.json').exists())

    def test_wrong_owner_rejected(self):
        self.owner='d'*32
        with self.assertRaisesRegex(ValueError,'receipt/owner/proof differs'):
            self.seal()

    def test_foreign_partial_metadata_stage_is_preserved(self):
        path=self.root/'handoff_transaction.json.tmp'
        path.write_bytes(b'incomplete unrelated receipt')
        with self.assertRaisesRegex(ValueError,'Different staging residue'):
            self.seal()
        self.assertEqual(path.read_bytes(),b'incomplete unrelated receipt')
        self.assertFalse((self.root/'parent_handoff.json').exists())

    def test_new_unlisted_process_owning_source_is_rejected(self):
        p=subprocess.Popen([sys.executable,'-c','import time; print("ready",flush=True); time.sleep(60)',
                            '--run-dir',str(self.source)],stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(p.stdout.readline().strip(),'ready')
            with self.assertRaisesRegex(ValueError,'Another process owns'):
                self.seal()
            self.assertFalse((self.root/'parent_handoff.json').exists())
        finally:
            p.terminate();p.wait(timeout=5);p.stdout.close()


if __name__=='__main__':unittest.main()
