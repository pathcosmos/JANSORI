"""Exclude replay and distinguish partial-rank work after a saved boundary."""
import pytest
from scripts.seal_budget_handoff import discarded_suffix

def rows(lengths):
    return [dict(rank=r,contract_id='source',steps=[dict(step=16001+i,source_sequences={'fixture':64}) for i in range(n)]) for r,n in enumerate(lengths)]

def test_partial_rank_updates_not_counted_as_full_global_steps():
    result=discarded_suffix(rows([3,3,3,3,3,3,3,2]),step=16000,contract_id='source')
    assert result['all8_persisted_steps']==[16001,16002]
    assert result['persisted_step_rank_pairs']==23 and result['partial_rank_tokens']==7*262144
    assert not result['credited_to_new_committed_total']

@pytest.mark.parametrize('fault',['missing','identity','gap','geometry'])
def test_wrong_scope_rejected(fault):
    value=rows([2]*8)
    if fault=='missing':value.pop()
    elif fault=='identity':value[7]['contract_id']='other'
    elif fault=='gap':value[7]['steps'][0]['step']=16003
    else:value[7]['steps'][0]['source_sequences']['fixture']=63
    with pytest.raises(ValueError):discarded_suffix(value,step=16000,contract_id='source')
