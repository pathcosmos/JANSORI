"""Checkpoint-boundary handoff with exact process handles and a verified copy.

No CLI or automatic invocation. A caller must supply a concrete approved plan.
Failure preserves the exact process states and artifacts for explicit recovery;
closing a handle never resumes or restarts an existing producer.
"""
from contextlib import contextmanager, ExitStack
import fcntl
import json
from pathlib import Path
import signal
import time

from common.data_contracts import resolve, identity, sha256_file
from scripts.horizon_process_handoff import BoundProcessSet, capture_producer, same_identity
from scripts.tiered_checkpoint_retention import exact_path, atomic_json, inventory
from scripts.seal_horizon_handoff import (unchanged, require_parent_absent,
    prepare_lineage, seal_handoff, write_once, json_bytes)
from scripts.protect_horizon_parent import protect_parent
from scripts.horizon_storage_plan import enforce_runtime_space, GIB


def terminate_bound(bound,name,*,grace_seconds=5):
    """Planned termination, tolerating already-exited peers; never PID-only kill."""
    def send(sig):
        if bound.exited(name):return
        try:bound.signal([name],sig)
        except ProcessLookupError:
            if not bound.exited(name):raise
    send(signal.SIGTERM)
    send(signal.SIGCONT)  # Deliver termination even when the process was stopped.
    try:bound.wait([name],timeout=grace_seconds)
    except TimeoutError:
        send(signal.SIGKILL)
        bound.wait([name],timeout=5)


def boundary_transaction(capture,output,*,precheck,retention_lock,protect,after_stop):
    """Freeze ranks -> quiesce retention/supervisor -> copy -> stop.

    Checkpoint validation/resource admission and storage locking are supplied by
    the production wrapper below. The source remains recoverable while copying.
    No termination is permitted before a verified protected-parent receipt.
    """
    output=exact_path(output)
    if output.exists():raise ValueError('Preserve prior boundary transaction; inspect before recovery')
    roles=capture['processes']
    if set(roles)!={'supervisor','worker',*[f'rank{r}' for r in range(8)]}:
        raise ValueError('Exact supervisor/launcher/8rank identities required')
    precheck()
    output.mkdir(parents=True)
    phase='binding';bound=None
    def record(name,**extra):
        nonlocal phase
        phase=name
        atomic_json(output/'state.json',dict(phase=name,**extra))
    try:
        with BoundProcessSet(roles,output/'signals.jsonl') as bound:
            names=['worker',*[f'rank{r}' for r in range(8)]]
            record('pausing_workers')
            bound.signal(names,signal.SIGSTOP);bound.wait(names,stopped=True)
            record('awaiting_retention_idle')
            with ExitStack() as held:
                deadline=time.monotonic()+120
                while True:
                    # Freeze BEFORE trying its lock, so the running supervisor
                    # cannot interpret our lock as a competing pruning job.
                    bound.signal(['supervisor'],signal.SIGSTOP)
                    bound.wait(['supervisor'],stopped=True)
                    try:
                        held.enter_context(retention_lock());break
                    except BlockingIOError:
                        bound.signal(['supervisor'],signal.SIGCONT)
                        if time.monotonic()>deadline:raise TimeoutError('Retention did not settle within handoff budget')
                        time.sleep(.1)
                record('all_original_processes_stopped',states=bound.states())
                precheck()  # Recheck the actual selected checkpoint/capacity while frozen.
                protected=protect()
                if protected.get('status')!='verified_protected_parent':
                    raise ValueError('No verified protected copy; original processes must not terminate')
                record('protected_parent_verified',protected=protected)
                terminate_bound(bound,'supervisor')
                # Paused launcher cannot race rank termination/restart decisions.
                for rank in range(8):terminate_bound(bound,f'rank{rank}')
                terminate_bound(bound,'worker')
                bound.wait(roles,timeout=5)
                record('original_processes_exited',states=bound.states())
            result=after_stop()
            record('handoff_sealed',result=result)
            atomic_json(output/'report.json',dict(status='sealed_checkpoint_boundary_handoff',
                protected=protected,result=result,production_restarted=False))
            return result
    except BaseException as exc:
        # Bound handles may already be closed by the context manager. Record
        # the captured identity, never infer death from the observation error.
        atomic_json(output/'failure.json',dict(phase=phase,error=repr(exc),capture=capture,
            outputs_preserved=True,automatic_resume=False,automatic_restart=False,
            recovery='Inspect PID/start_ticks and signals journal; surviving paused original workers require an explicit same-contract resume decision.'))
        raise


