"""CPU coordinator sequencing/failure preservation; all GPU/launch actions mocked."""
import json
from pathlib import Path
import time
from types import SimpleNamespace
import pytest
from common.data_contracts import atomic_json
from scripts import run_budget_transition as m

@pytest.fixture
def scope(tmp_path,monkeypatch):
    main=tmp_path/'main';main.mkdir();control=tmp_path/'control';control.mkdir();source=tmp_path/'source';source.mkdir()
    (source/'training').mkdir();(source/'training/log.jsonl').write_text('{"step":16000,"tok_s":123}\n')
    paths=dict(source=source,destination=main,output=control/'boundary16000')
    capture=dict(processes={'supervisor':{'pid':123},'worker':{'pid':124}})
    for key,value in [('draft',{}),('capture',capture),('controllers',{}),('authorization',{})]:
        path=tmp_path/(key+'.json');atomic_json(path,value);paths[key]=path
    plan=dict(boundary_step=16000);path=tmp_path/'plan.json';atomic_json(path,plan)
    monkeypatch.setattr(m,'validate_plan',lambda p:paths)
    monkeypatch.setattr(m,'capture_extended_producer',lambda *a:capture)
    monkeypatch.setattr(m,'same_identity',lambda *a:True)
    monkeypatch.setattr(m,'validate_controllers',lambda *a:None)
    monkeypatch.setattr(m,'enforce_shared_space',lambda *a:{})
    monkeypatch.setattr(m,'ready',lambda *a:True)
    original=m.resolve
    monkeypatch.setattr(m,'resolve',lambda p:tmp_path/'pipeline_events.jsonl' if p=='logs/pipeline_events.jsonl' else original(p))
    calls=[]
    monkeypatch.setattr(m,'handoff',lambda *a:calls.append('handoff'))
    monkeypatch.setattr(m,'migrate_old_registry',lambda *a:calls.append('archive_old_registrations'))
    monkeypatch.setattr('scripts.budget_horizon_retention.BudgetRetention',lambda *a,**k:SimpleNamespace(maintain=lambda:dict(uncertified_nfs=[],nfs_checkpoints=[1,2,3],local_checkpoints=[])))
    monkeypatch.setattr('scripts.prepare_budget_probe.prepare',lambda *a:calls.append('probe_prepare'))
    monkeypatch.setattr('scripts.run_budget_probe.execute',lambda *a:calls.append('actual8gpu'))
    def activate(*a):
        calls.append('activate');atomic_json(main/'contract.json',{'fixture':True})
    monkeypatch.setattr('scripts.activate_budget_training.activation',activate)
    monkeypatch.setattr('scripts.cleanup_budget_probe.execute',lambda *a:calls.append('cleanup'))
    monkeypatch.setattr('scripts.completed_budget_storage.prepare',lambda *a:calls.append('final_storage'))
    monkeypatch.setattr('scripts.completed_budget_storage.load',lambda *a:(main,{}))
    monkeypatch.setattr('scripts.run_completed_budget_followup.capacity',lambda *a:calls.append('capacity'))
    def launch(directory,cmd,env,binding):
        calls.append('launch_'+directory.name)
        assert env['TMPDIR']=='/tmp'
        return dict(process={'pid':234},command=cmd)
    monkeypatch.setattr('scripts.launch_independent_services.launch_once',launch)
    monkeypatch.setattr(m,'register',lambda *a:None)
    monkeypatch.setattr(m,'wait_progress',lambda *a,**k:dict(status='fixture',committed_step=16001))
    monkeypatch.setattr('scripts.run_completed_budget_followup.prepare',lambda *a:calls.append('followup_prepare'))
    monkeypatch.setattr('scripts.run_completed_budget_followup.start',lambda *a:dict(status='fixture'))
    return path,control,calls


