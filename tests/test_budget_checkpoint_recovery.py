"""Recovery gate regressions; synthetic processes/files, no launches or signals."""
import json
from pathlib import Path
import pytest
import scripts.recover_budget_checkpoint_followup as recovery


def terminal():
    return dict(phase='error',case='verify_new8rank_progress'),dict(
        phase='verify_new8rank_progress',error=repr(TimeoutError(recovery.TIMEOUT)))


def test_expected_timeout_requires_process_terminal_not_just_error_file():
    state,failure=terminal()
    assert not recovery.old_terminal(True,dict(phase='running',case='verify_new8rank_progress'),None,False)
    assert not recovery.old_terminal(True,state,failure,False)
    assert recovery.old_terminal(False,state,failure,False)


@pytest.mark.parametrize('fault',['missing_failure','different_error','different_phase','still_running','already_complete'])
def test_other_exit_reasons_or_completion_cannot_launch_services(fault):
    state,failure=terminal();completed=False
    if fault=='missing_failure':failure=None
    elif fault=='different_error':failure['error']='RuntimeError("corrupt")'
    elif fault=='different_phase':state['case']='actual8gpu'
    elif fault=='still_running':state['phase']='running'
    elif fault=='already_complete':completed=True
    with pytest.raises(ValueError):recovery.old_terminal(False,state,failure,completed)


def test_final_manager_environment_does_not_inherit_observer_gpu_mask(monkeypatch):
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES','')
    env=recovery.environment()
    assert 'CUDA_VISIBLE_DEVICES' not in env
    assert env['TMPDIR']=='/tmp' and env['OMP_NUM_THREADS']=='2'


@pytest.fixture
def main(tmp_path):
    (tmp_path/'training/ckpt_0016500').mkdir(parents=True)
    (tmp_path/'checkpoint_certificates').mkdir()
    (tmp_path/'training/training_contract.json').write_text(json.dumps({'id':'actual-contract'}))
    return tmp_path


def proof(main):
    return dict(status='passed_main_checkpoint_storage',step=16500,
        training_contract_id='actual-contract',checkpoint=str(main/'training/ckpt_0016500'))


def test_waits_for_both_certificate_and_completion_file(main):
    assert not recovery.certified(main)
    path=main/'checkpoint_certificates/ckpt_0016500.json';path.write_text(json.dumps(proof(main)))
    assert not recovery.certified(main)
    (main/'training/ckpt_0016500/.complete').write_text('')
    assert recovery.certified(main)  # Full content/8rank audit is a separate gate.


@pytest.mark.parametrize('field,value',[('step',16000),('status','pending'),
    ('training_contract_id','other'),('checkpoint','/tmp/unrelated/ckpt_0016500')])
def test_wrong_checkpoint_cannot_reach_actual_audit(main,field,value):
    value_proof=proof(main);value_proof[field]=value
    (main/'checkpoint_certificates/ckpt_0016500.json').write_text(json.dumps(value_proof))
    (main/'training/ckpt_0016500/.complete').write_text('')
    with pytest.raises(ValueError):recovery.certified(main)


def test_producer_replacement_rejected_without_adoption(monkeypatch):
    row=dict(pid=10,start_ticks=100,argv_sha256='a',cwd='/source',uid=1)
    expected=dict(attempt='one',processes={'supervisor':row})
    monkeypatch.setattr(recovery,'read',lambda p: {'command':['supervisor']} if p.name=='intent.json'
                        else {'phase':'running','case':'main','worker_pid':20})
    monkeypatch.setattr(recovery,'inspect_handle',lambda *a:{'process':{'pid':10}})
    monkeypatch.setattr(recovery,'capture_budget_producer',lambda *a:dict(attempt='two',processes={'supervisor':row}))
    with pytest.raises(ValueError,match='identity changed'):recovery.producer({'producer':expected})


