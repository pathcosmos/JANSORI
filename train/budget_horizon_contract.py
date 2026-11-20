"""Strict 57k -> 50k fourth-generation contract, without execution permission.

The two prior transition bindings are preserved byte-for-byte. Production
admission additionally needs a sealed prefix, full-state parent certificate,
process ownership and actual distributed resume evidence.
"""
from copy import deepcopy
import re

from common.data_contracts import identity, resolve, sha256_file
from train.horizon_transition import build_candidate, validate_transition, _verified_contract
from train.parent_checkpoint_resume import canonical

TARGET_STEPS = 50000
TOTAL_TOKENS = TARGET_STEPS * 2097152
DECAY_START = 42500
WORKER = 'train/monitored_budget_horizon.py'
BINDING_KEY = 'budget_horizon_transition'
BRIDGE_CODE = {
    WORKER, 'train/budget_horizon_contract.py', 'train/budget_horizon_handoff.py',
    'train/budget_parent_resume.py', 'scripts/budget_horizon_lineage.py',
    'scripts/audit_budget_horizon.py', 'train/horizon_transition.py',
    'train/parent_checkpoint_resume.py', 'train/final_resume_probe.py',
    'scripts/audit_extended_horizon.py', 'scripts/audit_horizon_training.py',
    'scripts/audit_main_training.py', 'scripts/tiered_checkpoint_retention.py',
    'scripts/seal_horizon_handoff.py', 'scripts/extended_horizon_storage.py',
}


def candidate(metadata):
    parent = _verified_contract(metadata['training_contract'])
    cfg = parent['config']
    if (cfg['max_steps'] != 57000 or cfg['global_tokens'] != 2097152
            or cfg['seq_len'] != 4096 or cfg['warmup'] != 1000
            or cfg['lr'] != .0003 or cfg['decay_frac'] != .15
            or cfg['min_lr_ratio'] != .1):
        raise ValueError('Expected the approved 57k source geometry/schedule')
    if (BINDING_KEY in parent or parent.get('horizon_transition', {}).get('parent_step') != 8000
            or parent.get('extended_horizon_transition', {}).get('parent_step') != 10000):
        raise ValueError('Expected unchanged 8k/10k ancestry and no prior 50k binding')
    step = metadata['step']
    if type(step) is not int or not 10000 < step < DECAY_START or step % 500:
        raise ValueError('50k handoff requires a certified 500-step boundary before decay')
    child, report = build_candidate(metadata, TARGET_STEPS)
    report.update(total_tokens=TOTAL_TOKENS, lineage_sealed=False, runtime_authorized=False)
    return child, report


def validate_candidate(metadata, child):
    expected, report = candidate(metadata)
    if child != expected:
        raise ValueError('Unreviewed 50k data/config/code/ancestry change')
    validate_transition(metadata['training_contract'], child, metadata['step'])
    return report


def validate_binding(metadata, binding):
    fields = {'parent_contract_id', 'parent_step', 'parent_metadata_id',
              'parent_certificate_sha256', 'parent_certificate_id',
              'parent_ledger_manifest_sha256', 'code'}
    if not isinstance(binding, dict) or set(binding) != fields:
        raise ValueError('Missing or unknown 50k execution binding fields')
    if (binding['parent_contract_id'] != metadata['training_contract']['id']
            or type(binding['parent_step']) is not int or binding['parent_step'] != metadata['step']
            or binding['parent_metadata_id'] != identity(metadata)):
        raise ValueError('50k execution binding parent differs')
    if not isinstance(binding['code'], dict) or set(binding['code']) != BRIDGE_CODE:
        raise ValueError('50k bridge dependency coverage differs')
    digests = [binding[k] for k in fields - {'parent_step', 'code'}] + list(binding['code'].values())
    if any(not isinstance(h, str) or not re.fullmatch('[0-9a-f]{64}', h) for h in digests):
        raise ValueError('Invalid 50k binding digest')


def bound_candidate(metadata, binding):
    validate_binding(metadata, binding)
    child, _ = candidate(metadata)
    child.pop('id')
    child['monitored_evaluation']['code_sha256'] = binding['code'][WORKER]
    child[BINDING_KEY] = deepcopy(binding)
    child['id'] = identity(child)
    return child


def validate_bound_candidate(metadata, child, binding):
    if child != bound_candidate(metadata, binding):
        raise ValueError('Child differs from bound 50k contract')
    return dict(parent_contract_id=metadata['training_contract']['id'],
                candidate_contract_id=child['id'], parent_step=metadata['step'],
                target_steps=TARGET_STEPS, runtime_authorized=False,
                distributed_resume_verified=False)


def verify_bridge_files(binding):
    if set(binding.get('code', {})) != BRIDGE_CODE:
        raise ValueError('50k bridge dependency coverage differs')
    for name, digest in binding['code'].items():
        path = canonical(resolve(name))
        if not path.is_file() or sha256_file(path) != digest:
            raise ValueError('Missing or changed 50k bridge: ' + name)
