import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest

from scripts.horizon_probe_process import run_command, tagged_pids


class OwnedDiagnosticProcessTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory(prefix='owned-probe-fixture-')
        self.addCleanup(t.cleanup);self.root=Path(t.name)

    def command(self,code):return [sys.executable,'-c',code]
    def run_code(self,code,guard=None):
        return run_command(self.command(code),self.root,os.environ,
                           guard or (lambda *args:None),interval=.02)

    def test_fast_exit_has_durable_identity_and_success(self):
        result=self.run_code('print("fixture completed")')
        self.assertEqual(result['exit_code'],0)
        self.assertGreater(result['processes'][0]['start_ticks'],0)
        self.assertFalse(tagged_pids(result['owner']))
        self.assertIn('fixture completed',(self.root/'console.log').read_text())

    def test_nonzero_exit_preserves_failure_and_log(self):
        with self.assertRaisesRegex(RuntimeError,'exited 7'):
            self.run_code('import sys; print("failed fixture"); sys.exit(7)')
        self.assertTrue((self.root/'failure.json').exists())
        self.assertFalse((self.root/'result.json').exists())
        self.assertIn('failed fixture',(self.root/'console.log').read_text())

    def test_budget_failure_kills_separate_session_stubborn_child_only(self):
        unrelated=subprocess.Popen(self.command('import time; time.sleep(60)'))
        self.addCleanup(lambda:(unrelated.terminate(),unrelated.wait(timeout=5)))
        child='import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print("child-ready",flush=True); time.sleep(60)'
        code=f'import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c",{child!r}],start_new_session=True); print(p.pid,flush=True); time.sleep(60)'
        def guard(owned,elapsed,peak):
            if 'child-ready' in (self.root/'console.log').read_text():
                raise RuntimeError('fixture space budget exceeded')
            if elapsed>5:raise AssertionError('Child did not become ready')
        with self.assertRaisesRegex(RuntimeError,'space budget'):
            self.run_code(code,guard)
        result=json.loads((self.root/'failure.json').read_text())
        self.assertGreaterEqual(len(result['processes']),2)
        self.assertFalse(tagged_pids(result['owner']))
        self.assertIsNone(unrelated.poll())
        self.assertIn('signal_intent',(self.root/'events.jsonl').read_text())

    def test_orphan_after_launcher_success_is_detected_and_cleaned(self):
        child='import time; time.sleep(60)'
        code=f'import subprocess,sys; subprocess.Popen([sys.executable,"-c",{child!r}],start_new_session=True)'
        with self.assertRaisesRegex(RuntimeError,'live owned descendants'):
            self.run_code(code)
        result=json.loads((self.root/'failure.json').read_text())
        self.assertFalse(tagged_pids(result['owner']))

    def test_stopped_command_is_continued_and_terminated_on_timeout(self):
        stopped=False
        def guard(owned,elapsed,peak):
            nonlocal stopped
            if not stopped:
                signal.pidfd_send_signal(owned.fds[owned.proc.pid],signal.SIGSTOP);stopped=True
            if elapsed>.1:raise TimeoutError('fixture explicit runtime limit')
        with self.assertRaises(TimeoutError):self.run_code('import time; time.sleep(60)',guard)
        result=json.loads((self.root/'failure.json').read_text())
        self.assertFalse(tagged_pids(result['owner']))

    def test_prior_evidence_refused_without_overwrite_or_launch(self):
        (self.root/'keep').write_text('original')
        with self.assertRaisesRegex(ValueError,'preserve prior execution'):
            self.run_code('raise AssertionError("must not launch")')
        self.assertEqual(list(self.root.iterdir()),[self.root/'keep'])


if __name__=='__main__':unittest.main()
