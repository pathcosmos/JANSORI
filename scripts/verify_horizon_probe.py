"""Whole-state certificates and original/new source-ledger checks for35k probes.

No launch/pruning. Components never certify overall8GPU acceptance on their own.
Full acceptance also needs authenticated worker execution, four-mode observations,
independent CPU DCP comparisons and immutable input/code evidence.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path

from common.data_contracts import artifact_path, sha256_file
from scripts.tiered_checkpoint_retention import exact_path, atomic_json, fsync_dir
from scripts.verify_final_resume_probe import verify_inventory
from train.final_resume_probe import file_stats, stat_record
from train.horizon_probe_observer import MODES


def certify_checkpoint(path, expected_meta, output, *, cuda=True):
    """Full DCP/optimizer inventory and SHA, including every rank's RNG.

    cuda=False is for explicit small CPU fixtures. It never proves8GPU execution.
    """
    import torch
    import torch.distributed.checkpoint as dcp
    from scripts.supervise_main_training import validate_optimizer_map
    path,output=exact_path(path),exact_path(output)
    stats=file_stats(path);world=expected_meta['world_size']
    if type(world) is not int or world<1 or (cuda and world!=8):
        raise ValueError('Actual horizon certificate requires8ranks')
    required={'.complete','.metadata','meta.json',*[f'rng_rank{r}.pt' for r in range(world)]}
    payloads={n for n in stats if n.startswith('__') and n.endswith('.distcp') and '/' not in n}
    if set(stats)!=required|payloads or len(payloads)!=world or json.loads((path/'meta.json').read_text())!=expected_meta:
        raise ValueError('Horizon checkpoint completeness/metadata differs')
    metadata=dcp.FileSystemReader(path).read_metadata()
    validate_optimizer_map(metadata)
    if not metadata.storage_data:raise ValueError('DCP storage map missing')
    for item in metadata.storage_data.values():
        if (item.relative_path not in payloads or item.offset<0 or item.length<=0 or
                stats[item.relative_path]['st_size']<item.offset+item.length):
            raise ValueError('DCP storage bounds differ')
    for rank in range(world):
        rng=torch.load(path/f'rng_rank{rank}.pt',map_location='cpu',weights_only=True)
        if (set(rng)!=({'cpu','cuda'} if cuda else {'cpu'}) or
                any(not isinstance(v,torch.Tensor) or v.dtype!=torch.uint8 or v.ndim!=1 or v.numel()<1 for v in rng.values())):
            raise ValueError('Rank RNG state missing/invalid')
    def record(name):
        p=path/name
        with p.open('rb') as stream:os.fsync(stream.fileno())
        digest=sha256_file(p)
        if stat_record(p)!=stats[name]:raise ValueError('Checkpoint changed during certification')
        return dict(path=name,bytes=stats[name]['st_size'],sha256=digest)
    with ThreadPoolExecutor(max_workers=8) as pool:files=list(pool.map(record,sorted(stats)))
    fsync_dir(path)
    contract_id=expected_meta.get('training_contract',{}).get('id',expected_meta.get('child_contract_id'))
    if not contract_id:raise ValueError('Diagnostic training identity missing')
    proof=dict(status='certified_horizon_probe_checkpoint',checkpoint=artifact_path(path),path=artifact_path(path),
        metadata=expected_meta,step=expected_meta['step'],training_contract_id=contract_id,
        files=files,bytes=sum(r['bytes'] for r in files),stats=stats,
        diagnostic_only=True,cuda_rng_required=cuda,actual8gpu_acceptance=False)
    verify_inventory(path,proof)
    if output.exists():
        if json.loads(output.read_text())!=proof:raise ValueError('Preserve different existing certificate')
    else:atomic_json(output,proof)
    return proof


def compare_source_ledgers(rows, *, parent_step, parent_contract_id, child_contract_id,
                           sources, world=8, seq_len=4096, global_tokens=2097152):
    """Compare exact absolute sampler choices in all four modes on every rank."""
    indexed={}
    for mode,row in rows:
        rank=row['rank']
        if (mode not in MODES or type(rank) is not int or not 0<=rank<world or
                (mode,rank) in indexed or row['success'] is not True or row.get('generations')!=[]):
            raise ValueError('Invalid/incomplete diagnostic source ledger')
        expected_id=parent_contract_id if mode=='baseline' else child_contract_id
        begin,end=MODES[mode]
        if row['contract_id']!=expected_id or [s['step'] for s in row['steps']]!=list(range(parent_step+begin+1,parent_step+end+1)):
            raise ValueError('Source ledger contract/step differs')
        init=row['optimizer_initializations']
        if type(init) is not int or init<0:raise ValueError('Invalid excluded optimizer initialization count')
        for step in row['steps']:
            counts=step['source_sequences']
            if (not counts or set(counts)-set(sources) or any(type(v) is not int or v<1 for v in counts.values()) or
                    sum(counts.values())*seq_len*world!=global_tokens):
                raise ValueError('Source ledger batch geometry differs')
        indexed[mode,rank]=row
    if set(indexed)!={(mode,rank) for mode in MODES for rank in range(world)}:
        raise ValueError('Incomplete four-mode8rank source coverage')
    source_tokens={name:0 for name in sources}
    for rank in range(world):
        baseline,continuous,first,resumed=[indexed[mode,rank]['steps'] for mode in ['baseline','continuous','first','resumed']]
        if baseline!=continuous or continuous!=first+resumed:
            raise ValueError('Original/new or split source sampling differs')
        for step in continuous:
            for name,count in step['source_sequences'].items():source_tokens[name]+=count*seq_len
    return dict(status='passed_four_mode_source_ledger_comparison',world_size=world,
        unique_diagnostic_tokens=2*global_tokens,actual_diagnostic_tokens=6*global_tokens,
        main_committed_tokens_added=0,source_tokens=source_tokens,
        excluded_optimizer_initializations=sum(r['optimizer_initializations'] for r in indexed.values()),
        actual8gpu_acceptance=False)
