"""CPU fixtures and real tiny DCP; no3B,8GPU or production launch claims."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from common.data_contracts import identity, sha256_file
from train.budget_horizon_contract import (BRIDGE_CODE, bound_candidate,
    validate_bound_candidate, verify_bridge_files)
from train.budget_parent_resume import BudgetParentResume
from test_budget_horizon import fixture
from train.final_resume_probe import file_stats


def seal(d):
    d=deepcopy(d);d.pop('id',None);d['id']=identity(d);return d


class BudgetResumeTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.parent=self.root/'protected'/'ckpt_0016000';self.parent.mkdir(parents=True)
        self.destination=self.root/'child'/'training'
        self.meta=fixture()['parent_metadata']
        (self.parent/'meta.json').write_text(json.dumps(self.meta))
        for name in ['.complete','.metadata','__0_0.distcp',*[f'rng_rank{r}.pt' for r in range(8)]]:
            (self.parent/name).write_bytes(b'inventory fixture')
        self.refresh()

    def refresh(self):
        files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in sorted(self.parent.iterdir())]
        self.proof=dict(files=files,bytes=sum(r['bytes'] for r in files),step=16000,
                        training_contract_id=self.meta['training_contract']['id'])
        self.stats=file_stats(self.parent)
        # Synthetic entry/manifest digests. This does not satisfy code-file admission.
        self.binding=dict(parent_contract_id=self.meta['training_contract']['id'],parent_step=16000,
            parent_metadata_id=identity(self.meta),parent_certificate_sha256='a'*64,
            parent_certificate_id=identity(self.proof),parent_ledger_manifest_sha256='b'*64,
            code={p:'c'*64 for p in BRIDGE_CODE})
        self.child=bound_candidate(self.meta,self.binding)

    def hook(self,**changes):
        args=dict(parent_checkpoint=self.parent,
            destination=self.destination,parent_metadata=self.meta,child_contract=self.child,
            parent_proof=self.proof,verified_parent_stats=self.stats,execution_binding=self.binding)
        args.update(changes);return BudgetParentResume(**args)

    def test_preserves_ancestry_and_rejects_rehashed_binding_changes(self):
        before=deepcopy(self.meta)
        self.assertEqual(self.child['horizon_transition'],self.meta['training_contract']['horizon_transition'])
        self.assertEqual(self.child['budget_horizon_transition'],self.binding)
        report=validate_bound_candidate(self.meta,self.child,self.binding)
        self.assertFalse(report['runtime_authorized']);self.assertFalse(report['distributed_resume_verified'])
        self.assertEqual(before,self.meta)
        for mode in ['ancestry','input','binding','generation','worker']:
            with self.subTest(mode=mode):
                bad=deepcopy(self.child)
                if mode=='ancestry':bad['horizon_transition']['parent_step']=7500
                if mode=='input':bad['files']['other']='unbound'
                if mode=='binding':bad['budget_horizon_transition']['parent_ledger_manifest_sha256']='d'*64
                if mode=='generation':bad['monitored_evaluation']['generation_steps']=[50000]
                if mode=='worker':bad['monitored_evaluation']['code_sha256']='e'*64
                with self.assertRaises(ValueError):validate_bound_candidate(self.meta,seal(bad),self.binding)
        for key,value in [('parent_step',True),('parent_contract_id','f'*64),
                          ('parent_metadata_id','d'*64),('code',{})]:
            bad=deepcopy(self.binding);bad[key]=value
            with self.assertRaises(ValueError):bound_candidate(self.meta,bad)
        with self.assertRaises(ValueError):verify_bridge_files(self.binding)

    def test_parent_only_selected_once_and_never_pruned(self):
        hook=self.hook();before=file_stats(self.parent)
        partial=self.destination/'ckpt_0016001';partial.mkdir(parents=True)
        self.assertEqual(hook.checkpoints(self.destination),[self.parent])
        hook.validate(self.meta,self.child)
        self.assertEqual(hook.checkpoints(self.destination),[])
        self.assertEqual(file_stats(self.parent),before)
        self.assertEqual(self.hook().checkpoints(self.destination),[self.parent])

    def test_exact_child_restart_and_target_guards(self):
        path=self.destination/'ckpt_0016001';path.mkdir(parents=True);(path/'.complete').touch()
        meta=dict(self.meta,step=16001,config=self.child['config'],training_contract=self.child)
        hook=self.hook();shutil.rmtree(self.parent)
        self.assertEqual(hook.checkpoints(self.destination),[path])
        self.assertEqual(hook.validate(meta,self.child)['mode'],'exact_child_resume')
        for step in [16000,50001,True]:
            with self.assertRaises(ValueError):hook.validate(dict(meta,step=step),self.child)

    def test_proof_mutation_payload_mutation_and_scope_rejected(self):
        proof=deepcopy(self.proof);proof['extra']='tampered'
        with self.assertRaises(ValueError):self.hook(parent_proof=proof)
        with self.assertRaises(ValueError):self.hook(execution_binding=None)
        for path in [self.parent,self.parent.parent,self.parent/'nested']:
            with self.assertRaises(ValueError):self.hook(destination=path)
        hook=self.hook();hook.checkpoints(self.destination)
        def mutate(*a,**kw):(self.parent/'__0_0.distcp').write_bytes(b'changed')
        with self.assertRaises(ValueError):hook.load(mutate,{},checkpoint_id=self.parent)
        with self.assertRaises(ValueError):self.hook().checkpoints(self.destination)

    def test_real_cpu_dcp_next_update_and_split_resume(self):
        import torch
        import torch.distributed.checkpoint as dcp
        from torch.distributed.checkpoint.state_dict import get_state_dict
        from train.final_resume_probe import restore_state
        torch.set_num_threads(2)
        def make():
            m=torch.nn.Sequential(torch.nn.Linear(4,8),torch.nn.Dropout(.25),torch.nn.Linear(8,2))
            return m,torch.optim.AdamW(m.parameters(),lr=.0003)
        def update(m,o):
            x,y=torch.randn(3,4),torch.randn(3,2);loss=(m(x)-y).square().mean()
            loss.backward();o.step();o.zero_grad(set_to_none=True);return loss.item()
        def save(m,o,path,meta):
            path.mkdir(parents=True,exist_ok=True);msd,osd=get_state_dict(m,o)
            dcp.save({'model':msd,'optim':osd},checkpoint_id=path)
            for rank in range(8):torch.save({'cpu':torch.get_rng_state()},path/f'rng_rank{rank}.pt')
            (path/'meta.json').write_text(json.dumps(meta));(path/'.complete').touch()
        def load(hook):
            path=hook.checkpoints(self.destination)[-1];meta=json.loads((path/'meta.json').read_text())
            hook.validate(meta,self.child);m,o=make();loader=dcp.load
            with patch.object(dcp,'load',lambda *a,**kw:hook.load(loader,*a,**kw)):
                restore_state(m,o,path,0,'cpu',meta['step'])
            return m,o
        torch.manual_seed(184);m,o=make();update(m,o);update(m,o)
        # Synthetic boundary counter, not10000 actual updates. Payload and subsequent
        # AdamW/RNG updates are real CPU computations, with8RNG envelope fixtures.
        for state in o.state.values():state['step'].fill_(16000)
        (self.parent/'__0_0.distcp').unlink();(self.parent/'.metadata').unlink()
        save(m,o,self.parent,self.meta);self.refresh();before=file_stats(self.parent)
        expected_first=update(m,o);expected_second=update(m,o)
        expected=deepcopy(get_state_dict(m,o));rng=torch.get_rng_state().clone()
        resumed,opt=load(self.hook());self.assertEqual(update(resumed,opt),expected_first)
        child_path=self.destination/'ckpt_0016001'
        child_meta=dict(self.meta,step=16001,config=self.child['config'],training_contract=self.child)
        save(resumed,opt,child_path,child_meta)
        resumed,opt=load(self.hook());self.assertEqual(update(resumed,opt),expected_second)
        def same(a,b):
            if isinstance(a,torch.Tensor):self.assertTrue(torch.equal(a,b))
            elif isinstance(a,dict):
                self.assertEqual(a.keys(),b.keys())
                for k in a:same(a[k],b[k])
            elif isinstance(a,(list,tuple)):
                self.assertEqual(len(a),len(b))
                for x,y in zip(a,b):same(x,y)
            else:self.assertEqual(a,b)
        same(expected,get_state_dict(resumed,opt));self.assertTrue(torch.equal(rng,torch.get_rng_state()))
        self.assertEqual(before,file_stats(self.parent))


if __name__=='__main__':unittest.main()
