"""Separate final-stage reservations; never rewrite the training contract.

Preparation and capacity inspection are possible during training. Consumers
must call admit_completed before final-stage work. This module launches no
worker, changes no checkpoint and grants no parent-retirement permission.
"""
from copy import deepcopy
import json
from pathlib import Path

from common.data_contracts import resolve, sha256_file
from scripts.tiered_checkpoint_retention import exact_path, atomic_json, size_tree
from scripts.training_stage_storage_revision import DEFERRED, METADATA_ALLOWANCE, SCOPE
from scripts.extended_horizon_storage import enforce_shared_space

GIB = 2**30
OWN = 'scripts/completed_training_storage.py'
CODE = [OWN, 'scripts/extended_horizon_storage.py',
        'scripts/training_stage_storage_revision.py',
        'scripts/tiered_checkpoint_retention.py',
        'scripts/evaluate_restored_native.py', 'scripts/audit_restored_training.py',
        'scripts/horizon_process_handoff.py']
CAP = 8 * 2**20


def read(path):
    return json.loads(Path(path).read_text())


def projected_contract(main, source_sha256):
    """Budget view only. Source identities and all non-budget fields stay exact."""
    main = exact_path(resolve(main))
    path = main / 'contract.json'
    if sha256_file(path) != source_sha256:
        raise ValueError('Original training contract changed')
    base = read(path)
    phase = base.get('storage_phase_revision', {})
    if (base.get('purpose') != 'main_training' or base.get('target_steps') != 57000
            or exact_path(base['main_run']) != main
            or phase.get('scope') != SCOPE
            or phase.get('restore_before_final_stage') is not True
            or phase.get('unused_pool_metadata_cap_bytes') != METADATA_ALLOWANCE
            or set(phase.get('deferred_pools', {})) != set(DEFERRED)):
        raise ValueError('Exact deferred 57000-step source required')
    auth = exact_path(resolve(phase['authorization']))
    approval = read(auth)
    if (base['evidence'].get(str(auth)) != sha256_file(auth)
            or approval.get('authorized') is not True
            or approval.get('scope') != SCOPE or approval.get('target_step') != 57000
            or approval.get('restore_deferred_budget_before_final_work') is not True
            or approval.get('deferred_pool_names') != list(DEFERRED)):
        raise ValueError('Bound final-reservation authorization differs')
    if (base['shared_storage']['reserve_bytes'] < 200 * GIB
            or base['shared_storage']['temporary_budget_bytes'] != 1024 * GIB
            or base['tiered_retention']['nfs_keep'] != 3
            or base['tiered_retention']['local_keep'] != 15):
        raise ValueError('Original safety and retention limits differ')
    result = deepcopy(base)
    for name, gib in zip(DEFERRED, (8, 1, 1, 100)):
        original = phase['deferred_pools'][name]
        if (original.get('budget_bytes') != gib * GIB
                or len(original.get('paths', [])) != 1
                or base['shared_storage']['nfs_pools'][name] != dict(
                    paths=original['paths'], budget_bytes=METADATA_ALLOWANCE)):
            raise ValueError('Exact original final pool differs: ' + name)
        destination = exact_path(original['paths'][0])
        if destination.is_relative_to(main) or main.is_relative_to(destination):
            raise ValueError('Final output overlaps training source')
        result['shared_storage']['nfs_pools'][name] = deepcopy(original)
    if sha256_file(path) != source_sha256:
        raise ValueError('Original training contract changed during projection')
    return result


def prepare(main, root):
    main, root = (exact_path(resolve(p)) for p in (main, root))
    digest = sha256_file(main / 'contract.json')
    candidate = projected_contract(main, digest)
    pool = candidate['shared_storage']['nfs_pools']['transition']
    if len(pool['paths']) != 1 or pool['budget_bytes'] != GIB:
        raise ValueError('Exact transition metadata reservation required')
    scope = exact_path(pool['paths'][0])
    if (root.exists() or root == scope or not root.is_relative_to(scope)
            or size_tree(scope) + CAP > GIB):
        raise ValueError('Preserve existing paths and transition metadata budget')
    for p, h in {**candidate['code'], **candidate['evidence']}.items():
        if sha256_file(resolve(p)) != h:
            raise ValueError('Source code/evidence changed: ' + p)
    space = enforce_shared_space(main, candidate)
    plan = dict(purpose='completed_training_separate_final_reservations_v1',
                main_run=str(main), root=str(root), source_contract_sha256=digest,
                code={p: sha256_file(resolve(p)) for p in CODE},
                projected_contract=candidate, metadata_cap_bytes=CAP,
                requires_completed_57000_audit=True,
                source_contract_mutation_allowed=False,
                parent_retirement_authorized=False, execution_started=False)
    if sha256_file(main / 'contract.json') != digest:
        raise ValueError('Source changed before reservation preparation')
    root.mkdir()
    atomic_json(root / 'plan.json', plan)
    atomic_json(root / 'capacity_at_preparation.json', {
        k: v for k, v in space.items() if k != 'nfs_used_by_path'})
    atomic_json(root / 'prepared.json', dict(plan_sha256=sha256_file(root / 'plan.json')))
    return plan


