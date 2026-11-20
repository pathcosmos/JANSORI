import pytest
from scripts.budget_final_diagnostic_processes import owns


@pytest.mark.parametrize('module',['train.independent_resume_probe','train.budget_final_resume_probe'])
def test_rank_workers_in_both_diagnostic_families_are_detected(tmp_path,module):
    root=tmp_path/'diagnostic';other=tmp_path/'other'
    args=['python','-m',module,'--spec',str(root/'spec.json'),'--mode','continuous']
    assert owns(args,tmp_path,root)
    assert not owns(args,tmp_path,other)


@pytest.mark.parametrize('script',['run_independent_resume.py','run_budget_final_resume.py'])
def test_both_supervisor_workers_are_detected_with_relative_root(tmp_path,script):
    root=tmp_path/'diagnostic';args=['python',script,'--run-dir','diagnostic','--worker']
    assert owns(args,tmp_path,root)
    assert not owns(args,tmp_path,tmp_path/'other')


@pytest.mark.parametrize('args',[
    ['python','-m','train.budget_final_resume_probe'],
    ['python','-m','train.budget_final_resume_probe','--spec','one','--spec','two'],
    ['python','run_budget_final_resume.py','--worker','--run-dir'],
    ['python','run_budget_final_resume.py','--worker','--run-dir','one','--run-dir','two']])
def test_missing_or_ambiguous_scope_is_rejected(tmp_path,args):
    with pytest.raises(ValueError):owns(args,tmp_path,tmp_path/'diagnostic')


def test_unrelated_training_is_not_diagnostic(tmp_path):
    assert not owns(['python','supervise_budget_training.py','--run-dir','diagnostic'],tmp_path,tmp_path/'diagnostic')


@pytest.mark.parametrize('module',['train.independent_resume_probe','train.budget_final_resume_probe'])
def test_live_process_is_observed_and_exit_is_not_assumed(tmp_path,module):
    import subprocess,sys,time
    from scripts.budget_final_diagnostic_processes import running
    root=tmp_path/'diagnostic';root.mkdir()
    # A harmless private Python child has the real argv shape; no model is loaded.
    child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)',
        module,'--spec',str(root/'spec.json'),'--mode','continuous'])
    try:
        deadline=time.monotonic()+5;seen=[]
        while time.monotonic()<deadline:
            seen=running(root)
            if any(p['pid']==child.pid for p in seen):break
            time.sleep(.05)
        assert any(p['pid']==child.pid for p in seen)
        assert not any(p['pid']==child.pid for p in running(tmp_path/'another'))
    finally:
        child.terminate();child.wait(timeout=5)
    assert not any(p['pid']==child.pid for p in running(root))
