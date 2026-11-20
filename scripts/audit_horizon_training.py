"""Read-only35k audit preserving separate parent and child execution identities.

The existing57k auditor is immutable while production runs. Its numerical trace
checks are copied below with an explicit interval boundary. Ledger suffix steps
are translated in memory solely to reuse its exact coverage/replay validator;
no producer record, contract ID, checkpoint or trace is rewritten.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re

from common.data_contracts import resolve, artifact_path, identity, sha256_file
from scripts.audit_main_training import (Snapshot, aggregate, audit_trace,
    integer, finite, verify_restart_metadata)
from scripts.tiered_checkpoint_retention import exact_path, inventory
from train.horizon_target import TARGET_STEPS, TOTAL_TOKENS
from train.monitored_horizon import validate_configuration, load_handoff
from train.final_resume_probe import file_stats


def aggregate_suffix(records, *, parent_step, through_step, contract):
    """Validate child-only absolute updates; count replay without counting twice."""
    integer(parent_step,'parent step');integer(through_step,'through step')
    target=contract['config']['max_steps']
    if not parent_step < through_step <= target:
        raise ValueError('Invalid child interval')
    shifted=[]
    for attempt, original in records:
        row=deepcopy(original)
        if row['steps'] or row['generations']:
            if row['contract_id']!=contract['id']:
                raise ValueError('Child ledger contract differs')
        for field in ('steps','generations'):
            for item in row[field]:
                step=integer(item['step'],'absolute child step')
                if step<=parent_step:
                    raise ValueError('Child ledger crosses parent boundary')
                item['step']=step-parent_step
        shifted.append((attempt,row))
    c=contract['config']
    result=aggregate(shifted,world=8,through_step=through_step-parent_step,
        target_steps=target-parent_step,seq_len=c['seq_len'],global_tokens=c['global_tokens'],
        sources=contract['source_order'],contract_id=contract['id'],
        generation_steps=[s-parent_step for s in contract['monitored_evaluation']['generation_steps'] if s>parent_step])
    result.update(start_step_exclusive=parent_step,through_step=through_step,
        generation_sha256_by_step={str(int(s)+parent_step):h for s,h in result['generation_sha256_by_step'].items()})
    return result


def combine_counts(parent, child, *, parent_step, through_step):
    if (parent['through_step']!=parent_step or child['start_step_exclusive']!=parent_step or
            child['through_step']!=through_step or set(parent['source_tokens'])!=set(child['source_tokens']) or
            parent['contract_id']==child['contract_id']):
        raise ValueError('Parent/child coverage identities differ')
    tokens={s:parent['source_tokens'][s]+child['source_tokens'][s] for s in parent['source_tokens']}
    count=parent['committed_tokens']+child['committed_tokens']
    pairs=parent['committed_step_rank_pairs']+child['committed_step_rank_pairs']
    if count!=through_step*2097152 or pairs!=through_step*8 or sum(tokens.values())!=count:
        raise ValueError('Combined absolute coverage differs')
    generations={**parent['generation_sha256_by_step'],**child['generation_sha256_by_step']}
    if len(generations)!=len(parent['generation_sha256_by_step'])+len(child['generation_sha256_by_step']):
        raise ValueError('Parent/child generation overlap')
    return dict(through_step=through_step,committed_tokens=count,committed_step_rank_pairs=pairs,
        source_tokens=tokens,source_fractions={s:n/count for s,n in tokens.items()},
        parent_contract_id=parent['contract_id'],child_contract_id=child['contract_id'],
        parent_step=parent_step,successful_target_ranks=child['successful_target_ranks'],
        generation_sha256_by_step=generations,
        persisted_attempt_tokens_through_prefix=parent['persisted_attempt_tokens_through_prefix']+child['persisted_attempt_tokens_through_prefix'],
        replayed_tokens_through_prefix=parent['replayed_tokens_through_prefix']+child['replayed_tokens_through_prefix'],
        parent_persisted_tail_excluded=parent['persisted_attempt_tokens_beyond_prefix'],
        child_persisted_tail_excluded=child['persisted_attempt_tokens_beyond_prefix'],
        optimizer_initializations_excluded=parent['optimizer_initializations_excluded']+child['optimizer_initializations_excluded'],
        scope='Separate exact-contract ledgers; original prefix plus strictly later child updates. Diagnostic tokens excluded; unpersisted work is unknown.')


def audit_range_trace(rows, *, start_step, config, through_step, contract_id, probe, prompts, generation_hashes):
    if type(start_step) is not int or start_step < 0 or start_step >= through_step:
        raise ValueError("Invalid trace range")
    train = {}
    evaluated = {}
    generated = {}
    replayed_logs = 0
    groups = {g['source']: g for g in probe['groups']}
    if len(groups) != len(probe['groups']):
        raise ValueError('Duplicate probe source')
    category_sources = {}
    for source, group in groups.items():
        category_sources.setdefault(group['category'], []).append(source)
    prompt_map = {p['id']: p for p in prompts}
    if len(prompt_map) != len(prompts):
        raise ValueError('Duplicate fixed prompt')
    for row in rows:
        step = integer(row['step'], 'trace_step')
        if step <= start_step:
            raise ValueError('Trace crosses its assigned parent/child boundary')
        if step > config['max_steps']:
            raise ValueError('Trace outside training target')
        if step > through_step:
            continue
        if 'loss' in row:
            for key in ['loss', 'gnorm', 'lr', 'tok_s', 'tokens_b']:
                finite(row[key], key)
            if (step % config['log_every'] or row['tok_s'] <= 0 or
                    not math.isclose(row['tokens_b']*1e9, step*config['global_tokens'], abs_tol=1, rel_tol=0)):
                raise ValueError('Train trace geometry differs')
            exact = {k:row[k] for k in ['loss','gnorm','lr','tokens_b']}
            if step in train:
                if train[step] != exact:
                    raise ValueError('Deterministic replay trace differs')
                replayed_logs += 1
            train[step] = exact
        elif 'by_source' in row:
            if (step % config['eval_every'] or row['contract_id'] != contract_id or
                    set(row['by_source']) != set(groups) or set(row['by_category']) != set(category_sources)):
                raise ValueError('Evaluation identity/coverage differs')
            def weighted(value, targets, nll=None):
                if integer(value['valid_tokens'], 'valid_tokens') != targets:
                    raise ValueError('Evaluation target count differs')
                finite(value['nll_sum'], 'nll_sum');finite(value['holdout_loss'], 'holdout_loss')
                if not math.isclose(value['holdout_loss'], value['nll_sum']/targets, rel_tol=1e-12, abs_tol=1e-12):
                    raise ValueError('Evaluation weighted NLL differs')
                if nll is not None and not math.isclose(value['nll_sum'], nll, rel_tol=1e-12, abs_tol=1e-8):
                    raise ValueError('Evaluation NLL sum differs')
            for source, value in row['by_source'].items():
                weighted(value, groups[source]['count']*config['seq_len'])
            for category, sources in category_sources.items():
                weighted(row['by_category'][category],
                         sum(row['by_source'][s]['valid_tokens'] for s in sources),
                         sum(row['by_source'][s]['nll_sum'] for s in sources))
            weighted(row, probe['target_tokens'], sum(v['nll_sum'] for v in row['by_source'].values()))
            values = {s:{k:v[k] for k in ['nll_sum','valid_tokens','holdout_loss']} for s,v in row['by_source'].items()}
            if step in evaluated and evaluated[step] != values:
                raise ValueError('Deterministic evaluation replay differs')
            evaluated[step] = values
            if 'fixed_generations' in row:
                items = row['fixed_generations']
                if len(items) != len(prompts) or {g['id'] for g in items} != set(prompt_map):
                    raise ValueError('Fixed generation prompt coverage differs')
                for g in items:
                    p = prompt_map[g['id']]
                    if g['prompt'] != p['text'] or g['category'] != p['category']:
                        raise ValueError('Fixed generation prompt changed')
                    if not 1 <= len(g['generated_ids']) <= 64 or any(type(x) is not int or not 0 <= x < config['vocab_size'] for x in g['generated_ids']):
                        raise ValueError('Fixed generation token IDs invalid')
                digest = identity(items)
                if generation_hashes.get(str(step)) != digest:
                    raise ValueError('Generation trace differs from8rank receipts')
                generated[str(step)] = digest
        else:
            raise ValueError('Unknown trace record')
    if set(train) != set(range((start_step//config['log_every']+1)*config['log_every'], through_step+1, config['log_every'])):
        raise ValueError('Missing train log coverage')
    if set(evaluated) != set(range((start_step//config['eval_every']+1)*config['eval_every'], through_step+1, config['eval_every'])):
        raise ValueError('Missing evaluation coverage')
    if generated != generation_hashes:
        raise ValueError('Missing fixed generation trace')
    return dict(train_points=len(train), evaluation_points=len(evaluated), repeated_train_records=replayed_logs,
                first_train=train[min(train)] if train else None, last_train=train[max(train)] if train else None,
                generation_sha256_by_step=generated)



def audit_run(source, through_step):
    root=exact_path(resolve(source));snap=Snapshot()
    c=snap.read(root/'contract.json');config=snap.read(root/'main.json')
    if c.get('purpose')!='main_training' or c.get('main_training_authorized') is not True:
        raise ValueError('Authorized35k production source required')
    validate_configuration(config,c)
    if c['configurations']!={'main.json':snap.bindings[artifact_path(root/'main.json')]}:
        raise ValueError('Bound35k configuration differs')
    for p,h in {**c['code'],**c['evidence']}.items():
        if sha256_file(exact_path(resolve(p)))!=h:raise ValueError('Bound source evidence changed: '+p)
    # Uses the real worker admission: exact transition, original process absence,
    # sealed8rank lineage and bound actual8GPU diagnostic evidence.
    receipt,candidate,parent_proof=load_handoff(root,c,config)
    if snap.read(root/'parent_handoff.json')!=receipt:raise ValueError('Handoff changed during audit')
    parent_meta=receipt['parent_metadata'];begin=parent_meta['step']
    if not begin<integer(through_step,'through step')<=TARGET_STEPS:
        raise ValueError('Expected child checkpoint after handoff')
    parent_path=exact_path(resolve(receipt['parent_checkpoint']))
    inventory(parent_path,parent_proof)
    if file_stats(parent_path)!=receipt['parent_stats']:raise ValueError('Protected parent changed')
    t=snap.read(root/'training/training_contract.json')
    if t!=candidate:raise ValueError('Actual child training contract differs from sealed candidate')
    probe=snap.read(root/'probe/probe.json');prompts=snap.read(resolve(c['prompts']),lines=True)
    mix=snap.read(resolve(config['mix']))
    if (sha256_file(root/'probe/probe.json')!=t['monitored_evaluation']['probe_sha256'] or
            sha256_file(resolve(c['prompts']))!=t['monitored_evaluation']['prompts_sha256'] or
            set(mix['sources'])!=set(t['source_order']) or c['generation_steps']!=t['monitored_evaluation']['generation_steps']):
        raise ValueError('Horizon probe/source/generation identity differs')
    manifest=snap.read(root/'lineage/manifest.json');sealed=snap.read(root/'lineage/audit.json')
    binding=receipt['execution_binding']
    if (snap.bindings[artifact_path(root/'lineage/manifest.json')]!=binding['parent_ledger_manifest_sha256'] or
            snap.read(root/'parent_certificate.json')!=parent_proof or
            snap.bindings[artifact_path(root/'parent_certificate.json')]!=binding['parent_certificate_sha256']):
        raise ValueError('Parent manifest/certificate snapshot changed')
    parent_records=[]
    for rank in range(8):
        for item in snap.read(root/'lineage'/f'rank{rank}.json'):
            if item['record']['rank']!=rank:raise ValueError('Parent rank filename differs')
            parent_records.append((item['attempt'],item['record']))
    old=parent_meta['training_contract'];old_config=parent_meta['config']
    parent_counts=aggregate(parent_records,world=8,through_step=begin,target_steps=old_config['max_steps'],
        seq_len=old_config['seq_len'],global_tokens=old_config['global_tokens'],sources=old['source_order'],
        generation_steps=old['monitored_evaluation']['generation_steps'],contract_id=old['id'])
    if parent_counts!=sealed['counts']:raise ValueError('Sealed parent counts differ')
    parent_trace=audit_trace(snap.read(root/'lineage/trace.jsonl',lines=True,require_newline=True),
        config=old_config,through_step=begin,contract_id=old['id'],probe=probe,prompts=prompts,
        generation_hashes=parent_counts['generation_sha256_by_step'])
    if parent_trace!=sealed['trace']:raise ValueError('Sealed parent trace differs')
    for name,h in manifest['files'].items():
        if snap.bindings[artifact_path(root/'lineage'/name)]!=h:raise ValueError('Lineage snapshot differs')
    paths=sorted((root/'ledgers').glob('*.json'));seen=set();child_records=[]
    for path in paths:
        match=re.fullmatch(r'(.+)-rank([0-7])\.json',path.name)
        stamp=(path.stat().st_dev,path.stat().st_ino)
        if not match or stamp in seen:raise ValueError('Invalid or aliased child ledger')
        seen.add(stamp);record=snap.read(path)
        if record['rank']!=int(match[2]):raise ValueError('Child rank filename differs')
        child_records.append((match[1],record))
    child_counts=aggregate_suffix(child_records,parent_step=begin,through_step=through_step,contract=t)
    counts=combine_counts(parent_counts,child_counts,parent_step=begin,through_step=through_step)
    child_trace=audit_range_trace(snap.read(root/'training/log.jsonl',lines=True,require_newline=True),
        start_step=begin,config=config,through_step=through_step,contract_id=t['id'],probe=probe,prompts=prompts,
        generation_hashes=child_counts['generation_sha256_by_step'])
    checkpoint=root/'training'/f'ckpt_{through_step:07d}'
    checkpoint_stats=file_stats(checkpoint)
    certificate_path=root/'checkpoint_certificates'/(checkpoint.name+'.json')
    proof=snap.read(certificate_path)
    if (proof.get('status')!='passed_main_checkpoint_storage' or proof['step']!=through_step or
            proof['training_contract_id']!=t['id'] or resolve(proof['checkpoint'])!=checkpoint):
        raise ValueError('Horizon checkpoint certificate differs')
    inventory(checkpoint,proof)
    files={r['path']:r for r in proof['files']}
    for name in ['.complete','.metadata','meta.json',*[f'rng_rank{r}.pt' for r in range(8)]]:
        if name not in files or sha256_file(checkpoint/name)!=files[name]['sha256']:
            raise ValueError('Checkpoint metadata/RNG content differs')
    verify_restart_metadata(snap.read(checkpoint/'meta.json'),config,t,through_step)
    if through_step==TARGET_STEPS:
        marker=snap.read(root/'.done');report=snap.read(root/'report.json')
        if (marker['report_sha256']!=snap.bindings[artifact_path(root/'report.json')] or
                marker['contract_sha256']!=snap.bindings[artifact_path(root/'contract.json')] or
                report['status']!='main_target_completed_review_pending' or
                resolve(report['final_checkpoint'])!=checkpoint or report['trained_tokens']!=TOTAL_TOKENS or
                counts['successful_target_ranks']!=list(range(8)) or counts['committed_tokens']!=TOTAL_TOKENS):
            raise ValueError('Final35k completion/full8rank coverage differs')
    if paths!=sorted((root/'ledgers').glob('*.json')):raise ValueError('Child ledger set changed')
    if file_stats(parent_path)!=receipt['parent_stats']:raise ValueError('Protected parent changed during audit')
    if file_stats(checkpoint)!=checkpoint_stats:raise ValueError('Child checkpoint changed during audit')
    for p,h in {**c['code'],**c['evidence']}.items():
        if sha256_file(exact_path(resolve(p)))!=h:raise ValueError('Bound source evidence changed during audit: '+p)
    snap.unchanged();categories=Counter()
    for group in probe['groups']:categories[group['category']]+=counts['source_tokens'][group['source']]
    return dict(status='passed_horizon_committed_prefix_audit',utc=datetime.now(timezone.utc).isoformat(),
        source_run=artifact_path(root),validation_source=False,through_step=through_step,target_steps=TARGET_STEPS,
        goal_complete=False,counts=counts,parent_counts=parent_counts,child_counts=child_counts,
        category_tokens=dict(categories),trace=dict(parent=parent_trace,child=child_trace),
        checkpoint_certificate=artifact_path(certificate_path),evidence=snap.bindings,
        code={p:sha256_file(resolve(p)) for p in ['scripts/audit_horizon_training.py','scripts/audit_main_training.py']},
        limitations=['Full payload producer SHA receipt reused; metadata/RNG freshly hashed.',
            'Actual final GPU restore, native evaluation, retention and inference packaging remain separate.',
            'Protected parent payload remains required by current worker admission; lifecycle retirement is separate.'])
