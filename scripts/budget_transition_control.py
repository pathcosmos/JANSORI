"""Exact57k process/controller boundary admission for an authorized50k handoff."""
from contextlib import contextmanager,ExitStack
import fcntl
import json
import time

from common.data_contracts import resolve,sha256_file
from scripts.tiered_checkpoint_retention import exact_path,inventory
from scripts.horizon_process_handoff import process_record,same_identity,children
from scripts.independent_resume_process_handoff import capture_extended_producer
from scripts.seal_horizon_handoff import unchanged,require_parent_absent
from scripts.extended_horizon_storage import enforce_shared_space,GIB
from train.budget_horizon_handoff import require_controllers_absent
from scripts.budget_boundary_transaction import stop_transaction
from scripts.budget_final_lineage import intervals

SCOPE={
 'observer':('scripts/observe_extended_training_v2.py','--run-dir','runs/3b_57k_observer_v2'),
 'followup':('scripts/run_completed_training_followup.py','--storage-control','runs/3b_57k_transition_v2/completed_training_storage_v2')}

class WaitingControllerBusy(RuntimeError):pass


def validate_controllers(rows):
    if set(rows)!=set(SCOPE) or len({r['pid'] for r in rows.values()})!=2:
        raise ValueError('Exact previous observer and waiting final manager required')
    for name,row in rows.items():
        now=process_record(row['pid'])
        if not same_identity(row,now):raise ValueError('Previous controller identity changed')
        if children(row['pid']):raise WaitingControllerBusy('Controller has active descendants: '+name)
        entry,flag,target=SCOPE[name];args=row['argv']
        if (row['cwd']!=str(resolve('.')) or not any(resolve(arg)==resolve(entry) for arg in args)
                or args.count(flag)!=1 or args.index(flag)+1==len(args)
                or resolve(args[args.index(flag)+1])!=resolve(target)):
            raise ValueError('Controller scope differs: '+name)
        if name=='observer':
            if args.count('--source-run')!=1 or resolve(args[args.index('--source-run')+1])!=resolve('runs/3b_full_57k_v2'):
                raise ValueError('Previous observer watches another source')
        else:
            state=json.loads((resolve(target).with_name(resolve(target).name+'_followup')/'state.json').read_text())
            if state.get('phase')!='running' or state.get('case')!='await_training_completion' or state.get('total')!=57000:
                raise WaitingControllerBusy('Previous final coordinator is not waiting for training')


def validate_plan(plan):
    intervals(plan['boundary_step'])
    paths={k:exact_path(resolve(plan[k])) for k in ('source','destination','draft','capture','controllers','output','authorization','cpu_validation')}
    main=resolve('runs/3b_full_50k_v1');control=resolve('runs/3b_50k_transition_v1')
    if (plan.get('purpose')!='approved_budget_boundary_handoff' or plan.get('execution_authorized') is not True
            or plan.get('controller_authorized') is not True or plan.get('target_steps')!=50000
            or plan.get('poll_seconds')!=30 or plan.get('max_wait_seconds')!=7200 or plan.get('max_seconds')!=600
            or paths['source']!=resolve('runs/3b_full_57k_v2') or paths['destination']!=main
            or paths['output']!=control/f"boundary{plan['boundary_step']}"):
        raise ValueError('Exact authorized50k boundary plan required')
    unchanged({**plan['code'],**plan['inputs']})
    for key in ('draft','capture','controllers','authorization','cpu_validation'):
        if plan['inputs'].get(str(paths[key]))!=sha256_file(paths[key]):raise ValueError('Unbound boundary input '+key)
    auth=json.loads(paths['authorization'].read_text())
    if (auth.get('authorized') is not True or auth.get('checkpoint_boundary_stop_authorized') is not True
            or auth.get('retention_transfer_authorized') is not True or auth.get('target_steps')!=50000
            or auth.get('boundary_step')!=plan['boundary_step']
            or resolve(auth['source_run'])!=paths['source'] or resolve(auth['new_run'])!=main):
        raise ValueError('Boundary authorization differs')
    validation=json.loads(paths['cpu_validation'].read_text())
    if validation.get('status')!='passed_budget_cpu_integration_not_actual8gpu':raise ValueError('50k CPU integration not accepted')
    unchanged(validation['code']);unchanged(validation['evidence'])
    required={'scripts/budget_transition_control.py','scripts/budget_boundary_transaction.py','scripts/horizon_exit_wait.py',
        'scripts/run_budget_transition.py','scripts/prepare_budget_probe.py','scripts/run_budget_probe.py',
        'scripts/activate_budget_training.py','scripts/cleanup_budget_probe.py','scripts/seal_budget_handoff.py',
        'scripts/supervise_budget_training.py','scripts/budget_horizon_retention.py'}
    if not required<=plan['code'].keys():raise ValueError('Missing operational implementation bindings')
    return paths


