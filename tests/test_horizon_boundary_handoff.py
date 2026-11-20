"""Real small CPU process state-machine tests; never signal main training."""
from contextlib import contextmanager
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.horizon_process_handoff import process_record
from scripts.horizon_boundary_handoff import boundary_transaction, handoff, terminate_bound


class BoundaryTransactionTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory(prefix='boundary-fixture-');self.addCleanup(t.cleanup)
        self.root=Path(t.name);self.output=self.root/'transaction';self.processes={}
        for role in ['supervisor','worker',*[f'rank{r}' for r in range(8)]]:
            p=subprocess.Popen([sys.executable,'-c','import time; print("ready",flush=True); time.sleep(60)'],
                stdout=subprocess.PIPE,text=True,start_new_session=True)
            self.processes[role]=p
            self.assertEqual(p.stdout.readline().strip(),'ready')
        self.capture={'processes':{role:process_record(p.pid) for role,p in self.processes.items()}}
        self.addCleanup(self.reap)
        self.calls=[];self.prechecks=0

    def reap(self):
        for p in self.processes.values():
            if p.poll() is None:p.kill()
            p.wait(timeout=5);p.stdout.close()

    def precheck(self):
        self.prechecks+=1
        self.assertTrue(all(p.poll() is None for p in self.processes.values()))

    @contextmanager
    def lock(self):
        self.assertEqual(process_record(self.processes['supervisor'].pid)['state'],'T')
        self.calls.append('lock');yield

    def protect(self):
        self.assertEqual({process_record(p.pid)['state'] for p in self.processes.values()},{'T'})
        self.calls.append('protect')
        return {'status':'verified_protected_parent','synthetic_fixture_only':True}

    def after(self):
        self.assertTrue(all(p.wait(timeout=5)==-signal.SIGTERM for p in self.processes.values()))
        self.calls.append('after')
        return {'sealed_fixture':True}

    def run_transaction(self,**kwargs):
        return boundary_transaction(self.capture,self.output,
            **dict(precheck=self.precheck,retention_lock=self.lock,protect=self.protect,after_stop=self.after,**kwargs))

    def test_copy_verified_before_any_termination_then_all10_exit(self):
        self.run_transaction()
        self.assertEqual(self.prechecks,2)
        self.assertEqual(self.calls,['lock','protect','after'])
        self.assertEqual(json.loads((self.output/'state.json').read_text())['phase'],'handoff_sealed')
        rows=[json.loads(s) for s in (self.output/'signals.jsonl').read_text().splitlines()]
        terms=[r['name'] for r in rows if r['event']=='signal_sent' and r['signal']==signal.SIGTERM]
        self.assertEqual(terms[0],'supervisor');self.assertEqual(terms[-1],'worker');self.assertEqual(len(terms),10)

    def test_copy_failure_leaves_original_paused_and_never_terminates(self):
        def broken():raise OSError('fixture copy failed')
        with self.assertRaisesRegex(OSError,'copy failed'):
            boundary_transaction(self.capture,self.output,precheck=self.precheck,retention_lock=self.lock,
                                 protect=broken,after_stop=self.after)
        self.assertEqual({process_record(p.pid)['state'] for p in self.processes.values()},{'T'})
        rows=[json.loads(s) for s in (self.output/'signals.jsonl').read_text().splitlines()]
        self.assertFalse(any(r.get('signal') in (signal.SIGTERM,signal.SIGKILL) for r in rows))
        self.assertFalse(json.loads((self.output/'failure.json').read_text())['automatic_resume'])

    def test_unverified_copy_receipt_cannot_terminate_original(self):
        with self.assertRaisesRegex(ValueError,'No verified protected copy'):
            boundary_transaction(self.capture,self.output,precheck=self.precheck,retention_lock=self.lock,
                protect=lambda:{'status':'copy_started'},after_stop=self.after)
        self.assertTrue(all(p.poll() is None for p in self.processes.values()))

    def test_busy_retention_resumes_only_supervisor_then_freezes_before_lock(self):
        attempts=[]
        @contextmanager
        def busy():
            attempts.append(1)
            self.assertEqual({process_record(p.pid)['state'] for p in self.processes.values()},{'T'})
            if len(attempts)==1:raise BlockingIOError('fixture active retention')
            yield
        boundary_transaction(self.capture,self.output,precheck=self.precheck,retention_lock=busy,
                             protect=self.protect,after_stop=self.after)
        self.assertEqual(len(attempts),2)
        rows=[json.loads(s) for s in (self.output/'signals.jsonl').read_text().splitlines()]
        first_continue=next(r for r in rows if r['event']=='signal_sent' and r['signal']==signal.SIGCONT)
        self.assertEqual(first_continue['name'],'supervisor')

    def test_precheck_failure_sends_no_signal_and_existing_output_refused(self):
        def failed():raise ValueError('fixture bad input')
        with self.assertRaisesRegex(ValueError,'bad input'):
            boundary_transaction(self.capture,self.output,precheck=failed,retention_lock=self.lock,
                                 protect=self.protect,after_stop=self.after)
        self.assertFalse(self.output.exists())
        self.assertTrue(all(process_record(p.pid)['state']!='T' for p in self.processes.values()))
        self.output.mkdir();(self.output/'preserve').write_text('failure evidence')
        with self.assertRaisesRegex(ValueError,'Preserve prior'):self.run_transaction()
        self.assertEqual((self.output/'preserve').read_text(),'failure evidence')

    def test_post_stop_seal_failure_preserves_protected_receipt_and_no_restart(self):
        def failed():raise ValueError('fixture stale lineage')
        with self.assertRaisesRegex(ValueError,'stale lineage'):
            boundary_transaction(self.capture,self.output,precheck=self.precheck,retention_lock=self.lock,
                                 protect=self.protect,after_stop=failed)
        self.assertTrue(all(p.wait(timeout=5)==-signal.SIGTERM for p in self.processes.values()))
        self.assertEqual(json.loads((self.output/'failure.json').read_text())['phase'],'original_processes_exited')
        self.assertFalse((self.output/'report.json').exists())

    def test_unapproved_plan_rejected_before_any_source_inspection(self):
        path=self.root/'draft.json';path.write_text(json.dumps({'purpose':'horizon_preparation_not_executable'}))
        with self.assertRaisesRegex(ValueError,'Explicit bound35k'):handoff(path)
        self.assertTrue(all(p.poll() is None for p in self.processes.values()))


if __name__=='__main__':unittest.main()
