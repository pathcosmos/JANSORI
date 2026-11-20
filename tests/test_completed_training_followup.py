"""CPU lifecycle evidence, not actual final3B/GPU acceptance."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from common.data_contracts import sha256_file, resolve
from scripts.tiered_checkpoint_retention import atomic_json
from scripts import run_completed_training_followup as m
import test_independent_followup as fixtures
from test_independent_followup import owned


@pytest.fixture
def prepared_source(tmp_path,monkeypatch):
    main=tmp_path/'main';main.mkdir();atomic_json(main/'contract.json',{'fixture':True})
    scope=tmp_path/'transition';scope.mkdir();control=scope/'storage';control.mkdir()
    atomic_json(control/'plan.json',{'fixture':True});atomic_json(control/'prepared.json',{})
    pools={k:dict(paths=[str(tmp_path/k)],budget_bytes=gib*2**30) for k,gib in
           [('final_native',8),('final_resume',1),('final_retention',1),('hf_and_serving',100)]}
    pools['transition']=dict(paths=[str(scope)],budget_bytes=2**30)
    contract=dict(shared_storage=dict(nfs_pools=pools))
    reference=dict(control_dir=str(control),plan_sha256=sha256_file(control/'plan.json'))
    binding=dict(reference=reference,inputs={str(control/'plan.json'):reference['plan_sha256']})
    monkeypatch.setattr(m.storage,'load',lambda _: (main,contract))
    monkeypatch.setattr(m.storage,'bind',lambda *a:binding)
    monkeypatch.setattr(m,'main_observation',lambda *a:dict(complete=False,step=14310))
    monkeypatch.setattr(m,'capacity',lambda *a:{'fixture':True})
    environment=tmp_path/'environment';environment.mkdir()
    for name in ['report.json','contract.json','requirements.lock','import_report.json']:
        (environment/name).write_text('{}')
    monkeypatch.setattr(m,'ENVIRONMENT',str(environment))
    return SimpleNamespace(main=main,control=control,contract=contract,reference=reference)


def test_prepare_load_exact_serial_commands_and_separate_metadata_caps(prepared_source):
    s=prepared_source;plan=m.prepare(s.control);root=m.manager_root(s.control)
    assert not root.is_relative_to(s.control) and root.parent==s.control.parent
    assert m.load(s.control)[2]==plan
    assert plan['code'][m.OWN]==sha256_file(resolve(m.OWN))
    assert not plan['training_stop_allowed'] and not plan['automatic_retry']
    assert not (root/'started.json').exists()
    spec=json.loads((root/'job_spec.json').read_text())
    assert spec==m.job_spec(root) and spec['requires_review']
    assert spec['proofs'][1]['path']==str(root/'plan.json')
    assert [r['name'] for r in plan['stages']]==['native_prepare','native','resume_plan','resume_prepare',
        'resume','retention_prepare','retention','hf_prepare','hf','vllm_prepare','vllm','serving_prepare','serving']
    for row in plan['stages']:
        assert not Path(row['output']).exists()
        assert '--runtime-control' not in row['command']
        if row['name'] in ['native_prepare','native','resume_plan','retention_prepare','hf_prepare']:
            assert row['command'][row['command'].index('--storage-control')+1]==str(s.control)
    command=plan['stages'][9]['command']
    assert command[command.index('--local-environment-cap-gib')+1]=='60'


@pytest.mark.parametrize('fault',['command','ref','training_stop_allowed','input'])
def test_modified_plan_or_input_rejected(prepared_source,fault):
    s=prepared_source;plan=m.prepare(s.control);root=m.manager_root(s.control)
    if fault=='command':plan['stages'][0]['command']=['wrong']
    elif fault=='ref':plan['reference']={}
    elif fault=='training_stop_allowed':plan[fault]=True
    else:(s.main/'contract.json').write_text('changed')
    atomic_json(root/'plan.json',plan)
    atomic_json(root/'prepared.json',dict(plan_sha256=sha256_file(root/'plan.json'),execution_started=False))
    with pytest.raises(ValueError):m.load(s.control)


@pytest.mark.parametrize('fault',['space','existing','deferred'])
def test_prepare_failure_has_no_manager_or_final_outputs(prepared_source,monkeypatch,fault):
    s=prepared_source
    if fault=='space':monkeypatch.setattr(m,'capacity',lambda *a:(_ for _ in ()).throw(RuntimeError('space')))
    elif fault=='existing':Path(s.contract['shared_storage']['nfs_pools']['final_native']['paths'][0]).mkdir()
    else:s.contract['shared_storage']['nfs_pools']['final_resume']['budget_bytes']=16*2**20
    with pytest.raises((RuntimeError,ValueError)):m.prepare(s.control)
    assert not m.manager_root(s.control).exists()


@pytest.mark.parametrize('race',[False,True])
def test_completion_publication_never_bypasses_live_source(tmp_path,monkeypatch,race):
    if not race:(tmp_path/'.done').write_text('{}')
    monkeypatch.setattr(m,'main_observation',lambda *a:dict(complete=True,step=57000))
    monkeypatch.setattr(m.storage,'active_source_processes',lambda *a:[{'pid':123}])
    assert m.observation(tmp_path)==dict(complete=False,step=57000,phase='await_source_exit')
    monkeypatch.setattr(m.storage,'active_source_processes',lambda *a:[])
    assert m.observation(tmp_path)==dict(complete=True,step=57000,phase='final_audit_required')


@pytest.mark.parametrize('fault',[None,'dead','step','status','state'])
def test_worker_exit_publication_race_requires_live_supervisor_and_final_certificate(tmp_path,monkeypatch,fault):
    monkeypatch.setattr(m,'main_observation',lambda *a:(_ for _ in ()).throw(RuntimeError('worker exited')))
    monkeypatch.setattr('scripts.observe_extended_training_v2.process_identity',lambda *a:None if fault=='dead' else {'pid':123})
    (tmp_path/'pid').write_text('123')
    atomic_json(tmp_path/'state.json',dict(phase='error' if fault=='state' else 'running'))
    (tmp_path/'checkpoint_certificates').mkdir()
    atomic_json(tmp_path/'checkpoint_certificates/ckpt_0057000.json',dict(
        step=56500 if fault=='step' else 57000,status='failed' if fault=='status' else 'passed_main_checkpoint_storage'))
    if fault:
        with pytest.raises(RuntimeError):m.observation(tmp_path)
    else:assert m.observation(tmp_path)['phase']=='await_completion_publication'


@pytest.mark.parametrize('mode',['success','audit_failure','stage_failure','waiting_interrupt'])
def test_actual_cpu_serial_execution_waits_and_preserves_failed_evidence(tmp_path,monkeypatch,owned,mode):
    main=tmp_path/'main';main.mkdir();root=tmp_path/'manager';root.mkdir()
    rows=[fixtures.row(tmp_path,'raise SystemExit(7)' if mode=='stage_failure' else None,'first'),
          fixtures.row(tmp_path,name='second')]
    plan=dict(code={},inputs={},reference={'fixture':True},stages=rows,
              remaining_after_stages=['quality','Ollama','report'])
    atomic_json(root/'plan.json',plan)
    monkeypatch.setattr(m,'load',lambda _: (main,root,plan,{}))
    monkeypatch.setattr(m,'capacity',lambda *a:{})
    monkeypatch.setattr(m.storage,'view',lambda *a:{})
    monkeypatch.setattr(m,'enforce_shared_space',lambda *a:{})
    monkeypatch.setattr(m,'future_local_capacity',lambda *a:{})
    monkeypatch.setattr(m,'record',lambda *a,**k:None)
    seen=iter([dict(complete=False,step=14310,phase='await_training_completion'),
               dict(complete=False,step=57000,phase='await_source_exit'),
               dict(complete=True,step=57000,phase='final_audit_required')])
    monkeypatch.setattr(m,'observation',lambda *a:next(seen))
    real_sleep=m.time.sleep
    def sleep(seconds):
        if seconds==m.POLL:
            assert not owned and not Path(rows[0]['output']).exists()
            if mode=='waiting_interrupt':raise InterruptedError('fixture wait interrupt')
        else:real_sleep(seconds)
    monkeypatch.setattr(m.time,'sleep',sleep)
    admitted=[]
    def admit(*a):
        assert not owned
        if mode=='audit_failure':raise ValueError('final audit rejected')
        admitted.append(True);return {'fixture':True}
    monkeypatch.setattr(m.storage,'admit_reference',admit)
    original=m.phase
    monkeypatch.setattr(m,'phase',lambda root,row,guard,**kw:original(root,row,guard,verify=fixtures.verify,poll=.01))
    if mode=='success':
        report=m.run('fixture')
        assert admitted and len(owned)==2 and not report['goal_complete']
        assert report['remaining']==['quality','Ollama','report']
    else:
        with pytest.raises((ValueError,InterruptedError)):m.run('fixture')
        assert not Path(rows[1]['output']).exists()
        assert len(owned)==(1 if mode=='stage_failure' else 0)
        state=json.loads((root/'state.json').read_text())
        assert state['phase']=='error' and state['children_preserved'] and not state['automatic_retry']
        assert not (root/'.done').exists()


@pytest.mark.parametrize('name',['native','resume','retention','hf','vllm','serving'])
def test_stage_source_reference_cannot_be_substituted(tmp_path,name):
    child=dict(final_storage={'wrong':True},code={},inputs={})
    atomic_json(tmp_path/'contract.json',{'source':child} if name=='hf' else child)
    with pytest.raises(ValueError,match='another source/storage'):
        m.verify_stage({'reference':{'correct':True}},dict(name=name,output=str(tmp_path)))


def test_start_launches_only_manager_with_explicit_tmp_and_threads(prepared_source,monkeypatch):
    s=prepared_source;plan=m.prepare(s.control);calls=[]
    def launch(directory,command,env,binding):
        calls.append(command)
        assert directory==m.manager_root(s.control)/'service'
        assert command[2]==str(resolve(m.OWN)) and command[-1]=='--run'
        assert env['TMPDIR']=='/tmp' and env['OMP_NUM_THREADS']=='2'
        assert binding['plan_sha256']==sha256_file(m.manager_root(s.control)/'plan.json')
        return {'fixture':True}
    monkeypatch.setattr('scripts.launch_independent_services.launch_once',launch)
    assert m.start(s.control)=={'fixture':True} and len(calls)==1


@pytest.mark.parametrize('fault',[None,'memory','gpu','cpu','future_space'])
def test_capacity_checks_future_peak_and_current_hardware(monkeypatch,fault):
    gpus=['GPU'+str(i) for i in range(8)]
    contract=dict(max_pss_gib=384,gpu_identity=gpus)
    hardware=dict(gpus=gpus,cpu={'effective':96},memory={'cache_aware_admission':{'headroom_gib':656}})
    if fault=='memory':hardware['memory']['cache_aware_admission']['headroom_gib']=655
    elif fault=='gpu':hardware['gpus']=gpus[:-1]
    elif fault=='cpu':hardware['cpu']['effective']=1
    monkeypatch.setattr('scripts.resource_admission.inspect',lambda:hardware)
    monkeypatch.setattr(m,'enforce_shared_space',lambda *a:{})
    def future(*a):
        if fault=='future_space':raise RuntimeError('future local capacity')
        return {'fixture':True}
    monkeypatch.setattr(m,'future_local_capacity',future)
    if fault:
        with pytest.raises(RuntimeError):m.capacity('fixture',contract)
    else:assert m.capacity('fixture',contract)['combined_memory_gib']==656
