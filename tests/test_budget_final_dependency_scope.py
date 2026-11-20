"""Metadata scope checks do not claim actual access isolation or GPU proof."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from scripts.budget_final_dependency_scope import dependency_scope


@pytest.fixture
def actual_documents():
    return (json.loads(Path('runs/3b_full_50k_v1/contract.json').read_text()),
            json.loads(Path('runs/main_training_50k_plan_v1/actual50k_parent_scope_review_v1.json').read_text()))


def test_exact_three_protected_and_local_paths(actual_documents):
    c,r=actual_documents;v=dependency_scope(c,r)
    assert len(v['paths'])==6 and len(set(v['paths']))==6
    assert [Path(p).name for p in v['paths']]==['ckpt_0008000']*2+['ckpt_0010000']*2+['ckpt_0016000']*2
    assert v['own_checkpoint'] not in v['paths']
    assert not any(v[k] for k in ('existence_checked','isolation_enforced','actual8gpu_verified','retirement_authorized','goal_complete'))


@pytest.mark.parametrize('fault',['missing','steps','owner','original','protected','local_alias','old_target'])
def test_wrong_or_aliased_scopes_are_rejected(actual_documents,fault):
    c,r=deepcopy(actual_documents)
    if fault=='missing':r['entries'].pop()
    elif fault=='steps':r['entries'][2]['step']=15500
    elif fault=='owner':r['entries'][0]['owner']=c['main_run']
    elif fault=='original':r['entries'][1]['original_checkpoint']=r['entries'][1]['protected_checkpoint']
    elif fault=='protected':r['entries'][2]['protected_checkpoint']=c['main_run']+'/training/ckpt_0050000'
    elif fault=='local_alias':
        # Same exact local path requires the same checkpoint name as well; force
        # an unsafe scope nested in the protected final checkpoint instead.
        c['retention_branches'][0]['policy']['local_root']=c['main_run']+'/training/ckpt_0050000'
    elif fault=='old_target':c['target_steps']=57000
    with pytest.raises(ValueError):dependency_scope(c,r)
