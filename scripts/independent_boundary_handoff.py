"""Explicit own-NFS boundary stop; no watcher, launch, deletion or self-approval.

Uses exact pidfds and the fixed exit observer. A timeout preserves the remaining
process states for review; it does not add SIGKILL or restart training.
"""
from contextlib import contextmanager, ExitStack
import fcntl
import json
import math
from pathlib import Path
import signal
import time

from common.data_contracts import resolve,sha256_file
from scripts.tiered_checkpoint_retention import exact_path,atomic_json,inventory,size_tree
from scripts.restore_final_storage import load as restoration_plan,capacity,MAX_METADATA
from scripts.independent_resume_process_handoff import capture_extended_producer
from scripts.independent_observer_handoff import observer_scope,freeze
from scripts.horizon_process_handoff import BoundProcessSet,process_record,same_identity
from scripts.horizon_exit_wait import terminate_stopped,wait_exit_only
from scripts.seal_horizon_handoff import require_parent_absent
from scripts.prepare_latest_independent_revision import select_latest
from train.final_resume_probe import file_stats

OWN='scripts/independent_boundary_handoff.py'
CODE=[OWN,'scripts/horizon_process_handoff.py','scripts/horizon_exit_wait.py',
      'scripts/independent_resume_process_handoff.py','scripts/independent_observer_handoff.py',
      'scripts/restore_final_storage.py','scripts/prepare_latest_independent_revision.py']
ROLES={'supervisor','worker',*[f'rank{i}' for i in range(8)]}


def prepare(restoration,root,step):
    restoration,root=map(lambda p:exact_path(resolve(p)),(restoration,root))
    plan,main,base,candidate=restoration_plan(restoration)
    scope=exact_path(base['shared_storage']['nfs_pools']['transition']['paths'][0])
    if (root.exists() or root==scope or not root.is_relative_to(scope) or
            root.is_relative_to(restoration) or restoration.is_relative_to(root) or
            type(step) is not int or step%500 or not 10000<step<=56500):
        raise ValueError('Fresh exact transition namespace and future certified boundary required')
    if sha256_file(main/'contract.json')!=plan['base_contract_sha256']:
        raise ValueError('Source contract already changed')
    if size_tree(scope)+MAX_METADATA>2**30:raise RuntimeError('Boundary metadata budget unavailable')
    observer_scope(plan,main,base);capacity(main,candidate)
    value=dict(purpose='own_nfs_independent_boundary_stop_v1',root=str(root),restoration=str(restoration),
        restoration_plan_sha256=sha256_file(restoration/'plan.json'),main_run=str(main),boundary_step=step,
        source_capture=plan['source_capture'],observer_capture=plan['observer_capture'],
        code={p:sha256_file(resolve(p)) for p in CODE},max_seconds=600,metadata_cap_bytes=MAX_METADATA,
        source_stop_authorized=False,production_restart_authorized=False,checkpoint_deletion_authorized=False)
    root.mkdir();atomic_json(root/'plan.json',value);atomic_json(root/'prepared.json',dict(plan_sha256=sha256_file(root/'plan.json')))
    return value


def stop_transaction(capture,observer,output,*,precheck,retention_lock,verify_saved,after_stop):
    """Pause exact11, validate stable NFS state, then TERM/CONT exact handles."""
    output=exact_path(output)
    if output.exists():raise ValueError('Preserve existing boundary transaction for explicit recovery')
    if set(capture['processes'])!=ROLES:raise ValueError('Exact10 producer roles required')
    records=dict(capture['processes'],observer=observer)
    precheck();output.mkdir();phase='binding'
    def state(name,**extra):
        nonlocal phase
        phase=name;atomic_json(output/'state.json',dict(phase=name,**extra))
    try:
        with BoundProcessSet(records,output/'signals.jsonl') as bound:
            workers=['worker',*[f'rank{i}' for i in range(8)],'observer']
            state('pausing_workers_and_observer');bound.signal(workers,signal.SIGSTOP);bound.wait(workers,stopped=True)
            with ExitStack() as held:
                deadline=time.monotonic()+120
                while True:
                    bound.signal(['supervisor'],signal.SIGSTOP);bound.wait(['supervisor'],stopped=True)
                    try:held.enter_context(retention_lock());break
                    except BlockingIOError:
                        bound.signal(['supervisor'],signal.SIGCONT)
                        if time.monotonic()>deadline:raise TimeoutError('Retention lock did not settle')
                        time.sleep(.1)
                state('all11_paused');precheck();receipt=verify_saved()
                if receipt.get('status')!='verified_own_nfs_boundary':
                    raise ValueError('Fresh own NFS boundary verification required before termination')
                if any(s not in ('T','t') for s in bound.states().values()):
                    raise ValueError('All captured processes must remain paused before termination')
                atomic_json(output/'saved_boundary.json',receipt)
                state('verified_own_nfs_before_termination',boundary=receipt)
                for name in ['supervisor','observer',*[f'rank{i}' for i in range(8)],'worker']:
                    terminate_stopped(bound,name,grace_seconds=30)
                wait_exit_only(bound,records,timeout=30);state('exact11_exited')
            result=after_stop();state('boundary_stopped_ready_for_diagnostic',result=result)
            atomic_json(output/'report.json',dict(status='stopped_verified_own_nfs_boundary',boundary=receipt,
                result=result,training_restarted=False,checkpoints_deleted=False,goal_complete=False))
            return result
    except BaseException as exc:
        atomic_json(output/'failure.json',dict(phase=phase,error=repr(exc),capture=capture,observer=observer,
            outputs_preserved=True,automatic_resume=False,automatic_restart=False,
            recovery='Inspect exact pidfd journal/PID/start ticks; an observation timeout is not process death. Explicit recovery decision required.'))
        raise


