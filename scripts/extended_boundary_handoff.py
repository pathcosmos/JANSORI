"""Exact10000 boundary transaction; no polling or automatic invocation.

Requires a separately frozen execution bundle. Protection and ledger sealing
must finish before any new GPU work. Failures preserve processes and evidence.
"""
from contextlib import contextmanager, ExitStack
import fcntl
import json
from pathlib import Path
import signal
import time

from common.data_contracts import resolve,sha256_file
from scripts.horizon_process_handoff import BoundProcessSet,process_record,children,same_identity
from scripts.horizon_boundary_handoff import boundary_transaction,terminate_bound
from scripts.extended_horizon_process_handoff import capture_horizon_producer
from scripts.seal_horizon_handoff import unchanged,require_parent_absent
from scripts.seal_extended_handoff import seal_extension
from scripts.tiered_checkpoint_retention import exact_path,inventory,atomic_json
from scripts.protect_horizon_parent import protect_parent
from scripts.extended_horizon_storage import enforce_shared_space,GIB
from train.extended_handoff import require_waiting_controllers_absent

CONTROLLERS={'observer','native','resume','retention','hf','runtime','serving'}
CONTROLLER_SCOPE={
    'observer':('scripts/observe_horizon_training.py','--run-dir','runs/3b_35k_observer_v1'),
    'native':('scripts/evaluate_final_horizon.py','--run-dir','runs/3b_35k_final_native_v1'),
    'resume':('scripts/run_final_horizon_resume.py','--run-dir','runs/3b_35k_final_resume_v1'),
    'retention':('scripts/run_final_horizon_retention_audit.py','--run-dir','runs/3b_35k_final_retention_v1'),
    'hf':('scripts/export_final_horizon_hf.py','--run-dir','artifacts/3b_base_35k_v1'),
    'runtime':('scripts/run_final_vllm.py','--export-run','artifacts/3b_base_35k_v1'),
    'serving':('scripts/run_final_serving.py','--runtime-run','artifacts/3b_base_35k_v1/vllm_runtime_v1')}

class WaitingControllerBusy(RuntimeError):
    pass


def validate_plan(plan):
    if (plan.get('purpose')!='approved_extension_boundary_handoff' or
            plan.get('execution_authorized') is not True or plan.get('boundary_step')!=10000 or
            plan.get('target_steps')!=57000 or plan.get('max_seconds')!=600):
        raise ValueError('Explicit frozen10000-to57000 boundary plan required')
    paths={k:exact_path(resolve(plan[k])) for k in
           ('source','destination','draft','capture','controllers','output','authorization')}
    if (paths['source']!=resolve('runs/3b_full_35k_v1') or
            paths['destination']!=resolve('runs/3b_full_57k_v2') or
            paths['output']!=resolve('runs/3b_57k_transition_v2/boundary10000')):
        raise ValueError('Boundary namespaces differ from authorized scope')
    required={'scripts/extended_boundary_handoff.py','scripts/seal_extended_handoff.py',
              'scripts/horizon_boundary_handoff.py','scripts/protect_horizon_parent.py',
              'scripts/extended_horizon_process_handoff.py','scripts/horizon_process_handoff.py',
              'scripts/extended_horizon_storage.py','scripts/extended_horizon_lineage.py'}
    if not required<=plan.get('code',{}).keys():raise ValueError('Missing boundary implementation binding')
    unchanged({**plan['code'],**plan['inputs']})
    for k in ('draft','capture','controllers','authorization'):
        if plan['inputs'].get(str(paths[k]))!=sha256_file(paths[k]):raise ValueError('Unbound boundary input '+k)
    auth=json.loads(paths['authorization'].read_text())
    if (auth.get('authorized') is not True or auth.get('checkpoint_boundary_stop_authorized') is not True or
            auth.get('snapshot_maintenance_during_stop_authorized') is not True or
            auth.get('boundary_step')!=10000 or auth.get('target_steps')!=57000 or
            resolve(auth['source_run'])!=paths['source'] or resolve(auth['new_run'])!=paths['destination']):
        raise ValueError('Boundary scope lacks explicit user authorization')
    return paths


def validate_controllers(rows):
    if set(rows)!=CONTROLLERS or len({r['pid'] for r in rows.values()})!=7:
        raise ValueError('Exact seven waiting controllers required')
    for name,row in rows.items():
        if not same_identity(row,process_record(row['pid'])):
            raise ValueError('Waiting controller identity/topology changed: '+name)
        if children(row['pid']):raise WaitingControllerBusy('Waiting controller has an active child: '+name)
        entry,flag,target=CONTROLLER_SCOPE[name];args=row['argv']
        if row['cwd']!=str(resolve('.')) or not any(resolve(arg)==resolve(entry) for arg in args):
            raise ValueError('Controller is outside the current35k namespace')
        if args.count(flag)>1:raise ValueError('Ambiguous controller scope')
        if flag in args:
            if args.index(flag)+1==len(args) or resolve(args[args.index(flag)+1])!=resolve(target):
                raise ValueError('Controller target differs')
        elif name not in ('runtime','serving'):
            raise ValueError('Controller explicit run scope missing')


