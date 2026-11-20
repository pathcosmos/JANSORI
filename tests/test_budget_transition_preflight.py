"""Read-only real-source capacity/ownership and synthetic command rejection."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from common.data_contracts import resolve,sha256_file
from scripts.prepare_budget_run import storage_candidate
from scripts.budget_transition_control import validate_controllers,WaitingControllerBusy,SCOPE
from scripts.extended_horizon_storage import enforce_shared_space,GIB
from scripts.run_independent_followup import future_local_capacity


def test_current_source_four_owner_capacity_without_double_reserving_or_mutation():
    source=resolve('runs/3b_full_57k_v2/contract.json');before=source.read_bytes();old=json.loads(before)
    result=storage_candidate(old,run_id='a'*32);space=enforce_shared_space(resolve('runs/3b_full_50k_v1'),result)
    assert source.read_bytes()==before
    assert len(result['shared_storage']['local_temp_paths'])==4
    assert len(result['shared_storage']['local_history_paths'])==4
    assert space['local_temporary_used_bytes']+4*old['expected_checkpoint_bytes']*1.1+GIB<1024*GIB
    # After ONLY the approved four new probe copies are retired, serial final
    # native/resume/serving budgets fit. Existing old diagnostics stay resident.
    final=future_local_capacity(result,space)
    assert final['projected_final_temporary_bytes']<1024*GIB
    assert space['nfs_after_future_reserve_bytes']>0 and space['local_after_future_reserve_bytes']>0
    assert result['tiered_retention']['nfs_keep']==3 and result['tiered_retention']['local_keep']==15
    assert result['tiered_retention']['temp_root'].endswith('/3b_full_50k_v1')


def rows():
    answer={}
    for i,(name,(entry,flag,target)) in enumerate(SCOPE.items()):
        args=['python',entry,flag,target]
        if name=='observer':args+=['--source-run','runs/3b_full_57k_v2']
        answer[name]=dict(pid=900000+i,start_ticks=123,argv=args,cwd=str(resolve('.')),argv_sha256=str(i),uid=1100)
    return answer


def test_exact_waiting_controllers_and_busy_child_rejection():
    r=rows()
    with patch('scripts.budget_transition_control.process_record',side_effect=lambda pid:next(v for v in r.values() if v['pid']==pid)),patch('scripts.budget_transition_control.children',return_value=set()):
        validate_controllers(r)
    with patch('scripts.budget_transition_control.process_record',side_effect=lambda pid:next(v for v in r.values() if v['pid']==pid)),patch('scripts.budget_transition_control.children',return_value={123}):
        with pytest.raises(WaitingControllerBusy):validate_controllers(r)

@pytest.mark.parametrize('fault',['duplicate','entry','scope','source','missing'])
def test_foreign_controller_never_authorized(fault):
    r=rows()
    if fault=='duplicate':r['followup']['pid']=r['observer']['pid']
    elif fault=='entry':r['observer']['argv'][1]='scripts/unrelated.py'
    elif fault=='scope':r['observer']['argv'][3]='runs/unrelated'
    elif fault=='source':r['observer']['argv'][-1]='runs/unrelated'
    elif fault=='missing':r.pop('followup')
    with patch('scripts.budget_transition_control.process_record',side_effect=lambda pid:next(v for v in r.values() if v['pid']==pid)),patch('scripts.budget_transition_control.children',return_value=set()):
        with pytest.raises(ValueError):validate_controllers(r)
