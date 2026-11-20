import json
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

import scripts.run_restored_vllm as runtime
from common.data_contracts import atomic_json,sha256_file


def test_new_runtime_binds_actual_environment_without_export_launch(tmp_path,monkeypatch):
    import scripts.export_restored_hf as export
    from common.data_contracts import resolve
    source=dict(main_run='runs/3b_full_57k_v2',tokenizer='tokenizer/candidate_v1/tokenizer.json',prompts='runs/f7_preflight_v1/prompts.jsonl')
    monkeypatch.setattr(export,'load_contract',lambda _:dict(source=source,code={},inputs={}))
    atomic_json(tmp_path/'contract.json',{'fixture':True});atomic_json(tmp_path/'prepared.json',{'fixture':True})
    c=runtime.make_contract(tmp_path,'runs/main_training_35k_plan_v1/serving_environment_v2')
    assert c['max_seconds']==1800 and c['engine']['max_model_len']==4096
    assert c['engine']['enable_prefix_caching'] is False and c['target_steps']==57000
    assert c['local_subdirectory']=='runtime_57k_v2'
    assert c['local_environment_cap_gib']==64
    bounded=runtime.make_contract(tmp_path,'runs/main_training_35k_plan_v1/serving_environment_v2',local_environment_cap_gib=60)
    assert bounded['local_environment_cap_gib']==60 and bounded['engine']==c['engine']
    with pytest.raises(ValueError,match='incomplete'):runtime.authenticate_export(c)
    assert not (Path(c['root'])/'supervision').exists()


def test_export_wait_uses_new_controller_process(tmp_path):
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)',
        'scripts/export_restored_hf.py','--run-dir',str(tmp_path)])
    try:
        (tmp_path/'pid').write_text(str(child.pid));atomic_json(tmp_path/'state.json',dict(phase='running'))
        assert runtime.dependency_ready(dict(export_root=str(tmp_path))) is False
    finally:child.terminate();child.wait(timeout=5)
    with pytest.raises(RuntimeError,match='handle'):runtime.dependency_ready(dict(export_root=str(tmp_path)))


def test_live_orphan_and_partial_execution_are_preserved(tmp_path):
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)',runtime.WORKER,'--run-dir',str(tmp_path)])
    try:
        with pytest.raises(RuntimeError,match='Existing'):runtime.reject_previous(tmp_path)
        assert child.poll() is None
    finally:child.terminate();child.wait(timeout=5)
    (tmp_path/'admitted.json').write_text('{"partial":true}')
    with pytest.raises(RuntimeError,match='Preserve'):runtime.reject_previous(tmp_path)
    assert json.loads((tmp_path/'admitted.json').read_text())['partial']


def test_worker_wrong_environment_refused_without_gpu(tmp_path):
    from inference.restored_vllm_worker import run
    atomic_json(tmp_path/'contract.json',dict(purpose='final57k_vllm_runtime',root=str(tmp_path),venv='/invalid-runtime'))
    with pytest.raises(ValueError,match='isolated'):run(tmp_path)
    assert not (tmp_path/'worker_execution.json').exists()


@pytest.mark.parametrize('fault',['time','memory','source','result'])
def test_controller_failure_preserves_evidence_and_no_done(tmp_path,monkeypatch,fault):
    import scripts.evaluate_final_rehearsal as helpers
    import scripts.run_restored_final_resume as supervisor
    c=dict(wait_seconds=10,poll_seconds=0,local_root=str(tmp_path/'local'),local_subdirectory='runtime_57k_v2',cpu_affinity=[8],
        venv='/fixture',max_seconds=1800)
    Path(c['local_root']).mkdir()
    monkeypatch.setattr(runtime,'dependency_ready',lambda c:True)
    monkeypatch.setattr(runtime,'admission',lambda *a:({},{}))
    def source(c):
        if fault=='source':raise ValueError('source failed')
        return {'fixture':True}
    monkeypatch.setattr(runtime,'authenticate_export',source)
    @contextmanager
    def gpu(notify):yield
    monkeypatch.setattr(helpers,'acquire_idle_gpu',gpu)
    def execute(cmd,*,root,env,guard):
        assert env['HF_HUB_OFFLINE']=='1' and env['CUDA_VISIBLE_DEVICES']=='0'
        (root/'partial-result').write_text('preserve')
        guard(1,1801 if fault=='time' else 1,65*runtime.GIB if fault=='memory' else 0)
        return {'fixture':True}
    monkeypatch.setattr(supervisor,'supervise_command',execute)
    def verify(*a):raise ValueError('result failed')
    monkeypatch.setattr(runtime,'verify_result',verify)
    with pytest.raises((RuntimeError,ValueError)):runtime.controller(tmp_path,c)
    assert json.loads((tmp_path/'state.json').read_text())['phase']=='error'
    assert not (tmp_path/'.done').exists()
    if fault!='source':assert (tmp_path/'partial-result').read_text()=='preserve'


