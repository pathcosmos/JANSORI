"""Read-only four-run50k prefix/final audit; never relabels original IDs.

Actual native evaluation, final GPU restoration, retention and inference remain
separate gates. Reuses producer full SHA receipts and freshly hashes meta/RNG.
"""
from collections import Counter
from datetime import datetime,timezone
import re
from common.data_contracts import resolve,artifact_path,sha256_file
from scripts.audit_main_training import Snapshot,integer,verify_restart_metadata
from scripts.audit_horizon_training import aggregate_suffix,audit_range_trace
from scripts.audit_budget_horizon import aggregate_budget_lineage
from scripts.budget_horizon_lineage import calculate,FILES as LINEAGE_FILES,records as lineage_records
from scripts.tiered_checkpoint_retention import exact_path,inventory
from train.budget_horizon_handoff import validate_configuration,load_handoff
from train.final_resume_probe import file_stats
TARGET_STEPS=50000
TOTAL_TOKENS=TARGET_STEPS*2097152


def audit_run(source, through_step):
    root=exact_path(resolve(source));snap=Snapshot()
    c=snap.read(root/'contract.json');config=snap.read(root/'main.json')
    if c.get('purpose')!='main_training' or c.get('main_training_authorized') is not True:
        raise ValueError('Authorized50k production source required')
    validate_configuration(config,c)
    if c['configurations']!={'main.json':snap.bindings[artifact_path(root/'main.json')]}:
        raise ValueError('Bound50k configuration differs')
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
    if set(manifest['files'])!=LINEAGE_FILES:raise ValueError('Incomplete three-prefix lineage')
    payload={}
    for name,h in manifest['files'].items():
        payload[name]=snap.read(root/'lineage'/name,lines=name.endswith('.jsonl'),require_newline=name.endswith('.jsonl'))
        if snap.bindings[artifact_path(root/'lineage'/name)]!=h:raise ValueError('Lineage snapshot differs')
    prefix=calculate(payload,probe,prompts)
    if prefix!=sealed:raise ValueError('Sealed three-prefix audit differs from recomputation')
    original=payload['previous_lineage.json']['original_contract.json']
    extension_meta=payload['extension_metadata.json']
    current=extension_meta['training_contract'];restored=parent_meta['training_contract']
    prefix_records=lineage_records(payload)
    parent_counts=prefix['counts'];parent_trace=prefix['trace']
    paths=sorted((root/'ledgers').glob('*.json'));seen=set();child_records=[]
    for path in paths:
        match=re.fullmatch(r'(.+)-rank([0-7])\.json',path.name)
        stamp=(path.stat().st_dev,path.stat().st_ino)
        if not match or stamp in seen:raise ValueError('Invalid or aliased child ledger')
        seen.add(stamp);record=snap.read(path)
        if record['rank']!=int(match[2]):raise ValueError('Child rank filename differs')
        child_records.append((match[1],record))
    child_counts=aggregate_suffix(child_records,parent_step=begin,through_step=through_step,contract=t)
    counts=aggregate_budget_lineage(original=original,current=current,restored=restored,child=t,
        original_records=prefix_records['original'],current_records=prefix_records['current'],
        restored_records=prefix_records['restored'],child_records=child_records,
        extension_metadata=extension_meta,parent_metadata=parent_meta,through_step=through_step)
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
            raise ValueError('Final50k completion/full8rank coverage differs')
    if paths!=sorted((root/'ledgers').glob('*.json')):raise ValueError('Child ledger set changed')
    if file_stats(parent_path)!=receipt['parent_stats']:raise ValueError('Protected parent changed during audit')
    if file_stats(checkpoint)!=checkpoint_stats:raise ValueError('Child checkpoint changed during audit')
    for p,h in {**c['code'],**c['evidence']}.items():
        if sha256_file(exact_path(resolve(p)))!=h:raise ValueError('Bound source evidence changed during audit: '+p)
    snap.unchanged();categories=Counter()
    for group in probe['groups']:categories[group['category']]+=counts['source_tokens'][group['source']]
    return dict(status='passed_budget_committed_prefix_audit',utc=datetime.now(timezone.utc).isoformat(),
        source_run=artifact_path(root),validation_source=False,through_step=through_step,target_steps=TARGET_STEPS,
        goal_complete=False,counts=counts,parent_counts=parent_counts,child_counts=child_counts,
        category_tokens=dict(categories),trace=dict(parent=parent_trace,child=child_trace),
        checkpoint_certificate=artifact_path(certificate_path),evidence=snap.bindings,
        code={p:sha256_file(resolve(p)) for p in ['scripts/audit_budget_training.py','scripts/budget_final_lineage.py','scripts/audit_budget_horizon.py','scripts/audit_extended_horizon.py','scripts/audit_horizon_training.py','scripts/audit_main_training.py','scripts/budget_horizon_lineage.py','scripts/extended_horizon_lineage.py']},
        limitations=['Full payload producer SHA receipt reused; metadata/RNG freshly hashed.',
            'Actual final GPU restore, native evaluation, retention and inference packaging remain separate.',
            'Protected parent payload remains required by current worker admission; lifecycle retirement is separate.'])
