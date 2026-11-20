"""Seal the original committed lineage and protected parent for a new35k run.

No process signals, training launch, source modification or deletion. Preparing
a lineage while training is live is allowed; sealing requires fresh unchanged
source evidence, a verified protected copy, and absence of all captured PIDs.
"""
import fcntl
import json
import os
from pathlib import Path
import re

from common.data_contracts import resolve, artifact_path, identity, sha256_file
from scripts.audit_main_training import audit_run, aggregate
from scripts.tiered_checkpoint_retention import exact_path, fsync_dir, inventory
from scripts.horizon_process_handoff import unsaved_progress
from train.final_resume_probe import file_stats
from train.horizon_worker_contract import BRIDGE_CODE, executable_candidate

ROLES = {'supervisor', 'worker', *[f'rank{r}' for r in range(8)]}
LINEAGE_FILES = {'audit.json', 'trace.jsonl', *[f'rank{r}.json' for r in range(8)]}


def unchanged(bindings):
    for name, digest in bindings.items():
        if sha256_file(exact_path(resolve(name))) != digest:
            raise ValueError('Source evidence changed: '+name)


def write_once(path, raw):
    """Same content retry is allowed; foreign final files are never overwritten."""
    path = exact_path(path)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError('Different handoff residue; preserve and inspect: '+str(path))
        return
    tmp = exact_path(path.with_name(path.name+'.tmp'))
    if tmp.exists():
        if tmp.read_bytes() != raw:
            raise ValueError('Different staging residue; preserve and inspect: '+str(tmp))
        with tmp.open('rb') as f:
            os.fsync(f.fileno())
    else:
        with tmp.open('xb') as f:
            f.write(raw); f.flush(); os.fsync(f.fileno())
    tmp.replace(path)
    fsync_dir(path.parent)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)+'\n').encode()


def lineage_counts(directory, metadata):
    directory = exact_path(directory)
    manifest = json.loads((directory/'manifest.json').read_text())
    if set(manifest['files']) != LINEAGE_FILES:
        raise ValueError('Lineage requires audit, trace and all8rank files')
    for name, digest in manifest['files'].items():
        if sha256_file(exact_path(directory/name)) != digest:
            raise ValueError('Lineage payload changed: '+name)
    contract = metadata['training_contract']
    attempts = []
    for rank in range(8):
        for entry in json.loads((directory/f'rank{rank}.json').read_text()):
            if entry['record']['rank'] != rank:
                raise ValueError('Lineage filename/rank differs')
            attempts.append((entry['attempt'], entry['record']))
    counts = aggregate(attempts, world=8, through_step=metadata['step'],
        target_steps=contract['config']['max_steps'], seq_len=contract['config']['seq_len'],
        global_tokens=2097152, sources=contract['source_order'],
        generation_steps=contract['monitored_evaluation']['generation_steps'], contract_id=contract['id'])
    audit = json.loads((directory/'audit.json').read_text())
    if (audit['counts'] != counts or manifest['through_step'] != metadata['step'] or
            manifest['committed_tokens'] != counts['committed_tokens'] or
            manifest['committed_step_rank_pairs'] != counts['committed_step_rank_pairs'] or
            manifest['training_contract_id'] != contract['id']):
        raise ValueError('Lineage manifest/audit and recomputed committed prefix differ')
    return manifest, audit


def verify_lineage_source(directory, source, step):
    buckets = {r:[] for r in range(8)}
    for path in sorted((source/'ledgers').glob('*.json')):
        match = re.fullmatch(r'(.+)-rank([0-7])\.json', path.name)
        if not match:
            raise ValueError('Unexpected original ledger filename')
        buckets[int(match[2])].append(dict(attempt=match[1], record=json.loads(path.read_text())))
    for rank, entries in buckets.items():
        if json.loads((directory/f'rank{rank}.json').read_text()) != entries:
            raise ValueError('Copied rank attempts differ from original source')
    raw = (source/'training/log.jsonl').read_bytes()
    if raw and not raw.endswith(b'\n'):
        raise ValueError('Incomplete original trace; retry observation')
    expected = b''.join(line+b'\n' for line in raw.splitlines() if json.loads(line)['step'] <= step)
    if (directory/'trace.jsonl').read_bytes() != expected:
        raise ValueError('Copied trace differs from original committed prefix')