def test_combined_budget_without_double_reserving_export(tmp_path,monkeypatch):
    import scripts.export_restored_hf as export
    monkeypatch.setattr(export,'load_contract',lambda p:{'source':{}})
    hw=dict(memory={'cache_aware_admission':{'headroom_gib':1105}},cpu={'affinity':[8,9]})
    monkeypatch.setattr(export,'admission',lambda *a:(hw,dict(combined_memory_gib=1040,local_temporary_used_bytes=runtime.GIB)))
    c=dict(export_root=str(tmp_path),local_root=str(tmp_path),cpu_affinity=[8,9],local_environment_cap_gib=64)
    _,cap=runtime.admission(tmp_path,c)
    assert cap['runtime_part_of_export_budget'] is True
    hw['memory']['cache_aware_admission']['headroom_gib']=1103
    with pytest.raises(RuntimeError,match='Combined'):runtime.admission(tmp_path,c)


@pytest.mark.parametrize('cap,total,used,passes',[
    (64,971,10,False),(60,971,10,True),(64,970,10,True),
    (60,1020,61,False),(60,5,10,False),(60,975,10,False)])
def test_runtime_full_local_cap_fits_shared_pool_without_double_count(monkeypatch,cap,total,used,passes):
    monkeypatch.setattr('scripts.tiered_checkpoint_retention.size_tree',lambda _:used*runtime.GIB)
    c=dict(local_root='/fixture',local_environment_cap_gib=cap)
    capacity=dict(local_temporary_used_bytes=total*runtime.GIB)
    if passes:
        result=runtime.local_capacity(c,capacity)
        assert result['projected_temporary_bytes_with_runtime']==(total-used+cap)*runtime.GIB
    else:
        with pytest.raises(RuntimeError,match='Shared local'):runtime.local_capacity(c,capacity)


def test_completion_only_after_verifier_and_process_exit(tmp_path,monkeypatch):
    import scripts.evaluate_final_rehearsal as helpers
    import scripts.run_restored_final_resume as supervisor
    c=dict(wait_seconds=10,poll_seconds=0,local_root=str(tmp_path/'local'),local_subdirectory='runtime_57k_v2',cpu_affinity=[8],venv='/fixture',max_seconds=1800)
    Path(c['local_root']).mkdir();atomic_json(tmp_path/'contract.json',c)
    monkeypatch.setattr(runtime,'dependency_ready',lambda c:True)
    monkeypatch.setattr(runtime,'admission',lambda *a:({},{}))
    monkeypatch.setattr(runtime,'authenticate_export',lambda c:{'fixture':True})
    @contextmanager
    def gpu(notify):yield
    monkeypatch.setattr(helpers,'acquire_idle_gpu',gpu)
    monkeypatch.setattr(supervisor,'supervise_command',lambda *a,**k:{'exit_code':0,'remaining_owned_processes':[]})
    def verify(*a):
        assert not (tmp_path/'.done').exists()
        return dict(status='fixture_not_actual_gpu',goal_complete=False)
    monkeypatch.setattr(runtime,'verify_result',verify)
    runtime.controller(tmp_path,c)
    m=json.loads((tmp_path/'.done').read_text())
    assert m['report_sha256']==sha256_file(tmp_path/'report.json')


