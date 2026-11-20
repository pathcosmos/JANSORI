"""Prepare and recheck two original-identity prefixes for the57k extension.

Publication here never seals a process handoff or permits execution. The stop/
protection controller must bind this manifest and recheck its source snapshot.
"""
import json
from pathlib import Path
import re

from common.data_contracts import resolve, identity, sha256_file
from scripts.audit_main_training import Snapshot, audit_trace
from scripts.audit_horizon_training import audit_range_trace, audit_run
from scripts.audit_extended_horizon import aggregate_restored_lineage
from scripts.seal_horizon_handoff import write_once, json_bytes
from train.extended_horizon_transition import build_extension_candidate
from train.parent_checkpoint_resume import canonical

LINEAGE_FILES = {'original_contract.json', 'original_metadata.json', 'parent_metadata.json', 'audit.json',
    'original_trace.jsonl', 'current_trace.jsonl',
    *[f'{segment}_rank{rank}.json' for segment in ('original','current') for rank in range(8)]}


def calculate(payload, probe, prompts):
    original=payload['original_contract.json'];meta=payload['parent_metadata.json']
    original_meta=payload['original_metadata.json']
    if (original_meta['training_contract']!=original or original_meta['step']!=8000 or
            identity(original_meta)!=meta['training_contract']['horizon_transition']['parent_metadata_id']):
        raise ValueError('Original8k metadata ancestry differs')
    candidate,_=build_extension_candidate(meta,original)
    records={}
    for segment in ('original','current'):
        rows=[]
        for rank in range(8):
            entries=payload[f'{segment}_rank{rank}.json']
            if not isinstance(entries,list) or not entries:
                raise ValueError('Missing lineage rank attempts')
            for item in entries:
                if set(item)!={'attempt','record'} or item['record']['rank']!=rank:
                    raise ValueError('Lineage rank filename/record differs')
                rows.append((item['attempt'],item['record']))
        records[segment]=rows
    counts=aggregate_restored_lineage(original=original,current=meta['training_contract'],
        restored=candidate,original_records=records['original'],current_records=records['current'],
        restored_records=[],boundary=meta['step'],through_step=meta['step'])
    traces=dict(original=audit_trace(payload['original_trace.jsonl'],config=original_meta['config'],
        through_step=8000,contract_id=original['id'],probe=probe,prompts=prompts,
        generation_hashes=counts['segments'][0]['generation_sha256_by_step']),
        current=audit_range_trace(payload['current_trace.jsonl'],start_step=8000,
        config=meta['config'],through_step=meta['step'],contract_id=meta['training_contract']['id'],
        probe=probe,prompts=prompts,generation_hashes=counts['segments'][1]['generation_sha256_by_step']))
    return dict(counts=counts,trace=traces)


def verify_lineage(directory, metadata, original, probe, prompts):
    directory=canonical(directory);snap=Snapshot()
    if {p.name for p in directory.iterdir()} != LINEAGE_FILES|{'manifest.json'}:
        raise ValueError('Unknown or missing lineage files')
    manifest=snap.read(directory/'manifest.json')
    if (manifest.get('version')!=1 or manifest.get('status')!='prepared_extension_lineage' or
            set(manifest.get('files',{}))!=LINEAGE_FILES or
            manifest.get('parent_step')!=metadata['step'] or
            manifest.get('parent_contract_id')!=metadata['training_contract']['id'] or
            manifest.get('original_contract_id')!=original['id'] or
            manifest.get('probe_id')!=identity(probe) or manifest.get('prompts_id')!=identity(prompts)):
        raise ValueError('Lineage manifest identity differs')
    payload={}
    for name,digest in manifest['files'].items():
        path=canonical(directory/name)
        payload[name]=snap.read(path,lines=name.endswith('.jsonl'),require_newline=name.endswith('.jsonl'))
        if sha256_file(path)!=digest:
            raise ValueError('Lineage contents differ: '+name)
    if payload['parent_metadata.json']!=metadata or payload['original_contract.json']!=original:
        raise ValueError('Lineage parent metadata/original contract differs')
    actual=calculate(payload,probe,prompts)
    if payload['audit.json']!=actual:
        raise ValueError('Lineage counts/trace differ from fresh recomputation')
    snap.unchanged()
    return manifest,actual


