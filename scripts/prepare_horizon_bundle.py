"""Prepare a concrete, non-executable35k migration bundle without touching producers."""
import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import shutil
import sys
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve,artifact_path,sha256_file,atomic_json
from scripts.hardware_profile import inspect
from scripts.horizon_storage_plan import capacity_plan,GIB
from scripts.tiered_checkpoint_retention import exact_path,size_tree
from scripts.retention_capacity import history_growth
from train.horizon_target import (TARGET_STEPS, TOTAL_TOKENS, DECAY_START_STEP,
                                  NATIVE_KEEP, LABEL, generation_schedule)


def prepare(source, destination, output):
    source,destination,output=map(lambda p:exact_path(resolve(p)),[source,destination,output])
    if destination.parent!=resolve('runs') or destination.exists() or output.exists():
        raise ValueError('Use a new exact run namespace and a new independent draft directory')
    if any(output.is_relative_to(p) or p.is_relative_to(output) for p in [source,destination]):
        raise ValueError('Draft output overlaps an execution namespace')
    mc=json.loads((source/'contract.json').read_text());config=json.loads((source/'main.json').read_text())
    if config['max_steps']!=57000 or mc['configurations'].get('main.json')!=sha256_file(source/'main.json'):
        raise ValueError('Expected the bound original57k configuration')
    from scripts.supervise_main_training import require_authorization
    require_authorization(mc)
    for p,h in {**mc['code'],**mc['evidence']}.items():
        if sha256_file(resolve(p))!=h:raise ValueError('Original execution input/code changed: '+p)
    certificates=sorted((source/'checkpoint_certificates').glob('ckpt_*.json'))
    if not certificates:raise ValueError('No certified parent observation')
    certificate=certificates[-1];proof=json.loads(certificate.read_text())
    if not 0<proof['step']<DECAY_START_STEP:raise ValueError('Parent outside unchanged LR prefix')
    hw=inspect();scratch=Path(hw['storage']['scratch_dir'])
    # Measured payload plus an explicit metadata-growth allowance, not a claim
    # that a new35k checkpoint's exact size was already measured.
    estimated_checkpoint_bytes=proof['bytes']+2**20
    policy=deepcopy(mc['tiered_retention'])
    source_used=size_tree(source);old_temp=Path(policy['temp_root']);old_history=Path(policy['local_root'])
    nfs_free=shutil.disk_usage(source).free;local_free=shutil.disk_usage(scratch).free
    capacity=capacity_plan(checkpoint_bytes=estimated_checkpoint_bytes,nfs_free=nfs_free,
        local_free=local_free,source_nfs_remaining=max(0,policy['nfs_budget_bytes']-source_used),
        source_history_growth=history_growth(policy,size_tree(old_history)),temporary_used=size_tree(old_temp))
    if not capacity['minimum_capacity_feasible']:raise RuntimeError('Combined migration capacity is not available')
    owner=uuid.uuid4().hex
    policy.update(run_id=owner,nfs_run=str(destination),artifact_training_prefix=artifact_path(destination/'training'),
        local_root=str(scratch/'checkpoint_history'/destination.name/owner),temp_root=str(scratch/destination.name),
        expected_checkpoint_bytes=estimated_checkpoint_bytes,
        protected_parent_budget_bytes=capacity['protected_parent_budget_bytes'],
        nfs_budget_bytes=capacity['new_nfs_retention_budget_bytes']+capacity['protected_parent_budget_bytes'],
        local_budget_bytes=capacity['new_local_full15_budget_bytes'],other_nfs_future_bytes=0)
    config.update(max_steps=TARGET_STEPS,keep_ckpts=NATIVE_KEEP,stop_after=None,out_dir=artifact_path(destination/'training'))
    root=resolve('runs')
    diagnostic_roots=[root/f'3b_{LABEL}_probe_continuous_v1',root/f'3b_{LABEL}_probe_split_v1']
    half=math.ceil(3*estimated_checkpoint_bytes*1.1)+GIB
    reservations={str(p):half for p in diagnostic_roots}
    reservations.update({str(resolve(f'artifacts/3b_base_{LABEL}_v1')):100*GIB,
        str(root/f'3b_{LABEL}_final_native_v1'):8*GIB,str(root/f'3b_{LABEL}_final_resume_v1'):GIB,
        str(root/f'3b_{LABEL}_final_retention_v1'):GIB,
        str(root/f'3b_{LABEL}_transition_v1'):GIB,str(root/f'3b_{LABEL}_observer_v1'):GIB})
    if any(Path(p).exists() for p in reservations):
        raise ValueError('Proposed diagnostic/export/final namespace already exists; inspect ownership first')
    code=dict(mc['code'])
    for p in ['train/monitored_horizon.py','train/horizon_worker_contract.py','train/horizon_transition.py',
        'train/parent_checkpoint_resume.py','train/final_resume_probe.py','scripts/audit_main_training.py',
        'scripts/supervise_horizon_training.py','scripts/protect_horizon_parent.py',
        'scripts/horizon_storage_plan.py','scripts/prepare_horizon_bundle.py',
        'scripts/horizon_process_handoff.py','scripts/seal_horizon_handoff.py','train/horizon_target.py',
        'scripts/horizon_retention.py','scripts/audit_horizon_training.py','scripts/evaluate_final_horizon.py',
        'scripts/activate_horizon_training.py','scripts/transition_horizon_training.py','scripts/observe_horizon_training.py']:
        code[p]=sha256_file(resolve(p))
    output.mkdir(parents=True)
    (output/'probe').mkdir()
    for name in ['expected_tokens.json','probe/probe.json','probe/.done']:
        (output/name).write_bytes((source/name).read_bytes())
    from train.probe import verify_probe
    verify_probe(output/'probe')
    atomic_json(output/'main.json',config)
    draft=dict(version=1,purpose='horizon_preparation_not_executable',activation_ready=False,
        intended_run=artifact_path(destination),source_run=artifact_path(source),
        source_contract_sha256=sha256_file(source/'contract.json'),
        requested_target_steps=TARGET_STEPS,requested_target_tokens=TOTAL_TOKENS,
        main_training_authorized=False,new_checkpoint_pruning_authorized=False,
        authorization_note='Draft flags prevent launch; existing user requests are retained. No new permission question is implied by draft status.',
        retention_policy='nfs_latest_with_ephemeral_history',archive_required_before_prune=True,
        tiered_retention=policy,protected_parent_budget_bytes=capacity['protected_parent_budget_bytes'],
        durable_budget_bytes=policy['nfs_budget_bytes'],local_budget_gib=1024,reserve_gib=200,
        configurations={'main.json':sha256_file(output/'main.json')},code=code,
        evidence=deepcopy(mc['evidence']),prompts=mc['prompts'],tokenizer=mc['tokenizer'],
        generation_steps=generation_schedule(mc['generation_steps']),
        framework=mc['framework'],gpu_identity=mc['gpu_identity'],memory_metric='process_tree_pss',
        max_pss_gib=mc['max_pss_gib'],max_seconds=mc['max_seconds'],expected_checkpoint_bytes=estimated_checkpoint_bytes,
        space_reservations=dict(nfs=reservations,local_temp_paths=[str(old_temp),policy['temp_root']]),
        pending=['Checkpoint-boundary stop receipt and protected parent copy',
            'Fresh sealed original8rank lineage and exact execution binding',
            'Actual8GPU full-state/continuous/split validation and evidence',
            'Versioned35k final controllers and complete lineage audit',
            'Activation binding of configurations/evidence/authorization to exact management paths'])
    atomic_json(output/'contract.draft.json',draft);atomic_json(output/'capacity.json',capacity)
    atomic_json(output/'hardware.json',hw)
    invariants={p:sha256_file(resolve(p))==h for p,h in mc['code'].items()}
    if not all(invariants.values()) or sha256_file(source/'main.json')!=mc['configurations']['main.json']:
        raise ValueError('Original execution changed while preparing the bundle')
    report=dict(status='prepared_draft_not_activated',output=artifact_path(output),
        intended_run=artifact_path(destination),latest_observed_certificate=artifact_path(certificate),
        latest_observed_step=proof['step'],measured_checkpoint_bytes=proof['bytes'],
        planned_metadata_growth_bytes=2**20,capacity=capacity,original_code_files_unchanged=len(invariants),
        production_changed=False,actual_parent_copied=False,actual8gpu_resume=False,
        prepared_bytes=size_tree(output),files={str(p.relative_to(output)):sha256_file(p) for p in output.rglob('*') if p.is_file()})
    atomic_json(output/'preparation_report.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True);parser.add_argument('--destination',required=True)
    parser.add_argument('--output',required=True);args=parser.parse_args()
    result=prepare(args.source,args.destination,args.output)
    print(json.dumps({k:result[k] for k in ['status','output','intended_run','latest_observed_step','prepared_bytes']}))