def test_runner_cannot_launch_training_or_signal_processes():
    import ast
    tree=ast.parse(Path(recovery.__file__).read_text())
    calls=[n.func for n in ast.walk(tree) if isinstance(n,ast.Call)]
    assert not any(isinstance(n,ast.Attribute) and n.attr in ('kill','killpg','terminate','send_signal','unlink','rmtree') for n in calls)
    for command in ('supervise_budget_training.py','torch.distributed.run'):
        assert not any(isinstance(n,ast.Constant) and n.value==command for n in ast.walk(tree))


@pytest.mark.parametrize('failure',[None,'audit','followup'])
def test_runner_service_order_and_failure_preservation(tmp_path,monkeypatch,failure):
    import scripts.audit_budget_training as audit
    import scripts.run_budget_transition as transition
    import scripts.completed_budget_storage as storage
    import scripts.run_completed_budget_followup as followup
    def resolve(p):
        p=Path(p)
        return p if p.is_absolute() else tmp_path/p
    monkeypatch.setattr(recovery,'resolve',resolve)
    root=resolve(recovery.ROOT);root.mkdir(parents=True)
    main=resolve(recovery.MAIN);main.mkdir(parents=True)
    (main/'contract.json').write_text('{}')
    (root/'plan.json').write_text('{}')
    resolve('logs').mkdir()
    plan={'max_seconds':21600}
    monkeypatch.setattr(recovery,'load',lambda:plan)
    monkeypatch.setattr(recovery,'producer',lambda p:{'processes':{'supervisor':{'pid':10}}})
    exits=iter([False,True,True,True])
    monkeypatch.setattr(recovery,'original_exited',lambda p:next(exits))
    certificates=iter([False,True])
    monkeypatch.setattr(recovery,'certified',lambda p:next(certificates))
    monkeypatch.setattr(recovery.time,'sleep',lambda s:None)
    calls=[]
    def actual_audit(*args):
        calls.append('audit')
        if failure=='audit':raise ValueError('synthetic corrupt ledger')
        return {'counts':{'committed_tokens':16500*2097152}}
    monkeypatch.setattr(audit,'audit_run',actual_audit)
    monkeypatch.setattr(transition,'wait_progress',lambda *a,**k:{'committed_step':16001})
    monkeypatch.setattr(transition,'register',lambda spec:calls.append('register'))
    monkeypatch.setattr(storage,'load',lambda c:(main,{}))
    monkeypatch.setattr(followup,'capacity',lambda *a:{'test_capacity':True})
    manager=resolve(recovery.CONTROL)/'completed_storage_v1_followup'
    monkeypatch.setattr(followup,'manager_root',lambda p:manager)
    def observer(directory,command,env,binding):
        assert env['CUDA_VISIBLE_DEVICES']==''
        assert 'observe_budget_training.py' in command[2]
        calls.append('observer');return {'process':{'pid':30}}
    monkeypatch.setattr(recovery,'launch_once',observer)
    monkeypatch.setattr(followup,'prepare',lambda p:calls.append('prepare'))
    def start(p):
        calls.append('followup')
        if failure=='followup':raise ValueError('synthetic service failure')
        (manager/'service').mkdir(parents=True)
        (manager/'service/intent.json').write_text(json.dumps({'command':['followup']}))
        return {'process':{'pid':40}}
    monkeypatch.setattr(followup,'start',start)
    monkeypatch.setattr(followup,'job_spec',lambda p:{'test':True})
    monkeypatch.setattr(recovery,'inspect_handle',lambda *a:calls.append('inspect'))
    if failure:
        with pytest.raises(ValueError):recovery.run()
        assert (root/'failure.json').exists() and not (root/'.done').exists()
        if failure=='audit':assert calls==['audit']
        else:assert calls==['audit','observer','register','prepare','followup']
    else:
        result=recovery.run()
        assert calls==['audit','observer','register','prepare','followup','register','inspect','inspect']
        assert result['training_interrupted'] is False and not result['goal_complete']
        assert (root/'.done').exists()
    assert not (main/'training').exists()  # No training launch or checkpoint creation.
