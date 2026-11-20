"""Phase-aware40k migration capacity plan, never a deletion or launch policy."""
import math
from pathlib import Path
import shutil

GIB=2**30


class LocalHistoryCapacityError(RuntimeError):
    pass


def enforce_runtime_space(root, contract, *, local_copy_busy=False):
    """Fresh shared-device admission, subtracting already allocated artifacts."""
    from scripts.tiered_checkpoint_retention import exact_path,size_tree
    root=exact_path(root)
    reservations=contract['space_reservations']
    nfs={str(root):contract['durable_budget_bytes'],**reservations['nfs']}
    if str(root) in reservations['nfs']:
        raise ValueError('Main run reservation duplicated')
    paths=[exact_path(Path(p)) for p in nfs]
    for i,p in enumerate(paths):
        if any(p.is_relative_to(q) or q.is_relative_to(p) for q in paths[:i]):
            raise ValueError('Overlapping permanent reservations double count storage')
    remaining=0;used={}
    for path,budget in nfs.items():
        if type(budget) is not int or budget<0:raise ValueError('Invalid permanent reservation')
        p=exact_path(path);ancestor=p
        while not ancestor.exists():ancestor=ancestor.parent
        if ancestor.stat().st_dev!=root.stat().st_dev:raise ValueError('Permanent reservation filesystem differs')
        used[path]=size_tree(p)
        if used[path]>budget:raise RuntimeError('Permanent artifact/residue budget exceeded: '+path)
        remaining+=budget-used[path]
    reserve=contract['tiered_retention']['reserve_bytes']
    if type(reserve) is not int or reserve<200*GIB:raise ValueError('Safety reserve reduced')
    nfs_free=shutil.disk_usage(root).free
    if nfs_free<remaining+reserve:raise RuntimeError('Combined NFS future/reserve budget unavailable')
    temps=[exact_path(p) for p in reservations['local_temp_paths']]
    if not temps or len(set(temps))!=len(temps):raise ValueError('Missing/duplicate temporary scope')
    local=exact_path(contract['tiered_retention']['local_root']);ancestor=local
    while not ancestor.exists():ancestor=ancestor.parent
    device=ancestor.stat().st_dev
    if device==root.stat().st_dev:raise ValueError('NFS and local budgets refer to one filesystem')
    total=0
    for i,p in enumerate(temps):
        if p.is_relative_to(local) or local.is_relative_to(p) or any(p.is_relative_to(q) or q.is_relative_to(p) for q in temps[:i]):
            raise ValueError('Overlapping temporary/history scopes')
        a=p
        while not a.exists():a=a.parent
        if a.stat().st_dev!=device:raise ValueError('Temporary pool spans different filesystems')
        total+=size_tree(p)
    if total>1024*GIB:raise RuntimeError('Combined temporary pool exceeds1024GiB')
    incoming=0 if local_copy_busy else math.ceil(contract['expected_checkpoint_bytes']*1.1)+GIB
    local_future=1024*GIB-total+incoming
    if shutil.disk_usage(ancestor).free<local_future+reserve:
        raise LocalHistoryCapacityError('Combined local temporary/copy/reserve unavailable')
    return dict(nfs_remaining_bytes=remaining,nfs_free_bytes=nfs_free,
                nfs_after_future_reserve_bytes=nfs_free-remaining-reserve,
                nfs_allocated_bytes=used,local_temporary_used_bytes=total,
                local_future_bytes=local_future,scope='Combined fresh storage admission only')


def capacity_plan(*, checkpoint_bytes, nfs_free, local_free,
                  source_nfs_remaining, source_history_growth,
                  temporary_used=0, export_budget=100*GIB):
    values=locals().copy()
    if any(type(v) is not int or v<0 for v in values.values()) or checkpoint_bytes<1:
        raise ValueError('Capacity inputs must be nonnegative byte counts')
    if temporary_used>1024*GIB:
        raise ValueError('Existing temporary usage already exceeds its shared reservation')
    reserve=200*GIB
    nfs_retention=math.ceil(4*checkpoint_bytes*1.1)+GIB
    protected_parent=math.ceil(checkpoint_bytes*1.1)+GIB
    # Conservative slots: two additional protected parents for independent
    # diagnostic branches plus four whole-state outputs. Using local scratch
    # later can reduce this, but is not credited before implementation/tests.
    diagnostic=2*(math.ceil(3*checkpoint_bytes*1.1)+GIB)
    final_reports=12*GIB  # native8 + resume1 + retention1 + transition1 + observer1.
    before=source_nfs_remaining+protected_parent
    after=nfs_retention+protected_parent+diagnostic+export_budget+final_reports
    # Existing NFS/local histories are ALREADY in measured free space. The old
    # producer is stopped and its NFS retention settled before the second phase;
    # its unallocated NFS write peak is not added again to the new phase.
    peak_nfs=max(before,after)
    temp_remaining=1024*GIB-temporary_used
    history_available=local_free-reserve-temp_remaining-source_history_growth
    local_budget=lambda n:math.ceil((n+1)*checkpoint_bytes*1.1)+GIB
    affordable=[n for n in range(16) if local_budget(n)<=history_available]
    history_keep=max(affordable) if affordable else None
    local_incoming=local_budget(0)
    return dict(status='capacity_proposal_not_runtime_admission',
        checkpoint_bytes=checkpoint_bytes,reserve_per_filesystem_bytes=reserve,
        before_handoff_nfs_future_bytes=before,after_handoff_nfs_future_bytes=after,
        peak_nfs_future_bytes=peak_nfs,nfs_after_peak_and_reserve_bytes=nfs_free-peak_nfs-reserve,
        new_nfs_retention_budget_bytes=nfs_retention,protected_parent_budget_bytes=protected_parent,
        diagnostic_nfs_budget_bytes=diagnostic,export_nfs_budget_bytes=export_budget,
        final_report_nfs_budget_bytes=final_reports,
        temporary_total_budget_bytes=1024*GIB,temporary_future_bytes=temp_remaining,
        source_additional_history_bytes=source_history_growth,
        new_local_history_and_copy_available_bytes=history_available,
        planned_local_keep_max=15,affordable_new_local_keep_with_copy_headroom=history_keep,
        new_local_full15_budget_bytes=local_budget(15),
        new_local_one_copy_growth_bytes=local_incoming,
        local_after_startup_growth_and_reserve_bytes=history_available-local_incoming,
        minimum_capacity_feasible=nfs_free>=peak_nfs+reserve and history_available>=local_incoming,
        assumes_source_nfs_retention_settled_before_handoff=True,
        assumes_shared_temporary_pool_not_two_independent1024gib_pools=True,
        old_history_deletion_assumed=False,
        execution_requires=['Fresh free/used measurements at handoff',
            'Source NFS retention settled; no surviving old producer or final controller',
            'Old and new scratch usage together bounded by the shared temporary reservation',
            'Runtime enforcement includes diagnostic/export/final budgets, not only retention',
            'Adaptive local pruning remains confined to explicitly authorized ownership scopes'])