def handoff(plan_path):
    """Execute only an explicitly bound approved boundary plan, not a draft."""
    from scripts.audit_checkpoint_retention import audit_run
    from train.horizon_target import TARGET_STEPS, DECAY_START_STEP
    plan_path=exact_path(resolve(plan_path));plan=json.loads(plan_path.read_text())
    if (plan.get('purpose')!='approved_horizon_checkpoint_handoff' or
            plan.get('source_stop_authorized') is not True or plan.get('target_steps')!=TARGET_STEPS or
            plan.get('max_seconds')!=600):
        raise ValueError('Explicit bound35k checkpoint-boundary handoff plan required')
    source,destination,draft_path,capture_path,output=[exact_path(resolve(plan[k])) for k in
        ('source','destination','draft','capture','output')]
    if (source.parent!=resolve('runs') or destination.parent!=resolve('runs') or
            source==destination or not output.is_relative_to(resolve('runs')) or
            any(output.is_relative_to(p) or p.is_relative_to(output) for p in (source,destination))):
        raise ValueError('Handoff source/destination/evidence namespaces overlap')
    required={'scripts/horizon_boundary_handoff.py','scripts/horizon_process_handoff.py',
        'scripts/protect_horizon_parent.py','scripts/seal_horizon_handoff.py',
        'scripts/horizon_storage_plan.py','scripts/audit_checkpoint_retention.py'}
    if not required<=plan['code'].keys():raise ValueError('Unbound handoff implementation')
    unchanged({**plan['code'],**plan['inputs']})
    if any(plan['inputs'].get(str(p))!=sha256_file(p) for p in (draft_path,capture_path)):
        raise ValueError('Draft/capture are not bound in the handoff plan')
    draft=json.loads(draft_path.read_text());capture=json.loads(capture_path.read_text())
    if (resolve(draft['source_run'])!=source or resolve(draft['intended_run'])!=destination or
            draft['requested_target_steps']!=TARGET_STEPS or capture['run']!=str(source) or
            capture['status']!='observed_live_not_stopped'):
        raise ValueError('Reviewed draft/captured original namespace differs')
    step=plan['checkpoint_step']
    if type(step) is not int or step<500 or step%500 or step+2>=DECAY_START_STEP:
        raise ValueError('Invalid checkpoint handoff boundary')
    if destination.exists():raise ValueError('Inspect existing destination before handoff recovery')
    owner=dict(plan_sha256=sha256_file(plan_path),owner_id=draft['tiered_retention']['run_id'])
    checkpoint=source/'training'/f'ckpt_{step:07d}'
    proof_path=source/'checkpoint_certificates'/(checkpoint.name+'.json')
    for path in (source/'contract.json',source/'main.json',source/'training/training_contract.json',
                 proof_path,checkpoint/'meta.json'):
        if plan['inputs'].get(str(path))!=sha256_file(path):
            raise ValueError('Unbound original checkpoint/configuration input: '+str(path))
    proof=json.loads(proof_path.read_text())
    if proof['step']!=step or proof['bytes']>draft['expected_checkpoint_bytes']:
        raise ValueError('Selected checkpoint identity/size exceeds reviewed budget')
    started=time.monotonic()
    def budget():
        if time.monotonic()-started>plan['max_seconds']:raise TimeoutError('Bounded handoff time budget exceeded')
        return enforce_runtime_space(destination,draft)
    def precheck():
        unchanged({**plan['code'],**plan['inputs'],**draft['code'],**draft['evidence']})
        if sha256_file(source/'contract.json')!=draft['source_contract_sha256']:
            raise ValueError('Original execution contract changed')
        observed=capture_producer(source,capture['processes']['supervisor']['pid'],capture['processes']['worker']['pid'])
        if any(not same_identity(row,observed['processes'][role]) for role,row in capture['processes'].items()):
            raise ValueError('Approved original process identities changed')
        budget();inventory(checkpoint,proof)
        complete=sorted(p.name for p in (source/'training').glob('ckpt_*') if (p/'.complete').exists())
        if not complete or complete[-1]!=checkpoint.name:
            raise ValueError('Selected handoff is not the latest complete checkpoint')
        if json.loads(proof_path.read_text())!=proof:raise ValueError('Selected certificate changed')
    @contextmanager
    def retention_lock():
        # Read-open the existing lock: this coordinator never initializes or
        # invokes the old pruning manager, and cannot delete old history.
        with exact_path(source/'retention.lock').open('r') as lock:
            budget()
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            try:
                storage=audit_run(source,final=False)
                if (storage['issues'] or storage['residues'] or
                        sorted(storage['nfs_resume_candidates'])[-1]!=checkpoint.name or
                        len(storage['nfs_resume_candidates'])!=min(3,step//500)):
                    raise ValueError('Source retention is not settled and certified')
                yield
            finally:fcntl.flock(lock,fcntl.LOCK_UN)
    def protect():
        admission=budget()
        return protect_parent(checkpoint,destination,proof,owner_id=owner['owner_id'],
            required_free_after_bytes=200*GIB+max(0,admission['nfs_remaining_bytes']-proof['bytes']),
            cancelled=lambda:time.monotonic()-started>plan['max_seconds'])
    def after_stop():
        deadline=time.monotonic()+10
        while True:
            try:require_parent_absent(capture,source);break
            except ValueError:
                if time.monotonic()>deadline:raise
                time.sleep(.1)  # Allow the system parent to reap terminated processes.
        budget()
        prepare_lineage(source,step,destination/'lineage_candidate')
        result=seal_handoff(source,destination,capture_path,destination/'lineage_candidate',owner_id=owner['owner_id'])
        budget()
        return result
    destination.mkdir()
    write_once(destination/'handoff_owner.json',json_bytes(owner))
    return boundary_transaction(capture,output,precheck=precheck,retention_lock=retention_lock,
                                protect=protect,after_stop=after_stop)
