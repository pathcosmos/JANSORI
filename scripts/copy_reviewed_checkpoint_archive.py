"""Copy-only historical checkpoint archive. Never retire an NFS source.

A published directory is accepted only after fresh content verification and an
NFS receipt. The destination is disposable and is not a training resume source.
Existing training contracts are read, not rewritten by this preparatory tool.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.data_contracts import sha256_file
from common.paths import PROJECT_ROOT
from scripts.extended_horizon_storage import enforce_shared_space, ancestor
from scripts.tiered_checkpoint_retention import (
    atomic_json, copy_payload, exact_path, fsync_dir, inventory, size_tree)

GIB = 2**30


def snapshot(root):
    root = exact_path(root)
    result = {}
    if not root.is_dir():
        raise ValueError('Missing checkpoint directory')
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError('Unexpected archive file type')
        if path.is_file():
            s = path.stat()
            if s.st_nlink != 1:
                raise ValueError('Hard-linked checkpoint requires separate review')
            result[str(path.relative_to(root))] = [
                s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]
    return result


def seal(source, workers):
    before = snapshot(source)
    meta = json.loads((source / 'meta.json').read_text())
    def row(name):
        return dict(path=name, bytes=before[name][2], sha256=sha256_file(source/name))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        files = list(pool.map(row, before))
    proof = dict(files=files, bytes=sum(r['bytes'] for r in files),
                 step=meta['step'], training_contract_id=meta['training_contract']['id'])
    if snapshot(source) != before:
        raise ValueError('Source changed during seal')
    inventory(source, proof)
    return dict(proof=proof, source_stats=before)


def validate_plan(plan):
    if plan.get('purpose') != 'historical_checkpoint_copy_only_v1' or plan.get('delete_source') is not False:
        raise ValueError('Only copy-only archive plans are supported')
    project = exact_path(plan['project_root'])
    main = exact_path(plan['main_run'])
    source = exact_path(plan['source'])
    local = exact_path(plan['local_archive_root'])
    receipt = exact_path(plan['receipt_root'])
    review = exact_path(plan['review_path'])
    if not source.is_relative_to(project/'runs') or not main.is_relative_to(project/'runs'):
        raise ValueError('Source/main outside project runs')
    if sha256_file(main/'contract.json') != plan['main_contract_sha256']:
        raise ValueError('Main contract changed; re-admit archive work')
    if sha256_file(review) != plan['review_sha256']:
        raise ValueError('Reviewed source inventory changed')
    candidates = json.loads(review.read_text())['other_checkpoints']
    matches = [r for r in candidates if project/r['path'] == source]
    if len(matches) != 1 or matches[0]['bytes'] != plan['source_bytes']:
        raise ValueError('Source is outside exact reviewed inventory')
    for name, digest in plan['code'].items():
        if sha256_file(project/name) != digest:
            raise ValueError('Archive implementation changed')
    contract = json.loads((main/'contract.json').read_text())
    if local.parent not in [exact_path(p) for p in contract['shared_storage']['local_temp_paths']]:
        raise ValueError('Archive must use an existing shared temporary namespace')
    if not receipt.is_relative_to(project/'runs/3b_57k_transition_v2'):
        raise ValueError('Archive receipts must use reserved transition namespace')
    if local.name != 'historical_checkpoint_archive_v1':
        raise ValueError('Unexpected archive namespace')
    if ancestor(local).stat().st_dev == source.stat().st_dev:
        raise ValueError('Archive must be on the separate local filesystem')
    if ancestor(receipt).stat().st_dev != main.stat().st_dev:
        raise ValueError('Archive evidence must be on NFS')
    if plan.get('future_temp_bytes') != 200*GIB:
        raise ValueError('Keep final native8GiB and resume192GiB inside temporary pool')
    if type(plan['source_bytes']) is not int or plan['source_bytes'] < 1:
        raise ValueError('Invalid source size')
    for key, maximum in [('copy_workers', 4), ('hash_workers', 8)]:
        if type(plan[key]) is not int or not 1 <= plan[key] <= maximum:
            raise ValueError('Invalid worker budget')
    return main, source, local, receipt, contract


def copy_one(plan_path, *, fault=None, cancel=None, admission=None):
    """Resumes the same partial destination; never removes unknown residue."""
    plan_path = exact_path(plan_path)
    plan_digest = sha256_file(plan_path)
    plan = json.loads(plan_path.read_text())
    main, source, local, evidence, contract = validate_plan(plan)
    fault = fault or (lambda point: None)
    cancel = cancel or (lambda: False)
    admission = admission or enforce_shared_space
    # Stable slot across restarts; different source paths cannot share a slot.
    import hashlib
    slot = hashlib.sha256(str(source).encode()).hexdigest()[:24]
    stage, final = local/(slot+'.partial'), local/slot
    state = evidence/slot
    admission(main, contract)  # No output creation before storage admission.
    evidence.mkdir(parents=True, exist_ok=True)
    with (evidence/'copy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if sha256_file(plan_path) != plan_digest:
            raise ValueError('Plan changed')
        state.mkdir(exist_ok=True)
        binding = dict(plan_sha256=plan_digest, source=str(source), destination=str(final),
                       staging=str(stage), local_is_disposable=True, nfs_resume_candidate=False)
        binding_path = state/'binding.json'
        if binding_path.exists():
            if json.loads(binding_path.read_text()) != binding:
                raise ValueError('Archive slot belongs to another plan')
        else:
            if stage.exists() or final.exists():
                raise ValueError('Unowned archive residue; preserve and report')
            atomic_json(binding_path, binding)
        started = time.monotonic()
        last_guard = 0.0
        guard_lock = threading.Lock()
        observations = []
        def guard(force=False):
            nonlocal last_guard
            if cancel():
                raise InterruptedError('Archive cancelled; source and partial copy preserved')
            with guard_lock:
                if not force and time.monotonic()-last_guard < 30:
                    return False
                if sha256_file(main/'contract.json') != plan['main_contract_sha256']:
                    raise ValueError('Main contract changed during copy')
                space = admission(main, contract)
                if stage.exists() and final.exists():
                    raise ValueError('Both published and partial copies exist; preserve and stop')
                resident = size_tree(final if final.exists() else stage)
                if resident > plan['source_bytes']:
                    raise ValueError('Archive residue exceeds checkpoint size')
                remaining = plan['source_bytes']-resident
                # Count existing archive bytes once, plus remaining copy and final work.
                if (space['local_temporary_used_bytes']+remaining+plan['future_temp_bytes']+GIB >
                        contract['shared_storage']['temporary_budget_bytes']):
                    raise RuntimeError('Archive plus final-stage temporary peak exceeds pool')
                observations.append(dict(utc=datetime.now(timezone.utc).isoformat(),
                                         remaining_copy_bytes=remaining, **space))
                atomic_json(state/'space_latest.json', observations[-1])
                last_guard = time.monotonic()
                return False
        guard(True)
        sealed_path = state/'seal.json'
        if sealed_path.exists():
            sealed = json.loads(sealed_path.read_text())
            if snapshot(source) != sealed['source_stats']:
                raise ValueError('Source changed since archive seal')
            inventory(source, sealed['proof'], hashes=True, workers=plan['hash_workers'])
        else:
            sealed = seal(source, plan['hash_workers'])
            if sealed['proof']['bytes'] != plan['source_bytes']:
                raise ValueError('Reviewed checkpoint size changed')
            atomic_json(sealed_path, sealed)
        guard(True)
        proof = sealed['proof']
        local.mkdir(parents=True, exist_ok=True)
        if final.exists():
            inventory(final, proof, hashes=True, workers=plan['hash_workers'])
            timing = dict(revalidated_existing_destination=True)
        else:
            timing = copy_payload(source, stage, proof, copy_workers=plan['copy_workers'],
                                  hash_workers=plan['hash_workers'], fault=fault, cancelled=guard)
            if snapshot(source) != sealed['source_stats']:
                raise ValueError('Source changed during copy')
            guard(True)
            # Only local stage -> local final is renamed, never NFS -> local.
            stage.rename(final)
            fsync_dir(local)
            fault('published_local_before_ack')
        if snapshot(source) != sealed['source_stats']:
            raise ValueError('Source changed before acknowledgment')
        guard(True)
        result = dict(status='verified_copy_source_preserved', **binding,
                      bytes=proof['bytes'], file_count=len(proof['files']),
                      seal_sha256=sha256_file(sealed_path), utc=datetime.now(timezone.utc).isoformat(),
                      total_seconds=time.monotonic()-started, timing=timing,
                      peak_observed_temporary_bytes=max(r['local_temporary_used_bytes'] for r in observations),
                      deletion_authorized=False, source_exists=source.is_dir(),
                      scope='File-level copy integrity only; no new model restore validation or NFS space reclamation.')
        atomic_json(state/'receipt.json', result)
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True)
    args = parser.parse_args()
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    print(json.dumps(copy_one(Path(args.plan), cancel=stopped.is_set), ensure_ascii=False))
