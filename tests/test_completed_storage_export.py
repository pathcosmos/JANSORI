"""Real sidecar SHA/contract wiring; final tensor/GPU receipts remain mocked."""
from copy import deepcopy
from pathlib import Path
from argparse import Namespace
import json

import pytest

from common.data_contracts import atomic_json, sha256_file
from scripts import completed_training_storage as storage
from scripts import run_restored_retention_audit as retention
from scripts import export_restored_hf as export
from scripts import run_restored_vllm as runtime
from scripts import run_restored_serving as serving
from inference import restored_final_source as source
from test_restored_hf_source import env, attest
from test_restored_retention_controller import result_fixture
from test_independent_retention_audit_route import route


@pytest.fixture
def separate(env, monkeypatch):
    e = env
    transition = e['b']/'transition'; transition.mkdir()
    auth = transition/'authorization.json'
    atomic_json(auth, dict(authorized=True, scope=storage.SCOPE, target_step=57000,
        restore_deferred_budget_before_final_work=True, deferred_pool_names=list(storage.DEFERRED)))
    mc = e['mc']
    mc.update(purpose='main_training', main_run=str(e['main']), target_steps=57000,
              tiered_retention=dict(nfs_keep=3, local_keep=15))
    pools = deepcopy(mc['shared_storage']['nfs_pools'])
    mc['storage_phase_revision'] = dict(scope=storage.SCOPE, authorization=str(auth),
        restore_before_final_stage=True, unused_pool_metadata_cap_bytes=storage.METADATA_ALLOWANCE,
        deferred_pools=pools)
    mc['evidence'][str(auth)] = sha256_file(auth)
    mc['shared_storage'].update(reserve_bytes=200*2**30, temporary_budget_bytes=1024*2**30)
    mc['shared_storage']['nfs_pools'] = {k: dict(paths=v['paths'], budget_bytes=storage.METADATA_ALLOWANCE)
                                         for k, v in pools.items()}
    mc['shared_storage']['nfs_pools']['transition'] = dict(paths=[str(transition)], budget_bytes=2**30)
    atomic_json(e['main']/'contract.json', mc)
    monkeypatch.setattr(storage, 'enforce_shared_space', lambda *a: {'fixture_capacity_only': True})
    control = transition/'storage'; storage.prepare(e['main'], control)
    binding = storage.bind(e['main'], control); reference = binding['reference']
    plan = json.loads((e['resume']/'plan.json').read_text()); plan['final_storage'] = reference
    atomic_json(e['resume']/'plan.json', plan)
    monkeypatch.setattr('scripts.run_restored_final_resume.load_plan', lambda _: plan)
    for key in ('native', 'resume', 'retention'):
        path = e[key]/'contract.json'; c = json.loads(path.read_text())
        c['final_storage'] = reference; c['code'].update(binding['code']); c['inputs'].update(binding['inputs'])
        atomic_json(path, c)
    for key in ('main', 'native', 'resume', 'retention'):
        path = e[key]
        atomic_json(path/'.done', dict(report_sha256=sha256_file(path/'report.json'),
                                      contract_sha256=sha256_file(path/'contract.json')))
    e.update(control=control, reference=reference, plan=plan)
    e['c'] = source.make_contract(e['main'],e['root'],e['native'],e['resume'],e['retention'],storage_control=control)
    return e


def test_hf_attestation_preserves_storage_reference_and_original_contract(separate):
    e = separate; original = (e['main']/'contract.json').read_bytes()
    live = attest(e); live.check()
    assert live.binding['final_storage'] == e['reference']
    assert live.binding['goal_complete'] is False
    assert (e['main']/'contract.json').read_bytes() == original
    assert e['c']['inputs'][str(e['control']/'plan.json')] == sha256_file(e['control']/'plan.json')
    (e['control']/'prepared.json').write_text('{}')
    with pytest.raises(ValueError): live.check()


@pytest.mark.parametrize('dependency', ['native', 'resume', 'retention', 'plan'])
def test_hf_refuses_mixed_storage_dependencies_before_publication(separate, dependency):
    e = separate
    if dependency == 'plan': e['plan']['final_storage'] = {}
    else:
        path = e[dependency]/'contract.json'; c=json.loads(path.read_text());c['final_storage']={};atomic_json(path,c)
    with pytest.raises(ValueError, match='separate storage bindings'):
        source.make_contract(e['main'],e['root'],e['native'],e['resume'],e['retention'],storage_control=e['control'])
    assert not (e['root']/'source').exists()


def test_retention_contract_uses_same_source_and_explicit_projected_budget(separate):
    e=separate
    c=retention.make_contract(e['main'],e['native'],e['resume'],storage_control=e['control'])
    assert c['final_storage']==e['reference']
    assert c['source_contract_sha256']==sha256_file(e['main']/'contract.json')
    assert retention.budget_contract(c)['shared_storage']['nfs_pools']['final_retention']['budget_bytes']==2**30
    assert json.loads((e['main']/'contract.json').read_text())['shared_storage']['nfs_pools']['final_retention']['budget_bytes']==storage.METADATA_ALLOWANCE


def test_retention_cannot_hide_the_two_protected_nfs_parents(monkeypatch):
    result,c=result_fixture();reference={'control_dir':'fixture','plan_sha256':'fixture'}
    c['final_storage']=reference;result.update(final_storage=reference,protected_nfs_payload_count=2,total_nfs_checkpoint_count=5)
    monkeypatch.setattr(retention,'budget_contract',lambda _: {'synthetic':True})
    retention.require_result(result,c)
    for key,value in [('total_nfs_checkpoint_count',3),('protected_nfs_payload_count',0),('final_storage',{})]:
        bad=deepcopy(result);bad[key]=value
        with pytest.raises(ValueError):retention.require_result(bad,c)


