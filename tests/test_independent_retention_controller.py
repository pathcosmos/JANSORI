"""Final controller consumes explicit independent source and retirement proofs."""
from copy import deepcopy
from pathlib import Path
import json
from types import SimpleNamespace

import pytest
from common.data_contracts import resolve,sha256_file,atomic_json
from scripts.run_restored_retention_audit import require_result,make_contract
from scripts.independent_parent_retention import CODE as PARENT_CODE
from scripts.independent_final_source import CODE as SOURCE_CODE
from test_restored_retention_controller import result_fixture


def independent_result():
    result,contract=result_fixture();reference=dict(control_dir='fixture',runtime_contract_sha256='a'*64)
    contract['independent_runtime']=reference;result['independent_runtime']=reference
    for name in set(PARENT_CODE+SOURCE_CODE):
        contract['code'][name]=result['code'][name]=sha256_file(resolve(name))
    result['training_audit'].update(status='passed_independent_runtime_target_source_audit',production_runtime_verified=True,parent_payload_read=False)
    content=result['protected_parents'][0];result['protected_parents']=[]
    result.update(protected_nfs_payload_count=0,total_nfs_checkpoint_count=3,
        retired_parents=[dict(step=s,status='verified_retired_parent_optional_local',nfs_resume_candidate=False,
            local_namespace_missing=False,local_content=deepcopy(content),full_payload_sha_refreshed=True) for s in [8000,10000]])
    return result,contract


def test_final_three_only_with_retirement_proofs_and_optional_total_archive_loss():
    result,contract=independent_result();require_result(result,contract)
    for row in result['retired_parents']:row.update(local_namespace_missing=True,local_content=None,full_payload_sha_refreshed=False)
    require_result(result,contract)


@pytest.mark.parametrize('fault',['extra_nfs','protected_left','missing_parent','wrong_steps','local_resume','lost_counted','partial_loss','missing_rng','implementation','legacy_audit'])
def test_independent_final_verdict_rejects_missing_proof_and_false_resident_counts(fault):
    result,contract=independent_result()
    if fault=='extra_nfs':result['total_nfs_checkpoint_count']=5
    elif fault=='protected_left':result['protected_nfs_payload_count']=2
    elif fault=='missing_parent':result['retired_parents'].pop()
    elif fault=='wrong_steps':result['retired_parents'][0]['step']=9500
    elif fault=='local_resume':result['retired_parents'][0]['nfs_resume_candidate']=True
    elif fault=='lost_counted':
        for row in result['retired_parents']:row.update(local_namespace_missing=True,full_payload_sha_refreshed=False)
    elif fault=='partial_loss':result['retired_parents'][0]['local_namespace_missing']=True
    elif fault=='missing_rng':result['retired_parents'][0]['local_content']['rng_ranks']=7
    elif fault=='implementation':result['code'][PARENT_CODE[0]]='0'*64
    elif fault=='legacy_audit':result['training_audit']['status']='passed_restored_committed_prefix_audit'
    with pytest.raises(ValueError):require_result(result,contract)


def test_prepare_binds_same_native_resume_runtime_and_retirement_plan(tmp_path,monkeypatch):
    main=tmp_path/'main';native=tmp_path/'native';resume=tmp_path/'resume';control=tmp_path/'control'
    for p in [main,native,resume,control]:p.mkdir()
    reference=dict(control_dir=str(control),runtime_contract_sha256='a'*64)
    roots=[tmp_path/'old',tmp_path/'middle',main]
    for i,p in enumerate(roots):atomic_json(p/'training/training_contract.json',dict(id=str(i),source_order=['ko']))
    atomic_json(main/'main.json',dict(model='3b',max_steps=57000,global_tokens=2097152))
    auth=main/'auth.json';atomic_json(auth,{'fixture':True})
    mc=dict(configurations={'main.json':sha256_file(main/'main.json')},code={},evidence={},retention_authorization=str(auth),
        retention_revision=dict(authorization=str(auth)),retention_branches=[dict(run=str(p)) for p in roots],tiered_retention=dict(nfs_keep=3,adaptive_local_pruning=True))
    atomic_json(main/'contract.json',mc)
    nc=dict(purpose='final_native_main_evaluation',final_step=57000,main_run=str(main),code={},inputs={},independent_runtime=reference)
    rc=dict(purpose='restored_final_resume_controller',main_run=str(main),code={},inputs={},independent_runtime=reference)
    plan=dict(main_run=str(main),native_run=str(native),parent_step=57000,independent_runtime=reference)
    for p,v in [(native/'contract.json',nc),(resume/'contract.json',rc),(resume/'plan.json',plan),(resume/'prepared.json',{})]:atomic_json(p,v)
    retirement=control/'parent_retirement_plan.json';atomic_json(retirement,{'fixture':True})
    runtime=control/'runtime_contract.json';atomic_json(runtime,{'fixture':True})
    view=SimpleNamespace(binding=dict(parent_retirement=dict(path=str(retirement))))
    monkeypatch.setattr('scripts.supervise_extended_training.require_authorization',lambda *a:None)
    monkeypatch.setattr('scripts.independent_final_source.bind_source',lambda *a:dict(reference=reference,code={},inputs={str(runtime):sha256_file(runtime)}))
    monkeypatch.setattr('scripts.independent_final_source.source_view',lambda *a:view)
    monkeypatch.setattr('scripts.independent_parent_retention.reviewed_plan',lambda *a:None)
    c=make_contract(main,native,resume,runtime_control=control)
    assert c['independent_runtime']==reference and c['wait_rss_gib']==2
    assert c['inputs'][str(retirement)]==sha256_file(retirement)
    assert c['durable_budget_gib']==1 and c['max_pss_gib']==16 and c['local_additional_budget_gib']==0
    rc['independent_runtime']=None;atomic_json(resume/'contract.json',rc)
    with pytest.raises(ValueError,match='runtime bindings'):make_contract(main,native,resume,runtime_control=control)
