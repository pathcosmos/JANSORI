"""Explicitly defer unused final-stage reservations without changing training.

No deletion, process signals or implicit contract publication. Existing space
guards, per-pool caps, checkpoint capacity and200GiB floors remain intact.
"""
from copy import deepcopy
from pathlib import Path

from scripts.tiered_checkpoint_retention import exact_path, size_tree

DEFERRED = ('final_native', 'final_resume', 'final_retention', 'hf_and_serving')
METADATA_ALLOWANCE = 16 * 2**20
SCOPE = 'defer_unused_final_pools_during57k_training'


def revised_contract(original, approval):
    if (approval.get('authorized') is not True or approval.get('scope') != SCOPE
            or approval.get('target_step') != 57000 or approval.get('resume_step') != 10500
            or approval.get('deferred_pool_names') != list(DEFERRED)
            or not approval.get('user_decision')):
        raise ValueError('Explicit approved10500 storage revision required')
    if (original.get('purpose') != 'main_training' or original.get('target_steps') != 57000
            or original.get('storage_phase_revision') is not None):
        raise ValueError('Expected unmodified57k production execution contract')
    spec = original['shared_storage']
    if spec['reserve_bytes'] < 200*2**30 or spec['temporary_budget_bytes'] != 1024*2**30:
        raise ValueError('Existing physical safety floors must remain')
    policy = original['tiered_retention']
    if (policy['nfs_keep'] != 3 or policy['local_keep'] != 15
            or not policy.get('adaptive_local_pruning') or policy.get('resume_from_local') is not False):
        raise ValueError('Existing3/adaptive15 and NFS resume guarantee required')
    updated = deepcopy(original);deferred = {}
    main = exact_path(Path(original['main_run']))
    for name in DEFERRED:
        pool = original['shared_storage']['nfs_pools'][name]
        if len(pool['paths']) != 1:
            raise ValueError('Expected exact single final-stage namespace')
        path = exact_path(Path(pool['paths'][0]))
        if path == main or path.is_relative_to(main) or main.is_relative_to(path):
            raise ValueError('Cannot defer main/protected checkpoint capacity')
        # Final-stage controllers have not been created. Never shrink a live
        # or partly prepared namespace just because its current bytes are small.
        if path.exists():
            raise ValueError('Final-stage namespace already exists; inspect before deferral')
        if size_tree(path) != 0 or pool['budget_bytes'] <= METADATA_ALLOWANCE:
            raise ValueError('Expected unused future reservation')
        deferred[name] = deepcopy(pool)
        updated['shared_storage']['nfs_pools'][name]['budget_bytes'] = METADATA_ALLOWANCE
    updated['storage_phase_revision'] = dict(
        version=1, scope=SCOPE, resume_step=10500, target_step=57000,
        deferred_pools=deferred, unused_pool_metadata_cap_bytes=METADATA_ALLOWANCE,
        restore_before_final_stage=True, final_stage_execution_authorized_by_this_revision=False,
        safety_floor_bytes=spec['reserve_bytes'], training_checkpoint_reservation_changed=False,
        local_history_and_temp_budgets_changed=False,
        meaning='Final-stage reservations are deferred, not deleted data or globally disabled capacity protection.')
    return updated
