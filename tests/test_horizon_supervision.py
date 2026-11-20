import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from common.data_contracts import sha256_file, resolve
from scripts.protect_horizon_parent import protect_parent, GIB
from scripts.tiered_checkpoint_retention import inventory
from train.final_resume_probe import file_stats


class ProtectedParentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.source=self.base/'old/ckpt_0000002';self.source.mkdir(parents=True)
        self.run=self.base/'new';self.run.mkdir()
        meta=dict(step=2,world_size=8,training_contract={'id':'a'*64})
        (self.source/'meta.json').write_text(json.dumps(meta))
        for n in ['.complete','.metadata','__0_0.distcp',*[f'rng_rank{r}.pt' for r in range(8)]]:
            (self.source/n).write_bytes((n+'-fixture').encode()*32)
        files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in sorted(self.source.iterdir())]
        self.proof=dict(step=2,training_contract_id='a'*64,files=files,bytes=sum(p['bytes'] for p in files))
        self.before=file_stats(self.source)

    def tearDown(self):self.temp.cleanup()

    def protect(self, **kwargs):
        args=dict(owner_id='b'*32,required_free_after_bytes=200*GIB)
        args.update(kwargs)
        return protect_parent(self.source,self.run,self.proof,**args)

    def test_copy_publish_exact_reuse_and_source_unchanged(self):
        report=self.protect()
        final=self.run/'parent_checkpoint/ckpt_0000002'
        inventory(final,self.proof,hashes=True)
        self.assertEqual(report,self.protect())
        self.assertEqual(self.before,file_stats(self.source))
        self.assertFalse(report['source_deleted']);self.assertFalse(report['handoff_sealed'])

    def test_copy_interruption_retains_stage_and_resumes(self):
        def fail(point):
            if point=='copied_payload':raise InterruptedError('fixture copy interruption')
        with self.assertRaises(InterruptedError):self.protect(fault=fail)
        parent=self.run/'parent_checkpoint'
        self.assertTrue((parent/'ckpt_0000002.copying').exists())
        self.assertFalse((parent/'ckpt_0000002').exists())
        self.assertFalse((parent/'ckpt_0000002.receipt.json').exists())
        report=self.protect()
        self.assertEqual(report['status'],'verified_protected_parent')
        self.assertEqual(self.before,file_stats(self.source))

    def test_crash_after_publish_recovers_even_without_original(self):
        def fail(point):
            if point=='published_parent':raise InterruptedError('fixture crash after rename')
        with self.assertRaises(InterruptedError):self.protect(fault=fail)
        parent=self.run/'parent_checkpoint'
        self.assertTrue((parent/'ckpt_0000002').exists())
        self.assertFalse((parent/'ckpt_0000002.receipt.json').exists())
        # Delete only this test's temporary synthetic source to simulate loss.
        shutil.rmtree(self.source)
        self.assertEqual(self.protect()['status'],'verified_protected_parent')

    def test_space_failure_does_not_copy_or_remove_any_source(self):
        usage=shutil.disk_usage(self.run)
        with patch('scripts.protect_horizon_parent.shutil.disk_usage',return_value=type(usage)(usage.total,usage.used,200*GIB)):
            with self.assertRaisesRegex(RuntimeError,'space/reserve'):self.protect()
        self.assertEqual(self.before,file_stats(self.source))
        self.assertFalse((self.run/'parent_checkpoint/ckpt_0000002').exists())

    def test_foreign_residue_and_wrong_owner_preserved(self):
        stage=self.run/'parent_checkpoint/ckpt_0000002.copying';stage.mkdir(parents=True)
        (stage/'unrelated').write_text('keep')
        with self.assertRaisesRegex(ValueError,'Unowned'):self.protect()
        self.assertEqual((stage/'unrelated').read_text(),'keep')
        # Separate owned fixture namespace; foreign residue is left intact.
        self.run=self.base/'another';self.run.mkdir()
        self.protect()
        with self.assertRaisesRegex(ValueError,'different transaction'):self.protect(owner_id='c'*32)

    def test_source_corruption_and_destination_tampering_fail_closed(self):
        payload=self.source/'__0_0.distcp';payload.write_bytes(b'x'*payload.stat().st_size)
        with self.assertRaisesRegex(ValueError,'SHA'):self.protect()
        self.assertFalse((self.run/'parent_checkpoint/ckpt_0000002').exists())
        # Restore only synthetic fixture bytes; retry can repair its owned stage.
        payload.write_bytes(('__0_0.distcp-fixture').encode()*32)
        self.protect()
        p=self.run/'parent_checkpoint/ckpt_0000002/meta.json';p.write_text('{}')
        with self.assertRaisesRegex(ValueError,'changed after receipt'):self.protect()

    def test_actual_local_to_nfs_copy_uses_destination_filesystem_publication(self):
        with tempfile.TemporaryDirectory(prefix='horizon-protect-fixture-',dir=resolve('runs')) as path:
            self.run=Path(path)
            if self.source.stat().st_dev==self.run.stat().st_dev:
                self.skipTest('Two filesystems required for this test')
            self.protect()
            final=self.run/'parent_checkpoint/ckpt_0000002'
            self.assertEqual(final.stat().st_dev,self.run.stat().st_dev)
            inventory(final,self.proof,hashes=True)
            self.assertEqual(self.before,file_stats(self.source))


class HorizonSupervisorTests(unittest.TestCase):
    def test_legacy_adoption_cannot_be_used_for_new_horizon(self):
        from scripts.supervise_horizon_training import run
        with self.assertRaisesRegex(ValueError,'adoption is not yet validated'):
            run(Path('unused'),adoption='legacy-receipt')

    def test_new_sealer_certifies_full_cpu_state_and_rejects_old_target(self):
        import torch
        import torch.distributed.checkpoint as dcp
        from torch.distributed.checkpoint.state_dict import get_state_dict
        from scripts.supervise_horizon_training import seal
        torch.set_num_threads(2)
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);cp=base/'ckpt_0000002'
            model=torch.nn.Linear(4,2);opt=torch.optim.AdamW(model.parameters())
            for _ in range(2):
                model(torch.ones(1,4)).sum().backward();opt.step();opt.zero_grad(set_to_none=True)
            msd,osd=get_state_dict(model,opt)
            dcp.save({'model':msd,'optim':osd},checkpoint_id=cp)
            for rank in range(8):torch.save({'cpu':torch.get_rng_state()},cp/f'rng_rank{rank}.pt')
            (cp/'.complete').touch()
            meta=dict(step=2,world_size=8,tokens_per_step=2097152,training_contract={'id':'x'},
                      config={'model':'3b','max_steps':57000})
            (cp/'meta.json').write_text(json.dumps(meta))
            with self.assertRaisesRegex(ValueError,'identity differs'):seal(cp,base/'proof.json','x')
            meta['config']['max_steps']=35000;(cp/'meta.json').write_text(json.dumps(meta))
            proof=seal(cp,base/'proof.json','x')
            inventory(cp,proof,hashes=True)
            self.assertEqual(proof['step'],2)
            # CPU fixture checks full DCP optimizer map; it is not a3B/8GPU run.
            bad=base/'ckpt_0000003';dcp.save({'model':msd},checkpoint_id=bad)
            (bad/'.complete').touch();meta['step']=3;(bad/'meta.json').write_text(json.dumps(meta))
            for rank in range(8):torch.save({'cpu':torch.get_rng_state()},bad/f'rng_rank{rank}.pt')
            with self.assertRaisesRegex(ValueError,'optimizer state missing'):seal(bad,base/'bad.json','x')


if __name__=='__main__':unittest.main()
