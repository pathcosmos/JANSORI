"""Publish a verified NFS parent copy without modifying or deleting the source.

This is a preparation primitive, not a training launcher or a sealed handoff.
All interrupted staging and transaction records are retained for bounded retry.
"""
import fcntl
import json
from pathlib import Path
import shutil
import threading
import time

from common.data_contracts import identity
from scripts.tiered_checkpoint_retention import (
    exact_path, records, inventory, copy_payload, atomic_json, fsync_dir)
from train.final_resume_probe import file_stats

GIB = 2**30


def protect_parent(source, run, proof, *, owner_id, required_free_after_bytes,
                   copy_workers=4, hash_workers=8, fault=None, cancelled=None):
    """Copy->full SHA/fsync->same-filesystem rename->receipt, preserving failures.

    required_free_after_bytes must include all concurrent future reservations,
    final diagnostics and at least200GiB safety. Already allocated files must not
    be budgeted a second time. Caller performs hardware and aggregate admission.
    """
    source, run = exact_path(source), exact_path(run)
    if (type(required_free_after_bytes) is not int or required_free_after_bytes < 200*GIB or
            type(copy_workers) is not int or copy_workers not in (4,8) or
            type(hash_workers) is not int or not 1 <= hash_workers <= 8):
        raise ValueError('Unreviewed parent copy resource/reserve settings')
    if not isinstance(owner_id,str) or len(owner_id)!=32 or any(c not in '0123456789abcdef' for c in owner_id):
        raise ValueError('Invalid parent copy owner')
    step = proof.get('step')
    if type(step) is not int or step < 1 or source.name != f'ckpt_{step:07d}':
        raise ValueError('Source checkpoint identity differs')
    expected = records(proof)
    parent_dir = run/'parent_checkpoint'
    final = parent_dir/source.name
    stage = parent_dir/(source.name+'.copying')
    if source.is_relative_to(run) or run.is_relative_to(source):
        raise ValueError('Protected copy must not overlap the original checkpoint')
    parent_dir.mkdir(exist_ok=True)  # run must already be owned/prepared by caller.
    exact_path(parent_dir)
    if run.stat().st_dev != parent_dir.stat().st_dev:
        raise ValueError('Protected copy must remain on the permanent run filesystem')
    fault = fault or (lambda point:None)
    cancelled = cancelled or (lambda:False)
    transaction = dict(version=1,owner_id=owner_id,source=str(source),destination=str(final),
                       proof_id=identity(proof),step=step,bytes=proof['bytes'])
    record = parent_dir/(source.name+'.transaction.json')
    receipt = parent_dir/(source.name+'.receipt.json')
    for path in [stage,final,record,receipt,parent_dir/'.protect.lock']:
        exact_path(path)
    with (parent_dir/'.protect.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if record.exists():
            if json.loads(record.read_text()) != transaction:
                raise ValueError('Existing parent copy belongs to a different transaction')
        else:
            if stage.exists() or final.exists() or receipt.exists():
                raise ValueError('Unowned parent copy residue; preserve for inspection')
            atomic_json(record,transaction)
        for path in [stage,final,record,receipt]:exact_path(path)
        if stage.exists() and final.exists():
            raise ValueError('Both staging and final copies exist; preserve and inspect')
        check_lock = threading.Lock()
        checked_at = 0.0
        def guard(force=False):
            nonlocal checked_at
            if cancelled():raise InterruptedError('Parent protection cancelled; source/stage retained')
            with check_lock:
                now=time.monotonic()
                if not force and now-checked_at < 1:return False
                remaining=0
                if not final.exists():
                    for name,row in expected.items():
                        path=stage/name
                        if path.is_symlink():raise ValueError('Staging symlink rejected')
                        have=path.stat().st_size if path.is_file() else 0
                        remaining+=max(0,row['bytes']-have)
                if shutil.disk_usage(run).free < required_free_after_bytes+remaining:
                    raise RuntimeError('Protected parent space/reserve budget unavailable')
                checked_at=now
            return False
        try:
            if stage.exists() and any(not p.is_dir() and not p.is_file() for p in stage.rglob('*')):
                raise ValueError('Nonregular staging residue; preserve and inspect')
            guard(force=True)
            timing=None
            if final.exists():
                if receipt.exists():
                    previous=json.loads(receipt.read_text())
                    if previous.get('transaction')!=transaction or previous.get('status')!='verified_protected_parent':
                        raise ValueError('Protected parent receipt differs')
                    if previous['stats']!=file_stats(final):
                        raise ValueError('Protected parent changed after receipt publication')
                    inventory(final,proof)
                    return previous
                # Crash after rename: authenticate existing final before emitting
                # a receipt, even if the original source is no longer resident.
                inventory(final,proof,hashes=True,workers=hash_workers)
            else:
                timing=copy_payload(source,stage,proof,copy_workers=copy_workers,
                                    hash_workers=hash_workers,fault=fault,cancelled=guard)
                guard(force=True)
                if stage.stat().st_dev!=parent_dir.stat().st_dev:
                    raise ValueError('Publication rename would cross filesystems')
                if final.exists():raise ValueError('Destination appeared during copy')
                stage.rename(final)
                fsync_dir(parent_dir)
                fault('published_parent')
            inventory(final,proof)
            meta=json.loads((final/'meta.json').read_text())
            if (meta.get('step')!=step or meta.get('world_size')!=8 or
                    meta.get('training_contract',{}).get('id')!=proof.get('training_contract_id')):
                raise ValueError('Protected parent metadata differs')
            guard(force=True)
            result=dict(status='verified_protected_parent',transaction=transaction,
                        stats=file_stats(final),copy_timing=timing,proof_id=identity(proof),
                        required_free_after_bytes=required_free_after_bytes,
                        source_deleted=False,handoff_sealed=False)
            atomic_json(receipt,result)
            return result
        except BaseException as exc:
            atomic_json(parent_dir/(source.name+'.last_error.json'),
                        dict(transaction=transaction,error_type=type(exc).__name__,error=str(exc),
                             source_deleted=False,automatic_cleanup=False))
            raise