@pytest.mark.parametrize('corruption',[None,'owner','command','descendant','coverage','nonfinite','token_path'])
def test_independent_runtime_review_binds_execution_and_outputs(tmp_path,monkeypatch,corruption):
    from types import SimpleNamespace as NS
    from tokenizers import Tokenizer
    from inference.vllm_validation import build_cases,validate_outputs
    import scripts.evaluate_final_rehearsal as helpers
    c=dict(tokenizer='tokenizer/candidate_v1/tokenizer.json',prompts='runs/f7_preflight_v1/prompts.jsonl',
        versions={'fixture':'synthetic'},venv='/fixture',cpu_affinity=[8],max_seconds=1800)
    admitted=dict(contract_id=runtime.identity(c),source_binding={'scope':'fixture'},manifest_sha256='0'*64)
    monkeypatch.setattr(runtime,'authenticate_export',lambda c:admitted)
    monkeypatch.setattr(helpers,'check_bindings',lambda c:None)
    atomic_json(tmp_path/'admitted.json',admitted)
    h=dict(pid=123,owner='fixture',process={'start_ticks':456},command=runtime.command(tmp_path,c))
    atomic_json(tmp_path/'supervision/handle.json',h)
    execution=dict(pid=123,start_ticks=456,owner='fixture',contract_id=runtime.identity(c),
        admission_sha256=sha256_file(tmp_path/'admitted.json'),package_manifest_sha256='0'*64,
        versions=c['versions'],visible_device_count=1,device_name='fixture H200',cuda_build='12.8')
    atomic_json(tmp_path/'worker_execution.json',execution)
    run=dict(exit_code=0,remaining_owned_processes=[],owner='fixture',launcher_pid=123,command=h['command'])
    atomic_json(tmp_path/'supervision/result.json',run)
    tok=Tokenizer.from_file(c['tokenizer']);prompts=[json.loads(s) for s in Path(c['prompts']).read_text().splitlines()]
    cases=build_cases(tok,prompts)
    outputs=[NS(finished=True,prompt_token_ids=x['ids'],
        prompt_logprobs=[None]+[{t:NS(logprob=-1.)} for t in x['ids'][1:]],outputs=[NS(index=0,
        token_ids=[3]*x['generated'],finish_reason='length',stop_reason=None,
        logprobs=[{3:NS(logprob=-.5)} for _ in range(x['generated'])],cumulative_logprob=-.5*x['generated'])]) for x in cases]
    rows=validate_outputs(cases,outputs,tok,context=4096)
    checks=dict(status='passed_output_checks_execution_provenance_required',token_cases=rows,text_cases=rows[:10],
        goal_complete=False,quality_benchmark=False)
    result=dict(status='passed_final57k_vllm_worker_pending_review',actual_gpu_inference_executed=True,
        execution_sha256=sha256_file(tmp_path/'worker_execution.json'),manifest_sha256='0'*64,
        quality_success_asserted=False,ollama_verified=False,goal_complete=False,seconds=1.,cases=cases,output_checks=checks)
    atomic_json(tmp_path/'worker_result.json',result)
    # This success is synthetic review coverage, explicitly not real GPU proof.
    assert runtime.verify_result(tmp_path,c)['actual_gpu_inference_verified'] is True
    if corruption is None:return
    if corruption=='owner':run['owner']='other'
    if corruption=='command':run['command']=['other-python']
    if corruption=='descendant':run['remaining_owned_processes']=[789]
    if corruption=='coverage':result['cases']=cases[:-1]
    if corruption=='nonfinite':rows[0]['prompt_nll_sum']=float('nan')
    if corruption=='token_path':
        checks['text_cases']=json.loads(json.dumps(rows[:10]));checks['text_cases'][0]['generated_ids'][0]=4
    atomic_json(tmp_path/'supervision/result.json',run);atomic_json(tmp_path/'worker_result.json',result)
    with pytest.raises(ValueError):runtime.verify_result(tmp_path,c)


def test_completed_export_cannot_admit_old_35000_source(tmp_path,monkeypatch):
    import scripts.export_restored_hf as export
    atomic_json(tmp_path/'.done',{'fixture':True})
    monkeypatch.setattr(export,'load_contract',lambda _: {})
    monkeypatch.setattr(export,'controller',lambda *a:dict(source_binding={'step':35000}))
    with pytest.raises(ValueError,match='Wrong final target'):
        runtime.authenticate_export(dict(export_root=str(tmp_path)))


def test_rehashed_old_runtime_contract_is_not_a57k_preparation(tmp_path):
    root=tmp_path/'vllm_runtime_v1';root.mkdir()
    c=dict(purpose='final57k_vllm_runtime',target_steps=35000)
    atomic_json(root/'contract.json',c);atomic_json(root/'prepared.json',dict(contract_sha256=sha256_file(root/'contract.json')))
    with pytest.raises(ValueError,match='Unreviewed'):runtime.load_contract(root)
