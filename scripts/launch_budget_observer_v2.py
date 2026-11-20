"""Review/launch the separately authorized read-only replacement observer.

Never stops or restarts training, moves/deletes data or changes existing
contracts. A prior service launch is inspected, never automatically retried.
"""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from common.data_contracts import resolve,sha256_file
from scripts.launch_independent_services import inspect_handle,launch_once
from scripts.capture_budget_producer import capture_budget_producer
from scripts.observe_budget_training_v2 import admit_source
from scripts.horizon_probe_process import OWNER_ENV
from scripts.run_budget_transition import register,job

PLAN='runs/main_training_50k_plan_v1/observer_v2_recovery_plan_v1.json'
APPROVAL='runs/main_training_50k_plan_v1/observer_v2_recovery_authorization_v1.json'
MAIN='runs/3b_full_50k_v1'
OUT='runs/3b_50k_transition_v1/checkpoint_observer_v2'
SERVICE='runs/3b_50k_transition_v1/services/observer_v2'


def read(p):return json.loads(Path(p).read_text())


def validate_plan(plan,script_sha):
    expected=['/usr/bin/python','-u',str(resolve('scripts/observe_budget_training_v2.py')),
        '--source-run',str(resolve(MAIN)),'--run-dir',str(resolve(OUT))]
    if (plan.get('status')!='prepared_observer_recovery_awaiting_exception_decision' or
            resolve(plan['source'])!=resolve(MAIN) or resolve(plan['output'])!=resolve(OUT) or
            plan.get('script_sha256')!=script_sha or plan.get('command')!=expected or
            plan.get('caps')!={'rss_mib':2048,'metadata_mib':32,'interval_seconds':30,
                'resource_interval_seconds':600,'max_runtime_days':22,'cpu_threads':2,'gpu_visible':False} or
            plan.get('already_started') is not False or plan.get('training_restart_authorized') is not False or
            plan.get('checkpoint_deletion_authorized') is not False or plan.get('goal_complete') is not False):
        raise ValueError('Reviewed observer scope, budget or command differs')
    return expected


def check():
    plan=read(resolve(PLAN));command=validate_plan(plan,sha256_file(resolve('scripts/observe_budget_training_v2.py')))
    main=resolve(MAIN);out=resolve(OUT);service=resolve(SERVICE)
    if out.exists() or service.exists():raise ValueError('Preserve existing observer namespace; inspect handles before action')
    control=resolve('runs/3b_50k_transition_v1/checkpoint_recovery_v1')
    evidence=plan['gate']
    for name,key in [('first_progress.json','initial8rank_evidence_sha256'),
                     ('checkpoint16500_audit.json','checkpoint16500_audit_sha256')]:
        if sha256_file(control/name)!=evidence[key]:raise ValueError('Bound recovery evidence changed')
    first=read(control/'first_progress.json');audit=read(control/'checkpoint16500_audit.json')
    if (first.get('status')!='verified_first_actual8rank_budget_update' or first.get('committed_step')!=16001 or
            audit.get('status')!='passed_budget_committed_prefix_audit' or audit.get('through_step')!=16500 or
            audit['counts']['committed_tokens']!=16500*2097152 or audit['counts']['committed_step_rank_pairs']!=16500*8):
        raise ValueError('Certified16500/first8rank evidence incomplete')
    sup=resolve('runs/3b_50k_transition_v1/services/supervisor')
    supervisor=inspect_handle(sup,read(sup/'intent.json')['command'])
    state=read(main/'state.json')
    if state.get('phase')!='running':raise ValueError('Main source not running')
    capture=capture_budget_producer(main,supervisor['process']['pid'],state['worker_pid'])
    followup=resolve('runs/3b_50k_transition_v1/completed_storage_v1_followup/service')
    final_manager=inspect_handle(followup,read(followup/'intent.json')['command'])
    if read(followup.parent/'state.json').get('case')!='await_training_completion':
        raise ValueError('Final manager not in expected waiting state')
    contract=read(main/'contract.json');config=read(main/'main.json')
    admit_source(main,out,contract,config)
    return dict(status='replacement_observer_admission_passed_not_started',command=command,
        source_contract_sha256=sha256_file(main/'contract.json'),source_processes=capture['processes'],
        followup=final_manager['process'],plan_sha256=sha256_file(resolve(PLAN)),goal_complete=False)


def start():
    approval=read(resolve(APPROVAL))
    if (approval.get('authorized') is not True or
            approval.get('scope')!='single_readonly_observer_v2_after_16500' or
            approval.get('plan_sha256')!=sha256_file(resolve(PLAN)) or
            approval.get('training_restart_allowed') is not False or
            approval.get('checkpoint_deletion_allowed') is not False):
        raise ValueError('Exact explicit observer recovery approval required')
    admitted=check();env=dict(os.environ,TMPDIR='/tmp',OMP_NUM_THREADS='2',MKL_NUM_THREADS='2',
        OPENBLAS_NUM_THREADS='2',NUMEXPR_NUM_THREADS='2',RAYON_NUM_THREADS='1',
        PYTHONUNBUFFERED='1',CUDA_VISIBLE_DEVICES='')
    env.pop(OWNER_ENV,None)
    service=resolve(SERVICE)
    result=launch_once(service,admitted['command'],env,dict(
        main_contract_sha256=admitted['source_contract_sha256'],plan_sha256=admitted['plan_sha256']))
    register(job(resolve(OUT),'scripts/observe_budget_training_v2.py'))
    return dict(status='observer_v2_started_observation_pending',service=result,
        source_contract_sha256=admitted['source_contract_sha256'],goal_complete=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--check',action='store_true');g.add_argument('--start',action='store_true');a=p.parse_args()
    print(json.dumps(check() if a.check else start(),ensure_ascii=False))
