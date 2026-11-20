"""Synthetic final50k verdict rejection; no actual final/GPU completion claim."""
from copy import deepcopy
import pytest
from scripts.run_budget_retention_audit import require_result
from scripts.budget_final_lineage import intervals


def fixture():
    full=dict(model_tensors=255,state_tensors=1020,rng_ranks=8,payload_files=8,full_payload_sha_refreshed=True)
    nfs={f'ckpt_{s:07d}':dict(full) for s in [49000,49500,50000]}
    rows=[];expected=[];segments=[]
    for i,((a,b),(lo,hi)) in enumerate(zip(intervals(16000),intervals(16000,checkpoints=True))):
        root='runs/new' if i==3 else 'runs/prior'+str(i);cid='identity'+str(i);tokens=(b-a)*2097152
        rows.append(dict(root=root,local_root='/tmp/fixture-'+str(i),training_contract_id=cid,nfs=nfs if i==3 else {},local={},
            certificates={f'ckpt_{s:07d}':{} for s in range(lo,hi+1,500)},local_namespace_missing=False))
        expected.append(dict(root=root,contract_id=cid,start_step_exclusive=a,through_step=b))
        segments.append(dict(contract_id=cid,start_step_exclusive=a,through_step=b,
            committed_step_rank_pairs=(b-a)*8,committed_tokens=tokens,source_tokens={'fixture':tokens},
            persisted_attempt_tokens_through_prefix=tokens,replayed_tokens_through_prefix=0))
    c=dict(main_run='runs/new',source_contract_sha256='source',code={'scripts/audit_budget_retention.py':'audit'},inputs={},expected_segments=expected,expected_source_order=['fixture'])
    r=dict(status='passed_final_budget_retention_content',production=True,final=True,full_payload_sha_refreshed=True,
        source_run='runs/new',through_step=50000,issues=[],residues=[],overlap=[],nfs=nfs,local={},
        nfs_resume_candidates=sorted(nfs),expected_nfs=sorted(nfs),expected_local=[],distinct_checkpoints=3,
        certificate_count=100,branch_inventory=rows,protected_parent_counted_as_normal_history=False,
        protected_parents=[dict(full) for _ in range(3)],metadata_evidence={'runs/new/contract.json':'source'},code=c['code'],
        training_audit=dict(status='passed_budget_committed_prefix_audit',source_run='runs/new',target_steps=50000,
            validation_source=False,through_step=50000,counts=dict(status='passed_ledger_accounting_only',target_steps=50000,
                through_step=50000,committed_step_rank_pairs=400000,committed_tokens=104857600000,
                source_tokens={'fixture':104857600000},persisted_attempt_tokens_through_prefix=104857600000,
                replayed_tokens_through_prefix=0,successful_target_ranks=list(range(8)),segments=segments)))
    return r,c


def test_correct_four_generation_counts_and_explicit_three_parent_exception():
    r,c=fixture();require_result(r,c)
    counts=r['training_audit']['counts'];counts['segments'][2]['replayed_tokens_through_prefix']=2097152
    counts['segments'][2]['persisted_attempt_tokens_through_prefix']+=2097152
    counts['persisted_attempt_tokens_through_prefix']+=2097152;counts['replayed_tokens_through_prefix']=2097152
    require_result(r,c)

@pytest.mark.parametrize('fault',['target','old_total','missing_rank','three_owners','wrong_identity','gap','replay','parent','local_latest','certificate_count'])
def test_final_failure_cannot_be_relabelled_success(fault):
    r,c=fixture();counts=r['training_audit']['counts']
    if fault=='target':r['through_step']=57000
    elif fault=='old_total':counts['committed_tokens']=119537664000
    elif fault=='missing_rank':counts['successful_target_ranks'].pop()
    elif fault=='three_owners':r['branch_inventory'].pop(1)
    elif fault=='wrong_identity':counts['segments'][2]['contract_id']='identity1'
    elif fault=='gap':counts['segments'][3]['start_step_exclusive']=16500
    elif fault=='replay':counts['segments'][2]['replayed_tokens_through_prefix']=2097152
    elif fault=='parent':r['protected_parents'].pop()
    elif fault=='local_latest':r['nfs_resume_candidates'].pop()
    elif fault=='certificate_count':r['certificate_count']=114
    with pytest.raises(ValueError):require_result(r,c)
