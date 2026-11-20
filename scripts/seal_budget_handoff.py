"""Seal a certified stopped57k boundary for50k, with no signals or launch."""
import json
import re

from common.data_contracts import resolve, identity, sha256_file
from scripts.tiered_checkpoint_retention import exact_path, inventory
from scripts.seal_horizon_handoff import require_parent_absent, unchanged, write_once, json_bytes
from scripts.budget_horizon_lineage import prepare_lineage
from scripts.audit_restored_training import audit_run
from scripts.horizon_process_handoff import unsaved_progress
from train.budget_horizon_contract import BRIDGE_CODE, bound_candidate, verify_bridge_files
from train.budget_horizon_handoff import require_controllers_absent
from train.final_resume_probe import file_stats



def discarded_suffix(rows, *, step, contract_id):
    """Count persisted per-rank work after the saved boundary, never as new progress."""
    if len(rows)!=8 or {r['rank'] for r in rows}!=set(range(8)):
        raise ValueError('All eight stopped attempt ledgers required')
    sets=[];persisted=0;extra_pairs=0;counts={}
    for row in rows:
        if row['contract_id']!=contract_id:raise ValueError('Stopped ledger identity differs')
        extra=[r for r in row['steps'] if r['step']>step]
        numbers=[r['step'] for r in extra]
        if numbers!=list(range(step+1,step+len(numbers)+1)):
            raise ValueError('Stopped suffix has a gap or duplicate')
        for r in extra:
            values=r['source_sequences'].values()
            if any(type(v) is not int or v<1 for v in values) or sum(values)*4096!=2097152//8:
                raise ValueError('Stopped suffix token geometry differs')
        counts[str(row['rank'])]=len(extra);sets.append(set(numbers))
        extra_pairs+=len(extra);persisted+=len(extra)*(2097152//8)
    complete=sorted(set.intersection(*sets))
    return dict(per_rank_updates=counts,persisted_step_rank_pairs=extra_pairs,
        persisted_tokens_after_boundary=persisted,all8_persisted_steps=complete,
        all8_persisted_tokens=len(complete)*2097152,
        partial_rank_tokens=persisted-len(complete)*2097152,
        credited_to_new_committed_total=False,
        scope='Stopped persisted ledgers only; unrecorded in-flight work is unknown. Resume re-executes after saved boundary.')


def seal_budget(source, root, capture, controllers, *, owner_id, step):
    source,root=map(lambda p:exact_path(resolve(p)),(source,root))
    if (source!=resolve('runs/3b_full_57k_v2') or root!=resolve('runs/3b_full_50k_v1')
            or not re.fullmatch('[0-9a-f]{32}',owner_id)
            or type(step) is not int or not 10000<step<42500 or step%500):
        raise ValueError('Exact57k-to50k stopped boundary namespace required')
    require_parent_absent(capture,source);require_controllers_absent(controllers)
    fresh=audit_run(str(source),step)
    checkpoint=source/'training'/f'ckpt_{step:07d}'
    meta=json.loads((checkpoint/'meta.json').read_text())
    certificate=source/'checkpoint_certificates'/(checkpoint.name+'.json')
    proof=json.loads(certificate.read_text());parent=root/'parent_checkpoint'/checkpoint.name
    receipt_path=parent.with_name(parent.name+'.receipt.json')
    protection=json.loads(receipt_path.read_text());txn=protection['transaction']
    if (protection.get('status')!='verified_protected_parent' or txn.get('owner_id')!=owner_id
            or txn.get('source')!=str(checkpoint) or txn.get('destination')!=str(parent)
            or txn.get('proof_id')!=identity(proof) or protection.get('proof_id')!=identity(proof)
            or txn.get('step')!=step or txn.get('bytes')!=proof['bytes']):
        raise ValueError('50k protected copy owner/source/certificate differs')
    inventory(parent,proof)
    if (protection['stats']!=file_stats(parent) or parent.stat().st_dev!=root.stat().st_dev
            or json.loads((parent/'meta.json').read_text())!=meta):
        raise ValueError('Protected 50k parent changed')
    manifest,audit=prepare_lineage(source,root/'lineage',step,capture)
    for key in ['committed_tokens','committed_step_rank_pairs','source_tokens','generation_sha256_by_step']:
        if audit['counts'][key]!=fresh['counts'][key]:raise ValueError('Source prefix changed during seal')
    write_once(root/'parent_certificate.json',certificate.read_bytes())
    binding=dict(parent_contract_id=meta['training_contract']['id'],parent_step=step,
        parent_metadata_id=identity(meta),parent_certificate_id=identity(proof),
        parent_certificate_sha256=sha256_file(root/'parent_certificate.json'),
        parent_ledger_manifest_sha256=sha256_file(root/'lineage/manifest.json'),
        code={p:sha256_file(resolve(p)) for p in BRIDGE_CODE})
    child=bound_candidate(meta,binding);verify_bridge_files(binding)
    rows=[json.loads(s) for s in (source/'training/log.jsonl').read_text().splitlines() if s.strip()]
    last=max([r['step'] for r in rows if 'tok_s' in r] or [0])
    stopped_rows=[json.loads((source/'ledgers'/(capture['attempt']+f'-rank{rank}.json')).read_text()) for rank in range(8)]
    discarded=discarded_suffix(stopped_rows,step=step,contract_id=meta['training_contract']['id'])
    result=dict(status='sealed_budget_parent_handoff',execution_binding=binding,
        protection_root=str(root),source_run=str(source),source_capture=capture,
        stopped_waiting_controllers=controllers,parent_metadata=meta,
        parent_checkpoint=str(parent),parent_stats=protection['stats'],candidate_contract_id=child['id'],
        protected_copy_receipt_sha256=sha256_file(receipt_path),source_audit_counts=fresh['counts'],
        unsaved_progress=unsaved_progress(step,last,2097152),discarded_persisted_suffix=discarded,source_modified=False,
        runtime_applied=False,actual8gpu_resume_verified=False,
        code={'scripts/seal_budget_handoff.py':sha256_file(__file__)})
    unchanged(manifest['source_evidence']);unchanged(fresh['evidence'])
    if protection['stats']!=file_stats(parent):raise ValueError('Protected parent changed before sealing')
    require_parent_absent(capture,source);require_controllers_absent(controllers)
    write_once(root/'parent_handoff.json',json_bytes(result))
    return result
