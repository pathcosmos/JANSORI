"""One authorized50k transition; local polling, pinned ownership, no automatic retries."""
import argparse
from datetime import datetime,timezone
import fcntl
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from common.data_contracts import resolve,sha256_file,identity
from scripts.tiered_checkpoint_retention import atomic_json,inventory
from scripts.seal_horizon_handoff import unchanged,write_once,json_bytes
from scripts.budget_transition_control import validate_plan,handoff,validate_controllers,WaitingControllerBusy
from scripts.independent_resume_process_handoff import capture_extended_producer
from scripts.horizon_process_handoff import same_identity,children
from scripts.extended_horizon_storage import enforce_shared_space


def ready(source,step):
    name=f'ckpt_{step:07d}';cert=source/'checkpoint_certificates'/(name+'.json')
    if not cert.exists() or not (source/'training'/name/'.complete').exists():return False
    state=source/'global_retention_state.json'
    if not state.exists():return False
    rows=json.loads(state.read_text())
    if (rows.get('nfs_checkpoints')!=[f'ckpt_{s:07d}' for s in (step-1000,step-500,step)]
            or rows.get('uncertified_nfs') or len(rows.get('local_checkpoints',[]))>15):return False
    proof=json.loads(cert.read_text())
    if proof.get('step')!=step or proof.get('status')!='passed_main_checkpoint_storage':raise ValueError('Invalid boundary certificate')
    inventory(source/'training'/name,proof);return True


