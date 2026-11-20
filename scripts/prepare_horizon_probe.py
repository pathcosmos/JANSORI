"""Prepare four bounded native diagnostic modes from a stopped, sealed parent.

Copies and certifies two independent protected parents; never signals processes,
launches GPUs, deletes checkpoints, or activates the main training contract.
Owned partial preparation is resumable. Unknown residue is preserved and refused.
"""
import argparse
from copy import deepcopy
import fcntl
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve, identity, sha256_file
from scripts.tiered_checkpoint_retention import exact_path, inventory
from scripts.seal_horizon_handoff import (require_parent_absent, seal_handoff,
                                         write_once, json_bytes, unchanged)
from scripts.protect_horizon_parent import protect_parent
from scripts.horizon_storage_plan import enforce_runtime_space, GIB
from train.final_resume_probe import file_stats
from train.horizon_target import TARGET_STEPS, DECAY_START_STEP, NATIVE_KEEP, generation_schedule
from train.horizon_worker_contract import executable_candidate
from train.horizon_resume_probe import validate_plan, check_spec
from train.monitored_horizon import load_handoff, validate_configuration
from train.probe import verify_probe


CODE = {
    'scripts/prepare_horizon_probe.py', 'scripts/verify_horizon_probe.py',
    'scripts/verify_horizon_observations.py', 'scripts/verify_final_resume_probe.py',
    'train/horizon_resume_probe.py', 'train/horizon_probe_observer.py',
    'scripts/run_horizon_probe.py', 'scripts/horizon_probe_process.py',
}


def paths_from_draft(draft):
    roots = {}
    for mode in ('continuous', 'split'):
        matches = [exact_path(resolve(p)) for p in draft['space_reservations']['nfs']
                   if f'_probe_{mode}_' in Path(p).name]
        if len(matches) != 1 or matches[0].parent != resolve('runs'):
            raise ValueError('Exactly one reserved project diagnostic root per branch required')
        roots[mode] = matches[0]
    if roots['continuous'] == roots['split']:
        raise ValueError('Diagnostic branches overlap')
    return roots


def diagnostic_contract(draft, receipt, root):
    """No retention policy or production-controller authority in diagnostics."""
    keys = ('code', 'evidence', 'framework', 'prompts', 'tokenizer', 'gpu_identity')
    result = {k: deepcopy(draft[k]) for k in keys}
    result.update(purpose='horizon_validation', diagnostic_only=True,
        main_training_authorized=True, new_checkpoint_pruning_authorized=False,
        configurations={}, generation_steps=generation_schedule(draft['generation_steps']),
        horizon_execution=receipt['execution_binding'])
    result['code'].update({p: sha256_file(resolve(p)) for p in CODE})
    result['evidence'][str(root/'parent_handoff.json')] = sha256_file(root/'parent_handoff.json')
    return result


