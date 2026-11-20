"""Freeze a stopped observer's evidence and seed the accepted replacement.

No signals, launches or changes to the old observer. The boundary coordinator
must stop the exact captured producer and observer before calling freeze().
"""
import fcntl
import json
import math
import os
from pathlib import Path
import re

from common.data_contracts import resolve, sha256_file
from scripts.tiered_checkpoint_retention import exact_path, atomic_json, fsync_dir, size_tree
from scripts.restore_final_storage import load, require_quiet, MAX_METADATA
from scripts.independent_runtime_view import stamp, json_file, stopped_identities

OWN = 'scripts/independent_observer_handoff.py'
FILES = ('contract.json', 'cursor.json', 'events.jsonl', 'state.json')
CAP = 16 * 2**20


def observer_scope(plan, main, base):
    pool = base['shared_storage']['nfs_pools']['observer']
    if pool['budget_bytes'] != 2**30 or len(pool['paths']) != 1:
        raise ValueError('Exact original observer pool required')
    old = exact_path(pool['paths'][0]); row = plan['observer_capture']
    args = row['argv']
    if args.count('--run-dir') != 1 or args.count('--source-run') != 1:
        raise ValueError('Ambiguous captured observer arguments')
    def path(flag):
        return exact_path(Path(row['cwd']) / args[args.index(flag)+1])
    if (path('--run-dir') != old or path('--source-run') != main or
            not any((Path(row['cwd'])/a).resolve() == resolve('scripts/observe_extended_training_v2.py') for a in args)):
        raise ValueError('Captured observer scope differs')
    binding = json_file(old/'contract.json')
    if (resolve(binding['source_run']) != main or
            binding['source_contract_sha256'] != plan['base_contract_sha256'] or
            binding['source_config_sha256'] != sha256_file(main/'main.json')):
        raise ValueError('Original observer contract differs')
    for name, digest in binding['code'].items():
        if sha256_file(resolve(name)) != digest:
            raise ValueError('Original observer code differs')
    return old, binding


