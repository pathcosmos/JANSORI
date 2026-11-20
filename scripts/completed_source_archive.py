"""Freeze/replay completed57k accounting without protected-parent payloads.

Metadata only: no training launch, tensor restore, permission change or deletion.
The caller must pin the returned manifest digest in a separate execution contract.
Replay proves historical accounting, never current payload availability or GPU
independence. Original training contracts and completion markers stay unchanged.
"""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re

from common.data_contracts import resolve, artifact_path, sha256_file
from scripts.audit_main_training import Snapshot, verify_restart_metadata
from scripts.audit_horizon_training import aggregate_suffix, audit_range_trace
from scripts.audit_extended_horizon import aggregate_restored_lineage
from scripts.extended_horizon_lineage import calculate, LINEAGE_FILES
from scripts.freeze_independent_prefix import ArchivedSnapshot
from scripts.tiered_checkpoint_retention import exact_path, atomic_json, fsync_dir, size_tree

TARGET = 57000
TOKENS = 119537664000
MAX_TOTAL = 192 * 2**20
CAP = 200 * 2**20
RESTART_FILES = {'.complete', '.metadata', 'meta.json',
                 *[f'rng_rank{rank}.pt' for rank in range(8)]}
CODE = [
    'scripts/completed_source_archive.py', 'scripts/freeze_independent_prefix.py',
    'scripts/audit_main_training.py', 'scripts/audit_horizon_training.py',
    'scripts/audit_extended_horizon.py', 'scripts/extended_horizon_lineage.py',
    'scripts/audit_restored_training.py', 'scripts/completed_training_storage.py',
    'train/extended_handoff.py', 'train/extended_horizon_worker_contract.py',
    'common/data_contracts.py', 'common/paths.py',
    'scripts/tiered_checkpoint_retention.py', 'scripts/extended_horizon_storage.py',
]


def merged_bindings(*groups):
    result = {}
    for group in groups:
        for name, digest in group.items():
            key = str(resolve(name))
            if key in result and result[key] != digest:
                raise ValueError('Conflicting archived input identity')
            result[key] = digest
    return result


