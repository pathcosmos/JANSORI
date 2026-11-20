"""Explicit bindings for the35k worker; does not grant execution permission."""
from copy import deepcopy
import re

from common.data_contracts import identity
from train.horizon_transition import build_candidate
from train.horizon_target import TARGET_STEPS

WORKER = 'train/monitored_horizon.py'
BRIDGE_CODE = {WORKER, 'train/horizon_worker_contract.py',
               'train/horizon_transition.py', 'train/parent_checkpoint_resume.py',
               'train/horizon_target.py'}


def validate_binding(metadata, binding):
    fields = {'parent_contract_id', 'parent_step', 'parent_metadata_id',
              'parent_certificate_sha256', 'parent_ledger_manifest_sha256', 'code'}
    if not isinstance(binding, dict) or set(binding) != fields:
        raise ValueError('Incomplete or unknown horizon execution binding')
    if (binding['parent_contract_id'] != metadata['training_contract']['id'] or
            type(binding['parent_step']) is not int or binding['parent_step'] != metadata['step'] or
            binding['parent_metadata_id'] != identity(metadata)):
        raise ValueError('Execution binding references a different parent')
    if not isinstance(binding['code'], dict) or set(binding['code']) != BRIDGE_CODE:
        raise ValueError('Execution bridge code coverage differs')
    digests = [binding['parent_certificate_sha256'], binding['parent_ledger_manifest_sha256'],
               *binding['code'].values()]
    if any(not isinstance(d, str) or not re.fullmatch('[0-9a-f]{64}', d) for d in digests):
        raise ValueError('Invalid execution binding digest')


def executable_candidate(metadata, binding):
    validate_binding(metadata, binding)
    candidate, _ = build_candidate(metadata, TARGET_STEPS)
    candidate.pop('id')
    candidate['monitored_evaluation']['code_sha256'] = binding['code'][WORKER]
    candidate['horizon_transition'] = deepcopy(binding)
    candidate['id'] = identity(candidate)
    return candidate


def validate_executable_candidate(metadata, child, binding):
    expected = executable_candidate(metadata, binding)
    if child != expected:
        raise ValueError('Child differs from the exact bound execution candidate')
    return dict(parent_contract_id=metadata['training_contract']['id'],
                candidate_contract_id=child['id'], committed_prefix_step=metadata['step'],
                target_steps=TARGET_STEPS, runtime_applied=False, distributed_resume_verified=False)
