"""Finish the authorized50k transition after its first certified save.

Separate from the frozen controller/producer. Never signals or launches training,
changes a checkpoint, fabricates a rank receipt or automatically retries errors.
Waits for the original controller's exact timeout and actual process exit, then
the16500 certificate/8rank journals before starting existing final services once.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve, sha256_file
from scripts.tiered_checkpoint_retention import atomic_json, exact_path, size_tree
from scripts.horizon_process_handoff import process_record, same_identity
from scripts.launch_independent_services import inspect_handle, launch_once
from scripts.capture_budget_producer import capture_budget_producer
from scripts.seal_horizon_handoff import unchanged

OWN='scripts/recover_budget_checkpoint_followup.py'
MAIN='runs/3b_full_50k_v1'
CONTROL='runs/3b_50k_transition_v1'
ROOT=CONTROL+'/checkpoint_recovery_v1'
AUTH='runs/main_training_50k_plan_v1/progress_cadence_recovery_authorization_v1.json'
TIMEOUT='New producer progress observation timed out; inspect live processes, never relaunch automatically'
CAP=8*2**20


def read(path):return json.loads(Path(path).read_text())


def old_terminal(live, state, failure, completed):
    """Never treat an observation timeout, PID reuse or missing file as exit."""
    if completed:raise ValueError('Original transition completed; inspect existing services, no duplicate recovery')
    if live:
        if state.get('phase')=='running' and state.get('case')=='verify_new8rank_progress':
            return False
        if (state.get('phase')=='error' and state.get('case')=='verify_new8rank_progress'
                and failure and failure.get('error')==repr(TimeoutError(TIMEOUT))):
            return False  # Terminal publication can precede the actual exit.
        raise ValueError('Unexpected live original controller state')
    if (state.get('phase')!='error' or state.get('case')!='verify_new8rank_progress'
            or not failure or failure.get('phase')!='verify_new8rank_progress'
            or failure.get('error')!=repr(TimeoutError(TIMEOUT))):
        raise ValueError('Original controller absence lacks the exact reviewed timeout proof')
    return True


def producer(plan):
    main=resolve(MAIN);directory=resolve(CONTROL)/'services/supervisor'
    service=inspect_handle(directory,read(directory/'intent.json')['command'])
    state=read(main/'state.json')
    if state.get('phase')!='running' or state.get('case')!='main':
        raise ValueError('Producer no longer in the approved running state')
    observed=capture_budget_producer(main,service['process']['pid'],state['worker_pid'])
    expected=plan['producer']
    if (observed['attempt']!=expected['attempt'] or
            set(observed['processes'])!=set(expected['processes']) or
            any(not same_identity(row,observed['processes'][name])
                for name,row in expected['processes'].items())):
        raise ValueError('Producer identity changed; preserve and review, never adopt/restart')
    return observed


def prepare():
    main,control,root=map(resolve,(MAIN,CONTROL,ROOT))
    approval=read(resolve(AUTH));contract=read(main/'contract.json')
    if (approval.get('authorized') is not True or approval.get('checkpoint_step')!=16500 or
            approval.get('target_steps')!=50000 or resolve(approval['main_run'])!=main or
            resolve(approval['original_control'])!=control or resolve(approval['recovery_root'])!=root or
            approval.get('scope')!='keep_live50k_training_attach_observer_and_final_followup_after16500_certified_8rank_gate' or
            any(approval.get(k) is not False for k in ['training_stop_or_restart_allowed',
                 'pinned_producer_mutation_allowed','checkpoint_deletion_allowed'])):
        raise ValueError('Exact checkpoint recovery authorization required')
    if (root.exists() or contract['target_steps']!=50000 or contract['budget_execution']['parent_step']!=16000 or
            contract['shared_storage']['nfs_pools']['transition']!={'paths':[str(control)],'budget_bytes':2**30} or
            size_tree(control)+CAP>2**30):
        raise ValueError('Preserve existing recovery path or budget/source mismatch')
    initial=read(resolve('runs/main_training_50k_plan_v1/start50k_live_review_v1.json'))['capture']
    plan=dict(purpose='certified16500_followup_recovery_without_training_interruption',root=str(root),
        main_run=str(main),control=str(control),producer=initial,checkpoint_step=16500,
        parent_step=16000,target_steps=50000,poll_seconds=30,max_seconds=6*3600,
        metadata_cap_bytes=CAP,max_pss_gib=16,training_stop_allowed=False,training_launch_allowed=False,
        checkpoint_deletion_allowed=False,automatic_retry=False,goal_complete=False,
        old_controller=read(control/'launch_receipt.json')['process'])
    producer(plan)
    inputs=[resolve(AUTH),control/'plan.json',control/'launch_receipt.json',main/'contract.json',
        main/'main.json',main/'training/training_contract.json',main/'activation.json',
        main/'horizon_probe/report.json',main/'horizon_probe/.done',
        main/'horizon_probe/payload_retirement/report.json',
        control/'completed_storage_v1/plan.json',control/'completed_storage_v1/prepared.json']
    plan['inputs']={str(p):sha256_file(p) for p in inputs}
    plan['code']={str(p.relative_to(resolve('.'))):sha256_file(p)
        for folder in ['scripts','train','inference','model','common','eval']
        for p in resolve(folder).rglob('*.py')}
    unchanged(plan['code']);unchanged(plan['inputs'])
    root.mkdir();atomic_json(root/'plan.json',plan)
    atomic_json(root/'prepared.json',dict(plan_sha256=sha256_file(root/'plan.json')))
    return plan


def load():
    root=resolve(ROOT);plan=read(root/'plan.json')
    if (read(root/'prepared.json')!={'plan_sha256':sha256_file(root/'plan.json')} or
            (plan.get('root'),plan.get('main_run'),plan.get('control'))!=tuple(str(resolve(p)) for p in (ROOT,MAIN,CONTROL)) or
            (plan.get('checkpoint_step'),plan.get('parent_step'),plan.get('target_steps'),
             plan.get('poll_seconds'),plan.get('max_seconds'),plan.get('metadata_cap_bytes'),plan.get('max_pss_gib'))!=(16500,16000,50000,30,21600,CAP,16) or
            any(plan.get(k) is not False for k in ['training_stop_allowed','training_launch_allowed',
                 'checkpoint_deletion_allowed','automatic_retry','goal_complete']) or
            plan.get('code',{}).get(OWN)!=sha256_file(resolve(OWN))):
        raise ValueError('Recovery plan scope/code changed')
    unchanged(plan['code']);unchanged(plan['inputs'])
    return plan


def original_exited(plan):
    expected=plan['old_controller'];p=Path('/proc')/str(expected['pid'])
    live=False
    if p.exists():
        stat=(p/'stat').read_text().rsplit(')',1)[1].split()
        if int(stat[19])!=expected['start_ticks']:
            raise ValueError('Original controller PID reused; review required')
        if stat[0] not in ('Z','X'):
            actual=process_record(expected['pid'])
            if not same_identity(expected,actual):raise ValueError('Original controller identity changed')
            live=True
    control=resolve(CONTROL);failure=control/'failure.json'
    return old_terminal(live,read(control/'state.json'),read(failure) if failure.exists() else None,
                        (control/'report.json').exists() or (control/'.done').exists())


def certified(main):
    name='ckpt_0016500';p=main/'checkpoint_certificates'/(name+'.json')
    if not p.exists() or not (main/'training'/name/'.complete').exists():return False
    proof=read(p);candidate=read(main/'training/training_contract.json')
    if (proof.get('status')!='passed_main_checkpoint_storage' or proof.get('step')!=16500 or
            proof.get('training_contract_id')!=candidate['id'] or
            exact_path(resolve(proof['checkpoint']))!=main/'training'/name):
        raise ValueError('First certified source checkpoint identity differs')
    return True


def record(event,**fields):
    row=dict(utc=datetime.now(timezone.utc).isoformat(),stage='50k_checkpoint_recovery',event=event,**fields)
    for p in [resolve(ROOT)/'events.jsonl',resolve('logs/pipeline_events.jsonl')]:
        with p.open('a') as f:f.write(json.dumps(row)+'\n');f.flush();os.fsync(f.fileno())


def job_spec():
    root=resolve(ROOT)
    return dict(run_id='3b_50k_checkpoint_recovery_v1',state_file=str(root/'state.json'),
        pid_file=str(root/'pid'),log_file=str(root/'events.jsonl'),certificate=str(root/'.done'),
        report_file=str(root/'report.json'),pid_cmd_contains=OWN,stall_seconds=1200,requires_review=True,
        proofs=[dict(path=str(root/'report.json'),field=['report_sha256']),
                dict(path=str(root/'plan.json'),field=['plan_sha256'])])


def environment():
    from scripts.horizon_probe_process import OWNER_ENV
    env=dict(os.environ,TMPDIR='/tmp',OMP_NUM_THREADS='2',MKL_NUM_THREADS='2',
        OPENBLAS_NUM_THREADS='2',NUMEXPR_NUM_THREADS='2',RAYON_NUM_THREADS='1',PYTHONUNBUFFERED='1')
    env.pop(OWNER_ENV,None)
    # The final manager must retain all8GPU visibility for its future consumers.
    # Only the read-only observer receives an explicit empty visibility mask.
    env.pop('CUDA_VISIBLE_DEVICES',None)
    return env


def start():
    load();root=resolve(ROOT)
    result=launch_once(root/'service',[sys.executable,'-u',str(resolve(OWN)),'--run'],
        environment(),dict(plan_sha256=sha256_file(root/'plan.json')))
    from scripts.run_budget_transition import register
    register(job_spec())
    return result


def run():
    plan=load();root=resolve(ROOT);main=resolve(MAIN);control=resolve(CONTROL)
    with (root/'controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if (root/'started.json').exists():raise ValueError('Existing recovery attempt preserved; no automatic retry')
        atomic_json(root/'started.json',dict(utc=datetime.now(timezone.utc).isoformat(),pid=os.getpid()))
        (root/'pid').write_text(str(os.getpid())+'\n');started=time.monotonic()
        def interrupted(*args):raise InterruptedError('Recovery interrupted; training/services preserved')
        previous=signal.signal(signal.SIGTERM,interrupted)
        try:
            record('recovery_wait_started',checkpoint_step=16500)
            while True:
                load();observed=producer(plan)
                from scripts.process_accounting import parse_rollup
                pss=parse_rollup(Path('/proc/self/smaps_rollup').read_text())['Pss']
                if time.monotonic()-started>plan['max_seconds'] or size_tree(root)>CAP or pss>16*2**30:
                    raise RuntimeError('Bounded recovery wait/metadata budget exceeded; no producer action')
                exited=original_exited(plan)
                ready=exited and certified(main)
                atomic_json(root/'state.json',dict(phase='running',case='await_16500_certificate' if exited else 'await_original_controller_exit',
                    heartbeat_utc=datetime.now(timezone.utc).isoformat(),checkpoint_step=16500,
                    source_alive=True,goal_complete=False))
                if ready:break
                time.sleep(30)
            from scripts.audit_budget_training import audit_run
            from scripts.run_budget_transition import wait_progress,register,job
            from scripts import completed_budget_storage as storage
            from scripts import run_completed_budget_followup as followup
            record('certified16500_gate_observed')
            audited=audit_run(main,16500)
            atomic_json(root/'checkpoint16500_audit.json',audited)
            first=wait_progress(main,observed['processes']['supervisor']['pid'],
                deadline=time.monotonic()+120,heartbeat=lambda:None)
            atomic_json(root/'first_progress.json',first)
            producer(plan)
            if not original_exited(plan):raise ValueError('Original controller no longer terminal')
            storage_root=control/'completed_storage_v1'
            _,contract=storage.load(storage_root)
            atomic_json(root/'capacity_before_services.json',followup.capacity(main,contract))
            observer=resolve('runs/3b_50k_observer_v1')
            if observer.exists() or followup.manager_root(storage_root).exists():
                raise ValueError('Existing observer/final manager requires ownership review; no adoption')
            command=[sys.executable,'-u',str(resolve('scripts/observe_budget_training.py')),
                '--source-run',str(main),'--run-dir',str(observer)]
            binding=dict(main_contract_sha256=sha256_file(main/'contract.json'))
            observer_launch=launch_once(control/'services/observer',command,dict(environment(),CUDA_VISIBLE_DEVICES=''),binding)
            register(job(observer,'scripts/observe_budget_training.py'))
            followup.prepare(storage_root)
            followup_launch=followup.start(storage_root)
            manager=followup.manager_root(storage_root)
            register(followup.job_spec(manager))
            # Verify exact live service handles after launch, with no polling restart.
            inspect_handle(control/'services/observer',command)
            inspect_handle(manager/'service',read(manager/'service/intent.json')['command'])
            producer(plan)
            result=dict(status='certified16500_first8rank_and_final_services_connected',
                source_run=str(main),target_steps=50000,checkpoint_step=16500,first_progress=first,
                observer=observer_launch,followup=followup_launch,training_interrupted=False,
                original_failure_preserved=True,goal_complete=False)
            atomic_json(root/'report.json',result)
            atomic_json(root/'.done',dict(report_sha256=sha256_file(root/'report.json'),plan_sha256=sha256_file(root/'plan.json')))
            atomic_json(root/'state.json',dict(phase='complete',done=16500,total=50000,goal_complete=False))
            record('checkpoint_recovery_complete',checkpoint_step=16500)
            return result
        except BaseException as exc:
            atomic_json(root/'failure.json',dict(error=repr(exc),training_preserved=True,automatic_retry=False))
            atomic_json(root/'state.json',dict(phase='error',error=repr(exc),training_preserved=True))
            record('checkpoint_recovery_error',error=repr(exc))
            raise
        finally:signal.signal(signal.SIGTERM,previous)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);g=p.add_mutually_exclusive_group(required=True)
    for action in ['prepare','start','run']:g.add_argument('--'+action,action='store_true')
    a=p.parse_args();result=(prepare if a.prepare else start if a.start else run)()
    print(json.dumps(result,ensure_ascii=False))
