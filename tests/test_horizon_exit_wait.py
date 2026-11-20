import json
import signal
import subprocess
import sys
import time

import pytest

from scripts.horizon_process_handoff import BoundProcessSet, process_record
from scripts.horizon_exit_wait import wait_exit_only, terminate_stopped


@pytest.fixture
def children():
    processes = []
    def spawn(body='import time;print("ready",flush=True);time.sleep(60)'):
        p = subprocess.Popen([sys.executable,'-c',body],stdout=subprocess.PIPE,text=True,start_new_session=True)
        processes.append(p)
        assert p.stdout.readline().strip() == 'ready'
        return p
    yield spawn
    for p in processes:
        if p.poll() is None:p.kill()
        p.wait(timeout=5);p.stdout.close()


def test_real_exit_observation_ignores_transient_cmdline(children,tmp_path,monkeypatch):
    p=children('import time,signal,sys\ndef end(*a):\n print("ending",flush=True);time.sleep(.2);sys.exit(0)\nsignal.signal(signal.SIGTERM,end)\nprint("ready",flush=True)\ntime.sleep(60)')
    with BoundProcessSet({'worker':process_record(p.pid)},tmp_path/'journal') as bound:
        bound.signal(['worker'],signal.SIGTERM)
        assert p.stdout.readline().strip()=='ending'
        def dying_cmdline(*args):raise ValueError('Process command/identity changed')
        monkeypatch.setattr('scripts.horizon_process_handoff.process_record',dying_cmdline)
        with pytest.raises(ValueError):bound.wait(['worker'])
        assert wait_exit_only(bound,['worker'],timeout=2)=={'worker':'exited'}
    assert p.wait(timeout=5)==0


def test_timeout_preserves_paused_worker_and_unrelated_process(children,tmp_path):
    p,other=children(),children()
    with BoundProcessSet({'worker':process_record(p.pid)},tmp_path/'journal') as bound:
        bound.signal(['worker'],signal.SIGSTOP);bound.wait(['worker'],stopped=True)
        with pytest.raises(TimeoutError):wait_exit_only(bound,['worker'],timeout=.05)
        assert process_record(p.pid)['state']=='T' and other.poll() is None
    events=[json.loads(x) for x in (tmp_path/'journal').read_text().splitlines()]
    assert [x['signal'] for x in events if x['event']=='signal_sent']==[signal.SIGSTOP]


def test_actual_stopped_separate_sessions_terminate_in_order(children,tmp_path):
    a,b,other=children(),children(),children()
    records={name:process_record(p.pid) for name,p in [('rank0',a),('worker',b)]}
    with BoundProcessSet(records,tmp_path/'journal') as bound:
        bound.signal(records,signal.SIGSTOP);bound.wait(records,stopped=True)
        for name in records:terminate_stopped(bound,name,grace_seconds=2)
        assert wait_exit_only(bound,records,timeout=1)==dict.fromkeys(records,'exited')
    assert other.poll() is None
    assert a.wait(timeout=5)==b.wait(timeout=5)==-signal.SIGTERM


def test_running_worker_is_not_implicitly_terminated(children,tmp_path):
    p=children()
    with BoundProcessSet({'worker':process_record(p.pid)},tmp_path/'journal') as bound:
        with pytest.raises(ValueError):terminate_stopped(bound,'worker')
        for names,timeout in [([],1),(['other'],1),(['worker','worker'],1),(['worker'],0)]:
            with pytest.raises(ValueError):wait_exit_only(bound,names,timeout=timeout)
    assert p.poll() is None and 'signal_sent' not in (tmp_path/'journal').read_text()
