"""Actual separate-session CPU pidfd transitions; never signal production."""
from contextlib import contextmanager
import json
from pathlib import Path
import signal
import subprocess
import sys

import pytest
from scripts import independent_boundary_handoff as m
from scripts.horizon_process_handoff import process_record
from scripts.tiered_checkpoint_retention import atomic_json
from common.data_contracts import sha256_file
from test_independent_checkpoint_handoff import case
from test_restore_final_storage import setup
from test_independent_observer_handoff import handoff


@pytest.fixture
def processes(tmp_path):
    children={}
    for role in [*sorted(m.ROLES),'observer','unrelated']:
        p=subprocess.Popen([sys.executable,'-c','import time;print("ready",flush=True);time.sleep(90)'],
            stdout=subprocess.PIPE,text=True,start_new_session=True)
        assert p.stdout.readline().strip()=='ready';children[role]=p
    capture=dict(processes={n:process_record(p.pid) for n,p in children.items() if n in m.ROLES})
    observer=process_record(children['observer'].pid)
    yield children,capture,observer,tmp_path/'transaction'
    for p in children.values():
        if p.poll() is None:p.kill()
        p.wait(timeout=5);p.stdout.close()


def callbacks(e):
    children,capture,observer,out=e;calls=[]
    def precheck():
        assert all(children[n].poll() is None for n in [*m.ROLES,'observer']);calls.append('precheck')
    @contextmanager
    def locked():
        assert process_record(children['supervisor'].pid)['state']=='T';calls.append('lock');yield
    def verify():
        assert all(process_record(children[n].pid)['state']=='T' for n in [*m.ROLES,'observer'])
        calls.append('verified');return {'status':'verified_own_nfs_boundary','fixture_only':True}
    def after():
        for n in [*m.ROLES,'observer']:assert children[n].wait(timeout=5)==-signal.SIGTERM
        calls.append('after');return {'fixture_stopped':True}
    return dict(precheck=precheck,retention_lock=locked,verify_saved=verify,after_stop=after),calls


def test_exact11_verified_before_termination_and_unrelated_preserved(processes):
    children,capture,observer,out=processes;kw,calls=callbacks(processes)
    result=m.stop_transaction(capture,observer,out,**kw)
    assert result=={'fixture_stopped':True} and calls==['precheck','lock','precheck','verified','after']
    assert children['unrelated'].poll() is None
    journal=[json.loads(s) for s in (out/'signals.jsonl').read_text().splitlines()]
    sent=[r for r in journal if r['event']=='signal_sent']
    assert not any(r['signal']==signal.SIGKILL for r in sent)
    terms=[r['name'] for r in sent if r['signal']==signal.SIGTERM]
    assert terms==['supervisor','observer',*[f'rank{i}' for i in range(8)],'worker']
    assert json.loads((out/'report.json').read_text())['training_restarted'] is False


@pytest.mark.parametrize('fault',['before_pause','checkpoint_sha','unverified','after_stop','exit_timeout'])
def test_failure_preserves_evidence_and_never_restarts_or_adds_kill(processes,monkeypatch,fault):
    children,capture,observer,out=processes;kw,_=callbacks(processes)
    def fail(*a,**kw):raise RuntimeError('fixture failure')
    if fault=='before_pause':kw['precheck']=fail
    elif fault=='checkpoint_sha':kw['verify_saved']=fail
    elif fault=='unverified':kw['verify_saved']=lambda:{'status':'copy_started'}
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
            assert all(process_record(children[n].pid)['state']=='T' for n in [*m.ROLES,'observer'])


def test_same_pid_wrong_identity_sends_no_signals(processes):
    children,capture,observer,out=processes;kw,_=callbacks(processes);observer['start_ticks']+=1
    with pytest.raises(ValueError):m.stop_transaction(capture,observer,out,**kw)
    assert all(p.poll() is None for p in children.values())
    assert 'signal_sent' not in (out/'signals.jsonl').read_text()


def test_real_prepare_is_read_only_and_explicit_authorization_required(handoff,monkeypatch):
    monkeypatch.setattr(m,'capacity',lambda *a:{'nested_tiny_fixture_not_another_real_pool':True})
    e=handoff;main=e['c'].root;out=e['pool']/'boundary';before=(main/'contract.json').read_bytes()
    plan=m.prepare(e['root'],out,11000)
    assert not plan['source_stop_authorized'] and (main/'contract.json').read_bytes()==before
    with pytest.raises(FileNotFoundError):m.execute(out,out/'authorization.json')
    assert not (out/'transaction').exists()
    with pytest.raises(ValueError):m.prepare(e['root'],out,11000)


@pytest.mark.parametrize('step',[10000,11001,57000,True])
def test_unreviewed_boundary_never_creates_plan(handoff,step):
    e=handoff;out=e['pool']/'boundary'
    with pytest.raises(ValueError):m.prepare(e['root'],out,step)
    assert not out.exists()


@pytest.mark.parametrize('fault',['none','logged_ahead','payload_corrupt'])
def test_wrapper_uses_real_nfs_sha_boundary_and_stopped_observer_freeze(setup,monkeypatch,fault):
    # Real metadata, inventory/SHA and locks; the11-process state machine is
    # separately exercised above with actual CPU children. Never GPU claims.
    c=setup['c'];c.contract['max_seconds']=1000000
    definitions=[]
    for run,local in zip([c.ancestor,c.source,c.root],c.contract['shared_storage']['local_history_paths']):
        for p in [run/'retention_trash',Path(local)/'staging',Path(local)/'retention_trash']:p.mkdir(parents=True,exist_ok=True)
        (run/'retention.lock').touch();definitions.append(dict(run=str(run),policy={'local_root':local}))
    c.contract['retention_branches']=definitions
    e=handoff.__wrapped__(setup);out=e['pool']/'boundary'
    monkeypatch.setattr(m,'capacity',lambda *a:{'fixture_only':True})
    m.prepare(e['root'],out,10500)
    auth=out/'authorization.json';atomic_json(auth,dict(source_stop_authorized=True,
        plan_sha256=sha256_file(out/'plan.json'),user_decision='CPU fixture exact stop authorization'))
    atomic_json(c.root/'state.json',dict(phase='running',elapsed_seconds=30))
    monkeypatch.setattr(m,'capture_extended_producer',lambda *a:e['capture'])
    monkeypatch.setattr(m,'process_record',lambda pid:e['observer'])
    if fault=='logged_ahead':(c.root/'training/log.jsonl').write_text('{"step":10510}\n')
    if fault=='payload_corrupt':
        p=c.root/'training/ckpt_0010500/__0_0.distcp';p.write_bytes(b'x'*p.stat().st_size)
    saved=[]
    def transaction(capture,observer,output,*,precheck,retention_lock,verify_saved,after_stop):
        precheck();output.mkdir()
        with retention_lock():precheck();saved.append(verify_saved())
        return after_stop()
    monkeypatch.setattr(m,'stop_transaction',transaction)
    if fault=='none':
        result=m.execute(out,auth)
        assert result['ready_for_storage_restoration'] and not result['training_restarted']
        assert saved[0]['fresh_payload_sha_verified'] and saved[0]['source_elapsed_seconds']==30
        assert saved[0]['elapsed_before_seconds_at_verification']>=30
        assert (e['root']/'observer_handoff/frozen.json').exists()
    else:
        with pytest.raises(ValueError):m.execute(out,auth)
        assert not saved and not (e['root']/'observer_handoff').exists()