def handoff(plan):
    from scripts.protect_horizon_parent import protect_parent
    from scripts.seal_budget_handoff import seal_budget
    paths=validate_plan(plan);source,main=paths['source'],paths['destination'];step=plan['boundary_step']
    draft=json.loads(paths['draft'].read_text());capture=json.loads(paths['capture'].read_text())
    controllers=json.loads(paths['controllers'].read_text());owner=json.loads((main/'.preparation_owner.json').read_text())
    if (draft.get('purpose')!='budget_preparation_not_executable' or resolve(draft['intended_run'])!=main
            or owner['owner_id']!=draft['tiered_retention']['run_id'] or owner['boundary_step']!=step):
        raise ValueError('Prepared50k namespace/owner differs')
    checkpoint=source/'training'/f'ckpt_{step:07d}';proofpath=source/'checkpoint_certificates'/(checkpoint.name+'.json')
    proof=json.loads(proofpath.read_text());started=time.monotonic()
    if proof.get('status')!='passed_main_checkpoint_storage' or proof.get('step')!=step:raise ValueError('Certified boundary missing')
    def budget():
        if time.monotonic()-started>600:raise TimeoutError('Boundary600second budget exceeded')
        return enforce_shared_space(main,draft)
    def precheck():
        validate_plan(plan);unchanged({**draft['code'],**draft['evidence']})
        if sha256_file(source/'contract.json')!=draft['source_contract_sha256']:raise ValueError('57k source changed')
        fresh=capture_extended_producer(source,capture['processes']['supervisor']['pid'],capture['processes']['worker']['pid'])
        if any(not same_identity(row,fresh['processes'][name]) for name,row in capture['processes'].items()):
            raise ValueError('Captured producer changed')
        validate_controllers(controllers)
        for row in draft['retention_branches'][:2]:require_parent_absent(row['stopped_capture'],resolve(row['run']))
        inventory(checkpoint,proof)
        latest=sorted(p.name for p in (source/'training').glob('ckpt_*') if (p/'.complete').is_file())
        if not latest or latest[-1]!=checkpoint.name or json.loads(proofpath.read_text())!=proof:
            raise ValueError('Selected boundary is no longer latest complete certificate')
        budget()
    @contextmanager
    def locks():
        from scripts.audit_restored_retention import audit_run
        audit=audit_run(source,final=False)
        if audit['issues'] or audit['residues']:raise ValueError('Source history not settled')
        with ExitStack() as held:
            for row in sorted(draft['retention_branches'][:3],key=lambda r:r['run']):
                handle=held.enter_context((resolve(row['run'])/'retention.lock').open('r'))
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            budget();yield
    def protect():
        space=budget()
        return protect_parent(checkpoint,main,proof,owner_id=owner['owner_id'],
            required_free_after_bytes=200*GIB+max(0,space['nfs_remaining_bytes']-proof['bytes']),
            copy_workers=draft['tiered_retention']['copy_workers'],hash_workers=8,
            cancelled=lambda:time.monotonic()-started>600)
    def after():
        deadline=time.monotonic()+30
        while True:
            try:require_parent_absent(capture,source);require_controllers_absent(controllers);break
            except ValueError:
                if time.monotonic()>deadline:raise
                time.sleep(.1)
        budget();result=seal_budget(source,main,capture,controllers,owner_id=owner['owner_id'],step=step)
        budget();return result
    return stop_transaction(capture,controllers,paths['output'],precheck=precheck,
        retention_lock=locks,protect_saved=protect,after_stop=after)
