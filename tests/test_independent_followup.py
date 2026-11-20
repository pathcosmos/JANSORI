"""Real CPU orchestration/exit tests; no model evaluation or GPU success claims."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from common.data_contracts import sha256_file,resolve
from scripts.tiered_checkpoint_retention import atomic_json
from scripts import run_independent_followup as m


@pytest.fixture
def owned(monkeypatch):
    created=[];original=m.OwnedCommand
    def factory(*args):
        child=original(*args);created.append(child);return child
    monkeypatch.setattr(m,'OwnedCommand',factory)
    yield created
    for child in created:
        if child.journal.closed:child.journal=(child.directory/'events.jsonl').open('a')
        child.cleanup();child.close()


def row(tmp_path,code=None,name='stage'):
    output=tmp_path/(name+'.txt')
    return dict(name=name,output=str(output),command=[sys.executable,'-u','-c',
        code or 'import pathlib,sys;pathlib.Path(sys.argv[1]).write_text("verified fixture")',str(output)])


def verify(r):return dict(output_sha256=sha256_file(Path(r['output'])))


def test_success_and_repeat_verifies_content_without_respawn(tmp_path,owned):
    r=row(tmp_path);first=m.phase(tmp_path,r,lambda:None,verify=verify,poll=.01)
    assert m.phase(tmp_path,r,lambda:None,verify=verify,poll=.01)==first
    assert len(owned)==1 and owned[0].proc.returncode==0
    Path(r['output']).write_text('changed')
    with pytest.raises(ValueError,match='Previously verified'):m.phase(tmp_path,r,lambda:None,verify=verify)
    assert len(owned)==1


def test_nonzero_exit_preserves_evidence_and_never_retries(tmp_path,owned):
    r=row(tmp_path,'raise SystemExit(7)')
    with pytest.raises(ValueError,match='exit'):m.phase(tmp_path,r,lambda:None,verify=verify,poll=.01)
    with pytest.raises(ValueError,match='exit'):m.phase(tmp_path,r,lambda:None,verify=verify)
    assert len(owned)==1 and json.loads((tmp_path/'stage/exit.json').read_text())['exit_code']==7
    assert (tmp_path/'stage/failure.json').is_file()


def test_interrupt_preserves_live_owned_child_without_duplicate(tmp_path,owned):
    r=row(tmp_path,'import time;time.sleep(120)');calls=[]
    def guard():
        calls.append(1)
        if len(calls)>1:raise InterruptedError('fixture interrupt')
    with pytest.raises(InterruptedError):m.phase(tmp_path,r,guard,verify=verify,poll=.01)
    assert owned[0].proc.poll() is None
    with pytest.raises(RuntimeError,match='Unfinished stage'):m.phase(tmp_path,r,lambda:None,verify=verify)
    assert len(owned)==1 and owned[0].proc.poll() is None


def test_exited_controller_with_live_separate_session_descendant_rejected(tmp_path,owned):
    code='import subprocess,sys;subprocess.Popen([sys.executable,"-c","import time;time.sleep(120)"],start_new_session=True)'
    r=row(tmp_path,code)
    with pytest.raises(ValueError,match='descendants'):m.phase(tmp_path,r,lambda:None,verify=verify,poll=.01)
    assert (tmp_path/'stage/exit.json').is_file() and not (tmp_path/'stage/verified.json').exists()


def test_durable_successful_exit_can_resume_validation_only(tmp_path,owned):
    r=row(tmp_path)
    def interrupted(_):raise InterruptedError('after exit before verification')
    with pytest.raises(InterruptedError):m.phase(tmp_path,r,lambda:None,verify=interrupted,poll=.01)
    m.phase(tmp_path,r,lambda:None,verify=verify)
    assert len(owned)==1 and (tmp_path/'stage/verified.json').exists()


@pytest.mark.parametrize('field',['intent','command','exit_code'])
def test_changed_journal_prevents_adoption(tmp_path,owned,field):
    r=row(tmp_path);m.phase(tmp_path,r,lambda:None,verify=verify,poll=.01)
    p=tmp_path/'stage'/('intent.json' if field=='intent' else 'exit.json');value=json.loads(p.read_text())
    if field=='intent':value['output']='other'
    elif field=='command':value['command']=['wrong']
    else:value['exit_code']=1
    atomic_json(p,value)
    with pytest.raises(ValueError):m.phase(tmp_path,r,lambda:None,verify=verify)
    assert len(owned)==1


def test_prelaunch_guard_failure_creates_no_phase(tmp_path,owned):
    def guard():raise RuntimeError('space')
    with pytest.raises(RuntimeError,match='space'):m.phase(tmp_path,row(tmp_path),guard,verify=verify)
    assert not owned and not (tmp_path/'stage').exists()


def test_all13_commands_bind_reserved_paths_and_dependency_order(tmp_path):
    main=tmp_path/'main';main.mkdir();control=tmp_path/'control'
    pools={k:dict(paths=[str(tmp_path/k)],budget_bytes=gib*2**30) for k,gib in
        [('final_native',8),('final_resume',1),('final_retention',1),('hf_and_serving',100)]}
    atomic_json(main/'contract.json',dict(shared_storage=dict(nfs_pools=pools)))
    rows=m.stages(SimpleNamespace(main=main,control=control))
    assert [r['name'] for r in rows]==['native_prepare','native','resume_plan','resume_prepare','resume',
        'retention_prepare','retention','hf_prepare','hf','vllm_prepare','vllm','serving_prepare','serving']
    for r in rows:
        assert Path(r['command'][2]).is_file()
        if r['name'] in ['native_prepare','native','resume_plan','retention_prepare','hf_prepare']:
            assert r['command'][r['command'].index('--runtime-control')+1]==str(control)
    vllm=next(r for r in rows if r['name']=='vllm_prepare')['command']
    assert vllm[vllm.index('--local-environment-cap-gib')+1]=='60'
    pools['hf_and_serving']['budget_bytes']=16*2**20
    atomic_json(main/'contract.json',dict(shared_storage=dict(nfs_pools=pools)))
    with pytest.raises(ValueError,match='reservation'):m.stages(SimpleNamespace(main=main,control=control))


@pytest.mark.parametrize('name',['native','resume','retention','hf','vllm','serving'])
def test_done_marker_alone_never_passes_actual_stage(tmp_path,name):
    atomic_json(tmp_path/'contract.json',dict(code={},inputs={}))
    atomic_json(tmp_path/'report.json',dict(status='passed'))
    atomic_json(tmp_path/'.done',dict(report_sha256='0'*64,contract_sha256='0'*64))
    with pytest.raises(ValueError,match='marker/report'):m.verify_stage(dict(name=name,output=str(tmp_path)))


@pytest.mark.parametrize('fail',[True,False])
def test_serial_manager_stops_on_failure_and_never_claims_goal_complete(tmp_path,monkeypatch,owned,fail):
    main=tmp_path/'main';main.mkdir();atomic_json(main/'contract.json',{})
    root=tmp_path/'followup';root.mkdir();view=SimpleNamespace(main=main,unchanged=lambda:None)
    rows=[row(tmp_path,'raise SystemExit(2)' if fail else None,'first'),row(tmp_path,name='second')]
    plan=dict(code={},inputs={},max_seconds=100,stages=rows,remaining_after_stages=['quality','Ollama','report'])
    atomic_json(root/'plan.json',plan);monkeypatch.setattr(m,'load',lambda _: (view,root,plan))
    monkeypatch.setattr(m,'enforce_shared_space',lambda *a:None)
    monkeypatch.setattr(m,'coordinator_admission',lambda *a:{'fixture':True})
    monkeypatch.setattr(m,'future_local_capacity',lambda *a:{'fixture':True})
    original=m.phase
    monkeypatch.setattr(m,'phase',lambda root,row,guard:original(root,row,guard,verify=verify,poll=.01))
    if fail:
        with pytest.raises(ValueError):m.run('fixture')
        assert not Path(rows[1]['output']).exists() and len(owned)==1
        assert json.loads((root/'state.json').read_text())['phase']=='error'
    else:
        report=m.run('fixture');assert not report['goal_complete'] and len(report['stages'])==2
        assert report['remaining']==['quality','Ollama','report']
        assert m.run('fixture')==report and len(owned)==2


def test_missing_runtime_rejects_before_followup_output(tmp_path):
    control=tmp_path/'unpublished';control.mkdir()
    with pytest.raises((ValueError,FileNotFoundError)):m.prepare(control)
    assert list(control.iterdir())==[]


@pytest.fixture
def accepted(tmp_path,monkeypatch):
    main=tmp_path/'main';main.mkdir();pool=tmp_path/'transition';pool.mkdir()
    control=pool/'runtime';control.mkdir();atomic_json(control/'runtime_contract.json',{'fixture':True})
    pools={k:dict(paths=[str(tmp_path/k)],budget_bytes=gib*2**30) for k,gib in
        [('final_native',8),('final_resume',1),('final_retention',1),('hf_and_serving',100)]}
    pools['transition']=dict(paths=[str(pool)],budget_bytes=2**30)
    atomic_json(main/'contract.json',dict(shared_storage=dict(nfs_pools=pools)))
    view=SimpleNamespace(main=main,control=control,path=control/'runtime_contract.json',
        digest=sha256_file(control/'runtime_contract.json'),binding={'code':{m.OWN:sha256_file(resolve(m.OWN))}})
    monkeypatch.setattr(m,'RuntimeView',lambda _:view)
    monkeypatch.setattr('scripts.independent_final_source.observation',lambda *a:dict(complete=False,ready=False,step=12510))
    monkeypatch.setattr(m,'verify_retired_parents',lambda *a:[])
    monkeypatch.setattr(m,'enforce_shared_space',lambda *a:None)
    monkeypatch.setattr(m,'coordinator_admission',lambda *a:{'fixture':True})
    return view


def test_prepare_binds_code_and_restored_paths_without_stage_outputs(accepted):
    view=accepted;plan=m.prepare(view.control)
    assert len(plan['stages'])==13 and plan['max_pss_gib']==16
    assert plan['code'][m.OWN]==sha256_file(resolve(m.OWN))
    assert all(not Path(r['output']).exists() for r in plan['stages'])
    assert m.load(view.control)[2]==plan
    assert not (view.control/'followup_v1/started.json').exists()


@pytest.mark.parametrize('fault',['no_consumption','parents','space','deferred','existing_output','unbound_code'])
def test_prepare_refuses_unmet_prerequisites_without_output(accepted,monkeypatch,fault):
    view=accepted
    if fault=='no_consumption':monkeypatch.setattr('scripts.independent_final_source.observation',lambda *a:dict(phase='await_all8_consumption'))
    elif fault=='parents':monkeypatch.setattr(m,'verify_retired_parents',lambda *a:(_ for _ in ()).throw(ValueError('parents pending')))
    elif fault=='space':monkeypatch.setattr(m,'coordinator_admission',lambda *a:(_ for _ in ()).throw(RuntimeError('space')))
    elif fault=='deferred':
        c=json.loads((view.main/'contract.json').read_text());c['shared_storage']['nfs_pools']['final_resume']['budget_bytes']=16*2**20
        atomic_json(view.main/'contract.json',c)
    elif fault=='existing_output':Path(m.stages(view)[0]['output']).mkdir()
    elif fault=='unbound_code':view.binding['code'][m.OWN]='0'*64
    with pytest.raises((ValueError,RuntimeError)):m.prepare(view.control)
    assert not (view.control/'followup_v1').exists()


def test_modified_command_plan_not_accepted_even_with_rewritten_marker(accepted):
    view=accepted;plan=m.prepare(view.control);root=view.control/'followup_v1'
    plan['stages'][0]['command']=['wrong'];atomic_json(root/'plan.json',plan)
    atomic_json(root/'prepared.json',dict(plan_sha256=sha256_file(root/'plan.json'),execution_started=False))
    with pytest.raises(ValueError,match='scope'):m.load(view.control)


@pytest.mark.parametrize('fault',['none','memory','gpu','cpu'])
def test_coordinator_admission_reserves_main_child_and_own_memory(tmp_path,monkeypatch,fault):
    main=tmp_path/'main';main.mkdir();gpus=['fixture GPU'+str(i) for i in range(8)]
    atomic_json(main/'contract.json',dict(max_pss_gib=384,gpu_identity=gpus))
    hw=dict(gpus=gpus,cpu={'effective':96},memory={'cache_aware_admission':{'headroom_gib':656}})
    if fault=='memory':hw['memory']['cache_aware_admission']['headroom_gib']=655
    elif fault=='gpu':hw['gpus']=gpus[:-1]
    elif fault=='cpu':hw['cpu']['effective']=1
    monkeypatch.setattr('scripts.resource_admission.inspect',lambda:hw)
    monkeypatch.setattr(m,'enforce_shared_space',lambda *a:{'fixture':True})
    monkeypatch.setattr(m,'future_local_capacity',lambda *a:{'fixture':True})
    if fault=='none':assert m.coordinator_admission(SimpleNamespace(main=main))['combined_memory_gib']==656
    else:
        with pytest.raises(RuntimeError,match='hardware budget'):m.coordinator_admission(SimpleNamespace(main=main))


@pytest.mark.parametrize('extra_gib,passes',[(0,True),(2,True),(4,False)])
def test_final_future_peak_counts_resident_native_resume_and_environment_once(tmp_path,monkeypatch,extra_gib,passes):
    temp=tmp_path/'temp';env=temp/'environment'
    c=dict(tiered_retention={'temp_root':str(temp)},shared_storage={
        'local_temp_paths':[str(temp)],'nfs_pools':{'final_resume':{'paths':[str(tmp_path/'resume')]}}})
    monkeypatch.setattr(m,'json_file',lambda _: {'local_root':str(env)})
    sizes={temp/'final_native':3*m.CAP, temp/'resume':150*2**30,env:10*2**30}
    monkeypatch.setattr(m,'size_tree',lambda p:sizes[p])
    # Base unrelated temporary occupancy760GiB plus already materialized states.
    total=(760+extra_gib)*2**30+sum(sizes.values())
    if passes:
        result=m.future_local_capacity(c,{'local_temporary_used_bytes':total})
        assert result['projected_final_temporary_bytes']==(1021+extra_gib)*2**30
    else:
        with pytest.raises(RuntimeError,match='1024'):m.future_local_capacity(c,{'local_temporary_used_bytes':total})
