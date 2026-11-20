from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import torch
import torch.distributed.checkpoint as dcp
from torch.distributed.checkpoint.state_dict import get_state_dict
from scripts.verify_horizon_probe import certify_checkpoint, compare_source_ledgers
from scripts.verify_final_resume_probe import compare_states, verify_inventory
from train.horizon_probe_observer import MODES


class HorizonCertificateTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(2)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.cp=self.root/'restored_parent'
        model=torch.nn.Linear(4,2);optimizer=torch.optim.AdamW(model.parameters())
        for _ in range(2):
            model(torch.ones(3,4)).sum().backward();optimizer.step();optimizer.zero_grad(set_to_none=True)
        msd,osd=get_state_dict(model,optimizer)
        dcp.save({'model':msd,'optim':osd},checkpoint_id=self.cp)
        torch.save({'cpu':torch.get_rng_state()},self.cp/'rng_rank0.pt')
        self.meta=dict(kind='horizon_restored_parent',step=7000,world_size=1,diagnostic_only=True,
                       plan_id='p'*64,parent_contract_id='a'*64,child_contract_id='b'*64)
        (self.cp/'meta.json').write_text(json.dumps(self.meta));(self.cp/'.complete').touch()

    def certify(self,**kw):
        return certify_checkpoint(self.cp,self.meta,self.root/'proof.json',cuda=False,**kw)

    def test_actual_cpu_full_state_sha_roundtrip_and_idempotent_certificate(self):
        proof=self.certify()
        verify_inventory(self.cp,proof,hashes=True)
        self.assertEqual(proof,self.certify())
        self.assertFalse(proof['actual8gpu_acceptance']);self.assertTrue(proof['diagnostic_only'])
        destination=self.root/'copy';shutil.copytree(self.cp,destination)
        comparison=compare_states(self.cp,destination,world=1)
        self.assertTrue(comparison['exact'])
        self.assertEqual(comparison['model_tensors'],2)
        self.assertEqual(comparison['optimizer_tensors'],6)

    def test_cpu_fixture_rejected_by_production_cuda_gate(self):
        with self.assertRaisesRegex(ValueError,'requires8ranks'):
            certify_checkpoint(self.cp,self.meta,self.root/'gpu-proof.json')

    def test_missing_rng_and_invalid_rng_type_refused(self):
        path=self.cp/'rng_rank0.pt';path.unlink()
        with self.assertRaisesRegex(ValueError,'completeness'):self.certify()
        torch.save({'cpu':torch.ones(4)},path)
        with self.assertRaisesRegex(ValueError,'RNG state'):self.certify()

    def test_model_only_is_not_a_resume_certificate(self):
        bad=self.root/'weights_only'
        dcp.save({'model':{'weight':torch.ones(2,4)}},checkpoint_id=bad)
        for name in ['.complete','meta.json','rng_rank0.pt']:shutil.copy2(self.cp/name,bad/name)
        with self.assertRaisesRegex(ValueError,'optimizer state missing'):
            certify_checkpoint(bad,self.meta,self.root/'bad.json',cuda=False)

    def test_fresh_verification_rejects_post_certificate_same_size_mutation(self):
        proof=self.certify()
        path=next(self.cp.glob('*.distcp'));raw=bytearray(path.read_bytes());raw[-1]^=1;path.write_bytes(raw)
        with self.assertRaises(ValueError):verify_inventory(self.cp,proof,hashes=True)
        with self.assertRaisesRegex(ValueError,'different existing certificate'):self.certify()


class HorizonSourceLedgerTests(unittest.TestCase):
    def rows(self):
        result=[]
        for mode,(begin,end) in MODES.items():
            for rank in range(8):
                result.append((mode,dict(rank=rank,success=True,contract_id='old' if mode=='baseline' else 'new',
                    optimizer_initializations=1,generations=[],
                    steps=[dict(step=7000+i,source_sequences={'ko':64}) for i in range(begin+1,end+1)])))
        return result

    def review(self,rows):
        return compare_source_ledgers(rows,parent_step=7000,parent_contract_id='old',child_contract_id='new',sources=['ko','en'])

    def test_all32_mode_rank_ledgers_and_diagnostic_token_accounting(self):
        result=self.review(self.rows())
        self.assertEqual(result['unique_diagnostic_tokens'],4194304)
        self.assertEqual(result['actual_diagnostic_tokens'],12582912)
        self.assertEqual(result['source_tokens'],{'ko':4194304,'en':0})
        self.assertEqual(result['excluded_optimizer_initializations'],32)
        self.assertEqual(result['main_committed_tokens_added'],0)
        self.assertFalse(result['actual8gpu_acceptance'])

    def test_missing_duplicate_contract_and_sampler_mismatch_refused(self):
        rows=self.rows()
        with self.assertRaisesRegex(ValueError,'Incomplete four-mode'):self.review(rows[:-1])
        with self.assertRaises(ValueError):self.review(rows+rows[:1])
        bad=deepcopy(rows);bad[0][1]['contract_id']='new'
        with self.assertRaisesRegex(ValueError,'contract/step'):self.review(bad)
        bad=deepcopy(rows);bad[-1][1]['steps'][0]['source_sequences']={'ko':63,'en':1}
        with self.assertRaisesRegex(ValueError,'sampling differs'):self.review(bad)

    def test_failed_rank_and_outside_boundary_refused(self):
        rows=self.rows();rows[-1][1]['success']=False
        with self.assertRaises(ValueError):self.review(rows)
        rows=self.rows();rows[-1][1]['steps'][0]['step']=7003
        with self.assertRaisesRegex(ValueError,'contract/step'):self.review(rows)


if __name__=='__main__':unittest.main()
