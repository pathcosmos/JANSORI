import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import shutil
from common.data_contracts import resolve
from scripts.horizon_storage_plan import capacity_plan,GIB,enforce_runtime_space,LocalHistoryCapacityError


class HorizonStoragePlanTests(unittest.TestCase):
    def plan(self,**kw):
        args=dict(checkpoint_bytes=38667183745,nfs_free=818*GIB,local_free=1490*GIB,
                  source_nfs_remaining=51*GIB,source_history_growth=41*GIB)
        args.update(kw);return capacity_plan(**args)

    def test_phase_peaks_are_not_added_twice_and_old_usage_is_not_readded(self):
        p=self.plan()
        self.assertEqual(p['peak_nfs_future_bytes'],max(p['before_handoff_nfs_future_bytes'],p['after_handoff_nfs_future_bytes']))
        self.assertTrue(p['minimum_capacity_feasible'])
        self.assertEqual(p['affordable_new_local_keep_with_copy_headroom'],4)
        self.assertFalse(p['old_history_deletion_assumed'])

    def test_exact_admission_boundaries(self):
        p=self.plan();nfs=p['peak_nfs_future_bytes']+200*GIB
        local=200*GIB+1024*GIB+41*GIB+p['new_local_one_copy_growth_bytes']
        self.assertTrue(self.plan(nfs_free=nfs,local_free=local)['minimum_capacity_feasible'])
        self.assertFalse(self.plan(nfs_free=nfs-1,local_free=local)['minimum_capacity_feasible'])
        self.assertFalse(self.plan(nfs_free=nfs,local_free=local-1)['minimum_capacity_feasible'])

    def test_allocated_temporary_files_reduce_free_and_future_equally(self):
        before=self.plan();after=self.plan(temporary_used=192*GIB,local_free=(1490-192)*GIB)
        self.assertEqual(before['new_local_history_and_copy_available_bytes'],after['new_local_history_and_copy_available_bytes'])
        with self.assertRaises(ValueError):self.plan(temporary_used=1024*GIB+1)

    def test_history_never_exceeds15_and_copy_space_is_reserved(self):
        self.assertEqual(self.plan(local_free=5000*GIB)['affordable_new_local_keep_with_copy_headroom'],15)
        self.assertIsNone(self.plan(local_free=1200*GIB)['affordable_new_local_keep_with_copy_headroom'])

    def test_invalid_measurements_fail_closed(self):
        for key,value in [('checkpoint_bytes',0),('nfs_free',-1),('local_free',True),('export_budget',1.5)]:
            with self.subTest(key=key):
                with self.assertRaises(ValueError):self.plan(**{key:value})


class RuntimeSpaceTests(unittest.TestCase):
    def setUp(self):
        self.nfs=tempfile.TemporaryDirectory(prefix='horizon-space-fixture-',dir=resolve('runs'))
        self.local=tempfile.TemporaryDirectory()
        self.root=Path(self.nfs.name)/'main';self.root.mkdir()
        self.export=Path(self.nfs.name)/'export'
        self.temp=Path(self.local.name)/'temp';self.temp.mkdir()
        self.history=Path(self.local.name)/'history'
        self.contract=dict(durable_budget_bytes=GIB,expected_checkpoint_bytes=100,
            tiered_retention=dict(reserve_bytes=200*GIB,local_root=str(self.history)),
            space_reservations=dict(nfs={str(self.export):GIB},local_temp_paths=[str(self.temp)]))

    def tearDown(self):self.nfs.cleanup();self.local.cleanup()

    def test_allocated_artifacts_are_subtracted_and_overlaps_rejected(self):
        if self.root.stat().st_dev==self.temp.stat().st_dev:self.skipTest('Two filesystems required')
        before=enforce_runtime_space(self.root,self.contract)
        self.export.mkdir();(self.export/'artifact').write_bytes(b'x'*100)
        after=enforce_runtime_space(self.root,self.contract)
        self.assertEqual(before['nfs_remaining_bytes']-after['nfs_remaining_bytes'],100)
        self.contract['space_reservations']['nfs'][str(self.root/'nested')]=GIB
        with self.assertRaisesRegex(ValueError,'Overlapping'):enforce_runtime_space(self.root,self.contract)

    def test_inflight_copy_is_not_reserved_as_a_second_copy(self):
        if self.root.stat().st_dev==self.temp.stat().st_dev:self.skipTest('Two filesystems required')
        usage=shutil.disk_usage(self.root)
        def available(path):
            free=300*GIB if Path(path)==self.root else 1224*GIB
            return type(usage)(usage.total,usage.used,free)
        with patch('scripts.horizon_storage_plan.shutil.disk_usage',side_effect=available):
            with self.assertRaises(LocalHistoryCapacityError):enforce_runtime_space(self.root,self.contract)
            enforce_runtime_space(self.root,self.contract,local_copy_busy=True)

    def test_parent_included_in_root_budget_exactly_once(self):
        from scripts.supervise_horizon_training import validate_parent_budget
        p=capacity_plan(checkpoint_bytes=100,nfs_free=1000*GIB,local_free=2000*GIB,
                        source_nfs_remaining=0,source_history_growth=0)
        total=p['new_nfs_retention_budget_bytes']+p['protected_parent_budget_bytes']
        contract=dict(expected_checkpoint_bytes=100,protected_parent_budget_bytes=p['protected_parent_budget_bytes'],
            durable_budget_bytes=total,tiered_retention=dict(nfs_budget_bytes=total,
                protected_parent_budget_bytes=p['protected_parent_budget_bytes']))
        validate_parent_budget(contract,{'bytes':100})
        contract['tiered_retention']['nfs_budget_bytes']=p['new_nfs_retention_budget_bytes']
        with self.assertRaisesRegex(ValueError,'included once'):validate_parent_budget(contract,{'bytes':100})


if __name__=='__main__':unittest.main()