def replay(directory, manifest_sha256):
    """Recompute all three segments from pinned NFS metadata, not parent files."""
    directory = exact_path(resolve(directory))
    manifest_path = exact_path(directory / 'manifest.json')
    before = Snapshot.stamp(manifest_path)
    raw_manifest = manifest_path.read_bytes()
    if (Snapshot.stamp(manifest_path) != before
            or hashlib.sha256(raw_manifest).hexdigest() != manifest_sha256):
        raise ValueError('Completed archive manifest differs from caller binding')
    manifest = json.loads(raw_manifest)
    if (manifest.get('purpose') != 'completed57000_source_archive_v1'
            or set(manifest.get('implementation', {})) != set(CODE)
            or manifest.get('audit') != str(directory / 'original_completed_audit.json')
            or sum(row['bytes'] for row in manifest['files'].values()) > MAX_TOTAL
            or size_tree(directory) > CAP
            or {p.name for p in directory.iterdir()} - {'manifest.json', 'blobs', 'original_completed_audit.json', 'verified.json'}):
        raise ValueError('Completed archive scope or metadata budget differs')
    for name, digest in manifest['implementation'].items():
        if sha256_file(resolve(name)) != digest:
            raise ValueError('Completed replay implementation changed: ' + name)
    snap = ArchivedSnapshot(directory, manifest)
    audit = snap.read(manifest['audit'])
    if sha256_file(exact_path(manifest['audit'])) != hashlib.sha256(snap.raw(manifest['audit'])).hexdigest():
        raise ValueError('Published original audit differs from its archive')
    if (audit.get('status') != 'passed_restored_committed_prefix_audit'
            or audit.get('validation_source') is not False
            or audit.get('through_step') != TARGET or audit.get('target_steps') != TARGET):
        raise ValueError('Full original completed57000 audit required')
    main = resolve(audit['source_run'])
    c = snap.read(main / 'contract.json')
    config = snap.read(main / 'main.json')
    from train.extended_handoff import validate_configuration
    from train.extended_horizon_worker_contract import bound_candidate
    validate_configuration(config, c)
    if (c.get('purpose') != 'main_training' or c.get('main_training_authorized') is not True
            or c['configurations'] != {'main.json': hashlib.sha256(snap.raw(main / 'main.json')).hexdigest()}):
        raise ValueError('Original authorized configuration differs')
    bindings = merged_bindings(c['code'], c['evidence'], audit['evidence'], audit['code'])
    for name, digest in bindings.items():
        if hashlib.sha256(snap.raw(name)).hexdigest() != digest:
            raise ValueError('Archived original input binding differs: ' + name)
    receipt = snap.read(main / 'parent_handoff.json')
    parent = receipt['parent_metadata']
    if hashlib.sha256(snap.raw(main / 'parent_certificate.json')).hexdigest() != receipt['execution_binding']['parent_certificate_sha256']:
        raise ValueError('Archived parent certificate differs')
    t = snap.read(main / 'training/training_contract.json')
    if t != bound_candidate(parent, receipt['original_contract'], receipt['execution_binding']):
        raise ValueError('Original training identity differs')
    probe = snap.read(main / 'probe/probe.json')
    prompts = snap.read(c['prompts'], lines=True)
    mix = snap.read(config['mix'])
    if (hashlib.sha256(snap.raw(main / 'probe/probe.json')).hexdigest() != t['monitored_evaluation']['probe_sha256']
            or hashlib.sha256(snap.raw(c['prompts'])).hexdigest() != t['monitored_evaluation']['prompts_sha256']
            or set(mix['sources']) != set(t['source_order'])
            or c['generation_steps'] != t['monitored_evaluation']['generation_steps']):
        raise ValueError('Archived probe/input/generation contract differs')
    lineage = snap.read(main / 'lineage/manifest.json')
    if (set(lineage['files']) != LINEAGE_FILES
            or hashlib.sha256(snap.raw(main / 'lineage/manifest.json')).hexdigest() != receipt['execution_binding']['parent_ledger_manifest_sha256']):
        raise ValueError('Archived lineage manifest differs')
    payload = {}
    for name, digest in lineage['files'].items():
        path = main / 'lineage' / name
        payload[name] = snap.read(path, lines=name.endswith('.jsonl'), require_newline=name.endswith('.jsonl'))
        if hashlib.sha256(snap.raw(path)).hexdigest() != digest:
            raise ValueError('Archived lineage content differs')
    prefix = calculate(payload, probe, prompts)
    if prefix != snap.read(main / 'lineage/audit.json'):
        raise ValueError('Historical prefix recomputation differs')
    records = {segment: [(row['attempt'], row['record']) for rank in range(8)
                        for row in payload[f'{segment}_rank{rank}.json']]
               for segment in ['original', 'current']}
    children = []
    for name in sorted(audit['evidence']):
        path = resolve(name)
        if path.parent != main / 'ledgers':
            continue
        match = re.fullmatch(r'(.+)-rank([0-7])\.json', path.name)
        row = snap.read(path)
        if not match or row['rank'] != int(match[2]):
            raise ValueError('Archived child rank identity differs')
        children.append((match[1], row))
    child = aggregate_suffix(children, parent_step=parent['step'], through_step=TARGET, contract=t)
    counts = aggregate_restored_lineage(
        original=receipt['original_contract'], current=parent['training_contract'], restored=t,
        original_records=records['original'], current_records=records['current'],
        restored_records=children, boundary=parent['step'], through_step=TARGET, parent_metadata=parent)
    trace = audit_range_trace(snap.read(main / 'training/log.jsonl', lines=True, require_newline=True),
                             start_step=parent['step'], config=config, through_step=TARGET,
                             contract_id=t['id'], probe=probe, prompts=prompts,
                             generation_hashes=child['generation_sha256_by_step'])
    categories = Counter()
    for group in probe['groups']:
        categories[group['category']] += counts['source_tokens'][group['source']]
    expected = dict(counts=counts, parent_counts=prefix['counts'], child_counts=child,
                    trace=dict(parent=prefix['trace'], child=trace), category_tokens=dict(categories))
    if any(audit.get(name) != value for name, value in expected.items()):
        raise ValueError('Completed archived accounting/trace differs')
    cp = main / 'training/ckpt_0057000'
    cert = snap.read(audit['checkpoint_certificate'])
    if (cert.get('status') != 'passed_main_checkpoint_storage' or resolve(cert['checkpoint']) != cp
            or cert['step'] != TARGET or cert['training_contract_id'] != t['id']):
        raise ValueError('Completed checkpoint certificate differs')
    from scripts.tiered_checkpoint_retention import records as certificate_records
    files = certificate_records(cert)
    for name in RESTART_FILES:
        raw = snap.raw(cp / name)
        if len(raw) != files[name]['bytes'] or hashlib.sha256(raw).hexdigest() != files[name]['sha256']:
            raise ValueError('Archived restart metadata/RNG differs')
    verify_restart_metadata(snap.read(cp / 'meta.json'), config, t, TARGET)
    marker = snap.read(main / '.done')
    report = snap.read(main / 'report.json')
    if (marker.get('report_sha256') != hashlib.sha256(snap.raw(main / 'report.json')).hexdigest()
            or marker.get('contract_sha256') != hashlib.sha256(snap.raw(main / 'contract.json')).hexdigest()
            or report.get('status') != 'main_target_completed_review_pending'
            or resolve(report['final_checkpoint']) != cp or report.get('trained_tokens') != TOKENS
            or counts['committed_tokens'] != TOKENS or counts['committed_step_rank_pairs'] != TARGET * 8
            or counts['successful_target_ranks'] != list(range(8))):
        raise ValueError('Completed marker/report/all8 accounting differs')
    wanted = {str(resolve(name)) for name in [manifest['audit'], *bindings,
                                             *[str(cp / name) for name in RESTART_FILES]]}
    if (set(snap.rows) != wanted
            or {p.name for p in (directory / 'blobs').iterdir()} != {row['blob'] for row in snap.rows.values()}):
        raise ValueError('Completed metadata coverage/residue differs')
    snap.unchanged()
    if sha256_file(manifest_path) != manifest_sha256:
        raise ValueError('Completed archive manifest changed during replay')
    return dict(status='replayed_completed57000_historical_accounting', source_run=str(main),
                manifest_sha256=manifest_sha256, **expected, current_payload_verified=False,
                actual8gpu_independence_verified=False, parent_retirement_authorized=False,
                goal_complete=False)


