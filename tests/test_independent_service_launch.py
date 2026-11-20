"""Real CPU service ownership/lifetime tests; runtime/GPU admission is mocked."""
import fcntl
import json
import os
import sys
from types import SimpleNamespace

import pytest

from scripts import launch_independent_services as m
from scripts.tiered_checkpoint_retention import atomic_json


@pytest.fixture
def owned(monkeypatch):
    created=[];original=m.OwnedCommand
    def factory(*args):
        child=original(*args);created.append(child);return child
    monkeypatch.setattr(m,'OwnedCommand',factory)
    yield created
    # Tests terminate only their own still-pinned CPU children. The launcher
    # deliberately detached tracking handles without terminating the service.
    for child in created:
        if child.journal.closed:child.journal=(child.directory/'events.jsonl').open('a')
        child.cleanup();child.close()


def command(label='service'):
    return [sys.executable,'-u','-c','import time; time.sleep(120)',label]


def test_slow_start_repeat_inspects_same_live_process(tmp_path,owned):
    path=tmp_path/'service';cmd=command();binding={'runtime':'fixture'}
    first=m.launch_once(path,cmd,dict(os.environ),binding)
    second=m.launch_once(path,cmd,dict(os.environ),binding)
    assert first['process']['pid']==second['process']['pid']
    assert second['status']=='existing_exact_service_live' and len(owned)==1
    assert owned[0].proc.poll() is None and owned[0].journal.closed


@pytest.mark.parametrize('change',['command','binding','start_ticks','owner'])
def test_changed_identity_never_adopts_or_relaunches(tmp_path,owned,change):
    path=tmp_path/'service';cmd=command();binding={'runtime':'fixture'}
    m.launch_once(path,cmd,dict(os.environ),binding)
    if change=='command':cmd=command('different')
    elif change=='binding':binding={'runtime':'other'}
    else:
        p=path/'owned/handle.json';handle=json.loads(p.read_text())
        if change=='start_ticks':handle['process']['start_ticks']+=1
        else:handle['owner']='0'*32
        atomic_json(p,handle)
    with pytest.raises(ValueError):m.launch_once(path,cmd,dict(os.environ),binding)
    assert len(owned)==1 and owned[0].proc.poll() is None


def test_exited_service_requires_review_not_restart(tmp_path,owned):
    path=tmp_path/'service';cmd=command();binding={}
    m.launch_once(path,cmd,dict(os.environ),binding)
    owned[0].proc.terminate();owned[0].proc.wait(timeout=5)
    with pytest.raises(ValueError,match='exited'):
        m.launch_once(path,cmd,dict(os.environ),binding)
    assert len(owned)==1


def test_missing_handle_preserves_tagged_process_and_intent(tmp_path,owned):
    path=tmp_path/'service';cmd=command();binding={}
    first=m.launch_once(path,cmd,dict(os.environ),binding)
    (path/'owned/handle.json').unlink()
    with pytest.raises(ValueError,match=str(first['process']['pid'])):
        m.launch_once(path,cmd,dict(os.environ),binding)
    assert len(owned)==1 and owned[0].proc.poll() is None


@pytest.fixture
def services(tmp_path,monkeypatch,owned):
    control=tmp_path/'control';control.mkdir();main=tmp_path/'main';main.mkdir()
    observation=dict(phase='await_independent_execution',consumption=None,processes=None)
    view=SimpleNamespace(control=control,main=main,digest='fixture',binding={},observe=lambda:dict(observation))
    commands={name:command(name) for name in ['supervisor','observer']}
    monkeypatch.setattr(m,'admitted',lambda _: (view,commands,dict(os.environ)))
    monkeypatch.setattr(m,'require_absent',lambda *args:None)
    return view,commands,observation


def test_two_services_start_once_no_restart_during_slow_import(services,owned):
    view,commands,observation=services
    first=m.start(view.control);second=m.start(view.control)
    assert len(owned)==2 and not first['actual_all8_consumed_verified']
    for role in commands:assert first[role]['process']['pid']==second[role]['process']['pid']
    result=m.status(view.control)
    assert result['status']=='live_services_runtime_startup_pending'
    assert not result['goal_complete'] and not result['automatic_restart']
    env=open(f'/proc/{second["observer"]["process"]["pid"]}/environ','rb').read().split(b'\0')
    assert b'CUDA_VISIBLE_DEVICES=' in env


def test_live_main_controller_lock_blocks_before_any_child(services,owned):
    view,_,_=services
    with (view.main/'controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):m.start(view.control)
    assert not owned and not (view.control/'services/supervisor').exists()


def test_observer_launch_failure_preserves_supervisor_without_relaunch(services,owned,monkeypatch):
    view,_,_=services;factory=m.OwnedCommand
    def failure(command,directory,env):
        if command[-1]=='observer':raise OSError('fixture launch failure')
        return factory(command,directory,env)
    monkeypatch.setattr(m,'OwnedCommand',failure)
    with pytest.raises(OSError,match='fixture launch failure'):m.start(view.control)
    assert len(owned)==1 and owned[0].proc.poll() is None
    with pytest.raises(ValueError,match='Unfinished service launch'):m.start(view.control)
    assert len(owned)==1 and owned[0].proc.poll() is None
    assert (view.control/'services/observer/intent.json').is_file()


@pytest.mark.parametrize('fault',['main_error','control_error','missing_rank','missing_launcher','supervisor_mismatch','none'])
def test_runtime_errors_not_reported_as_healthy_startup(services,owned,fault):
    view,_,observation=services;started=m.start(view.control)
    observation.update(phase='running',consumption={'fixture_only':True},processes={
        'all8_consumed_live':True,'launcher':{'pid':123},
        'supervisor':{'pid':started['supervisor']['process']['pid']}})
    if fault=='main_error':observation['phase']='error'
    elif fault=='control_error':atomic_json(view.control/'state.json',dict(phase='error'))
    elif fault=='missing_rank':observation['processes']['all8_consumed_live']=False
    elif fault=='missing_launcher':observation['processes']['launcher']=None
    elif fault=='supervisor_mismatch':observation['processes']['supervisor']['pid']+=1
    if fault=='supervisor_mismatch':
        with pytest.raises(ValueError,match='supervisor differs'):m.status(view.control)
    else:
        result=m.status(view.control)
        assert result['status']==('all8_consumed_live' if fault=='none' else 'runtime_error_review_required')
        assert not result['goal_complete']
    assert len(owned)==2 and all(child.proc.poll() is None for child in owned)


def test_missing_published_runtime_creates_no_services(tmp_path):
    control=tmp_path/'unpublished';control.mkdir()
    with pytest.raises((ValueError,FileNotFoundError)):m.start(control)
    assert list(control.iterdir())==[]


def test_prepared_final_manager_launches_once_after_services(services,owned,monkeypatch):
    view,_,_=services
    root=view.control/'followup_v1';root.mkdir();atomic_json(root/'plan.json',{'fixture':True})
    monkeypatch.setattr('scripts.run_independent_followup.load',lambda _: (view,root,{}))
    monkeypatch.setattr(m,'followup_command',lambda _:command('followup'))
    with pytest.raises(ValueError,match='not been launched'):m.start_followup(view.control)
    m.start(view.control)
    first=m.start_followup(view.control);second=m.start_followup(view.control)
    assert first['process']['pid']==second['process']['pid'] and len(owned)==3
    atomic_json(root/'plan.json',{'changed':True})
    with pytest.raises(ValueError,match='another runtime'):m.start_followup(view.control)
    assert len(owned)==3 and all(c.proc.poll() is None for c in owned)
