"""Freeze and recompute three stopped prefixes for a fourth, 50k generation.

The archive is NFS metadata only. Replaying it never reads retired ancestor
payloads, changes an original contract, certifies tensors or authorizes launch.
"""
import json
import re

from common.data_contracts import resolve, identity, sha256_file
from scripts.audit_main_training import Snapshot
from scripts.audit_horizon_training import audit_range_trace
from scripts.audit_budget_horizon import aggregate_budget_lineage
from scripts.extended_horizon_lineage import calculate as calculate_old, LINEAGE_FILES as OLD_FILES
from scripts.seal_horizon_handoff import write_once, json_bytes, require_parent_absent
from scripts.tiered_checkpoint_retention import exact_path, size_tree
from train.budget_horizon_contract import candidate

FILES = {'previous_lineage.json', 'extension_metadata.json', 'parent_metadata.json',
         'restored_trace.jsonl', 'audit.json', *[f'restored_rank{r}.json' for r in range(8)]}
CAP = 192 * 2**20


def records(payload):
    previous=payload['previous_lineage.json']; result={}
    for segment in ['original','current','restored']:
        rows=[];seen=set()
        source=payload if segment=='restored' else previous
        for rank in range(8):
            entries=source[f'{segment}_rank{rank}.json']
            if not isinstance(entries,list) or not entries:
                raise ValueError('Missing archived rank attempts')
            for item in entries:
                if (set(item)!={'attempt','record'} or item['record']['rank']!=rank
                        or (item['attempt'],rank) in seen):
                    raise ValueError('Aliased or mismatched archived rank attempt')
                seen.add((item['attempt'],rank));rows.append((item['attempt'],item['record']))
        result[segment]=rows
    return result


def calculate(payload, probe, prompts):
    previous=payload['previous_lineage.json']
    if set(previous)!=OLD_FILES:
        raise ValueError('Incomplete preserved two-prefix lineage')
    old_audit=calculate_old(previous,probe,prompts)
    if previous['audit.json']!=old_audit:
        raise ValueError('Preserved two-prefix audit differs')
    extension=payload['extension_metadata.json'];parent=payload['parent_metadata.json']
    if extension!=previous['parent_metadata.json'] or extension['step']!=10000:
        raise ValueError('Original 10k extension parent differs')
    original=previous['original_contract.json']; child,_=candidate(parent)
    rows=records(payload)
    counts=aggregate_budget_lineage(original=original,current=extension['training_contract'],
        restored=parent['training_contract'],child=child,
        original_records=rows['original'],current_records=rows['current'],restored_records=rows['restored'],
        child_records=[],extension_metadata=extension,parent_metadata=parent,through_step=parent['step'])
    trace=audit_range_trace(payload['restored_trace.jsonl'],start_step=10000,
        config=parent['config'],through_step=parent['step'],contract_id=parent['training_contract']['id'],
        probe=probe,prompts=prompts,generation_hashes=counts['segments'][2]['generation_sha256_by_step'])
    return dict(counts=counts,trace=dict(previous=old_audit['trace'],restored=trace))


def verify_lineage(directory, metadata, probe, prompts):
    directory=exact_path(resolve(directory));snap=Snapshot()
    if {p.name for p in directory.iterdir()}!=FILES|{'manifest.json'} or size_tree(directory)>CAP:
        raise ValueError('50k lineage file scope or capacity differs')
    manifest=snap.read(directory/'manifest.json')
    if (manifest.get('status')!='prepared_three_prefix_50k_lineage'
            or manifest.get('version')!=1 or set(manifest.get('files',{}))!=FILES
            or manifest.get('parent_step')!=metadata['step']
            or manifest.get('parent_contract_id')!=metadata['training_contract']['id']
            or manifest.get('probe_id')!=identity(probe) or manifest.get('prompts_id')!=identity(prompts)):
        raise ValueError('50k lineage manifest identity differs')
    payload={}
    for name,digest in manifest['files'].items():
        path=exact_path(directory/name)
        payload[name]=snap.read(path,lines=name.endswith('.jsonl'),require_newline=name.endswith('.jsonl'))
        if sha256_file(path)!=digest:raise ValueError('50k lineage bytes changed: '+name)
    if payload['parent_metadata.json']!=metadata:raise ValueError('50k lineage metadata differs')
    actual=calculate(payload,probe,prompts)
    if actual!=payload['audit.json']:raise ValueError('50k lineage recalculation differs')
    snap.unchanged()
    return manifest,actual


