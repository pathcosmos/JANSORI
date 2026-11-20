import unittest
from unittest.mock import patch
from common.data_contracts import resolve
from scripts.observe_extended_training import process_identity,admit_source,summarize

class ExtendedObserverTests(unittest.TestCase):
    def setUp(self):
        self.root=resolve('runs/3b_full_57k_v2')
        self.row=dict(pid=12345,start_ticks=456,argv_sha256='a'*64,state='S',cwd=str(resolve('.')),
            argv=['python','scripts/supervise_extended_training.py','--run-dir',str(self.root),'--run'])

    def test_exact_pid_identity_and_command_scope(self):
        with patch('scripts.observe_extended_training.process_record',return_value=self.row):
            self.assertEqual(process_identity(12345,'supervisor',self.root)['start_ticks'],456)
            self.assertIsNone(process_identity(12345,'supervisor',resolve('runs/other')))
            self.row['argv'][1]='scripts/supervise_horizon_training.py'
            self.assertIsNone(process_identity(12345,'supervisor',self.root))
            self.row['argv']=['python','-m','torch.distributed.run','--module','train.monitored_extended_horizon','--run-dir',str(self.root)]
            self.assertEqual(process_identity(12345,'worker',self.root)['pid'],12345)
            self.row['argv'][4]='train.monitored_horizon'
            self.assertIsNone(process_identity(12345,'worker',self.root))
        with patch('scripts.observe_extended_training.process_record',side_effect=ProcessLookupError):
            self.assertIsNone(process_identity(12345,'worker',self.root))

    def test_scope_budget_and_actual_handoff_admission_required(self):
        out=resolve('runs/3b_57k_observer_v2');cfg=dict(max_steps=57000)
        c=dict(purpose='main_training',main_training_authorized=True,main_run=str(self.root),
               shared_storage={'nfs_pools':{'observer':{'paths':[str(out)],'budget_bytes':2**30}}})
        with patch('train.extended_handoff.validate_configuration') as config,patch('train.extended_handoff.load_handoff') as gate:
            admit_source(self.root,out,c,cfg);config.assert_called_once();gate.assert_called_once()
            gate.side_effect=ValueError('Missing actual GPU proof')
            with self.assertRaisesRegex(ValueError,'actual GPU'):admit_source(self.root,out,c,cfg)
            c['shared_storage']['nfs_pools']['observer']['paths'].append(str(self.root))
            with self.assertRaisesRegex(ValueError,'reserved'):admit_source(self.root,out,c,cfg)

    def test_absolute_step_and_compute_eta_on_restored_suffix(self):
        cfg=dict(max_steps=57000,global_tokens=2097152,log_every=10,eval_every=250,ckpt_every=500)
        rows=[dict(step=s,loss=2.,gnorm=.2,lr=.0003,tok_s=140000.,tokens_b=s*2097152/1e9) for s in (10010,10020,10030,10040)]
        result=summarize(rows,cfg)
        self.assertEqual(result['logged_step'],10040)
        self.assertAlmostEqual(result['remaining_compute_seconds'],(57000-10040)*2097152/140000)
        self.assertEqual(result['train_records'],4)