def freeze(restoration):
    restoration = exact_path(resolve(restoration))
    plan, main, base, candidate = load(restoration)
    require_quiet(plan, main, base)
    old, _ = observer_scope(plan, main, base)
    out = restoration/'observer_handoff'
    if out.exists():
        raise ValueError('Preserve existing observer handoff; verify instead of replacing')
    if size_tree(restoration)+CAP > MAX_METADATA:
        raise RuntimeError('Observer handoff exceeds restoration metadata budget')
    # The old observer uses exactly this lock; never snapshot a live writer.
    with (old/'controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require_quiet(plan, main, base)
        stamps = {name: stamp(old/name) for name in FILES}
        if sum(s[2] for s in stamps.values()) > CAP:
            raise RuntimeError('Old observer evidence exceeds bounded handoff budget')
        raw = {name: (old/name).read_bytes() for name in FILES}
        if {name: stamp(old/name) for name in FILES} != stamps:
            raise ValueError('Stopped observer evidence changed during capture')
        out.mkdir()
        for name, data in raw.items():
            with (out/name).open('xb') as f:
                f.write(data); f.flush(); os.fsync(f.fileno())
        manifest = dict(purpose='stopped_observer_handoff_v1', main_run=str(main),
            old_output=str(old), observer_capture=plan['observer_capture'],
            source_capture=plan['source_capture'],
            previous_contract_sha256=plan['base_contract_sha256'],
            candidate_contract_sha256=plan['candidate_contract_sha256'],
            config_sha256=sha256_file(main/'main.json'),
            restoration=str(restoration), restoration_plan_sha256=sha256_file(restoration/'plan.json'),
            files={name: dict(bytes=len(raw[name]), sha256=sha256_file(out/name)) for name in FILES},
            code={OWN: sha256_file(resolve(OWN))}, production_observer_started=False)
        atomic_json(out/'manifest.json', manifest)
        atomic_json(out/'frozen.json', dict(manifest_sha256=sha256_file(out/'manifest.json')))
        fsync_dir(out)
        return dict(path=str(out/'manifest.json'), sha256=sha256_file(out/'manifest.json'))


def seed_history(view, output):
    """Return a new cursor; preserve old raw bytes and translate old event IDs."""
    output = exact_path(resolve(output)); ref = view.binding['observer_handoff']
    path = exact_path(resolve(ref['path'])); root = path.parent
    if (sha256_file(path) != ref['sha256'] or path.name != 'manifest.json' or
            json_file(root/'frozen.json') != {'manifest_sha256': ref['sha256']}):
        raise ValueError('Frozen observer handoff pin differs')
    manifest = json_file(path)
    phase = view.contract.get('final_storage_restoration', {})
    restoration = exact_path(manifest['restoration'])
    old = exact_path(manifest['old_output'])
    if (manifest['purpose'] != 'stopped_observer_handoff_v1' or
            resolve(manifest['main_run']) != view.main or output != old/'independent_v1' or
            root != restoration/'observer_handoff' or
            not root.is_relative_to(exact_path(view.contract['shared_storage']['nfs_pools']['transition']['paths'][0])) or
            sha256_file(restoration/'plan.json') != manifest['restoration_plan_sha256'] or
            manifest['candidate_contract_sha256'] != sha256_file(view.main/'contract.json') or
            manifest['previous_contract_sha256'] != phase.get('previous_contract_sha256') or
            manifest['config_sha256'] != sha256_file(view.main/'main.json') or
            manifest['code'] != {OWN: sha256_file(resolve(OWN))} or
            view.binding['code'].get(OWN) != manifest['code'][OWN] or
            set(manifest['files']) != set(FILES)):
        raise ValueError('Observer handoff runtime/source/code scope differs')
    if not stopped_identities([manifest['observer_capture'], *manifest['source_capture']['processes'].values()]):
        raise ValueError('Previous observer or producer still present')
    stamps = {name: stamp(root/name) for name in FILES}
    for name, row in manifest['files'].items():
        p = exact_path(root/name)
        if p.stat().st_size != row['bytes'] or sha256_file(p) != row['sha256']:
            raise ValueError('Frozen observer evidence changed')
    cursor = json_file(root/'cursor.json'); certs = cursor.get('certificate_hashes', {})
    for name, digest in certs.items():
        if not re.fullmatch(r'ckpt_[0-9]{7}\.json', name) or sha256_file(view.main/'checkpoint_certificates'/name) != digest:
            raise ValueError('Previously observed certificate differs')
    # Migrate only events actually written for this source. The old observer's
    # milestone key stored a 500-step bucket, whereas the new one stores step.
    seen = set()
    for line in (root/'events.jsonl').read_text().splitlines():
        row = json.loads(line); key = row['event_id']
        if resolve(row['source_run']) != view.main or row.get('stage') != 'main_training_observer':
            raise ValueError('Foreign event in old observer journal')
        # Journal receipts also cover a crash after append but before the old
        # cursor was persisted; do not send those events again either.
        if key.startswith('milestone:'):
            bucket = key.split(':', 1)[1]
            if not bucket.isdigit() or int(bucket)*500 > 57000 or row['step']//500 != int(bucket):
                raise ValueError('Invalid previous milestone event')
            seen.add('milestone:'+str(int(bucket)*500))
        elif key.startswith(('certificate:', 'evaluation:')):
            seen.add(key)
    # Reset liveness/stall state for the new attempt, rather than inheriting an
    # old PID or a long diagnostic pause as a new-runtime stall.
    history = dict(seen=sorted(seen), certificate_hashes=certs)
    resource_time = cursor.get('last_resource_unix')
    if isinstance(resource_time, (int, float)) and math.isfinite(resource_time) and resource_time >= 0:
        history['last_resource_unix'] = resource_time
    if {name: stamp(root/name) for name in FILES} != stamps or sha256_file(path) != ref['sha256']:
        raise ValueError('Observer evidence changed during cursor migration')
    return history
