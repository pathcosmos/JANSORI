"""Shared physical-device reservations for three-run57k restoration.

All existing allocations already reduce free space. Only unallocated portions
of disjoint budget pools are reserved again. No files are created or deleted.
"""
import math
from pathlib import Path
import shutil

from scripts.tiered_checkpoint_retention import exact_path, size_tree
from scripts.horizon_storage_plan import LocalHistoryCapacityError, GIB


def ancestor(path):
    path=exact_path(path)
    while not path.exists():path=path.parent
    return path


def disjoint_paths(names):
    paths=[exact_path(Path(n)) for n in names]
    if not paths:raise ValueError('Empty storage scope')
    for i,path in enumerate(paths):
        if any(path.is_relative_to(p) or p.is_relative_to(path) for p in paths[:i]):
            raise ValueError('Overlapping storage scopes would double count')
    return paths


def enforce_shared_space(root, contract, *, local_copy_busy=False, incoming_bytes=0):
    root=exact_path(root);spec=contract['shared_storage']
    reserve=spec['reserve_bytes'];temporary=spec['temporary_budget_bytes']
    if type(reserve) is not int or reserve<200*GIB or temporary!=1024*GIB:
        raise ValueError('Safety reserve or shared temporary pool changed')
    if type(incoming_bytes) is not int or incoming_bytes<0:raise ValueError('Invalid incoming byte count')
    if type(local_copy_busy) is not bool:raise ValueError('Invalid copy state')
    nfs_device=ancestor(root).stat().st_dev
    pools=spec['nfs_pools']
    if not isinstance(pools,dict) or not pools:raise ValueError('Missing permanent pools')
    all_nfs=disjoint_paths([p for pool in pools.values() for p in pool['paths']])
    if root not in all_nfs:raise ValueError('Main namespace absent from permanent pool')
    if any(ancestor(p).stat().st_dev!=nfs_device for p in all_nfs):
        raise ValueError('Permanent pool spans different filesystems')
    remaining=0;allocated={};pool_usage={}
    for name,pool in pools.items():
        budget=pool['budget_bytes']
        if type(budget) is not int or budget<0:raise ValueError('Invalid permanent pool budget')
        used=0
        for path in pool['paths']:
            allocated[path]=size_tree(exact_path(path));used+=allocated[path]
        if used>budget:raise RuntimeError('Permanent pool/residue budget exceeded: '+name)
        remaining+=budget-used;pool_usage[name]=dict(used_bytes=used,budget_bytes=budget)
    nfs_free=shutil.disk_usage(ancestor(root)).free
    if nfs_free<remaining+reserve:raise RuntimeError('Shared NFS future/reserve unavailable')
    history=disjoint_paths(spec['local_history_paths']);temps=disjoint_paths(spec['local_temp_paths'])
    disjoint_paths([str(p) for p in history+temps])
    local_device=ancestor(history[0]).stat().st_dev
    if local_device==nfs_device or any(ancestor(p).stat().st_dev!=local_device for p in history+temps):
        raise ValueError('Local history/temp device differs or aliases permanent storage')
    history_used=sum(size_tree(p) for p in history);temp_used=sum(size_tree(p) for p in temps)
    if temp_used>temporary:raise RuntimeError('Shared temporary pool exceeds1024GiB')
    cap=spec['local_history_budget_bytes']
    if type(cap) is not int or cap<0:raise ValueError('Invalid local history cap')
    if history_used+incoming_bytes>cap:raise LocalHistoryCapacityError('Global history/copy cap exceeded')
    expected=contract['expected_checkpoint_bytes']
    if type(expected) is not int or expected<1:raise ValueError('Invalid checkpoint size budget')
    copy_reserve=0 if local_copy_busy else math.ceil(expected*1.1)+GIB
    copy_reserve=max(copy_reserve,incoming_bytes)
    future=temporary-temp_used+copy_reserve
    local_free=shutil.disk_usage(ancestor(history[0])).free
    if local_free<future+reserve:raise LocalHistoryCapacityError('Shared local temporary/copy/reserve unavailable')
    return dict(nfs_used_by_path=allocated,nfs_pools=pool_usage,nfs_remaining_bytes=remaining,
        nfs_free_bytes=nfs_free,nfs_after_future_reserve_bytes=nfs_free-remaining-reserve,
        local_history_used_bytes=history_used,local_temporary_used_bytes=temp_used,
        local_future_bytes=future,local_free_bytes=local_free,
        local_after_future_reserve_bytes=local_free-future-reserve,
        scope='Disjoint shared pools; existing physical allocations already in free space; no deletion credit.')
