"""Read-only waiter admission and no-marker-only boundary selection."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts.run_extended_transition import ready,run

class ExtendedTransitionWaitTests(unittest.TestCase):
    def test_waits_for_certificate_and_settled_three_checkpoint_state(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);cp=root/'training/ckpt_0010000';cp.mkdir(parents=True);(cp/'.complete').touch()
            self.assertFalse(ready(root))
            cert=root/'checkpoint_certificates/ckpt_0010000.json';cert.parent.mkdir();cert.write_text('{}')
            self.assertFalse(ready(root))
            state=root/'global_retention_state.json'
            value=dict(nfs_checkpoints=['ckpt_0009000','ckpt_0009500','ckpt_0010000'],uncertified_nfs=[],local_checkpoints=[])
            state.write_text(json.dumps(value))
            with patch('scripts.run_extended_transition.inventory',side_effect=ValueError('invalid payload')):
                with self.assertRaisesRegex(ValueError,'invalid payload'):ready(root)
            with patch('scripts.run_extended_transition.inventory') as check:
                self.assertTrue(ready(root));check.assert_called_once()
                for key,val in [('uncertified_nfs',['ckpt_0010000']),('local_checkpoints',['x']*16),('nfs_checkpoints',[])]:
                    state.write_text(json.dumps(dict(value,**{key:val})));self.assertFalse(ready(root))

    def test_unapproved_waiter_cannot_signal_or_launch(self):
        with tempfile.TemporaryDirectory() as name:
            p=Path(name)/'plan.json';p.write_text('{}')
            with patch('scripts.run_extended_transition.handoff') as stop,patch('scripts.run_extended_transition.subprocess.Popen') as launch:
                with self.assertRaises(ValueError):run(p)
                stop.assert_not_called();launch.assert_not_called()