def register(spec):
    from scripts.job_registry import validate,ensure_watcher
    validate(spec);path=resolve('logs/independent_jobs.json')
    with resolve('logs/independent_jobs.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        data=json.loads(path.read_text())
        if data.get('schema_version')!=1:raise ValueError('Registry schema changed')
        prior=[r for r in data['jobs'] if r['run_id']==spec['run_id']]
        if prior and prior!=[spec]:raise ValueError('Job already registered with different scope')
        if not prior:data['jobs'].append(spec);atomic_json(path,data)
        ensure_watcher(resolve('.'))


def migrate_old_registry(control,source,controllers):
    """Archive exactly three stopped job definitions; leave all source evidence."""
    from train.budget_horizon_handoff import require_controllers_absent
    require_controllers_absent(controllers)
    identities={source.name:str(source/'pid'), '3b_57k_observer_v2':str(resolve('runs/3b_57k_observer_v2/pid')),
       'completed_training_storage_v2_followup':str(resolve('runs/3b_57k_transition_v2/completed_training_storage_v2_followup/pid'))}
    path=resolve('logs/independent_jobs.json')
    with resolve('logs/independent_jobs.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX);data=json.loads(path.read_text())
        rows=[r for r in data['jobs'] if r['run_id'] in identities]
        if len(rows)!=3 or any(str(resolve(r['pid_file']))!=identities[r['run_id']] for r in rows):
            raise ValueError('Exact stopped job registrations differ')
        write_once(control/'superseded_job_definitions.json',json_bytes(rows))
        data['jobs']=[r for r in data['jobs'] if r['run_id'] not in identities];atomic_json(path,data)


def job(root,entry,*,log='events.jsonl'):
    return dict(run_id=root.name,state_file=str(root/'state.json'),pid_file=str(root/'pid'),
        log_file=str(root/log),certificate=str(root/'.done'),report_file=str(root/'report.json'),
        pid_cmd_contains=entry,stall_seconds=1200,requires_review=True,
        proofs=[dict(path=str(root/'report.json'),field=['report_sha256']),
                dict(path=str(root/'contract.json'),field=['contract_sha256'])])


def wait_progress(main,supervisor,*,deadline,heartbeat):
    from scripts.capture_budget_producer import capture_budget_producer
    from train.budget_horizon_handoff import load_handoff
    c=json.loads((main/'contract.json').read_text());cfg=json.loads((main/'main.json').read_text())
    _,candidate,_=load_handoff(main,c,cfg);begin=c['budget_execution']['parent_step']
    while time.monotonic()<deadline:
        state=json.loads((main/'state.json').read_text()) if (main/'state.json').exists() else {}
        if state.get('phase') in ('error','failed','paused'):raise RuntimeError('New producer reported an exception; no automatic restart')
        worker=state.get('worker_pid')
        if worker and len(children(worker))==8:
            capture=capture_budget_producer(main,supervisor,worker)
            contract=main/'training/training_contract.json'
            if contract.exists():
                if json.loads(contract.read_text())!=candidate:raise ValueError('Actual new training identity differs')
                records=[]
                for rank in range(8):
                    path=main/'ledgers'/(capture['attempt']+f'-rank{rank}.json')
                    if not path.exists():break
                    row=json.loads(path.read_text())
                    if row['rank']!=rank or row['contract_id']!=candidate['id']:raise ValueError('Actual rank/contract differs')
                    updates=[r for r in row['steps'] if r['step']==begin+1]
                    if len(updates)!=1:break
                    tokens=sum(updates[0]['source_sequences'].values())*4096
                    if tokens!=2097152//8:raise ValueError('First new committed rank geometry differs')
                    records.append(dict(rank=rank,step=begin+1,tokens=tokens,ledger=str(path)))
                if len(records)==8:
                    return dict(status='verified_first_actual8rank_budget_update',capture=capture,
                        committed_step=begin+1,committed_tokens_added=2097152,
                        candidate_contract_id=candidate['id'],ranks=records,goal_complete=False)
        heartbeat();time.sleep(5)
    raise TimeoutError('New producer progress observation timed out; inspect live processes, never relaunch automatically')


def run(path):
    path=resolve(path);plan=json.loads(path.read_text());paths=validate_plan(plan)
    source,main=paths['source'],paths['destination'];control=paths['output'].parent
    draft=json.loads(paths['draft'].read_text());capture=json.loads(paths['capture'].read_text())
    controllers=json.loads(paths['controllers'].read_text());step=plan['boundary_step']
    if any((control/name).exists() for name in ('started.json','failure.json','report.json')):
        raise ValueError('Prior transition evidence exists; explicit recovery review required')
    phase='waiting';start=time.monotonic()
    def event(name,**fields):
        row=dict(utc=datetime.now(timezone.utc).isoformat(),stage='50k_transition',event=name,**fields)
        for p in [control/'events.jsonl',resolve('logs/pipeline_events.jsonl')]:
            with p.open('a') as f:f.write(json.dumps(row)+'\n');f.flush();os.fsync(f.fileno())
        atomic_json(control/'state.json',dict(phase='running',case=name,**fields))
    with (control/'controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        write_once(control/'started.json',json_bytes(dict(pid=os.getpid(),plan_id=identity(plan))))
        (control/'pid').write_text(str(os.getpid())+'\n');event('await_certified_boundary',boundary_step=step)
        try:
            while True:
                validate_plan(plan)
                fresh=capture_extended_producer(source,capture['processes']['supervisor']['pid'],capture['processes']['worker']['pid'])
                if any(not same_identity(r,fresh['processes'][k]) for k,r in capture['processes'].items()):raise ValueError('Source changed while waiting')
                idle=True
                try:validate_controllers(controllers)
                except WaitingControllerBusy:idle=False
                enforce_shared_space(main,draft)
                rows=[json.loads(line) for line in (source/'training/log.jsonl').read_text().splitlines() if line.strip()]
                logged=max([r['step'] for r in rows if 'tok_s' in r] or [0])
                if logged>step+50:raise RuntimeError('Boundary lag exceeded50steps; existing source kept running')
                if idle and ready(source,step):break
                if time.monotonic()-start>7200:raise TimeoutError('Wait budget exhausted; source kept running')
                atomic_json(control/'state.json',dict(phase='running',case='await_certified_boundary',done=logged,total=step,source_stopped=False))
                time.sleep(30)
            phase='protect_stop_seal';event(phase,boundary_step=step);handoff(plan)
            migrate_old_registry(control,source,controllers)
            phase='snapshot_maintenance';event(phase)
            from scripts.budget_horizon_retention import BudgetRetention
            result=BudgetRetention(main,draft,authorized=True).maintain()
            if result['uncertified_nfs'] or len(result['nfs_checkpoints'])!=3 or len(result['local_checkpoints'])>15:raise ValueError('Boundary retention unsettled')
            write_once(control/'boundary_retention.json',json_bytes(result))
            phase='actual8gpu';event(phase)
            from scripts.prepare_budget_probe import prepare
            from scripts.run_budget_probe import execute
            prepare(main,paths['draft'],paths['capture']);execute(main/'horizon_probe/plan.json')
            phase='activate50k';event(phase)
            from scripts.activate_budget_training import activation
            activation(main,paths['draft'],paths['capture'],paths['authorization'])
            phase='cleanup_new_probe_payloads';event(phase)
            from scripts.cleanup_budget_probe import execute as cleanup
            cleanup(main)
            phase='final_capacity_preparation';event(phase)
            from scripts import completed_budget_storage as storage
            storage_root=control/'completed_storage_v1';storage.prepare(main,storage_root)
            from scripts.run_completed_budget_followup import capacity
            capacity(main,storage.load(storage_root)[1])
            phase='start50k';event(phase)
            from scripts.launch_independent_services import launch_once
            from scripts.horizon_probe_process import OWNER_ENV
            env=dict(os.environ,TMPDIR='/tmp',OMP_NUM_THREADS='2',MKL_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',
                     NUMEXPR_NUM_THREADS='2',RAYON_NUM_THREADS='1',PYTHONUNBUFFERED='1')
            env.pop(OWNER_ENV,None)
            services=control/'services';services.mkdir()
            binding=dict(main_contract_sha256=sha256_file(main/'contract.json'))
            command=[sys.executable,'-u',str(resolve('scripts/supervise_budget_training.py')),'--run-dir',str(main),'--run']
            launched=launch_once(services/'supervisor',command,env,binding)
            register(job(main,'scripts/supervise_budget_training.py',log='training/log.jsonl'))
            phase='verify_new8rank_progress';event(phase)
            progress=wait_progress(main,launched['process']['pid'],deadline=time.monotonic()+1200,
                heartbeat=lambda:atomic_json(control/'state.json',dict(phase='running',case=phase,heartbeat_utc=datetime.now(timezone.utc).isoformat())))
            write_once(control/'first_progress.json',json_bytes(progress))
            phase='start_observer_and_final_manager';event(phase)
            observer=resolve('runs/3b_50k_observer_v1')
            command=[sys.executable,'-u',str(resolve('scripts/observe_budget_training.py')),'--source-run',str(main),'--run-dir',str(observer)]
            observer_launch=launch_once(services/'observer',command,dict(env,CUDA_VISIBLE_DEVICES=''),binding)
            register(job(observer,'scripts/observe_budget_training.py'))
            from scripts import run_completed_budget_followup as followup
            followup.prepare(storage_root);followup_launch=followup.start(storage_root)
            register(followup.job_spec(followup.manager_root(storage_root)))
            result=dict(status='activated50k_actual8rank_progress_and_followup_started',parent_step=step,target_step=50000,
                main_run=str(main),supervisor=launched,observer=observer_launch,followup=followup_launch,
                first_progress=progress,goal_complete=False,final_training_complete=False,automatic_restart=False)
            write_once(control/'report.json',json_bytes(result))
            atomic_json(control/'.done',dict(report_sha256=sha256_file(control/'report.json'),plan_sha256=sha256_file(path)))
            event('50k_transition_complete',committed_step=step+1)
            atomic_json(control/'state.json',dict(phase='complete',done=step+1,total=50000));return result
        except BaseException as exc:
            atomic_json(control/'failure.json',dict(phase=phase,error=repr(exc),automatic_retry=False,
                outputs_preserved=True,actual_process_state_must_be_inspected=True))
            event('transition_exception',phase_at_failure=phase,error=repr(exc))
            atomic_json(control/'state.json',dict(phase='error',case=phase,error=repr(exc)));raise

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',required=True)
    p.add_argument('--execute',required=True,action='store_true');a=p.parse_args();run(a.plan)
