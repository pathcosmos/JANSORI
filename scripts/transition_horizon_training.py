"""Execute the authorized35k transition at a future certified500step boundary.

Waits without LLM calls. Exact producer/controller handles, bounded copy/probe,
no automatic retry, no unrelated cleanup. Failures preserve evidence and stop
this workflow; an already launched new main run is not silently terminated.
"""
import argparse
from datetime import datetime,timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve,sha256_file,artifact_path
from scripts.tiered_checkpoint_retention import atomic_json,exact_path,size_tree
from scripts.seal_horizon_handoff import unchanged,write_once,json_bytes
from scripts.horizon_process_handoff import (capture_producer,process_record,same_identity,children,BoundProcessSet)
from scripts.horizon_boundary_handoff import handoff,terminate_bound
from scripts.prepare_horizon_probe import prepare
from scripts.run_horizon_probe import execute
from scripts.activate_horizon_training import activation
from train.horizon_target import TARGET_STEPS,TOTAL_TOKENS


def event(kind,**data):
    row=dict(utc=datetime.now(timezone.utc).isoformat(),stage='main_training_horizon_transition',event=kind,**data)
    with resolve('logs/pipeline_events.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    print(json.dumps(row,ensure_ascii=False),flush=True)


def read_plan(path):
    path=exact_path(resolve(path));p=json.loads(path.read_text());unchanged({**p['code'],**p['inputs']})
    required={'scripts/transition_horizon_training.py','scripts/activate_horizon_training.py',
        'scripts/horizon_boundary_handoff.py','scripts/horizon_process_handoff.py','scripts/protect_horizon_parent.py',
        'scripts/seal_horizon_handoff.py','scripts/horizon_storage_plan.py','scripts/horizon_retention.py',
        'scripts/audit_checkpoint_retention.py','scripts/prepare_horizon_probe.py','scripts/run_horizon_probe.py',
        'scripts/supervise_horizon_training.py','scripts/observe_horizon_training.py','scripts/evaluate_final_horizon.py'}
    if not required<=p['code'].keys():raise ValueError('Unbound transition implementation')
    approval=json.loads(resolve(p['authorization']).read_text())
    if (p.get('purpose')!='authorized35k_transition_at_boundary' or p.get('target_steps')!=TARGET_STEPS or
            type(p['boundary_step']) is not int or p['boundary_step']%500 or not 500<=p['boundary_step']<29750 or
            p['wait_max_seconds']!=7200 or approval.get('authorized') is not True or
            approval.get('checkpoint_boundary_stop_authorized') is not True or
            approval.get('new_run_training_authorized') is not True or approval.get('retention_transfer_authorized') is not True or
            approval.get('target_steps')!=TARGET_STEPS or approval.get('decay_start_step')!=29750 or
            resolve(approval['source_run'])!=resolve(p['source']) or resolve(approval['new_run'])!=resolve(p['destination'])):
        raise ValueError('Exact authorized35k boundary workflow required')
    for key in ['authorization','draft','capture']:
        if p['inputs'].get(str(resolve(p[key])))!=sha256_file(resolve(p[key])):raise ValueError('Unbound transition '+key)
    root=exact_path(resolve(p['output']));source=exact_path(resolve(p['source']));dest=exact_path(resolve(p['destination']))
    if root.parent!=resolve('runs') or source.parent!=root.parent or dest.parent!=root.parent or len({root,source,dest})!=3:
        raise ValueError('Transition namespaces overlap')
    if size_tree(root)>2**30:raise RuntimeError('Transition evidence budget exceeded; preserve residue')
    return p,root


def ready(plan):
    source=resolve(plan['source']);capture=json.loads(resolve(plan['capture']).read_text())
    actual=capture_producer(source,capture['processes']['supervisor']['pid'],capture['processes']['worker']['pid'])
    if any(not same_identity(row,actual['processes'][role]) for role,row in capture['processes'].items()):
        raise ValueError('Original producer changed while awaiting boundary')
    state=json.loads((source/'state.json').read_text())
    if state.get('phase') in ['error','failed','paused']:raise RuntimeError('Original producer requires inspection')
    text=(source/'training/log.jsonl').read_bytes();lines=text.splitlines()
    if text and not text.endswith(b'\n'):lines=lines[:-1]
    rows=[json.loads(r) for r in lines if r];step=max((r['step'] for r in rows if 'loss' in r),default=0)
    name=f"ckpt_{plan['boundary_step']:07d}"
    certificate=source/'checkpoint_certificates'/(name+'.json')
    settled=json.loads((source/'retention_state.json').read_text())
    names=settled.get('nfs_checkpoints',[])
    done=certificate.exists() and len(names)==3 and names[-1]==name and not settled.get('uncertified_nfs')
    if step>=plan['boundary_step']+500:raise RuntimeError('Missed exact boundary; preserve source and review new plan')
    return done,step


def supersede_waiters(plan,root):
    records=plan['waiting_controllers']
    for name,row in records.items():
        if not same_identity(row,process_record(row['pid'])) or children(row['pid']):
            raise ValueError('Waiting controller changed or owns active children: '+name)
    # Remove only exact superseded specs from active polling, preserve their
    # complete definitions and artifacts, and emit an intentional replacement.
    registry=resolve('logs/independent_jobs.json')
    with resolve('logs/independent_jobs.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        document=json.loads(registry.read_text());old=[s for s in document['jobs'] if s['run_id'] in records]
        if {s['run_id'] for s in old}!=set(records):raise ValueError('Waiting registry ownership differs')
        write_once(root/'superseded_job_specs.json',json_bytes(old))
        document['jobs']=[s for s in document['jobs'] if s['run_id'] not in records];atomic_json(registry,document)
    event('waiting_controllers_superseding',runs=list(records),evidence=artifact_path(root/'superseded_job_specs.json'))
    with BoundProcessSet(records,root/'waiting_controller_signals.jsonl') as bound:
        for name in records:terminate_bound(bound,name)
    atomic_json(root/'waiting_controllers_superseded.json',dict(identities=records,status='intentionally_superseded'))


def boundary_plan(plan,root):
    source=resolve(plan['source']);step=plan['boundary_step'];cp=source/'training'/f'ckpt_{step:07d}'
    paths=[source/'contract.json',source/'main.json',source/'training/training_contract.json',
        source/'checkpoint_certificates'/(cp.name+'.json'),cp/'meta.json']
    inputs={**plan['inputs'],**{str(p):sha256_file(p) for p in paths}}
    out=dict(purpose='approved_horizon_checkpoint_handoff',source_stop_authorized=True,target_steps=TARGET_STEPS,
        max_seconds=600,checkpoint_step=step,source=plan['source'],destination=plan['destination'],
        draft=plan['draft'],capture=plan['capture'],output=str(root/'boundary'),code=plan['code'],inputs=inputs,
        user_authorization=plan['authorization'])
    path=root/'boundary_plan.json';write_once(path,json_bytes(out));return path


def spawn(command,root,name):
    path=root/(name+'.log')
    env=dict(os.environ,OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',NUMEXPR_NUM_THREADS='2',RAYON_NUM_THREADS='1',
        PYTHONUNBUFFERED='1',CUDA_VISIBLE_DEVICES='0,1,2,3,4,5,6,7')
    with path.open('x') as log:
        process=subprocess.Popen(command,cwd=resolve('.'),env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    atomic_json(root/(name+'_launch.json'),dict(pid=process.pid,command=command,log=str(path)))
    return process


def register_started(root,run,process,entry,log):
    from scripts.job_registry import validate
    run=resolve(run);deadline=time.monotonic()+30
    while not (run/'pid').exists():
        if process.poll() is not None or time.monotonic()>deadline:
            raise RuntimeError('Started controller did not publish a live PID: '+str(run))
        time.sleep(.1)
    if int((run/'pid').read_text())!=process.pid:raise ValueError('Controller PID ownership differs')
    spec=validate(dict(run_id=run.name,state_file=artifact_path(run/'state.json'),pid_file=artifact_path(run/'pid'),
        log_file=artifact_path(log),certificate=artifact_path(run/'.done'),report_file=artifact_path(run/'report.json'),
        proofs=[dict(path=artifact_path(run/'report.json'),field=['report_sha256']),
                dict(path=artifact_path(run/'contract.json'),field=['contract_sha256'])],
        stall_seconds=1800,pid_cmd_contains=entry))
    with resolve('logs/independent_jobs.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        path=resolve('logs/independent_jobs.json');registry=json.loads(path.read_text())
        if any(s['run_id']==run.name for s in registry['jobs']):raise ValueError('Preserve existing observer/native registration')
        registry['jobs'].append(spec);atomic_json(path,registry)
    write_once(root/(run.name+'_job.json'),json_bytes(spec))


def workflow(path):
    plan,root=read_plan(path)
    if (root/'execution_started.json').exists():raise ValueError('Preserve prior transition; no automatic retry')
    root.mkdir(exist_ok=True)
    with (root/'controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        atomic_json(root/'execution_started.json',dict(plan_sha256=sha256_file(resolve(path)),pid=os.getpid()))
        (root/'pid').write_text(str(os.getpid())+'\n');phase='await_checkpoint_boundary';started=time.monotonic()
        try:
            while True:
                read_plan(path);done,step=ready(plan)
                atomic_json(root/'state.json',dict(phase='running',case=phase,done=step,total=plan['boundary_step']))
                if done:break
                if time.monotonic()-started>plan['wait_max_seconds']:raise TimeoutError('Boundary wait budget; source not signalled')
                time.sleep(30)
            read_plan(path)
            phase='supersede_waiting_controllers';atomic_json(root/'state.json',dict(phase='running',case=phase))
            supersede_waiters(plan,root)
            phase='protect_and_stop_original';event('authorized_boundary_handoff_started',step=plan['boundary_step'])
            atomic_json(root/'state.json',dict(phase='running',case=phase))
            handoff(boundary_plan(plan,root))
            phase='prepare_actual8gpu_probe';atomic_json(root/'state.json',dict(phase='running',case=phase))
            prepare(plan['destination'],plan['draft'],plan['capture'])
            phase='actual8gpu_probe';event('actual8gpu_horizon_probe_started',step=plan['boundary_step'])
            atomic_json(root/'state.json',dict(phase='running',case=phase))
            execute(resolve(plan['destination'])/'horizon_probe/plan.json')
            phase='activate35k_contract';atomic_json(root/'state.json',dict(phase='running',case=phase))
            result=activation(plan['destination'],plan['draft'],plan['capture'],plan['authorization'])
            phase='launch35k_main';dest=resolve(plan['destination'])
            process=spawn([sys.executable,'-u','scripts/supervise_horizon_training.py','--run-dir',str(dest),'--run'],root,'main35k')
            deadline=time.monotonic()+1200
            while True:
                if process.poll() is not None:raise RuntimeError('New supervisor exited; inspect preserved launch log')
                state=json.loads((dest/'state.json').read_text()) if (dest/'state.json').exists() else {}
                if state.get('phase')=='error':raise RuntimeError('New supervisor reported an error')
                atomic_json(root/'state.json',dict(phase='running',case=phase,supervisor_pid=process.pid))
                log=dest/'training/log.jsonl';raw=log.read_bytes() if log.exists() else b''
                lines=raw.splitlines();lines=lines if not raw or raw.endswith(b'\n') else lines[:-1]
                train=[json.loads(s) for s in lines if s and 'loss' in json.loads(s)]
                if train and train[-1]['step']>=plan['boundary_step']+10:break
                if time.monotonic()>deadline:raise TimeoutError('New worker initial progress observation timeout; do not restart automatically')
                time.sleep(10)
            observer=spawn([sys.executable,'-u','scripts/observe_horizon_training.py','--run-dir',plan['observer'],
                '--source-run',str(dest)],root,'observer35k')
            from scripts.evaluate_final_horizon import main as native_main
            native_main(argparse.Namespace(worker=False,run_dir=plan['native'],main_run=str(dest),
                reference_run='runs/rehearsal_final_native_v1',prepare=True))
            native=spawn([sys.executable,'-u','scripts/evaluate_final_horizon.py','--run-dir',plan['native'],
                '--main-run',str(dest),'--reference-run','runs/rehearsal_final_native_v1','--run'],root,'native35k')
            register_started(root,plan['observer'],observer,'scripts/observe_horizon_training.py',root/'observer35k.log')
            register_started(root,plan['native'],native,'scripts/evaluate_final_horizon.py',root/'native35k.log')
            result.update(status='transitioned_actual35k_training_running',logged_step=train[-1]['step'],
                supervisor_pid=process.pid,worker_pid=state['worker_pid'],observer_pid=observer.pid,native_pid=native.pid,
                remaining=['Final35k resume/retention controller preparation and execution','35000 target/final native/restore/retention','HF/vLLM packaging and Ollama compatibility'])
            atomic_json(root/'report.json',result);atomic_json(root/'.done',dict(report_sha256=sha256_file(root/'report.json')))
            atomic_json(root/'state.json',dict(phase='complete',done=result['logged_step'],total=TARGET_STEPS))
            progress_path=resolve('runs/main_training_readiness_v1/progress.json');progress=json.loads(progress_path.read_text())
            progress.update(run_dir=artifact_path(dest),runtime_target_steps=TARGET_STEPS,target_migration_applied=True,
                horizon_migration_applied=True,last_logged_step=result['logged_step'],supervisor_pid=process.pid,
                worker_pid=state['worker_pid'],observer=plan['observer'],final_native_controller=plan['native'],
                final_native_controller_pid=native.pid,final_resume_controller=None,final_resume_controller_pid=None,
                final_retention_controller=None,final_retention_controller_pid=None,
                retired_57k_controllers=plan['waiting_controllers'],
                goal_complete=False,transition_evidence=artifact_path(root/'report.json'))
            atomic_json(progress_path,progress)
            revision_path=resolve('runs/main_training_35k_plan_v1/goal_revision.json');revision=json.loads(revision_path.read_text())
            revision.update(status='actual35k_training_running_final_packaging_pending',runtime_target_steps=TARGET_STEPS,
                runtime_run=artifact_path(dest),runtime_migration_applied=True,transition_evidence=artifact_path(root/'report.json'))
            atomic_json(revision_path,revision)
            notification=resolve('logs/conversation_notification_policy.json');policy=json.loads(notification.read_text())
            policy['step_notifications']['explicit_steps']=[TARGET_STEPS];atomic_json(notification,policy)
            event('actual35k_transition_running',evidence=artifact_path(root/'report.json'),step=result['logged_step'])
        except BaseException as exc:
            atomic_json(root/'failure.json',dict(phase=phase,error=repr(exc),automatic_retry=False,outputs_preserved=True))
            atomic_json(root/'state.json',dict(phase='error',case=phase,error=str(exc)))
            event('transition_requires_inspection',phase=phase,error=str(exc),evidence=artifact_path(root/'failure.json'))
            raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',required=True)
    workflow(p.parse_args().plan)
