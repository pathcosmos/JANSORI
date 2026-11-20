"""Real nine tiny process recovery; production checkpoint admission is separate."""
import json
from pathlib import Path
import signal
import subprocess
import sys

import pytest

from common.data_contracts import sha256_file
from scripts.horizon_process_handoff import BoundProcessSet,process_record
from scripts.recover_horizon_boundary import transaction,check_approval,SCOPE,execute


@pytest.fixture
def paused(tmp_path):
    procs={}
    for name in ['worker',*[f'rank{r}' for r in range(8)]]:
        p=subprocess.Popen([sys.executable,'-c','import time;print("ready",flush=True);time.sleep(60)'],
                           stdout=subprocess.PIPE,text=True,start_new_session=True)
        procs[name]=p;assert p.stdout.readline().strip()=='ready'
    rows={name:process_record(p.pid) for name,p in procs.items()}
    with BoundProcessSet(rows,tmp_path/'fixture_stop.jsonl') as bound:
        bound.signal(rows,signal.SIGSTOP);bound.wait(rows,stopped=True)
    yield procs,rows
    for p in procs.values():
        if p.poll() is None:p.kill()
        p.wait(timeout=5);p.stdout.close()


def test_real_nine_paused_processes_exit_after_parents_verified(paused,tmp_path):
    procs,rows=paused;calls=[]
    def precheck():
        assert all(process_record(p.pid)['state']=='T' for p in procs.values());calls.append('precheck')
    def verify():
        assert all(p.poll() is None for p in procs.values());calls.append('verified')
    def seal():
        assert all(p.wait(timeout=5)==-signal.SIGTERM for p in procs.values())
        calls.append('seal');return {'fixture_sealed':True}
    out=tmp_path/'recover'
    transaction(rows,out,authorized=True,precheck=precheck,verify_parents=verify,after_stop=seal,guard=lambda:None,
                execution_binding={'fixture_only':True})
    assert calls==['precheck','precheck','verified','seal']
    events=[json.loads(s) for s in (out/'signals.jsonl').read_text().splitlines()]
    assert [e['name'] for e in events if e.get('signal')==signal.SIGTERM and e['event']=='signal_sent']==[f'rank{r}' for r in range(8)]+['worker']
    assert not json.loads((out/'report.json').read_text())['launch_training']
    done=json.loads((out/'.done').read_text())
    assert done['execution_binding_sha256']==sha256_file(out/'execution_binding.json')


@pytest.mark.parametrize('failure',['unauthorized','precheck','copy','budget'])
def test_admission_failures_never_terminate(paused,tmp_path,failure):
    procs,rows=paused
    def fail():raise ValueError('fixture failure')
    out=tmp_path/'recover'
    with pytest.raises(ValueError):
        transaction(rows,out,authorized=failure!='unauthorized',precheck=fail if failure=='precheck' else lambda:None,
                    verify_parents=fail if failure=='copy' else lambda:None,after_stop=fail,
                    guard=fail if failure=='budget' else lambda:None)
    assert all(process_record(p.pid)['state']=='T' for p in procs.values())
    journal=out/'signals.jsonl'
    assert not journal.exists() or 'signal_sent' not in journal.read_text()
    if failure=='copy':
        assert (out/'failure.json').exists()
        with pytest.raises(ValueError,match='Preserve'):
            transaction(rows,out,authorized=True,precheck=lambda:None,verify_parents=lambda:None,after_stop=lambda:None,guard=lambda:None)


def test_failure_after_exit_keeps_evidence_without_completed_claim(paused,tmp_path):
    procs,rows=paused
    def fail():raise OSError('seal failed')
    out=tmp_path/'recover'
    with pytest.raises(OSError,match='seal failed'):
        transaction(rows,out,authorized=True,precheck=lambda:None,verify_parents=lambda:None,after_stop=fail,guard=lambda:None)
    assert all(p.wait(timeout=5)==-signal.SIGTERM for p in procs.values())
    assert (out/'signals.jsonl').exists() and (out/'failure.json').exists()
    assert not (out/'.done').exists()


def test_authorization_cannot_be_inferred_from_old_goal_or_template(tmp_path):
    p=tmp_path/'plan.json';p.write_text('{}')
    a=tmp_path/'approval.json'
    valid=dict(authorized=True,scope=SCOPE,plan_sha256=sha256_file(p),target_steps=35000,parent_step=8000,
               launch_training=False,user_decision='fixture explicit decision')
    for change in [dict(authorized=False),dict(plan_sha256='0'*64),dict(target_steps=57000),
                   dict(parent_step=7500),dict(launch_training=True),dict(user_decision='')]:
        a.write_text(json.dumps({**valid,**change}))
        with pytest.raises(ValueError,match='explicit new user decision'):
            execute(p,a)  # Fails before plan fields, production context, locks or signals.
    a.write_text(json.dumps(valid));assert check_approval(p,a)==valid


def test_selected_identity_change_refused_before_signal(paused,tmp_path):
    procs,rows=paused;rows['rank3']={**rows['rank3'],'start_ticks':rows['rank3']['start_ticks']+1}
    out=tmp_path/'recover'
    with pytest.raises(ValueError,match='identity changed'):
        transaction(rows,out,authorized=True,precheck=lambda:None,verify_parents=lambda:None,after_stop=lambda:None,guard=lambda:None)
    assert all(process_record(p.pid)['state']=='T' for p in procs.values())
    assert 'signal_sent' not in (out/'signals.jsonl').read_text()
