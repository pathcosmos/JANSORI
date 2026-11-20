"""Wait for unchanged 50000-step training, then run serial final consumers.

Never stops/restarts training, retires parents, uploads, or marks the Goal done.
Only exact completed-source evidence admits final work. Durable owned stage
handles preserve failures and prevent duplicate launches after interruption.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.data_contracts import resolve, sha256_file
from scripts.tiered_checkpoint_retention import exact_path, atomic_json, size_tree
from scripts import completed_budget_storage as storage
from scripts.evaluate_final_rehearsal import check_bindings
from scripts.evaluate_budget_native import main_observation
from scripts.extended_horizon_storage import enforce_shared_space
from scripts.run_independent_followup import phase, future_local_capacity
from scripts.budget_final_stages import verify_stage as verify_existing

OWN = 'scripts/run_completed_budget_followup.py'
ENVIRONMENT = 'runs/main_training_35k_plan_v1/serving_environment_v2'
CAP = 32 * 2**20
POLL = 30
MAX_SECONDS = 22 * 86400


def read(path):
    return json.loads(Path(path).read_text())


def manager_root(control):
    # Keep the manager's32MiB cap outside the storage control's8MiB cap.
    return control.parent/(control.name+'_followup')


def job_spec(root):
    from scripts.job_registry import validate
    return validate(dict(run_id=root.name,state_file=str(root/'state.json'),pid_file=str(root/'pid'),
        log_file=str(root/'events.jsonl'),certificate=str(root/'.done'),report_file=str(root/'report.json'),
        pid_cmd_contains=OWN,stall_seconds=1200,requires_review=True,
        proofs=[dict(path=str(root/'report.json'),field=['report_sha256']),
                dict(path=str(root/'plan.json'),field=['plan_sha256'])]))


def stages(main, control, contract):
    paths = {}
    for name, gib in [('final_native',8),('final_resume',1),('final_retention',1),('hf_and_serving',100)]:
        pool = contract['shared_storage']['nfs_pools'][name]
        if len(pool['paths']) != 1 or pool['budget_bytes'] != gib*2**30:
            raise ValueError('Exact final reservation required: '+name)
        paths[name] = str(exact_path(pool['paths'][0]))
    native, resume, retention, export = [paths[n] for n in ['final_native','final_resume','final_retention','hf_and_serving']]
    runtime, serving = str(Path(export)/'vllm_runtime_v1'), str(Path(export)/'vllm_serving_v1')
    ref = ['--storage-control',str(control)]
    main_args = ['--main-run',str(main)]
    definitions = [
        ('native_prepare','evaluate_budget_native.py',native,['--run-dir',native,*main_args,*ref,'--prepare']),
        ('native','evaluate_budget_native.py',native,['--run-dir',native,*main_args,*ref,'--run']),
        ('resume_plan','prepare_budget_final_resume.py',resume,['--source-run',str(main),'--run-dir',resume,'--native-run',native,*ref]),
        ('resume_prepare','run_budget_final_resume.py',resume,['--run-dir',resume,'--prepare']),
        ('resume','run_budget_final_resume.py',resume,['--run-dir',resume,'--run']),
        ('retention_prepare','run_budget_retention_audit.py',retention,['--run-dir',retention,*main_args,'--native-run',native,'--resume-run',resume,*ref,'--prepare']),
        ('retention','run_budget_retention_audit.py',retention,['--run-dir',retention,'--run']),
        ('hf_prepare','export_budget_hf.py',export,['--run-dir',export,*main_args,'--native-run',native,'--resume-run',resume,'--retention-run',retention,*ref,'--prepare']),
        ('hf','export_budget_hf.py',export,['--run-dir',export,'--run']),
        ('vllm_prepare','run_budget_vllm.py',runtime,['--export-run',export,'--environment',str(resolve(ENVIRONMENT)),'--local-environment-cap-gib','60','--prepare']),
        ('vllm','run_budget_vllm.py',runtime,['--export-run',export,'--run']),
        ('serving_prepare','run_budget_serving.py',serving,['--runtime-run',runtime,'--prepare']),
        ('serving','run_budget_serving.py',serving,['--runtime-run',runtime,'--run'])]
    return [dict(name=name,output=output,command=[sys.executable,'-u',str(resolve('scripts/'+entry)),*args])
            for name,entry,output,args in definitions]


def capacity(main, contract):
    from scripts.resource_admission import inspect
    hw=inspect();required=contract['max_pss_gib']+256+16
    if (hw['gpus']!=contract['gpu_identity'] or len(hw['gpus'])!=8 or hw['cpu']['effective']<2
            or hw['memory']['cache_aware_admission']['headroom_gib']<required):
        raise RuntimeError('Final coordinator/source/child hardware capacity unavailable')
    space=enforce_shared_space(main,contract)
    return dict(hardware=hw,combined_memory_gib=required,
                space={k:v for k,v in space.items() if k!='nfs_used_by_path'},
                future_local=future_local_capacity(contract,space))


def prepare(control):
    control=exact_path(resolve(control));main,contract=storage.load(control)
    root=manager_root(control);binding=storage.bind(main,control)
    rows=stages(main,control,contract)
    if root.exists() or any(Path(r['output']).exists() for r in rows):
        raise ValueError('Preserve existing followup and final output namespaces')
    scope=exact_path(contract['shared_storage']['nfs_pools']['transition']['paths'][0])
    if not root.is_relative_to(scope) or size_tree(scope)+CAP>2**30:
        raise ValueError('Final manager metadata outside available transition pool')
    # Live state is evidence of a real source, never permission to stop it.
    observed=main_observation(main)
    admission=capacity(main,contract)
    code={str(p.relative_to(resolve('.'))):sha256_file(p)
          for folder in ['scripts','train','inference','model','common','eval']
          for p in resolve(folder).rglob('*.py')}
    inputs={**binding['inputs'],str(main/'contract.json'):sha256_file(main/'contract.json')}
    inputs.update({str(resolve(ENVIRONMENT)/n):sha256_file(resolve(ENVIRONMENT)/n)
                   for n in ['report.json','contract.json','requirements.lock','import_report.json']})
    plan=dict(purpose='unchanged_training_completed50k_followup_v1',root=str(root),main_run=str(main),
              control=str(control),reference=binding['reference'],stages=rows,code=code,inputs=inputs,
              metadata_cap_bytes=CAP,max_seconds=MAX_SECONDS,poll_seconds=POLL,max_pss_gib=16,
              finalization_wait_seconds=1200,automatic_retry=False,training_stop_allowed=False,
              goal_complete=False,remaining_after_stages=['quality review','Ollama compatibility conclusion',
                                                        'protected-parent retention exception review','final Korean report'])
    check_bindings(plan)
    root.mkdir();atomic_json(root/'plan.json',plan)
    atomic_json(root/'preparation_admission.json',admission)
    atomic_json(root/'source_observation_at_preparation.json',observed)
    atomic_json(root/'prepared.json',dict(plan_sha256=sha256_file(root/'plan.json'),execution_started=False))
    atomic_json(root/'job_spec.json',job_spec(root))
    return plan


def load(control):
    control=exact_path(resolve(control));main,contract=storage.load(control)
    root=manager_root(control);plan=read(root/'plan.json')
    if (read(root/'prepared.json')!=dict(plan_sha256=sha256_file(root/'plan.json'),execution_started=False)
            or plan.get('purpose')!='unchanged_training_completed50k_followup_v1'
            or plan.get('root')!=str(root) or plan.get('control')!=str(control) or plan.get('main_run')!=str(main)
            or plan.get('reference')!=storage.bind(main,control)['reference']
            or plan.get('stages')!=stages(main,control,contract)
            or (plan.get('metadata_cap_bytes'),plan.get('max_seconds'),plan.get('poll_seconds'),
                plan.get('max_pss_gib'),plan.get('finalization_wait_seconds'))!=(CAP,MAX_SECONDS,POLL,16,1200)
            or plan.get('automatic_retry') is not False or plan.get('training_stop_allowed') is not False
            or plan.get('goal_complete') is not False or plan['code'].get(OWN)!=sha256_file(resolve(OWN))):
        raise ValueError('Followup scope, source, commands or implementation changed')
    check_bindings(plan)
    return main,root,plan,contract


def observation(main):
    """The terminal publication/worker-exit race is waiting, never a restart."""
    if (main/'.done').exists():
        live=storage.active_source_processes(main)
        return dict(complete=not live,step=50000,phase='await_source_exit' if live else 'final_audit_required')
    try:
        result=main_observation(main)
        if result['complete']:
            live=storage.active_source_processes(main)
            return dict(complete=not live,step=50000,phase='await_source_exit' if live else 'final_audit_required')
        return dict(result,phase='await_training_completion')
    except RuntimeError:
        from scripts.observe_budget_training import process_identity
        state=read(main/'state.json')
        supervisor=process_identity(int((main/'pid').read_text()),'supervisor',main)
        cert=main/'checkpoint_certificates/ckpt_0050000.json'
        if (state.get('phase')=='running' and supervisor is not None and cert.exists()
                and read(cert).get('step')==50000 and read(cert).get('status')=='passed_main_checkpoint_storage'):
            return dict(complete=False,step=50000,phase='await_completion_publication')
        raise


def verify_stage(plan, row):
    root=exact_path(row['output']);name=row['name']
    child=read(root/('plan.json' if name=='resume_plan' else 'contract.json'))
    source=child['source'] if name in ('hf_prepare','hf') else child
    if source.get('final_storage')!=plan['reference'] or source.get('independent_runtime') is not None:
        raise ValueError('Final stage uses another source/storage route')
    if name!='native':return verify_existing(row)
    from tokenizers import Tokenizer
    from scripts.evaluate_budget_native import verify_result
    from train.budget_final_resume_probe import verify_final_audit
    check_bindings(child);report=read(root/'report.json');main=resolve(child['main_run'])
    if (read(root/'.done')!=dict(report_sha256=sha256_file(root/'report.json'),contract_sha256=sha256_file(root/'contract.json'))
            or report.get('status')!='passed_actual_final3b_native_evaluation'
            or report['result_sha256']!=sha256_file(root/'3b_final.json')
            or report['final_training_audit_sha256']!=sha256_file(root/'final_training_audit.json')
            or report['main_report_sha256']!=sha256_file(main/'report.json')):
        raise ValueError('Native content/audit/marker binding differs')
    audit=read(root/'final_training_audit.json')
    verify_final_audit(audit,main,read(main/'training/training_contract.json')['id'])
    result=read(root/'3b_final.json');proof=read(main/'checkpoint_certificates/ckpt_0050000.json')
    verify_result(result,proof,Tokenizer.from_file(str(resolve(child['tokenizer']))),child['expected_targets'],
                  read(resolve(child['reference_report'])),expected_step=50000,expected_device='cuda')
    if any(report[k]!=result[k] for k in ['languages','aggregate']):
        raise ValueError('Native summary differs from verified content')
    return dict(report_sha256=sha256_file(root/'report.json'),contract_sha256=sha256_file(root/'contract.json'))


def record(root, event, **fields):
    value=dict(utc=datetime.now(timezone.utc).isoformat(),stage='completed_training_followup',
               event=event,root=str(root),**fields)
    for path in [root/'events.jsonl',resolve('logs/pipeline_events.jsonl')]:
        with path.open('a') as stream:
            stream.write(json.dumps(value)+'\n');stream.flush();os.fsync(stream.fileno())


def run(control):
    main,root,plan,contract=load(control)
    with (root/'controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        atomic_json(root/'launch_admission.json',capacity(main,contract))
        (root/'pid').write_text(str(os.getpid())+'\n')
        if not (root/'started.json').exists():atomic_json(root/'started.json',dict(unix=time.time()))
        started=read(root/'started.json')['unix'];last=[-float('inf')]
        def guard():
            if not 0<=time.time()-started<=MAX_SECONDS:raise RuntimeError('Final followup elapsed limit')
            if time.monotonic()-last[0]<POLL:return
            check_bindings(plan);storage.view(main,plan['reference'])
            from scripts.process_accounting import parse_rollup
            if parse_rollup(Path('/proc/self/smaps_rollup').read_text())['Pss']>16*2**30 or size_tree(root)>CAP:
                raise RuntimeError('Final coordinator PSS/metadata limit')
            space=enforce_shared_space(main,contract);future_local_capacity(contract,space)
            if (root/'state.json').exists():
                state=read(root/'state.json')
                state['heartbeat_utc']=datetime.now(timezone.utc).isoformat()
                atomic_json(root/'state.json',state)
            last[0]=time.monotonic()
        def interrupted(*_):raise InterruptedError('Final coordinator interrupted; children and training preserved')
        previous=signal.signal(signal.SIGTERM,interrupted)
        try:
            terminal_since=None
            while True:
                guard();seen=observation(main)
                atomic_json(root/'state.json',dict(phase='running',case=seen['phase'],done=seen['step'],total=50000,
                    source=seen,goal_complete=False))
                if seen['complete']:break
                if seen['phase'] in ('await_source_exit','await_completion_publication'):
                    if terminal_since is None:terminal_since=time.monotonic()
                    if time.monotonic()-terminal_since>1200:raise RuntimeError('Final source publication/exit needs review')
                else:terminal_since=None
                time.sleep(POLL)
            accepted=storage.admit_reference(main,plan['reference'])
            atomic_json(root/'final_source_admission.json',accepted)
            record(root,'completed_source_admitted')
            results={}
            for row in plan['stages']:
                last[0]=-float('inf');guard();capacity(main,contract)
                atomic_json(root/'state.json',dict(phase='running',case=row['name'],goal_complete=False))
                record(root,'final_stage_started',case=row['name'])
                results[row['name']]=phase(root,row,guard,verify=lambda r:verify_stage(plan,r))
                last[0]=-float('inf');guard()
                record(root,'final_stage_verified',case=row['name'])
            report=dict(status='final_diagnostics_and_serving_verified_review_pending',stages=results,
                plan_sha256=sha256_file(root/'plan.json'),goal_complete=False,remaining=plan['remaining_after_stages'])
            atomic_json(root/'report.json',report)
            atomic_json(root/'.done',dict(report_sha256=sha256_file(root/'report.json'),plan_sha256=sha256_file(root/'plan.json')))
            atomic_json(root/'state.json',dict(phase='complete',goal_complete=False,remaining=report['remaining']))
            record(root,'final_followup_verified_review_pending')
            return report
        except BaseException as exc:
            atomic_json(root/'state.json',dict(phase='error',error=repr(exc),children_preserved=True,automatic_retry=False,goal_complete=False))
            record(root,'final_followup_error',error=repr(exc),automatic_retry=False)
            raise
        finally:signal.signal(signal.SIGTERM,previous)


def start(control):
    from scripts.launch_independent_services import launch_once
    from scripts.horizon_probe_process import OWNER_ENV
    main,root,plan,contract=load(control)
    capacity(main,contract)
    command=[sys.executable,'-u',str(resolve(OWN)),'--storage-control',str(resolve(control)),'--run']
    env=dict(os.environ,TMPDIR='/tmp',OMP_NUM_THREADS='2',NUMEXPR_NUM_THREADS='2',
             MKL_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',RAYON_NUM_THREADS='1',PYTHONUNBUFFERED='1')
    env.pop(OWNER_ENV,None)
    with (root/'launch.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return launch_once(root/'service',command,env,dict(plan_sha256=sha256_file(root/'plan.json')))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--storage-control',required=True)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare',action='store_true');mode.add_argument('--start',action='store_true');mode.add_argument('--run',action='store_true')
    args=parser.parse_args()
    (prepare if args.prepare else start if args.start else run)(args.storage_control)