def test_one_transition_order_no_restart_and_main_tmpdir(scope):
    path,root,calls=scope;r=m.run(path)
    assert calls==['handoff','archive_old_registrations','probe_prepare','actual8gpu','activate','cleanup',
                   'final_storage','capacity','launch_supervisor','launch_observer','followup_prepare']
    assert r['target_step']==50000 and not r['final_training_complete'] and not r['goal_complete']
    assert (root/'.done').exists()
    with pytest.raises(ValueError,match='Prior'):m.run(path)
    assert calls.count('launch_supervisor')==1

@pytest.mark.parametrize('stage',['handoff','probe','cleanup','capacity'])
def test_failure_blocks_every_later_stage_and_preserves_evidence(scope,monkeypatch,stage):
    path,root,calls=scope
    def fail(*a,**k):raise RuntimeError('fixture failure')
    target={'handoff':'scripts.run_budget_transition.handoff','probe':'scripts.run_budget_probe.execute',
            'cleanup':'scripts.cleanup_budget_probe.execute','capacity':'scripts.run_completed_budget_followup.capacity'}[stage]
    monkeypatch.setattr(target,fail)
    with pytest.raises(RuntimeError,match='fixture'):m.run(path)
    assert 'launch_supervisor' not in calls and not (root/'.done').exists()
    f=json.loads((root/'failure.json').read_text());assert not f['automatic_retry'] and f['outputs_preserved']
    assert json.loads((root/'state.json').read_text())['phase']=='error'

@pytest.fixture
def progressing(tmp_path,monkeypatch):
    main=tmp_path/'new';main.mkdir();(main/'ledgers').mkdir();(main/'training').mkdir()
    candidate={'id':'new-contract'}
    atomic_json(main/'contract.json',{'budget_execution':{'parent_step':16000}});atomic_json(main/'main.json',{})
    atomic_json(main/'state.json',dict(phase='running',worker_pid=345));atomic_json(main/'training/training_contract.json',candidate)
    for rank in range(8):atomic_json(main/'ledgers'/f'new-attempt-rank{rank}.json',dict(rank=rank,contract_id='new-contract',steps=[dict(step=16001,source_sequences={'fixture':64})]))
    monkeypatch.setattr('train.budget_horizon_handoff.load_handoff',lambda *a:({},candidate,{}))
    monkeypatch.setattr('scripts.run_budget_transition.children',lambda *a:set(range(8)))
    monkeypatch.setattr('scripts.capture_budget_producer.capture_budget_producer',lambda *a:dict(attempt='new-attempt',processes={}))
    return main


def test_first_progress_requires_all8_actual_new_committed_updates(progressing):
    result=m.wait_progress(progressing,123,deadline=time.monotonic()+10,heartbeat=lambda:None)
    assert result['committed_tokens_added']==2097152 and len(result['ranks'])==8
    assert result['committed_step']==16001 and not result['goal_complete']

@pytest.mark.parametrize('fault',['rank','identity','tokens','partial','startup'])
def test_partial_or_wrong_progress_cannot_publish_success(progressing,monkeypatch,fault):
    path=progressing/'ledgers/new-attempt-rank7.json';row=json.loads(path.read_text())
    if fault=='rank':row['rank']=6
    elif fault=='identity':row['contract_id']='old'
    elif fault=='tokens':row['steps'][0]['source_sequences']['fixture']=1
    elif fault=='partial':row['steps']=[]
    else:
        monkeypatch.setattr('scripts.run_budget_transition.children',lambda *a:{1,2,3})
        monkeypatch.setattr('scripts.capture_budget_producer.capture_budget_producer',lambda *a:pytest.fail('Incomplete spawn is waiting'))
    atomic_json(path,row)
    def stop_wait(*a):raise InterruptedError('fixture waiting')
    monkeypatch.setattr(m.time,'sleep',stop_wait)
    with pytest.raises((ValueError,InterruptedError)):
        m.wait_progress(progressing,123,deadline=time.monotonic()+10,heartbeat=lambda:None)
