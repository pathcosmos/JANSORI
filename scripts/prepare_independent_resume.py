"""Prepare isolated own-NFS diagnostic metadata, never stop/change training.

The capture is made by a separately reviewed boundary controller. Execution
requires those exact processes gone and original dependency paths unavailable.
Preparation does not authorize production adoption or checkpoint retirement.
"""
import argparse
import json
from pathlib import Path
import sys
from contextlib import nullcontext

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve,artifact_path,sha256_file,identity,atomic_json


def prepare(source,output,revision_path,capture_path,*,isolation_root=None):
    from scripts.tiered_checkpoint_retention import exact_path
    from train.extended_handoff import validate_configuration
    from train.independent_checkpoint_handoff import review_handoff
    from train.independent_resume_probe import required_absent_paths
    from scripts.run_independent_resume import admission
    main,root,revision_path,capture_path=map(lambda p:exact_path(resolve(p)),
        (source,output,revision_path,capture_path))
    if root.exists() or root.is_relative_to(main) or main.is_relative_to(root):
        raise ValueError('Preserve existing/source namespace')
    contract=json.loads((main/'contract.json').read_text())
    config=json.loads((main/'main.json').read_text())
    validate_configuration(config,contract)
    revision=json.loads(revision_path.read_text());step=revision['child']['step']
    if type(step) is not int or not 10000<step<=56500 or step%500:
        raise ValueError('Own certified500-step boundary before final required')
    capture=json.loads(capture_path.read_text())
    if (capture.get('status')!='observed_live_not_stopped' or capture.get('run')!=str(main) or
            set(capture.get('processes',{}))!={'supervisor','worker',*[f'rank{i}' for i in range(8)]}):
        raise ValueError('Exact original10process capture required')
    scratch=exact_path(Path(contract['tiered_retention']['temp_root'])/root.name)
    if scratch.exists():raise ValueError('Preserve existing diagnostic scratch')
    # Full original lineage/candidate/proofs and own checkpoint SHA. This
    # read-only check may run while training lives; execution cannot.
    from scripts.independent_resume_dependency_access import bind_isolation, IMPLEMENTATIONS
    from scripts.independent_resume_isolation import accounting_view
    isolation = None if isolation_root is None else bind_isolation(
        isolation_root,main,root,revision_path,capture_path)
    with nullcontext() if isolation is None else accounting_view(isolation['root']):
        review=review_handoff(main,contract,config,revision_path)
    parent=json.loads((main/'training/training_contract.json').read_text())
    if parent['id']!=review['training_contract_id']:raise ValueError('Original training identity differs')
    receipt=json.loads((main/'parent_handoff.json').read_text())
    files=[main/'contract.json',main/'main.json',main/'training/training_contract.json',
        main/'parent_handoff.json',resolve(receipt['source_run'])/'parent_handoff.json',
        revision_path,capture_path]
    code=dict(contract['code'])
    for name in ['scripts/prepare_independent_resume.py','scripts/run_independent_resume.py',
        'train/independent_resume_probe.py','train/independent_resume_bridge.py',
        'train/independent_operational_admission.py',
        'train/independent_checkpoint_handoff.py','train/independent_child_resume.py',
        'scripts/audit_independent_training.py','scripts/verify_independent_resume_probe.py',
        'scripts/freeze_independent_prefix.py',
        'scripts/seal_independent_resume_result.py',
        'scripts/verify_final_resume_probe.py','scripts/evaluate_final_rehearsal.py',
        'scripts/independent_resume_process_handoff.py',
        'scripts/horizon_probe_process.py','scripts/horizon_process_handoff.py',
        'scripts/seal_horizon_handoff.py','scripts/extended_horizon_storage.py',
        'scripts/resource_admission.py','scripts/hardware_profile.py','scripts/process_accounting.py']:
        code[name]=sha256_file(resolve(name))
    plan=dict(version=1,purpose='isolated_independent_nfs_resume_probe',
        main_run=artifact_path(main),run_dir=artifact_path(root),revision=artifact_path(revision_path),
        source_capture=capture,source_capture_path=artifact_path(capture_path),
        required_absent_paths=required_absent_paths(main,contract,revision),scratch=str(scratch),
        parent_contract=artifact_path(main/'training/training_contract.json'),
        parent_config=artifact_path(main/'main.json'),parent_training_contract_id=parent['id'],
        parent_step=step,world_size=8,updates=2,main_target_modified=False,
        lr_policy='original_schedule_next_updates',code=code,
        inputs={**contract['evidence'],**{artifact_path(p):sha256_file(p) for p in files}},
        framework=contract['framework'],max_seconds=3600,max_pss_gib=256,
        cpu_affinity=list(range(64,96)),local_budget_gib=192,durable_budget_gib=1,
        reserved_main_temp_gib=1024,estimated_checkpoint_count=4,
        frozen_prefix_required=True,
        operational_consumption_required=True,
        shared_admission_required=True,
        expected_checkpoint_bytes=contract['expected_checkpoint_bytes'],
        scope='Preparation only. No producer stop, path mutation, main update, deletion or production adoption.')
    if isolation is not None:
        plan['dependency_isolation']=isolation
        plan['inputs'].update(isolation['evidence'])
    for name in IMPLEMENTATIONS:
        plan['code'][name]=sha256_file(resolve(name))
    admission(root,plan)
    root.mkdir(parents=True,exist_ok=False)
    atomic_json(root/'plan.json',plan)
    atomic_json(root/'prepared.json',dict(status='prepared_not_executed',
        plan_sha256=sha256_file(root/'plan.json'),plan_id=identity(plan),actual8gpu_verified=False))
    return plan


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['source-run','run-dir','revision','capture']:p.add_argument('--'+name,required=True)
    p.add_argument('--isolation-root')
    a=p.parse_args();prepare(a.source_run,a.run_dir,a.revision,a.capture,isolation_root=a.isolation_root)
