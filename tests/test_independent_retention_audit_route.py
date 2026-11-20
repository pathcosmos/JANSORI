"""Route composition only: inventories/runtime mocked, metadata/locks are real."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from common.data_contracts import atomic_json,sha256_file
from scripts.audit_restored_retention import audit_run
from test_independent_retention_controller import independent_result


@pytest.fixture
def route(tmp_path,monkeypatch):
    roots=[tmp_path/n for n in ['old','middle','main']]
    for root in roots:root.mkdir();(root/'retention.lock').touch()
    main=roots[-1];configs=[dict(model='3b',max_steps=57000,ckpt_every=500) for _ in roots]
    parents=[{'id':name} for name in ['old','middle','child']];policy={'fixture_only':True}
    defs=[]
    for i,(root,cfg,parent) in enumerate(zip(roots,configs,parents)):
        atomic_json(root/'main.json',cfg);atomic_json(root/'training/training_contract.json',parent)
        bc=dict(configurations={'main.json':sha256_file(root/'main.json')},tiered_retention=policy)
        atomic_json(root/'contract.json',bc)
        lo,hi=[(500,8000),(8500,10000),(10500,57000)][i]
        defs.append(dict(run=str(root),policy=policy,first_step=lo,last_step=hi,contract_sha256=sha256_file(root/'contract.json'),stopped_capture={}))
    auth=main/'auth.json';atomic_json(auth,dict(authorized=True,retention_transfer_authorized=True,nfs_keep=3,local_max_keep=15,adaptive_local_oldest_first=True,
        original_run=str(roots[0]),source_run=str(roots[1]),new_run=str(main),boundary_step=10000))
    c=dict(configurations={'main.json':sha256_file(main/'main.json')},code={},evidence={},tiered_retention=policy,retention_branches=defs,
        retention_authorization=str(auth),retention_authorization_sha256=sha256_file(auth))
    atomic_json(main/'contract.json',c)
    atomic_json(main/'parent_handoff.json',dict(parent_metadata=dict(step=10000,training_contract=parents[1],config=configs[1]),original_contract=parents[0],parent_checkpoint=str(main/'parent_checkpoint/ckpt_0010000'),parent_stats={}))
    atomic_json(main/'parent_certificate.json',{})
    atomic_json(roots[1]/'parent_handoff.json',dict(parent_checkpoint=str(roots[1]/'parent_checkpoint/ckpt_0008000'),parent_stats={}))
    atomic_json(roots[1]/'parent_certificate.json',{});atomic_json(main/'lineage/original_metadata.json',dict(config=configs[0]))
    result,_=independent_result();result['branch_inventory'][2]['certificate_receipts']={}
    atomic_json(main/'report.json',dict(certificate_receipts={}))
    for name in ['scripts.supervise_extended_training.require_authorization','train.extended_handoff.validate_configuration','scripts.seal_horizon_handoff.require_parent_absent']:
        monkeypatch.setattr(name,lambda *a:None)
    monkeypatch.setattr('train.extended_handoff.load_handoff',lambda *a:pytest.fail('must not read protected parents'))
    monkeypatch.setattr('scripts.audit_restored_training.audit_run',lambda *a:pytest.fail('must not use original parent auditor'))
    view=SimpleNamespace(main=main,unchanged=lambda:None)
    monkeypatch.setattr('scripts.independent_final_source.source_view',lambda *a:view)
    monkeypatch.setattr('scripts.independent_final_source.admitted_candidate',lambda *a:parents[2])
    monkeypatch.setattr('scripts.audit_restored_retention.inventory_group',lambda *a,**kw:deepcopy(result))
    monkeypatch.setattr('scripts.extended_horizon_storage.enforce_shared_space',lambda *a:{'fixture':True})
    calls=[]
    def retired(*args):calls.append('retired');return result['retired_parents']
    def completed(*args):calls.append(('source',args));return None,None,None,result['training_audit']
    monkeypatch.setattr('scripts.independent_parent_retention.verify_retired_parents',retired)
    monkeypatch.setattr('scripts.independent_final_source.completed_source',completed)
    return SimpleNamespace(main=main,calls=calls,result=result)


def test_final_audit_uses_explicit_own_source_and_retirement_evidence_without_parents(route):
    reference={'fixture_runtime':'accepted_elsewhere'}
    result=audit_run(route.main,final=True,independent_runtime=reference)
    assert result['protected_parents']==[] and result['retired_parents']==route.result['retired_parents']
    assert result['protected_nfs_payload_count']==0 and result['total_nfs_checkpoint_count']==3
    assert route.calls==['retired',('source',(route.main,reference))]
    assert not result['goal_complete'] and not (route.main/'parent_checkpoint').exists()


def test_missing_retirement_never_reaches_final_source_pass(route,monkeypatch):
    def fail(*a):raise ValueError('No verified retirement')
    monkeypatch.setattr('scripts.independent_parent_retention.verify_retired_parents',fail)
    with pytest.raises(ValueError,match='retirement'):audit_run(route.main,final=True,independent_runtime={})
    assert not route.calls