@pytest.mark.parametrize('entry', ['retention', 'export', 'runtime', 'serving'])
def test_incomplete_source_refuses_output_creation(tmp_path,monkeypatch,entry):
    reference={'control_dir':'fixture','plan_sha256':'fixture'}
    c={'main_run':'fixture-main','final_storage':reference}
    def fail(*a):raise RuntimeError('Training source still active')
    monkeypatch.setattr(storage,'admit_reference',fail)
    if entry=='retention':
        module=retention;root=tmp_path/'retention'
        args=Namespace(worker=False,prepare=True,run_dir=str(root),main_run='unused',native_run='unused',resume_run='unused')
    elif entry=='export':
        module=export;root=tmp_path/'export';c={'source':c}
        args=Namespace(worker=False,prepare=True,run_dir=str(root),main_run='unused',native_run='unused',resume_run='unused',retention_run='unused')
    elif entry=='runtime':
        module=runtime;root=tmp_path/'vllm_runtime_v1'
        args=Namespace(prepare=True,export_run=str(tmp_path),environment='unused')
    else:
        module=serving;root=tmp_path/'vllm_serving_v1'
        args=Namespace(prepare=True,runtime_run=str(tmp_path/'vllm_runtime_v1'))
    monkeypatch.setattr(module,'make_contract',lambda *a,**k:c)
    monkeypatch.setattr(module,'admission',lambda *a:pytest.fail('must fail before resource/output setup'))
    with pytest.raises(RuntimeError,match='still active'):module.main(args)
    assert not root.exists()


def test_runtime_and_api_refuse_changed_storage_reference(tmp_path,monkeypatch):
    c={'export_root':str(tmp_path),'final_storage':{'plan_sha256':'original'}}
    monkeypatch.setattr(export,'load_contract',lambda _: {'source':{'final_storage':{'plan_sha256':'changed'}}})
    with pytest.raises(ValueError,match='separate storage'):runtime.admission(tmp_path,c)
    monkeypatch.setattr(runtime,'load_contract',lambda _: {'final_storage':{'plan_sha256':'changed'}})
    c['runtime_root']=str(tmp_path)
    with pytest.raises(ValueError,match='separate storage'):serving.admission(tmp_path,c)


def test_final_retention_auditor_accounts_projected_capacity_and_keeps_parents(route,monkeypatch):
    from scripts import audit_restored_retention as auditor
    main=route.main; original=(main/'contract.json').read_bytes()
    reference={'control_dir':str(main/'storage'), 'plan_sha256':'synthetic'}
    evidence=main/'storage/plan.json';atomic_json(evidence,{'fixture':'separate storage'})
    code={storage.OWN:sha256_file(Path(storage.__file__))}
    monkeypatch.setattr(storage,'bind',lambda *a:dict(reference=reference,code=code,inputs={str(evidence):sha256_file(evidence)}))
    projected={'fixture_projected_final_capacity':True}
    monkeypatch.setattr(storage,'view',lambda *a:projected)
    receipt=json.loads((main/'parent_handoff.json').read_text())
    parent=json.loads((main/'training/training_contract.json').read_text())
    monkeypatch.setattr('train.extended_handoff.load_handoff',lambda *a:(receipt,parent,{}))
    monkeypatch.setattr('scripts.audit_restored_training.audit_run',lambda *a:route.result['training_audit'])
    monkeypatch.setattr(auditor,'file_stats',lambda *a:{})
    parents=[]
    def content(path,*a,**k):
        parents.append(path);return {'synthetic_parent_content':True}
    monkeypatch.setattr(auditor,'checkpoint_content',content)
    capacities=[]
    monkeypatch.setattr('scripts.extended_horizon_storage.enforce_shared_space',
                        lambda root,c: capacities.append(c) or {'fixture':True})
    result=auditor.audit_run(main,final=True,final_storage=reference)
    assert capacities==[projected] and len(parents)==2
    assert result['final_storage']==reference
    assert result['protected_nfs_payload_count']==2 and result['total_nfs_checkpoint_count']==5
    assert result['metadata_evidence'][str(evidence)]==sha256_file(evidence)
    assert (main/'contract.json').read_bytes()==original
    assert not route.calls  # No independent-runtime retirement or source path.


def test_vllm_and_serving_propagate_the_same_storage_reference(tmp_path,monkeypatch):
    reference={'control_dir':'synthetic-control','plan_sha256':'synthetic'}
    s=dict(main_run='runs/3b_full_57k_v2',tokenizer='tokenizer/candidate_v1/tokenizer.json',
           prompts='runs/f7_preflight_v1/prompts.jsonl',final_storage=reference)
    monkeypatch.setattr(export,'load_contract',lambda _:dict(source=s,code={},inputs={}))
    atomic_json(tmp_path/'contract.json',{});atomic_json(tmp_path/'prepared.json',{})
    c=runtime.make_contract(tmp_path,'runs/main_training_35k_plan_v1/serving_environment_v2',local_environment_cap_gib=60)
    assert c['final_storage']==reference and c['local_environment_cap_gib']==60
    root=tmp_path/'vllm_runtime_v1';atomic_json(root/'contract.json',c);atomic_json(root/'prepared.json',{})
    monkeypatch.setattr(runtime,'load_contract',lambda _:c)
    api=serving.make_contract(root)
    assert api['final_storage']==reference and api['local_environment_cap_gib']==60