def prepare_lineage(source, step, destination):
    """Fresh original57k audit, copied committed prefix, no execution admission."""
    source, destination = map(lambda p:exact_path(resolve(p)), (source, destination))
    if destination.exists() or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError('Use a new disjoint lineage candidate destination')
    audit = audit_run(source, step)
    config = json.loads((source/'main.json').read_text())
    training = resolve(config['out_dir'])
    meta = json.loads((training/f'ckpt_{step:07d}'/'meta.json').read_text())
    buckets = {r:[] for r in range(8)}
    ledger_paths = sorted((source/'ledgers').glob('*.json'))
    for path in ledger_paths:
        match = re.fullmatch(r'(.+)-rank([0-7])\.json', path.name)
        if not match or audit['evidence'].get(artifact_path(path)) != sha256_file(path):
            raise ValueError('Original ledger differs from fresh audit')
        buckets[int(match[2])].append(dict(attempt=match[1], record=json.loads(path.read_text())))
    raw = (training/'log.jsonl').read_bytes()
    # Preserve exact original record bytes for the committed prefix.
    trace = b''.join(line+b'\n' for line in raw.splitlines() if json.loads(line)['step'] <= step)
    unchanged(audit['evidence'])
    destination.mkdir(parents=True)
    for rank, entries in buckets.items():
        write_once(destination/f'rank{rank}.json', json_bytes(entries))
    write_once(destination/'trace.jsonl', trace)
    write_once(destination/'audit.json', json_bytes(audit))
    counts = audit['counts']
    manifest = dict(status='candidate_prefix_snapshot_not_sealed_handoff', through_step=step,
        committed_tokens=counts['committed_tokens'], committed_step_rank_pairs=counts['committed_step_rank_pairs'],
        training_contract_id=counts['contract_id'], files={n:sha256_file(destination/n) for n in sorted(LINEAGE_FILES)})
    write_once(destination/'manifest.json', json_bytes(manifest))
    lineage_counts(destination, meta)
    verify_lineage_source(destination, source, step)
    if ledger_paths != sorted((source/'ledgers').glob('*.json')):
        raise ValueError('Original ledger set changed during snapshot')
    unchanged(audit['evidence'])
    return manifest


def require_parent_absent(capture, source):
    if (capture.get('status') != 'observed_live_not_stopped' or
            capture.get('run') != str(source) or set(capture['processes']) != ROLES):
        raise ValueError('Missing original10process identity capture')
    rows = capture['processes']
    if len({r['pid'] for r in rows.values()}) != 10:
        raise ValueError('Parent process identities are not distinct')
    for row in rows.values():
        pid, ticks = row['pid'], row['start_ticks']
        if type(pid) is not int or pid <= 1 or type(ticks) is not int or ticks <= 0:
            raise ValueError('Invalid captured PID/start ticks')
        try:
            current = int(Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19])
        except FileNotFoundError:
            continue
        if current == ticks:
            raise ValueError('Captured parent still present, including stopped/zombie processes')
    # Absence of old identities is insufficient if another controller has
    # already restarted the same source namespace under new PIDs.
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            args = [s.decode() for s in (proc/'cmdline').read_bytes().split(b'\0') if s]
            if '--run-dir' not in args:
                continue
            index = args.index('--run-dir')
            if index+1 >= len(args):
                continue
            command_root = (proc/'cwd').resolve(strict=True)/args[index+1]
            if command_root.resolve() == source:
                raise ValueError('Another process owns the original run namespace: '+proc.name)
        except (FileNotFoundError, ProcessLookupError):
            continue  # A process exiting during the read is not a live owner.


