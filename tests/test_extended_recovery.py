import json
import os
import signal
import subprocess
import sys
import time

import pytest

from scripts.horizon_process_handoff import process_record
from scripts.recover_extended_transition import PinnedRecovery, execute


@pytest.fixture
def processes():
    rows=[]
    def spawn():
        p=subprocess.Popen([sys.executable,'-c','import time;print("ready",flush=True);time.sleep(60)'],
                           stdout=subprocess.PIPE,text=True,start_new_session=True)
        assert p.stdout.readline().strip()=='ready';rows.append(p);return p
    yield spawn
    for p in rows:
        if p.poll() is None:p.kill()
        p.wait(timeout=5);p.stdout.close()


def stop(p):
    os.kill(p.pid,signal.SIGSTOP)
    deadline=time.monotonic()+3
    while process_record(p.pid)['state']!='T':
        assert time.monotonic()<deadline;time.sleep(.01)
    return process_record(p.pid)


def test_real_zombie_and_stopped_survivor(processes,tmp_path):
    dead,p,other=processes(),processes(),processes()
    dead_record=process_record(dead.pid);dead.kill()
    # Keep the child unreaped, reproducing the real rank0 condition.
    deadline=time.monotonic()+3
    while f') Z ' not in open(f'/proc/{dead.pid}/stat').read():
        assert time.monotonic()<deadline;time.sleep(.01)
    row=stop(p)
    with PinnedRecovery({'rank0':dead_record,'rank1':row},tmp_path/'signals') as b:
        b.terminate('rank0');b.terminate('rank1',timeout=2)
    assert p.wait(timeout=3)==-signal.SIGTERM and other.poll() is None
    events=[json.loads(s) for s in (tmp_path/'signals').read_text().splitlines()]
    assert not any(e.get('name')=='rank0' and e['event']=='signal_sent' for e in events)


def test_no_dying_proc_reads_after_term(processes,tmp_path,monkeypatch):
    p=processes();row=stop(p)
    import scripts.recover_extended_transition as mod
    real_signal=signal.pidfd_send_signal
    def send(fd,sig):
        result=real_signal(fd,sig)
        if sig==signal.SIGTERM:
            def gone(*a):raise FileNotFoundError('/proc/cwd disappeared')
            monkeypatch.setattr(mod,'process_record',gone)
        return result
    with PinnedRecovery({'rank1':row},tmp_path/'signals') as b:
        monkeypatch.setattr(mod.signal,'pidfd_send_signal',send)
        b.terminate('rank1',timeout=2)
    assert p.wait(timeout=3)==-signal.SIGTERM


def test_running_and_reused_identity_rejected(processes,tmp_path):
    p=processes();row=process_record(p.pid)
    with pytest.raises(ValueError,match='stopped'):PinnedRecovery({'rank':row},tmp_path/'signals')
    row=stop(p);row['start_ticks']+=1
    with pytest.raises(ValueError,match='reused'):PinnedRecovery({'rank':row},tmp_path/'signals')
    assert process_record(p.pid)['state']=='T' and not (tmp_path/'signals').exists()


def test_timeout_no_fallback_signal(processes,tmp_path,monkeypatch):
    p=processes();row=stop(p)
    with PinnedRecovery({'rank':row},tmp_path/'signals') as b:
        monkeypatch.setattr('scripts.recover_extended_transition.signal.pidfd_send_signal',lambda *a:None)
        with pytest.raises(TimeoutError):b.terminate('rank',timeout=.03)
    assert process_record(p.pid)['state']=='T'
    events=[json.loads(s) for s in (tmp_path/'signals').read_text().splitlines()]
    assert [e['signal'] for e in events if e['event']=='signal_sent']==[15,18]


def test_unexpected_descendant_blocks_signals(processes,tmp_path,monkeypatch):
    p=processes();row=stop(p)
    monkeypatch.setattr('scripts.recover_extended_transition.children',lambda pid:{999999999})
    with pytest.raises(ValueError,match='descendant'):PinnedRecovery({'rank':row},tmp_path/'signals')
    assert process_record(p.pid)['state']=='T' and not (tmp_path/'signals').exists()


def test_missing_authorization_precedes_context_or_mutation(tmp_path,monkeypatch):
    plan=tmp_path/'plan.json';plan.write_text('{}')
    monkeypatch.setattr('scripts.recover_extended_transition.context',lambda:pytest.fail('context must not run'))
    with pytest.raises(ValueError,match='authorization'):execute(plan)
    assert list(tmp_path.iterdir())==[plan]
