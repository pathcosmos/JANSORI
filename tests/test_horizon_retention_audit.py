"""Real tinyCPU DCP and NFS/local history; never actual3B final acceptance."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import unittest

from common.data_contracts import atomic_json,sha256_file
from scripts.supervise_main_training import seal
from scripts.audit_horizon_retention import inventory_group
from scripts.audit_checkpoint_retention import Snapshot,ChangedDuringAudit
import test_horizon_retention as global_fixture


class HorizonRetentionAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import torch,torch.distributed.checkpoint as dcp
        cls.torch,cls.dcp=torch,dcp;torch.set_num_threads(2)

    def setUp(self):global_fixture.GlobalRetentionTests.setUp(self)

    def config(self,index):
        return dict(model='3b',max_steps=57000 if index==0 else 35000,global_tokens=2097152,
            ckpt_every=500,out_dir=str(self.roots[index]/'training'))

    def checkpoint(self,index,step,certified=True):
        root=self.roots[index];cp=root/'training'/f'ckpt_{step:07d}';t=self.torch
        self.dcp.save({'model':{'weight':t.arange(16.)+step},'optim':{'state':{'weight':{
            'step':t.tensor(float(step)),'exp_avg':t.arange(16.)/10,'exp_avg_sq':t.arange(16.)/100}}}},checkpoint_id=cp)
        atomic_json(cp/'meta.json',dict(step=step,world_size=8,tokens_per_step=2097152,
            config=self.config(index),training_contract={'id':'contract'+str(index)}))
        for rank in range(8):
            t.save(dict(cpu=t.Generator().manual_seed(step+rank).get_state(),cuda=t.tensor([],dtype=t.uint8)),cp/f'rng_rank{rank}.pt')
        (cp/'.complete').touch()
        from scripts.supervise_horizon_training import seal as horizon_seal
        certifier=seal if index==0 else horizon_seal
        proof=certifier(cp,root/'checkpoint_certificates'/(cp.name+'.json'),'contract'+str(index))
        return cp,proof

    def seed(self,end=4500):
        for step in range(3000,end+1,500):self.checkpoint(1,step);self.manager.maintain()
        self.end=end

    def branches(self):
        return [dict(root=self.roots[i],policy=self.policies[i],parent={'id':'contract'+str(i)},config=self.config(i),
            first_step=500 if i==0 else 3000,last_step=2500 if i==0 else self.end) for i in range(2)]

    def audit(self,**kwargs):
        return inventory_group(self.branches(),through_step=self.end,production=False,**kwargs)

    def test_global3_15_fresh_dcp_hashes_no_repair_no_mutation(self):
        self.seed(11500)
        bases=[*self.roots,*[Path(p['local_root']) for p in self.policies]]
        def hashes():return {str(p):sha256_file(p) for base in bases for p in base.rglob('*') if p.is_file()}
        before=hashes();result=self.audit(hashes=True,final=True)
        self.assertEqual(result['status'],'passed_fixture_horizon_retention_content')
        self.assertEqual(len(result['nfs']),3);self.assertEqual(len(result['local']),15)
        self.assertEqual(result['certificate_count'],23);self.assertEqual(result['distinct_checkpoints'],18)
        self.assertEqual(result['nfs_resume_candidates'],['ckpt_0010500','ckpt_0011000','ckpt_0011500'])
        self.assertFalse(result['protected_parent_counted_as_normal_history'])
        self.assertFalse(result['residues'] or result['issues'] or result['overlap'])
        self.assertTrue(all(v['rng_ranks']==8 and v['state_tensors']==4 for v in result['nfs'].values()))
        self.assertEqual(before,hashes())

    def test_missing_old_local_namespace_is_optional_and_never_recreated(self):
        self.seed();loc=Path(self.policies[0]['local_root']);shutil.rmtree(loc)
        result=self.audit(hashes=True,final=True)
        self.assertTrue(result['local_history_optional_loss']);self.assertFalse(loc.exists())
        self.assertEqual(len(result['nfs_resume_candidates']),3)
        self.assertTrue(all(not p.startswith(str(loc)) for p in result['local_paths'].values()))

    def test_unexplained_hole_and_unverified_staging_fail_without_repair(self):
        self.seed();loc=Path(self.policies[0]['local_root']);pending=loc/'staging/partial';pending.mkdir()
        (pending/'payload').write_text('incomplete')
        result=self.audit(hashes=False,final=False);self.assertEqual(len(result['residues']),1)
        with self.assertRaisesRegex(ValueError,'residues'):self.audit(hashes=True,final=True)
        shutil.rmtree(pending)
        missing=loc/'certificates/ckpt_0000500.json';missing.unlink()
        with self.assertRaisesRegex(ValueError,'residues|history'):self.audit(hashes=True,final=True)
        self.assertFalse(missing.exists())

    def test_payload_corruption_and_crossed_certificate_identity_fail(self):
        self.seed();p=next((Path(self.policies[0]['local_root'])/'checkpoints').glob('*/__*.distcp'))
        raw=p.read_bytes();p.write_bytes(bytes([raw[0]^1])+raw[1:])
        with self.assertRaisesRegex(ValueError,'SHA'):self.audit(hashes=True,final=True)
        p.write_bytes(raw);cert=self.root/'checkpoint_certificates/ckpt_0004500.json';v=json.loads(cert.read_text())
        v['training_contract_id']='contract0';atomic_json(cert,v)
        with self.assertRaisesRegex(ValueError,'identity'):self.audit(hashes=True,final=True)

    def test_local_only_final_cannot_supply_nfs_resume(self):
        self.seed();name='ckpt_0004500';loc=Path(self.policies[1]['local_root'])
        shutil.move(str(self.root/'training'/name),str(loc/'checkpoints'/name))
        shutil.copy2(self.root/'checkpoint_certificates'/(name+'.json'),loc/'certificates'/(name+'.json'))
        result=self.audit(hashes=False,final=False)
        self.assertNotIn(name,result['nfs_resume_candidates']);self.assertIn(name,result['local'])
        with self.assertRaisesRegex(ValueError,'NFS'):self.audit(hashes=True,final=True)

    def test_wrong_global_manager_paths_and_missing_schedule_are_rejected(self):
        self.seed();state=json.loads((self.root/'global_retention_state.json').read_text())
        state['nfs_paths']['ckpt_0004500']='/tmp/false';atomic_json(self.root/'global_retention_state.json',state)
        with self.assertRaisesRegex(ValueError,'Manager|state differs'):self.audit(hashes=True,final=True)
        (self.source/'checkpoint_certificates/ckpt_0000500.json').unlink()
        with self.assertRaisesRegex(ValueError,'schedule'):self.audit(hashes=True,final=True)

    def test_final_requires_full_sha_and_snapshot_tracks_lost_namespace(self):
        self.seed()
        with self.assertRaisesRegex(ValueError,'full-SHA'):self.audit(hashes=False,final=True)
        snap=Snapshot();self.audit(hashes=True,final=True,snapshot=snap)
        p=Path(self.policies[0]['local_root']);shutil.rmtree(p)
        with self.assertRaisesRegex(ValueError,'regular immutable file'):snap.unchanged()

if __name__=='__main__':unittest.main()