def load(root):
    root = exact_path(resolve(root))
    plan = read(root / 'plan.json')
    if (read(root / 'prepared.json') != dict(plan_sha256=sha256_file(root / 'plan.json'))
            or plan.get('purpose') != 'completed_training_separate_final_reservations_v1'
            or plan.get('root') != str(root) or plan.get('metadata_cap_bytes') != CAP
            or plan.get('requires_completed_57000_audit') is not True
            or plan.get('source_contract_mutation_allowed') is not False
            or plan.get('parent_retirement_authorized') is not False
            or plan.get('execution_started') is not False
            or set(plan.get('code', {})) != set(CODE)):
        raise ValueError('Unreviewed final reservation preparation')
    for p, h in plan['code'].items():
        if sha256_file(resolve(p)) != h:
            raise ValueError('Final reservation code changed')
    main = exact_path(plan['main_run'])
    candidate = projected_contract(main, plan['source_contract_sha256'])
    for p, h in {**candidate['code'], **candidate['evidence']}.items():
        if sha256_file(resolve(p)) != h:
            raise ValueError('Source code/evidence changed: ' + p)
    scope = exact_path(candidate['shared_storage']['nfs_pools']['transition']['paths'][0])
    if (root == scope or not root.is_relative_to(scope)
            or candidate != plan['projected_contract'] or size_tree(root) > CAP):
        raise ValueError('Final reservation scope or budget projection changed')
    return main, candidate


def active_source_processes(main):
    from scripts.horizon_process_handoff import process_record
    found = []
    for path in Path('/proc').glob('[0-9]*'):
        try:
            # Only inspect full identities for candidate source processes;
            # PID1, kernel threads and unrelated jobs need no cwd inspection.
            raw_args = (path / 'cmdline').read_bytes().split(b'\0')
            if b'--run-dir' not in raw_args:
                continue
            if (path / 'stat').read_text().rsplit(')', 1)[1].split()[0] in ('Z', 'X'):
                continue
            cwd = (path / 'cwd').resolve(strict=True)
            if not any(arg == b'--run-dir' and (cwd / raw_args[i + 1].decode()).resolve() == main
                       for i, arg in enumerate(raw_args[:-1])):
                continue
            row = process_record(int(path.name))
            args = row['argv']
            for i, arg in enumerate(args[:-1]):
                if arg == '--run-dir' and (Path(row['cwd']) / args[i + 1]).resolve() == main:
                    if row['state'] not in ('Z', 'X'):
                        found.append(row)
                    break
        except (FileNotFoundError, ProcessLookupError):
            continue
    return found


def admit_completed(root):
    """Full final audit and live-process rejection, then fresh shared capacity.

The returned view is for explicit final consumers only, never a replacement
main contract. GPU locking and each child stage's own admission remain required.
"""
    from scripts.evaluate_restored_native import completed_main
    main, candidate = load(root)
    if active_source_processes(main):
        raise RuntimeError('Training source still active; final reservation not admitted')
    _, _, _, audit = completed_main(main)
    space = enforce_shared_space(main, candidate)
    load(root)
    if active_source_processes(main):
        raise RuntimeError('Training source became active during final admission')
    return dict(contract=candidate, final_audit=audit,
                capacity={k: v for k, v in space.items() if k != 'nfs_used_by_path'})


def bind(main, root):
    """Explicit reference for final consumer contracts; not an execution gate."""
    root = exact_path(resolve(root))
    actual, candidate = load(root)
    if actual != exact_path(resolve(main)):
        raise ValueError('Separate reservation belongs to another training run')
    return dict(reference=dict(control_dir=str(root), plan_sha256=sha256_file(root/'plan.json')),
                code=read(root/'plan.json')['code'],
                inputs={str(root/name): sha256_file(root/name)
                        for name in ['plan.json', 'prepared.json']})


def view(main, reference):
    """Read a pinned budget view; never infer a sidecar from an existing file."""
    if not isinstance(reference, dict) or set(reference) != {'control_dir', 'plan_sha256'}:
        raise ValueError('Explicit separate reservation reference required')
    root = exact_path(resolve(reference['control_dir']))
    if sha256_file(root/'plan.json') != reference['plan_sha256']:
        raise ValueError('Separate reservation reference changed')
    actual, candidate = load(root)
    if actual != exact_path(resolve(main)):
        raise ValueError('Separate reservation belongs to another training run')
    return candidate


def admit_reference(main, reference):
    candidate = view(main, reference)
    result = admit_completed(reference['control_dir'])
    if result['contract'] != candidate:
        raise ValueError('Separate reservation changed during final admission')
    view(main, reference)
    return result
