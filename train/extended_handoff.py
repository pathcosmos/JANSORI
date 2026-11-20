"""Fail-closed admission for a sealed35k->57k, two-prefix handoff.

This module never stops producers, creates protection copies or admits a run
from summary pass flags alone. All bound prefix records/traces are recomputed.
"""
import json
from pathlib import Path

from common.data_contracts import resolve, sha256_file, identity
from train.parent_checkpoint_resume import canonical
from train.extended_horizon_worker_contract import BRIDGE_CODE, bound_candidate, verify_bridge_files
from train.extended_parent_checkpoint_resume import ExtendedParentResume
from scripts.extended_horizon_lineage import verify_lineage
from scripts.seal_horizon_handoff import require_parent_absent
from scripts.tiered_checkpoint_retention import inventory
from train.final_resume_probe import file_stats
from scripts.extended_horizon_storage import enforce_shared_space


def validate_configuration(config, contract):
    from scripts.plan_main_training import geometry
    g=geometry(config)
    expected=dict(model='3b',vocab_size=65536,seq_len=4096,micro_bsz=4,
        global_tokens=2097152,compile=False,sampler_version=2,max_steps=57000,
        lr=.0003,warmup=1000,decay_frac=.15,min_lr_ratio=.1,
        eval_every=250,ckpt_every=500,save_final=True,log_every=10)
    if any(type(config.get(k)) is not type(v) or config[k]!=v for k,v in expected.items()):
        raise ValueError('Unreviewed57k training geometry/cadence')
    if type(config.get('keep_ckpts')) is not int or config['keep_ckpts']<=57000:
        raise ValueError('Native pruning must remain disabled')
    if contract.get('main_training_authorized') is not True:
        raise ValueError('Missing training authorization')
    purpose=contract.get('purpose')
    if purpose=='main_training':
        if config.get('stop_after') is not None:raise ValueError('Production target shortened')
    elif purpose=='extended_horizon_validation':
        begin=contract['extension_execution']['parent_step']
        if type(config.get('stop_after')) is not int or not begin<config['stop_after']<=begin+2:
            raise ValueError('Diagnostic must stop after one/two absolute updates')
    else:raise ValueError('Unknown57k execution purpose')
    schedule=contract.get('generation_steps')
    if (not isinstance(schedule,list) or not schedule or
            any(type(s) is not int or not 0<s<=57000 or s%250 for s in schedule) or
            schedule!=sorted(set(schedule)) or schedule[-1]!=57000):
        raise ValueError('Generation schedule must end at57000')
    return g


def require_waiting_controllers_absent(rows):
    required={'observer','native','resume','retention','hf','runtime','serving'}
    if set(rows)!=required or len({r['pid'] for r in rows.values()})!=len(required):
        raise ValueError('Missing/duplicate old final-controller identities')
    for row in rows.values():
        pid,ticks=row['pid'],row['start_ticks']
        if type(pid) is not int or pid<=1 or type(ticks) is not int or ticks<=0:
            raise ValueError('Invalid final-controller identity')
        try:stat=Path(f'/proc/{pid}/stat').read_text()
        except FileNotFoundError:continue
        if int(stat.rsplit(')',1)[1].split()[19])==ticks:
            raise ValueError('Old final controller still present')


