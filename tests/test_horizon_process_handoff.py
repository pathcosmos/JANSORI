import copy
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.horizon_process_handoff import BoundProcessSet, process_record, unsaved_progress


class ProcessHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.journal = Path(self.temp.name)/'signals.jsonl'
        self.procs = []

    def spawn(self, body='import time; print("ready", flush=True); time.sleep(60)'):
        p = subprocess.Popen([sys.executable, '-c', body], stdout=subprocess.PIPE, text=True,
                             start_new_session=True)
        self.procs.append(p)
        self.assertEqual(p.stdout.readline().strip(), 'ready')
        return p

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait(timeout=5)
            p.stdout.close()
        self.temp.cleanup()

    def test_actual_separate_sessions_pause_continue_terminate(self):
        a, b = self.spawn(), self.spawn()
        records = dict(worker=process_record(a.pid), rank0=process_record(b.pid))
        self.assertNotEqual(records['worker']['sid'], records['rank0']['sid'])
        with BoundProcessSet(records, self.journal) as bound:
            bound.signal(records, signal.SIGSTOP)
            bound.wait(records, stopped=True)
            bound.signal(records, signal.SIGCONT)
            bound.signal(records, signal.SIGTERM)
            bound.wait(records)
        for p in (a, b):
            self.assertEqual(p.wait(timeout=5), -signal.SIGTERM)
        events = [json.loads(s) for s in self.journal.read_text().splitlines()]
        self.assertEqual(sum(e['event'] == 'signal_sent' for e in events), 6)
        self.assertEqual(events[-1]['event'], 'observed_exited')

    def test_wrong_start_or_command_refused_before_signal(self):
        p = self.spawn()
        row = process_record(p.pid)
        for key, value in [('start_ticks', row['start_ticks']+1), ('argv_sha256', '0'*64)]:
            changed = copy.deepcopy(row); changed[key] = value
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                BoundProcessSet({'worker': changed}, self.journal)
            self.assertIsNone(p.poll())
            self.assertNotEqual(process_record(p.pid)['state'], 'T')
        self.assertEqual(self.journal.read_text(), '')

    def test_timeout_is_observation_only_and_close_does_not_signal(self):
        p = self.spawn()
        with BoundProcessSet({'worker': process_record(p.pid)}, self.journal) as bound:
            with self.assertRaises(TimeoutError):
                bound.wait(['worker'], stopped=True, timeout=.05)
        self.assertIsNone(p.poll())
        events = [json.loads(s)['event'] for s in self.journal.read_text().splitlines()]
        self.assertEqual(events, ['bound', 'wait_timeout_not_terminal'])

    def test_reopen_after_interruption_can_explicitly_resume(self):
        p = self.spawn(); records = {'worker': process_record(p.pid)}
        with BoundProcessSet(records, self.journal) as bound:
            bound.signal(records, signal.SIGSTOP); bound.wait(records, stopped=True)
        self.assertEqual(process_record(p.pid)['state'], 'T')
        with BoundProcessSet(records, self.journal) as bound:
            bound.signal(records, signal.SIGCONT)
            bound.signal(records, signal.SIGTERM); bound.wait(records)

    def test_exited_handle_cannot_signal_a_future_pid_owner(self):
        p = self.spawn(); records = {'worker': process_record(p.pid)}
        with BoundProcessSet(records, self.journal) as bound:
            p.terminate(); p.wait(timeout=5)
            self.assertTrue(bound.exited('worker'))
            with self.assertRaises(ProcessLookupError):
                bound.signal(records, signal.SIGTERM)
        self.assertNotIn('signal_sent', self.journal.read_text())

    def test_unbound_descendant_refused(self):
        body = ('import subprocess,sys,time; '
                'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
                'print("ready",flush=True); print(p.pid,flush=True); p.wait()')
        p = self.spawn(body)
        child_pid = int(p.stdout.readline())
        try:
            with BoundProcessSet({'worker': process_record(p.pid)}, self.journal) as bound:
                with self.assertRaisesRegex(ValueError, 'Unbound descendants'):
                    bound.signal(['worker'], signal.SIGSTOP)
            self.assertNotIn('signal_sent', self.journal.read_text())
        finally:
            # This child belongs only to this fixture and is pinned before kill.
            with BoundProcessSet({'child': process_record(child_pid)}, Path(self.temp.name)/'cleanup.jsonl') as bound:
                bound.signal(['child'], signal.SIGTERM); bound.wait(['child'])
            p.wait(timeout=5)

    def test_journal_lock_rejects_second_controller(self):
        p = self.spawn(); records = {'worker': process_record(p.pid)}
        with BoundProcessSet(records, self.journal):
            with self.assertRaises(BlockingIOError):
                BoundProcessSet(records, self.journal)

    def test_partial_signal_failure_is_recorded_without_implicit_resume(self):
        a, b = self.spawn(), self.spawn()
        records = dict(worker=process_record(a.pid), rank0=process_record(b.pid))
        real_send = signal.pidfd_send_signal
        with BoundProcessSet(records, self.journal) as bound:
            first_fd = bound.fds['worker']
            def send(fd, sig):
                if fd != first_fd:
                    raise OSError('injected second-handle failure')
                return real_send(fd, sig)
            with patch('scripts.horizon_process_handoff.signal.pidfd_send_signal', side_effect=send):
                with self.assertRaises(OSError):
                    bound.signal(records, signal.SIGSTOP)
            bound.wait(['worker'], stopped=True)
        events = [json.loads(s) for s in self.journal.read_text().splitlines()]
        failure = next(e for e in events if e['event'] == 'signal_partial_failure')
        self.assertEqual(failure['sent'], ['worker'])
        self.assertEqual(process_record(a.pid)['state'], 'T')
        self.assertNotEqual(process_record(b.pid)['state'], 'T')

    def test_live_command_mismatch_is_not_classified_as_exit(self):
        p = self.spawn(); row = process_record(p.pid)
        with BoundProcessSet({'worker': row}, self.journal) as bound:
            changed = dict(row, argv_sha256='0'*64)
            with patch('scripts.horizon_process_handoff.process_record', return_value=changed):
                with self.assertRaisesRegex(ValueError, 'command/identity changed'):
                    bound.states()
        self.assertIsNone(p.poll())

    def test_unknown_roles_and_duplicates_refused(self):
        p = self.spawn(); records = {'worker': process_record(p.pid)}
        with BoundProcessSet(records, self.journal) as bound:
            for names in ([], ['other'], ['worker', 'worker']):
                with self.assertRaises(ValueError):
                    bound.signal(names, signal.SIGTERM)
        self.assertIsNone(p.poll())

    def test_unsaved_progress_is_never_claimed_exact_from_periodic_log(self):
        report = unsaved_progress(7000, 7010, 2097152)
        self.assertEqual(report['discarded_updates_at_least'], 10)
        self.assertEqual(report['discarded_tokens_at_least'], 20971520)
        self.assertIsNone(report['exact_discarded_updates'])
        self.assertIsNone(report['discarded_updates_upper_bound'])
        self.assertIsNone(unsaved_progress(7000, 7000, 2097152)['exact_discarded_updates'])


if __name__ == '__main__':
    unittest.main()