def compose_modes(main_root, draft, roots, receipts, *, draft_path, capture_path):
    """Bind concrete configs/contracts/probes and publish immutable mode specs.

    Caller owns and has sealed both branches. Does not copy large payloads.
    Resumed spec is intentionally deferred until first's full certificate exists.
    """
    main_receipt = json.loads((main_root/'parent_handoff.json').read_text())
    meta = main_receipt['parent_metadata']; step = meta['step']
    child = executable_candidate(meta, main_receipt['execution_binding'])
    source = exact_path(resolve(draft['source_run']))
    parent_contract = json.loads((source/'contract.json').read_text())
    plan = dict(purpose='bounded_native_horizon_resume_probe', execution_authorized=True,
        world_size=8, target_steps=TARGET_STEPS, main_target_modified=False,
        parent_step=step, parent_contract_id=meta['training_contract']['id'],
        child_contract_id=child['id'], framework=draft['framework'],
        roots={k: str(p) for k, p in roots.items()}, modes={}, inputs={},
        code={**draft['code'], **{p: sha256_file(resolve(p)) for p in CODE}},
        max_seconds=3600, max_pss_gib=384,
        main_root=str(main_root), draft=str(draft_path), capture=str(capture_path),
        diagnostic_tokens_added_to_main=0)
    plan['inputs'].update({str(p):sha256_file(p) for p in (draft_path,capture_path)})
    contracts = {}
    for mode, root in roots.items():
        receipt = receipts[mode]
        if (receipt['execution_binding'] != main_receipt['execution_binding'] or
                receipt['parent_metadata'] != meta or
                receipt['candidate_contract_id'] != child['id']):
            raise ValueError('Diagnostic branch changed the original/child trajectory')
        contracts[root] = diagnostic_contract(draft, receipt, root)
    baseline = roots['continuous']/'baseline'
    baseline.mkdir(exist_ok=True)
    # Original monitored entry requires this purpose, but it has no supervisor
    # authorization/retention policy and may run only the bound two-step spec.
    contracts[baseline] = dict(purpose='main_training', diagnostic_only=True,
        main_training_authorized=True, new_checkpoint_pruning_authorized=False,
        configurations={}, code=deepcopy(plan['code']), evidence=deepcopy(draft['evidence']),
        framework=draft['framework'], prompts=draft['prompts'], tokenizer=draft['tokenizer'],
        generation_steps=deepcopy(parent_contract['generation_steps']))
    mode_roots = dict(baseline=baseline, continuous=roots['continuous'],
                      first=roots['split'], resumed=roots['split'])
    for mode, root in mode_roots.items():
        config = deepcopy(meta['config'])
        config.update(out_dir=str(root/'training'), stop_after=step+(1 if mode=='first' else 2),
            max_steps=57000 if mode=='baseline' else TARGET_STEPS,
            keep_ckpts=57001 if mode=='baseline' else NATIVE_KEEP, save_final=mode!='baseline')
        config_path = root/(mode+'.json')
        write_once(config_path, json_bytes(config))
        contracts[root]['configurations'][config_path.name] = sha256_file(config_path)
        plan['modes'][mode] = dict(run_dir=str(root), config=str(config_path),
            attempt='horizon-probe-'+mode, observations=str(root/'observations'))
        plan['inputs'][str(config_path)] = sha256_file(config_path)
        if mode!='baseline': validate_configuration(config, contracts[root])
    for root, contract in contracts.items():
        (root/'probe').mkdir(exist_ok=True)
        for name in ('expected_tokens.json', 'probe/probe.json', 'probe/.done'):
            write_once(root/name, (source/name).read_bytes())
            plan['inputs'][str(root/name)] = sha256_file(root/name)
        verify_probe(root/'probe')
        write_once(root/'contract.json', json_bytes(contract))
        plan['inputs'][str(root/'contract.json')] = sha256_file(root/'contract.json')
        plan['inputs'].update(contract['evidence'])
    plan['inputs'][str(main_root/'parent_handoff.json')] = sha256_file(main_root/'parent_handoff.json')
    validate_plan(plan)
    spec = dict(plan=plan, plan_id=identity(plan))
    # Admission uses the real worker's sealed lineage/candidate path. No CUDA.
    for mode in ('baseline', 'continuous', 'first'): check_spec(spec, mode)
    output = main_root/'horizon_probe'; output.mkdir(exist_ok=True)
    write_once(output/'plan.json', json_bytes(plan))
    for mode in ('baseline', 'continuous', 'first'):
        write_once(output/(mode+'.json'), json_bytes(spec))
    return plan


