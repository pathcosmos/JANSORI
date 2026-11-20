"""Actual NFS/local tiny file operations with synthetic checkpoint state."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid
from unittest.mock import patch

from common.data_contracts import resolve,artifact_path,sha256_file
from scripts.tiered_checkpoint_retention import make_policy,TieredRetention,atomic_json,inventory
from scripts.horizon_retention import HorizonRetention


class GlobalRetentionTests(unittest.TestCase):
    def setUp(self):
        self.roots=[];self.policies=[]
        disk=tempfile.TemporaryDirectory(prefix='global-history-');self.addCleanup(disk.cleanup);scratch=Path(disk.name)
        for i in range(2):
            t=tempfile.TemporaryDirectory(prefix='global-retention-',dir=resolve('runs'));self.addCleanup(t.cleanup)
            root=Path(t.name);self.roots.append(root)
            (root/'training').mkdir();(root/'checkpoint_certificates').mkdir()
            atomic_json(root/'training/training_contract.json',{'id':'contract'+str(i)})
            p=make_policy(root,scratch,2**20,run_id=uuid.uuid4().hex)
            p.update(version=2,nfs_keep=3,adaptive_local_pruning=True)
            self.policies.append(p)
            atomic_json(root/'contract.json',dict(tiered_retention=p))
        source,new=self.roots;self.source,self.root=source,new
        old=TieredRetention(source,self.policies[0],authorized=True)
        for step in range(500,2501,500):self.checkpoint(0,step);old.maintain()
        auth=dict(authorized=True,retention_transfer_authorized=True,source_run=str(source),new_run=str(new),
                  nfs_keep=3,local_max_keep=15,adaptive_local_oldest_first=True)
        self.auth=new/'approval.json';atomic_json(self.auth,auth)
        capture=dict(status='observed_live_not_stopped',run=str(source),processes={n:dict(pid=2147483500+i,start_ticks=1)
            for i,n in enumerate(['supervisor','worker',*[f'rank{r}' for r in range(8)]])})
        self.contract=dict(tiered_retention=self.policies[1],durable_budget_bytes=self.policies[1]['nfs_budget_bytes'],
            expected_checkpoint_bytes=2**20,space_reservations=dict(nfs={},local_temp_paths=[p['temp_root'] for p in self.policies]),
            legacy_retention=dict(source_run=str(source),source_policy=self.policies[0],parent_step=2500,
                authorization=str(self.auth),authorization_sha256=sha256_file(self.auth),
                source_contract_sha256=sha256_file(source/'contract.json'),stopped_capture=capture))
        self.manager=HorizonRetention(new,self.contract,authorized=True)

    def checkpoint(self,index,step,certified=True):
        root=self.roots[index];cp=root/'training'/f'ckpt_{step:07d}';cp.mkdir()
        atomic_json(cp/'meta.json',dict(step=step,training_contract={'id':'contract'+str(index)}))
        for name in ['.complete','.metadata','__0_0.distcp',*[f'rng_rank{r}.pt' for r in range(8)]]:(cp/name).write_text('synthetic-'+name+str(step))
        proof=dict(status='passed_main_checkpoint_storage',step=step,training_contract_id='contract'+str(index),
            checkpoint=artifact_path(cp),files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in sorted(cp.iterdir())])
        proof['bytes']=sum(r['bytes'] for r in proof['files'])
        if certified:atomic_json(root/'checkpoint_certificates'/(cp.name+'.json'),proof)
        return cp,proof

    def test_global_latest3_local15_across_contracts_and_owners(self):
        for step in range(3000,11501,500):
            self.checkpoint(1,step);state=self.manager.maintain()
            self.assertEqual(len(state['nfs_checkpoints']),3)
            self.assertLessEqual(len(state['local_checkpoints']),15)
        self.assertEqual(state['nfs_checkpoints'],['ckpt_0010500','ckpt_0011000','ckpt_0011500'])
        self.assertEqual(state['local_checkpoints'][0],'ckpt_0003000')
        self.assertEqual(state['distinct_checkpoints'],18)
        self.assertFalse(list((self.source/'training').glob('ckpt_*')))
        self.assertEqual(json.loads((self.source/'training/training_contract.json').read_text())['id'],'contract0')
        for b,name,proof in self.manager.locals():inventory(b.local/'checkpoints'/name,proof,hashes=True)

    def test_copy_failure_leaves_original_and_retry_recovers_same_owner(self):
        cp,_=self.checkpoint(1,3000)
        def fail(point):
            if point=='copied_file':raise OSError('fixture failed copy')
        self.manager.branches[0].fault=fail
        with self.assertRaisesRegex(OSError,'failed copy'):self.manager.maintain()
        self.assertTrue((self.source/'training/ckpt_0001500').exists());self.assertTrue(cp.exists())
        restarted=HorizonRetention(self.root,self.contract,authorized=True)
        self.assertEqual(restarted.maintain()['nfs_checkpoints'],['ckpt_0002000','ckpt_0002500','ckpt_0003000'])

    def test_partial_nfs_retirement_recovers_with_global_latest_protection(self):
        self.checkpoint(1,3000)
        def fail(point):
            if point=='deleted_file':raise InterruptedError('fixture deletion interrupted')
        self.manager.branches[0].fault=fail
        with self.assertRaises(InterruptedError):self.manager.maintain()
        state=HorizonRetention(self.root,self.contract,authorized=True).maintain()
        self.assertEqual(len(state['nfs_checkpoints']),3)
        self.assertIn('ckpt_0001500',state['local_checkpoints'])

    def test_uncertified_new_copy_blocks_eviction(self):
        self.checkpoint(1,3000,certified=False)
        state=self.manager.maintain()
        self.assertEqual(state['nfs_checkpoints'],['ckpt_0001500','ckpt_0002000','ckpt_0002500'])
        self.assertEqual(len(state['uncertified_nfs']),1)
        self.assertEqual(len(state['local_checkpoints']),2)

    def test_local_loss_does_not_become_resume_source_or_reconstruct_history(self):
        self.checkpoint(1,3000);self.manager.maintain()
        for b in self.manager.branches:shutil.rmtree(b.local) # Private fixture only.
        state=HorizonRetention(self.root,self.contract,authorized=True).maintain()
        self.assertEqual(state['local_checkpoints'],[])
        self.assertEqual(len(state['nfs_checkpoints']),3)

    def test_changed_authorization_and_crossed_contract_boundary_rejected(self):
        self.checkpoint(1,2500)
        with self.assertRaisesRegex(ValueError,'overlap|boundary'):self.manager.maintain()
        atomic_json(self.auth,{'authorized':False})
        with self.assertRaisesRegex(ValueError,'authorized'):HorizonRetention(self.root,self.contract,authorized=True)

    def test_capacity_eviction_uses_oldest_across_both_local_namespaces(self):
        self.checkpoint(1,3000);self.manager.maintain()
        actual=self.manager.budgets
        def pressure(**kwargs):
            if (self.manager.branches[0].local/'checkpoints/ckpt_0000500').exists():
                from scripts.horizon_storage_plan import LocalHistoryCapacityError
                raise LocalHistoryCapacityError('fixture pressure')
            return actual(**kwargs)
        with self.manager.locked(),patch.object(self.manager,'budgets',side_effect=pressure):self.manager.make_local_room(1)
        self.assertFalse((self.manager.branches[0].local/'checkpoints/ckpt_0000500').exists())
        self.assertTrue((self.manager.branches[0].local/'checkpoints/ckpt_0001000').exists())


if __name__=='__main__':unittest.main()
