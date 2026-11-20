from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from common.data_contracts import identity, sha256_file
from train.final_resume_probe import file_stats
from train.horizon_transition import build_candidate
from train.parent_checkpoint_resume import ExternalParentResume


class ParentResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.parent = self.root/'parent'/'ckpt_0000002'
        self.destination = self.root/'child'/'training'
        self.parent.mkdir(parents=True)
        config = dict(max_steps=57000, warmup=1000, lr=.0003, min_lr_ratio=.1,
                      decay_frac=.15, sampler_version=2, global_tokens=2097152)
        contract = dict(config=config, files={'tokens': 'bound'}, mix={}, source_order=['ko'],
                        holdout=[], code={'trainer': 'bound'}, execution={}, world_size=8,
                        monitored_evaluation={'generation_steps': [250, 5000, 40000, 57000]})
        contract['id'] = identity(contract)
        self.meta = dict(training_contract=contract, config=config, step=2, world_size=8,
                         sampler_version=2, tokens_per_step=2097152)
        self.child, _ = build_candidate(self.meta, 40000)
        (self.parent/'meta.json').write_text(json.dumps(self.meta))
        # Inventory-only fixtures; real DCP loading is exercised separately below.
        for name in ['.complete', '.metadata', '__0_0.distcp', *[f'rng_rank{r}.pt' for r in range(8)]]:
            (self.parent/name).write_bytes(b'fixture')
        self.refresh_proof()

    def tearDown(self):
        self.temp.cleanup()

    def refresh_proof(self):
        files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in sorted(self.parent.iterdir())]
        self.proof=dict(files=files,bytes=sum(p['bytes'] for p in files),step=2,
                        training_contract_id=self.meta['training_contract']['id'])
        self.stats=file_stats(self.parent)

    def hook(self, **kwargs):
        args=dict(parent_checkpoint=self.parent,destination=self.destination,parent_metadata=self.meta,
                  child_contract=self.child,parent_proof=self.proof,verified_parent_stats=self.stats)
        args.update(kwargs)
        return ExternalParentResume(**args)

    def write_child(self, step=3, contract=None):
        out=self.destination/f'ckpt_{step:07d}';out.mkdir(parents=True)
        (out/'.complete').touch()
        meta=dict(self.meta,step=step,training_contract=contract or self.child)
        (out/'meta.json').write_text(json.dumps(meta))
        return out,meta

    def test_parent_is_never_exposed_to_pruning_or_modified(self):
        hook=self.hook(); before=file_stats(self.parent)
        self.assertEqual(hook.checkpoints(self.destination),[self.parent])
        hook.validate(self.meta,self.child)
        self.assertEqual(hook.checkpoints(self.destination),[])  # failed first save
        child,_=self.write_child()
        self.assertEqual(hook.checkpoints(self.destination),[child])
        self.assertEqual(before,file_stats(self.parent))

    def test_restart_uses_child_even_if_parent_was_lost(self):
        child,meta=self.write_child()
        hook=self.hook();shutil.rmtree(self.parent)
        self.assertEqual(hook.checkpoints(self.destination),[child])
        hook.validate(meta,self.child)
        bad=deepcopy(meta);bad['training_contract']['config']['max_steps']=57000
        with self.assertRaises(ValueError):hook.validate(bad,self.child)

    def test_incomplete_child_is_not_resume_candidate(self):
        partial=self.destination/'ckpt_0000003';partial.mkdir(parents=True)
        self.assertEqual(self.hook().checkpoints(self.destination),[self.parent])
        # A restarted process rechecks the protected parent instead of starting at0.
        self.assertEqual(self.hook().checkpoints(self.destination),[self.parent])

    def test_content_mutation_missing_rng_and_unbound_contract_rejected(self):
        hook=self.hook();(self.parent/'__0_0.distcp').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'changed'):hook.checkpoints(self.destination)
        # Retrying a failed scan must not accidentally return[] and start at0.
        with self.assertRaisesRegex(ValueError,'changed'):hook.checkpoints(self.destination)
        self.refresh_proof()
        (self.parent/'rng_rank7.pt').unlink()
        with self.assertRaises(ValueError):self.hook().checkpoints(self.destination)
        (self.parent/'rng_rank7.pt').write_bytes(b'fixture');self.refresh_proof()
        hook=self.hook();hook.checkpoints(self.destination)
        bad=deepcopy(self.child);bad['id']='wrong'
        with self.assertRaisesRegex(ValueError,'Unbound'):hook.validate(self.meta,bad)

    def test_load_detects_mid_read_source_mutation_and_wrong_namespace(self):
        hook=self.hook();hook.checkpoints(self.destination)
        def corrupt(*args,**kwargs):
            (self.parent/'__0_0.distcp').write_bytes(b'modified-during-load')
        with self.assertRaisesRegex(ValueError,'changed'):
            hook.load(corrupt, {}, checkpoint_id=self.parent)
        with self.assertRaisesRegex(ValueError,'namespace'):
            hook.load(lambda *a,**kw:None, {}, checkpoint_id=self.root/'unbound')

    def test_scope_symlink_and_certificate_step_guards(self):
        for destination in [self.parent,self.parent.parent,self.parent/'nested']:
            with self.subTest(destination=destination):
                with self.assertRaises(ValueError):self.hook(destination=destination)
        link=self.root/'link';link.symlink_to(self.parent,target_is_directory=True)
        with self.assertRaises(ValueError):self.hook(parent_checkpoint=link)
        proof=deepcopy(self.proof);proof['step']=3
        with self.assertRaises(ValueError):self.hook(parent_proof=proof)
        with self.assertRaises(ValueError):self.hook().checkpoints(self.root/'other')

    def test_child_does_not_rewind_or_exceed_target(self):
        for step in [2,40001]:
            with self.subTest(step=step):
                child,meta=self.write_child(step)
                hook=self.hook();hook.checkpoints(self.destination)
                with self.assertRaises(ValueError):hook.validate(meta,self.child)
                shutil.rmtree(child)

    def test_real_cpu_dcp_model_optimizer_rng_next_update(self):
        import torch
        import torch.distributed.checkpoint as dcp
        from torch.distributed.checkpoint.state_dict import get_state_dict
        from train.final_resume_probe import restore_state
        from unittest.mock import patch
        from train.horizon_worker_contract import BRIDGE_CODE, executable_candidate
        torch.set_num_threads(2)
        def make():
            m=torch.nn.Sequential(torch.nn.Linear(4,8),torch.nn.Dropout(.25),torch.nn.Linear(8,2))
            return m,torch.optim.AdamW(m.parameters(),lr=.0003)
        def update(m,o):
            x,y=torch.randn(3,4),torch.randn(3,2)
            loss=(m(x)-y).square().mean();loss.backward();o.step();o.zero_grad(set_to_none=True)
            return loss.item()
        torch.manual_seed(182)
        model,opt=make()
        for _ in range(2):update(model,opt)
        # Replace fixture payload with real unsharded CPU DCP. Eight RNG files
        # satisfy inventory shape only; this is NOT an8GPU resume test.
        (self.parent/'__0_0.distcp').unlink();(self.parent/'.metadata').unlink()
        msd,osd=get_state_dict(model,opt)
        dcp.save({'model':msd,'optim':osd},checkpoint_id=self.parent)
        for rank in range(8):torch.save({'cpu':torch.get_rng_state()},self.parent/f'rng_rank{rank}.pt')
        self.refresh_proof();before=file_stats(self.parent)
        expected_loss=update(model,opt)
        binding=dict(parent_contract_id=self.meta['training_contract']['id'],parent_step=2,
            parent_metadata_id=identity(self.meta),parent_certificate_sha256=identity(self.proof),
            parent_ledger_manifest_sha256='a'*64,code={p:sha256_file(p) for p in BRIDGE_CODE})
        child=executable_candidate(self.meta,binding)
        hook=self.hook(child_contract=child,execution_binding=binding)
        selected=hook.checkpoints(self.destination)[-1]
        metadata=json.loads((selected/'meta.json').read_text());hook.validate(metadata,child)
        resumed,optimizer=make()
        original_load=dcp.load
        with patch.object(dcp,'load',lambda *a,**kw:hook.load(original_load,*a,**kw)):
            restore_state(resumed,optimizer,selected,0,'cpu',metadata['step'])
        self.assertEqual(update(resumed,optimizer),expected_loss)
        left,right=get_state_dict(model,opt),get_state_dict(resumed,optimizer)
        def same(a,b):
            if isinstance(a,torch.Tensor):self.assertTrue(torch.equal(a,b))
            elif isinstance(a,dict):
                self.assertEqual(a.keys(),b.keys())
                for k in a:same(a[k],b[k])
            elif isinstance(a,(tuple,list)):
                self.assertEqual(len(a),len(b))
                for x,y in zip(a,b):same(x,y)
            else:self.assertEqual(a,b)
        same(left,right)
        self.assertEqual(file_stats(self.parent),before)


if __name__ == '__main__':
    unittest.main()
