from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import ast
import pytest
from common.data_contracts import resolve
from scripts.observe_budget_training_v2 import admit_source


def input_values():
    source=resolve('runs/3b_full_50k_v1')
    output=resolve('runs/3b_50k_transition_v1/checkpoint_observer_v2')
    contract=dict(purpose='main_training',main_training_authorized=True,main_run=str(source),
        shared_storage={'nfs_pools':{'transition':{'paths':[str(output.parent)],'budget_bytes':2**30}}})
    return source,output,contract,dict(max_steps=50000)


def test_exact_output_pool_and_actual_handoff_gates():
    source,out,c,cfg=input_values()
    with patch('train.budget_horizon_handoff.validate_configuration') as conf, \
         patch('train.budget_horizon_handoff.load_handoff') as handoff, \
         patch('scripts.observe_budget_training_v2.size_tree',return_value=0):
        admit_source(source,out,c,cfg);conf.assert_called_once();handoff.assert_called_once()
        handoff.side_effect=ValueError('handoff proof missing')
        with pytest.raises(ValueError,match='handoff proof'):admit_source(source,out,c,cfg)


@pytest.mark.parametrize('fault',['output','source','target','authorization','pool','budget','full'])
def test_wrong_scope_or_unavailable_budget_is_rejected(fault):
    source,out,c,cfg=input_values();used=0
    if fault=='output':out=out.with_name('other')
    elif fault=='source':c['main_run']=str(resolve('runs/other'))
    elif fault=='target':cfg['max_steps']=57000
    elif fault=='authorization':c['main_training_authorized']=False
    elif fault=='pool':c['shared_storage']['nfs_pools']['transition']['paths']=[str(resolve('runs/other'))]
    elif fault=='budget':c['shared_storage']['nfs_pools']['transition']['budget_bytes']=2**29
    elif fault=='full':used=2**30
    with patch('train.budget_horizon_handoff.validate_configuration'), \
         patch('train.budget_horizon_handoff.load_handoff'), \
         patch('scripts.observe_budget_training_v2.size_tree',return_value=used):
        with pytest.raises(ValueError):admit_source(source,out,c,cfg)


def test_exact_memory_metadata_caps_and_no_signals_or_deletes():
    path=Path('scripts/observe_budget_training_v2.py');text=path.read_text();tree=ast.parse(text)
    assert 'max_rss_mib=2048' in text and 'size_tree(output)>32*2**20' in text
    calls=[n.func for n in ast.walk(tree) if isinstance(n,ast.Call)]
    assert not any(isinstance(f,ast.Attribute) and f.attr in ('kill','killpg','terminate','unlink','rmtree') for f in calls)
