"""Compute exact50k ancestor dependency isolation scope from reviewed metadata.

Pure scope derivation only: no filesystem permissions change, existence check,
GPU restoration, certification, deletion, or retirement authorization.
"""
from pathlib import Path
from common.data_contracts import resolve
from scripts.budget_final_lineage import contract_intervals


def dependency_scope(contract, reviewed):
    contract_intervals(contract, checkpoints=True)
    entries=reviewed.get('entries',[])
    branches=contract['retention_branches']
    boundary=contract['budget_execution']['parent_step']
    if (reviewed.get('status')!='reviewed_four_generation_parent_metadata_scope' or
            reviewed.get('parent_count')!=3 or len(entries)!=3 or
            [e.get('step') for e in entries]!=[8000,10000,boundary]):
        raise ValueError('Exact reviewed three-parent metadata required')
    own=resolve(contract['main_run'])/'training/ckpt_0050000'
    paths=[]
    for i,entry in enumerate(entries):
        step=entry['step']; name=f'ckpt_{step:07d}'
        owner=resolve(branches[i]['run']); next_owner=resolve(branches[i+1]['run'])
        protected=next_owner/'parent_checkpoint'/name
        original=owner/'training'/name
        local=resolve(branches[i]['policy']['local_root'])/'checkpoints'/name
        if (resolve(entry['owner'])!=next_owner or resolve(entry['previous_owner'])!=owner or
                resolve(entry['protected_checkpoint'])!=protected or
                resolve(entry['original_checkpoint'])!=original):
            raise ValueError('Protected/original checkpoint owner differs')
        paths.extend([protected,local])
    if len(set(paths))!=6 or any(p==own or own.is_relative_to(p) or p.is_relative_to(own) for p in paths):
        raise ValueError('Dependency scopes alias each other or own final checkpoint')
    return dict(status='derived_three_parent_six_dependency_paths_only',
        main_run=str(resolve(contract['main_run'])), own_checkpoint=str(own),
        paths=[str(p) for p in paths], parent_count=3, existence_checked=False,
        isolation_enforced=False, actual8gpu_verified=False, retirement_authorized=False,
        goal_complete=False)