def load_handoff(root, contract, config):
    root=canonical(root);main=canonical(resolve(contract['main_run']))
    if root.parent!=canonical(resolve('runs')) or main.parent!=root.parent:
        raise ValueError('Exact independent permanent run namespaces required')
    if root.stat().st_dev!=main.stat().st_dev:
        raise ValueError('Run metadata must remain on permanent storage')
    out=canonical(resolve(config['out_dir']))
    if contract['purpose']=='main_training':
        if root!=main or out!=main/'training':raise ValueError('Production output namespace differs')
    else:
        temp=canonical(contract['diagnostic_temp_root'])
        if (root==main or out!=temp/root.name/'training' or
                str(temp) not in contract['shared_storage']['local_temp_paths']):
            raise ValueError('Diagnostic output outside the single reserved local pool')
        ancestor=temp
        while not ancestor.exists():ancestor=ancestor.parent
        if ancestor.stat().st_dev==main.stat().st_dev:
            raise ValueError('Diagnostic training output must use local filesystem')
    receipt_path=root/'parent_handoff.json'
    if contract['evidence'].get(str(receipt_path))!=sha256_file(receipt_path):
        raise ValueError('Unbound extension handoff')
    receipt=json.loads(receipt_path.read_text());binding=contract['extension_execution']
    if (receipt.get('status')!='sealed_extended_parent_handoff' or
            receipt.get('execution_binding')!=binding or
            canonical(resolve(receipt['protection_root']))!=main):
        raise ValueError('Unsealed or mismatched extension handoff')
    verify_bridge_files(binding)
    if any(contract['code'].get(n)!=binding['code'][n] for n in BRIDGE_CODE):
        raise ValueError('Outer/inner extension code binding differs')
    for path,digest in {**contract['code'],**contract['evidence']}.items():
        if sha256_file(canonical(resolve(path)))!=digest:
            raise ValueError('Bound execution evidence changed: '+path)
    meta=receipt['parent_metadata'];original=receipt['original_contract']
    candidate=bound_candidate(meta,original,binding)
    if contract['generation_steps']!=candidate['monitored_evaluation']['generation_steps']:
        raise ValueError('Outer generation schedule differs')
    parent=canonical(resolve(receipt['parent_checkpoint']))
    if parent!=main/'parent_checkpoint'/f"ckpt_{meta['step']:07d}" or parent.stat().st_dev!=main.stat().st_dev:
        raise ValueError('Protected parent outside permanent new-run scope')
    proof_path=main/'parent_certificate.json'
    if sha256_file(proof_path)!=binding['parent_certificate_sha256']:
        raise ValueError('Protected parent certificate bytes changed')
    proof=json.loads(proof_path.read_text())
    if (identity(proof)!=binding['parent_certificate_id'] or proof.get('step')!=meta['step'] or
            proof.get('training_contract_id')!=meta['training_contract']['id']):
        raise ValueError('Protected parent certificate identity differs')
    inventory(parent,proof)
    if file_stats(parent)!=receipt['parent_stats'] or json.loads((parent/'meta.json').read_text())!=meta:
        raise ValueError('Protected parent changed after SHA verification')
    source=canonical(resolve(receipt['source_run']));ancestor=canonical(resolve(receipt['original_run']))
    if len({main,source,ancestor})!=3 or source.parent!=main.parent or ancestor.parent!=main.parent:
        raise ValueError('Three distinct run namespaces required')
    require_parent_absent(receipt['source_capture'],source)
    require_parent_absent(receipt['original_capture'],ancestor)
    require_waiting_controllers_absent(receipt['stopped_waiting_controllers'])
    lineage=main/'lineage'
    if sha256_file(lineage/'manifest.json')!=binding['parent_ledger_manifest_sha256']:
        raise ValueError('Two-prefix lineage manifest changed')
    probe=json.loads((root/'probe/probe.json').read_text())
    prompts=[json.loads(s) for s in resolve(contract['prompts']).read_text().splitlines() if s.strip()]
    if (sha256_file(root/'probe/probe.json')!=candidate['monitored_evaluation']['probe_sha256'] or
            sha256_file(resolve(contract['prompts']))!=candidate['monitored_evaluation']['prompts_sha256']):
        raise ValueError('Probe/prompt identity differs')
    manifest,_=verify_lineage(lineage,meta,original,probe,prompts)
    if canonical(resolve(manifest['source_run']))!=source:
        raise ValueError('Lineage source namespace differs from stopped producer')
    if contract['purpose']=='main_training':
        validation_path=canonical(resolve(contract['extension_validation_report']))
        if contract['evidence'].get(str(validation_path))!=sha256_file(validation_path):
            raise ValueError('Actual8GPU validation report not bound')
        validation=json.loads(validation_path.read_text())
        if (validation.get('status')!='passed_actual_8rank_extension_resume' or
                validation.get('world_size')!=8 or validation.get('parent_step')!=meta['step'] or
                validation.get('parent_contract_id')!=meta['training_contract']['id'] or
                validation.get('child_contract_id')!=candidate['id'] or
                any(validation.get(k) is not True for k in
                    ('model_optimizer_rng_exact','next_update_exact','split_resume_exact')) or
                not validation.get('evidence')):
            raise ValueError('Actual8GPU extension validation incomplete')
        for path,digest in validation['evidence'].items():
            if sha256_file(canonical(resolve(path)))!=digest:
                raise ValueError('Underlying8GPU validation evidence changed')
    enforce_shared_space(main,contract)
    return receipt,candidate,proof


def parent_resume(receipt, candidate, proof, config, contract):
    return ExtendedParentResume(original_contract=receipt['original_contract'],
        parent_checkpoint=resolve(receipt['parent_checkpoint']),destination=resolve(config['out_dir']),
        parent_metadata=receipt['parent_metadata'],child_contract=candidate,parent_proof=proof,
        verified_parent_stats=receipt['parent_stats'],execution_binding=contract['extension_execution'])
