"""Bound one newly launched diagnostic command, including separate-session ranks.

Signals use pinned pidfds, never killpg or a saved PID alone. An inherited random
ownership tag also discovers ranks orphaned by a launcher exiting between polls.
Existing receipts are preserved; this helper does not automatically retry jobs.
"""
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import time
import uuid

from common.paths import PROJECT_ROOT
from scripts.tiered_checkpoint_retention import exact_path, atomic_json
from scripts.process_accounting import parse_rollup

OWNER_ENV = 'LLM_HORIZON_DIAGNOSTIC_OWNER'


def tagged_pids(token):
    marker=(OWNER_ENV+'='+token).encode()
    result=[]
    for path in Path('/proc').iterdir():
        if not path.name.isdigit() or int(path.name)==os.getpid():continue
        try:
            if path.stat().st_uid==os.getuid() and marker in (path/'environ').read_bytes().split(b'\0'):
                result.append(int(path.name))
        except (FileNotFoundError, ProcessLookupError, PermissionError):continue
    return result


class OwnedCommand:
    def __init__(self, command, directory, env):
        self.directory=exact_path(directory)
        if not self.directory.is_dir() or any(self.directory.iterdir()):
            raise ValueError('Use an empty owned command directory; preserve prior execution')
        self.token=uuid.uuid4().hex;self.command=list(command);self.fds={};self.records={}
        self.proc=None;self.started=time.monotonic();self.peak=0
        self.journal=(self.directory/'events.jsonl').open('x')
        launch_env=dict(env);launch_env[OWNER_ENV]=self.token
        self.event('launch_intent',command=self.command,owner=self.token)
        try:
            with (self.directory/'console.log').open('x') as stream:
                self.proc=subprocess.Popen(self.command,cwd=PROJECT_ROOT,env=launch_env,
                    stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
            self.bind(self.proc.pid,root=True)
            atomic_json(self.directory/'handle.json',dict(pid=self.proc.pid,command=self.command,
                owner=self.token,process=self.records[self.proc.pid]))
        except BaseException:
            if self.proc is not None:self.cleanup()
            self.close();raise

    def event(self, event, **fields):
        self.journal.write(json.dumps(dict(event=event,time_ns=time.time_ns(),**fields))+'\n')
        self.journal.flush();os.fsync(self.journal.fileno())

    def bind(self,pid,*,root=False):
        if pid in self.fds:return
        path=Path('/proc')/str(pid)
        def stamp():return int((path/'stat').read_text().rsplit(')',1)[1].split()[19])
        fd=None
        try:
            before=stamp();fd=os.pidfd_open(pid)
            if stamp()!=before:raise ValueError('Process reused while pinning diagnostic ownership')
            if not root and (OWNER_ENV+'='+self.token).encode() not in (path/'environ').read_bytes().split(b'\0'):
                return
            record=dict(pid=pid,start_ticks=before,launcher=root)
            self.fds[pid]=fd;self.records[pid]=record;fd=None
            self.event('process_bound',**record)
        except (FileNotFoundError,ProcessLookupError):
            if root:raise
        finally:
            if fd is not None:os.close(fd)

    def scan(self):
        for pid in tagged_pids(self.token):self.bind(pid)
        return [pid for pid,fd in self.fds.items() if not select.select([fd],[],[],0)[0]]

    def memory(self):
        total=0
        for pid in self.scan():
            try:total+=parse_rollup(Path(f'/proc/{pid}/smaps_rollup').read_text())['Pss']
            except (FileNotFoundError,ProcessLookupError):continue
        self.peak=max(self.peak,total)
        return total

    def send(self,pids,sig):
        self.event('signal_intent',pids=pids,signal=int(sig))
        for pid in pids:
            try:signal.pidfd_send_signal(self.fds[pid],sig)
            except ProcessLookupError:pass

    def cleanup(self):
        """Only this launch's handles/tag; terminate stopped or orphaned ranks too."""
        deadline=time.monotonic()+3;signalled=set()
        while True:
            live=self.scan()
            if not live:break
            new=set(live)-signalled
            if new:
                self.send(sorted(new),signal.SIGTERM)
                self.send(sorted(new),signal.SIGCONT)
                signalled.update(new)
            if time.monotonic()>=deadline:
                self.send(live,signal.SIGKILL);break
            if self.proc is not None:self.proc.poll()
            time.sleep(.05)
        deadline=time.monotonic()+3
        while self.scan():
            self.send(self.scan(),signal.SIGKILL)
            if time.monotonic()>=deadline:
                raise RuntimeError('Owned diagnostic processes did not exit; preserve handles for recovery')
            time.sleep(.05)
        if self.proc is not None:self.proc.wait(timeout=3)
        self.event('owned_processes_exited')

    def close(self):
        for fd in self.fds.values():os.close(fd)
        self.fds.clear()
        self.journal.close()

    def run(self,guard,*,interval=1):
        try:
            if not 0<interval<=5:raise ValueError('Unreviewed diagnostic observation interval')
            while True:
                self.memory()
                elapsed=time.monotonic()-self.started
                guard(self,elapsed,self.peak)
                rc=self.proc.poll()
                if rc is not None:
                    if rc:raise RuntimeError('Diagnostic command exited '+str(rc))
                    if self.scan():raise RuntimeError('Launcher exited with live owned descendants')
                    result=dict(status='command_exited_successfully',exit_code=rc,
                        launcher_pid=self.proc.pid,command=self.command,owner=self.token,
                        processes=list(self.records.values()),elapsed_seconds=elapsed,peak_pss_bytes=self.peak,
                        remaining_owned_processes=[])
                    atomic_json(self.directory/'result.json',result)
                    return result
                time.sleep(interval)
        except BaseException as exc:
            self.event('command_failed',error=repr(exc))
            try:self.cleanup()
            except BaseException as cleanup_error:
                self.event('cleanup_failed',error=repr(cleanup_error));raise
            atomic_json(self.directory/'failure.json',dict(error=repr(exc),owner=self.token,
                processes=list(self.records.values()),peak_pss_bytes=self.peak,
                elapsed_seconds=time.monotonic()-self.started,outputs_preserved=True))
            raise
        finally:self.close()


def run_command(command,directory,env,guard,*,interval=1):
    return OwnedCommand(command,directory,env).run(guard,interval=interval)
