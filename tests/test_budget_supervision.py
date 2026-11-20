import json
from pathlib import Path
import tempfile
import unittest
from scripts.tiered_checkpoint_retention import inventory

class BudgetSupervisorTests(unittest.TestCase):
    def test_legacy_adoption_cannot_be_used_for_new_horizon(self):
        from scripts.supervise_budget_training import run
        with self.assertRaisesRegex(ValueError,'adoption is not yet validated'):
            run(Path('unused'),adoption='legacy-receipt')

    def test_new_sealer_certifies_full_cpu_state_and_rejects_old_target(self):
        import torch
        import torch.distributed.checkpoint as dcp
        from torch.distributed.checkpoint.state_dict import get_state_dict
        from scripts.supervise_budget_training import seal
        torch.set_num_threads(2)
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);cp=base/'ckpt_0000002'
            model=torch.nn.Linear(4,2);opt=torch.optim.AdamW(model.parameters())
            for _ in range(2):
                model(torch.ones(1,4)).sum().backward();opt.step();opt.zero_grad(set_to_none=True)
            msd,osd=get_state_dict(model,opt)
            dcp.save({'model':msd,'optim':osd},checkpoint_id=cp)
            for rank in range(8):torch.save({'cpu':torch.get_rng_state()},cp/f'rng_rank{rank}.pt')
            (cp/'.complete').touch()
            meta=dict(step=2,world_size=8,tokens_per_step=2097152,training_contract={'id':'x'},
                      config={'model':'3b','max_steps':57000})
            (cp/'meta.json').write_text(json.dumps(meta))
            with self.assertRaisesRegex(ValueError,'identity differs'):seal(cp,base/'proof.json','x')
            meta['config']['max_steps']=50000;(cp/'meta.json').write_text(json.dumps(meta))
            proof=seal(cp,base/'proof.json','x')
            inventory(cp,proof,hashes=True)
            self.assertEqual(proof['step'],2)
            # CPU fixture checks full DCP optimizer map; it is not a3B/8GPU run.
            bad=base/'ckpt_0000003';dcp.save({'model':msd},checkpoint_id=bad)
            (bad/'.complete').touch();meta['step']=3;(bad/'meta.json').write_text(json.dumps(meta))
            for rank in range(8):torch.save({'cpu':torch.get_rng_state()},bad/f'rng_rank{rank}.pt')
            with self.assertRaisesRegex(ValueError,'optimizer state missing'):seal(bad,base/'bad.json','x')


