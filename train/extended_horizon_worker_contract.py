"""Bind a second horizon transition without overwriting the original8k ancestry.

Constructing a contract is not worker admission. The future entry must verify
the code files, protected checkpoint, immutable lineage, process ownership and
actual8GPU diagnostic evidence before executing it.
"""
from copy import deepcopy
import re

from common.data_contracts import identity, resolve, sha256_file
from train.extended_horizon_transition import build_extension_candidate
from train.parent_checkpoint_resume import canonical

WORKER = 'train/monitored_extended_horizon.py'
BRIDGE_CODE = {WORKER, 'train/extended_horizon_worker_contract.py',
    'train/extended_parent_checkpoint_resume.py', 'train/extended_horizon_transition.py',
    'train/horizon_transition.py', 'train/parent_checkpoint_resume.py',
    'scripts/audit_extended_horizon.py', 'scripts/audit_horizon_training.py',
    'scripts/audit_main_training.py', 'train/final_resume_probe.py',
    'scripts/tiered_checkpoint_retention.py', 'train/extended_handoff.py',
    'scripts/extended_horizon_lineage.py', 'scripts/seal_horizon_handoff.py',
    'scripts/extended_horizon_storage.py'}


def validate_binding(metadata, original, binding):
    fields = {'original_contract_id', 'parent_contract_id', 'parent_step',
              'parent_metadata_id', 'parent_certificate_sha256',
              'parent_certificate_id', 'parent_ledger_manifest_sha256', 'code'}
    if not isinstance(binding, dict) or set(binding) != fields:
        raise ValueError('Incomplete or unknown extension execution binding')
    if (binding['original_contract_id'] != original['id'] or
            binding['parent_contract_id'] != metadata['training_contract']['id'] or
            type(binding['parent_step']) is not int or binding['parent_step'] != metadata['step'] or
            binding['parent_metadata_id'] != identity(metadata)):
        raise ValueError('Extension binding references different ancestry/metadata')
    if not isinstance(binding['code'], dict) or set(binding['code']) != BRIDGE_CODE:
        raise ValueError('Extension implementation coverage differs')
    digests = [binding[k] for k in fields-{'parent_step','code'}] + list(binding['code'].values())
    if any(not isinstance(d,str) or not re.fullmatch('[0-9a-f]{64}',d) for d in digests):
        raise ValueError('Invalid extension binding digest')


def bound_candidate(metadata, original, binding):
    validate_binding(metadata, original, binding)
    candidate, _ = build_extension_candidate(metadata, original)
    if 'extended_horizon_transition' in candidate:
        raise ValueError('Unexpected pre-existing extension binding')
    candidate.pop('id')
    candidate['monitored_evaluation']['code_sha256'] = binding['code'][WORKER]
    candidate['extended_horizon_transition'] = deepcopy(binding)
    candidate['id'] = identity(candidate)
    return candidate


def validate_bound_candidate(metadata, original, child, binding):
    expected = bound_candidate(metadata, original, binding)
    if child != expected:
        raise ValueError('Child differs from the exact bound57k extension')
    return dict(status='passed_extension_contract_binding_only',
        original_contract_id=original['id'], parent_contract_id=metadata['training_contract']['id'],
        candidate_contract_id=child['id'], committed_prefix_step=metadata['step'],
        target_steps=57000, runtime_authorized=False, distributed_resume_verified=False)


def verify_bridge_files(binding):
    """Fail closed if any entry/dependency is missing, symlinked or changed."""
    if set(binding.get('code', {})) != BRIDGE_CODE:
        raise ValueError('Extension implementation coverage differs')
    for name, digest in binding['code'].items():
        path = canonical(resolve(name))
        if not path.is_file() or sha256_file(path) != digest:
            raise ValueError('Missing or changed extension implementation: '+name)

