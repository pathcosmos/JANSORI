"""Four real tiny CPU DCP/NFS/local owners; no production checkpoint mutation."""
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
from common.data_contracts import atomic_json,sha256_file
from scripts.audit_budget_retention import inventory_group
import test_budget_horizon_retention as fixture

class RestoredRetentionAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import torch,torch.distributed.checkpoint as dcp
        cls.torch,cls.dcp=torch,dcp;torch.set_num_threads(2)

    def setUp(self):
        fixture.BudgetRetentionTests.setUp(self)
        # These tests cover resident content/journals, not server admission.
        # A fresh tiny fixture must not reserve a second empty1024GiB pool
        # alongside the real running producer. Capacity gates have separate
        # storage/admission tests; no production implementation is replaced.
        budget=patch.object(self.manager,'budgets',return_value={'fixture_content_only':True})
        budget.start();self.addCleanup(budget.stop)
        for step in range(500,7500,500):self.checkpoint(0,step)
        self.manager.maintain();self.end=16000

    def config(self,index):
        return dict(model='3b',max_steps=35000 if index==1 else (50000 if index==3 else 57000),global_tokens=2097152,
                    ckpt_every=500,out_dir=str(self.roots[index]/'training'))

    def checkpoint(self,index,step,certified=True):
        root=self.roots[index];cp=root/'training'/f'ckpt_{step:07d}';t=self.torch
        self.dcp.save({'model':{'weight':t.arange(16.)+step},'optim':{'state':{'weight':{
            'step':t.tensor(float(step)),'exp_avg':t.arange(16.)/10,'exp_avg_sq':t.arange(16.)/100}}}},checkpoint_id=cp)
        atomic_json(cp/'meta.json',dict(step=step,world_size=8,tokens_per_step=2097152,
                    config=self.config(index),training_contract={'id':'contract'+str(index)}))
        for rank in range(8):t.save(dict(cpu=t.Generator().manual_seed(step+rank).get_state(),cuda=t.tensor([],dtype=t.uint8)),cp/f'rng_rank{rank}.pt')
        (cp/'.complete').touch()
        from scripts.supervise_horizon_training import seal as old_seal
        from scripts.supervise_extended_training import seal as new_seal
        from scripts.supervise_budget_training import seal as budget_seal
        certifier=budget_seal if index==3 else (old_seal if index==1 else new_seal)
        certifier(cp,root/'checkpoint_certificates'/(cp.name+'.json'),'contract'+str(index));return cp

    def seed(self,end=17000):
        for step in range(16500,end+1,500):self.checkpoint(3,step);self.manager.maintain()
        self.end=end

    def branches(self):
        intervals=[(500,8000),(8500,10000),(10500,16000),(16500,self.end)]
        return [dict(root=self.roots[i],policy=self.policies[i],parent={'id':'contract'+str(i)},config=self.config(i),
                     first_step=lo,last_step=hi) for i,(lo,hi) in enumerate(intervals)]

    def audit(self,**kw):return inventory_group(self.branches(),through_step=self.end,production=False,**kw)

    def test_empty_new_branch_at_boundary_has_only_three_nfs_resume_candidates(self):
        result=self.audit(hashes=True,final=True)
        self.assertEqual(result['certificate_count'],32);self.assertEqual(len(result['nfs']),3)
        self.assertEqual(len(result['local']),15);self.assertFalse(result['issues'] or result['residues'])
        self.assertEqual(result['branch_inventory'][3]['certificates'],{})
        self.assertFalse(result['protected_parent_counted_as_normal_history'])

    def test_fresh_content_all_four_owners_is_read_only(self):
        self.seed();bases=[*self.roots,*[Path(p['local_root']) for p in self.policies]]
        def hashes():return {str(p):sha256_file(p) for b in bases for p in b.rglob('*') if p.is_file()}
        before=hashes();r=self.audit(hashes=True,final=True)
        self.assertEqual(before,hashes());self.assertEqual(r['certificate_count'],34)
        self.assertEqual(r['nfs_resume_candidates'],['ckpt_0016000','ckpt_0016500','ckpt_0017000'])
        self.assertEqual(r['distinct_checkpoints'],18)
        self.assertTrue(all(v['rng_ranks']==8 and v['state_tensors']==4 for v in r['nfs'].values()))

    def test_local_loss_is_optional_and_no_namespace_recreation(self):
        self.seed();local=Path(self.policies[2]['local_root']);shutil.rmtree(local)
        r=self.audit(hashes=True,final=True)
        self.assertTrue(r['local_history_optional_loss']);self.assertFalse(local.exists())
        self.assertEqual(len(r['nfs_resume_candidates']),3)

    def test_local_only_latest_does_not_count_as_nfs_resume(self):
        self.seed();name='ckpt_0017000';local=Path(self.policies[3]['local_root'])
        shutil.move(str(self.root/'training'/name),str(local/'checkpoints'/name))
        shutil.copyfile(self.root/'checkpoint_certificates'/(name+'.json'),local/'certificates'/(name+'.json'))
        r=self.audit(hashes=False,final=False);self.assertNotIn(name,r['nfs_resume_candidates'])
        with self.assertRaisesRegex(ValueError,'NFS'):self.audit(hashes=True,final=True)

    def test_partial_staging_and_missing_schedule_fail_without_repair(self):
        local=Path(self.policies[2]['local_root']);p=local/'staging/preserved';p.mkdir();(p/'data').write_text('residue')
        with self.assertRaisesRegex(ValueError,'residues'):self.audit(hashes=True,final=True)
        self.assertTrue((p/'data').exists());shutil.rmtree(p)
        cert=self.roots[0]/'checkpoint_certificates/ckpt_0000500.json';cert.unlink()
        with self.assertRaisesRegex(ValueError,'schedule'):self.audit(hashes=True,final=True)
        self.assertFalse(cert.exists())

    def test_corrupted_payload_and_final_without_full_sha_rejected(self):
        with self.assertRaisesRegex(ValueError,'full-SHA'):self.audit(hashes=False,final=True)
        p=next((Path(self.policies[2]['local_root'])/'checkpoints').glob('*/__*.distcp'))
        raw=p.read_bytes();p.write_bytes(bytes([raw[0]^1])+raw[1:])
        with self.assertRaisesRegex(ValueError,'SHA'):self.audit(hashes=True,final=True)
