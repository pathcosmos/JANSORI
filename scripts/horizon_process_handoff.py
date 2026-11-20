"""PID-fd bound handoff primitives. Importing/capturing never signals a process.

The caller must arrange the certified checkpoint, protected copy, sealed ledger,
and diagnostic readiness. This module deliberately has no production CLI.
Signals address individual pidfds: torchrun ranks can have separate sessions.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import time


IDENTITY_FIELDS = ('pid', 'start_ticks', 'argv_sha256', 'cwd', 'uid')


def process_record(pid):
    if type(pid) is not int or pid <= 1 or pid == os.getpid():
        raise ValueError('Invalid/self process identity')
    root = Path('/proc') / str(pid)
    def stat():
        return (root/'stat').read_text().rsplit(')', 1)[1].split()
    first = stat()
    command = (root/'cmdline').read_bytes()
    result = dict(pid=pid, start_ticks=int(first[19]), state=first[0],
                  ppid=int(first[1]), pgid=int(first[2]), sid=int(first[3]),
                  argv_sha256=hashlib.sha256(command).hexdigest(),
                  argv=[s.decode() for s in command.split(b'\0') if s],
                  cwd=str((root/'cwd').resolve(strict=True)), uid=root.stat().st_uid)
    last = stat()
    if first[19] != last[19] or last[0] in ('Z', 'X'):
        raise ProcessLookupError('Process exited/reused while inspecting')
    return result


def children(pid):
    # Children may be attached to any thread, not only the group leader.
    found = set()
    for task in (Path('/proc')/str(pid)/'task').iterdir():
        try:
            found.update(int(v) for v in (task/'children').read_text().split())
        except FileNotFoundError:
            pass
    return found


def same_identity(expected, actual):
    return all(expected.get(k) == actual.get(k) for k in IDENTITY_FIELDS)


def capture_producer(root, supervisor_pid, worker_pid):
    """Read-only snapshot of the exact existing main producer and eight ranks."""
    root = Path(root).resolve(strict=True)
    records = dict(supervisor=process_record(supervisor_pid), worker=process_record(worker_pid))
    def argument(row, flag):
        args = row['argv']
        if args.count(flag) != 1 or args.index(flag)+1 == len(args):
            raise ValueError('Missing/ambiguous '+flag)
        return args[args.index(flag)+1]
    def command_path(row, flag):
        return (Path(row['cwd'])/argument(row, flag)).resolve(strict=True)
    supervisor = records['supervisor']
    if not any((Path(supervisor['cwd'])/a).resolve() == root.parents[1]/'scripts/supervise_main_training.py'
               for a in supervisor['argv']):
        raise ValueError('Unexpected supervisor entry')
    for row in records.values():
        if command_path(row, '--run-dir') != root:
            raise ValueError('Producer run path differs')
    worker = records['worker']
    if 'torch.distributed.run' not in worker['argv'] or argument(worker, '--module') != 'train.monitored':
        raise ValueError('Unexpected torchrun entry')
    attempt = argument(worker, '--attempt')
    if command_path(worker, '--config') != root/'main.json':
        raise ValueError('Worker config differs')
    pids = children(worker_pid)
    if len(pids) != 8:
        raise ValueError('Expected exactly eight direct ranks')
    for pid in pids:
        row = process_record(pid)
        # Never persist environment contents: inspect only LOCAL_RANK.
        env = (Path('/proc')/str(pid)/'environ').read_bytes().split(b'\0')
        ranks = [e.split(b'=', 1)[1] for e in env if e.startswith(b'LOCAL_RANK=')]
        if len(ranks) != 1 or not ranks[0].isdigit() or not 0 <= int(ranks[0]) < 8:
            raise ValueError('Missing/invalid LOCAL_RANK')
        name = 'rank'+str(int(ranks[0]))
        if (name in records or 'train.monitored' not in row['argv'] or
                argument(row, '--attempt') != attempt or command_path(row, '--run-dir') != root or
                command_path(row, '--config') != root/'main.json' or row['ppid'] != worker_pid or children(pid)):
            raise ValueError('Rank ownership/command/topology differs')
        records[name] = row
    for row in records.values():
        if not same_identity(row, process_record(row['pid'])):
            raise ValueError('Process changed during capture')
    return dict(status='observed_live_not_stopped', run=str(root), attempt=attempt, processes=records)


class BoundProcessSet:
    """Explicit signals through pinned kernel handles, with durable intent/result.

    Close never resumes or terminates anything. Partial failures are journaled;
    callers must inspect states and explicitly recover. No killpg/PID-only kill.
    """
    def __init__(self, records, journal):
        if not records or len({r['pid'] for r in records.values()}) != len(records):
            raise ValueError('Process identities must be nonempty/distinct')
        self.records, self.fds = records, {}
        self.journal = None
        try:
            self.journal = Path(journal).open('a')
            fcntl.flock(self.journal, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for name, row in records.items():
                fd = os.pidfd_open(row['pid'])
                self.fds[name] = fd
                if not same_identity(row, process_record(row['pid'])) or self.exited(name):
                    raise ValueError('Process identity changed before binding')
            self._event('bound', processes=records)
        except BaseException:
            self.close()
            raise

    def _event(self, event, **fields):
        self.journal.write(json.dumps(dict(time_ns=time.time_ns(), event=event, **fields))+'\n')
        self.journal.flush()
        os.fsync(self.journal.fileno())

    def exited(self, name):
        return bool(select.select([self.fds[name]], [], [], 0)[0])

    def states(self):
        result = {}
        for name, row in self.records.items():
            if self.exited(name):
                result[name] = 'exited'
                continue
            try:
                now = process_record(row['pid'])
            except (FileNotFoundError, ProcessLookupError):
                # /proc can disappear just before pidfd becomes readable.
                if select.select([self.fds[name]], [], [], .05)[0]:
                    result[name] = 'exited'
                    continue
                raise
            if not same_identity(row, now):
                # During exit, cmdline can become empty before zombie state.
                # Only the original pidfd can prove this was an actual exit.
                if select.select([self.fds[name]], [], [], .05)[0]:
                    result[name] = 'exited'
                    continue
                raise ValueError('Process command/identity changed')
            result[name] = now['state']
        return result

    def signal(self, names, sig):
        names = list(names)
        if sig not in (signal.SIGSTOP, signal.SIGCONT, signal.SIGTERM, signal.SIGKILL):
            raise ValueError('Unsupported handoff signal')
        if not names or len(set(names)) != len(names) or not set(names) <= self.fds.keys():
            raise ValueError('Unknown/duplicate process role')
        # Validate all selected identities before sending the first signal.
        states = self.states()
        if any(states[n] == 'exited' for n in names):
            raise ProcessLookupError('Selected bound process already exited')
        bound_pids = {row['pid'] for row in self.records.values()}
        for name, row in self.records.items():
            if states[name] != 'exited' and children(row['pid']) - bound_pids:
                raise ValueError('Unbound descendants appeared; recapture after settling')
        self._event('signal_intent', names=names, signal=int(sig), states=states)
        sent = []
        try:
            for name in names:
                if not same_identity(self.records[name], process_record(self.records[name]['pid'])):
                    raise ValueError('Process identity changed immediately before signal')
                signal.pidfd_send_signal(self.fds[name], sig)
                sent.append(name)
                self._event('signal_sent', name=name, signal=int(sig))
        except BaseException as exc:
            self._event('signal_partial_failure', sent=sent, error=repr(exc))
            raise

    def wait(self, names, *, stopped=False, timeout=5):
        names = list(names)
        if not names or not set(names) <= self.fds.keys() or not 0 < timeout <= 60:
            raise ValueError('Invalid bounded wait')
        deadline = time.monotonic()+timeout
        while True:
            states = self.states()
            ready = all(states[n] in ('T', 't') if stopped else states[n] == 'exited' for n in names)
            if ready:
                self._event('observed_stopped' if stopped else 'observed_exited', names=names, states=states)
                return states
            if time.monotonic() >= deadline:
                self._event('wait_timeout_not_terminal', names=names, states=states)
                raise TimeoutError('Bound process state not reached; no restart/extra signal')
            time.sleep(.02)

    def close(self):
        for fd in self.fds.values():
            os.close(fd)
        self.fds.clear()
        if self.journal is not None:
            self.journal.close()
            self.journal = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def unsaved_progress(checkpoint_step, last_logged_step, tokens_per_step):
    """A periodic log proves a lower bound, never the exact stopped commit tail."""
    if any(type(v) is not int or v < 0 for v in (checkpoint_step, last_logged_step, tokens_per_step)) or not tokens_per_step:
        raise ValueError('Invalid progress geometry')
    lower = max(0, last_logged_step-checkpoint_step)
    return dict(resume_step=checkpoint_step, last_logged_step=last_logged_step,
                discarded_updates_at_least=lower, discarded_tokens_at_least=lower*tokens_per_step,
                discarded_updates_upper_bound=None, exact_discarded_updates=None,
                reason='Periodic logs and checkpoint ledgers do not prove the in-memory commit tail')
