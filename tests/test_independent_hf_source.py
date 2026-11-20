"""Real file/SHA/source wiring; accepted GPU runtime and tensor content mocked.

These tests are not actual57k completion or independent8GPU acceptance.
"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from common.data_contracts import atomic_json,sha256_file,artifact_path,resolve
from inference import restored_final_source as source
from scripts import independent_final_source as independent
from scripts import export_restored_hf as export
from scripts import run_restored_vllm as runtime
from scripts import run_restored_serving as serving
from test_restored_hf_source import env,attest


@pytest.fixture
def explicit(env,monkeypatch):
    e=env;control=e['b']/'control';control.mkdir()
    path=control/'runtime_contract.json';atomic_json(path,{'synthetic_gpu_acceptance':True})
    ref=dict(control_dir=str(control),runtime_contract_sha256=sha256_file(path))
    binding=dict(reference=ref,code={n:sha256_file(resolve(n)) for n in independent.CODE},
                 inputs={artifact_path(path):sha256_file(path)})
    plan=json.loads((e['resume']/'plan.json').read_text());plan['independent_runtime']=ref
    atomic_json(e['resume']/'plan.json',plan)
    monkeypatch.setattr('scripts.run_restored_final_resume.load_plan',lambda _:plan)
    for key in ('native','resume','retention'):
        p=e[key];c=json.loads((p/'contract.json').read_text());c['independent_runtime']=ref
        atomic_json(p/'contract.json',c)
        atomic_json(p/'.done',dict(report_sha256=sha256_file(p/'report.json'),contract_sha256=sha256_file(p/'contract.json')))
    def forbidden(*args,**kwargs):raise AssertionError('Original parent payload route forbidden')
    monkeypatch.setattr('train.extended_handoff.load_handoff',forbidden)
    monkeypatch.setattr(independent,'bind_source',lambda main,ctrl:binding)
    monkeypatch.setattr(independent,'admitted_candidate',lambda main,reference:e['parent'])
    directory=control/'final_source_v1'
    atomic_json(directory/'revision.json',{'synthetic':True});atomic_json(directory/'review.json',{'synthetic':True})
    atomic_json(directory/'prepared.json',dict(revision_sha256=sha256_file(directory/'revision.json'),
        review_sha256=sha256_file(directory/'review.json'),production_adoption_authorized=False))
    completion={'certificate_sha256':sha256_file(resolve(e['audit']['checkpoint_certificate']))}
    view=SimpleNamespace(main=e['main'],control=control,path=path,digest=sha256_file(path),
        acceptance={'synthetic_gpu_acceptance':True},observe=lambda:{'completion':completion},unchanged=lambda:None)
    # Exercise the actual audit verifier, replacing only the accepted GPU RuntimeView.
    monkeypatch.setattr(independent,'source_view',lambda main,reference:view)
    e['audit'].update(status='passed_independent_runtime_target_source_audit',production_runtime_verified=True,
        parent_payload_read=False,independent_runtime=dict(reference=ref,acceptance=view.acceptance,completion=completion,
        revision=str(directory/'revision.json'),revision_sha256=sha256_file(directory/'revision.json')))
    e['audit']['evidence'][artifact_path(path)]=sha256_file(path)
    def completed(main,reference):
        assert main==e['main'] and reference==ref
        return e['cp'],e['proof'],e['meta'],e['audit']
    monkeypatch.setattr('scripts.evaluate_restored_native.completed_main',completed)
    e.update(control=control,reference=ref,binding=binding,plan=plan,view=view)
    e['c']=source.make_contract(e['main'],e['root'],e['native'],e['resume'],e['retention'],runtime_control=control)
    return e


def test_legacy_source_never_labels_native_reference_as_runtime(env):
    live=attest(env)
    assert 'independent_runtime' not in live.binding


def test_explicit_source_attestation_preserves_runtime_and_fresh_pins(explicit):
    e=explicit;live=attest(e);live.check()
    assert live.binding['independent_runtime']==e['reference']
    assert live.binding['goal_complete'] is False
    assert live.binding['committed_tokens']==119537664000
    assert live.binding['committed_step_rank_pairs']==456000
    stored=json.loads((e['root']/'source/attestation.json').read_text())
    assert stored['binding']['independent_runtime']==e['reference']
    assert all(e['c']['inputs'][k]==v for k,v in e['binding']['inputs'].items())
    assert e['calls']==['native','fresh_inputs']
    (e['control']/'runtime_contract.json').write_text('{}')
    with pytest.raises(ValueError,match='changed'):live.check()


@pytest.mark.parametrize('dependency',['native','resume','retention','plan'])
def test_mixed_runtime_dependencies_fail_before_export(explicit,dependency):
    e=explicit
    if dependency=='plan':e['plan']['independent_runtime']={}
    else:
        p=e[dependency]/'contract.json';c=json.loads(p.read_text());c['independent_runtime']={};atomic_json(p,c)
    with pytest.raises(ValueError,match='runtime bindings differ'):
        source.make_contract(e['main'],e['root'],e['native'],e['resume'],e['retention'],runtime_control=e['control'])
    assert not (e['root']/'source').exists()


@pytest.mark.parametrize('fault',['prepared_only','unaccepted','parent_read','completion','revision','marker','rank','tokens'])
def test_incomplete_runtime_or_final_accounting_rejected(explicit,fault):
    e=explicit;a=e['audit']
    if fault=='prepared_only':a['status']='prepared_independent_prefix_audit_not_runtime_accepted'
    if fault=='unaccepted':a['production_runtime_verified']=False
    if fault=='parent_read':a['parent_payload_read']=True
    if fault=='completion':a['independent_runtime']['completion']={}
    if fault=='revision':a['independent_runtime']['revision_sha256']='0'*64
    if fault=='marker':atomic_json(e['control']/'final_source_v1/prepared.json',{})
    if fault=='rank':a['counts']['successful_target_ranks'].pop()
    if fault=='tokens':a['counts']['committed_tokens']-=2097152
    with pytest.raises(ValueError):attest(e)
    assert not (e['root']/'source').exists()


def test_export_contract_and_wait_route_explicit_runtime(explicit,monkeypatch):
    e=explicit;c=export.make_contract(e['main'],e['root'],e['native'],e['resume'],e['retention'],runtime_control=e['control'])
    assert c['source']['independent_runtime']==e['reference']
    def observe(main,reference):
        assert main==artifact_path(e['main']) and reference==e['reference']
        return {'complete':False}
    monkeypatch.setattr('scripts.evaluate_restored_native.main_observation',observe)
    monkeypatch.setattr(export,'dependency_ready',lambda *a:True)
    assert export.ready(c)['ready'] is False


def test_vllm_and_api_contracts_preserve_explicit_reference(tmp_path,monkeypatch):
    ref={'control_dir':str(tmp_path/'control'),'runtime_contract_sha256':'a'*64}
    src=dict(main_run='runs/3b_full_57k_v2',tokenizer='tokenizer/candidate_v1/tokenizer.json',
        prompts='runs/f7_preflight_v1/prompts.jsonl',independent_runtime=ref)
    monkeypatch.setattr(export,'load_contract',lambda _:dict(source=src,code={},inputs={}))
    atomic_json(tmp_path/'contract.json',{});atomic_json(tmp_path/'prepared.json',{})
    c=runtime.make_contract(tmp_path,'runs/main_training_35k_plan_v1/serving_environment_v2')
    assert c['independent_runtime']==ref and c['engine']['tensor_parallel_size']==1
    run=Path(resolve(c['root']));atomic_json(run/'contract.json',c);atomic_json(run/'prepared.json',{})
    monkeypatch.setattr(runtime,'load_contract',lambda _:c)
    sc=serving.make_contract(run)
    assert sc['independent_runtime']==ref and sc['target_steps']==57000


@pytest.mark.parametrize('consumer',['offline','api'])
@pytest.mark.parametrize('mismatch',[False,True])
def test_inference_admission_rejects_mixed_runtime(tmp_path,monkeypatch,consumer,mismatch):
    ref={'control_dir':str(tmp_path/'control'),'runtime_contract_sha256':'a'*64}
    binding=dict(step=57000,independent_runtime=deepcopy(ref))
    atomic_json(tmp_path/'.done',{});atomic_json(tmp_path/'report.json',{})
    if mismatch:binding['independent_runtime']['runtime_contract_sha256']='b'*64
    report=dict(source_binding=binding,publication_manifest_sha256='fixture',manifest_sha256='fixture',
        status='passed_actual_final57k_vllm_inference')
    module=export if consumer=='offline' else runtime
    monkeypatch.setattr(module,'load_contract',lambda _: {})
    monkeypatch.setattr(module,'controller',lambda *a:report)
    c=dict(independent_runtime=ref,**{('export_root' if consumer=='offline' else 'runtime_root'):str(tmp_path)})
    fn=runtime.authenticate_export if consumer=='offline' else serving.authenticate_sources
    if mismatch:
        with pytest.raises(ValueError,match='runtime differs'):fn(c)
    else:assert fn(c)['source_binding']['independent_runtime']==ref