def handoff(plan_path):
    plan=json.loads(exact_path(resolve(plan_path)).read_text());paths=validate_plan(plan)
    source,main=paths['source'],paths['destination'];draft=json.loads(paths['draft'].read_text())
    capture=json.loads(paths['capture'].read_text());controllers=json.loads(paths['controllers'].read_text())
    if draft.get('purpose')!='extension_preparation_not_executable' or resolve(draft['intended_run'])!=main:
        raise ValueError('Expected prepared57k namespace')
    owner=json.loads((main/'.preparation_owner.json').read_text())
    if owner['owner_id']!=draft['tiered_retention']['run_id']:raise ValueError('Prepared owner differs')
    source_contract=json.loads((source/'contract.json').read_text())
    legacy=source_contract['legacy_retention'];original=exact_path(resolve(legacy['source_run']))
    checkpoint=source/'training/ckpt_0010000';proof_path=source/'checkpoint_certificates/ckpt_0010000.json'
    proof=json.loads(proof_path.read_text())
    if proof.get('step')!=10000 or proof.get('status')!='passed_main_checkpoint_storage':
        raise ValueError('Certified10000 checkpoint not available')
    validate_controllers(controllers);started=time.monotonic();paused=False
    def budget():
        if time.monotonic()-started>600:raise TimeoutError('Boundary budget exceeded')
        return enforce_shared_space(main,draft)
    def precheck():
        validate_plan(plan);unchanged({**draft['code'],**draft['evidence']})
        if sha256_file(source/'contract.json')!=draft['source_contract_sha256']:
            raise ValueError('Current producer contract changed')
        fresh=capture_horizon_producer(source,capture['processes']['supervisor']['pid'],capture['processes']['worker']['pid'])
        if any(not same_identity(r,fresh['processes'][k]) for k,r in capture['processes'].items()):
            raise ValueError('Producer identity changed')
        validate_controllers(controllers);require_parent_absent(legacy['stopped_capture'],original)
        inventory(checkpoint,proof)
        latest=sorted(p.name for p in (source/'training').glob('ckpt_*') if (p/'.complete').is_file())
        if not latest or latest[-1]!=checkpoint.name or json.loads(proof_path.read_text())!=proof:
            raise ValueError('10000 is no longer the latest complete certified boundary')
        budget()
    # Controller pidfds are acquired before any source pause. They remain alive
    # but frozen through copy; no irreversible termination before SHA receipt.
    with BoundProcessSet(controllers,paths['output'].parent/'controller_signals.jsonl') as waiting:
        @contextmanager
        def locks():
            nonlocal paused
            # The source supervisor/ranks are already frozen here. The auditor
            # owns shared locks itself; never nest it under our exclusive locks.
            from scripts.audit_horizon_retention import audit_run
            audit=audit_run(source,final=False)
            if audit['issues'] or audit['residues']:
                raise ValueError('Source retention has unsettled or invalid history')
            with ExitStack() as stack:
                for root in sorted((source,original)):
                    handle=stack.enter_context((root/'retention.lock').open('r'))
                    fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                if not paused:
                    waiting.signal(controllers,signal.SIGSTOP);waiting.wait(controllers,stopped=True);paused=True
                # The two producers and all final controllers are now quiescent.
                budget()
                yield
        def protect():
            space=budget()
            return protect_parent(checkpoint,main,proof,owner_id=owner['owner_id'],
                required_free_after_bytes=200*GIB+max(0,space['nfs_remaining_bytes']-proof['bytes']),
                copy_workers=4,hash_workers=8,cancelled=lambda:time.monotonic()-started>600)
        def after():
            for name in controllers:terminate_bound(waiting,name)
            deadline=time.monotonic()+15
            while True:
                try:
                    require_parent_absent(capture,source);require_waiting_controllers_absent(controllers);break
                except ValueError:
                    if time.monotonic()>deadline:raise
                    time.sleep(.1)
            budget()
            result=seal_extension(source,main,capture,controllers,owner_id=owner['owner_id'],step=10000)
            budget();return result
        return boundary_transaction(capture,paths['output'],precheck=precheck,
                                    retention_lock=locks,protect=protect,after_stop=after)