def execute(root,authorization):
    root=exact_path(resolve(root));plan=json.loads((root/'plan.json').read_text())
    if (json.loads((root/'prepared.json').read_text())!={'plan_sha256':sha256_file(root/'plan.json')} or
            plan.get('purpose')!='own_nfs_independent_boundary_stop_v1' or plan.get('root')!=str(root) or
            plan.get('source_stop_authorized') is not False or plan.get('max_seconds')!=600 or
            plan.get('metadata_cap_bytes')!=MAX_METADATA):raise ValueError('Unreviewed boundary plan')
    authorization=exact_path(resolve(authorization));approval=json.loads(authorization.read_text())
    if (authorization!=root/'authorization.json' or approval.get('source_stop_authorized') is not True or
            approval.get('plan_sha256')!=sha256_file(root/'plan.json') or not approval.get('user_decision')):
        raise ValueError('Exact boundary stop authorization required')
    auth_sha=sha256_file(authorization);restoration=exact_path(plan['restoration'])
    if sha256_file(restoration/'plan.json')!=plan['restoration_plan_sha256']:raise ValueError('Restoration preparation changed')
    rp,main,base,candidate=restoration_plan(restoration)
    if (main!=resolve(plan['main_run']) or plan['source_capture']!=rp['source_capture'] or
            plan['observer_capture']!=rp['observer_capture']):raise ValueError('Boundary process/source scope differs')
    capture=plan['source_capture'];observer=plan['observer_capture'];step=plan['boundary_step']
    if type(step) is not int or step%500 or not 10000<step<=56500:raise ValueError('Unreviewed boundary step')
    started=time.monotonic();output=root/'transaction'
    def guard():
        if time.monotonic()-started>600 or size_tree(root)>MAX_METADATA:raise RuntimeError('Boundary time/metadata limit')
        if sha256_file(authorization)!=auth_sha:raise ValueError('Boundary authorization changed')
        for p,h in plan['code'].items():
            if sha256_file(resolve(p))!=h:raise ValueError('Boundary implementation changed')
        if set(plan['code'])!=set(CODE):raise ValueError('Boundary implementation coverage differs')
        if sha256_file(main/'contract.json')!=rp['base_contract_sha256']:raise ValueError('Main contract changed')
        for p,h in {**base['code'],**base['evidence']}.items():
            if sha256_file(resolve(p))!=h:raise ValueError('Original execution evidence changed')
        capacity(main,candidate)
    def precheck():
        guard();fresh=capture_extended_producer(main,capture['processes']['supervisor']['pid'],capture['processes']['worker']['pid'])
        if any(not same_identity(r,fresh['processes'][n]) for n,r in capture['processes'].items()):raise ValueError('Producer changed')
        if not same_identity(observer,process_record(observer['pid'])):raise ValueError('Observer changed')
        observer_scope(rp,main,base)
        selected=select_latest(main)
        rows=[json.loads(s) for s in (main/'training/log.jsonl').read_text().splitlines() if s.strip()]
        if selected['step']!=step or not rows or max(r['step'] for r in rows)!=step:
            raise ValueError('Exact latest certified/logged boundary unavailable; no signal')
    @contextmanager
    def locks():
        with ExitStack() as stack:
            for d in sorted(base['retention_branches'],key=lambda r:r['run']):
                lock=stack.enter_context((resolve(d['run'])/'retention.lock').open('r'))
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                local=exact_path(d['policy']['local_root'])
                for path in [resolve(d['run'])/'retention_trash',local/'retention_trash',local/'staging']:
                    if any(path.iterdir()):raise ValueError('Preserve unsettled retention residues')
            yield
    def saved():
        cp=main/'training'/f'ckpt_{step:07d}';cert=main/'checkpoint_certificates'/(cp.name+'.json')
        proof=json.loads(cert.read_text());before=file_stats(cp);inventory(cp,proof,hashes=True,workers=8)
        if file_stats(cp)!=before:raise ValueError('Own checkpoint changed during fresh SHA')
        state=json.loads((main/'state.json').read_text());elapsed=state.get('elapsed_seconds')
        if type(elapsed) not in (int,float) or not math.isfinite(elapsed) or not 0<=elapsed<base['max_seconds']:
            raise ValueError('Source consumed elapsed-time evidence missing')
        atomic_json(output/'source_state.json',state)
        # Captured frozen state timestamp precedes hash work; count that pause.
        observed_unix=(main/'state.json').stat().st_mtime
        receipt=dict(status='verified_own_nfs_boundary',checkpoint=str(cp),step=step,certificate_sha256=sha256_file(cert),
            file_stats=before,source_elapsed_seconds=elapsed,source_state_observed_unix=observed_unix,
            elapsed_before_seconds_at_verification=elapsed+max(0,time.time()-observed_unix),
            fresh_payload_sha_verified=True,extra_checkpoint_copy=False,
            unpersisted_updates_after_checkpoint='unknown; do not count as committed training')
        guard();return receipt
    def after():
        deadline=time.monotonic()+15
        while True:
            try:require_parent_absent(capture,main);break
            except ValueError:
                if time.monotonic()>deadline:raise
                time.sleep(.1)
        ref=freeze(restoration);guard()
        return dict(observer_handoff=ref,source_capture=capture,ready_for_storage_restoration=True,
            storage_restored=False,actual8gpu_verified=False,training_restarted=False)
    with (root/'controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return stop_transaction(capture,observer,output,precheck=precheck,retention_lock=locks,verify_saved=saved,after_stop=after)
