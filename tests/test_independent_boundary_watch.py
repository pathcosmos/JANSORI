"""Watcher decision timing only; process signalling is covered by boundary tests."""
import json
from pathlib import Path

import pytest
from common.data_contracts import sha256_file,resolve
from scripts.tiered_checkpoint_retention import atomic_json
from scripts import watch_independent_boundary as m


@pytest.fixture
def case(tmp_path,monkeypatch):
    root=tmp_path/'boundary';root.mkdir()
    plan=dict(purpose='own_nfs_independent_boundary_stop_v1',root=str(root),boundary_step=12500)
    atomic_json(root/'plan.json',plan);digest=sha256_file(root/'plan.json')
    atomic_json(root/'prepared.json',dict(plan_sha256=digest))
    auth=root/'authorization.json';atomic_json(auth,dict(source_stop_authorized=True,plan_sha256=digest,
        user_decision='synthetic fixture only',watcher_code_sha256=sha256_file(resolve(m.OWN)),
        watch_max_seconds=10800,watch_poll_seconds=30))
    clock=[0];calls=[];states=[dict(step=12490,certified=False)]
    monkeypatch.setattr(m.time,'monotonic',lambda:clock[0])
    def sleep(seconds):
        assert seconds==30;clock[0]+=seconds
        if len(states)>1:states.pop(0)
    monkeypatch.setattr(m.time,'sleep',sleep)
    monkeypatch.setattr(m,'observation',lambda _:states[0])
    monkeypatch.setattr(m,'execute',lambda *args:(calls.append(args) or {'fixture_stop':True}))
    monkeypatch.setattr(m,'record_event',lambda *args,**kwargs:None)
    return root,auth,states,clock,calls


def test_waits_for_certificate_then_executes_once(case):
    root,auth,states,clock,calls=case
    states[:]=[dict(step=12490,certified=False),dict(step=12500,certified=False),dict(step=12500,certified=True)]
    assert m.watch(root,auth)=={'fixture_stop':True}
    assert clock[0]==60 and len(calls)==1
    with pytest.raises(ValueError,match='existing watcher'):m.watch(root,auth)
    assert len(calls)==1


@pytest.mark.parametrize('fault',['missing','not_authorized','code','poll','boundary'])
def test_authorization_failures_create_no_output(case,fault):
    root,auth,_,_,calls=case;value=json.loads(auth.read_text())
    if fault=='missing':auth.unlink()
    else:
        key,val={'not_authorized':('source_stop_authorized',False),'code':('watcher_code_sha256','0'*64),
                 'poll':('watch_poll_seconds',1),'boundary':('plan_sha256','0'*64)}[fault]
        value[key]=val;atomic_json(auth,value)
    with pytest.raises((ValueError,FileNotFoundError)):m.watch(root,auth)
    assert not (root/'watch_v1').exists() and not calls


def test_passed_boundary_never_signals(case):
    root,auth,states,_,calls=case;states[:]=[dict(step=12510,certified=True)]
    with pytest.raises(RuntimeError,match='already passed'):m.watch(root,auth)
    assert not calls


def test_wait_timeout_never_signals(case,monkeypatch):
    root,auth,_,clock,calls=case
    monkeypatch.setattr(m.time,'sleep',lambda _:clock.__setitem__(0,10801))
    with pytest.raises(TimeoutError):m.watch(root,auth)
    assert not calls


def test_source_identity_failure_before_output(case,monkeypatch):
    root,auth,_,_,calls=case
    monkeypatch.setattr(m,'observation',lambda _:(_ for _ in ()).throw(ValueError('source identity changed')))
    with pytest.raises(ValueError,match='source identity'):m.watch(root,auth)
    assert not (root/'watch_v1').exists() and not calls


@pytest.mark.parametrize('fault',['plan','approval','boundary_failure'])
def test_changed_inputs_or_stop_failure_never_retries(case,monkeypatch,fault):
    root,auth,states,clock,calls=case
    if fault=='boundary_failure':
        states[:]=[dict(step=12500,certified=True)]
        def fail(*a):calls.append(a);raise RuntimeError('preserved stop failure')
        monkeypatch.setattr(m,'execute',fail)
    else:
        def mutate(_):
            p=root/'plan.json' if fault=='plan' else auth
            p.write_text(p.read_text()+' ');states[:]=[dict(step=12500,certified=True)]
        monkeypatch.setattr(m.time,'sleep',mutate)
    with pytest.raises((ValueError,RuntimeError)):m.watch(root,auth)
    assert len(calls)==(1 if fault=='boundary_failure' else 0)
    assert json.loads((root/'watch_v1/state.json').read_text())['phase']=='error'


def test_observation_ignores_incomplete_last_line_and_requires_exact_certificate(tmp_path,monkeypatch):
    main=tmp_path/'main';(main/'training').mkdir(parents=True);(main/'checkpoint_certificates').mkdir()
    (main/'training/log.jsonl').write_text('{"step":12500}\n{"step":12510')
    record={'pid':10};capture={'processes':{'supervisor':record,'worker':record}}
    plan=dict(main_run=str(main),source_capture=capture,observer_capture=record,boundary_step=12500)
    monkeypatch.setattr(m,'capture_extended_producer',lambda *a:capture)
    monkeypatch.setattr(m,'process_record',lambda _:record);monkeypatch.setattr(m,'same_identity',lambda *a:True)
    assert m.observation(plan)==dict(step=12500,certified=False)
    cert=main/'checkpoint_certificates/ckpt_0012500.json'
    atomic_json(cert,dict(step=12000,status='passed_main_checkpoint_storage'))
    assert not m.observation(plan)['certified']
    atomic_json(cert,dict(step=12500,status='passed_main_checkpoint_storage'))
    assert m.observation(plan)['certified']
