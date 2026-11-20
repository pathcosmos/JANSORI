"""Prepare35k final full-state resume diagnostics without launching GPUs.

Shared DCP certification/comparison remains in the frozen generic verifier.
Preparation requires the actually activated child contract; it does not count
as final restoration or change the running producer/transition code.
"""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve, artifact_path, atomic_json, identity, sha256_file
from train.horizon_target import TARGET_STEPS
from train.final_horizon_resume_probe import diagnostic_lr

def prepare(main, root, *, native_run=None):
    main, root = resolve(main), resolve(root)
    if root.exists() or root.is_relative_to(main) or main.is_relative_to(root) or not root.is_relative_to(resolve('runs')):
        raise ValueError('A new independent diagnostic run is required')
    config_path = main/'main.json'
    config = json.loads(config_path.read_text())
    mc = json.loads((main/'contract.json').read_text())
    parent_path = main/'training/training_contract.json'
    parent = json.loads(parent_path.read_text())
    if (not mc.get('main_training_authorized') or not mc.get('new_checkpoint_pruning_authorized') or
            mc['configurations']['main.json'] != sha256_file(config_path) or config['model'] != '3b' or config['max_steps'] != TARGET_STEPS or
            config['compile'] or parent['world_size'] != 8 or parent['id'] != identity({k:v for k,v in parent.items() if k != 'id'})):
        raise ValueError('Reviewed parent3B8rank contract missing')
    for path, digest in {**mc['code'], **mc['evidence']}.items():
        if sha256_file(resolve(path)) != digest:
            raise ValueError('Parent code/evidence changed: '+path)
    from train.monitored_horizon import validate_configuration, load_handoff
    validate_configuration(config, mc)
    _, candidate, _ = load_handoff(main, mc, config)
    if mc.get('purpose') != 'main_training' or parent != candidate:
        raise ValueError('Exact activated35k production parent required')
    if mc['space_reservations']['nfs'].get(str(root)) != 2**30:
        raise ValueError('Exact final resume NFS reservation missing')
    if native_run is None:
        raise ValueError('The concurrently reserved native evaluation run is required')
    native = resolve(native_run)
    native_contract = json.loads((native/'contract.json').read_text())
    if (native_contract.get('final_step') != TARGET_STEPS or native_contract['purpose'] != 'final_native_main_evaluation' or
            resolve(native_contract['main_run']) != main or native == root or
            (native_contract['host_rss_gib'], native_contract['durable_budget_gib'],
             native_contract['local_budget_gib']) != (256, 8, 8)):
        raise ValueError('Concurrent native run/target/resource reservation differs')
    if mc['space_reservations']['nfs'].get(str(native)) != 8*2**30:
        raise ValueError('Exact final native NFS reservation missing')
    scratch = Path(mc['tiered_retention']['temp_root'])/root.name
    if scratch.exists():
        raise ValueError('Preserve existing diagnostic scratch')
    if any(p.is_symlink() for path in [root, scratch] for p in [path, *path.parents]):
        raise ValueError('Diagnostic preparation path symlink rejected')
    files = [main/'contract.json', config_path, main/'authorization.json', parent_path,
             main/'expected_tokens.json', native/'contract.json']
    code = dict(mc['code'])
    for name in ['train/final_horizon_resume_probe.py', 'scripts/prepare_final_horizon_resume.py',
                 'train/final_resume_probe.py', 'scripts/verify_final_resume_probe.py',
                 'train/horizon_target.py', 'scripts/audit_horizon_training.py',
                 'scripts/audit_main_training.py', 'scripts/validate_tiered_retention_copy.py']:
        code[name] = sha256_file(resolve(name))
    plan = dict(version=2, purpose='isolated_final_horizon_resume_probe', main_run=artifact_path(main), run_dir=artifact_path(root),
                native_run=artifact_path(native),
                scratch=str(scratch), parent_contract=artifact_path(parent_path), parent_config=artifact_path(config_path),
                parent_training_contract_id=parent['id'], parent_step=TARGET_STEPS, world_size=8, updates=2,
                main_target_modified=False, lr_policy='freeze_last_parent_update', diagnostic_lr=diagnostic_lr(config),
                code=code, inputs={artifact_path(p): sha256_file(p) for p in files}, framework=mc['framework'],
                max_seconds=3600, max_pss_gib=256, cpu_affinity=list(range(64,96)),
                local_budget_gib=192, durable_budget_gib=1, reserved_main_temp_gib=1024,
                estimated_checkpoint_count=4, expected_checkpoint_bytes=mc['expected_checkpoint_bytes'],
                required_gpu_gate='Audited actual35000 + full parent SHA + global idle8GPU lock',
                scope='Full-state roundtrip + continuous2 versus split1/resume1 in separate local scratch. No pruning or main mutation.')
    root.mkdir(parents=True)
    atomic_json(root/'plan.json', plan)
    atomic_json(root/'prepared.json', dict(status='prepared_not_executed', plan_sha256=sha256_file(root/'plan.json'),
                plan_id=identity(plan), actual_final_resume_verified=False))
    return plan


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run', required=True)
    parser.add_argument('--run-dir', required=True)
    parser.add_argument('--native-run', required=True)
    args = parser.parse_args()
    prepare(args.source_run, args.run_dir, native_run=args.native_run)
