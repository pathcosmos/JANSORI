"""One approved10000 transition, no retries or timeout-based producer restart.

Local polling does not call a language model. Every execution gate is bound in
an explicit plan; actual8GPU comparison is required before main activation.
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
from common.paths import PROJECT_ROOT
from common.data_contracts import resolve,sha256_file,identity
from scripts.tiered_checkpoint_retention import atomic_json,inventory
from scripts.extended_boundary_handoff import validate_plan,handoff,validate_controllers,WaitingControllerBusy
from scripts.extended_horizon_process_handoff import capture_horizon_producer
from scripts.horizon_process_handoff import same_identity,process_record
from scripts.seal_horizon_handoff import unchanged,write_once,json_bytes
from scripts.extended_horizon_storage import enforce_shared_space


def ready(source):
    proof_path=source/'checkpoint_certificates/ckpt_0010000.json'
    if not proof_path.exists():return False
    checkpoint=source/'training/ckpt_0010000'
    if not (checkpoint/'.complete').is_file():return False
    state=source/'global_retention_state.json'
    if not state.exists():return False
    rows=json.loads(state.read_text())
    if (rows.get('nfs_checkpoints')!=['ckpt_0009000','ckpt_0009500','ckpt_0010000'] or
            rows.get('uncertified_nfs') or len(rows.get('local_checkpoints',[]))>15):return False
    inventory(checkpoint,json.loads(proof_path.read_text()));return True


def run(path):
    path=resolve(path);plan=json.loads(path.read_text());paths=validate_plan(plan)
    if plan.get('controller_authorized') is not True or plan.get('poll_seconds')!=30 or plan.get('max_wait_seconds')!=7200:
        raise ValueError('Unreviewed local transition waiter')
    required={'scripts/run_extended_transition.py','scripts/prepare_extended_probe.py','scripts/run_extended_probe.py',
              'scripts/activate_extended_training.py','scripts/supervise_extended_training.py','scripts/extended_horizon_retention.py'}
    if not required<=plan['code'].keys():raise ValueError('Missing transition controller dependencies')
    validation_path=resolve(plan['cpu_validation'])
    if plan['inputs'].get(str(validation_path))!=sha256_file(validation_path):raise ValueError('CPU validation not bound')
    validation=json.loads(validation_path.read_text())
    if validation.get('status')!='passed_extension_cpu_integration_not_actual8gpu':raise ValueError('CPU integration incomplete')
    unchanged(validation['code']);unchanged(validation['evidence'])
    root=paths['output'].parent
    if any((root/n).exists() for n in ('started.json','failure.json','report.json')):
        raise ValueError('Prior transition exists; inspect preserved evidence before recovery')
    source=paths['source'];main=paths['destination'];draft=json.loads(paths['draft'].read_text())
    capture=json.loads(paths['capture'].read_text());controllers=json.loads(paths['controllers'].read_text())
    began=time.monotonic()
    def event(name,**kw):
        row=dict(utc=datetime.now(timezone.utc).isoformat(),stage='57k_transition',event=name,**kw)
        with resolve('logs/pipeline_events.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
        atomic_json(root/'state.json',dict(phase='running',case=name,**kw))
    with (root/'controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        write_once(root/'started.json',json_bytes(dict(pid=os.getpid(),plan_id=identity(plan))))
        (root/'pid').write_text(str(os.getpid())+'\n');event('await_certified10000',source_stopped=False)
        phase='waiting'
        try:
            while True:
                validate_plan(plan);unchanged(validation['code']);unchanged(validation['evidence'])
                fresh=capture_horizon_producer(source,capture['processes']['supervisor']['pid'],capture['processes']['worker']['pid'])
                if any(not same_identity(row,fresh['processes'][name]) for name,row in capture['processes'].items()):
                    raise ValueError('Captured source changed while waiting')
                idle=True
                try:validate_controllers(controllers)
                except WaitingControllerBusy:idle=False
                enforce_shared_space(main,draft)
                rows=[json.loads(line) for line in (source/'training/log.jsonl').read_text().splitlines() if line.strip()]
                logged=max([r['step'] for r in rows if 'tok_s' in r] or [0])
                if logged>10050:raise RuntimeError('Selected boundary handoff lag exceeded50steps; source kept running')
                if idle and ready(source):break
                if time.monotonic()-began>7200:raise TimeoutError('Boundary wait budget exhausted; source kept running')
                atomic_json(root/'state.json',dict(phase='running',case='await_certified10000',logged_step=logged,source_stopped=False))
                time.sleep(30)
            phase='protect_stop_seal';event(phase,checkpoint_step=10000);receipt=handoff(path)
            phase='snapshot_maintenance';event(phase,source_stopped=True)
            from scripts.extended_horizon_retention import ExtendedRetention
            manager=ExtendedRetention(main,draft,authorized=True);retention=manager.maintain()
            if retention['uncertified_nfs'] or len(retention['nfs_checkpoints'])!=3 or len(retention['local_checkpoints'])>15:
                raise ValueError('Stopped-boundary global retention did not settle')
            write_once(root/'boundary_retention.json',json_bytes(retention))
            phase='prepare_actual8gpu';event(phase)
            from scripts.prepare_extended_probe import prepare
            prepare(main,paths['draft'],paths['capture'])
            phase='actual8gpu_validation';event(phase)
            from scripts.run_extended_probe import execute
            execute(main/'horizon_probe/plan.json')
            phase='activate57k';event(phase)
            from scripts.activate_extended_training import activation
            activation(main,paths['draft'],paths['capture'],paths['authorization'])
            phase='start57k';event(phase)
            command=[sys.executable,'-u','scripts/supervise_extended_training.py','--run-dir',str(main),'--run']
            env=dict(os.environ,OMP_NUM_THREADS='2',MKL_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',
                     PYTHONUNBUFFERED='1',TMPDIR=draft['tiered_retention']['temp_root'])
            with (root/'supervisor.log').open('x') as log:
                proc=subprocess.Popen(command,cwd=PROJECT_ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            # Observation only: lack of progress never triggers another launch.
            record=process_record(proc.pid)
            result=dict(status='57k_supervisor_launched_verification_pending',supervisor=record,command=command,
                parent_step=10000,target_step=57000,main_run=str(main),goal_complete=False,
                first_new_committed_update_verified=False,final_controllers_migration_pending=True)
            write_once(root/'report.json',json_bytes(result));event('57k_supervisor_launched',pid=proc.pid)
            atomic_json(root/'state.json',dict(phase='complete',case='handoff_complete_main_running'))
            return result
        except BaseException as exc:
            atomic_json(root/'failure.json',dict(phase=phase,error=repr(exc),automatic_retry=False,
                outputs_preserved=True,source_state_must_be_inspected=True))
            event('transition_exception',phase_at_failure=phase,error=repr(exc))
            atomic_json(root/'state.json',dict(phase='error',case=phase,error=repr(exc)));raise

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--plan',required=True)
    parser.add_argument('--execute',action='store_true',required=True);args=parser.parse_args();run(args.plan)
