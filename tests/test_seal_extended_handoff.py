"""Synthetic10000 protected payload; fresh prefix audit mocked explicitly."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from common.data_contracts import sha256_file,identity
from scripts.seal_extended_handoff import seal_extension
import test_prepare_extended_probe as fixtures

@unittest.skipUnless(os.environ.get('DENSE_EXTENSION_LINEAGE_FIXTURE'),'Explicit prefix fixture required')
class ExtendedSealTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.ExtendedProbePreparationTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        f=self.f.fixture;source=self.f.source;root=self.f.root;step=10000
        cp=source/'training/ckpt_0010000';cp.mkdir(parents=True)
        (cp/'meta.json').write_text(json.dumps(f.meta))
        (source/'training/log.jsonl').write_text(json.dumps(dict(step=10010,tok_s=1))+'\n')
        (source/'checkpoint_certificates').mkdir();(source/'checkpoint_certificates/ckpt_0010000.json').write_bytes((root/'parent_certificate.json').read_bytes())
        f.write(source/'contract.json',dict(legacy_retention=dict(source_run=f.receipt['original_run'],stopped_capture=f.receipt['original_capture'])))
        f.write(f.parent.with_name(f.parent.name+'.receipt.json'),dict(status='verified_protected_parent',
            transaction=dict(owner_id='a'*32,source=str(cp),destination=str(f.parent),proof_id=identity(f.proof),step=step,bytes=f.proof['bytes']),
            proof_id=identity(f.proof),stats=f.receipt['parent_stats']))
        # Remove only this fixture's synthetic receipt to exercise write-once seal.
        (root/'parent_handoff.json').unlink()
        self.audit=dict(counts=dict(committed_tokens=step*2097152,committed_step_rank_pairs=step*8,
                                   source_tokens={'fixture':step*2097152},generation_sha256_by_step={}),evidence={})
        self.manifest=dict(source_evidence={})
        for target,retval in [('scripts.seal_extended_handoff.audit_run',self.audit),
                              ('scripts.seal_extended_handoff.prepare_lineage',(self.manifest,self.audit))]:
            mock=patch(target,return_value=retval);mock.start();self.addCleanup(mock.stop)

    def seal(self):
        f=self.f.fixture
        return seal_extension(self.f.source,self.f.root,f.receipt['source_capture'],f.receipt['stopped_waiting_controllers'],owner_id='a'*32,step=10000)

    def test_copy_identity_and_prefix_receipt_bound_without_relabeling(self):
        result=self.seal();f=self.f.fixture
        self.assertEqual(result['status'],'sealed_extended_parent_handoff')
        self.assertEqual(result['execution_binding']['parent_contract_id'],f.meta['training_contract']['id'])
        self.assertFalse(result['runtime_applied']);self.assertFalse(result['actual8gpu_resume_verified'])
        before=sha256_file(self.f.root/'parent_handoff.json');self.seal()
        self.assertEqual(before,sha256_file(self.f.root/'parent_handoff.json'))

    def test_wrong_copy_owner_and_changed_payload_refused(self):
        f=self.f.fixture;path=f.parent.with_name(f.parent.name+'.receipt.json');v=json.loads(path.read_text())
        v['transaction']['owner_id']='b'*32;f.write(path,v)
        with self.assertRaisesRegex(ValueError,'owner/source'):self.seal()
        self.assertFalse((self.f.root/'parent_handoff.json').exists())
        v['transaction']['owner_id']='a'*32;f.write(path,v)
        (f.parent/'__0_0.distcp').write_text('corrupted')
        with self.assertRaises(ValueError):self.seal()
        self.assertFalse((self.f.root/'parent_handoff.json').exists())
