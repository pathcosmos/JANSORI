from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common.data_contracts import identity, resolve, sha256_file
from train import train as trainer
from train.final_resume_probe import file_stats
from train.horizon_resume_probe import validate_plan, check_spec


class ProbeAdmissionTests(unittest.TestCase):
    """Small real inventories; sealed-worker admission mocked at its boundary."""
    def setUp(self):
        self.temps=[tempfile.TemporaryDirectory(prefix='horizon-probe-fixture-',dir=resolve('runs')) for _ in range(2)]
        for temp in self.temps:self.addCleanup(temp.cleanup)
        self.continuous,self.split=[Path(t.name) for t in self.temps]
        self.paths=dict(baseline=self.continuous/'baseline',continuous=self.continuous,
                        first=self.split,resumed=self.split)
        self.paths['baseline'].mkdir()
        self.original=dict(trainer.DEFAULT,model='3b',micro_bsz=4,max_steps=57000,
            lr=.0003,warmup=1000,compile=False,keep_ckpts=57001,out_dir='runs/original/training')
        from train.runtime import RESUME_KEYS
        parent=dict(config={k:self.original[k] for k in RESUME_KEYS},source_order=['ko'],world_size=8,
                    monitored_evaluation={'generation_steps':[250,2500,5000,10000,35000,57000]})
        parent['id']=identity(parent)
        self.meta=dict(step=7000,world_size=8,tokens_per_step=2097152,config=self.original,
                       training_contract=parent,sampler_version=2)
        self.plan=dict(purpose='bounded_native_horizon_resume_probe',execution_authorized=True,
            world_size=8,target_steps=35000,main_target_modified=False,parent_step=7000,
            parent_contract_id=parent['id'],child_contract_id='c'*64,
            roots={'continuous':str(self.continuous),'split':str(self.split)},modes={},inputs={},
            code={p:sha256_file(resolve(p)) for p in ['train/horizon_resume_probe.py','train/horizon_probe_observer.py',
                'train/horizon_target.py','train/monitored.py','train/monitored_horizon.py']})
        self.contracts={};self.receipts={};self.proofs={}
        for mode,root in self.paths.items():
            config=dict(self.original,max_steps=57000 if mode=='baseline' else 35000,
                keep_ckpts=57001 if mode=='baseline' else 35001,save_final=mode!='baseline',
                stop_after=7001 if mode=='first' else 7002,out_dir=str(root/'training'))
            path=root/(mode+'.json');path.write_text(json.dumps(config))
            contract=self.contracts.setdefault(root,dict(purpose='main_training' if mode=='baseline' else 'horizon_validation',
                configurations={},main_training_authorized=True,horizon_execution={'parent_step':7000},
                generation_steps=parent['monitored_evaluation']['generation_steps'] if mode=='baseline' else [250,2500,5000,10000,35000]))
            contract['configurations'][path.name]=sha256_file(path)
            self.plan['modes'][mode]=dict(run_dir=str(root),config=str(path),attempt='horizon-probe-'+mode,observations=str(root/'observations'))
            self.plan['inputs'][str(path)]=sha256_file(path)
        for root,contract in self.contracts.items():
            path=root/'contract.json';path.write_text(json.dumps(contract));self.plan['inputs'][str(path)]=sha256_file(path)
        for root in [self.continuous,self.split]:
            parent_path=root/'parent_checkpoint/ckpt_0007000';parent_path.mkdir(parents=True)
            (parent_path/'meta.json').write_text(json.dumps(self.meta))
            for name in ['.complete','.metadata','__0_0.distcp',*[f'rng_rank{r}.pt' for r in range(8)]]:
                (parent_path/name).write_text('fixture')
            files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in parent_path.iterdir()]
            proof=dict(step=7000,files=files,bytes=sum(r['bytes'] for r in files),training_contract_id=parent['id'])
            self.receipts[root]=dict(parent_metadata=self.meta,parent_checkpoint=str(parent_path),parent_stats=file_stats(parent_path))
            self.proofs[root]=proof
        def handoff(root,*args):return self.receipts[root],{'id':'c'*64},self.proofs[root]
        self.mock=patch('train.monitored_horizon.load_handoff',side_effect=handoff);self.mock.start();self.addCleanup(self.mock.stop)

    def spec(self):return dict(plan=self.plan,plan_id=identity(self.plan))

    def change_config(self,mode,**updates):
        path=Path(self.plan['modes'][mode]['config']);config=json.loads(path.read_text());config.update(updates);path.write_text(json.dumps(config))
        root=path.parent;self.contracts[root]['configurations'][path.name]=sha256_file(path)
        (root/'contract.json').write_text(json.dumps(self.contracts[root]))
        self.plan['inputs'][str(path)]=sha256_file(path)
        self.plan['inputs'][str(root/'contract.json')]=sha256_file(root/'contract.json')

    def test_three_fresh_modes_select_only_protected_parent(self):
        for mode in ['baseline','continuous','first']:
            result=check_spec(self.spec(),mode)
            self.assertEqual(result[1],self.paths[mode])
            self.assertEqual(result[-2].name,'ckpt_0007000')

    def test_baseline_target_save_or_lr_changes_are_rejected(self):
        for updates in [dict(save_final=True),dict(max_steps=35000),dict(lr=.001)]:
            self.change_config('baseline',**updates)
            with self.assertRaisesRegex(ValueError,'original57k trajectory'):
                check_spec(self.spec(),'baseline')
            self.change_config('baseline',save_final=False,max_steps=57000,lr=.0003)

    def test_probe_boundary_or_attempt_broadening_is_rejected(self):
        for step in [0,7001,30000]:
            bad=deepcopy(self.plan);bad['parent_step']=step
            with self.assertRaises(ValueError):validate_plan(bad)
        bad=deepcopy(self.plan);bad['modes']['continuous']['attempt']='main'
        with self.assertRaises(ValueError):validate_plan(bad)
        bad=deepcopy(self.plan);bad['roots']['continuous']=str(resolve('runs/3b_full_v1'))
        with self.assertRaises(ValueError):validate_plan(bad)

    def test_parent_or_code_mutation_fails(self):
        path=Path(self.receipts[self.continuous]['parent_checkpoint'])/'__0_0.distcp'
        path.write_text('changed')
        with self.assertRaises(ValueError):check_spec(self.spec(),'continuous')
        self.plan['code']['train/horizon_resume_probe.py']='0'*64
        with self.assertRaisesRegex(ValueError,'immutable input/code changed'):
            check_spec(self.spec(),'continuous')

    def test_existing_output_and_missing_first_checkpoint_refused(self):
        root=self.continuous/'training';root.mkdir();(root/'unfinished').write_text('preserve')
        with self.assertRaisesRegex(ValueError,'existing diagnostic output'):
            check_spec(self.spec(),'continuous')
        self.assertEqual((root/'unfinished').read_text(),'preserve')
        with self.assertRaisesRegex(ValueError,'exactly the first'):
            check_spec(self.spec(),'resumed')

    def test_wrong_intermediate_certificate_identity_refused(self):
        cp=self.split/'training/ckpt_0007001';cp.mkdir(parents=True);(cp/'.complete').touch()
        cert=self.split/'certificates/first.json';cert.parent.mkdir()
        cert.write_text(json.dumps(dict(step=7001,training_contract_id='wrong',checkpoint=str(cp))))
        spec=self.spec();spec.update(resume_certificate=str(cert),resume_certificate_sha256=sha256_file(cert))
        with self.assertRaisesRegex(ValueError,'Intermediate checkpoint identity'):
            check_spec(spec,'resumed')


if __name__=='__main__':unittest.main()
