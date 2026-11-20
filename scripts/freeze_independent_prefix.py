"""Preserve and replay stopped-source prefix evidence without live file reads.

This proves historical ledger/trace accounting, not current storage availability,
GPU restoration, production adoption or Goal completion. Never copies tensors.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from common.data_contracts import resolve, artifact_path, sha256_file, identity
from scripts.tiered_checkpoint_retention import exact_path, atomic_json, fsync_dir
from scripts.audit_main_training import Snapshot, verify_restart_metadata
from scripts.audit_horizon_training import aggregate_suffix, audit_range_trace
from scripts.audit_extended_horizon import aggregate_restored_lineage
from scripts.extended_horizon_lineage import calculate, LINEAGE_FILES

MAX_FILE = 128*2**20
MAX_TOTAL = 192*2**20
IMPLEMENTATIONS = ['scripts/freeze_independent_prefix.py',
    'scripts/audit_main_training.py','scripts/audit_horizon_training.py',
    'scripts/audit_extended_horizon.py','scripts/extended_horizon_lineage.py',
    'train/extended_handoff.py','train/extended_horizon_worker_contract.py']


class ArchivedSnapshot:
    def __init__(self, directory, manifest):
        self.directory = exact_path(directory)
        self.manifest = manifest
        self.rows = {str(resolve(p)):r for p,r in manifest['files'].items()}
        if len(self.rows) != len(manifest['files']):
            raise ValueError('Aliased archived source paths')
        self.bindings = {}
        self.stats = {}

    def raw(self, source):
        key = str(resolve(source))
        if key not in self.rows:raise ValueError('Missing archived evidence: '+key)
        row = self.rows[key]
        if row['blob'] != hashlib.sha256(key.encode()).hexdigest():
            raise ValueError('Unexpected archived blob path')
        p = exact_path(self.directory/'blobs'/row['blob'])
        if not p.is_file() or p.stat().st_nlink != 1:
            raise ValueError('Archived evidence must be an unaliased regular file')
        before = Snapshot.stamp(p)
        if before[2] != row['bytes'] or before[2] > MAX_FILE:
            raise ValueError('Archived evidence size differs')
        raw = p.read_bytes()
        after=Snapshot.stamp(p);digest=hashlib.sha256(raw).hexdigest()
        if (after != before and after[:3] == before[:3] and digest == row['sha256']):
            # This NFS can refresh mtime/ctime by microseconds after fsync.
            # One bounded full re-read is allowed ONLY for identical bytes and
            # unchanged device/inode/size. The second read must be wholly stable
            # and match SHA again; corruption/replacement is never retried.
            before=after;raw=p.read_bytes()
            after=Snapshot.stamp(p);digest=hashlib.sha256(raw).hexdigest()
        if after != before or digest != row['sha256']:
            # Preserve enough evidence to distinguish an attribute transition
            # from byte corruption; never retry or weaken the acceptance gate.
            raise ValueError('Archived evidence changed: '+json.dumps(dict(
                source=key,blob=str(p),before=before,after=after,
                expected_sha256=row['sha256'],observed_sha256=digest),sort_keys=True))
        self.stats[p] = before
        self.bindings[artifact_path(resolve(source))] = row['sha256']
        return raw

    def read(self, path, *, lines=False, require_newline=False):
        raw = self.raw(path)
        if lines:
            if require_newline and raw and not raw.endswith(b'\n'):
                raise ValueError('Incomplete archived trace')
            return [json.loads(line) for line in raw.splitlines() if line.strip()]
        return json.loads(raw)

    def unchanged(self):
        if any(Snapshot.stamp(p)!=s for p,s in self.stats.items()):
            raise ValueError('Archive changed during replay')

    def paths(self, directory):
        return sorted(Path(p) for p in self.rows if Path(p).parent==resolve(directory))


def replay(directory):
    """Recompute the original three segment counts and evaluation trace."""
    directory = exact_path(resolve(directory))
    manifest = json.loads((directory/'manifest.json').read_text())
    if (manifest['purpose'] != 'frozen_independent_prefix_v1' or
            sum(r['bytes'] for r in manifest['files'].values()) > MAX_TOTAL):
        raise ValueError('Unreviewed prefix archive or budget')
    for p,h in manifest['implementation'].items():
        if sha256_file(resolve(p)) != h:raise ValueError('Prefix replay implementation changed')
    if set(manifest['implementation']) != set(IMPLEMENTATIONS):
        raise ValueError('Replay implementation coverage differs')
    snap = ArchivedSnapshot(directory,manifest)
    audit = snap.read(manifest['audit'])
    if audit['status'] != 'prepared_independent_prefix_audit_not_runtime_accepted' or audit['validation_source']:
        raise ValueError('Actual prefix audit required')
    for p,h in {**audit['evidence'],**audit['code']}.items():
        if hashlib.sha256(snap.raw(p)).hexdigest()!=h:
            raise ValueError('Original prefix binding differs')
    main = resolve(audit['source_run']);step = audit['through_step']
    c=snap.read(main/'contract.json');config=snap.read(main/'main.json')
    from train.extended_handoff import validate_configuration
    from train.extended_horizon_worker_contract import bound_candidate
    validate_configuration(config,c)
    if (c.get('purpose')!='main_training' or c.get('main_training_authorized') is not True or
            c['configurations']!={'main.json':hashlib.sha256(snap.raw(main/'main.json')).hexdigest()}):
        raise ValueError('Original authorized configuration differs')
    receipt=snap.read(main/'parent_handoff.json');parent=receipt['parent_metadata']
    if hashlib.sha256(snap.raw(main/'parent_certificate.json')).hexdigest()!=receipt['execution_binding']['parent_certificate_sha256']:
        raise ValueError('Archived protected parent certificate differs')
    t=snap.read(main/'training/training_contract.json')
    if t!=bound_candidate(parent,receipt['original_contract'],receipt['execution_binding']):
        raise ValueError('Archived original training identity differs')
    probe=snap.read(main/'probe/probe.json');prompts=snap.read(resolve(c['prompts']),lines=True)
    mix=snap.read(resolve(config['mix']))
    if (hashlib.sha256(snap.raw(main/'probe/probe.json')).hexdigest()!=t['monitored_evaluation']['probe_sha256'] or
            hashlib.sha256(snap.raw(resolve(c['prompts']))).hexdigest()!=t['monitored_evaluation']['prompts_sha256'] or
            set(mix['sources'])!=set(t['source_order']) or
            c['generation_steps']!=t['monitored_evaluation']['generation_steps']):
        raise ValueError('Archived probe/source/schedule differs')
    ledger_manifest=snap.read(main/'lineage/manifest.json')
    if (set(ledger_manifest['files'])!=LINEAGE_FILES or
            hashlib.sha256(snap.raw(main/'lineage/manifest.json')).hexdigest()!=receipt['execution_binding']['parent_ledger_manifest_sha256']):
        raise ValueError('Archived sealed lineage differs')
    payload={}
    for name,h in ledger_manifest['files'].items():
        path=main/'lineage'/name
        payload[name]=snap.read(path,lines=name.endswith('.jsonl'),require_newline=name.endswith('.jsonl'))
        if hashlib.sha256(snap.raw(path)).hexdigest()!=h:raise ValueError('Archived lineage changed')
    prefix=calculate(payload,probe,prompts)
    if prefix!=snap.read(main/'lineage/audit.json'):raise ValueError('Archived prefix recomputation differs')
    records={s:[(r['attempt'],r['record']) for rank in range(8) for r in payload[f'{s}_rank{rank}.json']]
             for s in ['original','current']}
    children=[]
    for name in sorted(audit['evidence']):
        path=resolve(name)
        if path.parent!=main/'ledgers':continue
        match=re.fullmatch(r'(.+)-rank([0-7])\.json',path.name)
        row=snap.read(path)
        if not match or row['rank']!=int(match[2]):raise ValueError('Archived child ledger identity differs')
        children.append((match[1],row))
    child=aggregate_suffix(children,parent_step=parent['step'],through_step=step,contract=t)
    counts=aggregate_restored_lineage(original=receipt['original_contract'],current=parent['training_contract'],restored=t,
        original_records=records['original'],current_records=records['current'],restored_records=children,
        boundary=parent['step'],through_step=step,parent_metadata=parent)
    trace=audit_range_trace(snap.read(main/'training/log.jsonl',lines=True,require_newline=True),
        start_step=parent['step'],config=config,through_step=step,contract_id=t['id'],probe=probe,prompts=prompts,
        generation_hashes=child['generation_sha256_by_step'])
    categories=Counter()
    for group in probe['groups']:categories[group['category']]+=counts['source_tokens'][group['source']]
    expected=dict(counts=counts,parent_counts=prefix['counts'],child_counts=child,
                  trace=dict(parent=prefix['trace'],child=trace),category_tokens=dict(categories))
    if any(audit.get(k)!=v for k,v in expected.items()):raise ValueError('Archived accounting/trace differs from recomputation')
    cp=main/'training'/f'ckpt_{step:07d}';cert=snap.read(audit['checkpoint_certificate'])
    if (cert['status']!='passed_main_checkpoint_storage' or resolve(cert['checkpoint'])!=cp or
            cert['step']!=step or cert['training_contract_id']!=t['id']):
        raise ValueError('Archived checkpoint identity differs')
    verify_restart_metadata(snap.read(cp/'meta.json'),config,t,step)
    cert_files={r['path']:r for r in cert['files']}
    if len(cert_files)!=len(cert['files']):raise ValueError('Duplicate checkpoint certificate file')
    for name in ['.complete','.metadata','meta.json',*[f'rng_rank{r}.pt' for r in range(8)]]:
        raw=snap.raw(cp/name)
        if hashlib.sha256(raw).hexdigest()!=cert_files[name]['sha256'] or len(raw)!=cert_files[name]['bytes']:
            raise ValueError('Archived restart metadata/RNG differs')
    wanted={str(resolve(p)) for p in [manifest['audit'],*audit['evidence'],*audit['code'],
        *[str(cp/name) for name in ['.complete','.metadata','meta.json',*[f'rng_rank{r}.pt' for r in range(8)]]]]}
    if set(snap.rows)!=wanted or {p.name for p in (directory/'blobs').iterdir()}!={r['blob'] for r in snap.rows.values()}:
        raise ValueError('Archived evidence coverage/residue differs')
    snap.unchanged()
    return dict(status='replayed_archived_independent_prefix',source_run=str(main),through_step=step,
        counts=counts,trace=expected['trace'],category_tokens=dict(categories),target_steps=57000,
        manifest_sha256=sha256_file(directory/'manifest.json'),current_source_verified=False,
        actual8gpu_verified=False,production_adopted=False)


def freeze(audit_path, output, capture):
    from scripts.seal_horizon_handoff import require_parent_absent
    audit_path=exact_path(resolve(audit_path));output=exact_path(resolve(output))
    audit=json.loads(audit_path.read_text());main=resolve(audit['source_run'])
    require_parent_absent(capture,main)
    if output.exists() or output.is_relative_to(main) or main.is_relative_to(output):
        raise ValueError('Preserve existing/source namespace')
    source={**audit['evidence'],**audit['code'],str(audit_path):sha256_file(audit_path)}
    cert=json.loads(resolve(audit['checkpoint_certificate']).read_text());cp=resolve(cert['checkpoint'])
    selected={'.complete','.metadata','meta.json',*[f'rng_rank{r}.pt' for r in range(8)]}
    for row in cert['files']:
        if row['path'] in selected:source[str(cp/row['path'])]=row['sha256']
    canonical={}
    for p,h in source.items():
        key=str(resolve(p))
        if key in canonical and canonical[key]!=h:raise ValueError('Conflicting source aliases')
        canonical[key]=h
    total=sum(exact_path(p).stat().st_size for p in canonical)
    if total>MAX_TOTAL:raise ValueError('Frozen prefix exceeds192MiB budget')
    output.mkdir(parents=True,exist_ok=False);(output/'blobs').mkdir()
    rows={};stats={}
    for name,h in canonical.items():
        p=exact_path(name);before=Snapshot.stamp(p)
        if not p.is_file() or before[2]>MAX_FILE:raise ValueError('Invalid prefix metadata file')
        raw=p.read_bytes()
        if Snapshot.stamp(p)!=before or hashlib.sha256(raw).hexdigest()!=h:
            raise ValueError('Prefix changed before frozen publication')
        blob=hashlib.sha256(name.encode()).hexdigest();dest=output/'blobs'/blob
        with dest.open('xb') as stream:
            stream.write(raw);stream.flush()
            import os
            os.fsync(stream.fileno())
        rows[name]=dict(blob=blob,bytes=len(raw),sha256=h);stats[p]=before
    if any(Snapshot.stamp(p)!=s for p,s in stats.items()):raise ValueError('Prefix changed during frozen publication')
    require_parent_absent(capture,main);fsync_dir(output/'blobs')
    manifest=dict(purpose='frozen_independent_prefix_v1',audit=str(audit_path),files=rows,
        source_capture=capture,implementation={p:sha256_file(resolve(p)) for p in IMPLEMENTATIONS})
    atomic_json(output/'manifest.json',manifest)
    result=replay(output)
    if any(Snapshot.stamp(p)!=s for p,s in stats.items()):raise ValueError('Source changed before frozen acceptance')
    require_parent_absent(capture,main)
    atomic_json(output/'verified.json',dict(result=result,manifest_sha256=sha256_file(output/'manifest.json')))
    return result
