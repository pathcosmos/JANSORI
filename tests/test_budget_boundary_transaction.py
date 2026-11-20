"""Actual separate-session CPU pidfd transitions; never signal production."""
from contextlib import contextmanager
import json
from pathlib import Path
import signal
import subprocess
import sys

import pytest
from scripts import budget_boundary_transaction as m
from scripts.horizon_process_handoff import process_record
from scripts.tiered_checkpoint_retention import atomic_json
from common.data_contracts import sha256_file


@pytest.fixture
def processes(tmp_path):
    children={}
    for role in [*sorted(m.ROLES),'observer','followup','unrelated']:
        p=subprocess.Popen([sys.executable,'-c','import time;print("ready",flush=True);time.sleep(90)'],
            stdout=subprocess.PIPE,text=True,start_new_session=True)
        assert p.stdout.readline().strip()=='ready';children[role]=p
    capture=dict(processes={n:process_record(p.pid) for n,p in children.items() if n in m.ROLES})
    observer={name:process_record(children[name].pid) for name in ['observer','followup']}
    yield children,capture,observer,tmp_path/'transaction'
    for p in children.values():
        if p.poll() is None:p.kill()
        p.wait(timeout=5);p.stdout.close()


def callbacks(e):
    children,capture,observer,out=e;calls=[]
    def precheck():
        assert all(children[n].poll() is None for n in [*m.ROLES,'observer','followup']);calls.append('precheck')
    @contextmanager
    def locked():
        assert process_record(children['supervisor'].pid)['state']=='T';calls.append('lock');yield
    def verify():
        assert all(process_record(children[n].pid)['state']=='T' for n in [*m.ROLES,'observer','followup'])
        calls.append('verified');return {'status':'verified_protected_parent','fixture_only':True}
    def after():
        for n in [*m.ROLES,'observer','followup']:assert children[n].wait(timeout=5)==-signal.SIGTERM
        calls.append('after');return {'fixture_stopped':True}
    return dict(precheck=precheck,retention_lock=locked,protect_saved=verify,after_stop=after),calls


def test_exact12_verified_before_termination_and_unrelated_preserved(processes):
    children,capture,observer,out=processes;kw,calls=callbacks(processes)
    result=m.stop_transaction(capture,observer,out,**kw)
    assert result=={'fixture_stopped':True} and calls==['precheck','lock','precheck','verified','after']
    assert children['unrelated'].poll() is None
    journal=[json.loads(s) for s in (out/'signals.jsonl').read_text().splitlines()]
    sent=[r for r in journal if r['event']=='signal_sent']
    assert not any(r['signal']==signal.SIGKILL for r in sent)
    terms=[r['name'] for r in sent if r['signal']==signal.SIGTERM]
    assert terms==['supervisor','observer','followup',*[f'rank{i}' for i in range(8)],'worker']
    assert json.loads((out/'report.json').read_text())['training_restarted'] is False


@pytest.mark.parametrize('fault',['before_pause','checkpoint_sha','unverified','after_stop','exit_timeout'])
def test_failure_preserves_evidence_and_never_restarts_or_adds_kill(processes,monkeypatch,fault):
    children,capture,observer,out=processes;kw,_=callbacks(processes)
    def fail(*a,**kw):raise RuntimeError('fixture failure')
    if fault=='before_pause':kw['precheck']=fail
    elif fault=='checkpoint_sha':kw['protect_saved']=fail
    elif fault=='unverified':kw['protect_saved']=lambda:{'status':'copy_started'}
    elif fault=='after_stop':kw['after_stop']=fail
    elif fault=='exit_timeout':monkeypatch.setattr(m,'terminate_stopped',fail)
    with pytest.raises((RuntimeError,ValueError)):m.stop_transaction(capture,observer,out,**kw)
    assert children['unrelated'].poll() is None
    if fault=='before_pause':
        assert not out.exists() and all(p.poll() is None for p in children.values())
    else:
        failure=json.loads((out/'failure.json').read_text());assert not failure['automatic_resume'] and not failure['automatic_restart']
        sent=[json.loads(s) for s in (out/'signals.jsonl').read_text().splitlines()]
        assert not any(r.get('signal')==signal.SIGKILL for r in sent)
        if fault!='after_stop':
            assert not any(r.get('signal')==signal.SIGTERM for r in sent)
            assert all(process_record(children[n].pid)['state']=='T' for n in [*m.ROLES,'observer','followup'])


def test_same_pid_wrong_identity_sends_no_signals(processes):
    children,capture,observer,out=processes;kw,_=callbacks(processes);observer['observer']['start_ticks']+=1
    with pytest.raises(ValueError):m.stop_transaction(capture,observer,out,**kw)
    assert all(p.poll() is None for p in children.values())
    assert 'signal_sent' not in (out/'signals.jsonl').read_text()

