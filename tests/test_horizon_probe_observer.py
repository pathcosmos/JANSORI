from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import torch
from torch.distributed.checkpoint.state_dict import get_state_dict
from train import train as trainer
from train.horizon_probe_observer import fingerprint, capture_state, NativeStateObserver, observe_native
from scripts.verify_horizon_observations import compare_observations


def distributed_fingerprint_probe(rank, rendezvous, directory):
    import torch.distributed as dist
    from torch.distributed.device_mesh import init_device_mesh
    from torch.distributed.tensor import DTensor, Shard
    dist.init_process_group('gloo',init_method='file://'+rendezvous,rank=rank,world_size=2)
    try:
        mesh=init_device_mesh('cpu',(2,))
        local=torch.arange(rank*4,rank*4+4,dtype=torch.float32)
        tensor=DTensor.from_local(local,mesh,[Shard(0)],run_check=True,shape=(8,),stride=(1,))
        result=fingerprint({'weight':tensor})
        (Path(directory)/f'dtensor-{rank}.json').write_text(json.dumps(result))
    finally:
        dist.destroy_process_group()


class NativeObserverTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(2)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.config=dict(trainer.DEFAULT,max_steps=35000,stop_after=4,compile=False)
        self.model,self.optimizer=self.make()
        torch.manual_seed(41)
        for _ in range(2):self.update(self.model,self.optimizer)
        self.saved=deepcopy(get_state_dict(self.model,self.optimizer))
        self.rng=torch.get_rng_state().clone()

    def make(self):
        model=torch.nn.Sequential(torch.nn.Linear(4,8),torch.nn.Dropout(.3),torch.nn.Linear(8,2))
        return model,torch.optim.AdamW(model.parameters(),lr=.0003)

    def update(self,model,optimizer):
        model(torch.randn(3,4)).square().mean().backward()
        optimizer.step();optimizer.zero_grad(set_to_none=True)

    def run_mode(self,mode,saved=None,rng=None,callback=None):
        start=3 if mode=='resumed' else 2
        end=3 if mode=='first' else 4
        config=dict(self.config,stop_after=end,max_steps=57000 if mode=='baseline' else 35000)
        observer=NativeStateObserver(self.root,mode=mode,rank=0,world=1,parent_step=2,
            config=config,plan_id='a'*64,on_initial=callback)
        model,opt=self.make();msd,osd=deepcopy(saved or self.saved)
        with observe_native(trainer,observer):
            trainer.set_state_dict(model,opt,model_state_dict=msd,optim_state_dict=osd)
            torch.set_rng_state(self.rng if rng is None else rng)
            for step in range(start,end):
                lr=trainer.lr_at(step,config)
                for group in opt.param_groups:group['lr']=lr
                self.update(model,opt)
        return json.loads(observer.path.read_text()),deepcopy(get_state_dict(model,opt)),torch.get_rng_state().clone()

    def test_actual_cpu_native_hooks_compare_all_four_trajectories(self):
        baseline,_,_=self.run_mode('baseline')
        continuous,_,_=self.run_mode('continuous')
        first,saved,rng=self.run_mode('first')
        resumed,_,_=self.run_mode('resumed',saved,rng)
        self.assertEqual(baseline['initial'],continuous['initial'])
        self.assertEqual(continuous['initial'],first['initial'])
        self.assertEqual(baseline['updates'],continuous['updates'])
        self.assertEqual(continuous['updates'],first['updates']+resumed['updates'])
        self.assertEqual(first['updates'][0]['state'],resumed['initial']['state'])
        for report in [baseline,continuous,first,resumed]:
            self.assertTrue(report['success'])
            self.assertFalse(report['actual8gpu_acceptance'])
            self.assertEqual(report['main_committed_tokens_added'],0)
        rows=[baseline,continuous,first,resumed]
        result=compare_observations(rows,parent_step=2,plan_id='a'*64,world=1,require_cuda=False)
        self.assertFalse(result['actual8gpu_acceptance'])
        with self.assertRaisesRegex(ValueError,'Missing four-mode'):
            compare_observations(rows[:-1],parent_step=2,plan_id='a'*64,world=1,require_cuda=False)
        bad=deepcopy(rows);bad[-1]['updates'][0]['lr']*=2
        with self.assertRaisesRegex(ValueError,'Continuous/split'):
            compare_observations(bad,parent_step=2,plan_id='a'*64,world=1,require_cuda=False)
        # Relabeling CPU rows as8ranks cannot satisfy the production device gate.
        relabeled=[]
        for rank in range(8):
            for row in rows:
                row=deepcopy(row);row.update(rank=rank,world_size=8);relabeled.append(row)
        with self.assertRaisesRegex(ValueError,'CPU observation'):
            compare_observations(relabeled,parent_step=2,plan_id='a'*64)

    def test_observation_does_not_change_rng_or_optimizer_state(self):
        before_rng=torch.get_rng_state().clone()
        before=deepcopy(get_state_dict(self.model,self.optimizer))
        state=capture_state(self.model,self.optimizer,'cpu',2)
        self.assertTrue(torch.equal(before_rng,torch.get_rng_state()))
        self.assertEqual(state,capture_state(self.model,self.optimizer,'cpu',2))
        self.assertEqual(fingerprint(before),fingerprint(get_state_dict(self.model,self.optimizer)))

    def test_fingerprint_detects_shape_dtype_and_optimizer_changes(self):
        a=torch.arange(8,dtype=torch.float32)
        self.assertNotEqual(fingerprint(a),fingerprint(a.reshape(2,4)))
        self.assertNotEqual(fingerprint(a),fingerprint(a.double()))
        before=capture_state(self.model,self.optimizer,'cpu',2)
        parameter=next(self.model.parameters())
        self.optimizer.state[parameter]['exp_avg'].add_(1)
        after=capture_state(self.model,self.optimizer,'cpu',2)
        self.assertEqual(before['model'],after['model'])
        self.assertNotEqual(before['optimizer'],after['optimizer'])
        with self.assertRaises(ValueError):fingerprint({'unsupported':object()})

    def test_wrong_optimizer_step_rejected(self):
        with self.assertRaisesRegex(ValueError,'step differs'):
            capture_state(self.model,self.optimizer,'cpu',3)

    def test_callback_consuming_rng_fails_and_restores_hooks(self):
        original=(trainer.set_state_dict,trainer.lr_at,torch.optim.AdamW.step)
        def callback(*args):torch.rand(1)
        with self.assertRaisesRegex(ValueError,'callback changed'):
            self.run_mode('continuous',callback=callback)
        self.assertEqual(original,(trainer.set_state_dict,trainer.lr_at,torch.optim.AdamW.step))
        report=json.loads((self.root/'continuous-rank0.json').read_text())
        self.assertFalse(report['success']);self.assertIn('callback changed',report['error'])

    def test_incomplete_mode_refuses_success_and_preserves_receipt(self):
        observer=NativeStateObserver(self.root,mode='continuous',rank=0,world=1,parent_step=2,
            config=self.config,plan_id='a'*64)
        with self.assertRaisesRegex(ValueError,'exact requested updates'):
            with observe_native(trainer,observer):pass
        with self.assertRaisesRegex(ValueError,'Preserve existing'):
            NativeStateObserver(self.root,mode='continuous',rank=0,world=1,parent_step=2,
                config=self.config,plan_id='a'*64)

    def test_changed_training_configuration_fails_before_update(self):
        observer=NativeStateObserver(self.root,mode='first',rank=0,world=1,parent_step=2,
            config=dict(self.config,stop_after=3),plan_id='a'*64)
        observer.bind(self.model,self.optimizer)
        with self.assertRaisesRegex(ValueError,'update/configuration'):
            observer.before(2,dict(self.config,lr=.001),.001)

    def test_rng_consuming_state_extraction_is_rejected(self):
        original=get_state_dict
        def changed(*args,**kwargs):
            torch.rand(1)
            return original(*args,**kwargs)
        with patch('torch.distributed.checkpoint.state_dict.get_state_dict',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'consumed RNG'):
                capture_state(self.model,self.optimizer,'cpu',2)

    def test_actual_two_rank_cpu_dtensor_local_bytes_and_global_shape(self):
        torch.multiprocessing.spawn(distributed_fingerprint_probe,
            args=(str(self.root/'rendezvous'),str(self.root)),nprocs=2,join=True)
        reports=[json.loads((self.root/f'dtensor-{rank}.json').read_text()) for rank in range(2)]
        for row in reports:
            self.assertEqual(row['tensors'],1)
            self.assertEqual(row['global_elements'],8)
            self.assertEqual(row['local_bytes'],16)
        self.assertNotEqual(reports[0]['sha256'],reports[1]['sha256'])


if __name__=='__main__':unittest.main()