def prepare_lineage(source, destination, boundary):
    source=canonical(resolve(source));destination=canonical(resolve(destination))
    if (not destination.is_relative_to(resolve('runs')) or
            destination.is_relative_to(source) or source.is_relative_to(destination)):
        raise ValueError('Use an independent lineage destination under runs')
    c=json.loads((source/'contract.json').read_text())
    protected=[canonical(resolve(c['legacy_retention']['source_run']))]
    protected += [canonical(p['local_root']) for p in (c['tiered_retention'],c['legacy_retention']['source_policy'])]
    if any(destination.is_relative_to(p) or p.is_relative_to(destination) for p in protected):
        raise ValueError('Lineage output overlaps protected source/history')
    previous=audit_run(str(source),boundary);snap=Snapshot()
    meta=snap.read(source/'training'/f'ckpt_{boundary:07d}'/'meta.json')
    handoff=snap.read(source/'parent_handoff.json');original=handoff['parent_metadata']['training_contract']
    probe=snap.read(source/'probe/probe.json');prompts=snap.read(resolve(c['prompts']),lines=True)
    payload={'original_contract.json':original,'original_metadata.json':handoff['parent_metadata'],'parent_metadata.json':meta,
        'original_trace.jsonl':snap.read(source/'lineage/trace.jsonl',lines=True,require_newline=True),
        'current_trace.jsonl':snap.read(source/'training/log.jsonl',lines=True,require_newline=True)}
    for rank in range(8):
        payload[f'original_rank{rank}.json']=snap.read(source/'lineage'/f'rank{rank}.json')
        payload[f'current_rank{rank}.json']=[]
    ledger_paths=sorted((source/'ledgers').glob('*.json'));seen=set()
    for path in ledger_paths:
        match=re.fullmatch(r'(.+)-rank([0-7])\.json',path.name)
        if not match:raise ValueError('Unknown source ledger')
        row=snap.read(path);stat=path.stat();stamp=(stat.st_dev,stat.st_ino)
        if stamp in seen or row['rank']!=int(match[2]):raise ValueError('Aliased ledger or rank mismatch')
        seen.add(stamp);payload[f'current_rank{match[2]}.json'].append(dict(attempt=match[1],record=row))
    audit=calculate(payload,probe,prompts);payload['audit.json']=audit
    for k in ('committed_tokens','committed_step_rank_pairs','source_tokens','generation_sha256_by_step'):
        if audit['counts'][k]!=previous['counts'][k]:raise ValueError('Source prefix changed during preparation')
    if ledger_paths!=sorted((source/'ledgers').glob('*.json')):raise ValueError('Source ledger set changed')
    snap.unchanged()
    destination.mkdir(parents=True,exist_ok=True)
    allowed=LINEAGE_FILES|{'manifest.json'}|{n+'.tmp' for n in LINEAGE_FILES|{'manifest.json'}}
    if any(p.name not in allowed for p in destination.iterdir()):raise ValueError('Foreign lineage residue')
    for name,value in payload.items():
        raw=(b''.join((json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n').encode() for row in value)
             if name.endswith('.jsonl') else json_bytes(value))
        write_once(destination/name,raw)
    manifest=dict(version=1,status='prepared_extension_lineage',parent_step=boundary,
        parent_contract_id=meta['training_contract']['id'],original_contract_id=original['id'],
        probe_id=identity(probe),prompts_id=identity(prompts),
        source_run=str(source),source_evidence=snap.bindings,
        files={name:sha256_file(destination/name) for name in sorted(LINEAGE_FILES)})
    snap.unchanged();write_once(destination/'manifest.json',json_bytes(manifest))
    verify_lineage(destination,meta,original,probe,prompts)
    return manifest,audit