def prepare_lineage(source, destination, boundary, capture):
    source=exact_path(resolve(source));destination=exact_path(resolve(destination))
    if (source.parent!=resolve('runs') or destination.name!='lineage'
            or destination.parent.parent!=resolve('runs') or destination.parent==source):
        raise ValueError('Independent run lineage namespace required')
    require_parent_absent(capture,source)
    from scripts.audit_restored_training import audit_run
    audited=audit_run(str(source),boundary)
    snap=Snapshot();cfg=snap.read(source/'contract.json')
    metadata=snap.read(source/'training'/f'ckpt_{boundary:07d}'/'meta.json')
    receipt=snap.read(source/'parent_handoff.json')
    probe=snap.read(source/'probe/probe.json');prompts=snap.read(resolve(cfg['prompts']),lines=True)
    payload={'previous_lineage.json':{},'extension_metadata.json':receipt['parent_metadata'],
             'parent_metadata.json':metadata,
             'restored_trace.jsonl':snap.read(source/'training/log.jsonl',lines=True,require_newline=True)}
    old_manifest=snap.read(source/'lineage/manifest.json')
    if set(old_manifest['files'])!=OLD_FILES:raise ValueError('Source lineage coverage differs')
    for name,digest in old_manifest['files'].items():
        p=source/'lineage'/name
        payload['previous_lineage.json'][name]=snap.read(p,lines=name.endswith('.jsonl'),require_newline=name.endswith('.jsonl'))
        if sha256_file(p)!=digest:raise ValueError('Source lineage bytes changed')
    for rank in range(8):payload[f'restored_rank{rank}.json']=[]
    paths=sorted((source/'ledgers').glob('*.json'));seen=set()
    for path in paths:
        match=re.fullmatch(r'(.+)-rank([0-7])\.json',path.name);row=snap.read(path)
        stamp=(path.stat().st_dev,path.stat().st_ino)
        if not match or row['rank']!=int(match[2]) or stamp in seen:
            raise ValueError('Unknown or aliased source ledger')
        seen.add(stamp)
        payload[f'restored_rank{match[2]}.json'].append(dict(attempt=match[1],record=row))
    payload['audit.json']=calculate(payload,probe,prompts)
    for key in ['committed_tokens','committed_step_rank_pairs','source_tokens','generation_sha256_by_step']:
        if payload['audit.json']['counts'][key]!=audited['counts'][key]:
            raise ValueError('Source committed prefix changed')
    if paths!=sorted((source/'ledgers').glob('*.json')):raise ValueError('Source ledger set changed')
    raw={name:(b''.join((json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n').encode() for row in value)
               if name.endswith('.jsonl') else json_bytes(value)) for name,value in payload.items()}
    if sum(map(len,raw.values()))>CAP-2**20:raise ValueError('50k prefix metadata budget exceeded')
    snap.unchanged();require_parent_absent(capture,source)
    if destination.exists():raise ValueError('Preserve existing lineage output; review interrupted publication')
    destination.mkdir(parents=True)
    if destination.stat().st_dev!=source.stat().st_dev:raise ValueError('Lineage must remain on source NFS')
    for name,value in raw.items():write_once(destination/name,value)
    manifest=dict(version=1,status='prepared_three_prefix_50k_lineage',source_run=str(source),
        parent_step=boundary,parent_contract_id=metadata['training_contract']['id'],
        probe_id=identity(probe),prompts_id=identity(prompts),source_evidence=snap.bindings,
        files={name:sha256_file(destination/name) for name in sorted(FILES)})
    snap.unchanged();require_parent_absent(capture,source)
    write_once(destination/'manifest.json',json_bytes(manifest))
    verify_lineage(destination,metadata,probe,prompts)
    return manifest,payload['audit.json']
