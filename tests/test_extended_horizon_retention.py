"""Real cross-filesystem tiny-file copy/journal tests; not full3B peak tests."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

from common.data_contracts import resolve,artifact_path,sha256_file
from scripts.tiered_checkpoint_retention import make_policy,TieredRetention,atomic_json,inventory
from scripts.extended_horizon_retention import ExtendedRetention
from scripts.extended_horizon_storage import enforce_shared_space,GIB
from scripts.horizon_storage_plan import LocalHistoryCapacityError


class ExtendedRetentionTests(unittest.TestCase):
    def setUp(self):
        self.roots=[];self.policies=[]
        local=tempfile.TemporaryDirectory(prefix='extension-history-');self.addCleanup(local.cleanup)
        scratch=Path(local.name)
        for index in range(3):
            t=tempfile.TemporaryDirectory(prefix='extension-retention-',dir=resolve('runs'));self.addCleanup(t.cleanup)
            root=Path(t.name);self.roots.append(root);(root/'training').mkdir();(root/'checkpoint_certificates').mkdir()
            atomic_json(root/'training/training_contract.json',{'id':f'contract{index}'})
            policy=make_policy(root,scratch,2**20,run_id=uuid.uuid4().hex)
            policy.update(version=2,nfs_keep=3,adaptive_local_pruning=True);self.policies.append(policy)
            atomic_json(root/'contract.json',dict(tiered_retention=policy))
            with TieredRetention(root,policy,authorized=True).locked():pass
        self.root=self.roots[-1]
        self.auth=self.root/'approval.json'
        atomic_json(self.auth,dict(authorized=True,retention_transfer_authorized=True,
            original_run=str(self.roots[0]),source_run=str(self.roots[1]),new_run=str(self.root),
            nfs_keep=3,local_max_keep=15,adaptive_local_oldest_first=True,boundary_step=10000))
        definitions=[]
        for index,(root,policy,interval) in enumerate(zip(self.roots,self.policies,[(500,8000),(8500,10000),(10500,57000)])):
            row=dict(run=str(root),policy=policy,first_step=interval[0],last_step=interval[1])
            if index<2:
                row.update(contract_sha256=sha256_file(root/'contract.json'),
                    stopped_capture=dict(status='observed_live_not_stopped',run=str(root),
                        processes={n:dict(pid=1000000000+index*100+i,start_ticks=1) for i,n in enumerate(
                            ['supervisor','worker',*[f'rank{r}' for r in range(8)]])}))
            definitions.append(row)
        self.contract=dict(tiered_retention=self.policies[-1],retention_branches=definitions,
            retention_authorization=str(self.auth),retention_authorization_sha256=sha256_file(self.auth),
            expected_checkpoint_bytes=2**20,shared_storage=dict(reserve_bytes=200*GIB,temporary_budget_bytes=1024*GIB,
                nfs_pools=dict(history=dict(paths=[str(r) for r in self.roots],budget_bytes=GIB)),
                local_history_paths=[p['local_root'] for p in self.policies],
                local_temp_paths=[p['temp_root'] for p in self.policies],local_history_budget_bytes=GIB))
        self.protected=self.roots[1]/'parent_checkpoint'/'ckpt_0008000'
        self.protected.mkdir(parents=True);(self.protected/'evidence').write_text('must survive')
        for step in [7500,8000]:self.checkpoint(0,step)
        for step in [8500,9000,9500,10000]:self.checkpoint(1,step)
        self.manager=ExtendedRetention(self.root,self.contract,authorized=True)

    def checkpoint(self,index,step,certified=True):
        root=self.roots[index];cp=root/'training'/f'ckpt_{step:07d}';cp.mkdir()
        atomic_json(cp/'meta.json',dict(step=step,training_contract={'id':f'contract{index}'}))
        for name in ['.complete','.metadata','__0_0.distcp',*[f'rng_rank{r}.pt' for r in range(8)]]:
            (cp/name).write_text(f'fixture-{name}-{step}')
        files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha256_file(p)) for p in sorted(cp.iterdir())]
        proof=dict(status='passed_main_checkpoint_storage',step=step,training_contract_id=f'contract{index}',
            checkpoint=artifact_path(cp),files=files,bytes=sum(r['bytes'] for r in files))
        if certified:atomic_json(root/'checkpoint_certificates'/(cp.name+'.json'),proof)
        return cp

    def test_three_owners_global_latest3_local15_and_protected_evidence(self):
        self.manager.maintain()
        for step in range(10500,18001,500):
            self.checkpoint(2,step);state=self.manager.maintain()
            self.assertEqual(len(state['nfs_checkpoints']),3)
            self.assertLessEqual(len(state['local_checkpoints']),15)
        self.assertEqual(state['nfs_checkpoints'],['ckpt_0017000','ckpt_0017500','ckpt_0018000'])
        self.assertEqual(state['local_checkpoints'][0],'ckpt_0009500')
        self.assertEqual(state['distinct_checkpoints'],18)
        self.assertEqual((self.protected/'evidence').read_text(),'must survive')
        for branch,name,proof in self.manager.locals():inventory(branch.local/'checkpoints'/name,proof,hashes=True)

    def test_copy_failure_preserves_nfs_and_restarts_same_transaction(self):
        def fail(point):
            if point=='copied_file':raise OSError('fixture copy interruption')
        self.manager.branches[0].fault=fail
        with self.assertRaisesRegex(OSError,'copy interruption'):self.manager.maintain()
        self.assertTrue((self.roots[0]/'training/ckpt_0007500').exists())
        state=ExtendedRetention(self.root,self.contract,authorized=True).maintain()
        self.assertEqual(state['nfs_checkpoints'],['ckpt_0009000','ckpt_0009500','ckpt_0010000'])
        self.assertIn('ckpt_0007500',state['local_checkpoints'])

    def test_interrupted_retirement_recovers_and_local_loss_stays_optional(self):
        def fail(point):
            if point=='deleted_file':raise InterruptedError('fixture retirement interruption')
        self.manager.branches[0].fault=fail
        with self.assertRaises(InterruptedError):self.manager.maintain()
        manager=ExtendedRetention(self.root,self.contract,authorized=True);manager.maintain()
        for branch in manager.branches:shutil.rmtree(branch.local) # Test-owned directories only.
        state=ExtendedRetention(self.root,self.contract,authorized=True).maintain()
        self.assertEqual(state['local_checkpoints'],[]);self.assertEqual(len(state['nfs_checkpoints']),3)
        self.assertIn('local history is optional',state['resume_scope'])

    def test_uncertified_and_partial_new_snapshots_block_eviction(self):
        before=self.manager.maintain()
        self.checkpoint(2,10500,certified=False)
        partial=self.root/'training/ckpt_0011000';partial.mkdir()
        after=self.manager.maintain()
        self.assertEqual(after['nfs_checkpoints'],before['nfs_checkpoints'])
        self.assertEqual(after['local_checkpoints'],before['local_checkpoints'])
        self.assertEqual(len(after['uncertified_nfs']),2)

    def test_wrong_interval_owner_and_authorization_rejected(self):
        self.checkpoint(2,10000)
        with self.assertRaisesRegex(ValueError,'overlap|interval'):self.manager.maintain()
        atomic_json(self.auth,{'authorized':False})
        with self.assertRaises(ValueError):ExtendedRetention(self.root,self.contract,authorized=True)

    def test_pressure_evicts_oldest_local_only(self):
        self.manager.maintain();oldest=self.manager.branches[0].local/'checkpoints/ckpt_0007500'
        actual=self.manager.budgets
        def pressure(**kwargs):
            if oldest.exists():raise LocalHistoryCapacityError('fixture pressure')
            return actual(**kwargs)
        with self.manager.locked(),patch.object(self.manager,'budgets',side_effect=pressure):
            self.manager.make_local_room(1)
        self.assertFalse(oldest.exists())
        self.assertTrue((self.manager.branches[0].local/'checkpoints/ckpt_0008000').exists())
        self.assertTrue((self.protected/'evidence').exists())

    def test_shared_capacity_rejects_overlap_nfs_shortage_and_preserves_files(self):
        space=enforce_shared_space(self.root,self.contract)
        self.assertEqual(space['nfs_remaining_bytes'],GIB-sum(space['nfs_used_by_path'].values()))
        bad=deepcopy(self.contract);bad['shared_storage']['nfs_pools']['duplicate']=dict(paths=[str(self.roots[0])],budget_bytes=GIB)
        with self.assertRaisesRegex(ValueError,'Overlapping'):enforce_shared_space(self.root,bad)
        bad=deepcopy(self.contract);bad['shared_storage']['local_temp_paths']=[self.policies[0]['local_root']]
        with self.assertRaisesRegex(ValueError,'Overlapping'):enforce_shared_space(self.root,bad)
        with patch('scripts.extended_horizon_storage.shutil.disk_usage',return_value=SimpleNamespace(free=199*GIB)):
            with self.assertRaisesRegex(RuntimeError,'NFS'):enforce_shared_space(self.root,self.contract)
        self.assertTrue((self.roots[0]/'training/ckpt_0007500').exists())


if __name__=='__main__':unittest.main()