def seal_handoff(source, root, capture_path, lineage_template, *, owner_id, fault=None):
    source, root, capture_path, template = map(lambda p:exact_path(resolve(p)),
                                              (source, root, capture_path, lineage_template))
    if root.parent != resolve('runs') or source == root or not root.is_dir():
        raise ValueError('Expected distinct prepared project run namespace')
    if not re.fullmatch('[0-9a-f]{32}', owner_id):
        raise ValueError('Invalid handoff owner')
    fault = fault or (lambda point:None)
    capture = json.loads(capture_path.read_text())
    require_parent_absent(capture, source)
    with exact_path(root/'.handoff.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        step = json.loads((template/'manifest.json').read_text())['through_step']
        fresh = audit_run(source, step)
        source_checkpoint = source/'training'/f'ckpt_{step:07d}'
        meta = json.loads((source_checkpoint/'meta.json').read_text())
        manifest, audit = lineage_counts(template, meta)
        verify_lineage_source(template, source, step)
        # A template prepared while live cannot be sealed after its source changed.
        for key in ('counts', 'trace', 'evidence', 'code', 'through_step', 'source_run'):
            if audit[key] != fresh[key]:
                raise ValueError('Lineage template is stale relative to fresh stopped source: '+key)
        proof_path = source/'checkpoint_certificates'/f'ckpt_{step:07d}.json'
        proof = json.loads(proof_path.read_text())
        parent = root/'parent_checkpoint'/source_checkpoint.name
        copy_receipt_path = parent.with_name(parent.name+'.receipt.json')
        protected = json.loads(exact_path(copy_receipt_path).read_text())
        txn = protected['transaction']
        if (protected.get('status') != 'verified_protected_parent' or txn['owner_id'] != owner_id or
                txn['source'] != str(source_checkpoint) or txn['destination'] != str(parent) or
                txn['proof_id'] != identity(proof) or protected['proof_id'] != identity(proof) or
                txn['step'] != step or txn['bytes'] != proof['bytes']):
            raise ValueError('Protected parent receipt/owner/proof differs')
        inventory(parent, proof)
        if (protected['stats'] != file_stats(parent) or root.stat().st_dev != parent.stat().st_dev or
                json.loads((parent/'meta.json').read_text()) != meta):
            raise ValueError('Protected parent changed since full SHA verification')
        transaction = dict(owner_id=owner_id, source=str(source), parent_step=step,
            capture_sha256=sha256_file(capture_path), lineage_sha256=sha256_file(template/'manifest.json'),
            copy_receipt_sha256=sha256_file(copy_receipt_path))
        write_once(root/'handoff_transaction.json', json_bytes(transaction))
        lineage = exact_path(root/'lineage'); lineage.mkdir(exist_ok=True)
        for name in sorted(LINEAGE_FILES | {'manifest.json'}):
            write_once(lineage/name, (template/name).read_bytes())
        fault('lineage_copied')
        write_once(root/'parent_certificate.json', proof_path.read_bytes())
        binding = dict(parent_contract_id=meta['training_contract']['id'], parent_step=step,
            parent_metadata_id=identity(meta), parent_certificate_sha256=sha256_file(root/'parent_certificate.json'),
            parent_ledger_manifest_sha256=sha256_file(lineage/'manifest.json'),
            code={p:sha256_file(resolve(p)) for p in BRIDGE_CODE})
        candidate = executable_candidate(meta, binding)
        log = [json.loads(s) for s in (source/'training/log.jsonl').read_text().splitlines()]
        last_logged = max([r['step'] for r in log if 'tok_s' in r] or [0])
        receipt = dict(status='sealed_parent_handoff', execution_binding=binding,
            parent_metadata=meta, parent_checkpoint=str(parent), stopped_parent_processes=capture['processes'],
            parent_stats=protected['stats'],
            capture_sha256=transaction['capture_sha256'], protected_copy_receipt_sha256=transaction['copy_receipt_sha256'],
            candidate_contract_id=candidate['id'], unsaved_progress=unsaved_progress(step,last_logged,2097152),
            source_modified=False, runtime_applied=False, actual8gpu_resume_verified=False,
            code={artifact_path(Path(__file__)):sha256_file(Path(__file__))})
        lineage_counts(lineage, meta)
        verify_lineage_source(lineage, source, step)
        if sha256_file(lineage/'manifest.json') != transaction['lineage_sha256']:
            raise ValueError('Lineage template changed during copy')
        unchanged(audit['evidence'])
        if protected['stats'] != file_stats(parent):
            raise ValueError('Protected parent changed before publication')
        require_parent_absent(capture, source)
        write_once(root/'parent_handoff.json', json_bytes(receipt))
        fault('handoff_published')
        return receipt