def freeze(storage_control, output):
    """Admit the genuine completed source and publish a bounded metadata copy."""
    from scripts.completed_training_storage import admit_completed, active_source_processes
    from scripts.extended_horizon_storage import ancestor
    implementation = {name: sha256_file(resolve(name)) for name in CODE}
    admitted = admit_completed(storage_control)
    c, audit = admitted['contract'], admitted['final_audit']
    main = exact_path(resolve(audit['source_run']))
    pool = c['shared_storage']['nfs_pools']['transition']
    output = exact_path(resolve(output))
    scope = exact_path(pool['paths'][0])
    if (len(pool['paths']) != 1 or output.exists() or output == scope
            or not output.is_relative_to(scope) or output.is_relative_to(main)
            or main.is_relative_to(output) or ancestor(output).stat().st_dev != main.stat().st_dev
            or size_tree(scope) + CAP > pool['budget_bytes']):
        raise ValueError('Preserve existing/source paths and metadata capacity')
    if active_source_processes(main):
        raise RuntimeError('Completed source became active')
    cp = main / 'training/ckpt_0057000'
    proof = json.loads(resolve(audit['checkpoint_certificate']).read_text())
    original = json.loads((main / 'contract.json').read_text())
    bindings = merged_bindings(original['code'], original['evidence'], audit['evidence'], audit['code'])
    for row in proof['files']:
        if row['path'] in RESTART_FILES:
            bindings[str(cp / row['path'])] = row['sha256']
    canonical = {}
    for name, digest in bindings.items():
        key = str(exact_path(resolve(name)))
        if key in canonical and canonical[key] != digest:
            raise ValueError('Conflicting completed metadata alias')
        canonical[key] = digest
    audit_raw = (json.dumps(audit, sort_keys=True, indent=2) + '\n').encode()
    if sum(Path(name).stat().st_size for name in canonical) + len(audit_raw) > MAX_TOTAL:
        raise RuntimeError('Completed metadata exceeds192MiB; preserve source')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'blobs').mkdir()
    audit_path = output / 'original_completed_audit.json'
    with audit_path.open('xb') as stream:
        stream.write(audit_raw); stream.flush(); os.fsync(stream.fileno())
    canonical[str(audit_path)] = sha256_file(audit_path)
    rows, stats = {}, {}
    for name, digest in canonical.items():
        path = exact_path(name)
        before = Snapshot.stamp(path)
        if not path.is_file() or before[2] > 128 * 2**20:
            raise ValueError('Invalid completed metadata file')
        raw = path.read_bytes()
        if Snapshot.stamp(path) != before or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('Completed input changed during archival')
        blob = hashlib.sha256(name.encode()).hexdigest()
        with (output / 'blobs' / blob).open('xb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        rows[name] = dict(blob=blob, bytes=len(raw), sha256=digest)
        stats[path] = before
    fsync_dir(output / 'blobs')
    manifest = dict(purpose='completed57000_source_archive_v1', audit=str(audit_path), files=rows,
                    implementation=implementation)
    atomic_json(output / 'manifest.json', manifest)
    digest = sha256_file(output / 'manifest.json')
    result = replay(output, digest)
    if any(Snapshot.stamp(path) != stamp for path, stamp in stats.items()) or active_source_processes(main):
        raise ValueError('Completed source changed before publication')
    atomic_json(output / 'verified.json', dict(manifest_sha256=digest, result=result))
    return result
