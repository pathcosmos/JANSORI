"""Four-generation final coverage and wrong-boundary rejection, CPU only."""
from copy import deepcopy
import pytest
from scripts.budget_final_lineage import intervals,contract_intervals,bound_intervals


def fixture():
    ranges=intervals(16000)
    segments=[dict(contract_id=str(i),start_step_exclusive=a,through_step=b) for i,(a,b) in enumerate(ranges)]
    contract=dict(target_steps=50000,main_run='runs/child',budget_execution={'parent_step':16000},
        retention_branches=[dict(run=r,first_step=a,last_step=b) for r,(a,b) in zip(
            ['runs/one','runs/two','runs/three','runs/child'],intervals(16000,checkpoints=True))])
    return contract,segments


def test_full_scope_and_original_id_counts():
    c,s=fixture();assert contract_intervals(c)==bound_intervals(s)==[(0,8000),(8000,10000),(10000,16000),(16000,50000)]
    assert sum((b-a)*8 for a,b in contract_intervals(c))==400000
    assert sum((b-a)*2097152 for a,b in contract_intervals(c))==104857600000

@pytest.mark.parametrize('boundary',[True,10000,16001,42500,50000])
def test_unreviewed_boundaries_rejected(boundary):
    with pytest.raises(ValueError):intervals(boundary)

@pytest.mark.parametrize('fault',['missing','gap','identity','owner','target'])
def test_bound_scope_rejects_consistent_relabelling(fault):
    c,s=fixture()
    if fault=='missing':s.pop()
    elif fault=='gap':s[2]['start_step_exclusive']=10500
    elif fault=='identity':s[3]['contract_id']=s[2]['contract_id']
    elif fault=='owner':c['retention_branches'][1]['run']='runs/one'
    elif fault=='target':c['target_steps']=57000
    with pytest.raises(ValueError):
        if fault in ('owner','target'):contract_intervals(c)
        else:bound_intervals(s)
