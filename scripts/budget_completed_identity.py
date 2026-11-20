"""Reconstruct final50k numerical identity without opening ancestor payloads.

Pure metadata checks only. Fresh input/code hashes, original four-segment audit,
source-process absence, NFS tensor SHA, actual8GPU restore under dependency
isolation, and retirement acknowledgements remain separate mandatory gates.
No frozen production or final consumer imports this preparation module.
"""
import re

from common.data_contracts import identity, resolve
from scripts.budget_final_lineage import contract_intervals
from scripts.tiered_checkpoint_retention import records
from train.budget_horizon_contract import BRIDGE_CODE, BINDING_KEY, bound_candidate
from train.budget_horizon_handoff import validate_configuration

TARGET = 50000
TOKENS = TARGET * 2097152


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('Exact SHA256 digest required')
    return value


def _same_numerical_config(candidate, config, contract):
    if (candidate.get(BINDING_KEY) != contract['budget_execution'] or
            any(type(config.get(k)) is not type(v) or config[k] != v
                for k,v in candidate['config'].items()) or
            resolve(config['out_dir']) != resolve(contract['main_run'])/'training'):
        raise ValueError('Saved numerical config/binding/output differs from original identity')


def reconstruct_identity(contract, config, receipt, parent_certificate, actual, *,
                         parent_certificate_sha256):
    """Use durable receipt/certificate metadata, never load_handoff/payload I/O.

    The caller must bind the byte SHA supplied here to the actual certificate
    read; its canonical JSON identity is checked independently below.
    """
    if (contract.get('purpose') != 'main_training' or
            contract.get('main_training_authorized') is not True or
            contract.get('new_checkpoint_pruning_authorized') is not True):
        raise ValueError('Activated authorized50k main contract required')
    contract_intervals(contract)
    validate_configuration(config, contract)
    binding = contract['budget_execution']
    metadata = receipt['parent_metadata']
    if (receipt.get('status') != 'sealed_budget_parent_handoff' or
            receipt.get('execution_binding') != binding or
            _digest(parent_certificate_sha256) != binding['parent_certificate_sha256'] or
            identity(parent_certificate) != binding['parent_certificate_id']):
        raise ValueError('Original50k handoff/certificate binding differs')
    records(parent_certificate)
    if (parent_certificate.get('status') != 'passed_main_checkpoint_storage' or
            type(parent_certificate.get('step')) is not int or
            parent_certificate['step'] != metadata['step'] or
            parent_certificate.get('training_contract_id') != metadata['training_contract']['id']):
        raise ValueError('Historical parent certificate identity differs')
    candidate = bound_candidate(metadata, binding)
    if (actual != candidate or receipt.get('candidate_contract_id') != candidate['id'] or
            any(contract['code'].get(p) != binding['code'][p] for p in BRIDGE_CODE) or
            contract['generation_steps'] != candidate['monitored_evaluation']['generation_steps']):
        raise ValueError('Original50k numerical/code/generation identity differs')
    _same_numerical_config(candidate, config, contract)
    main = resolve(contract['main_run'])
    source = resolve(contract['retention_branches'][2]['run'])
    expected_parent = main/'parent_checkpoint'/f"ckpt_{metadata['step']:07d}"
    if (resolve(receipt['protection_root']) != main or resolve(receipt['source_run']) != source or
            resolve(receipt['parent_checkpoint']) != expected_parent or
            resolve(parent_certificate['checkpoint']) != source/'training'/expected_parent.name or
            resolve(config['out_dir']) != main/'training'):
        raise ValueError('Original source/protection/current output scope differs')
    # These paths are compared, never opened: ancestors can be inaccessible.
    return candidate


def verify_completed_metadata(contract, config, candidate, metadata, certificate,
                              report, marker, *, report_sha256, contract_sha256):
    """Require final50k completion and matching own NFS checkpoint metadata.

    Does not substitute for complete ledger/source audit or fresh payload SHA.
    A report/marker alone cannot authorize an independent restore or retirement.
    """
    contract_intervals(contract)
    validate_configuration(config, contract)
    if (candidate.get('id') != identity({k:v for k,v in candidate.items() if k != 'id'}) or
            candidate.get('world_size') != 8 or candidate['config']['max_steps'] != TARGET):
        raise ValueError('Verified original50k candidate required')
    _same_numerical_config(candidate, config, contract)
    main = resolve(contract['main_run'])
    checkpoint = main/'training'/'ckpt_0050000'
    saved_config = dict(config, out_dir=str(resolve(config['out_dir'])))
    if (type(metadata.get('step')) is not int or metadata['step'] != TARGET or
            metadata.get('training_contract') != candidate or metadata.get('config') != saved_config or
            metadata.get('world_size') != 8 or metadata.get('tokens_per_step') != 2097152 or
            metadata.get('sampler_version') != config['sampler_version']):
        raise ValueError('Final own checkpoint metadata/geometry differs')
    records(certificate)
    if (certificate.get('status') != 'passed_main_checkpoint_storage' or
            type(certificate.get('step')) is not int or certificate['step'] != TARGET or
            certificate.get('training_contract_id') != candidate['id'] or
            resolve(certificate['checkpoint']) != checkpoint):
        raise ValueError('Final certificate scope differs')
    if (marker.get('report_sha256') != _digest(report_sha256) or
            marker.get('contract_sha256') != _digest(contract_sha256) or
            report.get('status') != 'main_target_completed_review_pending' or
            type(report.get('trained_tokens')) is not int or report['trained_tokens'] != TOKENS or
            resolve(report['final_checkpoint']) != checkpoint):
        raise ValueError('Final completion marker/report/target differs')
    return dict(status='verified_final50k_metadata_identity_only',
        checkpoint=str(checkpoint), training_contract_id=candidate['id'], target_step=TARGET,
        target_tokens=TOKENS, report_sha256=report_sha256, contract_sha256=contract_sha256,
        parent_payload_read=False, ledger_audit_verified=False, source_exit_verified=False,
        full_payload_sha_verified=False, actual8gpu_verified=False,
        retirement_authorized=False, goal_complete=False)