def prepare(main_root, draft_path, capture_path):
    main_root, draft_path, capture_path = [exact_path(resolve(p)) for p in
                                         (main_root, draft_path, capture_path)]
    draft = json.loads(draft_path.read_text())
    if (draft.get('purpose')!='horizon_preparation_not_executable' or
            draft.get('requested_target_steps')!=TARGET_STEPS or
            exact_path(resolve(draft['intended_run']))!=main_root or
            main_root.parent!=resolve('runs')):
        raise ValueError('Expected exact35k prepared main namespace and draft')
    source = exact_path(resolve(draft['source_run']))
    if sha256_file(source/'contract.json')!=draft['source_contract_sha256']:
        raise ValueError('Original source execution contract changed')
    unchanged({**draft['code'], **draft['evidence']})
    capture = json.loads(capture_path.read_text())
    require_parent_absent(capture, source)  # Before any destination mutation.
    receipt = json.loads((main_root/'parent_handoff.json').read_text())
    if (receipt['capture_sha256']!=sha256_file(capture_path) or
            receipt['stopped_parent_processes']!=capture['processes']):
        raise ValueError('Main handoff refers to a different process capture')
    meta = receipt['parent_metadata']; step = meta['step']
    if type(step) is not int or step<=0 or step%500 or step+2>=DECAY_START_STEP:
        raise ValueError('Expected saved parent before the changed LR prefix')
    contract = diagnostic_contract(draft, receipt, main_root)
    config = dict(meta['config'], max_steps=TARGET_STEPS, keep_ckpts=NATIVE_KEEP,
                  out_dir=str(main_root/'training'), stop_after=step+2, save_final=True)
    validate_configuration(config, contract)
    _, candidate, proof = load_handoff(main_root, contract, config)
    parent = exact_path(resolve(receipt['parent_checkpoint']))
    inventory(parent, proof)
    if file_stats(parent)!=receipt['parent_stats']:
        raise ValueError('Main protected parent changed after certification')
    roots = paths_from_draft(draft)
    if source in roots.values() or main_root in roots.values():
        raise ValueError('Diagnostic namespace overlaps main/source')
    transaction = dict(draft_sha256=sha256_file(draft_path),
        capture_sha256=sha256_file(capture_path), main_handoff_sha256=sha256_file(main_root/'parent_handoff.json'),
        main_root=str(main_root), roots={k:str(p) for k,p in roots.items()},
        candidate_contract_id=candidate['id'], code={p:sha256_file(resolve(p)) for p in CODE})
    for name, root in roots.items():
        owner = dict(transaction=transaction, branch=name,
                     owner_id=identity(dict(transaction=transaction, branch=name))[:32])
        if root.exists():
            owner_path = exact_path(root/'probe_owner.json')
            if not owner_path.is_file() or json.loads(owner_path.read_text())!=owner:
                raise ValueError('Unowned or different diagnostic residue; preserve: '+str(root))
    with (main_root/'.probe_preparation.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        receipts = {}
        for name, root in roots.items():
            require_parent_absent(capture, source)
            admission = enforce_runtime_space(main_root, draft)
            owner = dict(transaction=transaction, branch=name,
                         owner_id=identity(dict(transaction=transaction, branch=name))[:32])
            root.mkdir(exist_ok=True)
            write_once(root/'probe_owner.json', json_bytes(owner))
            # Whole remaining reservation already includes this incoming copy.
            remaining = admission['nfs_remaining_bytes']
            protect_parent(source/'training'/f'ckpt_{step:07d}', root, proof,
                owner_id=owner['owner_id'],
                required_free_after_bytes=200*GIB+max(0,remaining-proof['bytes']))
            receipts[name] = seal_handoff(source, root, capture_path, main_root/'lineage',
                                           owner_id=owner['owner_id'])
            enforce_runtime_space(main_root, draft)
        plan = compose_modes(main_root, draft, roots, receipts,
                             draft_path=draft_path,capture_path=capture_path)
        require_parent_absent(capture, source)
        unchanged({**draft['code'], **draft['evidence']})
        enforce_runtime_space(main_root, draft)
        result = dict(status='prepared_bounded_horizon_probe_not_executed', plan_id=identity(plan),
            plan=str(main_root/'horizon_probe/plan.json'), parent_step=step,
            actual8gpu_acceptance=False, main_target_modified=False, process_signals_sent=0)
        write_once(main_root/'horizon_probe/preparation.json', json_bytes(result))
        return result


def resume_spec(plan_path):
    """Bind freshly certified first output after its process has terminated.

    A controller must additionally verify launcher termination and GPU ownership.
    """
    plan = json.loads(exact_path(resolve(plan_path)).read_text()); validate_plan(plan)
    root = exact_path(resolve(plan['modes']['resumed']['run_dir']))
    cert = root/'certificates/first.json'
    proof = json.loads(cert.read_text())
    if (proof.get('status')!='certified_horizon_probe_checkpoint' or
            proof.get('cuda_rng_required') is not True):
        raise ValueError('Fresh complete CUDA checkpoint certification required')
    spec = dict(plan=plan, plan_id=identity(plan), resume_certificate=str(cert),
                resume_certificate_sha256=sha256_file(cert), resume_stats=proof['stats'])
    check_spec(spec, 'resumed')
    output = exact_path(resolve(plan['main_root']))/'horizon_probe/resumed.json'
    write_once(output, json_bytes(spec))
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main-root',required=True);parser.add_argument('--draft',required=True)
    parser.add_argument('--capture',required=True)
    args=parser.parse_args()
    print(json.dumps(prepare(args.main_root,args.draft,args.capture)))
