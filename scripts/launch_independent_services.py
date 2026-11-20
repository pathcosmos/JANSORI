"""Start each reviewed persistent service once; inspect existing handles on retry.

No stopping/adoption of old jobs, timeout-based restart, cleanup or checkpoint
deletion. OwnedCommand closes its tracking handles after launch, not the child.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from common.data_contracts import resolve,sha256_file
from scripts.tiered_checkpoint_retention import exact_path,atomic_json,size_tree
from scripts.horizon_probe_process import OwnedCommand,OWNER_ENV,tagged_pids
from scripts.horizon_process_handoff import process_record
from scripts.independent_runtime_view import RuntimeView,json_file
from scripts.observe_independent_training import admit_output
from scripts.independent_observer_handoff import seed_history
from scripts.supervise_independent_training import runtime_inputs,require_absent

OWN='scripts/launch_independent_services.py'


def inspect_handle(directory,command):
    directory=exact_path(directory);intent=json_file(directory/'intent.json')
    if intent['command']!=command:raise ValueError('Existing service intent uses another command')
    handle_path=directory/'owned/handle.json'
    if not handle_path.exists():
        # OwnedCommand journals its token before spawning. A launch interrupted
        # before the handle must be inspected, never silently launched again.
        events=directory/'owned/events.jsonl';tokens=[]
        if events.exists():
            tokens=[r['owner'] for r in map(json.loads,events.read_text().splitlines()) if r.get('event')=='launch_intent']
        live={token:tagged_pids(token) for token in tokens}
        raise ValueError('Unfinished service launch; preserve intent and inspect owned PIDs: '+repr(live))
    handle=json_file(handle_path)
    if handle['command']!=command or handle['pid']!=handle['process']['pid']:
        raise ValueError('Service command/handle differs')
    try:row=process_record(handle['pid'])
    except (FileNotFoundError,ProcessLookupError,ValueError) as exc:
        raise ValueError('Recorded service exited; explicit recovery review required, no relaunch') from exc
    if (row['start_ticks']!=handle['process']['start_ticks'] or row['argv']!=command or
            row['cwd']!=str(resolve('.')) or row['pid'] not in tagged_pids(handle['owner'])):
        raise ValueError('Recorded service identity/owner changed; no adoption')
    return dict(status='existing_exact_service_live',process=row,owner=handle['owner'],command=command)


def launch_once(directory,command,env,binding):
    directory=exact_path(directory)
    if directory.exists():
        if json_file(directory/'intent.json')['binding']!=binding:raise ValueError('Service belongs to another runtime')
        return inspect_handle(directory,command)
    directory.mkdir();atomic_json(directory/'intent.json',dict(command=command,binding=binding))
    (directory/'owned').mkdir()
    owned=OwnedCommand(command,directory/'owned',env)
    try:
        # A slow import/restore is a live starting process, not a failed launch.
        result=inspect_handle(directory,command)
        atomic_json(directory/'launch.json',result)
        return dict(result,status='launched_service_process_starting')
    finally:owned.close()


def admitted(control):
    control=exact_path(resolve(control));runtime,contract,accepted,main=runtime_inputs(control)
    view=RuntimeView(control)
    if runtime!=view.runtime or view.binding['code'].get(OWN)!=sha256_file(resolve(OWN)):
        raise ValueError('Service launcher is not bound to reviewed runtime')
    report=json_file(control/'publication_report.json')
    if (report.get('status')!='published_independently_verified_runtime_not_started' or
            report.get('runtime_contract_sha256')!=view.digest or report.get('target_steps')!=57000 or
            report.get('boundary_step')!=accepted['parent_step'] or
            report.get('review_sha256')!=sha256_file(control/'review.json')):
        raise ValueError('Verified runtime publication report required')
    output=exact_path(view.binding['observer_output']);admit_output(view,output);seed_history(view,output)
    pool=Path(contract['shared_storage']['nfs_pools']['transition']['paths'][0])
    if size_tree(pool)+16*2**20>2**30:raise RuntimeError('Service metadata/log budget unavailable')
    env=dict(os.environ,TMPDIR='/tmp',OMP_NUM_THREADS='2',NUMEXPR_NUM_THREADS='2',
        MKL_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',RAYON_NUM_THREADS='1',PYTHONUNBUFFERED='1')
    env.pop(OWNER_ENV,None)
    commands=dict(supervisor=[sys.executable,'-u',str(resolve('scripts/supervise_independent_training.py')),
        '--control-dir',str(control),'--run'],
        observer=[sys.executable,'-u',str(resolve('scripts/observe_independent_training.py')),
        '--control-dir',str(control),'--run-dir',str(output)])
    return view,commands,env


def start(control):
    """Authorized runtime is prerequisite; repeat calls inspect the same PIDs."""
    view,commands,env=admitted(control);root=view.control/'services'
    root.mkdir(exist_ok=True)
    binding=dict(runtime_contract_sha256=view.digest,main_run=str(view.main),launcher_sha256=sha256_file(resolve(OWN)))
    with (root/'launch.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if not (root/'supervisor').exists():
            require_absent(view.main,view.control,view.binding)
            with (view.main/'controller.lock').open('a') as main_lock:
                fcntl.flock(main_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        supervisor=launch_once(root/'supervisor',commands['supervisor'],env,binding)
        # Only a live recorded supervisor can justify starting its observer.
        inspect_handle(root/'supervisor',commands['supervisor'])
        observer_env=dict(env,CUDA_VISIBLE_DEVICES='')
        observer=launch_once(root/'observer',commands['observer'],observer_env,binding)
        result=dict(status='service_processes_started_runtime_consumption_pending',
            supervisor=supervisor,observer=observer,actual_all8_consumed_verified=False,
            goal_complete=False,automatic_restart=False)
        atomic_json(root/'start_report.json',result)
        return result


def status(control):
    """No signals or launch. Report startup until the runtime proves all8 live."""
    view,commands,_=admitted(control);root=view.control/'services'
    services={name:inspect_handle(root/name,cmd) for name,cmd in commands.items()}
    observed=view.observe();processes=observed.get('processes') or {}
    ready=(observed.get('consumption') is not None and processes.get('all8_consumed_live') is True
           and processes.get('launcher') is not None and processes.get('supervisor') is not None)
    if ready and processes['supervisor']['pid']!=services['supervisor']['process']['pid']:
        raise ValueError('Runtime supervisor differs from launched service')
    control_state=json_file(view.control/'state.json') if (view.control/'state.json').exists() else {}
    failed=(observed.get('phase') in ('error','failed','interrupted') or
            control_state.get('phase') in ('error','failed','interrupted') or
            (observed.get('consumption') is not None and not ready))
    result=dict(status=('runtime_error_review_required' if failed else
        'all8_consumed_live' if ready else 'live_services_runtime_startup_pending'),
        controller_state=control_state,
        services=services,observation=observed,goal_complete=False,automatic_restart=False)
    atomic_json(root/'observed.json',result)
    return result


def followup_command(control):
    return [sys.executable,'-u',str(resolve('scripts/run_independent_followup.py')),
            '--control-dir',str(control),'--run']


def start_followup(control):
    """After parent retirement and explicit preparation, launch one final manager."""
    from scripts.run_independent_followup import load
    view,_,env=admitted(control);checked,root,plan=load(control)
    if checked.digest!=view.digest or checked.main!=view.main or root!=view.control/'followup_v1':
        raise ValueError('Final manager belongs to another runtime')
    services=view.control/'services'
    if not services.is_dir():raise ValueError('Persistent runtime services have not been launched')
    binding=dict(runtime_contract_sha256=view.digest,main_run=str(view.main),
        launcher_sha256=sha256_file(resolve(OWN)),followup_plan_sha256=sha256_file(root/'plan.json'))
    with (services/'launch.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return launch_once(services/'followup',followup_command(view.control),env,binding)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control-dir',required=True)
    action=parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--start',action='store_true')
    action.add_argument('--status',action='store_true')
    action.add_argument('--start-followup',action='store_true')
    args=parser.parse_args()
    function=start if args.start else start_followup if args.start_followup else status
    print(json.dumps(function(args.control_dir)))
