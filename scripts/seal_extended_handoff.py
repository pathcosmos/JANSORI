"""Seal a protected10000 boundary and both original-identity prefixes.

No signals or launch. Requires absent old producers/controllers, an authenticated
copy receipt, fresh prefix/trace recomputation and unchanged source evidence.
"""
import json
import re

from common.data_contracts import resolve,identity,sha256_file,artifact_path
from scripts.tiered_checkpoint_retention import exact_path,inventory
from scripts.seal_horizon_handoff import require_parent_absent,unchanged,write_once,json_bytes
from scripts.extended_horizon_lineage import prepare_lineage,verify_lineage
from scripts.audit_horizon_training import audit_run
from scripts.horizon_process_handoff import unsaved_progress
from train.extended_horizon_worker_contract import BRIDGE_CODE,bound_candidate,verify_bridge_files
from train.extended_handoff import require_waiting_controllers_absent
from train.final_resume_probe import file_stats


def seal_extension(source,root,capture,waiting_controllers,*,owner_id,step):
    source,root=map(lambda p:exact_path(resolve(p)),(source,root))
    if (source==root or source.parent!=resolve('runs') or root.parent!=resolve('runs') or
            not re.fullmatch('[0-9a-f]{32}',owner_id) or type(step) is not int or step!=10000):
        raise ValueError('Exact authorized10000 restoration namespace required')
    require_parent_absent(capture,source);require_waiting_controllers_absent(waiting_controllers)
    c=json.loads((source/'contract.json').read_text());legacy=c['legacy_retention']
    original_root=exact_path(resolve(legacy['source_run']))
    require_parent_absent(legacy['stopped_capture'],original_root)
    fresh=audit_run(str(source),step)
    checkpoint=source/'training'/f'ckpt_{step:07d}'
    meta=json.loads((checkpoint/'meta.json').read_text())
    source_proof=source/'checkpoint_certificates'/f'ckpt_{step:07d}.json'
    proof=json.loads(source_proof.read_text());parent=root/'parent_checkpoint'/checkpoint.name
    copy_receipt_path=parent.with_name(parent.name+'.receipt.json')
    protected=json.loads(copy_receipt_path.read_text());txn=protected['transaction']
    if (protected.get('status')!='verified_protected_parent' or txn.get('owner_id')!=owner_id or
            txn.get('source')!=str(checkpoint) or txn.get('destination')!=str(parent) or
            txn.get('proof_id')!=identity(proof) or protected.get('proof_id')!=identity(proof) or
            txn.get('step')!=step or txn.get('bytes')!=proof['bytes']):
        raise ValueError('Protected copy owner/source/certificate differs')
    inventory(parent,proof)
    if (protected['stats']!=file_stats(parent) or parent.stat().st_dev!=root.stat().st_dev or
            json.loads((parent/'meta.json').read_text())!=meta):
        raise ValueError('Protected full-state copy changed')
    manifest,audit=prepare_lineage(source,root/'lineage',step)
    for k in ('committed_tokens','committed_step_rank_pairs','source_tokens','generation_sha256_by_step'):
        if audit['counts'][k]!=fresh['counts'][k]:raise ValueError('Prefix changed during seal')
    write_once(root/'parent_certificate.json',source_proof.read_bytes())
    original=json.loads((root/'lineage/original_contract.json').read_text())
    binding=dict(original_contract_id=original['id'],parent_contract_id=meta['training_contract']['id'],
        parent_step=step,parent_metadata_id=identity(meta),parent_certificate_id=identity(proof),
        parent_certificate_sha256=sha256_file(root/'parent_certificate.json'),
        parent_ledger_manifest_sha256=sha256_file(root/'lineage/manifest.json'),
        code={p:sha256_file(resolve(p)) for p in BRIDGE_CODE})
    candidate=bound_candidate(meta,original,binding);verify_bridge_files(binding)
    rows=[json.loads(s) for s in (source/'training/log.jsonl').read_text().splitlines() if s.strip()]
    last=max([r['step'] for r in rows if 'tok_s' in r] or [0])
    receipt=dict(status='sealed_extended_parent_handoff',execution_binding=binding,
        protection_root=str(root),source_run=str(source),original_run=str(original_root),
        source_capture=capture,original_capture=legacy['stopped_capture'],
        stopped_waiting_controllers=waiting_controllers,parent_metadata=meta,original_contract=original,
        parent_checkpoint=str(parent),parent_stats=protected['stats'],candidate_contract_id=candidate['id'],
        protected_copy_receipt_sha256=sha256_file(copy_receipt_path),
        source_audit_counts=fresh['counts'],unsaved_progress=unsaved_progress(step,last,2097152),
        source_modified=False,runtime_applied=False,actual8gpu_resume_verified=False,
        code={artifact_path(__file__):sha256_file(__file__)})
    unchanged(manifest['source_evidence']);unchanged(fresh['evidence'])
    if protected['stats']!=file_stats(parent):raise ValueError('Protected parent changed before seal publication')
    require_parent_absent(capture,source);require_waiting_controllers_absent(waiting_controllers)
    write_once(root/'parent_handoff.json',json_bytes(receipt))
    return receipt
